"""
Version service - Business logic for version management
"""
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from pathlib import Path

from app.models.version import Version
from app.models.register import RegisterModule, Register, RegisterField
from app.models.schemas import VersionCreate, RegisterModuleCreate


class VersionService:
    def __init__(self, db: Session):
        self.db = db

    def get_versions(self) -> List[Version]:
        """Get all versions ordered by creation time"""
        return self.db.query(Version).order_by(Version.created_at.desc()).all()

    def get_filtered_versions(self, user: Optional[str] = None) -> List[Version]:
        """Get versions filtered by user and publish status

        Rules:
        - No user param: return only published versions
        - user == 'admin': return ALL versions
        - Regular user: return published OR own versions
        """
        query = self.db.query(Version)
        if not user:
            query = query.filter(Version.is_published == True)
        elif user != 'admin':
            query = query.filter(
                (Version.is_published == True) | (Version.user_id == user)
            )
        return query.order_by(Version.created_at.desc()).all()

    def get_version(self, version_id: int) -> Optional[Version]:
        """Get version by ID with all related data"""
        return self.db.query(Version).options(
            joinedload(Version.modules)
            .joinedload(RegisterModule.registers)
            .joinedload(Register.fields)
        ).filter(Version.id == version_id).first()

    def create_version(self, version_data: VersionCreate) -> Version:
        """Create a new version"""
        version = Version(
            name=version_data.name,
            description=version_data.description,
            user_id=version_data.user_id or 'default',
            is_published=False
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def delete_version(self, version_id: int) -> bool:
        """Delete a version and all its data"""
        version = self.get_version(version_id)
        if not version:
            return False

        self.db.delete(version)
        self.db.commit()
        return True

    def get_version_hierarchy(self, version_id: int) -> Optional[dict]:
        """Get version data as hierarchical dictionary"""
        version = self.get_version(version_id)
        if not version:
            return None

        def module_to_dict(module: RegisterModule, include_submodules: bool = True) -> dict:
            result = {
                'id': module.id,
                'name': module.name,
                'base_address': module.base_address,
                'end_address': module.end_address,
                'size': module.size,
                'is_array': module.is_array,
                'array_count': module.array_count,
                'registers': [
                    {
                        'id': r.id,
                        'name': r.name,
                        'address': r.address,
                        'offset': r.offset,
                        'width': r.width,
                        'description': r.description,
                        'fields': [
                            {
                                'id': f.id,
                                'name': f.name,
                                'bit_range': f.bit_range,
                                'msb': f.msb,
                                'lsb': f.lsb,
                                'access': f.access,
                                'reset_value': f.reset_value,
                                'description': f.description
                            }
                            for f in r.fields
                        ]
                    }
                    for r in module.registers
                ]
            }
            if include_submodules:
                result['submodules'] = [
                    module_to_dict(sub, include_submodules)
                    for sub in (module.submodules or [])
                ]
            return result

        return {
            'id': version.id,
            'name': version.name,
            'description': version.description,
            'created_at': version.created_at.isoformat() if version.created_at else None,
            'updated_at': version.updated_at.isoformat() if version.updated_at else None,
            'warnings': version.warnings or [],  # 返回保存的地址/大小警告
            'top_addrmap_name': version.top_addrmap_name,
            'modules': [
                module_to_dict(m)
                for m in version.modules
                if m.parent_module_id is None  # Only top-level modules
            ]
        }

    def save_hierarchy(self, version_id: int, hierarchy) -> bool:
        """Save hierarchy to database"""
        try:
            # Delete old data
            self.db.query(RegisterModule).filter(
                RegisterModule.version_id == version_id
            ).delete()
            self.db.commit()

            # Save new data
            for module in hierarchy.top_modules:
                self._save_module(version_id, module)

            self.db.commit()
            return True
        except Exception as e:
            print(f"Error saving hierarchy: {e}")
            import traceback
            traceback.print_exc()
            self.db.rollback()
            return False

    def _save_module(self, version_id: int, module, parent_id: int = None):
        """Save a module and its children"""
        db_module = RegisterModule(
            version_id=version_id,
            name=module.name,
            base_address=module.start_addr,
            end_address=module.end_addr,
            size=module.size,
            parent_module_id=parent_id,
            is_array=1 if module.is_array else 0,
            array_count=module.array_count
        )
        self.db.add(db_module)
        self.db.flush()  # Get ID

        # Save registers
        for reg in module.registers:
            db_reg = Register(
                module_id=db_module.id,
                name=reg.name,
                address=module.start_addr + reg.offset,
                offset=reg.offset,
                width=reg.width,
                description=reg.description,
                is_array=1 if reg.is_array else 0,
                array_count=reg.array_count
            )
            self.db.add(db_reg)
            self.db.flush()

            # Save fields
            for field in reg.fields:
                db_field = RegisterField(
                    register_id=db_reg.id,
                    name=field.name,
                    bit_range=f"[{field.msb}:{field.lsb}]",
                    msb=field.msb,
                    lsb=field.lsb,
                    access=field.access,
                    reset_value=field.reset_value,
                    description=field.description
                )
                self.db.add(db_field)

        # Save submodules
        for sub in module.submodules:
            self._save_module(version_id, sub, db_module.id)

    def get_module_by_name(self, version_id: int, module_name: str) -> Optional[RegisterModule]:
        """Get a module by name within a version"""
        return self.db.query(RegisterModule).filter(
            RegisterModule.version_id == version_id,
            RegisterModule.name == module_name
        ).first()

    def export_version_data(self, version_id: int) -> Optional[dict]:
        """Export version data for code generation"""
        hierarchy = self.get_version_hierarchy(version_id)
        if not hierarchy:
            return None

        # Flatten hierarchy for code generators
        def collect_modules(module_list):
            result = []
            for mod in module_list:
                module_data = {
                    'name': mod['name'],
                    'base_address': mod['base_address'],
                    'end_address': mod.get('end_address', mod['base_address']),
                    'size': mod.get('size', 0),
                    'registers': mod.get('registers', [])
                }
                result.append(module_data)
                # Add submodules
                if 'submodules' in mod:
                    result.extend(collect_modules(mod['submodules']))
            return result

        return {
            'version_name': hierarchy['name'],
            'modules': collect_modules(hierarchy.get('modules', []))
        }
