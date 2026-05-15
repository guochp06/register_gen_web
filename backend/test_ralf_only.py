import requests
import time
import os
import sys

BASE = 'http://localhost:8000'
TEST_DIR = '/home/xiaoer/register/test_ralf_integration'

print("=== RALF Only Test (without problematic gpio.xlsx) ===")

print("Step 1: Create version")
test_name = f'ralf_only_{int(time.time())}'
r = requests.post(f'{BASE}/api/v1/versions', json={
    'name': test_name, 
    'description': 'RALF test with uart only'
})
version = r.json()
vid = version['id']
print(f"  Version: ID={vid}")

print("\nStep 2: Upload only soc_addr_map + RALF (no gpio/timer)")
files = []
files.append(('files', open(f'{TEST_DIR}/soc_addr_map.xlsx', 'rb')))
files.append(('ralf_file', open(f'{TEST_DIR}/uart.ralf', 'rb')))

r = requests.post(f'{BASE}/api/v1/versions/{vid}/upload/batch', files=files)
for _, f in files:
    f.close()

if r.status_code != 200:
    print(f"Upload failed: {r.status_code}")
    print(r.text[:2000])
    sys.exit(1)

result = r.json()
print(f"  Warnings: {len(result.get('warnings', []))}")
print(f"  Errors: {len(result.get('errors', []))}")
print(f"  Modules: {result.get('modules_count', 0)}")

if result.get('errors'):
    for e in result['errors'][:5]:
        print(f"    Error: {e}")

print(f"  HTML: {result.get('html_url', 'None')}")

if not result.get('errors'):
    print("\n✅ Test PASSED!")
else:
    print("\n❌ Test FAILED")
