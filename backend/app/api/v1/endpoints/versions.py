"""
API endpoints for version management and code generation
"""
import shutil
import uuid
import time
import os
import logging
from pathlib import Path
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Form, Body
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.db.base import get_db
from app.models.schemas import (
    VersionCreate, VersionResponse, CodeGenerationRequest, UploadResponse,
    RTLGenerationRequest, RTLGenerationResponse, RTLConfigOptions, RTLFileInfo
)
from app.models.version import Version
from app.services.version_service import VersionService
from app.services.hierarchy_parser import HierarchyParser
from app.services.code_generator import CodeGenerator
from app.services.peakrdl_html_service import PeakRDLHTMLService
from app.services.peakrdl_wrapper import PeakRDLWrapper
from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
from app.services.cumulative_hierarchy_service import CumulativeHierarchyService
from app.services.module_code_generator import ModuleCodeGenerator
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_version_output_dir(version: Version) -> Path:
    """Get the output directory for a version: output/{user_id}/{version_name}/"""
    version_dir_name = _sanitize_filename(version.name) if version.name else f"v{version.id}"
    user_id = version.user_id or 'default'
    return settings.OUTPUT_DIR / user_id / version_dir_name


@router.get("/versions", response_model=List[VersionResponse])
async def get_versions(user: Optional[str] = Query(None, description="Filter by user. 'admin' returns all."), db: Session = Depends(get_db)):
    """Get versions filtered by user and publish status"""
    service = VersionService(db)
    return service.get_filtered_versions(user)


@router.post("/versions", response_model=VersionResponse)
async def create_version(version: VersionCreate, db: Session = Depends(get_db)):
    """Create a new version"""
    service = VersionService(db)
    # Check if version name already exists
    existing = db.query(Version).filter(Version.name == version.name).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Version name '{version.name}' already exists"
        )
    try:
        return service.create_version(version)
    except IntegrityError as e:
        raise HTTPException(status_code=400, detail=f"Create failed: {str(e)}")


