"""Final verification: Compare all RDL-exported files vs direct generation"""
import os
import re
from pathlib import Path
from app.services.rdl_exporter import RDLExporter
from app.services.module_code_generator import ModuleCodeGenerator
from app.services.hierarchy_parser import HierarchyParser

# Parse all files
parser = HierarchyParser()
hierarchy = parser.parse_files([
    '/home/xiaoer/register/addr_map_S/soc_addr_map.xls',
    '/home/xiaoer/register/addr_map_S/GCS.xls',
    '/home/xiaoer/register/addr_map_S/DRAM_IF.xls',
    '/home/xiaoer/register/addr_map_S/PE.xls',
    '/home/xiaoer/register/addr_map_S/PEC.xls',
    '/home/xiaoer/register/addr_map_S/C2C.xls'
], 'test')

print("=== Final Verification: RDL Export vs Direct Generation ===\n")

gen = ModuleCodeGenerator(Path('/tmp/test_output'))

results = {
    'ralf_match': 0,
    'ralf_mismatch': 0,
    'header_match': 0,
    'header_mismatch': 0,
    'svh_match': 0,
    'svh_mismatch': 0,
}

# Test all register modules
for name, mod in sorted(hierarchy.all_modules.items()):
    if len(mod.registers) == 0:
        continue  # Skip addr-map modules

    # Generate RDL
    rdl_content = gen._generate_rdl_for_base_module(mod, hierarchy.all_modules)
    
    # Export from RDL
    exporter = RDLExporter()
    export_results = exporter.export_from_rdl_content(rdl_content, mod.name)
    
    # Direct generation
    direct_ralf = gen._generate_ralf_for_base_module(mod)
    direct_header = gen._generate_c_header_for_base_module(mod)
    direct_svh = gen._generate_svh_for_base_module(mod)
    
    # Compare RALF registers
    export_regs = set(re.findall(r'register (\w+) @', export_results.get('ralf', '')))
    direct_regs = set(re.findall(r'register (\w+) @', direct_ralf))
    if export_regs == direct_regs:
        results['ralf_match'] += 1
    else:
        results['ralf_mismatch'] += 1
        print(f"RALF mismatch: {mod.name}")
    
    # Compare C Header offsets
    export_offsets = set(re.findall(r'#define (\w+_OFFSET)\s+0x[0-9A-Fa-f]+', export_results.get('header', '')))
    direct_offsets = set(re.findall(r'#define (\w+_OFFSET)\s+0x[0-9A-Fa-f]+', direct_header))
    if export_offsets == direct_offsets:
        results['header_match'] += 1
    else:
        results['header_mismatch'] += 1
        print(f"Header mismatch: {mod.name}")
    
    # Compare SVH defines
    export_defs = set(re.findall(r'`define (\w+_OFFSET)\s+', export_results.get('svh', '')))
    direct_defs = set(re.findall(r'`define (\w+_OFFSET)\s+', direct_svh))
    if export_defs == direct_defs:
        results['svh_match'] += 1
    else:
        results['svh_mismatch'] += 1
        print(f"SVH mismatch: {mod.name}")

print(f"\nResults:")
print(f"  RALF:  {results['ralf_match']} match, {results['ralf_mismatch']} mismatch")
print(f"  Header: {results['header_match']} match, {results['header_mismatch']} mismatch")
print(f"  SVH:    {results['svh_match']} match, {results['svh_mismatch']} mismatch")

if results['ralf_mismatch'] == 0 and results['header_mismatch'] == 0 and results['svh_mismatch'] == 0:
    print("\n✅ All files match perfectly!")
else:
    print("\n❌ Some files have mismatches")
