"""
Module-based Code Generator - Generate separate files for each module
Organized by base address hierarchy
"""
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime
from pathlib import Path
import os

from app.services.hierarchy_parser import RegisterHierarchy, Module, Register, RegisterField
from app.services.rdl_exporter import RDLExporter


class ModuleCodeGenerator:
    """Generate code files for each module separately

    Rules:
    1. Base modules (with registers): Generate RTL/RDL/RALF/Header/SVH (0-base for header/svh)
    2. Addr-map modules (no registers, only submodules):
       - No RTL generation
       - RDL/RALF use `include` to reference submodules
       - Header/SVH generate base address macros for submodules
    3. Same module instantiated multiple times: Only generate one copy of base code
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.warnings: List[str] = []
        self.errors: List[str] = []
        # Track generated base modules to avoid duplicates
        self.generated_base_modules: Set[str] = set()

    def generate_all(self, hierarchy: RegisterHierarchy, version_id: int = 0, version_name: str = "", skip_peakrdl: bool = False) -> Dict[str, Dict[str, str]]:
        """
        Generate all code files for all modules

        Args:
            hierarchy: Register hierarchy
            version_id: Version ID
            version_name: Version name
            skip_peakrdl: If True, skip PeakRDL compilation (UVM/RTL/C Header generation)
                         This is useful for transactional upload where we only need file content

        Returns:
            {
                'ralf': {module_name: content, ...},
                'rdl': {module_name: content, ...},
                'header': {module_name: content, ...},
                'svh': {module_name: content, ...},
                'rtl': {module_name: content, ...},  # Only for register modules
                'uvm': {module_name: content, ...},
            }

        Raises:
            ValueError: If field bit width exceeds register width or other validation errors
        """
        self.warnings = []
        self.errors = []
        self.generated_base_modules = set()
        self.skip_peakrdl = skip_peakrdl

        result = {
            'ralf': {},
            'rdl': {},
            'header': {},
            'svh': {},
            'rtl': {},
            'uvm': {},  # PeakRDL generates UVM for register modules
        }

        try:
            # First pass: identify all base modules (excluding array instances)
            base_modules = {}
            for module_name, module in hierarchy.all_modules.items():
                if getattr(module, 'is_array_instance', False):
                    continue
                base_modules[module_name] = module

            # Second pass: generate code for base register modules first (they have registers)
            # This also handles modules that have BOTH registers AND submodules
            for module_name, module in base_modules.items():
                has_registers = len(module.registers) > 0
                if has_registers:
                    self._generate_base_module(module, result, base_modules)

            # Third pass: generate code for empty modules (placeholders)
            for module_name, module in base_modules.items():
                has_registers = len(module.registers) > 0
                has_submodules = len(module.submodules) > 0
                if not has_registers and not has_submodules:
                    self._generate_empty_module_rdl(module, result)

            # Fourth pass: generate code for addr-map modules last (they need includes)
            # Only handle modules with submodules but NO registers (modules with both were handled in pass 2)
            for module_name, module in base_modules.items():
                has_registers = len(module.registers) > 0
                has_submodules = len(module.submodules) > 0
                if has_submodules and not has_registers:
                    self._generate_addrmap_module(module, result, base_modules)

            # Generate common headers
            self._generate_common_headers(result)

        except ValueError as e:
            # Validation error (e.g., field bit width mismatch) - cleanup and re-raise
            self.errors.append(str(e))
            self.cleanup_generated_files(version_id, version_name, user_id='default')
            raise

        return result

    def _generate_common_headers(self, result: Dict):
        """Generate common header files for stdint.h and REG32/REG64 definitions"""
        # Common C header
        common_c_header = """/* Common C Header for Register Definitions */
/* Auto-generated - Do not modify */

#ifndef _REG_COMMON_H_
#define _REG_COMMON_H_

#include <stdint.h>

#define REG32(_addr) (*(volatile uint32_t *)(_addr))
#define REG64(_addr) (*(volatile uint64_t *)(_addr))

#endif /* _REG_COMMON_H_ */
"""
        result['header']['reg_common'] = common_c_header

        # Common SV header (empty for now, just a placeholder)
        common_sv_header = """// Common SystemVerilog Header for Register Definitions
// Auto-generated - Do not modify

`ifndef _REG_COMMON_SVH_
`define _REG_COMMON_SVH_