@router.put("/versions/{version_id}", response_model=VersionResponse)
async def update_version(
    version_id: int,
    request: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Update a version (only owner can update unpublished versions)"""
    service = VersionService(db)
    version = service.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    request_user_id = request.get('user_id', 'default')

    # Only owner can update
    if version.user_id != request_user_id:
        raise HTTPException(status_code=403, detail="Cannot modify version owned by another user")

    # Cannot modify published versions
    if version.is_published:
        raise HTTPException(status_code=403, detail="Cannot modify published version")

    # Update fields
    if 'name' in request:
        version.name = request['name']
    if 'description' in request:
        version.description = request['description']

    db.commit()
    db.refresh(version)
    return version


@router.post("/versions/{version_id}/publish")
async def publish_version(
    version_id: int,
    request: dict = Body(...),
    db: Session = Depends(get_db)
):
    """Publish a version (only owner can publish)"""
    service = VersionService(db)
    version = service.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    request_user_id = request.get('user_id', 'default')

    # Only owner can publish
    if version.user_id != request_user_id:
        raise HTTPException(status_code=403, detail="Only the owner can publish this version")

    # Cannot publish already published versions
    if version.is_published:
        raise HTTPException(status_code=403, detail="Version is already published")

    version.is_published = True
    db.commit()
    db.refresh(version)
    return {"success": True, "message": "Version published", "version": VersionResponse.model_validate(version)}


@router.get("/versions/{version_id}")
async def get_version(version_id: int, db: Session = Depends(get_db)):
    """Get version details with hierarchy"""
    service = VersionService(db)
    version = service.get_version_hierarchy(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return version


@router.get("/versions/{version_id}/warnings")
async def get_version_warnings(version_id: int, db: Session = Depends(get_db)):
    """Get version warnings"""
    from app.models.version import Version
    version = db.query(Version).filter(Version.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return {
        "version_id": version_id,
        "version_name": version.name,
        "warnings": version.warnings or []
    }


@router.post("/versions/{version_id}/upload")
async def upload_excel(
    version_id: int,
    file: UploadFile = File(...),
    ralf_file: Optional[UploadFile] = File(None, description="Optional RALF file for mixed source"),
    db: Session = Depends(get_db)
):
    """Upload single Excel file with optional RALF file"""
    return await _process_upload(version_id, [file], db, ralf_file)


@router.post("/versions/{version_id}/upload/batch")
async def upload_excel_batch(
    version_id: int,
    files: List[UploadFile] = File(...),
    ralf_file: Optional[UploadFile] = File(None, description="Optional RALF file for mixed source"),
    db: Session = Depends(get_db)
):
    """Upload multiple Excel files with optional RALF file"""
    return await _process_upload(version_id, files, db, ralf_file)


async def _process_upload(version_id: int, files: List[UploadFile], db: Session, ralf_file: Optional[UploadFile] = None):
    """Process uploaded Excel files with optional RALF file using transactional mode

    Transactional upload flow:
    1. Parse uploaded files to temp directory
    2. Calculate merged hierarchy in memory (no DB save yet)
    3. Generate all files to temp output directory
    4. Validate generated files
    5. If all success: save DB + atomic file replacement
    6. If any failure: cleanup temp, keep original files and DB unchanged
    """
    service = VersionService(db)
    version = service.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    user_id = version.user_id or 'default'

    # Create temp directories
    temp_input_dir = settings.TEMP_DIR / str(uuid.uuid4())
    temp_output_dir = settings.TEMP_DIR / f"output_{uuid.uuid4()}"
    temp_input_dir.mkdir(parents=True, exist_ok=True)
    temp_output_dir.mkdir(parents=True, exist_ok=True)

    file_paths = []
    ralf_path = None
    html_url = None
    backup_dir = None

    try:
        # Step 1: Save uploaded files to temp directory
        for file in files:
            file_path = temp_input_dir / file.filename
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)

            # Check if file is RALF by extension
            if file.filename.lower().endswith('.ralf'):
                ralf_path = str(file_path)
            else:
                file_paths.append(str(file_path))

        # Save RALF file if provided separately
        if ralf_file:
            ralf_path = temp_input_dir / ralf_file.filename
            with open(ralf_path, "wb") as f:
                shutil.copyfileobj(ralf_file.file, f)
            ralf_path = str(ralf_path)

        # Step 2: Calculate merged hierarchy (no DB save yet)
        cumulative_service = CumulativeHierarchyService(db)
        result = cumulative_service.calculate_merged_hierarchy(version_id, file_paths, ralf_path)

        if not result['success']:
            raise HTTPException(
                status_code=400,
                detail={"errors": result.get('errors', []), "warnings": result.get('warnings', [])}
            )

        hierarchy = result['hierarchy']

        # Step 3: Generate all files to temp output directory
        print(f"[_process_upload] Starting code generation to temp dir: {temp_output_dir}")
        gen_start = time.time()

        # Generate HTML
        try:
            if hierarchy.top_modules:
                html_service = PeakRDLHTMLService()

                html_result = html_service.generate_html(
                    hierarchy,
                    version_id=version_id,
                    ralf_file=ralf_path,
                    version_name=version.name,
                    output_base_dir=temp_output_dir,
                    user_id=version.user_id or 'default'
                )

                if html_result['success']:
                    html_url = html_result['html_url']
                    version.html_path = html_result.get('html_path')
                    result['warnings'].extend(html_result.get('warnings', []))
                    print(f"[_process_upload] HTML generation successful: {html_url}")
                else:
                    result['warnings'].extend(html_result.get('errors', []))
                    version.html_path = None  # Clear old html_path on failure
                    print(f"[_process_upload] HTML generation failed: {html_result.get('errors', [])}")
        except Exception as e:
            result['warnings'].append(f"HTML generation failed: {str(e)}")
            version.html_path = None  # Clear old html_path on exception
            import traceback
            traceback.print_exc()

        # Generate code files to temp directory
        print(f"[_process_upload] Generating code files...")
        code_results = await _generate_all_codes_to_dir(
            hierarchy, temp_output_dir, version_id, version.name, user_id
        )
        print(f"[_process_upload] Code generation completed in {time.time() - gen_start:.2f}s")

        # Step 4: Validate generated files
        # Check for critical errors
        critical_errors = []
        for err in code_results.get('errors', []):
            if 'field' in err.lower() or 'width' in err.lower() or 'bit' in err.lower():
                critical_errors.append(err)

        if critical_errors:
            result['errors'].extend(critical_errors)
            raise HTTPException(
                status_code=400,
                detail={"errors": result['errors'], "warnings": result['warnings']}
            )

        # Step 5: All validation passed, now do atomic replacement
        version_dir_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"
        user_id = version.user_id or 'default'
        final_output_dir = settings.OUTPUT_DIR / user_id / version_dir_name
        backup_dir = settings.OUTPUT_DIR / user_id / f"{version_dir_name}_backup_{int(time.time())}"

        try:
            # Backup existing directory if it exists
            if final_output_dir.exists():
                final_output_dir.rename(backup_dir)

            # Move temp output to final location
            # All generators now use consistent path: temp_output_dir / user_id / version_dir_name
            temp_version_dir = temp_output_dir / user_id / version_dir_name
            if temp_version_dir.exists():
                final_output_dir.parent.mkdir(parents=True, exist_ok=True)
                temp_version_dir.rename(final_output_dir)
            else:
                # Fallback: move entire temp_output_dir
                final_output_dir.parent.mkdir(parents=True, exist_ok=True)
                temp_output_dir.rename(final_output_dir)

            # Update html_path to point to final directory (not temp)
            if html_url:
                final_html_path = final_output_dir / 'html'
                if final_html_path.exists():
                    version.html_path = str(final_html_path)
                    print(f"[_process_upload] Updated html_path to final: {version.html_path}")

            # Save to database (now that files are in place)
            db_success = cumulative_service.save_hierarchy(
                version_id, hierarchy, result['warnings']
            )

            if not db_success:
                raise Exception("Failed to save hierarchy to database")

            # Save top_addrmap_name to version for later use
            if hierarchy.top_addrmap_name:
                version.top_addrmap_name = hierarchy.top_addrmap_name

            db.commit()

            # After atomic replacement, generate UVM/RTL using PeakRDL
            # This is done after files are in final location to avoid include path issues
            # Note: We pass settings.OUTPUT_DIR / user_id (not final_output_dir) because
            # ModuleCodeGenerator.save_all creates version subdirectory internally
            try:
                await _generate_peakrdl_codes_post_upload(
                    hierarchy, version_id, version.name, settings.OUTPUT_DIR, result, user_id
                )
            except Exception as e:
                # Non-critical: UVM/RTL generation failure shouldn't fail the upload
                print(f"[POST_UPLOAD] PeakRDL generation warning: {e}")
                result['warnings'].append(f"UVM/RTL generation: {str(e)}")

            # Success - delete backup
            if backup_dir and backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)

        except Exception as e:
            # Rollback: restore backup if exists
            if backup_dir and backup_dir.exists():
                if final_output_dir.exists():
                    shutil.rmtree(final_output_dir, ignore_errors=True)
                backup_dir.rename(final_output_dir)
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail={"errors": [f"Failed to save upload: {str(e)}"], "warnings": result['warnings']}
            )

        # Non-critical errors become warnings
        if code_results.get('errors'):
            result['warnings'].extend(code_results['errors'])

        if code_results.get('warnings'):
            result['warnings'].extend(code_results['warnings'])

        # Calculate stats
        total_modules = len(hierarchy.all_modules)
        total_registers = sum(len(m.registers) for m in hierarchy.all_modules.values())

        # Convert uninstantiated modules to response format
        uninstantiated = []
        for um in result['uninstantiated']:
            uninstantiated.append({
                'name': um.name,
                'source': um.source,
                'reason': um.reason,
                'start_addr': um.start_addr,
                'register_count': len(um.registers)
            })

        return {
            "success": True,
            "total_files": len(files),
            "modules_count": total_modules,
            "registers_count": total_registers,
            "warnings": result['warnings'],
            "errors": result['errors'],
            "html_url": html_url,
            "code_results": code_results,
            "uninstantiated_modules": uninstantiated,
            "top_addrmap_name": hierarchy.top_addrmap_name
        }

    finally:
        # Cleanup temp directories
        if temp_input_dir.exists():
            shutil.rmtree(temp_input_dir, ignore_errors=True)
        if temp_output_dir.exists():
            shutil.rmtree(temp_output_dir, ignore_errors=True)


async def _generate_peakrdl_codes_post_upload(
    hierarchy, version_id: int, version_name: str, output_dir: Path, result: dict, user_id: str = "default"
):
    """Generate UVM/RTL using PeakRDL after files are in final location

    This is called after atomic replacement to avoid include path issues
    with temporary directories during transactional upload.
    """
    from app.services.module_code_generator import ModuleCodeGenerator

    print(f"[POST_UPLOAD] Generating UVM/RTL for version {version_name} in {output_dir}")

    generator = ModuleCodeGenerator(output_dir)

    # Generate with PeakRDL enabled (skip_peakrdl=False by default)
    generated = generator.generate_all(hierarchy, version_id, version_name)

    # Save generated files
    saved = generator.save_all(generated, version_id, version_name, user_id=user_id)

    # Track results
    # saved['uvm'] and saved['rtl'] are lists of file paths (not dicts)
    if saved.get('uvm'):
        for file_path in saved['uvm']:
            if 'modules' not in result:
                result['modules'] = {}
            # Extract module name from filename (e.g., "TEST_MOD_regmodel.sv" -> "TEST_MOD")
            module_name = Path(file_path).stem.replace('_regmodel', '')
            if module_name not in result['modules']:
                result['modules'][module_name] = {}
            result['modules'][module_name]['uvm'] = {
                "success": True,
                "path": file_path
            }
        print(f"[POST_UPLOAD] UVM generated: {saved['uvm']}")

    if saved.get('rtl'):
        for file_path in saved['rtl']:
            if 'modules' not in result:
                result['modules'] = {}
            # Extract module name from filename (e.g., "TEST_MOD_reg.sv" -> "TEST_MOD")
            module_name = Path(file_path).stem.replace('_reg', '')
            if module_name not in result['modules']:
                result['modules'][module_name] = {}
            result['modules'][module_name]['rtl'] = {
                "success": True,
                "path": file_path
            }
        print(f"[POST_UPLOAD] RTL generated: {saved['rtl']}")

    # Add any warnings/errors (but don't fail the upload)
    if generator.get_warnings():
        result.setdefault('warnings', []).extend(generator.get_warnings())
    if generator.get_errors():
        result.setdefault('warnings', []).extend([f"PeakRDL: {e}" for e in generator.get_errors()])


async def _generate_all_codes_to_dir(hierarchy, output_base_dir: Path, version_id: int = 0, version_name: str = "", user_id: str = "default") -> dict:
    """Generate all code formats to a specific directory (for transactional upload)

    Args:
        hierarchy: Register hierarchy
        output_base_dir: Base directory for output (e.g., temp directory)
        version_id: Version ID
        version_name: Version name

    Returns:
        dict with generation results
    """
    from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
    from app.services.code_generator import RALFGenerator, CHeaderGenerator, SVHeaderGenerator

    results = {
        "modules": {},
        "combined": {},
        "errors": [],
        "warnings": []
    }

    # Use version_name for directory naming
    version_dir_name = _sanitize_filename(version_name) if version_name else f"v{version_id}"
    output_dir = output_base_dir / user_id / version_dir_name

    # Find top addrmap name
    top_name = None
    for module in hierarchy.top_modules:
        if len(module.submodules) > 0 and len(module.registers) == 0:
            top_name = module.name
            break
    if not top_name and hierarchy.top_modules:
        top_name = hierarchy.top_modules[0].name

    if not top_name:
        results["errors"].append("No top module found in hierarchy")
        return results

    # Generate simplified RALF
    try:
        ralf_gen = RALFGenerator()
        ralf_content = ralf_gen.generate(hierarchy)
        ralf_output_dir = output_dir / 'ralf'
        ralf_output_dir.mkdir(parents=True, exist_ok=True)
        ralf_file_path = ralf_output_dir / f"{top_name}.ralf"
        ralf_file_path.write_text(ralf_content, encoding='utf-8')

        results["combined"]['ralf'] = {
            "success": True,
            "path": str(ralf_file_path)
        }
    except Exception as e:
        results["errors"].append(f"RALF generation failed: {str(e)}")

    # Generate simplified C Header
    try:
        c_gen = CHeaderGenerator()
        c_content = c_gen.generate(hierarchy)
        c_output_dir = output_dir / 'header'
        c_output_dir.mkdir(parents=True, exist_ok=True)
        c_file_path = c_output_dir / f"{top_name}.h"
        c_file_path.write_text(c_content, encoding='utf-8')

        results["combined"]['header'] = {
            "success": True,
            "path": str(c_file_path)
        }
    except Exception as e:
        results["errors"].append(f"C Header generation failed: {str(e)}")

    # Generate simplified SV Header
    try:
        svh_gen = SVHeaderGenerator()
        svh_content = svh_gen.generate(hierarchy)
        svh_output_dir = output_dir / 'svh'
        svh_output_dir.mkdir(parents=True, exist_ok=True)
        svh_file_path = svh_output_dir / f"{top_name}.svh"
        svh_file_path.write_text(svh_content, encoding='utf-8')

        results["combined"]['svheader'] = {
            "success": True,
            "path": str(svh_file_path)
        }
    except Exception as e:
        results["errors"].append(f"SVH generation failed: {str(e)}")

    # Generate per-module files using ModuleCodeGenerator
    # Note: For transactional upload, we skip PeakRDL compilation (UVM/RTL)
    # to avoid include path issues with temporary directories
    try:
        from app.services.module_code_generator import ModuleCodeGenerator
        uvm_generator = ModuleCodeGenerator(output_base_dir)
        # Only generate RDL/RALF/Header/SVH, skip UVM/RTL to avoid PeakRDL compilation errors
        uvm_generated = uvm_generator.generate_all(hierarchy, version_id=version_id, version_name=version_name, skip_peakrdl=True)

        # Files are saved by save_all method
        saved_uvm = uvm_generator.save_all(uvm_generated, version_id=version_id, version_name=version_name, user_id=user_id)

        # Add RDL files to results
        if saved_uvm.get('rdl'):
            if top_name in saved_uvm['rdl']:
                results["combined"]['rdl'] = {
                    "success": True,
                    "path": str(output_dir / 'rdl' / f"{top_name}.rdl")
                }
            for module_name in saved_uvm.get('rdl', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                results["modules"][module_name]['rdl'] = {
                    "success": True,
                    "path": str(output_dir / 'rdl' / f"{module_name}.rdl")
                }

        if saved_uvm.get('uvm'):
            results["combined"]['uvm'] = {
                "success": True,
                "path": str(output_dir / 'uvm' / f"{top_name}_regmodel.sv")
            }
            for module_name in saved_uvm.get('uvm', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                results["modules"][module_name]['uvm'] = {
                    "success": True,
                    "path": str(output_dir / 'uvm' / f"{module_name}_regmodel.sv")
                }

        if saved_uvm.get('rtl'):
            for module_name in saved_uvm.get('rtl', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                results["modules"][module_name]['rtl'] = {
                    "success": True,
                    "path": str(output_dir / 'rtl' / f"{module_name}_reg.sv")
                }

        if saved_uvm.get('svh'):
            for module_name in saved_uvm.get('svh', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                if 'svh' not in results["modules"][module_name]:
                    results["modules"][module_name]['svh'] = {
                        "success": True,
                        "path": str(output_dir / 'svh' / f"{module_name}.svh")
                    }

        if saved_uvm.get('header'):
            for module_name in saved_uvm.get('header', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                if 'header' not in results["modules"][module_name]:
                    results["modules"][module_name]['header'] = {
                        "success": True,
                        "path": str(output_dir / 'header' / f"{module_name}.h")
                    }

        if saved_uvm.get('ralf'):
            for module_name in saved_uvm.get('ralf', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                if 'ralf' not in results["modules"][module_name]:
                    results["modules"][module_name]['ralf'] = {
                        "success": True,
                        "path": str(output_dir / 'ralf' / f"{module_name}.ralf")
                    }

    except Exception as e:
        results["errors"].append(f"Module code generation failed: {str(e)}")
        import traceback
        traceback.print_exc()

    return results


async def _generate_all_codes(hierarchy, version_id: int, version_name: str = "", db: Session = None, user_id: str = "default") -> dict:
    """Generate all code formats using PeakRDL-compatible RDL generator

    Simplified version that:
    1. Generates monolithic RDL (for download and RTL generation)
    2. Does NOT generate per-module RALF/Header/SVH (avoiding validation errors)
    3. RTL is generated separately via dedicated API endpoint
    """
    from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
    from app.services.code_generator import RALFGenerator, CHeaderGenerator, SVHeaderGenerator

    results = {
        "modules": {},
        "combined": {},
        "errors": [],
        "warnings": []
    }

    # Use version_name for directory naming
    version_dir_name = _sanitize_filename(version_name) if version_name else f"v{version_id}"

    # Find top addrmap name
    top_name = None
    for module in hierarchy.top_modules:
        if len(module.submodules) > 0 and len(module.registers) == 0:
            top_name = module.name
            break
    if not top_name and hierarchy.top_modules:
        top_name = hierarchy.top_modules[0].name

    if not top_name:
        results["errors"].append("No top module found in hierarchy")
        return results

    # RDL files are generated by ModuleCodeGenerator below (per-module with includes)
    # The top-level RDL (soc_addr_map.rdl) uses include statements for submodules
    # This provides a cleaner, modular structure

    # Generate simplified RALF (addrmap only, no field details to avoid validation errors)
    ralf_gen = RALFGenerator()
    ralf_content = ralf_gen.generate(hierarchy)
    ralf_output_dir = settings.OUTPUT_DIR / user_id / version_dir_name / 'ralf'
    ralf_output_dir.mkdir(parents=True, exist_ok=True)
    ralf_file_path = ralf_output_dir / f"{top_name}.ralf"
    ralf_file_path.write_text(ralf_content, encoding='utf-8')

    results["combined"]['ralf'] = {
        "success": True,
        "path": str(ralf_file_path)
    }

    # Generate simplified C Header (base addresses only)
    c_gen = CHeaderGenerator()
    c_content = c_gen.generate(hierarchy)
    c_output_dir = settings.OUTPUT_DIR / user_id / version_dir_name / 'header'
    c_output_dir.mkdir(parents=True, exist_ok=True)
    c_file_path = c_output_dir / f"{top_name}.h"
    c_file_path.write_text(c_content, encoding='utf-8')

    results["combined"]['header'] = {
        "success": True,
        "path": str(c_file_path)
    }

    # Generate simplified SV Header
    svh_gen = SVHeaderGenerator()
    svh_content = svh_gen.generate(hierarchy)
    svh_output_dir = settings.OUTPUT_DIR / user_id / version_dir_name / 'svh'
    svh_output_dir.mkdir(parents=True, exist_ok=True)
    svh_file_path = svh_output_dir / f"{top_name}.svh"
    svh_file_path.write_text(svh_content, encoding='utf-8')

    results["combined"]['svheader'] = {
        "success": True,
        "path": str(svh_file_path)
    }

    # Generate UVM using ModuleCodeGenerator
    try:
        from app.services.module_code_generator import ModuleCodeGenerator
        # ModuleCodeGenerator.save_all will add version_dir_name, so pass OUTPUT_DIR / user_id directly
        uvm_generator = ModuleCodeGenerator(settings.OUTPUT_DIR)
        uvm_generated = uvm_generator.generate_all(hierarchy, version_id=version_id, version_name=version_name)

        # UVM files are saved by save_all method
        saved_uvm = uvm_generator.save_all(uvm_generated, version_id=version_id, version_name=version_name, user_id=user_id)

        # Add RDL files to results (generated by ModuleCodeGenerator)
        if saved_uvm.get('rdl'):
            # Top-level addrmap RDL (e.g., soc_addr_map.rdl)
            if top_name in saved_uvm['rdl']:
                results["combined"]['rdl'] = {
                    "success": True,
                    "path": str(settings.OUTPUT_DIR / user_id / _sanitize_filename(version_name) / 'rdl' / f"{top_name}.rdl")
                }
            # Individual module RDL files
            for module_name in saved_uvm.get('rdl', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                results["modules"][module_name]['rdl'] = {
                    "success": True,
                    "path": str(settings.OUTPUT_DIR / user_id / _sanitize_filename(version_name) / 'rdl' / f"{module_name}.rdl")
                }

        if saved_uvm.get('uvm'):
            results["combined"]['uvm'] = {
                "success": True,
                "path": str(settings.OUTPUT_DIR / user_id / _sanitize_filename(version_name) / 'uvm' / f"{top_name}_regmodel.sv")
            }
            # Also add individual module UVM files to modules
            for module_name in saved_uvm.get('uvm', []):
                if module_name not in results["modules"]:
                    results["modules"][module_name] = {}
                results["modules"][module_name]['uvm'] = {
                    "success": True,
                    "path": str(settings.OUTPUT_DIR / user_id / _sanitize_filename(version_name) / 'uvm' / f"{module_name}_regmodel.sv")
                }
        else:
            results["combined"]['uvm'] = {
                "success": False,
                "message": "No UVM files generated"
            }
    except Exception as e:
        results["combined"]['uvm'] = {
            "success": False,
            "message": f"UVM generation failed: {str(e)}"
        }

    # RTL is generated separately via dedicated API endpoint
    # This avoids blocking the main upload flow

    # Save warnings to database
    if db:
        from app.models.version import Version
        version = db.query(Version).filter(Version.id == version_id).first()
        if version:
            # Merge new warnings with existing warnings
            existing_warnings = version.warnings or []
            all_warnings = existing_warnings + results["warnings"]
            # Remove duplicates while preserving order
            seen = set()
            unique_warnings = []
            for w in all_warnings:
                if w not in seen:
                    seen.add(w)
                    unique_warnings.append(w)
            version.warnings = unique_warnings
            db.commit()

    return results



@router.post("/versions/{version_id}/generate")
async def generate_code(
    version_id: int,
    request: CodeGenerationRequest,
    db: Session = Depends(get_db)
):
    """Generate specific code formats for a version"""
    service = VersionService(db)
    version = service.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Get hierarchy data
    hierarchy_data = service.get_version_hierarchy(version_id)
    if not hierarchy_data:
        raise HTTPException(status_code=400, detail="No data in version")

    # Re-parse to get hierarchy object
    # Note: In production, you'd want to store the hierarchy or rebuild from DB
    # Here we'll return an error asking to re-upload
    raise HTTPException(
        status_code=400,
        detail="Please re-upload Excel files to regenerate codes"
    )


# ============================================================================
# PDF Download Endpoint (MUST be registered BEFORE /download/{format_type}
# to avoid "/pdf" being captured as a format_type value)
# ============================================================================

@router.get("/versions/{version_id}/download/pdf")
async def download_pdf(
    version_id: int,
    include_all_pages: bool = True,
    db: Session = Depends(get_db)
):
    """
    Generate and download PDF register documentation.

    Uses Playwright (headless Chromium) to render peakrdl-html output.
    Cached: subsequent requests return the cached PDF if HTML hasn't changed.

    Args:
        version_id: Version ID
        include_all_pages: If True, merge all module pages into one PDF with bookmarks.
                           If False, only render the top-level index.html.
    """
    import fcntl

    service = VersionService(db)
    version = service.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # ---- Check that HTML exists ----
    html_dir = _get_version_output_dir(version) / "html"
    html_file = html_dir / "index.html"
    if not html_file.exists():
        raise HTTPException(
            status_code=400,
            detail="HTML not yet generated. Please upload Excel and process first."
        )

    output_dir = _get_version_output_dir(version)
    pdf_dir = output_dir / "pdf"
    pdf_path = pdf_dir / f"{version.name}.pdf"
    lock_path = pdf_dir / f"{version.name}.pdf.lock"

    # ---- Cache check (no lock needed for reads) ----
    if pdf_path.exists():
        html_mtime = _get_newest_file_mtime(html_dir)
        if pdf_path.stat().st_mtime >= html_mtime:
            return FileResponse(
                pdf_path,
                media_type="application/pdf",
                filename=f"{version.name}.pdf"
            )

    # ---- All pages mode builds bookmarks from RAL data ----
    # Single page mode just renders the combined HTML without bookmarks

    # ---- File lock: prevent duplicate generation ----
    pdf_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, 'w')
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Another request is generating this PDF — poll-wait for it
        lock_fd.close()
        for _ in range(120):
            await asyncio.sleep(0.5)
            if pdf_path.exists():
                return FileResponse(
                    pdf_path,
                    media_type="application/pdf",
                    filename=f"{version.name}.pdf"
                )
        raise HTTPException(
            status_code=503,
            detail="PDF generation in progress by another request, please retry in a minute."
        )

    try:
        # ---- Double-check cache after acquiring lock ----
        if pdf_path.exists():
            html_mtime = _get_newest_file_mtime(html_dir)
            if pdf_path.stat().st_mtime >= html_mtime:
                return FileResponse(
                    pdf_path,
                    media_type="application/pdf",
                    filename=f"{version.name}.pdf"
                )

        # ---- Generate PDF ----
        from app.services.pdf_generator import generate_pdf_safe, PDFGenerationError

        result_path = await generate_pdf_safe(
            html_dir=html_dir,
            output_dir=pdf_dir,
            title=version.name or f"version_{version_id}"
        )

        return FileResponse(
            result_path,
            media_type="application/pdf",
            filename=f"{version.name}.pdf"
        )

    except PDFGenerationError as e:
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed: {str(e)}"
        )
    except Exception as e:
        logger.exception("Unexpected error during PDF generation")
        raise HTTPException(
            status_code=500,
            detail=f"PDF generation failed unexpectedly: {str(e)}"
        )
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def _get_newest_file_mtime(directory: Path) -> float:
    """Get the most recent modification time of any file under directory."""
    max_mtime = 0.0
    for f in directory.rglob("*"):
        if f.is_file():
            mtime = f.stat().st_mtime
            if mtime > max_mtime:
                max_mtime = mtime
    return max_mtime


@router.get("/versions/{version_id}/download/{format_type}")
async def download_code(
    version_id: int,
    format_type: str,
    module: str = None,
    db: Session = Depends(get_db)
):
    """Download generated code file

    Args:
        version_id: Version ID
        format_type: Format type (rdl, ralf, header, svheader)
        module: Module name (optional, downloads combined file if not specified)
    """
    service = VersionService(db)
    version = service.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Use user-aware path
    version_output_dir = _get_version_output_dir(version)
    version_dir_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"

    # Map format to directory and extension
    # Files are saved in OUTPUT_DIR / {user_id} / version_name / format/
    format_map = {
        'rdl': (version_output_dir / 'rdl', 'rdl'),
        'ralf': (version_output_dir / 'ralf', 'ralf'),
        'header': (version_output_dir / 'header', 'h'),
        'svheader': (version_output_dir / 'svh', 'svh'),
        'uvm': (version_output_dir / 'uvm', 'sv'),
        'rtl': (version_output_dir / 'rtl', 'sv'),
    }

    if format_type not in format_map:
        raise HTTPException(status_code=400, detail=f"Unknown format: {format_type}")

    output_dir, ext = format_map[format_type]

    # Special handling for RTL (multiple files in directory)
    if format_type == 'rtl':
        return await _download_rtl_files(version_id, module, output_dir, db, version)

    # Determine filename based on module parameter
    if module:
        # Download specific module file - try multiple naming patterns
        # Pattern 1: {module}.{ext}
        # Pattern 2: {module}_top.{ext} (PeakRDL style)
        # Pattern 3: {module}_reg.{ext} (RTL style)
        candidates = [
            f"{module}.{ext}",
            f"{module}_top.{ext}",
            f"{module}_reg.{ext}",
            f"{module}_regmodel.{ext}",  # UVM style
        ]
        # For header files, also try .header extension
        if ext == 'h':
            candidates.extend([
                f"{module}.header",
                f"{module}_top.header",
                f"{module}_reg.header",
            ])

        file_path = None
        for candidate in candidates:
            test_path = output_dir / candidate
            if test_path.exists():
                file_path = test_path
                break

        if not file_path:
            raise HTTPException(
                status_code=404,
                detail=f"File not found for module '{module}' with extension '{ext}'"
            )

        download_name = f"{module}.{ext}"
    else:
        # Download combined file - look for soc_addr_map.h or similar
        # Try to find the top-level combined file
        import os
        combined_file = None

        # First, try exact matches for common names
        for name in ['soc_addr_map', version_dir_name, version.name]:
            candidates = [
                f"{name}.{ext}",
                f"{name}_top.{ext}",
                f"{name}_root.{ext}",
            ]
            if ext == 'h':
                candidates.extend([
                    f"{name}.header",
                    f"{name}_top.header",
                    f"{name}_root.header",
                ])

            for candidate in candidates:
                test_path = output_dir / candidate
                if test_path.exists():
                    combined_file = candidate
                    break
            if combined_file:
                break

        # If not found, look for largest file as fallback
        if not combined_file and output_dir.exists():
            largest_file = None
            largest_size = 0
            for fname in os.listdir(output_dir):
                if fname.endswith(f".{ext}") and fname not in ['reg_common.h', 'reg_common.svh']:
                    fpath = output_dir / fname
                    if fpath.is_file() and fpath.stat().st_size > largest_size:
                        largest_size = fpath.stat().st_size
                        largest_file = fname

            if largest_file and largest_size > 100:  # At least 100 bytes
                combined_file = largest_file

        if not combined_file:
            raise HTTPException(
                status_code=404,
                detail=f"Combined file not found. Please upload Excel first."
            )

        file_path = output_dir / combined_file
        download_name = f"{version.name}.{ext}"

    return FileResponse(
        file_path,
        media_type="text/plain",
        filename=download_name
    )


async def _download_rtl_files(version_id: int, module: str, output_dir: Path, db, version=None):
    """Helper to download RTL files as zip"""
    import zipfile
    import io

    # Get version if not provided
    if version is None:
        service = VersionService(db)
        version = service.get_version(version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version not found")

    # Use version_name for directory naming
    version_dir_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"

    # RTL files are saved as {module}_reg.sv directly in rtl/ directory
    rtl_dir = output_dir  # output_dir is already the rtl directory

    # Get all SV files
    if rtl_dir.exists():
        all_files = list(rtl_dir.glob("*.sv"))
        if module:
            # Filter files matching module name
            sv_files = [f for f in all_files if f.stem.startswith(module) or module in f.stem]
            zip_name = f"{module}_rtl.zip"
        else:
            sv_files = all_files
            zip_name = f"{version_dir_name}_rtl.zip"
    else:
        sv_files = []
        zip_name = f"{version_dir_name}_rtl.zip"

    if not sv_files:
        raise HTTPException(status_code=404, detail="RTL not generated yet. Use /rtl/generate endpoint first.")

    # Create zip in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in sv_files:
            arcname = file_path.name  # Just use filename
            zip_file.write(file_path, arcname)

    zip_buffer.seek(0)

    # Create a temp file for FileResponse
    from starlette.datastructures import UploadFile as StarletteUploadFile
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_name}"}
    )


@router.get("/versions/{version_id}/files")
async def list_generated_files(version_id: int, db: Session = Depends(get_db)):
    """List all generated files for a version"""
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    version_name = version.name
    result = {
        "version_id": version_id,
        "version_name": version_name,
        "modules": {},
        "combined": {}
    }

    # Use user-aware path
    version_output_dir = _get_version_output_dir(version)
    version_dir_name = _sanitize_filename(version_name) if version_name else f"v{version_id}"

    # Get top_addrmap_name from database (saved during upload)
    # This is the authoritative source, not inferred from filenames
    top_addrmap_name = version.top_addrmap_name

    # Scan all output directories - unified path: output/{user_id}/{version_name}/{format}/
    format_dirs = {
        'rdl': version_output_dir / 'rdl',
        'ralf': version_output_dir / 'ralf',
        'header': version_output_dir / 'header',
        'svheader': version_output_dir / 'svh',
        'uvm': version_output_dir / 'uvm',
        'rtl': version_output_dir / 'rtl',
    }

    for fmt, version_dir in format_dirs.items():
        if version_dir.exists():
            files_in_dir = [f for f in version_dir.iterdir() if f.is_file()]
            for file_path in files_in_dir:
                file_info = {
                    "name": file_path.name,
                    "path": str(file_path),
                    "size": file_path.stat().st_size
                }

                # Categorize as module file or combined file
                # Combined files are typically:
                # 1. Named with version_dir_name (e.g., "test_v1.rdl")
                # 2. Named with _top or _root suffix (from PeakRDL, e.g., "soc_addr_map_top.rdl")
                # Note: Individual module files go to modules (e.g., "CPD.rdl", "PMH.rdl")
                # IMPORTANT: Don't use top_addrmap_name check because for single-module
                # cases, the module file name equals top_addrmap_name but should still
                # be categorized as a module file, not combined.
                is_combined = (
                    file_path.stem == version_dir_name or
                    file_path.stem.endswith('_top') or
                    file_path.stem.endswith('_root')
                )

                if is_combined:
                    result["combined"][fmt] = file_info
                else:
                    # Remove common suffixes to get module name
                    module_name = file_path.stem
                    if module_name.endswith('_regmodel'):
                        module_name = module_name[:-9]  # Remove '_regmodel'
                    elif module_name.endswith('_reg'):
                        module_name = module_name[:-4]  # Remove '_reg'

                    if module_name not in result["modules"]:
                        result["modules"][module_name] = {}
                    result["modules"][module_name][fmt] = file_info

    # Check HTML (PeakRDL generates a directory with index.html)
    # Unified path: output/{user_id}/{version_name}/html/index.html
    html_dir = version_output_dir / 'html'
    html_file = html_dir / "index.html"
    result["html"] = {
        "exists": html_file.exists(),
        "url": f"/static/{version.user_id or 'default'}/{version_dir_name}/html/index.html" if html_file.exists() else None,
        "directory": str(html_dir)
    }

    return result


@router.get("/versions/{version_id}/html")
async def get_version_html(version_id: int, db: Session = Depends(get_db)):
    """Get HTML view URL for a version"""
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # PeakRDL generates a directory with index.html
    # Use user-aware path: output/{user_id}/{version_name}/html/index.html
    version_dir_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"
    user_id = version.user_id or 'default'
    html_dir = settings.OUTPUT_DIR / user_id / version_dir_name / 'html'
    html_file = html_dir / "index.html"

    if html_file.exists():
        return {
            "version_id": version_id,
            "version_name": version.name,
            "html_url": f"/static/{user_id}/{version_dir_name}/html/index.html",
            "generated": True
        }
    else:
        return {
            "version_id": version_id,
            "version_name": version.name,
            "html_url": None,
            "generated": False,
            "message": "HTML not generated yet. Please upload Excel files first."
        }


@router.get("/versions/{version_id}/uninstantiated")
async def get_uninstantiated_modules(version_id: int, db: Session = Depends(get_db)):
    """
    获取未例化的模块列表

    这些模块存在于数据库中，但无法在当前的层次结构中找到合适的例化位置
    """
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    cumulative_service = CumulativeHierarchyService(db)
    uninstantiated = cumulative_service.get_uninstantiated_modules(version_id)

    return {
        "version_id": version_id,
        "version_name": version.name,
        "count": len(uninstantiated),
        "modules": uninstantiated
    }


@router.post("/versions/{version_id}/instantiate-module")
async def instantiate_module(
    version_id: int,
    module_name: str = Form(...),
    parent_module: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    手动例化一个模块到指定的父模块下
    """
    cumulative_service = CumulativeHierarchyService(db)

    try:
        # 将模块从 uninstantiated 移动到指定的父模块
        success = cumulative_service.instantiate_module_manually(
            version_id, module_name, parent_module
        )

        if success:
            return {
                "success": True,
                "message": f"Module '{module_name}' instantiated under '{parent_module}'"
            }
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to instantiate module '{module_name}'"
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _sanitize_filename(name: str) -> str:
    """Sanitize name to be filesystem-safe"""
    import re
    # Replace invalid characters with underscore
    return re.sub(r'[<>?:"/\\|?*]', '_', name)


@router.delete("/versions/{version_id}")
async def delete_version(
    version_id: int,
    request: dict = Body(default={}),
    db: Session = Depends(get_db)
):
    """Delete a version and all its data

    Request body must include 'password':
    - If user_id == 'admin': password must be 'askcp' -> can delete ANY version
    - Else: password must equal version.user_id -> can only delete own versions
    """
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    password = request.get('password', '')
    request_user_id = request.get('user_id', '')

    # Check permissions
    if request_user_id == 'admin':
        if password != 'askcp':
            raise HTTPException(status_code=403, detail="Invalid admin password")
    else:
        if password != version.user_id:
            raise HTTPException(status_code=403, detail="Cannot delete version owned by another user")

    # Delete generated files
    # Use user-aware path: OUTPUT_DIR / {user_id} / {version_name} /
    version_output_dir = _get_version_output_dir(version)
    if version_output_dir.exists():
        shutil.rmtree(version_output_dir, ignore_errors=True)

    # Delete from database
    success = service.delete_version(version_id)
    if success:
        return {"success": True, "message": "Version deleted"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete version")


# ============================================================================
# RTL Generation Endpoints
# ============================================================================

@router.get("/versions/{version_id}/rtl/options")
async def get_rtl_options(version_id: int, db: Session = Depends(get_db)):
    """Get available RTL generation configuration options"""
    from sqlalchemy.orm import joinedload
    from app.models.version import Version
    from app.models.register import RegisterModule

    # Load version with modules using joinedload
    version = db.query(Version).options(
        joinedload(Version.modules).joinedload(RegisterModule.registers)
    ).filter(Version.id == version_id).first()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Get available modules for RTL generation
    modules = []
    for module in version.modules:
        # Count registers
        reg_count = len(module.registers) if module.registers else 0
        modules.append({
            "name": module.name,
            "register_count": reg_count,
            "is_array": module.is_array,
            "array_count": module.array_count
        })

    return {
        "cpu_interfaces": [
            {"value": "axilite", "label": "AXI4-Lite", "default": True},
            {"value": "apb3", "label": "APB3", "default": False},
            {"value": "apb4", "label": "APB4", "default": False},
        ],
        "address_widths": [
            {"value": 16, "label": "16-bit", "default": False},
            {"value": 32, "label": "32-bit", "default": True},
            {"value": 64, "label": "64-bit", "default": False},
        ],
        "reset_types": [
            {"value": "active_low", "label": "Active Low", "default": True},
            {"value": "active_high", "label": "Active High", "default": False},
        ],
        "modules": modules
    }


@router.post("/versions/{version_id}/rtl/generate", response_model=RTLGenerationResponse)
async def generate_rtl(
    version_id: int,
    request: RTLGenerationRequest,
    db: Session = Depends(get_db)
):
    """
    Generate RTL code for a specific module or entire version

    Args:
        version_id: Version ID
        request: RTL generation configuration
    """
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Use user-aware path
    version_output_dir = _get_version_output_dir(version)

    # Check if PeakRDL is available
    peakrdl = PeakRDLWrapper()
    if not peakrdl.check_dependencies():
        raise HTTPException(
            status_code=503,
            detail="RTL generation not available. Please install: systemrdl-compiler, peakrdl-regblock"
        )

    # Determine RDL source
    # Use user-aware path: output/{user_id}/{version_name}/rdl/
    rdl_base_dir = version_output_dir / 'rdl'
    rtl_base_dir = version_output_dir / 'rtl'

    if request.module:
        # Generate RTL for specific module
        rdl_file = rdl_base_dir / f"{request.module}.rdl"
        rtl_output_dir = rtl_base_dir / request.module

        if not rdl_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"RDL file for module '{request.module}' not found. Please re-upload Excel files."
            )
    else:
        # Generate RTL for combined/all modules
        # Try wrapper file first, then combined file
        rdl_file = rdl_base_dir / f"soc_addr_map_top.rdl"
        if not rdl_file.exists():
            # Fallback to any .rdl file in the directory
            rdl_files = list(rdl_base_dir.glob("*.rdl"))
            if rdl_files:
                rdl_file = rdl_files[0]

        rtl_output_dir = rtl_base_dir

        if not rdl_file or not rdl_file.exists():
            raise HTTPException(
                status_code=404,
                detail="RDL file not found. Please upload Excel files first."
            )
    rtl_output_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Generate RTL using PeakRDL
        success, message = peakrdl.generate_rtl(
            rdl_file=rdl_file,
            output_dir=rtl_output_dir,
            cpu_if=request.cpu_if,
            address_width=request.address_width
        )

        if not success:
            raise HTTPException(status_code=500, detail=message)

        # List generated files
        generated_files = []
        if rtl_output_dir.exists():
            for file_path in rtl_output_dir.rglob("*.sv"):
                generated_files.append({
                    "filename": file_path.name,
                    "path": str(file_path),
                    "size": file_path.stat().st_size
                })

        return {
            "success": True,
            "version_id": version_id,
            "module": request.module,
            "message": message,
            "files": generated_files,
            "rtl_path": str(rtl_output_dir),
            "download_url": f"/api/v1/versions/{version_id}/rtl/download?module={request.module}" if request.module else f"/api/v1/versions/{version_id}/rtl/download"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"RTL generation failed: {str(e)}")


@router.get("/versions/{version_id}/rtl/status")
async def get_rtl_status(
    version_id: int,
    module: str = None,
    db: Session = Depends(get_db)
):
    """Check if RTL has been generated for a version/module"""
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Use user-aware path
    version_output_dir = _get_version_output_dir(version)

    # Unified path: output/{user_id}/{version_name}/rtl/
    rtl_base_dir = version_output_dir / 'rtl'

    # RTL files are saved as {module}_reg.sv directly in rtl/ directory
    # If module is specified, look for files matching {module}_*.sv or containing module name
    if module:
        files = []
        if rtl_base_dir.exists():
            for file_path in rtl_base_dir.glob("*.sv"):
                # Match files like {module}_reg.sv or {module}.sv
                if file_path.stem.startswith(module) or module in file_path.stem:
                    files.append(file_path)
        exists = len(files) > 0
    else:
        files = list(rtl_base_dir.glob("*.sv")) if rtl_base_dir.exists() else []
        exists = len(files) > 0

    file_list = []
    if exists:
        for file_path in files:
            file_list.append({
                "filename": file_path.name,
                "path": str(file_path.relative_to(rtl_base_dir)),
                "size": file_path.stat().st_size,
                "modified": file_path.stat().st_mtime
            })

    return {
        "version_id": version_id,
        "module": module,
        "generated": exists,
        "file_count": len(file_list),
        "files": file_list
    }


@router.get("/versions/{version_id}/rtl/download")
async def download_rtl(
    version_id: int,
    module: str = None,
    file: str = None,
    db: Session = Depends(get_db)
):
    """
    Download RTL files

    Args:
        version_id: Version ID
        module: Module name (optional)
        file: Specific file to download (optional, if not provided returns zip of all files)
    """
    import zipfile
    import io

    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Use user-aware path
    version_output_dir = _get_version_output_dir(version)
    version_dir_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"
    safe_version_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"

    # Unified path: output/{user_id}/{version_name}/rtl/
    rtl_base_dir = version_output_dir / 'rtl'

    # Get list of RTL files matching module pattern
    sv_files = []
    if rtl_base_dir.exists():
        if module:
            # Check module subdirectory first (per-module generation)
            module_dir = rtl_base_dir / module
            if module_dir.exists():
                sv_files = list(module_dir.glob("*.sv"))
            # Fallback: check top-level files matching module name
            if not sv_files:
                all_files = list(rtl_base_dir.glob("*.sv"))
                sv_files = [f for f in all_files if f.stem.startswith(module) or module in f.stem]
            zip_name = f"{module}_rtl.zip"
        else:
            # For combined download, get all top-level .sv files
            sv_files = list(rtl_base_dir.glob("*.sv"))
            zip_name = f"{safe_version_name}_rtl.zip"

    if not sv_files:
        raise HTTPException(status_code=404, detail="RTL not generated yet")

    # If specific file requested, return it directly
    if file:
        # Check for file in module subdirectory first (per-module generation)
        if module:
            file_path = rtl_base_dir / module / file
            if file_path.exists() and file_path.is_file():
                return FileResponse(
                    file_path,
                    media_type="text/plain",
                    filename=file_path.name
                )

        # Fallback to top-level file (combined generation)
        file_path = rtl_base_dir / file
        if file_path.exists() and file_path.is_file():
            return FileResponse(
                file_path,
                media_type="text/plain",
                filename=file_path.name
            )

        raise HTTPException(status_code=404, detail=f"File not found: {file}")

    # Otherwise, create zip of all RTL files
    if not sv_files:
        raise HTTPException(status_code=404, detail="No RTL files found")

    # Create zip in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in sv_files:
            arcname = file_path.name  # Just use filename, no subdirs
            zip_file.write(file_path, arcname)

    zip_buffer.seek(0)

    # Use StreamingResponse for in-memory zip
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_name}"}
    )


