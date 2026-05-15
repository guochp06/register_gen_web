"""
PeakRDL Wrapper - Generate UVM and RTL from RDL
Requires: systemrdl-compiler, peakrdl-uvm, peakrdl-regblock
"""
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional, List
import tempfile
import shutil


class PeakRDLWrapper:
    """Wrapper for PeakRDL tools to generate UVM and RTL"""

    def __init__(self):
        self.check_dependencies()

    def check_dependencies(self) -> bool:
        """Check if required PeakRDL packages are installed"""
        try:
            from systemrdl import RDLCompiler
            return True
        except ImportError:
            print("Warning: systemrdl-compiler not installed. UVM/RTL generation disabled.")
            return False

    def generate_uvm(self, rdl_file: Path, output_file: Path, options: Optional[dict] = None) -> tuple[bool, str]:
        """
        Generate UVM register model from RDL file

        Args:
            rdl_file: Path to input RDL file
            output_file: Path to output SV file
            options: Optional generation options

        Returns:
            (success: bool, message: str)
        """
        try:
            from systemrdl import RDLCompiler
            from peakrdl_uvm import UVMExporter

            # Compile RDL
            rdlc = RDLCompiler()
            rdlc.compile_file(str(rdl_file))
            root = rdlc.elaborate()

            # Generate UVM
            exporter = UVMExporter()
            output_file.parent.mkdir(parents=True, exist_ok=True)
            exporter.export(root, str(output_file), export_as_package=False, use_uvm_factory=True)

            return True, f"UVM generated: {output_file}"

        except Exception as e:
            return False, f"UVM generation failed: {str(e)}"

    def generate_rtl(self, rdl_file: Path, output_dir: Path, cpu_if: str = "axilite",
                     address_width: int = 32) -> tuple[bool, str]:
        """
        Generate RTL register block from RDL file

        Args:
            rdl_file: Path to input RDL file
            output_dir: Directory for output files
            cpu_if: CPU interface type (apb3, apb4, axilite)
            address_width: Address bus width

        Returns:
            (success: bool, message: str)
        """
        try:
            from systemrdl import RDLCompiler
            from peakrdl_regblock import RegblockExporter
            from peakrdl_regblock.cpuif.axi4lite import AXI4Lite_Cpuif
            from peakrdl_regblock.cpuif.apb3 import APB3_Cpuif
            from peakrdl_regblock.cpuif.apb4 import APB4_Cpuif
            from peakrdl_regblock.udps import ALL_UDPS

            # Select CPU interface
            cpuif_map = {
                'axilite': AXI4Lite_Cpuif,
                'apb3': APB3_Cpuif,
                'apb4': APB4_Cpuif,
            }
            cpuif_cls = cpuif_map.get(cpu_if.lower(), AXI4Lite_Cpuif)

            # Compile RDL
            rdlc = RDLCompiler()
            for udp in ALL_UDPS:
                rdlc.register_udp(udp)

            rdlc.compile_file(str(rdl_file))
            root = rdlc.elaborate()

            # Generate RTL
            exporter = RegblockExporter()
            output_dir.mkdir(parents=True, exist_ok=True)
            exporter.export(
                node=root,
                output_dir=str(output_dir),
                address_width=address_width,
                cpuif_cls=cpuif_cls
            )

            return True, f"RTL generated in: {output_dir}"

        except Exception as e:
            return False, f"RTL generation failed: {str(e)}"

    def generate_from_content(self, rdl_content: str, version_name: str,
                               output_base_dir: Path, cpu_if: str = "axilite") -> dict:
        """
        Generate UVM and RTL from RDL content string

        Args:
            rdl_content: RDL content as string
            version_name: Version name for output directory (used for display/naming)
            output_base_dir: Base output directory
            cpu_if: CPU interface type
            version_id: Version ID for output directory (used for concurrency safety)

        Returns:
            Dictionary with generation results
        """
        results = {
            'uvm': {'success': False, 'path': None, 'message': ''},
            'rtl': {'success': False, 'path': None, 'message': ''},
        }

        # Check dependencies
        if not self.check_dependencies():
            results['uvm']['message'] = "systemrdl-compiler not installed"
            results['rtl']['message'] = "systemrdl-compiler not installed"
            return results

        # Use version_name for directory naming to match other endpoints
        version_dir_name = version_name

        # Unified output paths: output/{user_id}/{version_name}/uvm/ and output/{user_id}/{version_name}/rtl/
        # (output_base_dir is expected to already include user_id, e.g. settings.OUTPUT_DIR / user_id)
        uvm_output = output_base_dir / version_dir_name / "uvm" / f"{version_name}_regmodel.sv"
        rtl_output_dir = output_base_dir / version_dir_name / "rtl"

        # Create temp directory for RDL file
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_rdl = Path(temp_dir) / f"{version_name}.rdl"

            # Write RDL content to temp file
            with open(temp_rdl, 'w', encoding='utf-8') as f:
                f.write(rdl_content)

            # Generate UVM
            success, msg = self.generate_uvm(temp_rdl, uvm_output)
            results['uvm']['success'] = success
            results['uvm']['path'] = str(uvm_output) if success else None
            results['uvm']['message'] = msg

            # Generate RTL
            success, msg = self.generate_rtl(temp_rdl, rtl_output_dir, cpu_if)
            results['rtl']['success'] = success
            results['rtl']['path'] = str(rtl_output_dir) if success else None
            results['rtl']['message'] = msg

        return results


