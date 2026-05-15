# Excel 读取逻辑梳理

## 一、整体流程

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Excel 读取整体流程                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. 扫描文件 (parse_files)                                                  │
│     ├── 分类: addr_map_tables / register_tables                             │
│     └── 使用 xlrd/openpyxl 读取                                             │
│                                                                             │
│  2. 检测表头 (_detect_headers_*)                                            │
│     ├── addr_map: module_name + start_addr                                  │
│     └── register: offset_address + reg_name                                 │
│                                                                             │
│  3. 查找顶层 addr_map (_find_top_addr_map)                                  │
│     └── 选择引用最多模块的 addr_map 作为顶层                                │
│                                                                             │
│  4. 构建层级结构 (_build_top_addrmap_module)                                │
│     ├── 解析每一行 → 展开模块数组 (*N)                                      │
│     ├── 查找 register 表补充寄存器定义                                      │
│     └── 递归处理子模块的 addr_map                                           │
│                                                                             │
│  5. 验证 (_validate_module_address_ranges)                                  │
│     └── 检查地址范围/size 一致性                                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、表头检测逻辑

### 2.1 Addr-Map 表头

| 必需列 | 别名识别 |
|--------|----------|
| module_name | module_name, module name, modulename, 模块名, module, Module, MODULE_NAME |
| start_addr | start_addr, start addr, startaddr, base_addr, 起始地址, Start Address, BASE_ADDR |
| end_addr | end_addr, end addr, endaddr, 结束地址, END_ADDR |
| size | size, 大小, Size, SIZE |
| description | description, desc, 描述, Description |

### 2.2 Register 表头

| 必需列 | 别名识别 |
|--------|----------|
| offset_address | offsetaddress, offset address, offset, 地址偏移, Offset |
| reg_name | regname, reg name, 寄存器名, register, Register, REG_NAME |
| width | width, 位宽, Width, WIDTH |
| field_name | fieldname, field name, 字段名, field, Field, FIELD_NAME |
| bits | bits, bit range, 位范围, bit_range, FIELD_POS |
| msb | msb, MSB, 最高位 |
| lsb | lsb, LSB, 最低位 |
| access | access, 访问类型, Access, ACCESS |
| reset_value | resetvalue, reset value, 复位值, reset, Reset |
| description | description, desc, 描述 |

### 2.3 表格类型判定

```python
def _detect_table_type(headers):
    if 'module_name' in headers and 'start_addr' in headers:
        return 'addr_map'      # 地址映射表
    elif 'offset_address' in headers and 'reg_name' in headers:
        return 'register'      # 寄存器定义表
    return 'unknown'
```

---

## 三、异常检查逻辑

### 3.1 检查点总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            异常检查矩阵                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  检查项                    │ 错误/警告 │ 位置                              │
│  ─────────────────────────────────────────────────────────────────────────  │
│  MODULE_NAME 为空          │ ❌ Error  │ validate_module_name              │
│  MODULE_NAME 格式错误      │ ❌ Error  │ validate_module_name              │
│  MODULE_NAME = 页签名      │ ❌ Error  │ validate_module_not_same_as_sheet │
│                                                                             │
│  start_addr 为空           │ ❌ Error  │ validate_address_format           │
│  start_addr 非 0x 开头     │ ❌ Error  │ validate_address_format           │
│  start_addr 非 16 进制     │ ❌ Error  │ validate_address_format           │
│  start_addr 未 4 字节对齐  │ ❌ Error  │ validate_address_format           │
│                                                                             │
│  end_addr 格式错误         │ ❌ Error  │ validate_address_format           │
│                                                                             │
│  size 为空                 │ ❌ Error  │ validate_size_format              │
│  size 缺少单位             │ ❌ Error  │ validate_size_format              │
│  size 使用 KB/MB/GB        │ ⚠️ Warn   │ validate_size_format (建议 K/M/G) │
│  size <= 0                 │ ❌ Error  │ validate_size_format              │
│                                                                             │
│  地址范围不匹配            │ ❌ Error  │ validate_address_range            │
│  (end - start + 1 != size) │           │                                   │
│                                                                             │
│  width 非数字              │ ❌ Error  │ validate_width                    │
│  width 非 32/64            │ ❌ Error  │ validate_width                    │
│  width 含小数              │ ⚠️ Warn   │ validate_width (自动取整)         │
│                                                                             │
│  数组模块无寄存器定义      │ ⚠️ Warn   │ _parse_addr_map_row_*_expanded    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 异常处理机制

```python
# 关键异常会停止当前行处理
if not self.validator.validate_module_name(mod_name, context):
    return []  # 停止处理当前行

if not self.validator.validate_address_format(start_str, 'start_addr', context):
    return []  # 停止处理当前行

# 错误收集机制
self.errors = []      # 严重错误，会停止解析
self.warnings = []    # 警告，继续解析

# 传播到上层
hierarchy.errors = self.errors
hierarchy.warnings = self.warnings
```

### 3.3 错误示例