@router.get("/versions/{version_id}/rtl/files")
async def list_rtl_files(
    version_id: int,
    module: str = None,
    db: Session = Depends(get_db)
):
    """List all generated RTL files with details"""
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Use user-aware path
    version_output_dir = _get_version_output_dir(version)

    # Unified path: output/{user_id}/{version_name}/rtl/
    rtl_base_dir = version_output_dir / 'rtl'

    # RTL files are saved in rtl/ directory, either:
    # 1. Directly as {module}_reg.sv for combined generation
    # 2. In subdirectories rtl/{module}/ for per-module generation
    if not rtl_base_dir.exists():
        return {
            "version_id": version_id,
            "module": module,
            "generated": False,
            "file_count": 0,
            "files": []
        }

    files = []

    if module:
        # For specific module, check module subdirectory first
        module_dir = rtl_base_dir / module
        if module_dir.exists():
            # Per-module generation: files in rtl/{module}/
            for file_path in module_dir.glob("*.sv"):
                if file_path.is_file():
                    files.append({
                        "filename": file_path.name,
                        "relative_path": f"{module}/{file_path.name}",
                        "full_path": str(file_path),
                        "size": file_path.stat().st_size,
                        "modified": file_path.stat().st_mtime,
                        "type": file_path.suffix
                    })
        else:
            # Fallback: check top-level files matching module name
            for file_path in rtl_base_dir.glob("*.sv"):
                if file_path.is_file() and (file_path.stem.startswith(module) or module in file_path.stem):
                    files.append({
                        "filename": file_path.name,
                        "relative_path": file_path.name,
                        "full_path": str(file_path),
                        "size": file_path.stat().st_size,
                        "modified": file_path.stat().st_mtime,
                        "type": file_path.suffix
                    })
    else:
        # For combined/all modules, check top-level files only
        for file_path in rtl_base_dir.glob("*.sv"):
            if file_path.is_file():
                files.append({
                    "filename": file_path.name,
                    "relative_path": file_path.name,
                    "full_path": str(file_path),
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime,
                    "type": file_path.suffix
                })

    return {
        "version_id": version_id,
        "module": module,
        "generated": len(files) > 0,
        "file_count": len(files),
        "files": sorted(files, key=lambda x: x["filename"])
    }


