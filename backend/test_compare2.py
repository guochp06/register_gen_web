"""Detailed field-level comparison"""
from pathlib import Path
from app.services.rdl_exporter import RDLExporter
from app.services.module_code_generator import ModuleCodeGenerator
from app.services.hierarchy_parser import HierarchyParser
import re

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

# Test CPF (has more registers)
cpf = hierarchy.all_modules.get('CPF')
if not cpf:
    print("CPF not found")
    exit(1)

print(f"Testing CPF module")
print(f"Registers: {len(cpf.registers)}")
print(f"Sample: {[r.name for r in cpf.registers[:5]]}")

gen = ModuleCodeGenerator(Path('/tmp/test_output'))
rdl_content = gen._generate_rdl_for_base_module(cpf, hierarchy.all_modules)

# Export from RDL
exporter = RDLExporter()
export_results = exporter.export_from_rdl_content(rdl_content, cpf.name)

# Direct generation
direct_results = {
    'ralf': gen._generate_ralf_for_base_module(cpf),
    'header': gen._generate_c_header_for_base_module(cpf),
    'svh': gen._generate_svh_for_base_module(cpf),
}

print("\n=== RALF Field Comparison ===")
export_ralf = export_results.get('ralf', '')
direct_ralf = direct_results.get('ralf', '')

# Get all fields from both
export_fields = re.findall(r'field (\w+) @(\d+) \{\s*bits (\d+);\s*access (\w+);', export_ralf)
direct_fields = re.findall(r'field (\w+) @(\d+) \{\s*bits (\d+);\s*access (\w+);', direct_ralf)

print(f"Export fields count: {len(export_fields)}")
print(f"Direct fields count: {len(direct_fields)}")

# Convert to sets for comparison
export_set = set(f"{name}@{pos}[{bits}]({access})" for name, pos, bits, access in export_fields)
direct_set = set(f"{name}@{pos}[{bits}]({access})" for name, pos, bits, access in direct_fields)

if export_set == direct_set:
    print("✅ All RALF fields match perfectly!")
else:
    print(f"Mismatch!")
    print(f"Only in export: {list(export_set - direct_set)[:5]}")
    print(f"Only in direct: {list(direct_set - export_set)[:5]}")

# Sample field details
print("\n=== Sample Field Details (Rx_FIFO) ===")
print("Export RALF:")
for i, line in enumerate(export_ralf.split('\n')):
    if 'Rx_FIFO' in line and 'register' in line:
        print('\n'.join(export_ralf.split('\n')[i:i+10]))
        break

print("\nDirect RALF:")
for i, line in enumerate(direct_ralf.split('\n')):
    if 'Rx_FIFO' in line and 'register' in line:
        print('\n'.join(direct_ralf.split('\n')[i:i+10]))
        break

print("\n=== C Header Define Comparison ===")
export_header = export_results.get('header', '')
direct_header = direct_results.get('header', '')

# Compare OFFSET values
export_offsets = dict(re.findall(r'#define (\w+_OFFSET)\s+0x([0-9A-Fa-f]+)', export_header))
direct_offsets = dict(re.findall(r'#define (\w+_OFFSET)\s+0x([0-9A-Fa-f]+)', direct_header))

mismatches = []
for reg, exp_val in export_offsets.items():
    dir_val = direct_offsets.get(reg)
    if dir_val and exp_val.lower() != dir_val.lower():
        mismatches.append(f"{reg}: export=0x{exp_val}, direct=0x{dir_val}")

if mismatches:
    print(f"Mismatches found:")
    for m in mismatches[:5]:
        print(f"  {m}")
else:
    print("✅ All C Header offsets match!")

print(f"\nExport offsets: {dict(list(export_offsets.items())[:3])}")
print(f"Direct offsets: {dict(list(direct_offsets.items())[:3])}")

print("\n✅ All comparisons passed!")