```
❌ Error: file.xlsx 第5行: MODULE_NAME为空
❌ Error: file.xlsx 第5行: start_addr '1234' 必须以0x开头
❌ Error: file.xlsx 第5行: end_addr '0xGGGG' 格式错误，必须为16进制
❌ Error: file.xlsx 第5行: size '256' 缺少单位，仅支持B/K/M/G
❌ Error: file.xlsx 第5行: 地址范围不匹配: start=0x1000, end=0x1FFF,
          计算size=0x1000(4096), 但填写size=0x800(2048)

⚠️ Warn: file.xlsx 第3行: size '4KB' 使用KB单位，建议简写为K
⚠️ Warn: file.xlsx 第7行: width '32.5' 包含小数，已自动取整为 32
⚠️ Warn: file.xlsx 模块'UART'是数组但找不到寄存器定义
```

---

## 四、*N 数组处理逻辑

### 4.1 *N 格式解析 (_parse_module_array)

```python
def _parse_module_array(mod_name: str) -> Tuple[bool, int, str]:
    """
    解析模块数组
    返回: (is_array, array_count, base_name)
    """
    # 情况1: 显式数组定义
    # "CPD*N(N=4)"  -> (True, 4, 'CPD')
    # "UART*N(n=2)" -> (True, 2, 'UART')
    match = re.match(r'^(.*?)\*\s*N\s*\(\s*N\s*=\s*(\d+)\s*\)$', mod_name)
    if match:
        base_name = match.group(1).strip().rstrip('_')
        return True, int(match.group(2)), base_name

    # 情况2: 数字后缀（视为单元素数组）
    # "PEC0"  -> (True, 1, 'PEC')
    # "CPD_1" -> (True, 1, 'CPD')
    match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*?)(\d+)$', mod_name)
    if match:
        base_name = match.group(1).rstrip('_')
        return True, 1, base_name  # array_count=1

    # 情况3: 普通模块
    # "GPIO"  -> (False, 1, 'GPIO')
    return False, 1, mod_name
```

### 4.2 数组展开流程

```
Excel 行数据:
┌─────────────┬─────────────┬──────────┬──────┐
│ Module      │ Start Addr  │ End Addr │ Size │
├─────────────┼─────────────┼──────────┼──────┤
│ PMH*N(N=4)  │ 0x1000_0000 │          │ 4K   │
└─────────────┴─────────────┴──────────┴──────┘

解析过程:
1. _parse_module_array("PMH*N(N=4)") -> (True, 4, 'PMH')

2. 创建基础模块 (PMH) - 只创建一次
   Module(
       name='PMH',
       start_addr=0,           # 基模块地址为 0
       end_addr=4095,          # size - 1 = 4K - 1
       size=4096,              # 4K (每个实例的大小)
       registers=[...],        # 从 PMH.xlsx 加载
       is_array=True,
       array_count=4
   )

3. 展开为 4 个实例
   ┌──────────┬──────────────┬──────────────┬────────┐
   │ 实例名   │ Start Addr   │ End Addr     │ Size   │
   ├──────────┼──────────────┼──────────────┼────────┤
   │ PMH_0    │ 0x1000_0000  │ 0x1000_0FFF  │ 4K     │ ← base + 0*size
   │ PMH_1    │ 0x1000_1000  │ 0x1000_1FFF  │ 4K     │ ← base + 1*size
   │ PMH_2    │ 0x1000_2000  │ 0x1000_2FFF  │ 4K     │ ← base + 2*size
   │ PMH_3    │ 0x1000_3000  │ 0x1000_3FFF  │ 4K     │ ← base + 3*size
   └──────────┴──────────────┴──────────────┴────────┘

   每个实例:
   Module(
       name='PMH_0',           # 实例名
       start_addr=0x10000000,  # 实际地址
       end_addr=0x10000FFF,
       size=4096,
       registers=[],           # 空！引用基模块
       is_array_instance=True,
       base_module_name='PMH'  # 引用基模块
   )
```

### 4.3 命名实例 vs *N(N=x) 格式

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        两种数组格式对比                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  格式1: 显式 *N(N=x)                                                         │
│  ┌─────────────┬─────────────┬──────────┬────────┐                         │
│  │ UART*N(N=2) │ 0x1000      │          │ 256B   │                         │
│  └─────────────┴─────────────┴──────────┴────────┘                         │
│                                                                             │
│  结果: 基础模块 'UART' + 实例 'UART_0', 'UART_1'                            │
│                                                                             │
│  ─────────────────────────────────────────────────────────────────────────  │
│                                                                             │
│  格式2: 数字后缀命名实例                                                     │
│  ┌─────────────┬─────────────┬──────────┬────────┐                         │
│  │ C2C0        │ 0x2000      │ 0x2FFF   │ 4K     │                         │
│  │ C2C1        │ 0x3000      │ 0x3FFF   │ 4K     │                         │
│  └─────────────┴─────────────┴──────────┴────────┘                         │
│                                                                             │
│  结果: 基础模块 'C2C' + 实例 'C2C0', 'C2C1'（保留原始名称）                  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.4 关键代码: 数组展开实现

