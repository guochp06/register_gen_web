"""
PeakRDL-compatible RDL Generator - Generates nested RDL without includes
This allows PeakRDL to compile the RDL directly without external file dependencies

Supports:
- Modules from Excel
- Modules from RALF (overrides Excel with warning)
- Array instances
"""
from typing import List, Dict, Set
from datetime import datetime
from pathlib import Path

from app.services.hierarchy_parser import RegisterHierarchy, Module, Register, RegisterField


class PeakRDLCompatibleRDLGenerator:
    """Generate SystemRDL 2.0 format that PeakRDL can compile directly

    Unlike the standard RDLGenerator, this generates nested addrmap structures
    without using `include` statements, making it directly compilable by PeakRDL.

    Supports mixed sources:
    - Modules from Excel (default)
    - Modules from RALF (override Excel modules with same name)
    """

    def __init__(self):
        self.warnings: List[str] = []
        self.ralf_modules: Dict[str, str] = {}  # module_name -> RDL content from RALF

    def set_ralf_modules(self, ralf_modules: Dict[str, str]):
        """
        Set modules that should be sourced from RALF.

        Args:
            ralf_modules: Dict mapping module_name to RDL content from RALF
        """
        self.ralf_modules = ralf_modules

    def _sanitize_name(self, name: str) -> str:
        """Sanitize name for SystemRDL compatibility
        SystemRDL identifiers must be valid C-like identifiers
        """
        # Replace invalid characters with underscore
        import re
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        # Ensure doesn't start with a digit
        if sanitized and sanitized[0].isdigit():
            sanitized = '_' + sanitized
        return sanitized

    def generate(self, hierarchy: RegisterHierarchy) -> str:
        """Generate nested RDL from hierarchy for PeakRDL compilation

        Strategy:
        1. First pass: Define all module types at global scope (to avoid scope issues)
           - For modules in ralf_modules, use the RALF-provided RDL
           - For other modules, generate from Excel data
        2. Second pass: Create top-level addrmap with instances
        """
        self.warnings = []
        # Use top_addrmap_name from Excel (e.g., soc_addr_map) if available, otherwise fall back to version_name
        top_name = hierarchy.top_addrmap_name if hierarchy.top_addrmap_name else hierarchy.version_name
        # Sanitize the top-level name
        safe_version_name = self._sanitize_name(top_name)

        lines = [
            "// SystemRDL 2.0 Register Description - PeakRDL Compatible",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
        ]

        # Track which modules have been defined to avoid duplicates
        defined_modules: Set[str] = set()

        # Collect all unique module definitions (bottom-up, to define leaf modules first)
        all_modules_ordered: List[Module] = []
        self._collect_modules_bottom_up(hierarchy, all_modules_ordered, defined_modules)

        # First: Define all module types at global scope
        lines.append("// ============================================")
        lines.append("// Module Type Definitions (Global Scope)")
        lines.append("// ============================================")
        lines.append("")

        for module in all_modules_ordered:
            # Check if this module should come from RALF
            base_name = getattr(module, 'base_module_name', module.name) if getattr(module, 'is_array_instance', False) else module.name

            if base_name in self.ralf_modules:
                # Use RALF-provided RDL
                lines.append(f"// Module {base_name} sourced from RALF")
                lines.append(self.ralf_modules[base_name])
                lines.append("")
            else:
                # Generate from Excel data
                lines.extend(self._generate_module_definition(module, indent=0))
                lines.append("")

        # Check if we need top-level integration
        # Always create a wrapper to ensure the top module is instantiated as root
        # This is necessary because SystemRDL needs an instance, not just a type definition

        # Create wrapper with a unique name to avoid conflicts with module type definition
        wrapper_name = f"{safe_version_name}_top"
        if wrapper_name == safe_version_name:
            wrapper_name = f"{safe_version_name}_root"

        # Second: Create top-level addrmap with instances
        lines.append("// ============================================")
        lines.append("// Top-Level Integration")
        lines.append("// ============================================")
        lines.append("")
        lines.append(f"addrmap {wrapper_name} {{")
        lines.append(f'    name = "{top_name} Top Level";')
        lines.append(f"    default regwidth = 32;")
        lines.append(f"    addressing = regalign;")
        lines.append(f"    lsb0;")
        lines.append("")

        # Find the top addrmap module (soc_addr_map) - this is the ONLY module to instantiate at top level
        # Base modules like CPD, C2C, L2B, etc. are defined as types but instantiated inside soc_addr_map
        top_addrmap_module = None
        for module in hierarchy.top_modules:
            # The top addrmap has submodules but is not a leaf module
            if module.submodules and len(module.registers) == 0:
                top_addrmap_module = module
                break

        # If no addrmap found (no submodules), use the first non-empty module
        if not top_addrmap_module:
            for module in hierarchy.top_modules:
                is_array_instance = getattr(module, 'is_array_instance', False)
                if module.registers or module.submodules or is_array_instance:
                    top_addrmap_module = module
                    break

        if top_addrmap_module:
            # Instantiate only the top addrmap - it contains all other modules as sub-instances
            type_name = top_addrmap_module.name
            inst_name = top_addrmap_module.name
            lines.append(f"    {type_name} {inst_name}_inst @0x0;")
        else:
            self.warnings.append("No top addrmap module found - output may be incomplete")

        lines.append("};")

        return '\n'.join(lines)
        return '\n'.join(lines)

    def get_warnings(self) -> List[str]:
        """Get warnings generated during RDL generation"""
        return self.warnings

    def _collect_modules_bottom_up(self, hierarchy: RegisterHierarchy,
                                    result: List[Module], defined: Set[str]):
        """Collect all modules in bottom-up order (leaves first)

        This ensures that child modules are defined before parent modules,
        which is required by SystemRDL's single-pass compilation.
        """
        def get_module_for_definition(module: Module) -> Module:
            """Get the module to use for definition (base module for array instances)"""
            is_array_instance = getattr(module, 'is_array_instance', False)
            if is_array_instance:
                base_name = getattr(module, 'base_module_name', module.name)
                # Look up the base module in hierarchy
                if base_name in hierarchy.all_modules:
                    return hierarchy.all_modules[base_name]
            return module

        def collect_leaf_first(module: Module):
            # Get the module to use for definition (handles array instances)
            def_module = get_module_for_definition(module)
            base_name = def_module.name

            if base_name in defined:
                return

            # First, recursively collect all submodules (they need to be defined first)
            # For array instances, also collect submodules from the base module
            submodules_to_process = list(module.submodules)
            if getattr(module, 'is_array_instance', False):
                # Also process submodules from the base module
                base_module = get_module_for_definition(module)
                submodules_to_process.extend(base_module.submodules)

            for sub in submodules_to_process:
                collect_leaf_first(sub)

            # Then add this module (using the definition module, not the instance)
            # Include empty modules - they are valid address placeholders
            if base_name not in defined:
                defined.add(base_name)
                result.append(def_module)

        # Start from top modules and work down
        for module in hierarchy.top_modules:
            collect_leaf_first(module)

        # Also collect any modules that weren't reached from top modules
        # Sort by number of submodules (leaves first) to ensure correct order
        remaining = sorted(
            [m for m in hierarchy.all_modules.values() if m.name not in defined],
            key=lambda m: len(m.submodules)
        )
        for module in remaining:
            collect_leaf_first(module)

    def _generate_module_definition(self, module: Module, indent: int = 0) -> List[str]:
        """Generate a module definition (not instance) at global scope"""
        prefix = "    " * indent
        lines = []

        # For array instances, use base name
        is_array_instance = getattr(module, 'is_array_instance', False)
        mod_name = getattr(module, 'base_module_name', module.name) if is_array_instance else module.name

        # Determine register width from fields (default 32, but can be 64)
        max_bit = 31
        for reg in module.registers:
            for field in reg.fields:
                if field.msb > max_bit:
                    max_bit = field.msb
        regwidth = 64 if max_bit >= 32 else 32

        lines.append(f"{prefix}addrmap {mod_name} {{")
        lines.append(f'{prefix}    name = "{mod_name}";')
        lines.append(f"{prefix}    default regwidth = {regwidth};")
        lines.append(f"{prefix}    addressing = regalign;")
        lines.append(f"{prefix}    lsb0;")

        if module.description:
            escaped_desc = module.description.replace('"', '\\"')
            lines.append(f'{prefix}    desc = "{escaped_desc}";')

        lines.append("")

        # For empty modules (address placeholders), add a placeholder register
        if self._is_empty_module(module):
            lines.append(f"{prefix}    // Address placeholder - no registers defined")
            lines.append(f"{prefix}    reg {{")
            lines.append(f"{prefix}        field {{")
            lines.append(f"{prefix}            sw = rw;")
            lines.append(f"{prefix}            hw = r;")
            lines.append(f"{prefix}            reset = 0;")
            lines.append(f"{prefix}            desc = \"Reserved - address placeholder\";")
            lines.append(f"{prefix}        }} reserved [{regwidth-1}:0];")
            lines.append(f"{prefix}    }} reserved_reg;")
            lines.append("")

        # Generate registers (if any)
        if module.registers:
            lines.append(f"{prefix}    // Registers")
            # Track register names to avoid duplicates
            used_reg_names = {}
            for reg in module.registers:
                reg_name = reg.name
                if reg_name in used_reg_names:
                    used_reg_names[reg_name] += 1
                    reg_name = f"{reg_name}_{used_reg_names[reg_name]}"
                else:
                    used_reg_names[reg_name] = 0
                lines.extend(self._generate_register(reg, indent + 1, regwidth, reg_name))
            lines.append("")

        # Instantiate sub-modules (using relative offsets from module start)
        if module.submodules:
            lines.append(f"{prefix}    // Sub-module instances")
            # Track instance names to avoid duplicates
            used_instance_names = set()
            for sub in module.submodules:
                sub_is_array = getattr(sub, 'is_array_instance', False)
                sub_base_name = getattr(sub, 'base_module_name', sub.name) if sub_is_array else sub.name
                # Calculate relative offset from parent module
                rel_offset = sub.start_addr - module.start_addr
                # Handle negative offset: use sub's address as relative offset if it appears to be absolute
                if rel_offset < 0:
                    rel_offset = sub.start_addr

                # Generate unique instance name
                # For array instances, use their name directly (PE0, PE1, etc.)
                # For regular modules, use name + _inst
                if sub_is_array:
                    inst_name = sub.name
                else:
                    inst_name = f"{sub.name}_inst"
                    # Ensure uniqueness by adding suffix if needed
                    base_inst_name = inst_name
                    counter = 1
                    while inst_name in used_instance_names:
                        inst_name = f"{base_inst_name}_{counter}"
                        counter += 1

                used_instance_names.add(inst_name)
                lines.append(f"{prefix}    {sub_base_name} {inst_name} @0x{rel_offset:X};")
            lines.append("")

        lines.append(f"{prefix}}};")

        return lines

    def _is_empty_module(self, module: Module) -> bool:
        """Check if a module is empty (no registers and no submodules)"""
        return not module.registers and not module.submodules

    def _generate_register(self, reg: Register, indent: int, default_regwidth: int = 32, reg_name: str = None) -> List[str]:
        """Generate register definition"""
        prefix = "    " * indent

        # Determine register width from fields
        max_bit = 31
        for field in reg.fields:
            if field.msb > max_bit:
                max_bit = field.msb
        regwidth = 64 if max_bit >= 32 else 32

        # Use provided name or default to reg.name
        name = reg_name if reg_name is not None else reg.name

        lines = [
            f"{prefix}reg {name} {{",
        ]

        if reg.description:
            escaped_desc = reg.description.replace('"', '\\"')
            lines.append(f'{prefix}    desc = "{escaped_desc}";')

        # Add regwidth property if different from default
        if regwidth != default_regwidth:
            lines.append(f"{prefix}    regwidth = {regwidth};")

        # Track field names to ensure uniqueness within this register
        field_name_counts = {}
        for field in reg.fields:
            field_name = field.name
            if field_name in field_name_counts:
                field_name_counts[field_name] += 1
                field_name = f"{field_name}_{field_name_counts[field_name]}"
            else:
                field_name_counts[field_name] = 0
            lines.extend(self._generate_field(field, indent + 1, field_name))

        # Use the offset from Excel - this ensures correct register addresses in generated HTML
        lines.append(f"{prefix}}} {name} @ 0x{reg.offset:X};")
        return lines

    def _generate_field(self, field: RegisterField, indent: int, field_name: str = None) -> List[str]:
        """Generate field definition"""
        prefix = "    " * indent
        access = self._map_access(field.access)

        # Use provided field_name or default to field.name
        name = field_name if field_name is not None else field.name

        lines = [f"{prefix}field {{"]
        lines.append(f"{prefix}    sw = {access['sw']};")
        lines.append(f"{prefix}    hw = {access['hw']};")

        if access.get('onread'):
            lines.append(f"{prefix}    onread = {access['onread']};")
        if access.get('onwrite'):
            lines.append(f"{prefix}    onwrite = {access['onwrite']};")

        reset_val = self._parse_reset_value(field.reset_value)
        lines.append(f"{prefix}    reset = {reset_val};")

        if field.description:
            escaped_desc = field.description.replace('"', '\\"')
            lines.append(f'{prefix}    desc = "{escaped_desc}";')

        lines.append(f"{prefix}}} {name} [{field.msb}:{field.lsb}];")
        return lines

    def _map_access(self, access: str) -> Dict[str, str]:
        """Map access type to SystemRDL properties"""
        access_map = {
            'RO': {'sw': 'r', 'hw': 'rw'},
            'RW': {'sw': 'rw', 'hw': 'rw'},
            'WO': {'sw': 'w', 'hw': 'rw'},
            'NA': {'sw': 'na', 'hw': 'na'},
            'RC': {'sw': 'r', 'hw': 'rw', 'onread': 'rclr'},
            'RS': {'sw': 'r', 'hw': 'rw', 'onread': 'rset'},
            'WRC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wclr', 'onread': 'rclr'},
            'WRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wset', 'onread': 'rset'},
            'WC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wclr'},
            'WS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wset'},
            'W1C': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr'},
            'W1S': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset'},
            'W1T': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wot'},
            'W0C': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wzc'},
            'W0S': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wzs'},
            'WO1': {'sw': 'w', 'hw': 'rw'},
            'HWR': {'sw': 'r', 'hw': 'r'},
            'HWW': {'sw': 'na', 'hw': 'w'},
        }
        return access_map.get(access.upper(), {'sw': 'rw', 'hw': 'rw'})

    def _parse_reset_value(self, reset_val: str) -> str:
        """Parse and format reset value for SystemRDL compatibility"""
        if not reset_val:
            return "0"
        reset_str = str(reset_val).strip()

        # Handle Verilog-style literals (e.g., 3'o2, 8'hFF, 4'b1010)
        if "'" in reset_str:
            try:
                # Parse Verilog literal: <width>'<base><value>
                # Base: b/B=binary, o/O=octal, d/D=decimal, h/H=hex
                import re
                match = re.match(r"(\d+)'([bBoOdDhH])([0-9a-fA-F_xXzZ?]+)", reset_str)
                if match:
                    width = int(match.group(1))
                    base = match.group(2).lower()
                    value_str = match.group(3).replace('_', '')

                    if base == 'b':
                        val = int(value_str, 2)
                    elif base == 'o':
                        val = int(value_str, 8)
                    elif base == 'd':
                        val = int(value_str, 10)
                    elif base == 'h':
                        val = int(value_str, 16)
                    else:
                        return "0"

                    # Return in SystemRDL-compatible format
                    return f"{width}'h{val:X}"
                else:
                    # If can't parse, return as-is and hope it works
                    return reset_str
            except:
                return "0"

        # Handle plain hex values
        try:
            if reset_str.startswith('0x'):
                val = int(reset_str, 16)
            else:
                val = int(reset_str)
            return f"32'h{val:X}"
        except:
            return "0"

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save RDL content to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving RDL: {e}")
            return False

    def generate_single_module(self, module: Module) -> str:
        """Generate RDL for a single module (leaf module with registers only)

        This generates a standalone addrmap without external references,
        suitable for RTL generation by peakrdl-regblock.

        Args:
            module: The module to generate RDL for

        Returns:
            SystemRDL 2.0 content as string
        """
        lines = [
            "// SystemRDL 2.0 Register Description - Single Module",
            f"// Module: {module.name}",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
        ]

        # Determine register width from fields
        max_bit = 31
        for reg in module.registers:
            for field in reg.fields:
                if field.msb > max_bit:
                    max_bit = field.msb
        regwidth = 64 if max_bit >= 32 else 32

        # Generate single addrmap without external references
        lines.append(f"addrmap {module.name} {{")
        lines.append(f'    name = "{module.name}";')
        lines.append(f"    default regwidth = {regwidth};")
        lines.append(f"    addressing = regalign;")
        lines.append(f"    lsb0;")

        if module.description:
            escaped_desc = module.description.replace('"', '\\"')
            lines.append(f'    desc = "{escaped_desc}";')

        lines.append("")

        # Generate registers only (no submodules for RTL generation)
        if module.registers:
            lines.append("    // Registers")
            used_reg_names = {}
            for reg in module.registers:
                reg_name = reg.name
                if reg_name in used_reg_names:
                    used_reg_names[reg_name] += 1
                    reg_name = f"{reg_name}_{used_reg_names[reg_name]}"
                else:
                    used_reg_names[reg_name] = 0
                lines.extend(self._generate_register(reg, 1, regwidth, reg_name))
            lines.append("")
        else:
            # Add placeholder register for empty modules
            lines.append("    // Address placeholder - no registers defined")
            lines.append("    reg {")
            lines.append("        field {")
            lines.append("            sw = rw;")
            lines.append("            hw = r;")
            lines.append("            reset = 0;")
            lines.append('            desc = "Reserved - address placeholder";')
            lines.append(f"        }} reserved [{regwidth-1}:0];")
            lines.append("    } reserved_reg;")
            lines.append("")

        lines.append("};")

        return '\n'.join(lines)
