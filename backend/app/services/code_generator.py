"""
Code generators for various output formats
Including: RDL, RALF, C Header, SV Header
"""
from typing import List, Dict, Any
from datetime import datetime
from pathlib import Path
import os


class RDLGenerator:
    """Generate SystemRDL 2.0 format"""

    def generate(self, hierarchy) -> str:
        """Generate RDL from hierarchy"""
        lines = [
            "// SystemRDL 2.0 Register Description",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
        ]

        # Generate addr_map for each top module
        # Skip array instances - they are instantiated within their parent or listed as external
        for module in hierarchy.top_modules:
            if getattr(module, 'is_array_instance', False):
                # For top-level array instances, generate an external reference
                lines.extend(self._generate_external_instance(module))
            else:
                lines.extend(self._generate_addrmap(module))
            lines.append("")

        return '\n'.join(lines)

    def _generate_external_instance(self, module) -> List[str]:
        """Generate external instance declaration for array instances at top level"""
        base_name = getattr(module, 'base_module_name', module.name)
        lines = [
            f"// External instance: {module.name} (instance of {base_name})",
            f"`include \"{base_name}.rdl\"",
            f"",
            f"{base_name} {module.name}_reg @0x{module.start_addr:X};",
        ]
        return lines

    def _generate_addrmap(self, module, indent: int = 0) -> List[str]:
        """Generate addrmap for a module"""
        prefix = "    " * indent
        lines = [
            f"{prefix}addrmap {module.name} {{",
            f"{prefix}    name = \"{module.name}\";",
            f"{prefix}    default regwidth = 32;",
            f"{prefix}    addressing = compact;",
            f"{prefix}    lsb0;",
            "",
        ]

        # Collect unique modules to include (for array instances, include base module)
        included_modules = set()
        for sub in module.submodules:
            if getattr(sub, 'is_array_instance', False):
                base_name = getattr(sub, 'base_module_name', sub.name)
                included_modules.add(base_name)
            else:
                included_modules.add(sub.name)

        # Include sub-modules
        for mod_name in sorted(included_modules):
            lines.append(f"{prefix}    `include \"{mod_name}.rdl\"")

        if module.submodules:
            lines.append("")

        # Generate registers
        for reg in module.registers:
            lines.extend(self._generate_register(reg, module.start_addr, indent + 1))

        # Instantiate sub-modules
        for sub in module.submodules:
            if getattr(sub, 'is_array_instance', False):
                # For array instances, use base module type but instance name
                base_name = getattr(sub, 'base_module_name', sub.name)
                lines.append(f"{prefix}    {base_name} {sub.name}_reg @0x{sub.start_addr:X};")
            else:
                lines.append(f"{prefix}    {sub.name} {sub.name}_reg @0x{sub.start_addr:X};")

        lines.append(f"{prefix}}};")
        return lines

    def _generate_register(self, reg, base_addr: int, indent: int) -> List[str]:
        """Generate register definition"""
        prefix = "    " * indent
        full_addr = base_addr + reg.offset

        lines = [
            f"{prefix}reg {reg.name} {{",
        ]

        for field in reg.fields:
            lines.extend(self._generate_field(field, indent + 1))

        lines.append(f"{prefix}}} {reg.name} @ 0x{reg.offset:X};")
        lines.append("")
        return lines

    def _generate_field(self, field, indent: int) -> List[str]:
        """Generate field definition"""
        prefix = "    " * indent
        access = self._map_access(field.access)

        lines = [
            f"{prefix}field {{",
            f"{prefix}    sw = {access['sw']};",
            f"{prefix}    hw = {access['hw']};",
        ]

        if access.get('onread'):
            lines.append(f"{prefix}    onread = {access['onread']};")
        if access.get('onwrite'):
            lines.append(f"{prefix}    onwrite = {access['onwrite']};")

        reset_val = self._parse_reset_value(field.reset_value)
        lines.append(f"{prefix}    reset = {reset_val};")

        if field.description:
            escaped_desc = field.description.replace('"', '\\"')
            lines.append(f"{prefix}    desc = \"{escaped_desc}\";")

        lines.append(f"{prefix}}} {field.name} [{field.msb}:{field.lsb}];")
        return lines

    def _map_access(self, access: str) -> Dict[str, str]:
        """Map access type to SystemRDL properties

        Supports all SystemRDL 2.0 access types:
        - Basic: RO, RW, WO
        - Read side-effects: RC, RS, RAC, RAS
        - Write side-effects: WRC, WRS, WS, WC
        - Write-one-to-X: W1C, W1S, W1T, W1SRC, W1CRS
        - Write-zero-to-X: W0C, W0S, W0T, W0SRC, W0CRS
        - Write-clear-all: WCRS
        - Others: WO1, NA (no access)
        """
        access_map = {
            # Basic access types
            'RO': {'sw': 'r', 'hw': 'rw'},
            'RW': {'sw': 'rw', 'hw': 'rw'},
            'WO': {'sw': 'w', 'hw': 'rw'},
            'NA': {'sw': 'na', 'hw': 'na'},

            # Read-clear/set (sticky bits)
            'RC': {'sw': 'r', 'hw': 'rw', 'onread': 'rclr'},
            'RS': {'sw': 'r', 'hw': 'rw', 'onread': 'rset'},
            'RAC': {'sw': 'r', 'hw': 'rw', 'onread': 'raclr'},  # Read and clear all
            'RAS': {'sw': 'r', 'hw': 'rw', 'onread': 'raset'},  # Read and set all

            # Write with read side-effects
            'WRC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wclr', 'onread': 'rclr'},
            'WRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wset', 'onread': 'rset'},
            'WCRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wclrs'},  # Write clears all

            # Write-clear/set/toggle (write affects entire field)
            'WC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wclr'},
            'WS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wset'},
            'WT': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wtoggle'},

            # Write-one-to-clear/set/toggle
            'W1C': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr'},
            'W1S': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset'},
            'W1T': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wot'},
            'W1SRC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset', 'onread': 'rclr'},
            'W1CRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr', 'onread': 'rset'},

            # Write-zero-to-clear/set/toggle
            'W0C': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wzc'},
            'W0S': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wzs'},
            'W0T': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wzt'},
            'W0SRC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wzs', 'onread': 'rclr'},
            'W0CRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wzc', 'onread': 'rset'},

            # Write-once (first write only)
            'WO1': {'sw': 'w', 'hw': 'rw'},

            # Hardware/software decoupled
            'HWR': {'sw': 'r', 'hw': 'r'},   # HW read-only, SW read-only
            'HWW': {'sw': 'na', 'hw': 'w'},  # HW write-only, SW no access
        }
        return access_map.get(access.upper(), {'sw': 'rw', 'hw': 'rw'})

    def _parse_reset_value(self, reset_val: str) -> str:
        """Parse and format reset value"""
        if not reset_val:
            return "0"
        reset_str = str(reset_val).strip()
        # Handle format like 32'h0 or 32'b0 or just 0
        if "'" in reset_str:
            return reset_str
        try:
            if reset_str.startswith('0x'):
                val = int(reset_str, 16)
            else:
                val = int(reset_str)
            return f"32'h{val:X}"
        except:
            return "0"

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save content to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving RDL: {e}")
            return False


