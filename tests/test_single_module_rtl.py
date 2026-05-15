"""
Test Case: Single Module RTL Generation

Scenario: Upload a single Excel file with only register type sheet (e.g., dramif)
Expected:
1. Module file should be categorized as 'modules', not 'combined'
2. RTL options should include the module
3. RTL generation should work for the single module
"""
import requests
import time
import os
import sys

BASE = 'http://localhost:8000'

def test_single_module_rtl():
    """Test single module (register type only) RTL generation"""

    # Create test version
    test_name = f'single_module_{int(time.time())}'
    print(f"\n=== Test: Single Module RTL Generation ===")
    print(f"Test version: {test_name}")

    # Step 1: Create version
    print("\nStep 1: Creating version...")
    r = requests.post(f'{BASE}/api/v1/versions', json={
        'name': test_name,
        'description': 'Test single module RTL generation'
    })
    if r.status_code != 200:
        print(f"❌ Failed to create version: {r.text}")
        return False

    version = r.json()
    vid = version['id']
    print(f"✅ Version created: ID={vid}")

    # Step 2: Create a simple test Excel file (simulate dramif)
    print("\nStep 2: Creating test Excel file...")
    import openpyxl
    from openpyxl import Workbook

    wb = Workbook()

    # Create dramif register sheet
    ws = wb.active
    ws.title = "dramif"
    ws.append(["Reg_Name", "Offset", "Width", "Field_Name", "MSB", "LSB", "Access", "Reset", "Description"])
    ws.append(["CTRL", "0x0", "32", "CTRL", "31", "0", "RW", "0x0", "Control register"])
    ws.append(["STATUS", "0x4", "32", "STATUS", "31", "0", "RO", "0x0", "Status register"])

    test_file = f'/tmp/{test_name}_dramif.xlsx'
    wb.save(test_file)
    print(f"✅ Test file created: {test_file}")

    # Step 3: Upload Excel file
    print("\nStep 3: Uploading Excel file...")
    with open(test_file, 'rb') as f:
        r = requests.post(
            f'{BASE}/api/v1/versions/{vid}/upload/batch',
            files={'files': f}
        )

    if r.status_code != 200:
        print(f"❌ Upload failed: {r.text}")
        return False

    result = r.json()
    print(f"✅ Upload successful")
    print(f"   Modules: {result.get('modules_count', 0)}")
    print(f"   Top addrmap: {result.get('top_addrmap_name', 'None')}")

    # Step 4: Check file listing
    print("\nStep 4: Checking file listing...")
    time.sleep(1)  # Wait for file generation
    r = requests.get(f'{BASE}/api/v1/versions/{vid}/files')
    if r.status_code != 200:
        print(f"❌ Failed to get file listing: {r.text}")
        return False

    files = r.json()
    print(f"   Combined files: {list(files.get('combined', {}).keys())}")
    print(f"   Modules: {list(files.get('modules', {}).keys())}")

    # Critical check: dramif should be in modules, not combined
    if 'dramif' in files.get('modules', {}):
        print("✅ dramif correctly categorized as module")
    elif 'dramif' in files.get('combined', {}):
        print("❌ dramif incorrectly categorized as combined (should be module)")
        return False
    else:
        print("❌ dramif not found in files")
        return False

    # Step 5: Check RTL options
    print("\nStep 5: Checking RTL options...")
    r = requests.get(f'{BASE}/api/v1/versions/{vid}/rtl/options')
    if r.status_code != 200:
        print(f"❌ Failed to get RTL options: {r.text}")
        return False

    rtl_options = r.json()
    module_names = [m['name'] for m in rtl_options.get('modules', [])]
    print(f"   Available modules for RTL: {module_names}")

    if 'dramif' in module_names:
        print("✅ dramif available for RTL generation")
    else:
        print("❌ dramif not available for RTL generation")
        return False

    # Step 6: Test RTL generation
    print("\nStep 6: Testing RTL generation...")
    r = requests.post(
        f'{BASE}/api/v1/versions/{vid}/rtl/generate',
        json={
            'module': 'dramif',
            'cpu_if': 'axilite',
            'address_width': 32,
            'reset_type': 'active_low'
        }
    )

    if r.status_code != 200:
        print(f"❌ RTL generation failed: {r.text}")
        return False

    rtl_result = r.json()
    if rtl_result.get('success'):
        print(f"✅ RTL generation successful")
        print(f"   Files: {[f['filename'] for f in rtl_result.get('files', [])]}")
    else:
        print(f"❌ RTL generation failed: {rtl_result.get('message')}")
        return False

    # Step 7: Verify top_addrmap_name in database
    print("\nStep 7: Verifying top_addrmap_name saved correctly...")
    r = requests.get(f'{BASE}/api/v1/versions/{vid}')
    if r.status_code == 200:
        version_info = r.json()
        top_name = version_info.get('top_addrmap_name')
        print(f"   top_addrmap_name: {top_name}")
        if top_name == 'dramif':
            print("✅ top_addrmap_name correctly set to dramif")
        else:
            print(f"⚠️  top_addrmap_name is '{top_name}' (expected 'dramif')")

    print("\n" + "="*60)
    print("✅ All tests passed!")
    print("="*60)

    # Cleanup
    if os.path.exists(test_file):
        os.remove(test_file)

    return True


if __name__ == "__main__":
    try:
        success = test_single_module_rtl()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
