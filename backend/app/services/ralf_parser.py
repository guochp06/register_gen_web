"""
RALF Parser - Parse UVM RALF format and convert to Module/Register objects
"""
import re
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from app.services.hierarchy_parser import Module, Register, RegisterField


class RALFParser:
    """Parse UVM RALF format"""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def parse_file(self, file_path: str) -> Optional[Module]:
        """Parse a RALF file and return the root module"""
        try:
            content = Path(file_path).read_text()
            return self.parse_content(content)
        except Exception as e:
            self.errors.append(f"Failed to read RALF file: {e}")
            return None

    def parse_content(self, content: str) -> Optional[Module]:
        """Parse RALF content string"""
        self.errors = []
        self.warnings = []

        try:
            # Parse blocks recursively
            root_module = self._parse_block(content.strip(), None)
            return root_module

        except Exception as e:
            self.errors.append(f"Parse error: {e}")
            return None

    def _parse_block(self, content: str, parent: Optional[Module]) -> Optional[Module]:
        """Parse a RALF block - handles nested structures properly"""
        # Match block definition start
        block_start_pattern = r'block\s+(\w+)\s+@0x([0-9a-fA-F]+)\s*\{'
        match = re.search(block_start_pattern, content, re.DOTALL)

        if not match:
            return None

        name = match.group(1)
        base_addr = int(match.group(2), 16)

        # Find the block body by counting braces (handles arbitrary nesting)
        start = match.end()  # Position right after opening brace
        brace_count = 1  # We've seen the opening brace
        end = start
        for i, char in enumerate(content[start:]):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end = start + i
                    break

        body = content[start:end]

        # Create module
        module = Module(
            name=name,
            start_addr=base_addr,
            end_addr=base_addr + 0xFFF,  # Will be updated based on content
            size=0x1000,
            source_file="ralf"
        )

        if parent:
            module.start_addr = parent.start_addr + base_addr
            module.end_addr = module.start_addr + 0xFFF

        # Parse bytes directive
        bytes_match = re.search(r'bytes\s+(\d+);', body)
        if bytes_match:
            module.size = int(bytes_match.group(1))
            module.end_addr = module.start_addr + module.size

        # Parse nested blocks (submodules)
        nested_blocks = self._parse_nested_blocks(body, module)
        module.submodules = nested_blocks

        # Parse registers
        registers = self._parse_registers(body, module)
        module.registers = registers

        return module

    def _parse_nested_blocks(self, content: str, parent: Module) -> List[Module]:
        """Parse nested blocks"""
        submodules = []
        block_pattern = r'block\s+(\w+)\s+@0x([0-9a-fA-F]+)\s*\{'

        pos = 0
        while True:
            match = re.search(block_pattern, content[pos:], re.DOTALL)
            if not match:
                break

            block_name = match.group(1)
            offset = int(match.group(2), 16)

            # Find the block body (handle nested braces)
            start = pos + match.end() - 1  # Position of opening brace
            brace_count = 0
            end = start
            for i, char in enumerate(content[start:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = start + i + 1
                        break

            block_content = content[start:end]
            submodule = self._parse_block(f"block {block_name} @0x{offset:X} {block_content}", parent)
            if submodule:
                submodules.append(submodule)

            pos = end

        return submodules

    def _parse_registers(self, content: str, module: Module) -> List[Register]:
        """Parse registers - handles nested field definitions"""
        registers = []
        reg_start_pattern = r'register\s+(\w+)\s+@0x([0-9a-fA-F]+)\s*\{'

        pos = 0
        while True:
            match = re.search(reg_start_pattern, content[pos:], re.DOTALL)
            if not match:
                break

            reg_name = match.group(1)
            offset = int(match.group(2), 16)

            # Find the register body (handle nested braces for fields)
            # match.end() is relative to content[pos:], so adjust
            start = pos + match.end()  # Position right after opening brace
            brace_count = 1  # We've already seen the opening brace
            end = start
            for i, char in enumerate(content[start:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end = start + i
                        break

            reg_body = content[start:end]  # Extract body without outer braces

            reg = self._parse_register(reg_name, offset, reg_body)
            if reg:
                registers.append(reg)

            pos = end + 1  # Move past the closing brace

        return registers

    def _parse_register(self, name: str, offset: int, body: str) -> Optional[Register]:
        """Parse a single register"""
        # Parse bytes
        bytes_match = re.search(r'bytes\s+(\d+);', body)
        width = 32  # default
        if bytes_match:
            width = int(bytes_match.group(1)) * 8

        # Parse fields
        fields = self._parse_fields(body)

        return Register(
            name=name,
            offset=offset,
            width=width,
            fields=fields
        )

    def _parse_fields(self, content: str) -> List[RegisterField]:
        """Parse fields"""
        fields = []
        field_pattern = r'field\s+(\w+)\s+@(\d+)\s*\{([^}]*)\}'

        for match in re.finditer(field_pattern, content, re.DOTALL):
            field_name = match.group(1)
            lsb = int(match.group(2))
            field_body = match.group(3)

            # Parse bits
            bits_match = re.search(r'bits\s+(\d+);', field_body)
            width = 1
            if bits_match:
                width = int(bits_match.group(1))
            msb = lsb + width - 1

            # Parse access
            access_match = re.search(r'access\s+(\w+);', field_body)
            access = "RW"
            if access_match:
                access = access_match.group(1).upper()

            # Parse reset
            reset_match = re.search(r'reset\s+(\d+);', field_body)
            reset_value = "0"
            if reset_match:
                reset_value = reset_match.group(1)

            fields.append(RegisterField(
                name=field_name,
                msb=msb,
                lsb=lsb,
                access=access,
                reset_value=reset_value,
                description=""
            ))

        return fields

    def parse_for_rdl_generation(self, file_path: str, module_name: str) -> Optional[str]:
        """
        Parse RALF and return raw RDL content for the specified module.
        This is used when we want to use RALF as the source for a specific module.
        """
        module = self.parse_file(file_path)
        if not module:
            return None

        # Find the requested module in the hierarchy
        target = self._find_module(module, module_name)
        if target:
            return self._convert_to_rdl(target)

        return None

    def _find_module(self, root: Module, name: str) -> Optional[Module]:
        """Find a module by name in the hierarchy"""
        if root.name == name:
            return root
        for sub in root.submodules:
            result = self._find_module(sub, name)
            if result:
                return result
        return None

    def _convert_to_rdl(self, module: Module) -> str:
        """Convert a module to SystemRDL format"""
        lines = [
            f"addrmap {module.name} {{",
            f'    name = "{module.name}";',
            "    default regwidth = 32;",
            "    addressing = compact;",
            "    lsb0;",
            "",
        ]

        # Add registers
        for reg in module.registers:
            lines.extend(self._convert_register_to_rdl(reg, 1))

        # Add submodules
        for sub in module.submodules:
            rel_offset = sub.start_addr - module.start_addr
            lines.append(f"    {sub.name} {sub.name}_inst @0x{rel_offset:X};")

        lines.append("};")
        return '\n'.join(lines)

    def _convert_register_to_rdl(self, reg: Register, indent: int) -> List[str]:
        """Convert a register to SystemRDL"""
        prefix = "    " * indent
        lines = [
            f"{prefix}reg {reg.name} {{",
        ]

        for field in reg.fields:
            access = self._map_access(field.access)
            lines.append(f"{prefix}    field {{")
            lines.append(f"{prefix}        sw = {access['sw']};")
            lines.append(f"{prefix}        hw = {access['hw']};")
            if access.get('onread'):
                lines.append(f"{prefix}        onread = {access['onread']};")
            if access.get('onwrite'):
                lines.append(f"{prefix}        onwrite = {access['onwrite']};")
            lines.append(f"{prefix}        reset = {field.reset_value};")
            if field.description:
                escaped = field.description.replace('"', '\\"')
                lines.append(f'{prefix}        desc = "{escaped}";')
            lines.append(f"{prefix}    }} {field.name} [{field.msb}:{field.lsb}];")

        lines.append(f"{prefix}}} {reg.name} @ 0x{reg.offset:X};")
        return lines

    def _map_access(self, access: str) -> Dict[str, str]:
        """Map RALF access to SystemRDL properties"""
        access_map = {
            'RW': {'sw': 'rw', 'hw': 'rw'},
            'RO': {'sw': 'r', 'hw': 'rw'},
            'WO': {'sw': 'w', 'hw': 'rw'},
            'RC': {'sw': 'r', 'hw': 'rw', 'onread': 'rclr'},
            'RS': {'sw': 'r', 'hw': 'rw', 'onread': 'rset'},
            'W1C': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr'},
            'W1S': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset'},
        }
        return access_map.get(access.upper(), {'sw': 'rw', 'hw': 'rw'})


def find_modules_in_ralf(file_path: str) -> List[str]:
    """Find all module names in a RALF file"""
    try:
        content = Path(file_path).read_text()
        block_pattern = r'block\s+(\w+)\s+@0x[0-9a-fA-F]+'
        return re.findall(block_pattern, content)
    except Exception:
        return []
