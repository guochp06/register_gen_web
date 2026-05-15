import requests
import time
import os
import sys

BASE = 'http://localhost:8000'
EXCEL_DIR = '/home/xiaoer/register/addr_map_S'

print("=== Step 1: Create new version ===")
test_name = f'test_{int(time.time())}'
r = requests.post(f'{BASE}/api/v1/versions', json={'name': test_name, 'description': 'Full test'})
if r.status_code != 200:
    print(f"Failed: {r.text}")
    sys.exit(1)
version = r.json()
vid = version['id']
print(f"Version: ID={vid}, name={version['name']}")

print("\n=== Step 2: Upload all 6 Excel files ===")
excel_files = ['soc_addr_map.xls', 'GCS.xls', 'DRAM_IF.xls', 'PE.xls', 'PEC.xls', 'C2C.xls']
files = []
for fname in excel_files:
    fpath = os.path.join(EXCEL_DIR, fname)
    files.append(('files', open(fpath, 'rb')))

r = requests.post(f'{BASE}/api/v1/versions/{vid}/upload/batch', files=files)
for _, f in files:
    f.close()

if r.status_code != 200:
    print(f"Upload failed: {r.status_code}")
    print(r.text[:2000])
    sys.exit(1)

result = r.json()
print(f"Warnings: {len(result.get('warnings', []))}")
print(f"Errors: {len(result.get('errors', []))}")
print(f"Modules: {result.get('modules_count', 0)}")
print(f"Uninstantiated: {len(result.get('uninstantiated_modules', []))}")

if result.get('errors'):
    for e in result['errors'][:10]:
        print(f"  Error: {e}")
    sys.exit(1)

print(f"HTML: {result.get('html_url', 'None')}")

print("\n=== Step 3: Verify RALF/Header/SVH generated from RDL ===")
output_base = f'/home/xiaoer/AI_GEN/regtool/backend/output/{test_name}'

# Check that files are generated
for fmt in ['ralf', 'header', 'svh', 'rdl']:
    fmt_dir = f'{output_base}/{fmt}'
    if os.path.exists(fmt_dir):
        files = [f for f in os.listdir(fmt_dir) if not f.endswith('_pkg.sv')]
        print(f"{fmt}: {len(files)} files")

print("\n=== Step 4: Check consistency between RDL and derived files ===")
# Check CPF module as example
rdl_file = f'{output_base}/rdl/CPF.rdl'
ralf_file = f'{output_base}/ralf/CPF.ralf'
header_file = f'{output_base}/header/CPF.h'
svh_file = f'{output_base}/svh/CPF.svh'

if os.path.exists(rdl_file):
    with open(rdl_file) as f:
        rdl_content = f.read()
    # Check for registers in RDL
    rdl_regs = set()
    for line in rdl_content.split('\n'):
        if 'reg ' in line and '{' in line:
            reg_name = line.split('reg ')[1].split('{')[0].strip()
            rdl_regs.add(reg_name)
    print(f"CPF RDL registers: {rdl_regs}")

if os.path.exists(ralf_file):
    with open(ralf_file) as f:
        ralf_content = f.read()
    ralf_regs = set()
    for line in ralf_content.split('\n'):
        if 'register ' in line and '@' in line:
            reg_name = line.split('register ')[1].split('@')[0].strip()
            ralf_regs.add(reg_name)
    print(f"CPF RALF registers: {ralf_regs}")
    if rdl_regs == ralf_regs:
        print("  -> RDL and RALF match!")
    else:
        print(f"  -> Mismatch: RDL has {rdl_regs - ralf_regs}, RALF has {ralf_regs - rdl_regs}")

print("\n=== Test Complete ===")