# ============================================================================
# Module-based Code Generation Endpoints
# ============================================================================

@router.post("/versions/{version_id}/generate-module-files")
async def generate_module_files(
    version_id: int,
    db: Session = Depends(get_db)
):
    """
    Generate RALF, C Header, SVH for all modules
    Generate RTL only for register modules (not addr_map)
    Each module gets its own file named after the module
    """
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Rebuild hierarchy from database
    cumulative_service = CumulativeHierarchyService(db)
    # Use version.name for hierarchy naming, version_id for file paths
    hierarchy = cumulative_service._rebuild_hierarchy_from_db(version_id, version.name)

    if not hierarchy.all_modules:
        raise HTTPException(status_code=400, detail="No modules found for this version")

    # Generate module files
    user_id = version.user_id or 'default'
    output_dir = settings.OUTPUT_DIR
    generator = ModuleCodeGenerator(output_dir)

    # Generate all code
    try:
        generated = generator.generate_all(hierarchy, version.id, version.name)
    except ValueError as e:
        # Field validation error - return error response
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"代码生成失败: {str(e)}",
                "error_type": "validation_error",
                "error": str(e)
            }
        )

    # Save to files using user-aware paths
    saved = generator.save_all(generated, version.id, version.name, user_id=user_id)

    # Count files by type
    result = {
        "success": True,
        "version_id": version_id,
        "version_name": version.name,
        "generated_files": {
            "ralf": {
                "count": len(saved.get('ralf', [])),
                "files": [Path(f).name for f in saved.get('ralf', [])]
            },
            "header": {
                "count": len(saved.get('header', [])),
                "files": [Path(f).name for f in saved.get('header', [])]
            },
            "svh": {
                "count": len(saved.get('svh', [])),
                "files": [Path(f).name for f in saved.get('svh', [])]
            },
            "rtl": {
                "count": len(saved.get('rtl', [])),
                "files": [Path(f).name for f in saved.get('rtl', [])],
                "note": "RTL is only generated for register modules (not addr_map)"
            }
        },
        "warnings": generator.get_warnings(),
        "errors": generator.get_errors()
    }

    if generator.get_errors():
        raise HTTPException(status_code=500, detail={"errors": generator.get_errors()})

    return result


