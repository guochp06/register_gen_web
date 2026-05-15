"""
增量更新服务 - 支持分批次上传Excel/RALF文件并合入现有版本

功能：
1. 向已有版本（如abcv1）增量上传新文件
2. Excel文件：检查addr map和register页签，按名字匹配替换
3. RALF文件：只支持register类型，按名字匹配替换
4. 未匹配的模块：报warning，生成文件但不合入根状列表
5. 新文件高优先级，替换所有已生成文件
"""
import shutil
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
from sqlalchemy.orm import Session

from app.services.hierarchy_parser import HierarchyParser, RegisterHierarchy, Module, Register, RegisterField
from app.services.ralf_parser import RALFParser
from app.services.module_code_generator import ModuleCodeGenerator
from app.services.peakrdl_html_service import PeakRDLHTMLService
from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
from app.models.version import Version
from app.models.register import RegisterModule as DBRegisterModule, Register as DBRegister, RegisterField as DBRegisterField
from app.core.config import settings


class ModuleMatchStatus(Enum):
    """模块匹配状态"""
    MATCHED = "matched"           # 匹配成功，替换并合入
    UNMATCHED = "unmatched"       # 未匹配，生成但不合入
    ADDR_MAP_NEW = "addr_map_new" # 新的addr map，不合入


@dataclass
class IncrementalModuleResult:
    """增量更新模块结果"""
    name: str
    source: str  # 'excel' or 'ralf'
    module_type: str  # 'register' or 'addr_map'
    status: ModuleMatchStatus
    original_module: Optional[Module] = None  # 原版本中的模块
    new_module: Optional[Module] = None  # 新上传的模块
    message: str = ""


