"""
RTL File Detection Test Suite

Tests RTL generation, file detection, and download functionality for both
single-module and multi-module scenarios.

Covers:
1. Single module RTL generation (register type only)
2. File detection in subdirectory structure (rtl/{module}/)
3. File listing API returning correct paths
4. Download functionality for single files and module bundles
"""
import requests
import time
import os
import sys
import shutil
from pathlib import Path

BASE = 'http://localhost:8000'


def create_test_excel(file_path: str, module_name: str, registers=None):
    """Create a test Excel file with register definitions"""
    try:
        import openpyxl
    except ImportError:
        print("❌ openpyxl not installed, trying to install...")
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
        import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = module_name

    # Header row
    ws.append(["Reg_Name", "Offset", "Width", "Field_Name", "MSB", "LSB", "Access", "Reset", "Description"])

    # Default registers if none provided
    if registers is None:
        registers = [
            ("CTRL", "0x0", "32", "CTRL", "31", "0", "RW", "0x0", "Control register"),
            ("STATUS", "0x4", "32", "STATUS", "31", "0", "RO", "0x0", "Status register"),
        ]

    for reg in registers:
        ws.append(reg)

    wb.save(file_path)
    return file_path


def test_single_module_rtl_generation():
    """Test 1: Single module (register type only) RTL generation"""
    print("\n" + "="*60)
    print("Test 1: Single Module RTL Generation")
    print("="*60)

    # Create version
    test_name = f'rtl_test_single_{int(time.time())}'
    r = requests.post(f'{BASE}/api/v1/versions', json={
        'name': test_name,
        'description': 'Test single module RTL'
    })
    assert r.status_code == 200, f"Failed to create version: {r.text}"
    version = r.json()
    vid = version['id']
    print(f"✅ Version created: ID={vid}")

    # Create and upload test file
    test_file = f'/tmp/{test_name}_uart.xlsx'
    create_test_excel(test_file, 'UART', [
        ("TX_DATA", "0x0", "32", "TX_DATA", "31", "0", "WO", "0x0", "Transmit data"),
        ("RX_DATA", "0x4", "32", "RX_DATA", "31", "0", "RO", "0x0", "Receive data"),
        ("BAUD", "0x8", "32", "BAUD", "31", "0", "RW", "0x2580", "Baud rate"),
    ])

    with open(test_file, 'rb') as f:
        r = requests.post(
            f'{BASE}/api/v1/versions/{vid}/upload/batch',
            files={'files': f}
        )
    assert r.status_code == 200, f"Upload failed: {r.text}"
    print(f"✅ File uploaded")

    # Wait for generation
    time.sleep(1)

    # Step 1: Check file listing - module should be detected
    r = requests.get(f'{BASE}/api/v1/versions/{vid}/files')
    assert r.status_code == 200, f"Failed to get files: {r.text}"
    files = r.json()

    assert 'UART' in files.get('modules', {}), "UART should be in modules"
    print(f"✅ UART module detected in file listing")

    # Step 2: Check RTL options
    r = requests.get(f'{BASE}/api/v1/versions/{vid}/rtl/options')
    assert r.status_code == 200, f"Failed to get RTL options: {r.text}"
    options = r.json()

    module_names = [m['name'] for m in options.get('modules', [])]
    assert 'UART' in module_names, f"UART should be in RTL options, got: {module_names}"
    print(f"✅ UART available for RTL generation")

    # Step 3: Generate RTL
    print(f"\n  Generating RTL for UART...")
    r = requests.post(
        f'{BASE}/api/v1/versions/{vid}/rtl/generate',
        json={
            'module': 'UART',
            'cpu_if': 'axilite',
            'address_width': 32,
            'reset_type': 'active_low'
        }
    )
    assert r.status_code == 200, f"RTL generation failed: {r.text}"
    result = r.json()
    assert result['success'], f"RTL generation not successful: {result}"
    assert len(result['files']) >= 2, f"Expected at least 2 files, got: {len(result['files'])}"
    print(f"✅ RTL generated: {len(result['files'])} files")
    for f in result['files']:
        print(f"   - {f['filename']} ({f['size']} bytes)")

    # Step 4: Check file detection API
    print(f"\n  Checking file detection API...")
    r = requests.get(f'{BASE}/api/v1/versions/{vid}/rtl/files?module=UART')
    assert r.status_code == 200, f"Failed to get RTL files: {r.text}"
    rtl_files = r.json()

    assert rtl_files['generated'] is True, "Should detect generated files"
    assert rtl_files['file_count'] >= 2, f"Expected at least 2 files, got: {rtl_files['file_count']}"
    print(f"✅ File detection correct: {rtl_files['file_count']} files detected")

    # Verify file paths contain subdirectory
    for f in rtl_files['files']:
        assert 'UART/' in f['relative_path'] or f['relative_path'].startswith('UART'), \
            f"File path should contain module subdirectory: {f['relative_path']}"
    print(f"✅ File paths correct (contain module subdirectory)")

    # Step 5: Test single file download
    print(f"\n  Testing single file download...")
    sv_file = [f for f in rtl_files['files'] if f['filename'].endswith('.sv') and not f['filename'].endswith('_pkg.sv')]
    if sv_file:
        r = requests.get(
            f'{BASE}/api/v1/versions/{vid}/rtl/download',
            params={'module': 'UART', 'file': sv_file[0]['filename']}
        )
        assert r.status_code == 200, f"Single file download failed: {r.status_code}"
        assert len(r.content) > 0, "Downloaded file should not be empty"
        print(f"✅ Single file download successful: {sv_file[0]['filename']}")

    # Step 6: Test module bundle download
    print(f"\n  Testing module bundle download...")
    r = requests.get(
        f'{BASE}/api/v1/versions/{vid}/rtl/download',
        params={'module': 'UART'}
    )
    assert r.status_code == 200, f"Bundle download failed: {r.status_code}"
    assert len(r.content) > 0, "Downloaded bundle should not be empty"
    print(f"✅ Module bundle download successful")

    # Cleanup
    if os.path.exists(test_file):
        os.remove(test_file)

    print(f"\n{'='*60}")
    print("✅ Test 1 PASSED - Single module RTL generation works")
    print(f"{'='*60}")
    return True