class RALFGenerator:
    """Generate UVM RALF format"""

    def generate(self, hierarchy) -> str:
        """Generate RALF from hierarchy"""
        lines = [
            "// UVM RALF Register Description",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
        ]

        for module in hierarchy.top_modules:
            lines.extend(self._generate_block(module))
            lines.append("")

        return '\n'.join(lines)

    def _generate_block(self, module, indent: int = 0) -> List[str]:
        """Generate block for a module"""
        prefix = "    " * indent
        lines = [
            f"{prefix}block {module.name} @0x{module.start_addr:X} {{",
            f"{prefix}    bytes {module.size if module.size > 0 else 1024};",
        ]

        for reg in module.registers:
            lines.extend(self._generate_register(reg, indent + 1))

        for sub in module.submodules:
            lines.append(f"{prefix}    block {sub.name} {{")
            lines.append(f"{prefix}        bytes {sub.size if sub.size > 0 else 1024};")
            lines.append(f"{prefix}    }}")

        lines.append(f"{prefix}}}")
        return lines

    def _generate_register(self, reg, indent: int) -> List[str]:
        """Generate register in RALF"""
        prefix = "    " * indent
        lines = [
            f"{prefix}register {reg.name} @0x{reg.offset:X} {{",
            f"{prefix}    bytes {reg.width // 8};",
        ]

        for field in reg.fields:
            lines.append(f"{prefix}    field {field.name} @{field.lsb} {{")
            lines.append(f"{prefix}        bits {field.msb - field.lsb + 1};")
            access = self._map_ralf_access(field.access)
            lines.append(f"{prefix}        access {access};")
            reset_val = self._parse_reset_value(field.reset_value)
            lines.append(f"{prefix}        reset {reset_val};")
            lines.append(f"{prefix}    }}")

        lines.append(f"{prefix}}}")
        return lines

    def _map_ralf_access(self, access: str) -> str:
        """Map access type to RALF format

        RALF supports: rw, ro, wo, rw1, wo1, w1c
        """
        access_map = {
            # Basic types
            'RW': 'rw',
            'RO': 'ro',
            'WO': 'wo',
            'NA': 'rw',  # Default to rw for NA

            # Read side-effects (treat as RO for RALF)
            'RC': 'ro',
            'RS': 'ro',
            'RAC': 'ro',
            'RAS': 'ro',

            # Write side-effects (treat as RW for RALF)
            'WRC': 'rw',
            'WRS': 'rw',
            'WC': 'rw',
            'WS': 'rw',
            'WT': 'rw',
            'WCRS': 'rw',

            # Write-one-to-clear/set (RALF has w1c)
            'W1C': 'w1c',
            'W1S': 'rw',  # RALF doesn't have w1s, use rw
            'W1T': 'rw',
            'W1SRC': 'w1c',  # Closest match
            'W1CRS': 'rw',

            # Write-zero-to-X
            'W0C': 'rw',
            'W0S': 'rw',
            'W0T': 'rw',
            'W0SRC': 'rw',
            'W0CRS': 'rw',

            # Write-once
            'WO1': 'wo1',

            # Hardware decoupled
            'HWR': 'ro',
            'HWW': 'wo',
        }
        return access_map.get(access.upper() if access else 'RW', 'rw')

    def _parse_reset_value(self, reset_val: str) -> str:
        """Parse reset value for RALF"""
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

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save content to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving RALF: {e}")
            return False