@router.get("/versions/{version_id}/module-files/{file_type}")
async def list_module_files(
    version_id: int,
    file_type: str,  # ralf, header, svh, rtl
    db: Session = Depends(get_db)
):
    """
    List all generated module files of a specific type
    """
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Map file_type to directory using user-aware path
    version_output_dir = _get_version_output_dir(version)
    dir_map = {
        'ralf': version_output_dir / 'ralf',
        'header': version_output_dir / 'header',
        'svh': version_output_dir / 'svh',
        'uvm': version_output_dir / 'uvm',
        'rtl': version_output_dir / 'rtl',
    }

    if file_type not in dir_map:
        raise HTTPException(status_code=400, detail=f"Invalid file_type. Must be one of: {list(dir_map.keys())}")

    target_dir = dir_map[file_type]

    if not target_dir.exists():
        return {
            "version_id": version_id,
            "version_name": version.name,
            "file_type": file_type,
            "exists": False,
            "files": []
        }

    files = []
    for file_path in target_dir.iterdir():
        if file_path.is_file():
            files.append({
                "filename": file_path.name,
                "module_name": file_path.stem.replace('_reg', ''),  # Remove _reg suffix for RTL
                "size": file_path.stat().st_size,
                "path": str(file_path)
            })

    return {
        "version_id": version_id,
        "version_name": version.name,
        "file_type": file_type,
        "exists": True,
        "file_count": len(files),
        "files": sorted(files, key=lambda x: x["filename"])
    }