def test_file_detection_before_and_after_generation():
    """Test 2: Verify file detection works correctly (upload now auto-generates RTL)"""
    print("\n" + "="*60)
    print("Test 2: File Detection with Auto-Generation")
    print("="*60)

    # Create version
    test_name = f'rtl_test_state_{int(time.time())}'
    r = requests.post(f'{BASE}/api/v1/versions', json={
        'name': test_name,
        'description': 'Test RTL state detection'
    })
    assert r.status_code == 200
    vid = r.json()['id']
    print(f"✅ Version created: ID={vid}")

    # Upload file (RTL is now auto-generated after upload)
    test_file = f'/tmp/{test_name}_spi.xlsx'
    create_test_excel(test_file, 'SPI')

    with open(test_file, 'rb') as f:
        r = requests.post(
            f'{BASE}/api/v1/versions/{vid}/upload/batch',
            files={'files': f}
        )
    assert r.status_code == 200
    time.sleep(2)  # Wait for async generation
    print(f"✅ File uploaded (RTL auto-generated)")

    # After upload - RTL should be auto-generated
    r = requests.get(f'{BASE}/api/v1/versions/{vid}/rtl/files?module=SPI')
    assert r.status_code == 200
    state_after = r.json()
    assert state_after['generated'] is True, "RTL should be auto-generated after upload"
    # Note: At least 1 file (may be just .sv or include _pkg.sv)
    assert state_after['file_count'] >= 1, f"Should have at least 1 file, got: {state_after['file_count']}"
    print(f"✅ After upload: generated={state_after['generated']}, files={state_after['file_count']}")

    # Cleanup
    if os.path.exists(test_file):
        os.remove(test_file)

    print(f"\n{'='*60}")
    print("✅ Test 2 PASSED - RTL auto-generated after upload")
    print(f"{'='*60}")
    return True


def test_multiple_modules_independent():
    """Test 3: Multiple modules are all auto-generated on upload"""
    print("\n" + "="*60)
    print("Test 3: Multiple Modules Auto-Generation")
    print("="*60)

    # Create version
    test_name = f'rtl_test_multi_{int(time.time())}'
    r = requests.post(f'{BASE}/api/v1/versions', json={
        'name': test_name,
        'description': 'Test multi-module RTL'
    })
    assert r.status_code == 200
    vid = r.json()['id']
    print(f"✅ Version created: ID={vid}")

    # Create and upload multiple files
    test_files = []
    modules = ['I2C', 'PWM']

    for module in modules:
        test_file = f'/tmp/{test_name}_{module}.xlsx'
        create_test_excel(test_file, module)
        test_files.append(test_file)

    # Upload all files (RTL auto-generated for all modules)
    files_to_upload = [('files', open(f, 'rb')) for f in test_files]
    r = requests.post(
        f'{BASE}/api/v1/versions/{vid}/upload/batch',
        files=files_to_upload
    )
    for _, f in files_to_upload:
        f.close()

    assert r.status_code == 200
    time.sleep(2)  # Wait for async generation
    print(f"✅ Files uploaded for modules: {modules} (RTL auto-generated)")

    # Check state - Both modules should be auto-generated
    r = requests.get(f'{BASE}/api/v1/versions/{vid}/rtl/files?module=I2C')
    i2c_state = r.json()
    assert i2c_state['generated'] is True, "I2C should be auto-generated"
    print(f"✅ I2C state: generated={i2c_state['generated']}, files={i2c_state['file_count']}")

    r = requests.get(f'{BASE}/api/v1/versions/{vid}/rtl/files?module=PWM')
    pwm_state = r.json()
    assert pwm_state['generated'] is True, "PWM should also be auto-generated"
    print(f"✅ PWM state: generated={pwm_state['generated']}, files={pwm_state['file_count']}")

    # Cleanup
    for f in test_files:
        if os.path.exists(f):
            os.remove(f)

    # Cleanup
    for f in test_files:
        if os.path.exists(f):
            os.remove(f)

    print(f"\n{'='*60}")
    print("✅ Test 3 PASSED - All modules auto-generated on upload")
    print(f"{'='*60}")
    return True


def main():
    """Run all tests"""
    print("\n" + "="*60)
    print("RTL File Detection Test Suite")
    print("="*60)

    tests = [
        ("Single Module RTL Generation", test_single_module_rtl_generation),
        ("File Detection State Changes", test_file_detection_before_and_after_generation),
        ("Multiple Modules Independent", test_multiple_modules_independent),
    ]

    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"\n❌ Test '{name}' failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)

    passed = sum(1 for _, success in results if success)
    failed = len(results) - passed

    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"  {status} - {name}")

    print(f"\nTotal: {len(results)} tests")
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")

    if failed == 0:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n❌ {failed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