`endif // _REG_COMMON_SVH_
"""
        result['svh']['reg_common'] = common_sv_header

    def _validate_module_fields(self, module: Module):
        """Validate all register fields in a module before generation"""
        for reg in module.registers:
            reg_width = reg.width if reg.width else 32
            for field in reg.fields:
                msb = field.msb
                lsb = field.lsb

                # Check for negative bit positions
                if msb < 0:
                    raise ValueError(f"Field '{field.name}' in register '{reg.name}' has negative msb={msb}")
                if lsb < 0:
                    raise ValueError(f"Field '{field.name}' in register '{reg.name}' has negative lsb={lsb}")

                # Check for invalid bit range
                if msb < lsb:
                    raise ValueError(f"Field '{field.name}' in register '{reg.name}' has invalid bit range [{msb}:{lsb}] (msb < lsb)")

                # Check field bit range against register width
                if msb >= reg_width:
                    raise ValueError(f"Field '{field.name}' in register '{reg.name}' has msb={msb} exceeding register width {reg_width}")
                if lsb >= reg_width:
                    raise ValueError(f"Field '{field.name}' in register '{reg.name}' has lsb={lsb} exceeding register width {reg_width}")

    def _generate_base_module(self, module: Module, result: Dict, all_modules: Dict[str, Module] = None):
        """Generate code for a base register module (has registers)

        For register modules, use PeakRDL to generate UVM and C Header.
        RALF and SVH are generated using RDLExporter (custom implementation).
        """
        module_name = module.name

        # Skip if already generated (same base module used multiple times)
        if module_name in self.generated_base_modules:
            return
        self.generated_base_modules.add(module_name)

        # Validate all fields before generation
        self._validate_module_fields(module)

        # Step 1: Generate RDL first (source of truth)
        try:
            rdl_content = self._generate_rdl_for_base_module(module, all_modules)
            if rdl_content:
                result['rdl'][module_name] = rdl_content
        except ValueError:
            raise  # Re-raise validation errors
        except Exception as e:
            self.errors.append(f"RDL generation failed for {module_name}: {e}")
            return  # Cannot proceed without RDL

        # Step 2: Use PeakRDL for UVM and C Header (register modules only)
        # Skip if skip_peakrdl is True (for transactional upload)
        cheader_content = None
        if getattr(self, 'skip_peakrdl', False):
            # Use fallback generation without PeakRDL
            self._generate_cheader_and_svh_from_exporter(rdl_content, module_name, result)
        else:
            try:
                from app.services.peakrdl_wrapper import PeakRDLGenerator
                peakrdl_gen = PeakRDLGenerator()

                if peakrdl_gen.is_available():
                    # Generate UVM using PeakRDL
                    success, uvm_content = peakrdl_gen.generate_uvm_for_module(rdl_content, module_name)
                    if success:
                        result['uvm'][module_name] = uvm_content
                    else:
                        self.warnings.append(f"PeakRDL UVM generation for {module_name}: {uvm_content}")

                    # Generate C Header using PeakRDL (generate_bitfields=False for simple macros)
                    success, cheader_content = peakrdl_gen.generate_cheader_for_module(
                        rdl_content, module_name, generate_bitfields=False
                    )
                    if success:
                        # Append register address macros to C Header
                        cheader_content = self._append_register_addresses_to_cheader(cheader_content, module)
                        result['header'][module_name] = cheader_content
                        # Convert C Header to SVH (same name, different extension)
                        svh_content = self._convert_cheader_to_svh(cheader_content, module_name)
                        result['svh'][module_name] = svh_content
                    else:
                        self.warnings.append(f"PeakRDL C Header generation for {module_name}: {cheader_content}")
                else:
                    self.warnings.append(f"PeakRDL not available for {module_name}, using fallback generation")
                    # Fallback to RDLExporter for C Header and SVH
                    self._generate_cheader_and_svh_from_exporter(rdl_content, module_name, result)

            except Exception as e:
                self.errors.append(f"PeakRDL generation failed for {module_name}: {e}")
                # Fallback to RDLExporter
                self._generate_cheader_and_svh_from_exporter(rdl_content, module_name, result)

        # Step 3: Use RDLExporter for RALF (PeakRDL doesn't support RALF)
        try:
            exporter = RDLExporter()
            export_results = exporter.export_from_rdl_content(rdl_content, module_name)

            if export_results.get('ralf'):
                result['ralf'][module_name] = export_results['ralf']

            # Note: C Header and SVH are already generated by PeakRDL above, only use as fallback
            if not result['header'].get(module_name):
                if export_results.get('header'):
                    result['header'][module_name] = export_results['header']
                if export_results.get('svh'):
                    result['svh'][module_name] = export_results['svh']

            # Collect any warnings/errors from exporter
            self.warnings.extend(exporter.get_warnings())
            self.errors.extend(exporter.get_errors())

        except Exception as e:
            self.errors.append(f"RDL export failed for {module_name}: {e}")
            # Fallback: use direct generation
            self._fallback_generate_from_direct(module, result, all_modules)

        # Step 4: Generate RTL using PeakRDL (register modules only)
        # Skip if skip_peakrdl is True (for transactional upload)
        if not getattr(self, 'skip_peakrdl', False):
            try:
                from app.services.peakrdl_wrapper import PeakRDLGenerator
                peakrdl_gen = PeakRDLGenerator()

                if peakrdl_gen.is_available():
                    success, rtl_content = peakrdl_gen.generate_rtl_for_module(
                        rdl_content, module_name, cpu_if="axilite", address_width=32
                    )
                    if success:
                        result['rtl'][module_name] = rtl_content
                    else:
                        self.errors.append(f"PeakRDL RTL generation for {module_name}: {rtl_content}")
                else:
                    self.errors.append(f"PeakRDL not available for RTL {module_name}")

            except Exception as e:
                self.errors.append(f"RTL generation failed for {module_name}: {e}")

    def _append_register_addresses_to_cheader(self, cheader_content: str, module: Module) -> str:
        """Append register address offset macros to C Header content

        Adds macros like:
            #define MODULE_REG_NAME_ADDR 0xOFFSET

        Args:
            cheader_content: Original C Header content from PeakRDL
            module: Module object with register information

        Returns:
            C Header content with address macros appended
        """
        if not module.registers:
            return cheader_content

        # Find the position to insert (before #ifdef __cplusplus or before #endif at end)
        lines = cheader_content.split('\n')

        # Generate address macros section
        addr_lines = []
        addr_lines.append("")
        addr_lines.append("// Register Address Offsets")

        for reg in module.registers:
            reg_name_upper = reg.name.upper()
            module_name_upper = module.name.upper()
            offset = reg.offset if reg.offset is not None else 0
            addr_lines.append(f"#define {module_name_upper}__{reg_name_upper}_ADDR 0x{offset:X}")

        addr_lines.append("")

        # Insert before the last #endif (which closes the header guard)
        # Find the last #endif that corresponds to the header guard
        insert_pos = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith('#endif'):
                insert_pos = i
                break

        # Insert address macros at the found position
        new_lines = lines[:insert_pos] + addr_lines + lines[insert_pos:]
        return '\n'.join(new_lines)

    def _convert_cheader_to_svh(self, cheader_content: str, module_name: str) -> str:
        """Convert C Header content to SystemVerilog Header format

        Conversion rules:
        - #ifndef / #define / #endif -> `ifndef / `define / `endif
        - #include "xxx.h" -> `include "xxx.svh" (C standard headers are removed)
        - #define -> `define
        - /* */ comments -> // comments
        - Remove C++ extern "C" guards
        - Same filename (just .svh instead of .h)
        """
        import re
        lines = cheader_content.split('\n')
        svh_lines = []
        in_extern_c_block = False
        extern_c_brace_depth = 0
        in_struct_block = False
        struct_brace_depth = 0

        for line in lines:
            stripped = line.strip()

            # Handle C++ blocks: skip #ifdef __cplusplus and extern "C"
            if '#ifdef __cplusplus' in stripped or (stripped.startswith('#ifdef') and '__cplusplus' in stripped):
                in_extern_c_block = True
                extern_c_brace_depth = 0
                continue

            if in_extern_c_block:
                # Track braces to find end of extern "C" block
                if '{' in stripped:
                    extern_c_brace_depth += stripped.count('{')
                if '}' in stripped:
                    extern_c_brace_depth -= stripped.count('}')

                # Check for #endif that closes __cplusplus block
                if stripped.startswith('#endif'):
                    in_extern_c_block = False
                    continue
                continue  # Skip all lines inside __cplusplus block

            # Skip C struct typedefs (not valid in SV)
            if 'typedef struct' in stripped or (stripped.startswith('typedef') and 'struct' in stripped and '__attribute__' in stripped):
                in_struct_block = True
                struct_brace_depth = 0
                continue

            if in_struct_block:
                if '{' in stripped:
                    struct_brace_depth += stripped.count('{')
                if '}' in stripped:
                    struct_brace_depth -= stripped.count('}')
                # Check for end of typedef struct (ends with } name;)
                if stripped.startswith('}') and ';' in stripped and struct_brace_depth <= 0:
                    in_struct_block = False
                continue  # Skip all struct-related lines

            # Skip static_assert lines (not valid in SV)
            if 'static_assert' in stripped:
                continue

            # Skip C type definitions (uint64_t, etc.) - these are in struct members
            if re.search(r'\b(uint64_t|uint32_t|uint16_t|uint8_t|int64_t|int32_t|int16_t|int8_t)\b', stripped):
                continue

            # Convert C-style comments to SV-style
            if '/*' in line and '*/' in line:
                line = line.replace('/*', '//')
                line = line.replace('*/', '')
            elif stripped.startswith('/*'):
                # Start of multi-line comment
                line = line.replace('/*', '//')
            elif stripped.startswith('*') and not stripped.startswith('*/'):
                # Continuation of block comment
                line = '//' + line[line.find('*')+1:]
            elif stripped.startswith('*/'):
                # End of block comment
                continue

            # Skip C standard headers (angle bracket includes like <stdint.h>)
            if re.search(r'#include\s+<[^>]+>', line):
                continue

            # Convert user headers: #include "filename.h" -> `include "filename.svh"
            line = re.sub(r'#include\s+"([^"]+)\.h"', r'`include "\1.svh"', line)

            # Convert preprocessor directives
            if '#ifndef' in line:
                # Convert all #ifndef to `ifndef
                line = line.replace('#ifndef', '`ifndef')
            elif '#define' in line:
                # Convert header guard name ( XXX_H -> XXX_SVH ) or regular #define
                if re.search(r'#define\s+(_?\w+)_H(_?)\b', line):
                    line = re.sub(r'#define\s+(_?\w+)_H(_?)\b', r'`define \1_SVH\2', line)
                else:
                    # Convert other #define to `define
                    line = line.replace('#define', '`define')
            elif '#endif' in line:
                # End of header guard
                line = re.sub(r'#endif\s*/?\*?.*', r'`endif', line)
            elif '#include' in line:
                line = line.replace('#include', '`include')

            # Convert C-style hex constants (0x) to SV-style ('h)
            # Match 0x[hex_digits] that are not part of a word (to avoid matching inside identifiers)
            # Use word boundary or space to ensure we match complete numbers
            line = re.sub(r'\b0x([0-9a-fA-F]+)\b', r"'h\1", line)

            svh_lines.append(line)

        return '\n'.join(svh_lines)

    def _generate_cheader_and_svh_from_exporter(self, rdl_content: str, module_name: str, result: Dict):
        """Generate C Header and SVH using RDLExporter as fallback"""
        try:
            from app.services.rdl_exporter import RDLExporter
            exporter = RDLExporter()
            export_results = exporter.export_from_rdl_content(rdl_content, module_name)

            if export_results.get('header'):
                result['header'][module_name] = export_results['header']
                # Convert to SVH
                svh_content = self._convert_cheader_to_svh(export_results['header'], module_name)
                result['svh'][module_name] = svh_content

            if export_results.get('svh') and not result.get('svh', {}).get(module_name):
                result['svh'][module_name] = export_results['svh']

            self.warnings.extend(exporter.get_warnings())
        except Exception as e:
            self.errors.append(f"Fallback C Header/SVH generation failed for {module_name}: {e}")

    def _fallback_generate_from_direct(self, module: Module, result: Dict, all_modules: Dict[str, Module] = None):
        """Fallback: Directly generate RALF, C Header, SVH without RDL export"""
        module_name = module.name

        # Generate RALF directly
        try:
            if len(module.submodules) > 0:
                ralf_content = self._generate_ralf_for_addrmap_module(module)
            else:
                ralf_content = self._generate_ralf_for_base_module(module)
            if ralf_content:
                result['ralf'][module_name] = ralf_content
        except Exception as e:
            self.errors.append(f"Fallback RALF generation failed for {module_name}: {e}")

        # Generate C Header directly
        try:
            if len(module.submodules) > 0 and all_modules:
                header_content = self._generate_c_header_for_addrmap_module(module, all_modules)
            else:
                header_content = self._generate_c_header_for_base_module(module)
            if header_content:
                result['header'][module_name] = header_content
        except Exception as e:
            self.errors.append(f"Fallback C Header generation failed for {module_name}: {e}")

        # Generate SVH directly
        try:
            if len(module.submodules) > 0 and all_modules:
                svh_content = self._generate_svh_for_addrmap_module(module, all_modules)
            else:
                svh_content = self._generate_svh_for_base_module(module)
            if svh_content:
                result['svh'][module_name] = svh_content
        except Exception as e:
            self.errors.append(f"Fallback SVH generation failed for {module_name}: {e}")

    def _generate_addrmap_module(self, module: Module, result: Dict, all_modules: Dict[str, Module]):
        """Generate code for an addr-map module (no registers, only submodules)"""
        module_name = module.name

        # Generate RALF with includes for submodules
        try:
            ralf_content = self._generate_ralf_for_addrmap_module(module)
            if ralf_content:
                result['ralf'][module_name] = ralf_content
        except ValueError:
            raise  # Re-raise validation errors
        except Exception as e:
            self.errors.append(f"RALF generation failed for addr-map {module_name}: {e}")

        # Generate C Header with base address macros
        header_content = None
        try:
            header_content = self._generate_c_header_for_addrmap_module(module, all_modules)
            if header_content:
                result['header'][module_name] = header_content
        except ValueError:
            raise  # Re-raise validation errors
        except Exception as e:
            self.errors.append(f"C Header generation failed for addr-map {module_name}: {e}")

        # Generate SVH by converting C Header (same name, different extension)
        try:
            if header_content:
                svh_content = self._convert_cheader_to_svh(header_content, module_name)
                result['svh'][module_name] = svh_content
            else:
                # Fallback to direct SVH generation
                svh_content = self._generate_svh_for_addrmap_module(module, all_modules)
                if svh_content:
                    result['svh'][module_name] = svh_content
        except ValueError:
            raise  # Re-raise validation errors
        except Exception as e:
            self.errors.append(f"SVH generation failed for addr-map {module_name}: {e}")

        # Generate RDL with includes for submodules
        rdl_content = None
        try:
            # Pass already generated RDL modules to avoid including non-existent files
            generated_rdl_modules = set(result['rdl'].keys())
            rdl_content = self._generate_rdl_for_addrmap_module(module, generated_rdl_modules, all_modules)
            if rdl_content:
                result['rdl'][module_name] = rdl_content
        except ValueError:
            raise  # Re-raise validation errors
        except Exception as e:
            self.errors.append(f"RDL generation failed for addr-map {module_name}: {e}")

        # Generate UVM for addr-map modules using PeakRDL (hierarchical instantiation only)
        # Skip if skip_peakrdl is True (for transactional upload)
        if rdl_content and not getattr(self, 'skip_peakrdl', False):
            try:
                from app.services.peakrdl_wrapper import PeakRDLGenerator
                peakrdl_gen = PeakRDLGenerator()

                if peakrdl_gen.is_available():
                    # For addr-map modules with includes, need to provide include paths
                    # Create temp directory with all dependent RDL files
                    import tempfile
                    with tempfile.TemporaryDirectory() as temp_rdl_dir:
                        temp_rdl_path = Path(temp_rdl_dir)

                        # Write all previously generated RDL files to temp directory
                        for dep_name, dep_rdl in result['rdl'].items():
                            dep_file = temp_rdl_path / f"{dep_name}.rdl"
                            dep_file.write_text(dep_rdl, encoding='utf-8')

                        # Generate UVM - pass temp_rdl_dir as output_dir so RDL is written there
                        # This ensures includes work correctly (same directory)
                        success, uvm_content = peakrdl_gen.generate_uvm_for_module(
                            rdl_content, module_name,
                            include_paths=[temp_rdl_dir],
                            output_dir=temp_rdl_dir
                        )
                        if success:
                            result['uvm'][module_name] = uvm_content
                        else:
                            self.warnings.append(f"PeakRDL UVM generation for addr-map {module_name}: {uvm_content}")
                else:
                    self.warnings.append(f"PeakRDL not available for addr-map {module_name}")

            except Exception as e:
                self.warnings.append(f"UVM generation failed for addr-map {module_name}: {e}")

        # No RTL generation for addr-map modules

    def _generate_ralf_for_base_module(self, module: Module) -> str:
        """Generate RALF for a base module (0-base address)"""
        lines = [
            "// UVM RALF Register Description",
            f"// Module: {module.name}",
            f"// This is a base register module",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
            f"block {module.name} @0x0 {{",
            f"    bytes {module.size if module.size > 0 else 1024};",
            "",
        ]

        # Generate registers (0-base offsets)
        for reg in module.registers:
            lines.extend(self._generate_ralf_register(reg))

        lines.append("}")
        return '\n'.join(lines)

    def _get_base_module_name(self, name: str) -> str:
        """Get base module name by removing trailing _digits or digits (e.g., PEC0 -> PEC, CPD_0 -> CPD)"""
        import re
        # First try to match _digits pattern (e.g., CPD_0 -> CPD)
        match = re.match(r'^(.+?)_\d+$', name)
        if match:
            return match.group(1)
        # Then try to match digits pattern (e.g., PEC0 -> PEC)
        match = re.match(r'^(.+?)(\d+)$', name)
        if match:
            return match.group(1)
        return name

    def _parse_size_to_bytes(self, size_val) -> int:
        """Parse size value to bytes, handling KB/MB suffixes

        KB = 1024 bytes, MB = 1024*1024 bytes
        """
        import re
        if size_val is None:
            return 0

        size_str = str(size_val).strip().upper()

        # Remove spaces
        size_str = size_str.replace(' ', '')

        # Match number with optional suffix
        match = re.match(r'^(\d+(?:\.\d+)?)\s*(KB|MB|K|M|B)?$', size_str, re.IGNORECASE)
        if not match:
            # Try plain number
            try:
                return int(size_str)
            except:
                return 0

        num = float(match.group(1))
        suffix = match.group(2) if match.group(2) else ''

        if suffix in ('KB', 'K'):
            return int(num * 1024)
        elif suffix in ('MB', 'M'):
            return int(num * 1024 * 1024)
        else:
            return int(num)

    def _generate_ralf_for_addrmap_module(self, module: Module) -> str:
        """Generate RALF for an addr-map module with includes

        For addr-map type sheets:
        - Base address starts from 0x0 (relative addressing)
        - Size is taken from the module's size field (KB=1024, MB=1024*1024)
        - For *N format arrays, addresses are calculated sequentially:
          First item @0x0, Second @0x0+size, Third @0x0+2*size, etc.
        """
        lines = [
            "// UVM RALF Address Map Description",
            f"// Addr-Map: {module.name}",
            f"// This module contains submodule instances only",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
        ]

        # Include base submodules (without trailing digits)
        # For PEC0, PEC1 - only include PEC.ralf once
        included_bases = set()
        for sub in module.submodules:
            base_name = self._get_base_module_name(sub.name)
            if base_name not in included_bases:
                lines.append(f"#include \"{base_name}.ralf\"")
                included_bases.add(base_name)

        lines.append("")
        # Addr-map starts from 0x0 (relative addressing)
        lines.append(f"block {module.name} @0x0 {{")
        # Convert size to bytes (KB=1024, MB=1024*1024)
        size_bytes = self._parse_size_to_bytes(module.size)
        lines.append(f"    bytes {size_bytes};")
        lines.append("")

        # Instance submodules with their relative offsets
        # For *N format: addresses are calculated sequentially from 0
        # First item @0x0, Second @0x0+size, etc.
        prev_end_addr = 0
        for i, sub in enumerate(module.submodules):
            base_name = self._get_base_module_name(sub.name)
            # Calculate offset sequentially for array instances
            if i == 0:
                offset = 0
            else:
                offset = prev_end_addr

            lines.append(f"    // Instance of {sub.name}")
            # Use base module type but instance name with suffix
            lines.append(f"    {base_name} {sub.name}_inst @0x{offset:X};")

            # Update prev_end_addr for next iteration
            sub_size = self._parse_size_to_bytes(sub.size)
            prev_end_addr = offset + sub_size

        lines.append("}")
        return '\n'.join(lines)

    def _generate_ralf_register(self, reg: Register) -> List[str]:
        """Generate RALF register definition

        Handles duplicate field names (e.g., multiple 'reserved' fields) by adding suffixes.
        """
        lines = [
            f"    register {reg.name} @0x{reg.offset:X} {{",
            f"        bytes {reg.width // 8};",
        ]

        # Track field names to handle duplicates (e.g., multiple 'reserved' fields)
        used_field_names = {}

        for field in reg.fields:
            access = self._map_ralf_access(field.access)
            reset_val = self._parse_reset_value(field.reset_value)

            # Handle duplicate field names (same logic as RDL)
            field_name = field.name
            if field_name in used_field_names:
                used_field_names[field_name] += 1
                field_name = f"{field_name}_{used_field_names[field_name]}"
            else:
                used_field_names[field_name] = 0

            lines.append(f"        field {field_name} @{field.lsb} {{")
            lines.append(f"            bits {field.msb - field.lsb + 1};")
            lines.append(f"            access {access};")
            lines.append(f"            reset {reset_val};")
            lines.append(f"        }}")

        lines.append(f"    }}")
        return lines

    def _generate_c_header_for_base_module(self, module: Module) -> str:
        """Generate C Header for a base module (0-base addresses)"""
        guard_name = f"_{module.name.upper()}_H_"

        lines = [
            f"/* C Header for {module.name} */",
            f"/* This is a base register module (0-base, relative addresses) */",
            f"/* Generated at: {datetime.now().isoformat()} */",
            "",
            f"#ifndef {guard_name}",
            f"#define {guard_name}",
            "",
            '#include "reg_common.h"',
            "",
            f"/* Module: {module.name} */",
            f"/* NOTE: This header uses 0-base relative addresses */",
            f"/*       Add base address offset when accessing actual hardware */",
            "",
        ]

        # Generate register definitions (0-base offsets)
        for reg in module.registers:
            reg_name = f"{module.name.upper()}_{reg.name.upper()}"

            lines.extend([
                f"/* Register: {reg.name} */",
                f"#define {reg_name}_OFFSET    0x{reg.offset:04X}",
            ])

            # Generate field definitions
            for field in reg.fields:
                field_name = f"{reg_name}_{field.name.upper()}"
                width = field.msb - field.lsb + 1
                mask = (1 << width) - 1

                lines.extend([
                    f"#define {field_name}_POS     {field.lsb}",
                    f"#define {field_name}_MASK    0x{mask:08X}",
                    f"#define {field_name}_WIDTH   {width}",
                ])

            lines.append("")

        lines.extend([
            f"#endif /* {guard_name} */",
            "",
        ])

        return '\n'.join(lines)

    def _collect_all_submodules_recursive(self, module: Module) -> List[Tuple[str, Module]]:
        """
        Recursively collect all submodules with their hierarchical path.
        Returns list of (hierarchical_name, module) tuples.
        Example: [("GCS_CPD0", cpd0_module), ("GCS_CPD1", cpd1_module), ...]
        """
        result = []

        def collect(parent_name: str, mod: Module):
            for sub in mod.submodules:
                # Build hierarchical name: parent_sub
                if parent_name:
                    hier_name = f"{parent_name}_{sub.name}"
                else:
                    hier_name = sub.name

                # Add this submodule
                result.append((hier_name, sub))

                # Recursively collect nested submodules
                if sub.submodules:
                    collect(hier_name, sub)

        collect("", module)
        return result

    def _generate_c_header_for_addrmap_module(self, module: Module, all_modules: Dict[str, Module]) -> str:
        """Generate C Header for an addr-map module with base address macros

        Rules:
        1. Base address uses absolute address from Excel
        2. Only includes direct submodules (not recursively)
        3. Checks if submodule addresses exceed module size
        """
        guard_name = f"_{module.name.upper()}_H_"

        # Parse size and check bounds
        module_size = self._parse_size_to_bytes(module.size)

        lines = [
            f"/* C Header for Address Map: {module.name} */",
            f"/* Generated at: {datetime.now().isoformat()} */",
            "",
            f"#ifndef {guard_name}",
            f"#define {guard_name}",
            "",
            '#include "reg_common.h"',
            "",
            f"/* Address Map: {module.name} */",
            f"#ifndef {module.name.upper()}_BASE_ADDR",
            f"#define {module.name.upper()}_BASE_ADDR    0x{module.start_addr:08X}",
            f"#endif",
            f"#ifndef {module.name.upper()}_SIZE",
            f"#define {module.name.upper()}_SIZE         0x{module_size:08X}",
            f"#endif",
            "",
        ]

        # Only process direct submodules (not recursive)
        direct_submodules = module.submodules

        # Include base module headers (without trailing digits)
        included_bases = set()
        for sub in direct_submodules:
            base_name = self._get_base_module_name(sub.name)
            if base_name not in included_bases:
                base_module = all_modules.get(base_name)
                if base_module and len(base_module.registers) > 0:
                    lines.append(f"#include \"{base_name}.h\"")
                    included_bases.add(base_name)

        lines.append("")
        lines.append("/* Sub-module instance base addresses (relative to parent) */")

        # Check size bounds and generate base address macros
        prev_end_addr = 0
        for i, sub in enumerate(direct_submodules):
            sub_size = self._parse_size_to_bytes(sub.size)

            # Calculate relative offset (first at 0, subsequent sequential)
            if i == 0:
                rel_offset = 0
            else:
                rel_offset = prev_end_addr

            # Check if address exceeds module size
            if rel_offset + sub_size > module_size:
                raise ValueError(
                    f"Module '{module.name}': submodule '{sub.name}' at offset 0x{rel_offset:X} "
                    f"with size 0x{sub_size:X} exceeds module size 0x{module_size:X}"
                )

            sub_base_name = f"{module.name.upper()}_{sub.name.upper()}_BASE"
            lines.append(f"#ifndef {sub_base_name}")
            lines.append(f"#define {sub_base_name}    0x{rel_offset:08X}")
            lines.append(f"#endif")

            prev_end_addr = rel_offset + sub_size

        lines.append("")
        lines.extend([
            f"#endif /* {guard_name} */",
            "",
        ])

        return '\n'.join(lines)

    def _generate_svh_for_base_module(self, module: Module) -> str:
        """Generate SystemVerilog Header for a base module (0-base addresses)"""
        guard_name = f"_{module.name.upper()}_SVH_"

        lines = [
            f"// SystemVerilog Header for {module.name}",
            f"// This is a base register module (0-base, relative addresses)",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
            f"`ifndef {guard_name}",
            f"`define {guard_name}",
            "",
            "`include \"reg_common.svh\"",
            "",
            f"// Module: {module.name}",
            f"// NOTE: This header uses 0-base relative addresses",
            f"//       Add base address offset when accessing actual hardware",
            "",
        ]

        # Generate register definitions (0-base offsets)
        for reg in module.registers:
            reg_name = f"{module.name.upper()}_{reg.name.upper()}"

            lines.extend([
                f"// Register: {reg.name}",
                f"`define {reg_name}_OFFSET    16'h{reg.offset:04X}",
            ])

            # Generate field definitions
            for field in reg.fields:
                field_name = f"{reg_name}_{field.name.upper()}"
                width = field.msb - field.lsb + 1
                mask = (1 << width) - 1

                lines.extend([
                    f"`define {field_name}_POS     {field.lsb}",
                    f"`define {field_name}_MASK    {width}'h{mask:X}",
                    f"`define {field_name}_WIDTH   {width}",
                ])

            lines.append("")

        lines.extend([
            f"`endif // {guard_name}",
            "",
        ])

        return '\n'.join(lines)

    def _generate_svh_for_addrmap_module(self, module: Module, all_modules: Dict[str, Module]) -> str:
        """Generate SystemVerilog Header for an addr-map module with base address macros

        Rules:
        1. Base address uses absolute address from Excel
        2. Only includes direct submodules (not recursively)
        3. Checks if submodule addresses exceed module size
        """
        guard_name = f"_{module.name.upper()}_SVH_"

        # Parse size and check bounds
        module_size = self._parse_size_to_bytes(module.size)

        lines = [
            f"// SystemVerilog Header for Address Map: {module.name}",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
            f"`ifndef {guard_name}",
            f"`define {guard_name}",
            "",
            "`include \"reg_common.svh\"",
            "",
            f"// Address Map: {module.name}",
            f"`ifndef {module.name.upper()}_BASE_ADDR",
            f"`define {module.name.upper()}_BASE_ADDR    32'h{module.start_addr:08X}",
            f"`endif",
            f"`ifndef {module.name.upper()}_SIZE",
            f"`define {module.name.upper()}_SIZE         32'h{module_size:08X}",
            f"`endif",
            "",
        ]

        # Only process direct submodules (not recursive)
        direct_submodules = module.submodules

        # Include base module headers (without trailing digits)
        included_bases = set()
        for sub in direct_submodules:
            base_name = self._get_base_module_name(sub.name)
            if base_name not in included_bases:
                base_module = all_modules.get(base_name)
                if base_module and len(base_module.registers) > 0:
                    lines.append(f"`include \"{base_name}.svh\"")
                    included_bases.add(base_name)

        lines.append("")
        lines.append("// Sub-module instance base addresses (relative to parent)")

        # Check size bounds and generate base address macros
        prev_end_addr = 0
        for i, sub in enumerate(direct_submodules):
            sub_size = self._parse_size_to_bytes(sub.size)

            # Calculate relative offset (first at 0, subsequent sequential)
            if i == 0:
                rel_offset = 0
            else:
                rel_offset = prev_end_addr

            # Check if address exceeds module size
            if rel_offset + sub_size > module_size:
                raise ValueError(
                    f"Module '{module.name}': submodule '{sub.name}' at offset 0x{rel_offset:X} "
                    f"with size 0x{sub_size:X} exceeds module size 0x{module_size:X}"
                )

            sub_base_name = f"{module.name.upper()}_{sub.name.upper()}_BASE"
            lines.append(f"`ifndef {sub_base_name}")
            lines.append(f"`define {sub_base_name}    32'h{rel_offset:08X}")
            lines.append(f"`endif")

            prev_end_addr = rel_offset + sub_size

        lines.append("")
        lines.extend([
            f"`endif // {guard_name}",
            "",
        ])

        return '\n'.join(lines)

    def _generate_rdl_for_base_module(self, module: Module, all_modules: Dict[str, Module] = None) -> str:
        """Generate SystemRDL 2.0 for a base module (0-base addresses)

        Also handles submodules if the module has both registers AND submodules.
        """
        # Determine the register width based on max field bit position
        max_bit = 0
        for reg in module.registers:
            for field in reg.fields:
                if field.msb > max_bit:
                    max_bit = field.msb
        # Use 64 if any field exceeds 31, otherwise 32
        regwidth = 64 if max_bit >= 32 else 32

        has_submodules = len(module.submodules) > 0

        lines = [
            f"// SystemRDL 2.0 Register Description",
            f"// Module: {module.name}",
            f"// This is a base register module (0-base, relative addresses)",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
            f"addrmap {module.name} {{",
            f"    name = \"{module.name}\";",
            f"    default regwidth = {regwidth};",
            "    addressing = regalign;",
            "    lsb0;",
            "",
        ]

        # Generate registers (0-base offsets)
        for reg in module.registers:
            lines.extend(self._generate_rdl_register(reg, reg.width))

        # Generate submodule instances (if this module has submodules)
        if has_submodules:
            lines.append("")
            lines.append("    // Submodule instances")
            for sub in module.submodules:
                base_name = self._get_base_module_name(sub.name)
                offset = sub.start_addr - module.start_addr
                lines.append(f"    // Instance of {sub.name}")
                lines.append(f"    {base_name} {sub.name}_inst @0x{offset:08X};")
                lines.append("")

        lines.extend([
            "};",
            "",
        ])

        return '\n'.join(lines)

    def _generate_rdl_for_addrmap_module(self, module: Module, generated_modules: set = None,
                                          all_modules: Dict[str, Module] = None) -> str:
        """Generate SystemRDL 2.0 for an addr-map module with includes

        For addr-map type sheets:
        - Base address starts from 0x0 (relative addressing)
        - Size is taken from the module's size field (KB=1024, MB=1024*1024)
        - For *N format arrays, addresses are calculated sequentially:
          First item @0x0, Second @0x0+size, Third @0x0+2*size, etc.

        For *N format modules (e.g., PEC0, PEC1):
        - Include base module RDL (`include "PEC.rdl")
        - Instantiate with full instance name (PEC0_inst, PEC1_inst)

        To avoid duplicate includes, register modules that are already included by
        addr-map submodules are not included directly.
        """
        if generated_modules is None:
            generated_modules = set()
        if all_modules is None:
            all_modules = {}

        lines = [
            f"// SystemRDL 2.0 Address Map Description",
            f"// Addr-Map: {module.name}",
            f"// This module contains submodule instances only",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
        ]

        # First, identify addr-map submodules (they have their own submodules)
        # These will be included and they will include their own register dependencies
        addrmap_submodules = []
        register_submodules = []

        for sub in module.submodules:
            base_name = self._get_base_module_name(sub.name)
            # Check if base module exists in all_modules (not just generated_modules)
            # This allows addr-map modules to include submodules that will be generated
            base_module = all_modules.get(base_name)
            if not base_module:
                self.warnings.append(f"Submodule {sub.name} references unknown base module {base_name}")
                continue

            if base_module and len(base_module.submodules) > 0:
                # This is an addr-map submodule - it will include its own dependencies
                addrmap_submodules.append((sub, base_name))
            else:
                # This is a register submodule - may need direct include
                register_submodules.append((sub, base_name))

        # Collect all register modules that will be transitively included by addr-map submodules
        transitively_included_registers = set()
        for sub, base_name in addrmap_submodules:
            base_module = all_modules.get(base_name)
            if base_module:
                # Get all register submodules of this addr-map submodule
                for subsub in base_module.submodules:
                    subsub_base = self._get_base_module_name(subsub.name)
                    subsub_module = all_modules.get(subsub_base)
                    if subsub_module and len(subsub_module.registers) > 0:
                        transitively_included_registers.add(subsub_base)

        # Build include statements (addr-map types first, then register types)
        # But keep original order for instantiation to maintain correct address calculation
        included_bases = set()

        # First, generate include statements for addr-map submodules
        # Addr-map submodules will always be generated (either already or later), so always include
        for sub, base_name in addrmap_submodules:
            if base_name not in included_bases:
                lines.append(f"`include \"{base_name}.rdl\"")
                included_bases.add(base_name)

        # Then, generate include statements for register submodules
        # Only include register modules that have already been generated
        # (register modules are generated first, so they should be in generated_modules)
        for sub, base_name in register_submodules:
            if (base_name not in included_bases and
                base_name not in transitively_included_registers and
                base_name in generated_modules):
                lines.append(f"`include \"{base_name}.rdl\"")
                included_bases.add(base_name)

        # For instantiation, maintain original submodule order (important for *N address calculation)
        # Rebuild included_submodules in original order from module.submodules
        included_submodules = []  # All submodule instances (for instantiation)
        for sub in module.submodules:
            base_name = self._get_base_module_name(sub.name)
            if base_name in all_modules:
                included_submodules.append((sub, base_name))

        lines.append("")
        lines.extend([
            f"addrmap {module.name} {{",
            f"    name = \"{module.name}\";",
            f"    desc = \"Address map for {module.name}\";",
            "",
        ])

        # Instantiate submodules with relative offsets from the database
        # Use actual relative address from Excel, not sequential calculation
        for sub, base_name in included_submodules:
            # Calculate relative offset from parent module's start address
            rel_offset = sub.start_addr - module.start_addr
            # Handle edge case where addresses might not be properly set
            if rel_offset < 0:
                rel_offset = 0

            lines.append(f"    // Instance of {sub.name}")
            lines.append(f"    {base_name} {sub.name}_inst @0x{rel_offset:08X};")
            lines.append("")

        lines.extend([
            "};",
            "",
        ])

        return '\n'.join(lines)

    def _generate_empty_module_rdl(self, module: Module, result: Dict):
        """Generate RDL for empty modules (no registers, no submodules)

        Adds an 'occupy' register to satisfy SystemRDL requirement that
        addrmap must contain at least one reg, regfile, mem, or addrmap.
        """
        module_name = module.name

        # Generate RALF with occupy register
        try:
            ralf_lines = [
                f"// UVM RALF Register Description",
                f"// Module: {module_name}",
                f"// This module has an occupy placeholder register",
                f"// Generated at: {datetime.now().isoformat()}",
                "",
                f"block {module_name} @0x0 {{",
                f"    bytes 4;",
                "",
                "    // Occupy register - placeholder for empty module",
                "    register occupy @0x0 {",
                "        bytes 4;",
                "        field occupy_val @0 {",
                "            bits 32;",
                "            access rw;",
                "            reset 0x0;",
                "        }",
                "    }",
                "}};",
                "",
            ]
            result['ralf'][module_name] = '\n'.join(ralf_lines)
        except Exception as e:
            self.errors.append(f"RALF generation failed for empty module {module_name}: {e}")

        # Generate RDL with occupy register
        try:
            rdl_lines = [
                f"// SystemRDL 2.0 Register Description",
                f"// Module: {module_name}",
                f"// This module has an occupy placeholder register",
                f"// Generated at: {datetime.now().isoformat()}",
                "",
                f"addrmap {module_name} {{",
                f'    name = "{module_name}";',
                f'    desc = "Module with occupy placeholder register";',
                "",
                "    // Occupy register - placeholder for empty module",
                "    reg occupy {",
                '        name = "occupy";',
                '        desc = "Placeholder register for empty module";',
                "        field {",
                "            sw = rw;",
                "            hw = r;",
                '            desc = "Placeholder field";',
                "        } occupy_val[31:0];",
                "    } occupy @0x0;",
                "};",
                "",
            ]
            result['rdl'][module_name] = '\n'.join(rdl_lines)
        except Exception as e:
            self.errors.append(f"RDL generation failed for empty module {module_name}: {e}")

        # Generate minimal header/svh for occupy register
        try:
            header_lines = [
                f"/* C Header for {module_name} */",
                f"/* Generated at: {datetime.now().isoformat()} */",
                "",
                f"#ifndef _{module_name.upper()}_H_",
                f"#define _{module_name.upper()}_H_",
                "",
                f"/* Module: {module_name} */",
                "/* This module has an occupy placeholder register */",
                "",
                "/* Register: occupy */",
                f"#define {module_name.upper()}_OCCUPY_OFFSET    0x0000",
                f"#define {module_name.upper()}_OCCUPY_VAL_POS     0",
                f"#define {module_name.upper()}_OCCUPY_VAL_MASK    0xFFFFFFFF",
                f"#define {module_name.upper()}_OCCUPY_VAL_WIDTH   32",
                "",
                f"#endif /* _{module_name.upper()}_H_ */",
                "",
            ]
            result['header'][module_name] = '\n'.join(header_lines)
        except Exception as e:
            self.errors.append(f"Header generation failed for empty module {module_name}: {e}")

        # Generate SVH
        try:
            svh_lines = [
                f"// SystemVerilog Header for {module_name}",
                f"// Generated at: {datetime.now().isoformat()}",
                "",
                f"`ifndef _{module_name.upper()}_SVH_",
                f"`define _{module_name.upper()}_SVH_",
                "",
                f"// Module: {module_name}",
                "// This module has an occupy placeholder register",
                "",
                "// Register: occupy",
                f"`define {module_name.upper()}_OCCUPY_OFFSET    16'h0000",
                f"`define {module_name.upper()}_OCCUPY_VAL_POS     0",
                f"`define {module_name.upper()}_OCCUPY_VAL_MASK    32'hFFFFFFFF",
                f"`define {module_name.upper()}_OCCUPY_VAL_WIDTH   32",
                "",
                f"`endif // _{module_name.upper()}_SVH_",
                "",
            ]
            result['svh'][module_name] = '\n'.join(svh_lines)
        except Exception as e:
            self.errors.append(f"SVH generation failed for empty module {module_name}: {e}")

    def _escape_rdl_string(self, s: str) -> str:
        """Escape string for SystemRDL - remove or escape quotes"""
        if not s:
            return s
        # Remove double quotes to avoid breaking RDL syntax
        # Replace with single quotes or nothing
        return s.replace('"', "'")

    def _generate_rdl_register(self, reg: Register, reg_width: int = 32) -> List[str]:
        """Generate SystemRDL 2.0 register definition"""
        lines = [
            f"    reg {reg.name} {{",
            f"        name = \"{reg.name}\";",
        ]

        if reg.description:
            escaped_desc = self._escape_rdl_string(reg.description)
            lines.append(f"        desc = \"{escaped_desc}\";")

        # Generate fields
        # Track field names to handle duplicates (e.g., multiple 'reserved' fields)
        used_field_names = {}
        for field in reg.fields:
            # First validate field bit range before any calculations
            msb = field.msb
            lsb = field.lsb

            # Check for invalid bit range
            if msb < lsb:
                raise ValueError(f"Field '{field.name}' in register '{reg.name}' has invalid bit range [{msb}:{lsb}] (msb < lsb)")

            # Check field bit range against register width
            if msb >= reg_width:
                raise ValueError(f"Field '{field.name}' in register '{reg.name}' has msb={msb} exceeding register width {reg_width}")
            if lsb >= reg_width:
                raise ValueError(f"Field '{field.name}' in register '{reg.name}' has lsb={lsb} exceeding register width {reg_width}")

            access = self._map_rdl_access(field.access)
            width = msb - lsb + 1
            reset_val = self._parse_reset_value(field.reset_value)

            # Handle duplicate field names
            field_name = field.name
            if field_name in used_field_names:
                used_field_names[field_name] += 1
                field_name = f"{field_name}_{used_field_names[field_name]}"
            else:
                used_field_names[field_name] = 0

            lines.append(f"        field {{")
            if field.description:
                escaped_field_desc = self._escape_rdl_string(field.description)
                lines.append(f"            desc = \"{escaped_field_desc}\";")
            lines.append(f"            sw = {access};")
            lines.append(f"            hw = rw;")

            # Add onwrite property for write-to-X types
            onwrite = self._get_rdl_onwrite(field.access)
            if onwrite:
                lines.append(f"            onwrite = {onwrite};")

            # Add onread property for read-to-X types
            onread = self._get_rdl_onread(field.access)
            if onread:
                lines.append(f"            onread = {onread};")

            if reset_val != "0x0":
                lines.append(f"            reset = {width}'h{reset_val.replace('0x', '')};")
            lines.append(f"        }} {field_name} [{msb}:{lsb}];")

        lines.append(f"    }} {reg.name} @0x{reg.offset:08X};")
        lines.append("")
        return lines

    def _map_rdl_access(self, access: str) -> str:
        """Map access type to SystemRDL 2.0 sw property"""
        access_map = {
            'RW': 'rw', 'RO': 'r', 'WO': 'w',
            'W1': 'w1', 'WO1': 'w1',
            'W1C': 'rw', 'W1S': 'rw', 'W1T': 'rw',
            'W0C': 'rw', 'W0S': 'rw', 'W0T': 'rw',
            'RC': 'r', 'RS': 'r',
            'WRC': 'rw', 'WRS': 'rw', 'WC': 'rw', 'WS': 'rw',
            'WOS': 'w', 'WOC': 'w',
            'W1SRC': 'rw', 'W1CRS': 'rw', 'W0SRC': 'rw', 'W0CRS': 'rw',
            'WSRC': 'rw', 'WCRS': 'rw',
            'HWR': 'r', 'HWW': 'w',
        }
        return access_map.get(access.upper() if access else 'RW', 'rw')

    def _get_rdl_onwrite(self, access: str) -> Optional[str]:
        """Get onwrite property for SystemRDL

        Note: SystemRDL uses abbreviated forms:
        - woclr (not woclr)
        - woset (not woset)
        - wot (not wotoggle)
        """
        onwrite_map = {
            'W1C': 'woclr', 'W1S': 'woset', 'W1T': 'wot',
            'W0C': 'woclr', 'W0S': 'woset', 'W0T': 'wot',
            'WC': 'woclr', 'WS': 'woset',
            'WOC': 'woclr', 'WOS': 'woset',
            'W1SRC': 'woset', 'W1CRS': 'woclr',
            'W0SRC': 'woset', 'W0CRS': 'woclr',
            'WSRC': 'woset', 'WCRS': 'woclr',
        }
        return onwrite_map.get(access.upper() if access else '', None)

    def _get_rdl_onread(self, access: str) -> Optional[str]:
        """Get onread property for SystemRDL"""
        onread_map = {
            'RC': 'rclr', 'RS': 'rset',
            'WRC': 'rclr', 'WRS': 'rset',
            'W1SRC': 'rclr', 'W1CRS': 'rset',
            'W0SRC': 'rclr', 'W0CRS': 'rset',
            'WSRC': 'rclr', 'WCRS': 'rset',
        }
        return onread_map.get(access.upper() if access else '', None)

    def save_all(self, generated: Dict[str, Dict[str, str]], version_id: int, version_name: str = "", user_id: str = "default") -> Dict[str, List[str]]:
        """
        Save all generated files to disk
        Overwrites existing files (new content replaces old)
        Directory structure: output/{user_id}/{version_name}/
        """
        result = {
            'ralf': [],
            'rdl': [],
            'header': [],
            'svh': [],
            'uvm': [],
            'rtl': [],
        }

        # Use version_name as directory name (as requested by user)
        # Sanitize version_name to be filesystem-safe
        safe_version_name = self._sanitize_filename(version_name) if version_name else f"v{version_id}"
        version_dir = self.output_dir / user_id / safe_version_name

        # Save RALF files
        ralf_dir = version_dir / 'ralf'
        for module_name, content in generated.get('ralf', {}).items():
            file_path = ralf_dir / f"{module_name}.ralf"
            if self._save_file(content, file_path):
                result['ralf'].append(str(file_path))

        # Save RDL files
        rdl_dir = version_dir / 'rdl'
        for module_name, content in generated.get('rdl', {}).items():
            file_path = rdl_dir / f"{module_name}.rdl"
            if self._save_file(content, file_path):
                result['rdl'].append(str(file_path))

        # Save C Header files
        header_dir = version_dir / 'header'
        for module_name, content in generated.get('header', {}).items():
            file_path = header_dir / f"{module_name}.h"
            if self._save_file(content, file_path):
                result['header'].append(str(file_path))

        # Save SVH files
        svh_dir = version_dir / 'svh'
        for module_name, content in generated.get('svh', {}).items():
            file_path = svh_dir / f"{module_name}.svh"
            if self._save_file(content, file_path):
                result['svh'].append(str(file_path))

        # Save UVM files
        uvm_dir = version_dir / 'uvm'
        for module_name, content in generated.get('uvm', {}).items():
            file_path = uvm_dir / f"{module_name}_regmodel.sv"
            if self._save_file(content, file_path):
                result['uvm'].append(str(file_path))

        # Save RTL files
        rtl_dir = version_dir / 'rtl'
        for module_name, content in generated.get('rtl', {}).items():
            file_path = rtl_dir / f"{module_name}_reg.sv"
            if self._save_file(content, file_path):
                result['rtl'].append(str(file_path))

        return result

    def _save_file(self, content: str, file_path: Path) -> bool:
        """Save content to file (overwrites if exists)"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            self.errors.append(f"Error saving {file_path}: {e}")
            return False

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize name to be filesystem-safe"""
        import re
        # Replace invalid characters with underscore
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    def _map_ralf_access(self, access: str) -> str:
        """Map access type to RALF format (UVM RAL 1.2)"""
        access_map = {
            # 基础
            'RW': 'rw', 'RO': 'ro', 'WO': 'wo',
            # Write-Once
            'W1': 'w1', 'WO1': 'w1',
            # Write-1-to-X
            'W1C': 'w1c', 'W1S': 'w1s', 'W1T': 'w1t',
            # Write-0-to-X
            'W0C': 'w0c', 'W0S': 'w0s', 'W0T': 'w0t',
            # Read-to-X
            'RC': 'rc', 'RS': 'rs',
            # Write-Read Combined
            'WRC': 'wrc', 'WRS': 'wrs', 'WC': 'wc', 'WS': 'ws',
            'WOC': 'woc', 'WOS': 'wos',
            # Complex Combined
            'W1SRC': 'w1src', 'W1CRS': 'w1crs',
            'W0SRC': 'w0src', 'W0CRS': 'w0crs',
            'WSRC': 'wsrc', 'WCRS': 'wcrs',
            # 硬件
            'HWR': 'ro', 'HWW': 'wo',
        }
        return access_map.get(access.upper() if access else 'RW', 'rw')

    def _parse_reset_value(self, reset_val: str) -> str:
        """Parse reset value"""
        if not reset_val:
            return "0x0"
        reset_str = str(reset_val).strip()
        try:
            if reset_str.startswith('0x'):
                return reset_str
            elif "'h" in reset_str:
                return "0x" + reset_str.split("'h")[1]
            else:
                val = int(reset_str)
                return f"0x{val:X}"
        except:
            return "0x0"

    def get_warnings(self) -> List[str]:
        return self.warnings

    def get_errors(self) -> List[str]:
        return self.errors

    def cleanup_generated_files(self, version_id: int, version_name: str = "", user_id: str = "default"):
        """Delete generated files for a version when generation fails

        Only removes files generated by ModuleCodeGenerator (RALF, RDL, Header, SVH, RTL)
        to avoid affecting files generated by other services (e.g., HTML from PeakRDL)
        """
        import shutil
        safe_version_name = self._sanitize_filename(version_name) if version_name else f"v{version_id}"
        version_dir = self.output_dir / user_id / safe_version_name

        if version_dir.exists():
            # Only remove specific format directories we generate
            # Do NOT remove 'html' directory (generated by PeakRDLHTMLService)
            format_dirs = ['ralf', 'rdl', 'header', 'svh', 'rtl']
            for fmt in format_dirs:
                fmt_dir = version_dir / fmt
                if fmt_dir.exists():
                    try:
                        shutil.rmtree(fmt_dir)
                    except Exception as e:
                        self.errors.append(f"Failed to cleanup directory {fmt_dir}: {e}")
