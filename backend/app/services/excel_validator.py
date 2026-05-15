"""
Excel格式验证器 - 根据Excel描述要求实现所有验证点
"""
import re
from typing import List, Dict, Tuple, Optional
from pathlib import Path


class ExcelValidator:
    """Excel内容验证器"""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_module_name(self, name: str, context: str = "") -> bool:
        """
        验证MODULE_NAME格式
        - 支持空格（会在处理时自动去除）
        - 支持常规命名和数组格式如 ABC*N(N=4) 或 ABC*N（N=4）中文括号
        """
        if not name:
            self.errors.append(f"{context}: MODULE_NAME为空")
            return False

        # 将中文括号转换为英文括号
        name = name.replace('（', '(').replace('）', ')')

        # 去除所有空格
        name_cleaned = name.replace(' ', '')

        # 检查是否是数组格式：ABC*N(N=4) 或 ABC_*
        if '*N(N=' in name_cleaned or name_cleaned.endswith('*'):
            # 数组格式，验证基础部分
            base_name = name_cleaned.split('*')[0].rstrip('_')
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', base_name):
                self.errors.append(f"{context}: MODULE_NAME '{name}' 基础名称格式错误")
                return False
            return True

        # 检查常规格式
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name_cleaned):
            self.errors.append(f"{context}: MODULE_NAME '{name}' 格式错误，只能包含字母、数字和下划线，且不能以数字开头")
            return False

        return True

    def validate_address_format(self, addr_str: str, field_name: str, context: str = "",
                                 check_alignment: bool = True) -> Tuple[bool, int]:
        """
        验证地址格式
        - 必须16进制，0x打头
        - byte align (仅当check_alignment=True时检查，如start_addr)
        - 支持 0x12345 或 0x1234_1234 格式
        """
        if not addr_str:
            self.errors.append(f"{context}: {field_name}为空")
            return False, 0

        addr_str = str(addr_str).strip()

        # 检查是否0x打头
        if not addr_str.lower().startswith('0x'):
            self.errors.append(f"{context}: {field_name} '{addr_str}' 必须以0x开头")
            return False, 0

        # 提取数字部分（去掉下划线）
        clean_addr = addr_str.replace('_', '').replace(' ', '')

        # 验证格式：0x[0-9A-Fa-f]+
        if not re.match(r'^0x[0-9A-Fa-f]+$', clean_addr, re.IGNORECASE):
            self.errors.append(f"{context}: {field_name} '{addr_str}' 格式错误，必须为16进制（支持0x1234_5678格式）")
            return False, 0

        try:
            addr_val = int(clean_addr, 16)
        except ValueError:
            self.errors.append(f"{context}: {field_name} '{addr_str}' 无法解析为有效地址")
            return False, 0

        # 检查byte align（地址应该是4的倍数）- 只对start_addr检查
        if check_alignment and addr_val % 4 != 0:
            self.errors.append(f"{context}: {field_name} '0x{addr_val:X}' 未按4字节对齐")
            return False, 0

        return True, addr_val

    def validate_size_format(self, size_str: str, context: str = "") -> Tuple[bool, int]:
        """
        验证size格式
        - 仅支持 B/K/M 三类单位（比如 4K, 4M）
        - 不支持 KB/MB（B会被去掉处理，但纯数字+KB会报错）
        - 不填写单位报错
        """
        if not size_str:
            self.errors.append(f"{context}: size为空")
            return False, 0

        size_str = str(size_str).strip().upper().replace(' ', '')

        # 检查是否包含单位
        if size_str[-1] not in ['B', 'K', 'M', 'G']:
            self.errors.append(f"{context}: size '{size_str}' 缺少单位，仅支持B/K/M/G（如4K, 4M）")
            return False, 0

        # 处理 GB/MB/KB -> G/M/K
        if size_str.endswith('GB'):
            self.warnings.append(f"{context}: size '{size_str}' 使用GB单位，建议简写为G")
            size_str = size_str[:-2] + 'G'
        elif size_str.endswith('MB'):
            self.warnings.append(f"{context}: size '{size_str}' 使用MB单位，建议简写为M")
            size_str = size_str[:-2] + 'M'
        elif size_str.endswith('KB'):
            self.warnings.append(f"{context}: size '{size_str}' 使用KB单位，建议简写为K")
            size_str = size_str[:-2] + 'K'

        # 提取数字部分
        unit = size_str[-1]
        num_part = size_str[:-1]

        # B单位时，数字部分就是全部
        if unit == 'B':
            num_part = size_str  # 保留B，但下面会处理

        try:
            if unit == 'B':
                # B单位直接解析整个字符串（去掉B）
                num_part = size_str[:-1] if size_str.endswith('B') else size_str
                size_val = int(num_part)
            elif unit == 'K':
                size_val = int(num_part) * 1024
            elif unit == 'M':
                size_val = int(num_part) * 1024 * 1024
            elif unit == 'G':
                size_val = int(num_part) * 1024 * 1024 * 1024
            else:
                size_val = int(size_str)
        except ValueError:
            self.errors.append(f"{context}: size '{size_str}' 格式错误，必须为数字+单位（如4K, 4M）")
            return False, 0

        if size_val <= 0:
            self.errors.append(f"{context}: size必须大于0")
            return False, 0

        return True, size_val

    def validate_address_range(self, start_addr: int, end_addr: int, size: int, context: str = "") -> bool:
        """
        验证地址范围
        - 检查 end - start + 1 == size
        """
        expected_size = end_addr - start_addr + 1

        if expected_size != size:
            self.errors.append(
                f"{context}: 地址范围不匹配: start=0x{start_addr:X}, end=0x{end_addr:X}, "
                f"计算size=0x{expected_size:X}({expected_size}), 但填写size=0x{size:X}({size})"
            )
            return False

        return True

    def validate_module_not_same_as_sheet(self, mod_name: str, sheet_name: str, context: str = "") -> bool:
        """
        验证MODULE_NAME不可以和本页签同名
        """
        if mod_name == sheet_name:
            self.errors.append(f"{context}: MODULE_NAME '{mod_name}' 不能和页签名称相同")
            return False
        return True

    def validate_width(self, width_str: str, context: str = "") -> Tuple[bool, int]:
        """
        验证寄存器width
        - 仅支持32/64
        - 接受浮点数，自动取整
        """
        if not width_str:
            self.warnings.append(f"{context}: width为空，默认使用32")
            return True, 32

        try:
            # 尝试作为浮点数解析，然后取整
            width_float = float(str(width_str).strip())
            width = int(width_float)
            # 如果浮点数有小数部分，添加警告
            if width_float != width:
                self.warnings.append(f"{context}: width '{width_str}' 包含小数，已自动取整为 {width}")
        except ValueError:
            self.errors.append(f"{context}: width '{width_str}' 必须是数字")
            return False, 32

        if width not in [32, 64]:
            self.errors.append(f"{context}: width '{width}' 不受支持，仅支持32或64")
            return False, width

        return True, width

    def validate_64bit_address_alignment(self, offset: int, width: int, context: str = "") -> bool:
        """
        验证64位寄存器的地址对齐
        - 64bits时，地址不能是32bits奇数地址（如0xC报错，0x8 OK）
        """
        if width == 64:
            # 对于64位寄存器，偏移应该是8的倍数
            # 0x0, 0x8, 0x10, 0x18 是OK的
            # 0x4, 0xC 是奇数地址（按32bit word算）
            word_index = offset // 4
            if word_index % 2 != 0:
                self.errors.append(
                    f"{context}: 64位寄存器地址 '0x{offset:X}' 对齐错误，"
                    f"必须是8字节对齐（0x0, 0x8, 0x10...），不能是0x4, 0xC等"
                )
                return False
        return True

    def validate_access_type(self, access: str, context: str = "") -> Tuple[bool, str]:
        """
        验证访问类型
        - 去除空格
        - 检查是否为支持的类型
        """
        if not access:
            self.warnings.append(f"{context}: access为空，默认使用RO")
            return True, "RO"

        access = str(access).strip().upper().replace(' ', '')

        # RALF 和 RDL 共同支持的访问类型 (基于 UVM RAL 1.2 标准)
        # 基础类型
        #   RO   = Read-Only
        #   RW   = Read-Write
        #   WO   = Write-Only
        # Write-Once 类型
        #   W1   = Write Once (标准)
        #   WO1  = Write Once (别名)
        # Write-1-to-X 类型
        #   W1C  = Write-1-to-Clear
        #   W1S  = Write-1-to-Set
        #   W1T  = Write-1-to-Toggle
        # Write-0-to-X 类型
        #   W0C  = Write-0-to-Clear
        #   W0S  = Write-0-to-Set
        #   W0T  = Write-0-to-Toggle
        # Read-to-X 类型
        #   RC   = Read-to-Clear
        #   RS   = Read-to-Set
        # Write-Read Combined 类型
        #   WRC  = Write, Read Clears
        #   WRS  = Write, Read Sets
        #   WC   = Write Clears (any write)
        #   WS   = Write Sets (any write)
        # Complex Combined 类型
        #   W1SRC = Write-1 Sets, Read Clears
        #   W1CRS = Write-1 Clears, Read Sets
        #   W0SRC = Write-0 Sets, Read Clears
        #   W0CRS = Write-0 Clears, Read Sets
        #   WSRC  = Write Sets, Read Clears
        #   WCRS  = Write Clears, Read Sets
        # 硬件访问类型
        #   HWR  = Hardware Read
        #   HWW  = Hardware Write
        valid_types = [
            # 基础
            'RO', 'RW', 'WO',
            # Write-Once
            'W1', 'WO1',
            # Write-1-to-X
            'W1C', 'W1S', 'W1T',
            # Write-0-to-X
            'W0C', 'W0S', 'W0T',
            # Read-to-X
            'RC', 'RS',
            # Write-Read Combined
            'WRC', 'WRS', 'WC', 'WS', 'WOS', 'WOC',
            # Complex Combined
            'W1SRC', 'W1CRS', 'W0SRC', 'W0CRS', 'WSRC', 'WCRS',
            # 硬件
            'HWR', 'HWW'
        ]

        if access not in valid_types:
            self.errors.append(f"{context}: access类型 '{access}' 不受支持，支持的类型: {', '.join(valid_types)}")
            return False, access

        return True, access

    def validate_bits_range(self, bits_str: str, reg_width: int, context: str = "") -> Tuple[bool, int, int]:
        """
        验证bits范围
        - 支持 [31:24] 或 31:24 格式
        - 检查范围有效性
        """
        if not bits_str:
            self.errors.append(f"{context}: bits范围为空")
            return False, 0, 0

        bits_str = str(bits_str).strip().replace('[', '').replace(']', '').replace(' ', '')

        if ':' in bits_str:
            parts = bits_str.split(':')
            if len(parts) != 2:
                self.errors.append(f"{context}: bits范围 '{bits_str}' 格式错误，应为[MSB:LSB]或MSB:LSB")
                return False, 0, 0

            try:
                msb = int(parts[0])
                lsb = int(parts[1])
            except ValueError:
                self.errors.append(f"{context}: bits范围 '{bits_str}' 包含非数字")
                return False, 0, 0

            if msb < lsb:
                self.errors.append(f"{context}: bits范围 [{msb}:{lsb}] 无效，MSB必须大于等于LSB")
                return False, msb, lsb

            if msb >= reg_width:
                self.errors.append(f"{context}: MSB {msb} 超出寄存器宽度 {reg_width}")
                return False, msb, lsb

            if lsb < 0:
                self.errors.append(f"{context}: LSB不能为负数")
                return False, msb, lsb

            return True, msb, lsb
        else:
            # 单个bit
            try:
                bit = int(bits_str)
                if bit < 0 or bit >= reg_width:
                    self.errors.append(f"{context}: bit位置 {bit} 超出寄存器宽度 {reg_width}")
                    return False, bit, bit
                return True, bit, bit
            except ValueError:
                self.errors.append(f"{context}: bit位置 '{bits_str}' 不是有效数字")
                return False, 0, 0

    def validate_field_name(self, name: str, context: str = "") -> bool:
        """
        验证field名称
        - reserved作为占位关键字可以反复使用
        - 其他field需要符合命名规范
        """
        if not name:
            self.errors.append(f"{context}: Field名称为空")
            return False

        name = str(name).strip()

        # reserved是关键字，可以多次使用
        if name.lower() == 'reserved':
            return True

        # 检查空格
        if ' ' in name:
            self.errors.append(f"{context}: Field名称 '{name}' 包含空格")
            return False

        return True

    def check_address_conflicts(self, modules: List[Dict], context: str = "") -> bool:
        """
        检查地址冲突
        """
        ranges = []
        for mod in modules:
            start = mod.get('start_addr', 0)
            end = mod.get('end_addr', start)
            name = mod.get('name', 'unknown')
            ranges.append((start, end, name))

        # 检查重叠
        for i, (s1, e1, n1) in enumerate(ranges):
            for s2, e2, n2 in ranges[i+1:]:
                # 检查是否重叠
                if not (e1 < s2 or e2 < s1):
                    self.errors.append(
                        f"{context}: 地址冲突 - '{n1}' [0x{s1:X}-0x{e1:X}] 和 "
                        f"'{n2}' [0x{s2:X}-0x{e2:X}] 有重叠"
                    )
                    return False

        return True

    def check_register_address_conflicts(self, registers: List[Dict], context: str = "") -> bool:
        """
        检查寄存器地址冲突
        """
        offsets = {}
        for reg in registers:
            offset = reg.get('offset', 0)
            name = reg.get('name', 'unknown')
            width = reg.get('width', 32)

            # 计算占用的地址范围
            size = width // 8

            if offset in offsets:
                self.errors.append(
                    f"{context}: 寄存器地址冲突 - '{name}' @0x{offset:X} 与 "
                    f"'{offsets[offset]}' 地址相同"
                )
                return False

            offsets[offset] = name

        return True

    def validate_excel_file_matches_sheet(self, file_path: str, available_sheets: List[str], context: str = "") -> Tuple[bool, str]:
        """
        验证Excel文件名需要和某一个页签名字保持一致
        """
        file_name = Path(file_path).stem

        if file_name in available_sheets:
            return True, file_name

        self.errors.append(
            f"{context}: Excel文件名 '{file_name}' 未在页签中找到匹配，"
            f"可用页签: {', '.join(available_sheets)}"
        )
        return False, ""

    def get_results(self) -> Tuple[List[str], List[str]]:
        """返回错误和警告列表"""
        return self.errors, self.warnings

    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0

    def clear(self):
        """清除错误和警告"""
        self.errors = []
        self.warnings = []
