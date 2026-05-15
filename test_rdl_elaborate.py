#!/usr/bin/env python3
"""
Check RDL elaboration to find top nodes
"""
import os
import sys
sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.db.base import SessionLocal
from app.services.cumulative_hierarchy_service import CumulativeHierarchyService
from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
from app.models.version import Version
from systemrdl import RDLCompiler

def check_elaboration():
    db = SessionLocal()
    try:
        version = db.query(Version).filter(Version.name == "test_complete_v1").first()
        if not version:
            print("Version not found")
            return

        cumulative_service = CumulativeHierarchyService(db)
        hierarchy = cumulative_service._rebuild_hierarchy_from_db(version.id, version.name)

        print(f"Hierarchy top_addrmap_name: {hierarchy.top_addrmap_name}")
        print(f"Hierarchy top_modules: {[m.name for m in hierarchy.top_modules]}")

        # Generate RDL
        generator = PeakRDLCompatibleRDLGenerator()
        rdl = generator.generate(hierarchy)

        # Compile with PeakRDL
        rdlc = RDLCompiler()
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.rdl', delete=False) as f:
            f.write(rdl)
            rdl_file = f.name

        try:
            rdlc.compile_file(rdl_file)
            root = rdlc.elaborate()

            print(f"\nElaborated root children:")
            for child in root.children():
                print(f"  - {child.inst_name} (type: {type(child).__name__})")
                # Check if it has children
                sub_children = list(child.children())
                if sub_children:
                    print(f"    Sub-components:")
                    for sub in sub_children[:5]:  # Show first 5
                        print(f"      - {sub.inst_name}")
                    if len(sub_children) > 5:
                        print(f"      ... and {len(sub_children) - 5} more")

        finally:
            os.unlink(rdl_file)

    finally:
        db.close()

if __name__ == '__main__':
    check_elaboration()
