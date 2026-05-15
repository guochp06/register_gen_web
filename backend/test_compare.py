"""Compare RDL export vs direct generation for consistency"""
import tempfile
from pathlib import Path
from app.services.rdl_exporter import RDLExporter
from app.services.module_code_generator import ModuleCodeGenerator
from app.services.hierarchy_parser import HierarchyParser

# Parse all files to get complete hierarchy
parser = HierarchyParser()
hierarchy = parser.parse_files([
    '/home/xiaoer/register/addr_map_S/soc_addr_map.xls',
    '/home/xiaoer/register/addr_map_S/GCS.xls',
    '/home/xiaoer/register/addr_map_S/DRAM_IF.xls',
    '/home/xiaoer/register/addr_map_S/PE.xls',
    '/home/xiaoer/register/addr_map_S/PEC.xls',
    '/home/xiaoer/register/addr_map_S/C2C.xls'
], 'test')

print(f"Total modules: {len(hierarchy.all_modules)}")
print(f"Module names: {sorted(hierarchy.all_modules.keys())[:10]}")

# Get a register module (not addr-map)
reg_module = None
for name, mod in hierarchy.all_modules.items():
    if len(mod.registers) > 0:
        reg_module = mod
        print(f"\nTesting module: {name}")
        print(f"Registers: {len(mod.registers)}")
        print(f"Sample registers: {[r.name for r in mod.registers[:3]]}")
        break

if not reg_module:
    print("No register module found")
    exit(1)

# Method 1: Direct generation (fallback)
gen = ModuleCodeGenerator(Path('/tmp/test_output'))

# Get RDL content first
rdl_content = gen._generate_rdl_for_base_module(reg_module, hierarchy.all_modules)
print(f"\nRDL content length: {len(rdl_content)} bytes")

# Save RDL for inspection
with open('/tmp/test_module.rdl', 'w') as f:
    f.write(rdl_content)
print("RDL saved to /tmp/test_module.rdl")

# Method 2: RDL export
exporter = RDLExporter()
export_results = exporter.export_from_rdl_content(rdl_content, reg_module.name)

print(f"\nExport warnings: {exporter.get_warnings()}")
print(f"Export errors: {exporter.get_errors()}")

# Method 3: Fallback direct generation
direct_results = {
    'ralf': gen._generate_ralf_for_base_module(reg_module),
    'header': gen._generate_c_header_for_base_module(reg_module),
    'svh': gen._generate_svh_for_base_module(reg_module),
}

import re

print("\n=== Comparing RALF ===")
export_ralf = export_results.get('ralf', '')
direct_ralf = direct_results.get('ralf', '')

if not export_ralf:
    print("Export RALF is empty!")
else:
    export_regs = set(re.findall(r'register (\w+) @', export_ralf))
    direct_regs = set(re.findall(r'register (\w+) @', direct_ralf))
    print(f"Export RALF registers: {sorted(export_regs)}")
    print(f"Direct RALF registers: {sorted(direct_regs)}")
    print(f"Match: {export_regs == direct_regs}")

print("\n=== Comparing C Header ===")
export_header = export_results.get('header', '')
direct_header = direct_results.get('header', '')

if export_header and direct_header:
    export_defines = set(re.findall(r'#define (\w+)', export_header))
    direct_defines = set(re.findall(r'#define (\w+)', direct_header))
    print(f"Export defines count: {len(export_defines)}")
    print(f"Direct defines count: {len(direct_defines)}")
    
    # Show differences
    only_in_export = export_defines - direct_defines
    only_in_direct = direct_defines - export_defines
    if only_in_export:
        print(f"Only in export: {list(only_in_export)[:5]}")
    if only_in_direct:
        print(f"Only in direct: {list(only_in_direct)[:5]}")

print("\n=== Comparing SVH ===")
export_svh = export_results.get('svh', '')
direct_svh = direct_results.get('svh', '')

if export_svh and direct_svh:
    export_defs = set(re.findall(r'`define (\w+)', export_svh))
    direct_defs = set(re.findall(r'`define (\w+)', direct_svh))
    print(f"Export defines count: {len(export_defs)}")
    print(f"Direct defines count: {len(direct_defs)}")
    print(f"Match: {export_defs == direct_defs}")

print("\nTest complete!")
