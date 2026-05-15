#!/usr/bin/env python3
"""
Test batch upload through API
"""
import sys
import os
sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.models import register, version
from app.db.base import SessionLocal
from app.models.version import Version
from app.core.config import settings

def test_batch_upload():
    db = SessionLocal()
    try:
        # Create test version
        version_name = "batch_test_v1"
        existing = db.query(Version).filter(Version.name == version_name).first()
        if existing:
            db.delete(existing)
            db.commit()

        version = Version(name=version_name, description="Batch upload test")
        db.add(version)
        db.commit()
        db.refresh(version)
        print(f"Created version: {version_name} (ID: {version.id})")

        # Import here after models are loaded
        from app.services.cumulative_hierarchy_service import CumulativeHierarchyService
        from app.services.peakrdl_html_service import PeakRDLHTMLService
        from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator

        cumulative_service = CumulativeHierarchyService(db)

        # Upload all files at once
        excel_files = [
            '/home/xiaoer/register/addr_map_S/soc_addr_map.xls',
            '/home/xiaoer/register/addr_map_S/GCS.xls',
            '/home/xiaoer/register/addr_map_S/C2C.xls',
            '/home/xiaoer/register/addr_map_S/DRAM_IF.xls',
            '/home/xiaoer/register/addr_map_S/PE.xls',
            '/home/xiaoer/register/addr_map_S/PEC.xls',
        ]

        print(f"\nUploading {len(excel_files)} files...")
        result = cumulative_service.process_upload(version.id, excel_files)

        if not result['success']:
            print(f"FAILED: {result.get('errors', [])}")
            return False

        print(f"Success! Total modules: {len(result['hierarchy'].all_modules)}")
        print(f"Top modules: {[m.name for m in result['hierarchy'].top_modules]}")
        print(f"Top addrmap: {result['hierarchy'].top_addrmap_name}")

        # Check uninstantiated
        uninstantiated = result['uninstantiated']
        print(f"\nUninstantiated modules: {len(uninstantiated)}")
        for um in uninstantiated:
            print(f"  - {um.name}: {um.reason}")

        # Generate RDL
        print("\nGenerating RDL...")
        rdl_gen = PeakRDLCompatibleRDLGenerator()
        rdl = rdl_gen.generate(result['hierarchy'])

        import re
        addrmaps = re.findall(r'addrmap\s+(\w+)\s*\{', rdl)
        from collections import Counter
        counts = Counter(addrmaps)
        duplicates = {k: v for k, v in counts.items() if v > 1}

        if duplicates:
            print(f"ERROR: Duplicate definitions: {duplicates}")
            return False
        print(f"RDL generated: {len(rdl)} bytes, {len(addrmaps)} addrmaps, no duplicates")

        # Save RDL
        rdl_path = settings.RDL_OUTPUT_DIR / version_name / f"{result['hierarchy'].top_addrmap_name or 'top'}.rdl"
        rdl_path.parent.mkdir(parents=True, exist_ok=True)
        rdl_path.write_text(rdl, encoding='utf-8')
        print(f"RDL saved to: {rdl_path}")

        # Generate HTML
        print("\nGenerating HTML...")
        html_service = PeakRDLHTMLService()
        html_result = html_service.generate_html(result['hierarchy'], version_id=version.id)

        if html_result['success']:
            print(f"HTML generated: {html_result['html_url']}")
            version.html_path = html_result.get('html_path')
            db.commit()
        else:
            print(f"HTML failed: {html_result.get('errors', [])}")
            return False

        # Final summary
        print(f"\n{'='*60}")
        print("SUCCESS!")
        print(f"{'='*60}")
        print(f"Version: {version_name}")
        print(f"Total modules: {len(result['hierarchy'].all_modules)}")
        print(f"Top addrmap: {result['hierarchy'].top_addrmap_name}")
        print(f"Uninstantiated: {len(uninstantiated)}")
        print(f"HTML: {version.html_path}")
        print(f"RDL: {rdl_path}")

        return len(uninstantiated) == 0

    finally:
        db.close()

if __name__ == '__main__':
    success = test_batch_upload()
    sys.exit(0 if success else 1)