@router.get("/versions/{version_id}/module-files/{file_type}/{module_name}")
async def get_module_file_content(
    version_id: int,
    file_type: str,
    module_name: str,
    db: Session = Depends(get_db)
):
    """
    Get the content of a specific module file
    """
    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Map file_type to directory and extension
    type_map = {
        'ralf': ('ralf', '.ralf'),
        'header': ('header', '.h'),
        'svh': ('svh', '.svh'),
        'rtl': ('rtl', '_reg.sv'),
    }

    if file_type not in type_map:
        raise HTTPException(status_code=400, detail=f"Invalid file_type. Must be one of: {list(type_map.keys())}")

    dir_name, ext = type_map[file_type]
    # Use user-aware path
    version_output_dir = _get_version_output_dir(version)
    file_path = version_output_dir / dir_name / f"{module_name}{ext}"

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path.name}")

    # Read file content
    try:
        content = file_path.read_text()
        return {
            "version_id": version_id,
            "version_name": version.name,
            "file_type": file_type,
            "module_name": module_name,
            "filename": file_path.name,
            "content": content
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading file: {e}")


@router.get("/versions/{version_id}/download-module-files/{file_type}")
async def download_module_files_zip(
    version_id: int,
    file_type: str,
    db: Session = Depends(get_db)
):
    """
    Download all module files of a specific type as a ZIP archive
    """
    import zipfile
    import io

    service = VersionService(db)
    version = service.get_version(version_id)

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # Map file_type to directory using user-aware path
    version_output_dir = _get_version_output_dir(version)
    version_dir_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"
    dir_map = {
        'ralf': version_output_dir / 'ralf',
        'header': version_output_dir / 'header',
        'svh': version_output_dir / 'svh',
        'uvm': version_output_dir / 'uvm',
        'rtl': version_output_dir / 'rtl',
    }

    if file_type not in dir_map:
        raise HTTPException(status_code=400, detail=f"Invalid file_type. Must be one of: {list(dir_map.keys())}")

    target_dir = dir_map[file_type]

    if not target_dir.exists():
        raise HTTPException(status_code=404, detail=f"No {file_type} files found")

    # Create ZIP archive
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for file_path in target_dir.iterdir():
            if file_path.is_file():
                zip_file.write(file_path, file_path.name)

    zip_buffer.seek(0)

    from fastapi.responses import StreamingResponse

    # Use version_name in filename
    safe_version_name = _sanitize_filename(version.name) if version.name else f"v{version_id}"
    return StreamingResponse(
        iter([zip_buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={safe_version_name}_{file_type}_files.zip"}
    )


# ============================================================================
# 增量更新API - 支持分批次上传Excel/RALF文件
# ============================================================================

@router.post("/versions/{version_id}/incremental-upload")
async def incremental_upload(
    version_id: int,
    files: List[UploadFile] = File(..., description="Excel files to upload"),
    ralf_file: Optional[UploadFile] = File(None, description="Optional RALF file"),
    db: Session = Depends(get_db)
):
    """
    增量上传文件到已有版本

    功能：
    1. 向已有版本（如abcv1）上传新的Excel或RALF文件
    2. Excel文件处理：
       - 检查addr map页签，在原有版本中查找对应位置
       - 检查register页签，通过名字匹配替换
       - 未匹配的模块生成文件但不合入根状列表
    3. RALF文件处理：
       - 只支持register类型
       - 通过名字匹配替换
       - 未匹配的生成但不合入
    4. 重新生成所有文件（HTML/SVH/RDL等）

    新上传的文件有更高优先级，会替换所有已生成文件。
    """
    from app.services.incremental_update_service import IncrementalUpdateService

    service = VersionService(db)
    version = service.get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # 保存上传的文件到临时目录
    temp_dir = settings.TEMP_DIR / str(uuid.uuid4())
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_paths = []
    ralf_path = None
    try:
        # 保存Excel文件
        for file in files:
            file_path = temp_dir / file.filename
            with open(file_path, "wb") as f:
                shutil.copyfileobj(file.file, f)
            file_paths.append(str(file_path))

        # 保存RALF文件
        if ralf_file:
            ralf_path = temp_dir / ralf_file.filename
            with open(ralf_path, "wb") as f:
                shutil.copyfileobj(ralf_file.file, f)
            ralf_path = str(ralf_path)

        # 使用增量更新服务处理
        incremental_service = IncrementalUpdateService(db)
        result = incremental_service.process_incremental_upload(
            version_id, file_paths, ralf_path
        )

        if not result['success']:
            raise HTTPException(
                status_code=400,
                detail={
                    "errors": result.get('errors', []),
                    "warnings": result.get('warnings', [])
                }
            )

        # 获取更新摘要
        summary = incremental_service.get_update_summary()

        return {
            "success": True,
            "version_id": version_id,
            "version_name": version.name,
            "total_files": len(files),
            "summary": summary,
            "matched_modules": result.get('matched_modules', []),
            "unmatched_modules": result.get('unmatched_modules', []),
            "warnings": result.get('warnings', []),
            "errors": result.get('errors', []),
            "html_url": result.get('generation_results', {}).get('html'),
            "generation_results": {
                "rdl_count": len(result.get('generation_results', {}).get('rdl', [])),
                "svh_count": len(result.get('generation_results', {}).get('svh', [])),
                "header_count": len(result.get('generation_results', {}).get('header', [])),
            }
        }

    finally:
        # 清理临时文件
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


@router.get("/versions/{version_id}/incremental-status")
async def get_incremental_status(
    version_id: int,
    db: Session = Depends(get_db)
):
    """
    获取增量更新状态摘要

    返回：
    - 匹配替换的模块列表
    - 未匹配（生成但不合入）的模块列表
    - 跳过的addr map模块列表
    """
    from app.services.incremental_update_service import IncrementalUpdateService

    version = db.query(Version).filter(Version.id == version_id).first()
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    # 创建服务实例获取摘要（不实际执行更新）
    service = IncrementalUpdateService(db)

    # 获取上次更新的结果（如果有的话）
    # 这里可以扩展为从数据库中存储的上次更新记录

    return {
        "version_id": version_id,
        "version_name": version.name,
        "message": "使用 POST /versions/{id}/incremental-upload 执行增量更新"
    }