class CHeaderGenerator:
    """Generate C Header file"""

    def generate(self, hierarchy) -> str:
        """Generate C header from hierarchy"""
        lines = [
            "/* C Header Register Description */",
            f"/* Generated at: {datetime.now().isoformat()} */",
            "",
            "#ifndef _REGISTERS_H_",
            "#define _REGISTERS_H_",
            "",
            "#include <stdint.h>",
            "",
            "#define REG32(_addr) (*(volatile uint32_t *)(_addr))",
            "#define REG64(_addr) (*(volatile uint64_t *)(_addr))",
            "",
        ]

        for module in hierarchy.top_modules:
            lines.extend(self._generate_module(module))

        lines.extend([
            "",
            "#endif /* _REGISTERS_H_ */",
        ])

        return '\n'.join(lines)

    def _generate_module(self, module) -> List[str]:
        """Generate module section"""
        lines = [
            "",
            f"/* Module: {module.name} */",
            f"#define {module.name.upper()}_BASE_ADDR    0x{module.start_addr:08X}",
        ]

        for reg in module.registers:
            lines.extend(self._generate_register(reg, module.name, module.start_addr))

        return lines

    def _generate_register(self, reg, module_name: str, base_addr: int) -> List[str]:
        """Generate register defines"""
        reg_name = f"{module_name.upper()}_{reg.name.upper()}"
        full_addr = base_addr + reg.offset

        lines = [
            "",
            f"/* Register: {reg.name} */",
            f"#define {reg_name}_OFFSET    0x{reg.offset:04X}",
            f"#define {reg_name}_ADDR      ({module_name.upper()}_BASE_ADDR + {reg_name}_OFFSET)",
            f"#define {reg_name}_REG       REG32({reg_name}_ADDR)",
        ]

        for field in reg.fields:
            lines.extend(self._generate_field(field, reg_name))

        return lines

    def _generate_field(self, field, reg_name: str) -> List[str]:
        """Generate field defines"""
        field_name = f"{reg_name}_{field.name.upper()}"
        width = field.msb - field.lsb + 1
        mask = (1 << width) - 1

        return [
            f"#define {field_name}_POS     {field.lsb}",
            f"#define {field_name}_MASK    0x{mask:X}",
            f"#define {field.name.upper()}_WIDTH   {width}",
        ]

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save content to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving C header: {e}")
            return False


