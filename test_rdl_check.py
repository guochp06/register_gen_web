#!/usr/bin/env python3
"""
Check RDL generation for PEC hierarchy
"""
import os
import sys
sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.db.base import SessionLocal
from app.services.cumulative_hierarchy_service import CumulativeHierarchyService
from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
from app.models.version import Version

def check_rdl():
    db = SessionLocal()
    try:
        version = db.query(Version).filter(Version.name == "test_complete_v1").first()
        if not version:
            print("Version not found")
            return

        cumulative_service = CumulativeHierarchyService(db)
        hierarchy = cumulative_service._rebuild_hierarchy_from_db(version.id, version.name)

        print(f"Top modules: {[m.name for m in hierarchy.top_modules]}")
        print(f"Top addrmap name: {hierarchy.top_addrmap_name}")
        print(f"All modules: {list(hierarchy.all_modules.keys())}")

        # Generate RDL
        generator = PeakRDLCompatibleRDLGenerator()
        rdl = generator.generate(hierarchy)

        # Check what addrmaps are defined
        import re
        addrmaps = re.findall(r'addrmap\s+(\w+)\s*\{', rdl)
        print(f"\nDefined addrmaps: {addrmaps}")

        # Find which one is the top
        print(f"\nLooking for top_name in RDL...")

        # Check if PEC is defined
        if 'PEC' in rdl:
            print("\nPEC found in RDL")
            # Show first few lines of PEC definition
            match = re.search(r'addrmap\s+PEC\s*\{.*?\};', rdl, re.DOTALL)
            if match:
                print("PEC definition snippet:")
                print(match.group()[:500])
        else:
            print("\nPEC NOT found in RDL!")

        # Check DRAM_IF
        if 'DRAM_IF' in rdl:
            print("\nDRAM_IF found in RDL")
            match = re.search(r'addrmap\s+DRAM_IF\s*\{.*?\};', rdl, re.DOTALL)
            if match:
                print("DRAM_IF definition snippet:")
                print(match.group()[:500])

        # Save RDL for inspection
        output_path = "/tmp/test_output.rdl"
        with open(output_path, 'w') as f:
            f.write(rdl)
        print(f"\nFull RDL saved to: {output_path}")

    finally:
        db.close()

if __name__ == '__main__':
    check_rdl()
