"""
RDL Exporter - Generate RALF, C Header, and SVH from compiled RDL

Uses systemrdl-compiler to parse RDL and generate output files.
This ensures consistency between RDL and all derived formats.
"""
from typing import List, Dict, Optional
from pathlib import Path
from datetime import datetime
import re

from systemrdl import RDLCompiler
from systemrdl.node import AddrmapNode, RegNode, FieldNode


class RDLExporter:
    """Export RDL to various formats by compiling and traversing the RDL tree"""

    def __init__(self):
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def export_from_rdl_file(self, rdl_file: Path) -> Dict[str, Optional[str]]:
        """
        Export RDL file to RALF, C Header, and SVH formats

        Returns:
            {
                'ralf': ralf_content or None,
                'header': c_header_content or None,
                'svh': svh_content or None,
            }
        """
        self.warnings = []
        self.errors = []

        results = {
            'ralf': None,
            'header': None,
            'svh': None,
        }

        try:
            # Compile RDL
            rdlc = RDLCompiler()
            rdlc.compile_file(str(rdl_file))
            root = rdlc.elaborate()

            # Find the top addrmap
            top_node = None
            for child in root.children():
                if isinstance(child, AddrmapNode):
                    top_node = child
                    break

            if not top_node:
                self.errors.append(f"No addrmap found in {rdl_file}")
                return results

            # Generate outputs
            results['ralf'] = self._generate_ralf(top_node)
            results['header'] = self._generate_c_header(top_node)
            results['svh'] = self._generate_svh(top_node)

        except Exception as e:
            self.errors.append(f"RDL export failed: {str(e)}")
            import traceback
            traceback.print_exc()

        return results

    def export_from_rdl_content(self, rdl_content: str, module_name: str) -> Dict[str, Optional[str]]:
        """
        Export RDL content to RALF, C Header, and SVH formats

        Args:
            rdl_content: RDL content string
            module_name: Module name for naming

        Returns:
            {
                'ralf': ralf_content or None,
                'header': c_header_content or None,
                'svh': svh_content or None,
            }
        """
        import tempfile
        import os

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.rdl', delete=False) as f:
            f.write(rdl_content)
            temp_path = Path(f.name)

        try:
            results = self.export_from_rdl_file(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

        return results

    def _generate_ralf(self, node: AddrmapNode) -> str:
        """Generate UVM RALF from compiled RDL addrmap"""
        lines = [
            "// UVM RALF Register Description",
            f"// Module: {node.inst_name}",
            f"// Generated from RDL at: {datetime.now().isoformat()}",
            "",
        ]

        # Get properties
        base_addr = 0  # Register modules start from 0
        size = self._get_addrmap_size(node)

        lines.append(f"block {node.inst_name} @0x{base_addr:X} {{")
        lines.append(f"    bytes {size};")
        lines.append("")

        # Generate registers
        for child in node.children():
            if isinstance(child, RegNode):
                lines.extend(self._generate_ralf_register(child))

        lines.append("}")
        return '\n'.join(lines)

    def _generate_ralf_register(self, reg: RegNode) -> List[str]:
        """Generate RALF register from compiled RDL register"""
        # Get register address offset
        addr_offset = reg.address_offset

        lines = [
            f"    register {reg.inst_name} @0x{addr_offset:X} {{",
            f"        bytes {reg.size};",
        ]

        # Generate fields
        for field in reg.children():
            if isinstance(field, FieldNode):
                lines.extend(self._generate_ralf_field(field))

        lines.append(f"    }}")
        return lines

    def _generate_ralf_field(self, field: FieldNode) -> List[str]:
        """Generate RALF field from compiled RDL field"""
        # Get field properties
        msb = field.msb
        lsb = field.lsb
        width = msb - lsb + 1

        # Get access type
        sw = field.get_property('sw')
        access = self._map_rdl_to_ralf_access(sw)

        # Get reset value
        reset_val = field.get_property('reset')
        if reset_val is None:
            reset_val = 0

        # Handle duplicate field names by checking siblings
        field_name = field.inst_name

        lines = [
            f"        field {field_name} @{lsb} {{",
            f"            bits {width};",
            f"            access {access};",
            f"            reset 0x{reset_val:X};",
            f"        }}",
        ]
        return lines

    def _generate_c_header(self, node: AddrmapNode) -> str:
        """Generate C Header from compiled RDL addrmap"""
        module_name = node.inst_name
        guard_name = f"_{module_name.upper()}_H_"

        lines = [
            f"/* C Header for {module_name} */",
            f"/* Generated from RDL at: {datetime.now().isoformat()} */",
            "",
            f"#ifndef {guard_name}",
            f"#define {guard_name}",
            "",
            '#include "reg_common.h"',
            "",
            f"/* Module: {module_name} */",
            f"/* Base Address: 0x00000000 (relative) */",
            "",
        ]

        # Generate register definitions
        for child in node.children():
            if isinstance(child, RegNode):
                lines.extend(self._generate_c_header_register(child, module_name))

        lines.extend([
            "",
            f"#endif /* {guard_name} */",
            "",
        ])

        return '\n'.join(lines)

    def _generate_c_header_register(self, reg: RegNode, module_name: str) -> List[str]:
        """Generate C Header register definitions"""
        reg_name = f"{module_name.upper()}_{reg.inst_name.upper()}"
        addr_offset = reg.address_offset

        lines = [
            f"/* Register: {reg.inst_name} */",
            f"#define {reg_name}_OFFSET    0x{addr_offset:04X}",
        ]

        # Generate field definitions
        for field in reg.children():
            if isinstance(field, FieldNode):
                lines.extend(self._generate_c_header_field(field, reg_name))

        lines.append("")
        return lines

    def _generate_c_header_field(self, field: FieldNode, reg_name: str) -> List[str]:
        """Generate C Header field definitions"""
        field_name = f"{reg_name}_{field.inst_name.upper()}"
        lsb = field.lsb
        msb = field.msb
        width = msb - lsb + 1
        mask = (1 << width) - 1

        return [
            f"#define {field_name}_POS     {lsb}",
            f"#define {field_name}_MASK    0x{mask:08X}",
            f"#define {field_name}_WIDTH   {width}",
        ]

    def _generate_svh(self, node: AddrmapNode) -> str:
        """Generate SystemVerilog Header from compiled RDL addrmap"""
        module_name = node.inst_name
        guard_name = f"_{module_name.upper()}_SVH_"

        lines = [
            f"// SystemVerilog Header for {module_name}",
            f"// Generated from RDL at: {datetime.now().isoformat()}",
            "",
            f"`ifndef {guard_name}",
            f"`define {guard_name}",
            "",
            "`include \"reg_common.svh\"",
            "",
            f"// Module: {module_name}",
            f"// Base Address: 0x00000000 (relative)",
            "",
        ]

        # Generate register definitions
        for child in node.children():
            if isinstance(child, RegNode):
                lines.extend(self._generate_svh_register(child, module_name))

        lines.extend([
            "",
            f"`endif // {guard_name}",
            "",
        ])

        return '\n'.join(lines)

    def _generate_svh_register(self, reg: RegNode, module_name: str) -> List[str]:
        """Generate SVH register definitions"""
        reg_name = f"{module_name.upper()}_{reg.inst_name.upper()}"
        addr_offset = reg.address_offset

        lines = [
            f"// Register: {reg.inst_name}",
            f"`define {reg_name}_OFFSET    16'h{addr_offset:04X}",
        ]

        # Generate field definitions
        for field in reg.children():
            if isinstance(field, FieldNode):
                lines.extend(self._generate_svh_field(field, reg_name))

        lines.append("")
        return lines

    def _generate_svh_field(self, field: FieldNode, reg_name: str) -> List[str]:
        """Generate SVH field definitions"""
        field_name = f"{reg_name}_{field.inst_name.upper()}"
        lsb = field.lsb
        msb = field.msb
        width = msb - lsb + 1
        mask = (1 << width) - 1

        return [
            f"`define {field_name}_POS     {lsb}",
            f"`define {field_name}_MASK    {width}'h{mask:X}",
            f"`define {field_name}_WIDTH   {width}",
        ]

    def _get_addrmap_size(self, node: AddrmapNode) -> int:
        """Calculate total size of addrmap based on register addresses"""
        max_addr = 0
        for child in node.children():
            if isinstance(child, RegNode):
                reg_end = child.address_offset + child.size
                if reg_end > max_addr:
                    max_addr = reg_end
        return max_addr if max_addr > 0 else 1024

    def _map_rdl_to_ralf_access(self, sw_access) -> str:
        """Map RDL sw access to RALF access string

        systemrdl returns AccessType enum, e.g., AccessType.r, AccessType.rw
        """
        access_map = {
            'rw': 'rw',
            'r': 'ro',
            'w': 'wo',
            'w1': 'w1',
        }
        if sw_access is None:
            return 'rw'

        # Handle AccessType enum (e.g., "AccessType.r" -> "r")
        sw_str = str(sw_access).lower()
        if 'accesstype.' in sw_str:
            sw_str = sw_str.split('.')[-1]

        return access_map.get(sw_str, 'rw')

    def get_warnings(self) -> List[str]:
        return self.warnings

    def get_errors(self) -> List[str]:
        return self.errors
