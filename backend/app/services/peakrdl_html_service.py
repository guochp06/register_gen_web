"""
PeakRDL HTML Service - Generate HTML using PeakRDL

This service replaces the custom HTML generator with PeakRDL's official HTML generator.
"""
import shutil
import tempfile
from pathlib import Path
from typing import List, Dict, Optional

from app.services.hierarchy_parser import RegisterHierarchy
from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
from app.services.ralf_parser import RALFParser, find_modules_in_ralf
from app.core.config import settings


class PeakRDLHTMLService:
    """Service to generate HTML using PeakRDL"""

    def __init__(self):
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def generate_html(self,
                      hierarchy: RegisterHierarchy,
                      version_id: Optional[int] = None,
                      ralf_file: Optional[str] = None,
                      version_name: Optional[str] = None,
                      output_base_dir: Optional[Path] = None,
                      user_id: str = 'default') -> Dict[str, any]:
        """
        Generate HTML for the register hierarchy using PeakRDL.

        Args:
            hierarchy: The register hierarchy parsed from Excel
            ralf_file: Optional path to RALF file for mixed source
            version_name: Optional version name for directory naming (defaults to hierarchy.version_name)
            output_base_dir: Optional base directory for output (for transactional upload)
            user_id: User ID for directory isolation (defaults to 'default')

        Returns:
            Dict with 'success', 'html_path', 'warnings', 'errors'
        """
        self.warnings = []
        self.errors = []

        # Use version_name for directory naming (prioritize passed version_name for consistency)
        # Unified path: output/{user_id}/{version_name}/rdl/ (consistent with _generate_all_codes)
        import re
        def _sanitize(name):
            return re.sub(r'[<>?":/\\|?*]', '_', name) if name else name
        output_suffix = _sanitize(version_name) if version_name else _sanitize(hierarchy.version_name)

        # Support transactional upload by using custom output base directory
        base_dir = output_base_dir if output_base_dir else settings.OUTPUT_DIR
        rdl_output_dir = base_dir / user_id / output_suffix / 'rdl'
        rdl_output_dir.mkdir(parents=True, exist_ok=True)

        # Find the top addrmap name for RDL file naming
        top_name = hierarchy.top_addrmap_name if hierarchy.top_addrmap_name else None
        if not top_name and hierarchy.top_modules:
            # Find the addrmap module (has submodules, no registers)
            for module in hierarchy.top_modules:
                if len(module.submodules) > 0 and len(module.registers) == 0:
                    top_name = module.name
                    break
            if not top_name:
                top_name = hierarchy.top_modules[0].name

        if not top_name:
            self.errors.append("No top module found in hierarchy")
            return {'success': False, 'errors': self.errors}

        # The RDL wrapper uses a unique name to avoid conflicts
        wrapper_name = f"{top_name}_top"
        if wrapper_name == top_name:
            wrapper_name = f"{top_name}_root"

        # Step 1: Generate a single monolithic RDL file using PeakRDLCompatibleRDLGenerator
        # This is used for HTML generation only (not saved to disk)
        # The modular RDL files (soc_addr_map.rdl with includes) are generated separately
        generator = PeakRDLCompatibleRDLGenerator()
        rdl_content = generator.generate(hierarchy)
        self.warnings.extend(generator.warnings)

        # Save to a temp file for PeakRDL compilation (don't pollute the output directory)
        temp_rdl_file = None
        rdl_file_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.rdl', delete=False) as f:
                f.write(rdl_content)
                temp_rdl_file = Path(f.name)
            rdl_file_path = temp_rdl_file

            # Step 2: Compile RDL with PeakRDL
            import time
            start_time = time.time()
            print(f"[PeakRDL HTML] Starting RDL compilation for {len(rdl_content)} bytes")

            from systemrdl import RDLCompiler
            from systemrdl.messages import RDLCompileError

            try:
                from systemrdl.messages import MessagePrinter

                # Create a message printer to capture all messages
                class CaptureMessagePrinter(MessagePrinter):
                    def __init__(self, warning_list, error_list, rdl_path):
                        super().__init__()
                        self.warning_list = warning_list
                        self.error_list = error_list
                        self.rdl_path = rdl_path

                    def print_message(self, severity, text, src_ref=None):
                        msg = f"{severity.name}: {text}"
                        if src_ref:
                            # Try to get line number from source ref
                            try:
                                line_info = ""
                                if hasattr(src_ref, 'line'):
                                    line_info = f" line {src_ref.line}"
                                elif hasattr(src_ref, 'start_line'):
                                    line_info = f" line {src_ref.start_line}"
                                elif hasattr(src_ref, 'segments') and src_ref.segments:
                                    seg = src_ref.segments[0]
                                    if hasattr(seg, 'line'):
                                        line_info = f" line {seg.line}"
                                msg += f" at {self.rdl_path}{line_info}"
                            except Exception as e:
                                msg += f" at {src_ref}"
                        if severity.value >= 3:  # ERROR or FATAL
                            self.error_list.append(msg)
                        elif severity.value >= 2:  # WARNING
                            self.warning_list.append(msg)
                        super().print_message(severity, text, src_ref)

                rdlc = RDLCompiler(message_printer=CaptureMessagePrinter(self.warnings, self.errors, str(rdl_file_path)))
                print(f"[PeakRDL HTML] Compiling RDL file: {rdl_file_path}")
                rdlc.compile_file(str(rdl_file_path))
                print(f"[PeakRDL HTML] RDL compiled in {time.time() - start_time:.2f}s, elaborating...")
                root = rdlc.elaborate()
                print(f"[PeakRDL HTML] RDL elaborated in {time.time() - start_time:.2f}s")
            except Exception as e:
                # Capture all errors including stderr output
                import traceback
                import io
                import sys

                error_detail = traceback.format_exc()
                if not self.errors:
                    self.errors.append(f"RDL compilation failed: {str(e)}")
                self.errors.append(f"Detail: {error_detail}")

                # Try to capture any stderr messages
                try:
                    # Read the RDL file content for debugging
                    rdl_content = rdl_file_path.read_text(encoding='utf-8')
                    # Count lines
                    lines = rdl_content.split('\n')
                    self.errors.append(f"RDL file has {len(lines)} lines")

                    # Check for duplicate definitions
                    import re
                    addrmap_defs = re.findall(r'^addrmap\s+(\w+)\s*\{', rdl_content, re.MULTILINE)
                    from collections import Counter
                    duplicates = {name: count for name, count in Counter(addrmap_defs).items() if count > 1}
                    if duplicates:
                        self.errors.append(f"Duplicate addrmap definitions found: {duplicates}")
                except Exception as read_err:
                    self.errors.append(f"Error analyzing RDL file: {read_err}")

                return {'success': False, 'errors': self.errors}

            # Find top node (using wrapper_name which is the root instance)
            top_node = None
            for child in root.children():
                if child.inst_name == wrapper_name:
                    top_node = child
                    break

            if not top_node:
                # Try alternative names for backwards compatibility
                for child in root.children():
                    if child.inst_name == top_name:
                        top_node = child
                        break

            if not top_node:
                # List available nodes for debugging
                available = [c.inst_name for c in root.children()]
                self.errors.append(f"Top node '{wrapper_name}' not found in elaborated RDL")
                self.errors.append(f"Available nodes: {available}")
                return {'success': False, 'errors': self.errors}

            # Step 4: Generate HTML with PeakRDL
            from peakrdl_html import HTMLExporter

            # Use version_name for directory naming
            # Unified path: output/{user_id}/{version_name}/html/ (consistent with other formats)
            # Support transactional upload by using custom output base directory
            output_dir = base_dir / user_id / output_suffix / 'html'

            # Clean old output if exists
            if output_dir.exists():
                shutil.rmtree(output_dir)

            exporter = HTMLExporter()
            print(f"[PeakRDL HTML] Exporting HTML to: {output_dir}")
            export_start = time.time()
            exporter.export(top_node, str(output_dir))
            print(f"[PeakRDL HTML] HTML exported in {time.time() - export_start:.2f}s")

            # Step 5: Verify output
            index_file = output_dir / "index.html"
            if not index_file.exists():
                self.errors.append("HTML generation failed: index.html not created")
                return {'success': False, 'errors': self.errors}

            return {
                'success': True,
                'html_path': str(output_dir),
                'html_url': f"/static/{user_id}/{output_suffix}/html/index.html",
                'rdl_path': str(rdl_file_path) if rdl_file_path else None,
                'warnings': self.warnings,
                'errors': self.errors
            }

        except Exception as e:
            self.errors.append(f"HTML generation failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                'success': False,
                'errors': self.errors,
                'warnings': self.warnings
            }
        finally:
            # Clean up temp RDL file
            if temp_rdl_file and temp_rdl_file.exists():
                temp_rdl_file.unlink(missing_ok=True)

    def _process_ralf_file(self, ralf_file: str, hierarchy: RegisterHierarchy) -> tuple[Dict[str, str], List[str]]:
        """
        Process RALF file and extract modules that match Excel hierarchy.

        Returns:
            Tuple of (ralf_modules dict, warnings list)
        """
        warnings = []
        ralf_modules = {}

        parser = RALFParser()
        root_module = parser.parse_file(ralf_file)

        if not root_module:
            warnings.append(f"Failed to parse RALF file: {ralf_file}")
            return ralf_modules, warnings

        # Find all modules in the RALF hierarchy
        def collect_ralf_modules(module: 'Module', collected: Dict[str, 'Module']):
            base_name = getattr(module, 'base_module_name', module.name) if getattr(module, 'is_array_instance', False) else module.name

            if base_name not in collected:
                collected[base_name] = module

            for sub in module.submodules:
                collect_ralf_modules(sub, collected)

        ralf_module_map = {}
        collect_ralf_modules(root_module, ralf_module_map)

        # Check for conflicts with Excel modules
        excel_module_names = set(hierarchy.all_modules.keys())

        for ralf_name, ralf_module in ralf_module_map.items():
            if ralf_name in excel_module_names:
                # Conflict: RALF overrides Excel
                warnings.append(f"Module '{ralf_name}' exists in both Excel and RALF. Using RALF version.")

            # Convert RALF module to RDL
            rdl_content = self._convert_module_to_rdl(ralf_module)
            ralf_modules[ralf_name] = rdl_content

        # Also check for modules in Excel that are not in RALF
        for excel_name in excel_module_names:
            excel_module = hierarchy.all_modules[excel_name]
            base_name = getattr(excel_module, 'base_module_name', excel_module.name) if getattr(excel_module, 'is_array_instance', False) else excel_module.name

            if base_name in ralf_module_map:
                # This Excel module should be replaced by RALF
                if base_name not in ralf_modules:
                    rdl_content = self._convert_module_to_rdl(ralf_module_map[base_name])
                    ralf_modules[base_name] = rdl_content

        return ralf_modules, warnings

    def _convert_module_to_rdl(self, module: 'Module') -> str:
        """Convert a RALF-parsed module to SystemRDL string"""
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
            lines.extend(self._convert_register_to_rdl(reg))

        # For empty modules, add a placeholder register
        if not module.registers and not module.submodules:
            lines.append("    // Address placeholder - no registers defined")
            lines.append("    reg {")
            lines.append("        field {")
            lines.append("            sw = rw;")
            lines.append("            hw = r;")
            lines.append("            reset = 0;")
            lines.append('            desc = "Reserved - address placeholder";')
            lines.append("        } reserved [31:0];")
            lines.append("    } reserved_reg;")
            lines.append("")

        # Add submodules
        if module.submodules:
            lines.append("    // Sub-module instances")
            used_instance_names = set()
            for sub in module.submodules:
                sub_is_array = getattr(sub, 'is_array_instance', False)
                sub_base_name = getattr(sub, 'base_module_name', sub.name) if sub_is_array else sub.name
                rel_offset = sub.start_addr - module.start_addr
                # Handle negative offset: use sub's address as relative offset if it appears to be absolute
                if rel_offset < 0:
                    rel_offset = sub.start_addr

                # Generate unique instance name
                if sub_is_array:
                    inst_name = sub.name
                else:
                    inst_name = f"{sub.name}_inst"
                    base_inst_name = inst_name
                    counter = 1
                    while inst_name in used_instance_names:
                        inst_name = f"{base_inst_name}_{counter}"
                        counter += 1

                used_instance_names.add(inst_name)
                lines.append(f"    {sub_base_name} {inst_name} @0x{rel_offset:X};")
            lines.append("")

        lines.append("};")
        return '\n'.join(lines)

    def _convert_register_to_rdl(self, reg: 'Register') -> List[str]:
        """Convert a register to SystemRDL lines"""
        lines = [
            f"    reg {reg.name} {{",
        ]

        for field in reg.fields:
            access = self._map_access(field.access)
            lines.append("        field {")
            lines.append(f"            sw = {access['sw']};")
            lines.append(f"            hw = {access['hw']};")
            if access.get('onread'):
                lines.append(f"            onread = {access['onread']};")
            if access.get('onwrite'):
                lines.append(f"            onwrite = {access['onwrite']};")
            lines.append(f"            reset = {field.reset_value};")
            if field.description:
                escaped = field.description.replace('"', '\\"')
                lines.append(f'            desc = "{escaped}";')
            lines.append(f"        }} {field.name} [{field.msb}:{field.lsb}];")

        lines.append(f"    }} {reg.name} @ 0x{reg.offset:X};")
        lines.append("")
        return lines

    def _map_access(self, access: str) -> Dict[str, str]:
        """Map access type to SystemRDL properties"""
        access_map = {
            # 基础
            'RO': {'sw': 'r', 'hw': 'rw'},
            'RW': {'sw': 'rw', 'hw': 'rw'},
            'WO': {'sw': 'w', 'hw': 'rw'},
            # Write-Once
            'W1': {'sw': 'w1', 'hw': 'rw'},
            'WO1': {'sw': 'w1', 'hw': 'rw'},
            # Write-1-to-X
            'W1C': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr'},
            'W1S': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset'},
            'W1T': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wot'},
            # Write-0-to-X (需要woset)
            'W0C': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr', 'woset': 'true'},
            'W0S': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset', 'woset': 'true'},
            'W0T': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'wot', 'woset': 'true'},
            # Read-to-X
            'RC': {'sw': 'r', 'hw': 'rw', 'onread': 'rclr'},
            'RS': {'sw': 'r', 'hw': 'rw', 'onread': 'rset'},
            # Write-Read Combined
            'WRC': {'sw': 'rw', 'hw': 'rw', 'onread': 'rclr'},
            'WRS': {'sw': 'rw', 'hw': 'rw', 'onread': 'rset'},
            'WC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr'},
            'WS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset'},
            'WOC': {'sw': 'w', 'hw': 'rw', 'onwrite': 'woclr'},
            'WOS': {'sw': 'w', 'hw': 'rw', 'onwrite': 'woset'},
            # Complex Combined
            'W1SRC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset', 'onread': 'rclr'},
            'W1CRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr', 'onread': 'rset'},
            'W0SRC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset', 'onread': 'rclr', 'woset': 'true'},
            'W0CRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr', 'onread': 'rset', 'woset': 'true'},
            'WSRC': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woset', 'onread': 'rclr'},
            'WCRS': {'sw': 'rw', 'hw': 'rw', 'onwrite': 'woclr', 'onread': 'rset'},
        }
        return access_map.get(access.upper(), {'sw': 'rw', 'hw': 'rw'})


class PeakRDLGenerator:
    """Compatibility wrapper for existing CodeGenerator interface"""

    def __init__(self):
        self.service = PeakRDLHTMLService()

    def generate_html(self, hierarchy: RegisterHierarchy, version_id: Optional[int] = None, ralf_file: Optional[str] = None, user_id: str = 'default') -> Dict[str, any]:
        """Generate HTML using PeakRDL"""
        return self.service.generate_html(hierarchy, version_id=version_id, ralf_file=ralf_file, user_id=user_id)
