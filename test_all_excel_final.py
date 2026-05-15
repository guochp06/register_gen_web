#!/usr/bin/env python3
"""
Test script to verify loading all Excel files works correctly
with HTML regeneration and no false uninstantiated modules
"""
import os
import sys
import shutil

# Add backend to path
sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

# Import models first to ensure SQLAlchemy relationships are set up
from app.models import register, version
from app.db.base import SessionLocal
from app.services.cumulative_hierarchy_service import CumulativeHierarchyService
from app.services.hierarchy_parser import HierarchyParser
from app.services.peakrdl_html_service import PeakRDLHTMLService
from app.services.module_code_generator import ModuleCodeGenerator
from app.models.version import Version
from app.core.config import settings

def test_full_upload():
    """Test loading all Excel files and generating complete output"""

    # Excel files to upload (in order - soc_addr_map first)
    excel_files = [
        '/home/xiaoer/register/addr_map_S/soc_addr_map.xls',
        '/home/xiaoer/register/addr_map_S/GCS.xls',
        '/home/xiaoer/register/addr_map_S/C2C.xls',
        '/home/xiaoer/register/addr_map_S/DRAM_IF.xls',
        '/home/xiaoer/register/addr_map_S/PE.xls',
        '/home/xiaoer/register/addr_map_S/PEC.xls',
    ]

    # Verify all files exist
    for f in excel_files:
        if not os.path.exists(f):
            print(f"ERROR: File not found: {f}")
            return False

    db = SessionLocal()
    try:
        # Create a test version
        version_name = "test_complete_v1"

        # Delete existing version with same name
        existing = db.query(Version).filter(Version.name == version_name).first()
        if existing:
            print(f"Deleting existing version: {version_name}")
            # Clean up files
            version_dir = settings.OUTPUT_DIR / version_name
            if version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
            html_dir = settings.HTML_OUTPUT_DIR / version_name
            if html_dir.exists():
                shutil.rmtree(html_dir, ignore_errors=True)
            rdl_dir = settings.RDL_OUTPUT_DIR / version_name
            if rdl_dir.exists():
                shutil.rmtree(rdl_dir, ignore_errors=True)

            db.delete(existing)
            db.commit()

        version = Version(name=version_name, description="Test complete upload")
        db.add(version)
        db.commit()
        db.refresh(version)

        print(f"Created version: {version_name} (ID: {version.id})")

        cumulative_service = CumulativeHierarchyService(db)

        # Upload files one by one
        all_warnings = []
        for i, file_path in enumerate(excel_files):
            print(f"\n{'='*60}")
            print(f"Upload {i+1}/{len(excel_files)}: {os.path.basename(file_path)}")
            print('='*60)

            result = cumulative_service.process_upload(version.id, [file_path])

            if not result['success']:
                print(f"FAILED: {result.get('errors', [])}")
                return False

            # Show warnings
            for w in result.get('warnings', []):
                if 'uninstantiated' not in w.lower():
                    print(f"  Warning: {w}")
                    all_warnings.append(w)

            # Show uninstantiated modules count
            uninstantiated = result.get('uninstantiated', [])
            if uninstantiated:
                print(f"  Uninstantiated: {len(uninstantiated)} modules")
                for um in uninstantiated[:5]:  # Show first 5
                    print(f"    - {um.name}: {um.reason}")
                if len(uninstantiated) > 5:
                    print(f"    ... and {len(uninstantiated) - 5} more")

            # Show stats
            hierarchy = result['hierarchy']
            print(f"  Total modules: {len(hierarchy.all_modules)}")
            print(f"  Top modules: {[m.name for m in hierarchy.top_modules]}")
            print(f"  Top addrmap: {hierarchy.top_addrmap_name}")

        # Final check: rebuild hierarchy from DB and verify
        print(f"\n{'='*60}")
        print("Final Verification")
        print('='*60)

        final_hierarchy = cumulative_service._rebuild_hierarchy_from_db(version.id, version_name)

        # Check uninstantiated modules
        uninstantiated = cumulative_service.get_uninstantiated_modules(version.id)
        print(f"\nFinal uninstantiated modules: {len(uninstantiated)}")
        if uninstantiated:
            print("Modules:")
            for um in uninstantiated:
                print(f"  - {um['name']}: {um['reason']}")

        # Generate HTML with full hierarchy
        print(f"\nGenerating HTML with {len(final_hierarchy.all_modules)} modules...")
        html_service = PeakRDLHTMLService()
        html_result = html_service.generate_html(final_hierarchy, version_id=version.id)

        if html_result['success']:
            print(f"HTML generated: {html_result['html_url']}")
            # Save to version
            version.html_path = html_result.get('html_path')
            db.commit()
        else:
            print(f"HTML generation failed: {html_result.get('errors', [])}")

        # Generate code files
        print("\nGenerating code files...")
        generator = ModuleCodeGenerator(settings.OUTPUT_DIR)
        try:
            generated = generator.generate_all(final_hierarchy, version.id, version_name)
            saved = generator.save_all(generated, version.id, version_name)
            print(f"Generated files: {len(saved)}")
        except Exception as e:
            print(f"Code generation error: {e}")

        # Final summary
        print(f"\n{'='*60}")
        print("Final Summary")
        print('='*60)
        print(f"Version: {version_name}")
        print(f"Total modules: {len(final_hierarchy.all_modules)}")
        print(f"Top modules: {[m.name for m in final_hierarchy.top_modules]}")
        print(f"Top addrmap: {final_hierarchy.top_addrmap_name}")
        print(f"Uninstantiated: {len(uninstantiated)}")
        print(f"HTML path: {version.html_path}")
        print(f"Warnings: {len(all_warnings)}")

        if len(uninstantiated) == 0 and html_result['success']:
            print("\n✅ SUCCESS: All modules instantiated and HTML generated!")
            return True
        else:
            print(f"\n⚠️  ISSUES: {len(uninstantiated)} uninstantiated modules")
            return False

    finally:
        db.close()

if __name__ == '__main__':
    success = test_full_upload()
    sys.exit(0 if success else 1)
