#!/usr/bin/env python3
"""
Test to verify RDL generation fixes - no HTML generation
"""
import sys
sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.models import register, version
from app.db.base import SessionLocal
from app.services.cumulative_hierarchy_service import CumulativeHierarchyService
from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
from app.models.version import Version
import re

def test_rdl():
    db = SessionLocal()
    try:
        version = db.query(Version).filter(Version.name == "test_complete_v1").first()
        if not version:
            print("ERROR: Version not found")
            return False

        print("Rebuilding hierarchy from DB...")
        cumulative_service = CumulativeHierarchyService(db)
        hierarchy = cumulative_service._rebuild_hierarchy_from_db(version.id, version.name)

        print(f"Top modules: {[m.name for m in hierarchy.top_modules]}")
        print(f"Top addrmap name: {hierarchy.top_addrmap_name}")
        print(f"Total modules: {len(hierarchy.all_modules)}")

        # Check uninstantiated
        uninstantiated = cumulative_service.get_uninstantiated_modules(version.id)
        print(f"Uninstantiated modules: {len(uninstantiated)}")
        if uninstantiated:
            for um in uninstantiated[:5]:
                print(f"  - {um['name']}: {um['reason']}")

        # Check PEC in top modules
        pec_in_top = any(m.name == 'PEC' for m in hierarchy.top_modules)
        single_top = len(hierarchy.top_modules) == 1
        print(f"PEC in top_modules: {pec_in_top}")
        print(f"Single top module: {single_top}")
        print(f"Skip wrapper: {single_top and pec_in_top}")

        # Generate RDL
        print("\nGenerating RDL...")
        generator = PeakRDLCompatibleRDLGenerator()
        rdl = generator.generate(hierarchy)

        # Check for duplicate definitions
        addrmaps = re.findall(r'addrmap\s+(\w+)\s*\{', rdl)
        print(f"\nTotal addrmap definitions: {len(addrmaps)}")

        # Count duplicates
        from collections import Counter
        counts = Counter(addrmaps)
        duplicates = {k: v for k, v in counts.items() if v > 1}
        if duplicates:
            print(f"ERROR: Duplicate definitions found: {duplicates}")
            return False

        # Check if wrapper is created when there are multiple top modules
        has_wrapper = f"addrmap PEC {{" in rdl and "name = \"PEC\"" in rdl
        print(f"Has PEC definition: {has_wrapper}")

        # Check if PEC is instantiated in a wrapper
        if not single_top:
            # Should have wrapper with instantiations
            wrapper_match = re.search(r'addrmap\s+\w+\s*\{[^}]*?(PEC\s+\w+_inst|PEC_inst)', rdl, re.DOTALL)
            if wrapper_match:
                print("PEC is instantiated in wrapper")
            else:
                print("WARNING: PEC may not be instantiated in wrapper")

        # Save RDL
        output_path = "/tmp/test_rdl_fix.rdl"
        with open(output_path, 'w') as f:
            f.write(rdl)
        print(f"\nRDL saved to: {output_path}")
        print(f"RDL size: {len(rdl)} bytes")

        if len(uninstantiated) == 0 and not duplicates:
            print("\nSUCCESS: No uninstantiated modules and no duplicate definitions!")
            return True
        else:
            print(f"\nFAILED: {len(uninstantiated)} uninstantiated, {len(duplicates)} duplicates")
            return False

    finally:
        db.close()

if __name__ == '__main__':
    success = test_rdl()
    sys.exit(0 if success else 1)
