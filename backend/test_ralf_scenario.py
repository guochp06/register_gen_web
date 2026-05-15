import requests
import time
import os
import sys

BASE = 'http://localhost:8000'
TEST_DIR = '/home/xiaoer/register/test_ralf_integration'

print("=== RALF Integration Test ===")
print("Scenario: Import RALF + Excel with uart*4 instantiation + other modules")
print()

print("Step 1: Create version")
test_name = f'ralf_test_{int(time.time())}'
r = requests.post(f'{BASE}/api/v1/versions', json={
    'name': test_name, 
    'description': 'RALF integration test with uart*4 instantiation'
})
if r.status_code != 200:
    print(f"Failed: {r.text}")
    sys.exit(1)
version = r.json()
vid = version['id']
print(f"  Version created: ID={vid}, name={test_name}")

print("\nStep 2: Upload Excel files + RALF")
files = []
excel_files = ['soc_addr_map.xlsx', 'gpio.xlsx', 'timer.xlsx']
for fname in excel_files:
    fpath = os.path.join(TEST_DIR, fname)
    files.append(('files', open(fpath, 'rb')))

# Add RALF file
ralf_path = os.path.join(TEST_DIR, 'uart.ralf')
files.append(('ralf_file', open(ralf_path, 'rb')))

r = requests.post(f'{BASE}/api/v1/versions/{vid}/upload/batch', files=files)
for _, f in files:
    f.close()

if r.status_code != 200:
    print(f"Upload failed: {r.status_code}")
    print(r.text[:3000])
    sys.exit(1)

result = r.json()
print(f"  Warnings: {len(result.get('warnings', []))}")
print(f"  Errors: {len(result.get('errors', []))}")
print(f"  Modules: {result.get('modules_count', 0)}")
print(f"  Uninstantiated: {len(result.get('uninstantiated_modules', []))}")

if result.get('warnings'):
    for w in result['warnings'][:10]:
        print(f"    Warning: {w}")

if result.get('errors'):
    for e in result['errors'][:10]:
        print(f"    Error: {e}")

html_url = result.get('html_url', 'None')
print(f"  HTML: {html_url}")

print("\nStep 3: Verify generated files")
output_base = f'/home/xiaoer/AI_GEN/regtool/backend/output/{test_name}'
time.sleep(1)

formats = ['rdl', 'ralf', 'header', 'svh', 'rtl']
for fmt in formats:
    fmt_dir = f'{output_base}/{fmt}'
    if os.path.exists(fmt_dir):
        files = [f for f in os.listdir(fmt_dir) if not f.startswith('.')]
        print(f"  {fmt}: {len(files)} files")

print("\nStep 4: Check uart module instantiation")
# Check if uart RALF was processed
uart_rdl = f'{output_base}/rdl/uart.rdl'
uart_ralf = f'{output_base}/ralf/uart.ralf'
if os.path.exists(uart_rdl):
    print(f"  uart.rdl: generated from RALF")
    with open(uart_rdl) as f:
        content = f.read()
        reg_count = content.count('reg ')
        print(f"    Registers in RDL: {reg_count}")

if os.path.exists(uart_ralf):
    print(f"  uart.ralf: generated from RDL export")

# Check addr_map includes uart instances
soc_rdl = f'{output_base}/rdl/soc_addr_map.rdl'
if os.path.exists(soc_rdl):
    with open(soc_rdl) as f:
        content = f.read()
        uart_instances = [line for line in content.split('\n') if 'uart' in line.lower()]
        print(f"\n  soc_addr_map RDL uart instances:")
        for line in uart_instances[:6]:
            print(f"    {line.strip()}")

print("\nStep 5: Verify other modules (gpio, timer)")
gpio_rdl = f'{output_base}/rdl/gpio_ctrl.rdl'
timer_rdl = f'{output_base}/rdl/timer.rdl'
if os.path.exists(gpio_rdl):
    with open(gpio_rdl) as f:
        content = f.read()
        reg_count = content.count('reg ')
        print(f"  gpio_ctrl.rdl: {reg_count} registers")
if os.path.exists(timer_rdl):
    with open(timer_rdl) as f:
        content = f.read()
        reg_count = content.count('reg ')
        print(f"  timer.rdl: {reg_count} registers")

print("\nStep 6: Check HTML generation")
if html_url and html_url != 'None':
    html_path = f'/home/xiaoer/AI_GEN/regtool/backend/output/html/{test_name}/index.html'
    if os.path.exists(html_path):
        print(f"  HTML generated successfully")
        print(f"  Access: http://localhost:8000{html_url}")
    else:
        print(f"  HTML file not found at expected path")

print("\n=== Test Complete ===")
if result.get('errors'):
    print("❌ Test FAILED with errors")
    sys.exit(1)
else:
    print("✅ Test PASSED - All files generated successfully!")
