#!/usr/bin/env python3
"""
Simple test to check RDL structure without compilation
"""
import os
import sys
sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

# Import models first to ensure SQLAlchemy relationships are set up
from app.models import register, version
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
        print(f"Number of modules: {len(hierarchy.all_modules)}")

        # Check for PEC in top modules
        pec_in_top = any(m.name == 'PEC' for m in hierarchy.top_modules)
        print(f"PEC in top_modules: {pec_in_top}")

        # Check single_top_module condition
        single_top = len(hierarchy.top_modules) == 1
        print(f"Single top module: {single_top}")
        print(f"Skip wrapper: {single_top and pec_in_top}")

        # Generate RDL
        generator = PeakRDLCompatibleRDLGenerator()
        rdl = generator.generate(hierarchy)

        # Check if there's a wrapper
        has_wrapper = f"addrmap {hierarchy.top_addrmap_name}" in rdl and f"name = \"{hierarchy.top_addrmap_name}\"" in rdl
        print(f"Has wrapper addrmap: {has_wrapper}")

        # Check addrmap definitions
        import re
        addrmaps = re.findall(r'addrmap\s+(\w+)\s*\{', rdl)
        print(f"Number of addrmap definitions: {len(addrmaps)}")

        # Count how many times PEC is defined
        pec_count = rdl.count('addrmap PEC {')
        print(f"PEC definition count: {pec_count}")

        # Check if PEC is instantiated in wrapper
        if 'PEC_inst @' in rdl:
            print("PEC is instantiated in wrapper")

        # Save RDL for inspection
        output_path = "/tmp/test_pec_rdl.rdl"
        with open(output_path, 'w') as f:
            f.write(rdl)
        print(f"RDL saved to: {output_path}")
        print(f"RDL size: {len(rdl)} bytes")

    finally:
        db.close()

if __name__ == '__main__':
    check_rdl()