class IncrementalUpdateService:
    """增量更新服务"""

    def __init__(self, db: Session):
        self.db = db
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.results: List[IncrementalModuleResult] = []

    def process_incremental_upload(
        self,
        version_id: int,
        file_paths: List[str],
        ralf_file: Optional[str] = None
    ) -> Dict:
        """
        处理增量上传

        Args:
            version_id: 现有版本ID
            file_paths: 新上传的Excel文件路径列表
            ralf_file: 可选的RALF文件路径

        Returns:
            {
                'success': bool,
                'hierarchy': RegisterHierarchy,  # 更新后的完整层次结构
                'matched_modules': List[str],    # 匹配并替换的模块
                'unmatched_modules': List[str],  # 未匹配（生成但不合入）
                'warnings': List[str],
                'errors': List[str],
                'generation_results': Dict  # 文件生成结果
            }
        """
        self.warnings = []
        self.errors = []
        self.results = []

        # 1. 获取版本信息和现有层次结构
        version = self.db.query(Version).filter(Version.id == version_id).first()
        if not version:
            return {'success': False, 'errors': ['Version not found']}

        existing_hierarchy = self._load_existing_hierarchy(version_id, version.name)

        # 2. 解析新上传的文件
        new_hierarchy = self._parse_new_files(file_paths, version.name)
        if not new_hierarchy:
            return {'success': False, 'errors': self.errors}

        # 3. 处理RALF文件（如果有）
        if ralf_file:
            self._process_ralf_file(ralf_file, existing_hierarchy)

        # 4. 处理新上传的模块（匹配/未匹配逻辑）
        merged_hierarchy = self._merge_incremental(
            existing_hierarchy,
            new_hierarchy
        )

        # 5. 保存更新后的层次结构到数据库
        self._save_merged_hierarchy(version_id, merged_hierarchy)

        # 6. 重新生成所有文件（包括HTML/SVH/RDL等）
        generation_results = self._regenerate_all_files(
            merged_hierarchy, version_id, version.name
        )

        # 7. 整理结果
        matched = [r.name for r in self.results if r.status == ModuleMatchStatus.MATCHED]
        unmatched = [r.name for r in self.results if r.status == ModuleMatchStatus.UNMATCHED]

        return {
            'success': len(self.errors) == 0,
            'hierarchy': merged_hierarchy,
            'matched_modules': matched,
            'unmatched_modules': unmatched,
            'warnings': self.warnings,
            'errors': self.errors,
            'generation_results': generation_results,
            'results': self.results
        }

    def _load_existing_hierarchy(self, version_id: int, version_name: str) -> RegisterHierarchy:
        """从数据库加载现有层次结构"""
        hierarchy = RegisterHierarchy(version_name=version_name)

        # 加载顶层模块
        db_modules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id,
            DBRegisterModule.parent_module_id.is_(None)
        ).all()

        for db_mod in db_modules:
            module = self._db_to_domain_module(db_mod)
            self._load_submodules_recursive(version_id, module, db_mod.id, hierarchy)
            hierarchy.top_modules.append(module)
            hierarchy.all_modules[module.name] = module

        # 设置顶层addrmap名称
        for mod in hierarchy.top_modules:
            if len(mod.submodules) > 0 and len(mod.registers) == 0:
                hierarchy.top_addrmap_name = mod.name
                break
        if not hierarchy.top_addrmap_name and hierarchy.top_modules:
            hierarchy.top_addrmap_name = hierarchy.top_modules[0].name

        # 加载所有模块
        all_db_modules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id
        ).all()
        for db_mod in all_db_modules:
            if db_mod.name not in hierarchy.all_modules:
                module = self._db_to_domain_module(db_mod)
                hierarchy.all_modules[module.name] = module

        return hierarchy

    def _db_to_domain_module(self, db_mod: DBRegisterModule) -> Module:
        """数据库模块转换为领域模型"""
        registers = []
        for db_reg in db_mod.registers:
            fields = []
            for db_field in db_reg.fields:
                field = RegisterField(
                    name=db_field.name,
                    msb=db_field.msb,
                    lsb=db_field.lsb,
                    access=db_field.access,
                    reset_value=db_field.reset_value or "0",
                    description=db_field.description or ""
                )
                fields.append(field)

            reg = Register(
                name=db_reg.name,
                offset=db_reg.offset,
                width=db_reg.width,
                fields=fields,
                description=db_reg.description or ""
            )
            registers.append(reg)

        return Module(
            name=db_mod.name,
            start_addr=db_mod.base_address,
            end_addr=db_mod.end_address,
            size=db_mod.size,
            registers=registers,
            is_array=bool(db_mod.is_array),
            array_count=db_mod.array_count
        )

    def _load_submodules_recursive(self, version_id: int, parent: Module, parent_db_id: int, hierarchy: RegisterHierarchy):
        """递归加载子模块"""
        db_submodules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id,
            DBRegisterModule.parent_module_id == parent_db_id
        ).all()

        for db_sub in db_submodules:
            sub_module = self._db_to_domain_module(db_sub)
            self._load_submodules_recursive(version_id, sub_module, db_sub.id, hierarchy)
            parent.submodules.append(sub_module)
            hierarchy.all_modules[sub_module.name] = sub_module

    def _parse_new_files(self, file_paths: List[str], version_name: str) -> Optional[RegisterHierarchy]:
        """解析新上传的文件"""
        parser = HierarchyParser()
        hierarchy = parser.parse_files(file_paths, version_name)

        if hierarchy.errors:
            self.errors.extend(hierarchy.errors)
            return None

        self.warnings.extend(hierarchy.warnings)
        return hierarchy

    def _process_ralf_file(self, ralf_file: str, existing_hierarchy: RegisterHierarchy):
        """处理RALF文件 - RALF只可能是register类型"""
        try:
            parser = RALFParser()
            ralf_module = parser.parse_file(ralf_file)

            if not ralf_module:
                self.warnings.append(f"RALF文件解析失败: {ralf_file}")
                return

            # 提取RALF中的所有模块（可能有多个）
            ralf_modules = self._extract_ralf_modules(ralf_module)

            for ralf_mod in ralf_modules:
                module_name = ralf_mod.name

                # 在现有层次结构中查找匹配的模块
                matched_module = self._find_module_in_hierarchy(
                    module_name, existing_hierarchy
                )

                if matched_module:
                    # 匹配成功 - 用RALF内容替换
                    self._replace_module_with_ralf(matched_module, ralf_mod)
                    self.results.append(IncrementalModuleResult(
                        name=module_name,
                        source='ralf',
                        module_type='register',
                        status=ModuleMatchStatus.MATCHED,
                        original_module=matched_module,
                        new_module=self._ralf_to_domain_module(ralf_mod),
                        message=f"RALF模块 '{module_name}' 匹配成功，已替换"
                    ))
                else:
                    # 未匹配 - 记录但不合入
                    new_mod = self._ralf_to_domain_module(ralf_mod)
                    self.results.append(IncrementalModuleResult(
                        name=module_name,
                        source='ralf',
                        module_type='register',
                        status=ModuleMatchStatus.UNMATCHED,
                        new_module=new_mod,
                        message=f"RALF模块 '{module_name}' 未在现有版本中找到匹配"
                    ))
                    self.warnings.append(
                        f"RALF模块 '{module_name}' 未找到匹配位置，将生成文件但不合入层次结构"
                    )

        except Exception as e:
            self.errors.append(f"RALF文件处理失败: {str(e)}")

    def _extract_ralf_modules(self, ralf_module) -> List:
        """从RALF结构中提取所有模块"""
        modules = []

        def collect(mod):
            modules.append(mod)
            for sub in getattr(mod, 'submodules', []):
                collect(sub)

        collect(ralf_module)
        return modules

    def _find_module_in_hierarchy(self, module_name: str, hierarchy: RegisterHierarchy) -> Optional[Module]:
        """在层次结构中查找模块"""
        # 先检查all_modules
        if module_name in hierarchy.all_modules:
            return hierarchy.all_modules[module_name]

        # 递归搜索子模块
        for top_mod in hierarchy.top_modules:
            found = self._find_in_submodules(module_name, top_mod)
            if found:
                return found

        return None

    def _find_in_submodules(self, module_name: str, parent: Module) -> Optional[Module]:
        """在子模块中递归查找"""
        for sub in parent.submodules:
            if sub.name == module_name:
                return sub
            found = self._find_in_submodules(module_name, sub)
            if found:
                return found
        return None

    def _ralf_to_domain_module(self, ralf_mod) -> Module:
        """将RALF模块转换为领域模型"""
        registers = []
        for reg in getattr(ralf_mod, 'registers', []):
            fields = []
            for field in getattr(reg, 'fields', []):
                f = RegisterField(
                    name=getattr(field, 'name', 'field'),
                    msb=getattr(field, 'msb', 0),
                    lsb=getattr(field, 'lsb', 0),
                    access=getattr(field, 'access', 'RW'),
                    reset_value=getattr(field, 'reset_value', '0'),
                    description=getattr(field, 'description', '')
                )
                fields.append(f)

            r = Register(
                name=getattr(reg, 'name', 'reg'),
                offset=getattr(reg, 'offset', 0),
                width=getattr(reg, 'width', 32),
                fields=fields
            )
            registers.append(r)

        return Module(
            name=ralf_mod.name,
            start_addr=getattr(ralf_mod, 'start_addr', 0),
            end_addr=getattr(ralf_mod, 'end_addr', 0),
            size=getattr(ralf_mod, 'size', 0),
            registers=registers
        )

    def _replace_module_with_ralf(self, existing_module: Module, ralf_mod):
        """用RALF模块内容替换现有模块的寄存器定义"""
        # 保留地址信息，替换寄存器
        existing_module.registers = []
        for reg in getattr(ralf_mod, 'registers', []):
            fields = []
            for field in getattr(reg, 'fields', []):
                f = RegisterField(
                    name=getattr(field, 'name', 'field'),
                    msb=getattr(field, 'msb', 0),
                    lsb=getattr(field, 'lsb', 0),
                    access=getattr(field, 'access', 'RW'),
                    reset_value=getattr(field, 'reset_value', '0'),
                    description=getattr(field, 'description', '')
                )
                fields.append(f)

            r = Register(
                name=getattr(reg, 'name', 'reg'),
                offset=getattr(reg, 'offset', 0),
                width=getattr(reg, 'width', 32),
                fields=fields
            )
            existing_module.registers.append(r)

    def _merge_incremental(
        self,
        existing_hierarchy: RegisterHierarchy,
        new_hierarchy: RegisterHierarchy
    ) -> RegisterHierarchy:
        """
        合并增量上传的模块

        处理逻辑：
        1. 新模块是addr_map类型：
           - 在现有层次结构中查找对应位置
           - 未找到 → warning，不生成代码，标记为ADDR_MAP_NEW
        2. 新模块是register类型：
           - 通过名字匹配查找对应位置
           - 匹配 → 替换寄存器定义，保留地址
           - 未匹配 → warning，生成文件但不合入，标记为UNMATCHED
        """
        merged = RegisterHierarchy(version_name=existing_hierarchy.version_name)
        merged.top_addrmap_name = existing_hierarchy.top_addrmap_name
        merged.top_modules = existing_hierarchy.top_modules.copy()
        merged.all_modules = existing_hierarchy.all_modules.copy()

        for name, new_module in new_hierarchy.all_modules.items():
            # 判断模块类型
            has_submodules = len(new_module.submodules) > 0
            has_registers = len(new_module.registers) > 0

            if has_submodules and not has_registers:
                # addr_map类型
                module_type = 'addr_map'
            elif has_registers:
                # register类型
                module_type = 'register'
            else:
                # 空模块，跳过
                continue

            # 在现有层次结构中查找同名模块
            existing_module = merged.all_modules.get(name)

            if existing_module:
                # 匹配成功
                if module_type == 'register':
                    # register类型：替换寄存器定义，保留地址信息
                    existing_module.registers = new_module.registers
                    self.results.append(IncrementalModuleResult(
                        name=name,
                        source='excel',
                        module_type='register',
                        status=ModuleMatchStatus.MATCHED,
                        original_module=existing_module,
                        new_module=new_module,
                        message=f"Register模块 '{name}' 匹配成功，已替换寄存器定义"
                    ))
                elif module_type == 'addr_map':
                    # addr_map类型：更新子模块结构
                    existing_module.submodules = new_module.submodules
                    self.results.append(IncrementalModuleResult(
                        name=name,
                        source='excel',
                        module_type='addr_map',
                        status=ModuleMatchStatus.MATCHED,
                        original_module=existing_module,
                        new_module=new_module,
                        message=f"AddrMap模块 '{name}' 匹配成功，已更新子模块"
                    ))
            else:
                # 未匹配
                if module_type == 'addr_map':
                    # addr_map未匹配 → 不生成代码
                    self.results.append(IncrementalModuleResult(
                        name=name,
                        source='excel',
                        module_type='addr_map',
                        status=ModuleMatchStatus.ADDR_MAP_NEW,
                        new_module=new_module,
                        message=f"AddrMap模块 '{name}' 未在现有版本中找到匹配，不生成代码"
                    ))
                    self.warnings.append(
                        f"AddrMap模块 '{name}' 未找到匹配位置，跳过此模块"
                    )
                else:
                    # register未匹配 → 生成但不合入
                    self.results.append(IncrementalModuleResult(
                        name=name,
                        source='excel',
                        module_type='register',
                        status=ModuleMatchStatus.UNMATCHED,
                        new_module=new_module,
                        message=f"Register模块 '{name}' 未在现有版本中找到匹配"
                    ))
                    self.warnings.append(
                        f"Register模块 '{name}' 未找到匹配位置，将生成文件但不合入层次结构"
                    )

        return merged

    def _regenerate_all_files(
        self,
        hierarchy: RegisterHierarchy,
        version_id: int,
        version_name: str
    ) -> Dict:
        """重新生成所有文件"""
        results = {
            'rdl': [],
            'svh': [],
            'header': [],
            'html': None,
            'errors': [],
            'warnings': []
        }

        # 生成输出目录
        version_dir_name = self._sanitize_filename(version_name)
        version = self.db.query(Version).filter(Version.id == version_id).first()
        user_id = version.user_id or 'default' if version else 'default'
        output_base = settings.OUTPUT_DIR / user_id / version_dir_name

        # 1. 生成模块代码（RDL/SVH/Header）
        generator = ModuleCodeGenerator(settings.OUTPUT_DIR)

        # 生成所有模块（包括匹配和未匹配的）
        all_modules_to_generate = []

        # 添加层次结构中的模块
        for name, mod in hierarchy.all_modules.items():
            all_modules_to_generate.append((name, mod, True))  # (name, module, is_in_hierarchy)

        # 添加未匹配的模块（生成但不合入）
        for result in self.results:
            if result.status == ModuleMatchStatus.UNMATCHED and result.new_module:
                all_modules_to_generate.append((result.name, result.new_module, False))

        # 为每个模块生成代码
        for name, mod, in_hierarchy in all_modules_to_generate:
            try:
                # 创建单个模块的临时层次结构用于生成
                temp_hierarchy = RegisterHierarchy(version_name=version_name)
                temp_hierarchy.all_modules[name] = mod
                temp_hierarchy.top_modules = [mod]

                generated = generator.generate_all(temp_hierarchy, version_id, version_name)
                saved = generator.save_all(generated, version_id, version_name, user_id=user_id)

                # 记录生成的文件
                for fmt, files in saved.items():
                    if fmt in results and files:
                        results[fmt].extend(files)

            except Exception as e:
                results['errors'].append(f"生成模块 '{name}' 失败: {str(e)}")

        # 2. 生成HTML（只包含层次结构中的模块）
        try:
            html_service = PeakRDLHTMLService()
            html_result = html_service.generate_html(
                hierarchy,
                version_id=version_id,
                version_name=version_name,
                user_id=user_id
            )

            if html_result['success']:
                results['html'] = html_result.get('html_url')
                # 更新版本HTML路径
                if version:
                    version.html_path = html_result.get('html_path')
                    self.db.commit()
            else:
                results['errors'].append(f"HTML生成失败: {html_result.get('errors', [])}")

        except Exception as e:
            results['errors'].append(f"HTML生成异常: {str(e)}")

        return results

    def _save_merged_hierarchy(self, version_id: int, hierarchy: RegisterHierarchy):
        """保存合并后的层次结构到数据库"""
        # 删除旧数据
        self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id
        ).delete()
        self.db.commit()

        # 保存模块（只保存合入层次结构的模块）
        saved_ids = {}

        # 保存顶层模块及其子模块链
        for module in hierarchy.top_modules:
            self._save_module_recursive(version_id, module, saved_ids)

        # 注意：不在top_modules子模块链中的模块（未实例化）不应该被保存
        # 或者如果需要保存，应该用特殊标记（如parent_id=-1）
        # 这里我们跳过它们，让数据库保持一致性

        self.db.commit()

    def _save_module_recursive(
        self,
        version_id: int,
        module: Module,
        saved_ids: Dict[str, int],
        is_top_level: bool = False,
        parent_id: int = None
    ):
        """递归保存模块"""
        if module.name in saved_ids:
            return

        db_mod = DBRegisterModule(
            version_id=version_id,
            name=module.name,
            base_address=module.start_addr,
            end_address=module.end_addr,
            size=module.size,
            parent_module_id=parent_id,
            is_array=1 if module.is_array else 0,
            array_count=module.array_count
        )
        self.db.add(db_mod)
        self.db.flush()
        saved_ids[module.name] = db_mod.id

        # 保存寄存器
        for reg in module.registers:
            db_reg = DBRegister(
                module_id=db_mod.id,
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

            # 保存字段
            for field in reg.fields:
                db_field = DBRegisterField(
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

        # 保存子模块
        for sub in module.submodules:
            self._save_module_recursive(version_id, sub, saved_ids, parent_id=db_mod.id)

    def _sanitize_filename(self, name: str) -> str:
        """清理文件名中的非法字符"""
        import re
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    def get_update_summary(self) -> Dict:
        """获取更新摘要"""
        matched = [r for r in self.results if r.status == ModuleMatchStatus.MATCHED]
        unmatched = [r for r in self.results if r.status == ModuleMatchStatus.UNMATCHED]
        addr_map_new = [r for r in self.results if r.status == ModuleMatchStatus.ADDR_MAP_NEW]

        return {
            'total_processed': len(self.results),
            'matched_count': len(matched),
            'unmatched_count': len(unmatched),
            'addr_map_new_count': len(addr_map_new),
            'matched_modules': [{'name': r.name, 'type': r.module_type} for r in matched],
            'unmatched_modules': [{'name': r.name, 'type': r.module_type, 'message': r.message} for r in unmatched],
            'skipped_modules': [{'name': r.name, 'message': r.message} for r in addr_map_new]
        }
