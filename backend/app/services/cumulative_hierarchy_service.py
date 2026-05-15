"""
累积式层次结构服务 - 支持多批次上传合并
"""
import shutil
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Set
from sqlalchemy.orm import Session, joinedload

from app.services.hierarchy_parser import HierarchyParser, RegisterHierarchy, Module, Register, RegisterField
from app.services.ralf_parser import RALFParser
from app.services.peakrdl_rdl_generator import PeakRDLCompatibleRDLGenerator
from app.models.version import Version
from app.models.register import RegisterModule as DBRegisterModule, Register as DBRegister, RegisterField as DBRegisterField
from app.core.config import settings


class UninstantiatedModule:
    """未例化的模块信息"""
    def __init__(self, name: str, source: str, reason: str):
        self.name = name
        self.source = source  # 'excel' or 'ralf'
        self.reason = reason
        self.registers: List[Register] = []
        self.start_addr: int = 0


class CumulativeHierarchyService:
    """累积式层次结构管理服务"""

    def __init__(self, db: Session):
        self.db = db
        self.warnings: List[str] = []
        self.errors: List[str] = []
        self.uninstantiated_modules: List[UninstantiatedModule] = []

    def calculate_merged_hierarchy(self, version_id: int, file_paths: List[str],
                                    ralf_file: Optional[str] = None) -> Dict:
        """
        计算合并后的层次结构（不保存到数据库）

        Returns:
            {
                'success': bool,
                'hierarchy': RegisterHierarchy,
                'uninstantiated': List[UninstantiatedModule],
                'warnings': List[str],
                'errors': List[str]
            }
        """
        self.warnings = []
        self.errors = []
        self.uninstantiated_modules = []

        # 1. 加载数据库中已有的模块
        existing_modules = self._load_existing_modules(version_id)

        # 2. 解析新上传的文件
        parser = HierarchyParser()
        version = self.db.query(Version).filter(Version.id == version_id).first()
        if not version:
            return {'success': False, 'errors': ['Version not found']}

        new_hierarchy = parser.parse_files(file_paths, version.name)
        if new_hierarchy.errors:
            self.errors.extend(new_hierarchy.errors)
            return {'success': False, 'errors': self.errors}

        self.warnings.extend(new_hierarchy.warnings)

        # 3. 处理 RALF 文件（如果有）
        ralf_structure = None
        if ralf_file:
            ralf_structure = self._parse_ralf_structure(ralf_file)
            if ralf_structure:
                # RALF 提供了完整结构，以其为基准
                merged_hierarchy = self._merge_with_ralf_structure(
                    existing_modules, new_hierarchy, ralf_structure
                )
            else:
                # RALF 解析失败，使用 Excel 结构
                self.warnings.append(f"RALF 文件解析失败，使用 Excel 结构")
                merged_hierarchy = self._merge_hierarchies(existing_modules, new_hierarchy)
        else:
            # 没有 RALF，合并现有模块和新模块
            merged_hierarchy = self._merge_hierarchies(existing_modules, new_hierarchy)

        # 4. 尝试将未例化的模块自动插入到合适位置
        self._try_instantiate_orphan_modules(merged_hierarchy)

        return {
            'success': True,
            'hierarchy': merged_hierarchy,
            'uninstantiated': self.uninstantiated_modules,
            'warnings': self.warnings,
            'errors': self.errors
        }

    def save_hierarchy(self, version_id: int, hierarchy: RegisterHierarchy,
                       warnings: List[str] = None) -> bool:
        """
        保存层次结构到数据库

        Args:
            version_id: 版本ID
            hierarchy: 层次结构
            warnings: 需要保存的警告列表

        Returns:
            bool: 是否保存成功
        """
        try:
            # 保存到数据库
            self._save_merged_hierarchy(version_id, hierarchy)

            # 保存warnings到版本
            if warnings:
                self._save_version_warnings(version_id, warnings)

            return True
        except Exception as e:
            self.db.rollback()
            print(f"Error saving hierarchy: {e}")
            import traceback
            traceback.print_exc()
            return False

    def process_upload(self, version_id: int, file_paths: List[str],
                       ralf_file: Optional[str] = None) -> Dict:
        """
        处理上传的文件，合并到现有版本（包含保存到数据库）
        注意：这是旧的方法，新代码应该使用 calculate_merged_hierarchy + save_hierarchy 组合

        Returns:
            {
                'success': bool,
                'hierarchy': RegisterHierarchy,
                'uninstantiated': List[UninstantiatedModule],
                'warnings': List[str],
                'errors': List[str]
            }
        """
        # 计算合并后的层次结构
        result = self.calculate_merged_hierarchy(version_id, file_paths, ralf_file)

        if not result['success']:
            return result

        # 保存到数据库
        hierarchy = result['hierarchy']
        if self.save_hierarchy(version_id, hierarchy, result['warnings']):
            return result
        else:
            return {
                'success': False,
                'errors': ['Failed to save hierarchy to database'],
                'warnings': result['warnings']
            }

    def _load_existing_modules(self, version_id: int) -> Dict[str, Module]:
        """从数据库加载已有模块，包括子模块关系"""
        modules = {}

        db_modules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id
        ).all()

        # 第一遍：创建所有模块
        for db_mod in db_modules:
            module = self._db_module_to_domain(db_mod)
            # 恢复 is_array_instance 和 base_module_name
            # 注意：数组实例的 is_array 在数据库中可能为 0，所以需要通过名称推断
            base_name = self._extract_base_module_name(db_mod.name)
            if base_name and base_name in [m.name for m in db_modules] and base_name != db_mod.name:
                module.is_array_instance = True
                module.base_module_name = base_name
            modules[module.name] = module

        # 第二遍：建立子模块关系
        for db_mod in db_modules:
            if db_mod.parent_module_id:
                # 找到父模块
                parent_db = self.db.query(DBRegisterModule).filter(
                    DBRegisterModule.id == db_mod.parent_module_id,
                    DBRegisterModule.version_id == version_id
                ).first()

                if parent_db and parent_db.name in modules:
                    parent_module = modules[parent_db.name]
                    child_module = modules.get(db_mod.name)
                    # 跳过数组基模块作为子模块的添加
                    # 基模块（如 PEC, C2C）虽然在数据库中是 soc_addr_map 的子模块，
                    # 但在层次结构中不应该作为子模块出现（数组实例如 PEC0 才是）
                    if child_module:
                        # 检查是否为基模块（被数组实例引用）
                        is_base_module = any(
                            getattr(m, 'base_module_name', None) == child_module.name
                            for m in modules.values()
                        )
                        if not is_base_module and child_module not in parent_module.submodules:
                            parent_module.submodules.append(child_module)

        # 第三遍：为基础模块复制子模块关系
        # 基础模块（如 PEC）需要从其数组实例（如 PEC0）复制子模块
        for name, module in list(modules.items()):
            if getattr(module, 'is_array_instance', False):
                base_name = getattr(module, 'base_module_name', None)
                if base_name and base_name in modules:
                    base_module = modules[base_name]
                    # 如果基础模块没有子模块，从数组实例复制
                    if not base_module.submodules and module.submodules:
                        for sub in module.submodules:
                            if sub not in base_module.submodules:
                                base_module.submodules.append(sub)

        return modules

    def _extract_base_module_name(self, name: str) -> Optional[str]:
        """从数组实例名称提取基础模块名称（如 PEC0 -> PEC, C2C0 -> C2C）"""
        import re
        # 尝试匹配末尾的数字（如 PEC0 -> PEC, DRAM_IF_1 -> DRAM_IF）
        match = re.match(r'^(.+?)(?:_)?(\d+)$', name)
        if match:
            base = match.group(1)
            # 如果基础模块名称以 _ 结尾，去掉它
            if base.endswith('_'):
                base = base[:-1]
            return base
        return None

    def _db_module_to_domain(self, db_mod: DBRegisterModule, parent_base_addr: int = 0) -> Module:
        """将数据库模块转换为领域模型

        Args:
            db_mod: 数据库模块对象
            parent_base_addr: 父模块的基地址，用于计算正确的绝对地址
        """
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

        # 数据库中存储的是绝对地址，直接使用
        actual_start_addr = db_mod.base_address
        actual_end_addr = db_mod.end_address

        return Module(
            name=db_mod.name,
            start_addr=actual_start_addr,
            end_addr=actual_end_addr,
            size=db_mod.size,
            registers=registers,
            is_array=bool(db_mod.is_array),
            array_count=db_mod.array_count
        )

    def _parse_ralf_structure(self, ralf_file: str) -> Optional[Dict]:
        """解析 RALF 文件获取结构信息"""
        try:
            parser = RALFParser()
            root_module = parser.parse_file(ralf_file)

            if not root_module:
                return None

            # 找到 RALF 中的最顶层（不被任何其他模块引用的）
            all_modules = {}
            referenced = set()

            def collect_modules(mod, parent_name=None):
                all_modules[mod.name] = mod
                if parent_name:
                    referenced.add(mod.name)
                for sub in getattr(mod, 'submodules', []):
                    collect_modules(sub, mod.name)

            collect_modules(root_module)

            # 找到顶层（不被引用的）
            top_candidates = [name for name in all_modules if name not in referenced]
            top_name = top_candidates[0] if top_candidates else root_module.name

            return {
                'top_name': top_name,
                'modules': all_modules,
                'root': root_module
            }
        except Exception as e:
            self.warnings.append(f"RALF 解析错误: {str(e)}")
            return None

    def _merge_with_ralf_structure(self, existing_modules: Dict[str, Module],
                                   new_hierarchy: RegisterHierarchy,
                                   ralf_structure: Dict) -> RegisterHierarchy:
        """将模块合并到 RALF 结构中"""
        ralf_top = ralf_structure['top_name']
        ralf_modules = ralf_structure['modules']

        # 合并所有模块
        all_modules = {**existing_modules}

        # 首先添加 RALF 中的模块（它们包含寄存器定义）
        for name, mod in ralf_modules.items():
            # 从 RALF 构建领域模型（包含寄存器）
            # _build_from_ralf_module 会合并到现有模块（如果存在）
            domain_mods = self._build_from_ralf_module(mod, all_modules, set())
            for domain_mod in domain_mods:
                if domain_mod.name not in all_modules:
                    all_modules[domain_mod.name] = domain_mod

        # 然后添加 Excel 中的模块
        # RALF 模块优先（特别是有寄存器的模块），除非 Excel 模块也有寄存器
        for name, mod in new_hierarchy.all_modules.items():
            if name in all_modules:
                existing = all_modules[name]
                # 如果现有模块（RALF）有寄存器，而新模块（Excel）没有，保留现有模块
                if existing.registers and not mod.registers:
                    # 保留 RALF 的寄存器定义，但保留 Excel 的地址信息
                    # RALF 中的地址是相对偏移（通常是0x0），实际地址在 Excel 中定义
                    if not existing.registers and mod.registers:
                        existing.registers = mod.registers
                    # 不更新地址，保留 Excel 中的实际地址
                    continue
                # 否则，新模块覆盖旧模块
                self.warnings.append(f"模块 '{name}' 已存在，使用新版本")
            all_modules[name] = mod

        # 尝试将 Excel 模块插入到 RALF 结构中
        for name, mod in all_modules.items():
            if name not in ralf_modules:
                # 这是一个新模块，尝试找到插入位置
                inserted = self._try_insert_module_to_ralf(mod, ralf_structure)
                if not inserted:
                    # 无法插入，标记为未例化
                    self._add_uninstantiated_module(mod, "无法在 RALF 结构中找到合适的例化位置")

        # 构建最终层次结构
        merged = RegisterHierarchy(version_name=new_hierarchy.version_name)
        # Excel的addr_map作为顶层，RALF模块作为子模块
        # 如果没有Excel（只有RALF），从existing_modules中恢复top_addrmap_name
        if new_hierarchy.top_addrmap_name:
            merged.top_addrmap_name = new_hierarchy.top_addrmap_name
        else:
            # 优先查找 soc_addr_map 作为顶层
            if 'soc_addr_map' in existing_modules:
                soc_mod = existing_modules['soc_addr_map']
                if soc_mod.submodules and len(soc_mod.registers) == 0:
                    merged.top_addrmap_name = 'soc_addr_map'

            # 如果没有找到 soc_addr_map，再查找其他顶层addrmap
            if not merged.top_addrmap_name:
                for name, mod in existing_modules.items():
                    if mod.submodules and len(mod.registers) == 0:
                        # 这是一个addrmap类型模块，检查它是否有父模块
                        has_parent = False
                        for other in existing_modules.values():
                            if other.submodules:
                                for sub in other.submodules:
                                    if sub.name == name:
                                        has_parent = True
                                        break
                            if has_parent:
                                break
                        if not has_parent:
                            merged.top_addrmap_name = name
                            break

            # 如果没找到，使用第一个没有父模块的模块
            if not merged.top_addrmap_name:
                for name, mod in existing_modules.items():
                    is_top = True
                    for other in existing_modules.values():
                        if other.submodules:
                            for sub in other.submodules:
                                if sub.name == name:
                                    is_top = False
                                    break
                        if not is_top:
                            break
                    if is_top:
                        merged.top_addrmap_name = name
                        break

        merged.all_modules = all_modules

        # 关键步骤：将 RALF 基模块的寄存器定义传播到所有数组实例
        # 例如：L2B（RALF）的寄存器定义 -> L2B_0, L2B_1, ...（Excel实例）
        for name, mod in list(all_modules.items()):
            # 检查是否为基模块（被数组实例引用）
            base_name = getattr(mod, 'base_module_name', None)
            if not base_name and mod.registers:
                # 这是一个有寄存器的基模块，查找其数组实例
                for other_name, other_mod in all_modules.items():
                    if getattr(other_mod, 'base_module_name', None) == name:
                        # other_mod 是 mod 的数组实例，复制寄存器定义
                        if not other_mod.registers:
                            other_mod.registers = mod.registers

        # 从Excel顶层构建层次（RALF模块已经被添加到all_modules中）
        if new_hierarchy.top_modules:
            merged.top_modules = []
            for top_mod in new_hierarchy.top_modules:
                # 使用all_modules中的版本（包含RALF寄存器定义）
                if top_mod.name in all_modules:
                    merged_top = all_modules[top_mod.name]
                    # 递归更新子模块为all_modules中的版本（确保实例有正确的寄存器）
                    self._update_submodules_from_all(merged_top, all_modules)
                    merged.top_modules.append(merged_top)
                else:
                    merged.top_modules.append(top_mod)
        else:
            # 如果没有Excel文件（只有RALF），从existing_modules恢复顶层结构
            # 找到顶层模块（parent_id=None的模块）
            for name, mod in existing_modules.items():
                # 检查是否为顶层模块（在existing_modules中但不被其他模块引用）
                is_top = True
                for other_mod in existing_modules.values():
                    if hasattr(other_mod, 'submodules') and other_mod.submodules:
                        for sub in other_mod.submodules:
                            if sub.name == name:
                                is_top = False
                                break
                    if not is_top:
                        break

                if is_top and name in all_modules:
                    # 深拷贝模块，避免修改原始模块
                    source_mod = all_modules[name]
                    merged_top = self._deep_copy_module(source_mod)
                    self._update_submodules_from_all(merged_top, all_modules)
                    merged.top_modules.append(merged_top)

            # 如果没有找到顶层模块，尝试使用之前的top_addrmap_name
            if not merged.top_modules and merged.top_addrmap_name:
                if merged.top_addrmap_name in all_modules:
                    source_mod = all_modules[merged.top_addrmap_name]
                    merged.top_modules = [self._deep_copy_module(source_mod)]

        return merged

    def _deep_copy_module(self, module: Module) -> Module:
        """深拷贝模块"""
        from app.services.hierarchy_parser import Register, RegisterField

        # 深拷贝寄存器
        copied_regs = []
        for reg in module.registers:
            copied_fields = [
                RegisterField(
                    name=f.name, msb=f.msb, lsb=f.lsb,
                    access=f.access, reset_value=f.reset_value,
                    description=f.description
                ) for f in reg.fields
            ]
            copied_regs.append(Register(
                name=reg.name, offset=reg.offset, width=reg.width,
                fields=copied_fields, description=reg.description
            ))

        # 深拷贝子模块（但不递归，子模块将在_update_submodules_from_all中处理）
        copied_subs = []
        for sub in module.submodules:
            copied_subs.append(Module(
                name=sub.name,
                start_addr=sub.start_addr,
                end_addr=sub.end_addr,
                size=sub.size,
                registers=[],  # 子模块的寄存器将在_update_submodules_from_all中填充
                submodules=[],
                is_array=sub.is_array,
                array_count=sub.array_count,
                source_file=sub.source_file,
                description=sub.description,
                is_array_instance=getattr(sub, 'is_array_instance', False),
                base_module_name=getattr(sub, 'base_module_name', None)
            ))

        return Module(
            name=module.name,
            start_addr=module.start_addr,
            end_addr=module.end_addr,
            size=module.size,
            registers=copied_regs,
            submodules=copied_subs,
            is_array=module.is_array,
            array_count=module.array_count,
            source_file=module.source_file,
            description=module.description,
            is_array_instance=getattr(module, 'is_array_instance', False),
            base_module_name=getattr(module, 'base_module_name', None)
        )

    def _update_submodules_from_all(self, module: Module, all_modules: Dict[str, Module], visited: set = None):
        """递归更新模块的子模块为all_modules中的版本（确保实例有正确的寄存器）

        Args:
            module: 当前模块
            all_modules: 所有模块的字典
            visited: 已访问模块集合（防止循环引用）
        """
        from app.services.hierarchy_parser import Module, Register, RegisterField

        if visited is None:
            visited = set()

        # 防止循环引用
        if module.name in visited:
            return
        visited.add(module.name)

        for i, sub in enumerate(module.submodules):
            if sub.name in all_modules:
                # 获取all_modules中的版本（可能有RALF寄存器）
                source_mod = all_modules[sub.name]

                # 检查是否需要从基模块复制寄存器（针对数组实例）
                base_name = getattr(sub, 'base_module_name', None)
                registers = source_mod.registers

                if base_name and base_name in all_modules and not registers:
                    # 从基模块复制寄存器
                    base_module = all_modules[base_name]
                    if base_module.registers:
                        copied_regs = []
                        for reg in base_module.registers:
                            copied_fields = [
                                RegisterField(
                                    name=f.name, msb=f.msb, lsb=f.lsb,
                                    access=f.access, reset_value=f.reset_value,
                                    description=f.description
                                ) for f in reg.fields
                            ]
                            copied_regs.append(Register(
                                name=reg.name, offset=reg.offset, width=reg.width,
                                fields=copied_fields, description=reg.description
                            ))
                        registers = copied_regs

                # 深拷贝子模块列表，避免修改原始模块
                copied_submodules = []
                for s in source_mod.submodules:
                    copied_submodules.append(Module(
                        name=s.name,
                        start_addr=s.start_addr,
                        end_addr=s.end_addr,
                        size=s.size,
                        registers=s.registers.copy() if s.registers else [],
                        submodules=[],  # 子模块将在递归中处理
                        is_array=s.is_array,
                        array_count=s.array_count,
                        source_file=s.source_file,
                        description=s.description,
                        is_array_instance=getattr(s, 'is_array_instance', False),
                        base_module_name=getattr(s, 'base_module_name', None)
                    ))

                # 创建新模块，保留原始地址但使用all_modules中的寄存器
                updated_sub = Module(
                    name=sub.name,
                    start_addr=sub.start_addr,
                    end_addr=sub.end_addr,
                    size=sub.size,
                    registers=registers,
                    submodules=copied_submodules,
                    is_array=sub.is_array,
                    array_count=sub.array_count,
                    source_file=sub.source_file,
                    description=sub.description,
                    is_array_instance=getattr(sub, 'is_array_instance', False),
                    base_module_name=base_name
                )
                module.submodules[i] = updated_sub

            # 递归处理子模块的子模块
            self._update_submodules_from_all(module.submodules[i], all_modules, visited.copy())

    def _try_insert_module_to_ralf(self, module: Module, ralf_structure: Dict) -> bool:
        """尝试将模块插入到 RALF 结构中的合适位置"""
        # 策略：根据地址范围匹配
        # 如果模块的地址范围与 RALF 中某个模块的子模块范围匹配，则插入

        ralf_modules = ralf_structure['modules']

        for ralf_name, ralf_mod in ralf_modules.items():
            ralf_start = getattr(ralf_mod, 'start_addr', 0)
            ralf_end = getattr(ralf_mod, 'end_addr', ralf_start + 0x1000)

            # 检查是否完全匹配
            if (module.start_addr == ralf_start and
                module.end_addr == ralf_end and
                module.name != ralf_name):
                # 地址匹配但名称不同，可能是同一模块的不同版本
                # 作为子模块插入
                if not hasattr(ralf_mod, 'submodules'):
                    ralf_mod.submodules = []

                # 检查是否已存在
                exists = any(sub.name == module.name for sub in ralf_mod.submodules)
                if not exists:
                    ralf_mod.submodules.append(module)
                    return True

        return False

    def _build_from_ralf_module(self, ralf_mod, all_modules: Dict[str, Module], visited: set = None) -> List[Module]:
        """从 RALF 模块构建领域模型"""
        if visited is None:
            visited = set()

        modules = []

        # 获取基础信息
        name = ralf_mod.name

        # 防止循环引用
        if name in visited:
            return modules
        visited.add(name)

        start_addr = getattr(ralf_mod, 'start_addr', 0)

        # 从 RALF 中提取寄存器定义
        ralf_registers = []
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
            ralf_registers.append(r)

        # 如果模块已存在（来自Excel），合并寄存器定义，保留Excel的地址
        if name in all_modules:
            domain_mod = all_modules[name]
            # 添加 RALF 寄存器（如果模块没有寄存器）
            if ralf_registers and not domain_mod.registers:
                domain_mod.registers = ralf_registers
            # 重要：保留Excel中的地址，不要使用RALF的0x0地址
            # RALF中的地址是相对偏移，实际地址已在Excel中定义
        else:
            # 使用 RALF 中的信息创建模块（新模块）
            domain_mod = Module(
                name=name,
                start_addr=start_addr,
                end_addr=getattr(ralf_mod, 'end_addr', start_addr + 0x1000),
                size=getattr(ralf_mod, 'size', 0x1000),
                registers=ralf_registers
            )

        # 处理子模块
        for sub in getattr(ralf_mod, 'submodules', []):
            sub_modules = self._build_from_ralf_module(sub, all_modules, visited.copy())
            domain_mod.submodules.extend(sub_modules)

        modules.append(domain_mod)
        return modules

    def _merge_hierarchies(self, existing: Dict[str, Module],
                           new: RegisterHierarchy) -> RegisterHierarchy:
        """合并两个层次结构"""
        merged = RegisterHierarchy(version_name=new.version_name)

        # 合并模块
        all_modules = {**existing}
        for name, mod in new.all_modules.items():
            if name in all_modules:
                self.warnings.append(f"模块 '{name}' 已存在，使用新版本")
            all_modules[name] = mod

        merged.all_modules = all_modules

        # 确定顶层
        if new.top_addrmap_name:
            # 新上传的有顶层 addr_map
            merged.top_addrmap_name = new.top_addrmap_name
            merged.top_modules = new.top_modules

            # 尝试将已有模块插入到新结构中
            for name, mod in existing.items():
                if name not in new.all_modules:
                    inserted = self._try_insert_module(mod, merged)
                    if not inserted:
                        self._add_uninstantiated_module(mod, "无法在当前层次结构中找到例化位置")
        elif existing:
            # 新上传的没有顶层，但已有模块
            # 保留之前找到的顶层
            merged.top_modules = new.top_modules
            for mod in new.top_modules:
                merged.top_modules.append(mod)
        else:
            # 都没有顶层，使用新的
            merged.top_modules = new.top_modules

        return merged

    def _try_insert_module(self, module: Module, hierarchy: RegisterHierarchy) -> bool:
        """尝试将模块插入到层次结构中的合适位置"""
        # 策略：根据地址范围匹配顶层模块的子模块

        for top_mod in hierarchy.top_modules:
            # 检查是否是子模块
            if (module.start_addr >= top_mod.start_addr and
                module.end_addr <= top_mod.end_addr and
                module.name != top_mod.name):

                # 检查是否已存在
                exists = any(sub.name == module.name for sub in top_mod.submodules)
                if not exists:
                    top_mod.submodules.append(module)
                    return True

        # 如果无法作为子模块插入，作为独立顶层模块
        exists = any(m.name == module.name for m in hierarchy.top_modules)
        if not exists:
            hierarchy.top_modules.append(module)
            return True

        return False

    def _try_instantiate_orphan_modules(self, hierarchy: RegisterHierarchy):
        """检查真正未例化的模块（既不在顶层也不在子模块链中）"""
        # 构建已实例化的模块集合
        instantiated = set()
        def mark_instantiated(modules):
            for mod in modules:
                instantiated.add(mod.name)
                mark_instantiated(mod.submodules)

        mark_instantiated(hierarchy.top_modules)

        # 同时收集基础模块名称（数组实例的基础）
        base_modules = set()
        for mod in hierarchy.all_modules.values():
            if getattr(mod, 'is_array_instance', False):
                base_name = getattr(mod, 'base_module_name', None)
                if base_name:
                    base_modules.add(base_name)

        # 只标记真正未例化的模块
        for name, mod in hierarchy.all_modules.items():
            # 如果已在实例化链中，跳过
            if name in instantiated:
                continue

            # 如果是基础模块（被数组实例引用），也认为是已实例化的
            if name in base_modules:
                continue

            # 检查是否有任何顶层模块将其作为子模块（递归检查）
            is_in_submodule_chain = False
            for top in hierarchy.top_modules:
                if self._is_module_in_submodule_chain(mod.name, top):
                    is_in_submodule_chain = True
                    break

            if not is_in_submodule_chain:
                # 真正未例化的模块，记录 warning
                self.warnings.append(f"模块 '{mod.name}' 未被例化到任何 addr_map 中，请在网页上手动例化")
                self._add_uninstantiated_module(mod, "模块未被例化，请手动例化")

    def _is_module_in_submodule_chain(self, module_name: str, parent: Module) -> bool:
        """递归检查模块是否在子模块链中"""
        for sub in parent.submodules:
            if sub.name == module_name:
                return True
            if self._is_module_in_submodule_chain(module_name, sub):
                return True
        return False

    def _add_uninstantiated_module(self, module: Module, reason: str):
        """添加未例化模块记录"""
        # 检查是否已存在
        for um in self.uninstantiated_modules:
            if um.name == module.name:
                return

        um = UninstantiatedModule(
            name=module.name,
            source='excel',
            reason=reason
        )
        um.registers = module.registers
        um.start_addr = module.start_addr
        self.uninstantiated_modules.append(um)

    def _save_merged_hierarchy(self, version_id: int, hierarchy: RegisterHierarchy):
        """保存合并后的层次结构到数据库"""
        # 删除旧的模块数据（级联删除寄存器和字段）
        # 注意：使用 query.delete() 不会触发级联删除，需要逐条删除
        old_modules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id
        ).all()
        for mod in old_modules:
            self.db.delete(mod)
        self.db.commit()

        # 保存模块（只保存合入层次结构的模块，即 top_modules 及其子模块链）
        saved_module_ids = {}

        # 首先保存顶层模块及其子模块链
        for module in hierarchy.top_modules:
            self._save_module_recursive(version_id, module, saved_module_ids)

        # 同时保存作为数组实例基础模板的模块（如 PEC 是 PEC0/PEC1 的基础）
        # 这些模块虽然不在子模块链中，但被数组实例引用，需要保存
        # 将它们作为 soc_addr_map 的子模块保存，而不是顶层模块
        soc_addr_map_id = None
        for mod_name, mod_id in saved_module_ids.items():
            if mod_name == 'soc_addr_map':
                soc_addr_map_id = mod_id
                break

        for name, module in hierarchy.all_modules.items():
            if name not in saved_module_ids:
                # 检查是否被任何数组实例作为基础模块引用
                is_base_module = any(
                    getattr(m, 'base_module_name', None) == name
                    for m in hierarchy.all_modules.values()
                )
                if is_base_module:
                    # 基础模块作为 soc_addr_map 的子模块保存（如果 soc_addr_map 存在）
                    # 否则作为顶层模块保存
                    # 注意：不递归保存子模块，因为子模块已经通过数组实例保存了
                    self._save_base_module(version_id, module, saved_module_ids,
                                          parent_id=soc_addr_map_id)

        self.db.commit()

    def _save_module_recursive(self, version_id: int, module: Module, saved_ids: Dict[str, int] = None, is_top_level: bool = False, parent_id: int = None):
        """递归保存模块"""
        if saved_ids is None:
            saved_ids = {}

        # 检查是否已经保存过
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

        # 记录已保存的模块ID
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

    def _save_base_module(self, version_id: int, module: Module, saved_ids: Dict[str, int], parent_id: int = None):
        """保存数组基础模块（不递归保存子模块）

        数组基础模块（如 C2C, PEC）包含寄存器定义，被数组实例引用。
        但它们不应该保存子模块，因为那些子模块已经通过数组实例保存了。
        """
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

        # 记录已保存的模块ID
        saved_ids[module.name] = db_mod.id

        # 保存寄存器（但不保存子模块）
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

        # 注意：不递归保存子模块，因为子模块已经通过数组实例保存了

    def _save_version_warnings(self, version_id: int, warnings: List[str]):
        """保存warnings到版本，保存所有生成过程中的warnings"""
        # 获取版本并保存所有warnings
        version = self.db.query(Version).filter(Version.id == version_id).first()
        if version:
            version.warnings = warnings
            self.db.commit()

    def generate_unified_rdl(self, version_id: int) -> str:
        """生成统一的 RDL 文件（包含所有模块）"""
        version = self.db.query(Version).filter(Version.id == version_id).first()
        if not version:
            return ""

        # 从数据库重建层次结构
        hierarchy = self._rebuild_hierarchy_from_db(version_id, version.name)

        # 使用 RDL 生成器
        generator = PeakRDLCompatibleRDLGenerator()
        return generator.generate(hierarchy)

    def _rebuild_hierarchy_from_db(self, version_id: int, version_name: str) -> RegisterHierarchy:
        """从数据库重建层次结构"""
        hierarchy = RegisterHierarchy(version_name=version_name)

        # 加载顶层模块
        db_modules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id,
            DBRegisterModule.parent_module_id.is_(None)
        ).all()

        for db_mod in db_modules:
            module = self._db_module_to_domain(db_mod)
            self._load_submodules_recursive(version_id, module, db_mod.id, hierarchy)
            hierarchy.top_modules.append(module)
            hierarchy.all_modules[module.name] = module

        # 设置顶层名称 - 优先选择有子模块但无寄存器的 addrmap 作为顶层
        if hierarchy.top_modules:
            # 找到 addrmap 类型的模块作为顶层（有子模块但没有寄存器）
            top_addrmap = None
            for mod in hierarchy.top_modules:
                if len(mod.submodules) > 0 and len(mod.registers) == 0:
                    top_addrmap = mod
                    break

            if top_addrmap:
                hierarchy.top_addrmap_name = top_addrmap.name
                # 只保留 addrmap 类型的模块作为顶层，其他移到 all_modules 但不作为顶层
                new_top_modules = [top_addrmap]
                for mod in hierarchy.top_modules:
                    if mod != top_addrmap:
                        # 确保在 all_modules 中
                        if mod.name not in hierarchy.all_modules:
                            hierarchy.all_modules[mod.name] = mod
                hierarchy.top_modules = new_top_modules
            else:
                hierarchy.top_addrmap_name = hierarchy.top_modules[0].name

        # 加载所有模块到 all_modules（包括非顶层模块）
        all_db_modules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id
        ).all()

        for db_mod in all_db_modules:
            if db_mod.name not in hierarchy.all_modules:
                module = self._db_module_to_domain(db_mod)
                hierarchy.all_modules[module.name] = module

        # 修复数组实例：从基础模块复制寄存器
        self._fixup_array_instances(hierarchy)

        return hierarchy

    def _fixup_array_instances(self, hierarchy: RegisterHierarchy):
        """修复数组实例：从基础模块复制寄存器，设置is_array_instance属性"""
        import re
        import copy

        # 第一遍：找出所有基础模块和实例的关系
        base_to_instances = {}  # base_name -> [instance_names]
        instance_to_base = {}   # instance_name -> base_name

        for module in list(hierarchy.all_modules.values()):
            # 尝试找到基础模块（去掉末尾的数字或_数字）
            # 匹配模式：Name123 或 Name_123
            match = re.match(r'^(.+?)(?:_(\d+)|(\d+))$', module.name)
            if not match:
                continue

            base_name = match.group(1)
            if base_name in hierarchy.all_modules:
                instance_to_base[module.name] = base_name
                if base_name not in base_to_instances:
                    base_to_instances[base_name] = []
                base_to_instances[base_name].append(module.name)

        # 第二遍：从基础模块复制到实例
        for module in list(hierarchy.all_modules.values()):
            if module.name not in instance_to_base:
                continue

            base_name = instance_to_base[module.name]
            base_module = hierarchy.all_modules.get(base_name)

            if not base_module:
                continue

            # 标记为数组实例
            module.is_array_instance = True
            module.base_module_name = base_name

            # 从基础模块复制寄存器（如果实例没有寄存器但基础模块有）
            if base_module.registers and not module.registers:
                module.registers = copy.deepcopy(base_module.registers)

            # 从基础模块复制子模块（如果实例没有子模块但基础模块有）
            if base_module.submodules and not module.submodules:
                module.submodules = copy.deepcopy(base_module.submodules)

        # 第三遍：从实例复制结构到基础模块（反向修复）
        # 如果基础模块没有子模块但实例有，则从第一个实例复制
        for base_name, instance_names in base_to_instances.items():
            base_module = hierarchy.all_modules.get(base_name)
            if not base_module:
                continue

            # 找到第一个有完整结构的实例作为模板
            template_instance = None
            for inst_name in instance_names:
                inst_module = hierarchy.all_modules.get(inst_name)
                if inst_module and (inst_module.submodules or inst_module.registers):
                    template_instance = inst_module
                    break

            if not template_instance:
                continue

            # 如果基础模块没有子模块，从模板实例复制
            if not base_module.submodules and template_instance.submodules:
                base_module.submodules = copy.deepcopy(template_instance.submodules)
                # 子模块地址已经是相对于模板实例的地址
                # 转换为相对于基础模块（基础模块地址为0，所以相对地址就是子模块地址）
                # 注意：template_instance.submodules 中的地址可能是绝对地址或相对地址
                # 这里我们保持原样，因为它们是相对于实例的偏移

            # 如果基础模块没有寄存器，从模板实例复制
            if not base_module.registers and template_instance.registers:
                base_module.registers = copy.deepcopy(template_instance.registers)

        # 第四遍：确保所有实例都有完整的子模块结构
        for base_name, instance_names in base_to_instances.items():
            base_module = hierarchy.all_modules.get(base_name)
            if not base_module:
                continue

            for inst_name in instance_names:
                inst_module = hierarchy.all_modules.get(inst_name)
                if not inst_module:
                    continue

                # 如果实例没有子模块但基础模块有，从基础模块复制
                if base_module.submodules and not inst_module.submodules:
                    inst_module.submodules = copy.deepcopy(base_module.submodules)
                    # 调整子模块地址为绝对地址（相对于实例的基地址）
                    for sub in inst_module.submodules:
                        sub.start_addr = sub.start_addr + inst_module.start_addr
                        sub.end_addr = sub.end_addr + inst_module.start_addr

    def _load_submodules_recursive(self, version_id: int, parent: Module, parent_db_id: int, hierarchy: RegisterHierarchy):
        """递归加载子模块并添加到 all_modules"""
        db_submodules = self.db.query(DBRegisterModule).filter(
            DBRegisterModule.version_id == version_id,
            DBRegisterModule.parent_module_id == parent_db_id
        ).all()

        for db_sub in db_submodules:
            # 数据库中存储的是绝对地址，直接转换
            sub_module = self._db_module_to_domain(db_sub)
            self._load_submodules_recursive(version_id, sub_module, db_sub.id, hierarchy)
            parent.submodules.append(sub_module)
            hierarchy.all_modules[sub_module.name] = sub_module

    def get_uninstantiated_modules(self, version_id: int) -> List[Dict]:
        """获取未例化的模块列表

        模块被认为是已例化的，如果它满足以下条件之一：
        1. 是顶层模块 (top_modules)
        2. 是顶层模块的子模块 (submodules)
        3. 在 all_modules 中且没有其他模块将其作为 base_module 引用
        """
        # 重新计算当前状态的未例化模块
        version = self.db.query(Version).filter(Version.id == version_id).first()
        if not version:
            return []

        hierarchy = self._rebuild_hierarchy_from_db(version_id, version.name)

        # 查找所有已实例化的模块（从顶层开始遍历）
        instantiated = set()
        def mark_instantiated(modules):
            for mod in modules:
                instantiated.add(mod.name)
                # 递归标记子模块
                mark_instantiated(mod.submodules)

        mark_instantiated(hierarchy.top_modules)

        # 同时，查找所有作为 base_module 被引用的模块（如 CPD 是 CPD_0/CPD_1 的基础模块）
        # 这些也应该被认为是已实例化的
        base_modules = set()
        for mod in hierarchy.all_modules.values():
            if getattr(mod, 'is_array_instance', False):
                base_name = getattr(mod, 'base_module_name', None)
                if base_name:
                    base_modules.add(base_name)

        # 合并已实例化和基础模块
        all_instantiated = instantiated.union(base_modules)

        # 找出真正未例化的（既不在实例化链中，也不是基础模块）
        uninstantiated = []
        for name, mod in hierarchy.all_modules.items():
            if name not in all_instantiated:
                # 检查是否有其他模块引用它作为子模块（通过 submodule 关系）
                is_referenced = False
                for top_mod in hierarchy.top_modules:
                    if self._is_module_in_submodules(mod.name, top_mod):
                        is_referenced = True
                        break

                if not is_referenced:
                    uninstantiated.append({
                        'name': name,
                        'start_addr': mod.start_addr,
                        'end_addr': mod.end_addr,
                        'size': mod.size,
                        'register_count': len(mod.registers),
                        'reason': '未在层次结构中找到例化位置'
                    })

        return uninstantiated

    def _is_module_in_submodules(self, module_name: str, parent: Module) -> bool:
        """递归检查模块是否在子模块链中"""
        for sub in parent.submodules:
            if sub.name == module_name:
                return True
            if self._is_module_in_submodules(module_name, sub):
                return True
        return False

    def instantiate_module_manually(self, version_id: int, module_name: str,
                                    parent_module_name: str) -> bool:
        """手动例化模块到指定的父模块"""
        try:
            # 查找父模块
            parent = self.db.query(DBRegisterModule).filter(
                DBRegisterModule.version_id == version_id,
                DBRegisterModule.name == parent_module_name
            ).first()

            if not parent:
                return False

            # 查找要例化的模块
            module = self.db.query(DBRegisterModule).filter(
                DBRegisterModule.version_id == version_id,
                DBRegisterModule.name == module_name
            ).first()

            if not module:
                return False

            # 更新父模块关系
            module.parent_module_id = parent.id
            self.db.commit()

            return True
        except Exception as e:
            self.db.rollback()
            print(f"Error instantiating module: {e}")
            return False