class SVHeaderGenerator:
    """Generate SystemVerilog Header file"""

    def generate(self, hierarchy) -> str:
        """Generate SV header from hierarchy"""
        lines = [
            "// SystemVerilog Header Register Description",
            f"// Generated at: {datetime.now().isoformat()}",
            "",
            "`ifndef _REGISTERS_SVH_",
            "`define _REGISTERS_SVH_",
            "",
        ]

        for module in hierarchy.top_modules:
            lines.extend(self._generate_module(module))

        lines.extend([
            "",
            "`endif // _REGISTERS_SVH_",
        ])

        return '\n'.join(lines)

    def _generate_module(self, module) -> List[str]:
        """Generate module section"""
        lines = [
            "",
            f"// Module: {module.name}",
            f"`define {module.name.upper()}_BASE_ADDR    32'h{module.start_addr:08X}",
        ]

        for reg in module.registers:
            lines.extend(self._generate_register(reg, module.name, module.start_addr))

        return lines

    def _generate_register(self, reg, module_name: str, base_addr: int) -> List[str]:
        """Generate register defines"""
        reg_name = f"{module_name.upper()}_{reg.name.upper()}"

        lines = [
            "",
            f"// Register: {reg.name}",
            f"`define {reg_name}_OFFSET    16'h{reg.offset:04X}",
            f"`define {reg_name}_ADDR      (`{module_name.upper()}_BASE_ADDR + `{reg_name}_OFFSET)",
        ]

        for field in reg.fields:
            lines.extend(self._generate_field(field, reg_name))

        return lines

    def _generate_field(self, field, reg_name: str) -> List[str]:
        """Generate field defines"""
        field_name = f"{reg_name}_{field.name.upper()}"
        width = field.msb - field.lsb + 1
        mask = (1 << width) - 1

        return [
            f"`define {field_name}_POS     {field.lsb}",
            f"`define {field_name}_MASK    {width}'h{mask:X}",
            f"`define {field_name}_WIDTH   {width}",
        ]

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save content to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        except Exception as e:
            print(f"Error saving SV header: {e}")
            return False


class CodeGenerator:
    """Main code generator that coordinates all format generators"""

    def __init__(self):
        self.generators = {
            'rdl': RDLGenerator(),
            'ralf': RALFGenerator(),
            'header': CHeaderGenerator(),
            'svheader': SVHeaderGenerator(),
        }

    def generate(self, format_type: str, hierarchy) -> str:
        """Generate code for specified format"""
        generator = self.generators.get(format_type.lower())
        if not generator:
            raise ValueError(f"Unknown format: {format_type}")
        return generator.generate(hierarchy)

    def generate_all(self, hierarchy) -> Dict[str, str]:
        """Generate code for all formats"""
        return {fmt: gen.generate(hierarchy) for fmt, gen in self.generators.items()}

    def save_to_file(self, content: str, file_path: Path) -> bool:
        """Save content to file"""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content if content else "")
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False