class PeakRDLGenerator:
    """Higher-level interface for PeakRDL generation"""

    def __init__(self):
        self.wrapper = PeakRDLWrapper()

    def is_available(self) -> bool:
        """Check if PeakRDL is available"""
        return self.wrapper.check_dependencies()

    def generate_all(self, rdl_content: str, version_name: str,
                     output_base_dir: Path, cpu_if: str = "axilite") -> dict:
        """Generate UVM and RTL"""
        return self.wrapper.generate_from_content(
            rdl_content, version_name, output_base_dir, cpu_if
        )

    def generate_uvm_for_module(self, rdl_content: str, module_name: str,
                                  include_paths: list = None,
                                  output_dir: str = None) -> tuple[bool, str]:
        """Generate UVM register model for a single module from RDL content

        Args:
            rdl_content: RDL content string
            module_name: Module name
            include_paths: List of paths to search for included RDL files
            output_dir: Optional directory to write RDL file (if None, creates temp dir)

        Returns:
            (success: bool, content_or_error: str)
        """
        import tempfile
        from pathlib import Path

        try:
            from systemrdl import RDLCompiler
            from peakrdl_uvm import UVMExporter
            from systemrdl import RDLCompileError

            # Use provided output_dir or create temp directory
            if output_dir:
                temp_rdl = Path(output_dir) / f"{module_name}.rdl"
                temp_rdl.write_text(rdl_content, encoding='utf-8')

                try:
                    # Compile RDL with include paths
                    rdlc = RDLCompiler()
                    rdlc.compile_file(str(temp_rdl), incl_search_paths=include_paths)
                    root = rdlc.elaborate()

                    # Generate UVM to string
                    exporter = UVMExporter()
                    with tempfile.TemporaryDirectory() as temp_out:
                        output_file = Path(temp_out) / f"{module_name}_regmodel.sv"
                        exporter.export(root, str(output_file), export_as_package=False, use_uvm_factory=True)
                        uvm_content = output_file.read_text()
                        return True, uvm_content
                except RDLCompileError as e:
                    return False, f"RDL compile error: {str(e)}"
            else:
                # Original behavior: write to temp directory
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp_rdl = Path(temp_dir) / f"{module_name}.rdl"
                    temp_rdl.write_text(rdl_content, encoding='utf-8')

                    try:
                        # Compile RDL with include paths
                        rdlc = RDLCompiler()
                        rdlc.compile_file(str(temp_rdl), incl_search_paths=include_paths)
                        root = rdlc.elaborate()

                        # Generate UVM to string
                        exporter = UVMExporter()
                        with tempfile.TemporaryDirectory() as temp_out:
                            output_file = Path(temp_out) / f"{module_name}_regmodel.sv"
                            exporter.export(root, str(output_file), export_as_package=False, use_uvm_factory=True)
                            uvm_content = output_file.read_text()
                            return True, uvm_content
                    except RDLCompileError as e:
                        return False, f"RDL compile error: {str(e)}"

        except Exception as e:
            return False, f"UVM generation failed: {str(e)}"

    def generate_cheader_for_module(self, rdl_content: str, module_name: str,
                                     generate_bitfields: bool = False) -> tuple[bool, str]:
        """Generate C header for a single module from RDL content

        Args:
            rdl_content: RDL content string
            module_name: Module name
            generate_bitfields: If True, generate struct bitfields for registers.
                               If False (default), generate simple #define macros.

        Returns:
            (success: bool, content_or_error: str)
        """
        import tempfile
        from pathlib import Path

        try:
            from systemrdl import RDLCompiler
            from peakrdl_cheader.exporter import CHeaderExporter

            # Write RDL to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.rdl', delete=False) as f:
                f.write(rdl_content)
                temp_rdl = Path(f.name)

            try:
                # Compile RDL
                rdlc = RDLCompiler()
                rdlc.compile_file(str(temp_rdl))
                root = rdlc.elaborate()

                # Generate C header to string
                exporter = CHeaderExporter()
                with tempfile.TemporaryDirectory() as temp_out:
                    output_file = Path(temp_out) / f"{module_name}.h"
                    # Use generate_bitfields=False to generate simple macros instead of structs
                    exporter.export(root, str(output_file), generate_bitfields=generate_bitfields)
                    header_content = output_file.read_text()
                    return True, header_content
            finally:
                temp_rdl.unlink(missing_ok=True)

        except Exception as e:
            return False, f"C header generation failed: {str(e)}"

    def generate_rtl_for_module(self, rdl_content: str, module_name: str,
                                 cpu_if: str = "axilite", address_width: int = 32) -> tuple[bool, str]:
        """Generate RTL register block for a single module from RDL content

        Args:
            rdl_content: RDL content string
            module_name: Module name
            cpu_if: CPU interface type (apb3, apb4, axilite)
            address_width: Address bus width

        Returns:
            (success: bool, content_or_error: str)
            On success, content is a concatenated string of all RTL files
        """
        import tempfile
        from pathlib import Path

        try:
            from systemrdl import RDLCompiler
            from peakrdl_regblock import RegblockExporter
            from peakrdl_regblock.cpuif.axi4lite import AXI4Lite_Cpuif
            from peakrdl_regblock.cpuif.apb3 import APB3_Cpuif
            from peakrdl_regblock.cpuif.apb4 import APB4_Cpuif
            from peakrdl_regblock.udps import ALL_UDPS

            # Select CPU interface
            cpuif_map = {
                'axilite': AXI4Lite_Cpuif,
                'apb3': APB3_Cpuif,
                'apb4': APB4_Cpuif,
            }
            cpuif_cls = cpuif_map.get(cpu_if.lower(), AXI4Lite_Cpuif)

            # Write RDL to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.rdl', delete=False) as f:
                f.write(rdl_content)
                temp_rdl = Path(f.name)

            try:
                # Compile RDL
                rdlc = RDLCompiler()
                for udp in ALL_UDPS:
                    rdlc.register_udp(udp)
                rdlc.compile_file(str(temp_rdl))
                root = rdlc.elaborate()

                # Generate RTL to temp directory
                exporter = RegblockExporter()
                with tempfile.TemporaryDirectory() as temp_out:
                    output_dir = Path(temp_out)
                    exporter.export(
                        node=root,
                        output_dir=str(output_dir),
                        address_width=address_width,
                        cpuif_cls=cpuif_cls
                    )

                    # Read all generated RTL files and concatenate
                    rtl_files = sorted(output_dir.glob('*.sv'))
                    rtl_content_parts = []
                    for rtl_file in rtl_files:
                        rtl_content_parts.append(f"// File: {rtl_file.name}")
                        rtl_content_parts.append(rtl_file.read_text())
                        rtl_content_parts.append("")

                    full_rtl = '\n'.join(rtl_content_parts)
                    return True, full_rtl

            finally:
                temp_rdl.unlink(missing_ok=True)

        except Exception as e:
            return False, f"RTL generation failed: {str(e)}"