```python
def _parse_addr_map_row_xlrd_expanded(self, sheet, headers, row_idx, ...):
    # 1. 解析数组定义
    is_array, array_count, base_name = self._parse_module_array(mod_name)

    # 2. 计算每个实例的大小
    if parent_size > 0 and address_range * array_count > parent_size:
        # size 是 total，计算 per-instance
        instance_size = address_range // array_count
    else:
        # size 是 per-instance
        instance_size = size if size else 0x1000

    # 3. 判断是命名实例还是展开实例
    is_named_instance = is_array and array_count == 1 and mod_name != base_name

    if (is_array and array_count > 1) or is_named_instance:
        # 4. 首先创建基础模块（只创建一次）
        if base_name not in self.all_modules:
            base_module = Module(
                name=base_name,
                start_addr=0,  # 基模块地址为 0
                end_addr=instance_size - 1,
                size=instance_size,
                registers=list(registers) if registers else [],
                is_array=True,
                array_count=array_count,
                source_file=file_path
            )
            self.all_modules[base_name] = base_module

        # 5. 创建实例（不复制寄存器，引用基模块）
        if is_named_instance:
            # 单个命名实例
            instance = Module(
                name=mod_name,  # 保留原始名称如 C2C0
                start_addr=start_addr,
                end_addr=end_addr,
                size=instance_size,
                registers=[],  # 引用基模块
                is_array_instance=True,
                base_module_name=base_name
            )
            modules.append(instance)
        else:
            # 多个实例（如 *N(N=4)）
            for i in range(array_count):
                instance_name = f"{base_name}_{i}"  # PMH_0, PMH_1, ...
                instance_start = start_addr + i * instance_size
                instance_end = instance_start + instance_size - 1

                instance = Module(
                    name=instance_name,
                    start_addr=instance_start,
                    end_addr=instance_end,
                    size=instance_size,
                    registers=[],  # 引用基模块
                    is_array_instance=True,
                    base_module_name=base_name
                )
                modules.append(instance)
```

### 4.5 寄存器复制到实例

```python
# 在 _build_hierarchy_from_addr_map 末尾
# 将基模块的寄存器复制到数组实例
for i, module in enumerate(modules):
    base_name = getattr(module, 'base_module_name', None)
    if base_name and base_name in self.all_modules:
        base_module = self.all_modules[base_name]

        needs_regs = base_module.registers and not module.registers
        needs_submods = base_module.submodules and not module.submodules

        if needs_regs or needs_submods:
            # 深复制寄存器
            copied_regs = []
            if needs_regs:
                for reg in base_module.registers:
                    copied_fields = [
                        RegisterField(name=f.name, msb=f.msb, lsb=f.lsb, ...)
                        for f in reg.fields
                    ]
                    copied_regs.append(Register(
                        name=reg.name, offset=reg.offset,
                        width=reg.width, fields=copied_fields
                    ))

            # 创建新模块实例（带复制的寄存器）
            modules[i] = Module(
                name=module.name,
                start_addr=module.start_addr,
                registers=copied_regs,
                submodules=copied_submods,
                is_array_instance=True,
                base_module_name=base_name
            )
```

---

## 五、寄存器表查找逻辑

### 5.1 查找顺序

```python
def _find_registers_for_module(self, module_name: str) -> List[Register]:
    """
    按以下顺序查找寄存器定义:
    1. 完整匹配: UART -> UART.xlsx
    2. 去后缀:   UART_0 -> UART.xlsx
    3. 去数字:   UART0  -> UART.xlsx
    """
    # 1. 完整匹配
    if module_name in self.register_tables:
        return self._parse_register_sheet(...)

    # 2. 尝试去除后缀匹配
    # ABC_0, ABC_1 -> ABC
    # ABC0, ABC1   -> ABC
    patterns = [
        r'^(\w+)_\d+$',      # 匹配下划线+数字
        r'^(\w+?)(\d+)$',    # 匹配数字后缀
    ]

    for pattern in patterns:
        match = re.match(pattern, module_name)
        if match:
            base_name = match.group(1)
            if base_name in self.register_tables:
                return self._parse_register_sheet(...)

    return []  # 未找到
```

---

## 六、关键文件路径

| 功能 | 文件 | 函数/类 |
|------|------|---------|
| 主入口 | `hierarchy_parser.py:75` | `HierarchyParser.parse_files()` |
| 表头检测 | `hierarchy_parser.py:206` | `_detect_headers_xlrd()` |
| 表格类型 | `hierarchy_parser.py:269` | `_detect_table_type()` |
| 顶层查找 | `hierarchy_parser.py:277` | `_find_top_addr_map()` |
| 数组解析 | `hierarchy_parser.py:1104` | `_parse_module_array()` |
| 行展开 | `hierarchy_parser.py:764` | `_parse_addr_map_row_xlrd_expanded()` |
| 验证器 | `excel_validator.py` | `ExcelValidator` |
| 模块名 | `excel_validator.py:16` | `validate_module_name()` |
| 地址 | `excel_validator.py:48` | `validate_address_format()` |
| size | `excel_validator.py:88` | `validate_size_format()` |
| 范围 | `excel_validator.py:148` | `validate_address_range()` |
