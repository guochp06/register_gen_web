# Excel 生成逻辑梳理（双表格类型）

## 概述

系统支持两种 Excel 表格类型：

| 类型 | 特征 | 示例 |
|------|------|------|
| **Register 表格** | 有 registers/fields 定义 | UART.xlsx, GPIO.xlsx |
| **Addr-Map 表格** | 只有 submodule 实例关系，无 registers | SOC_addr_map.xlsx |

---

## 一、Register 表格生成逻辑

### 1.1 输入特征
- Sheet 包含 Register 定义（Name, Address, Width, Description...）
- 每个 Register 包含 Field 定义（Name, Bits/Bit Range, Access, Reset...）

### 1.2 生成流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Register 表格生成流程                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐     ┌──────────────┐     ┌──────────────────────┐   │
│  │   Excel  │────▶│  RDL (自定义) │────▶│  PeakRDL 生成        │   │
│  │  解析    │     │              │     │  ├─ UVM regmodel     │   │
│  └──────────┘     └──────────────┘     │  ├─ C Header (.h)    │   │
│                                        │  └─ RTL (.sv)        │   │
│                                        └──────────────────────┘   │
│                                                   │                 │
│                                                   ▼                 │
│                                        ┌──────────────────────┐   │
│                                        │ 转换 C Header → SVH  │   │
│                                        │ (同名 .svh 文件)      │   │
│                                        └──────────────────────┘   │
│                                                                     │
│  ┌──────────┐     ┌────────────────────────────────────────────┐  │
│  │ RDLExporter│──▶│ 生成 RALF (PeakRDL 不支持 RALF 格式)         │  │
│  └──────────┘     └────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 输出文件

| 文件类型 | 生成工具 | 文件名示例 | 说明 |
|---------|---------|-----------|------|
| RDL | 自定义 | `UART.rdl` | SystemRDL 2.0 源码 |
| UVM | PeakRDL | `UART_regmodel.sv` | UVM register model |
| C Header | PeakRDL | `UART.h` | C 寄存器定义 |
| SVH | 转换 | `UART.svh` | SystemVerilog Header |
| RTL | PeakRDL | `UART_regblock.sv` | AXI-Lite/APB 接口 RTL |
| RALF | RDLExporter | `UART.ralf` | UVM RALF 格式 |

### 1.4 RDL 示例（Register）

```systemrdl
// UART.rdl - Register Module
addrmap UART @0x0 {
    name = "UART Controller";
    desc = "UART with TX/RX FIFO";

    reg {
        name = "TX_DATA";
        desc = "Transmit Data Register";
        field {
            sw = wo;
            hw = r;
            desc = "Transmit data byte";
        } data[7:0];
    } TX_DATA @0x00;

    reg {
        name = "STATUS";
        desc = "UART Status";
        field { sw = r; hw = w; desc = "TX FIFO Full"; } tx_full[0:0];
        field { sw = r; hw = w; desc = "RX FIFO Empty"; } rx_empty[1:1];
    } STATUS @0x04;
};
```

### 1.5 关键代码路径

```python
# module_code_generator.py:_generate_base_module()

# Step 1: 生成 RDL
rdl_content = self._generate_rdl_for_base_module(module, all_modules)
result['rdl'][module_name] = rdl_content

# Step 2: PeakRDL 生成 UVM/C Header/RTL
from app.services.peakrdl_wrapper import PeakRDLGenerator
peakrdl_gen = PeakRDLGenerator()

uvm_success, uvm_content = peakrdl_gen.generate_uvm_for_module(rdl_content, module_name)
chdr_success, cheader_content = peakrdl_gen.generate_cheader_for_module(rdl_content, module_name)
rtl_success, rtl_content = peakrdl_gen.generate_rtl_for_module(rdl_content, module_name)

# Step 3: C Header → SVH
svh_content = self._convert_cheader_to_svh(cheader_content, module_name)

# Step 4: RDLExporter 生成 RALF
exporter = RDLExporter()
export_results = exporter.export_from_rdl_content(rdl_content, module_name)
```

---

## 二、Addr-Map 表格生成逻辑

### 2.1 输入特征
- Sheet 只有 Address Map 定义（Module, Base Address, Size, Description...）
- 无 Register/Field 定义
- 使用 `*N` 格式定义数组实例（如 `PEC*N(N=4)`）

### 2.2 生成流程

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Addr-Map 表格生成流程                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐     ┌──────────────────────────────────────────┐     │
│  │   Excel  │────▶│  生成 RDL (含 `include 引用)              │     │
│  │  解析    │     │  ├─ `include "UART.rdl"                   │     │
│  └──────────┘     │  ├─ `include "GPIO.rdl"                   │     │
│                   │  └─ 实例化: UART UART0_inst @0x1000        │     │
│                   └──────────────────┬───────────────────────┘     │
│                                      │                              │
│                   ┌──────────────────┼──────────────────┐          │
│                   ▼                  ▼                  ▼          │
│            ┌──────────┐      ┌──────────┐      ┌──────────┐       │
│            │   RALF   │      │ C Header │      │   SVH    │       │
│            │ (自定义)  │      │ (自定义)  │      │ (转换)   │       │
│            └──────────┘      └──────────┘      └──────────┘       │
│                                                                     │
│                   ┌──────────────────────────────────────────┐     │
│                   ▼                                          ▼     │
│            ┌─────────────────────────────────────────────────┐     │
│            │  PeakRDL 生成 UVM (层次化，含子模块实例)           │     │
│            │                                                   │     │
│            │  1. 创建 temp 目录                                │     │
│            │  2. 写入所有 submodule RDL 文件                   │     │
│            │  3. compile_file(incl_search_paths=temp_dir)      │     │
│            │  4. 生成 SOC_regmodel.sv (含 UART/GPIO 实例)       │     │
│            └─────────────────────────────────────────────────┘     │
│                                                                     │
│  [RTL: ❌ 不生成 - Addr-Map 只有地址映射，无实际寄存器]               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.3 输出文件

| 文件类型 | 生成工具 | 文件名示例 | 说明 |
|---------|---------|-----------|------|
| RDL | 自定义 | `SOC.rdl` | 含 `` `include `` 和实例化 |
| RALF | 自定义 | `SOC.ralf` | 含 block include |
| C Header | 自定义 | `SOC.h` | 基地址宏定义 |
| SVH | 转换 | `SOC.svh` | 基地址宏定义 |
| UVM | PeakRDL | `SOC_regmodel.sv` | 层次化 regmodel |
| RTL | ❌ | - | 不生成 |

### 2.4 RDL 示例（Addr-Map）

```systemrdl
// SOC.rdl - Addr-Map Module
`include "UART.rdl"
`include "GPIO.rdl"
`include "TIMER.rdl"

addrmap SOC @0x0 {
    name = "System on Chip";
    desc = "Top-level address map";

    // UART0 @ 0x1000
    UART UART0_inst @0x00001000;

    // UART1 @ 0x2000
    UART UART1_inst @0x00002000;

    // GPIO @ 0x3000
    GPIO GPIO_inst @0x00003000;

    // TIMER @ 0x4000
    TIMER TIMER_inst @0x00004000;
};
```

### 2.5 关键代码路径

```python
# module_code_generator.py:_generate_addrmap_module()

# Step 1: 生成 RALF (自定义)
ralf_content = self._generate_ralf_for_addrmap_module(module)

# Step 2: 生成 C Header (自定义 - 基地址宏)
header_content = self._generate_c_header_for_addrmap_module(module, all_modules)

# Step 3: C Header → SVH
svh_content = self._convert_cheader_to_svh(header_content, module_name)

# Step 4: 生成 RDL (含 `include)
rdl_content = self._generate_rdl_for_addrmap_module(module, generated_rdl_modules)

# Step 5: PeakRDL 生成 UVM (需要 include_paths)
if peakrdl_gen.is_available():
    with tempfile.TemporaryDirectory() as temp_rdl_dir:
        # 写入所有 submodule RDL 到 temp 目录
        for dep_name, dep_rdl in result['rdl'].items():
            (Path(temp_rdl_dir) / f"{dep_name}.rdl").write_text(dep_rdl)

        # 编译时指定 include path
        success, uvm_content = peakrdl_gen.generate_uvm_for_module(
            rdl_content, module_name, include_paths=[temp_rdl_dir]
        )
```

---

## 三、核心差异对比

| 维度 | Register 表格 | Addr-Map 表格 |
|------|--------------|---------------|
| **数据来源** | Excel 定义 registers/fields | Excel 定义 module/submodule 关系 |
| **RDL 内容** | 实际 reg/field 定义 | `` `include `` + submodule 实例化 |
| **RALF** | RDLExporter 生成 | 自定义生成（含 include） |
| **C Header** | PeakRDL 生成（寄存器偏移） | 自定义生成（基地址宏） |
| **SVH** | C Header 转换 | C Header 转换 |
| **UVM** | PeakRDL 生成（平级 regmodel） | PeakRDL 生成（层次化含实例） |
| **RTL** | **PeakRDL 生成（必须）** | **❌ 不生成** |
| **数组展开** | 基础模块只生成一次 | 实例引用基础模块 |

---

## 四、数组实例处理

### 4.1 Excel 定义
```
Sheet: SOC_addr_map
| Module    | Base Address | Size |
|-----------|-------------|------|
| UART*N(N=2) | 0x1000    | 4K   |
```

### 4.2 解析结果
```python
# hierarchy_parser.py 展开结果
modules = {
    "UART": Module(           # 基础模块（生成代码）
        name="UART",
        registers=[...],       # 有实际 registers
        is_base_module=True
    ),
    "UART0": Module(          # 数组实例（不生成代码，引用 UART）
        name="UART0",
        base_address=0x1000,   # 0x1000 + 0 * 4K
        registers=[],          # 复制自 UART
        is_array_instance=True,
        base_module_name="UART"
    ),
    "UART1": Module(          # 数组实例
        name="UART1",
        base_address=0x2000,   # 0x1000 + 1 * 4K
        registers=[],
        is_array_instance=True,
        base_module_name="UART"
    ),
}
```

### 4.3 Addr-Map RDL 中的展开
```systemrdl
`include "UART.rdl"  // 只 include 基础模块

addrmap SOC @0x0 {
    UART UART0_inst @0x00001000;  // 第 0 个实例
    UART UART1_inst @0x00002000;  // 第 1 个实例
};
```

---

## 五、生成顺序

```python
# module_code_generator.py:generate_all()

# Pass 1: 识别基础模块（排除数组实例）
base_modules = {
    name: module for name, module in hierarchy.all_modules.items()
    if not module.is_array_instance
}

# Pass 2: 生成 Register 模块（有 registers）
for module in base_modules:
    if module.registers:
        self._generate_base_module(module, result, base_modules)

# Pass 3: 生成空模块（无 registers 无 submodules）
for module in base_modules:
    if not module.registers and not module.submodules:
        self._generate_empty_module_rdl(module, result)

# Pass 4: 生成 Addr-Map 模块（有 submodules 无 registers）
for module in base_modules:
    if module.submodules and not module.registers:
        self._generate_addrmap_module(module, result, base_modules)
```

**为什么 Addr-Map 要最后生成？**
- Addr-Map 的 RDL 需要 `` `include `` 引用 submodule RDL
- 必须确保所有 submodule RDL 已生成（在 result['rdl'] 中）
- UVM 生成时需要 temp 目录包含所有依赖 RDL 文件

---

## 六、关键文件路径

| 功能 | 文件 | 函数/类 |
|------|------|---------|
| 主入口 | `module_code_generator.py` | `ModuleCodeGenerator.generate_all()` |
| Register 生成 | `module_code_generator.py:155` | `_generate_base_module()` |
| Addr-Map 生成 | `module_code_generator.py:350` | `_generate_addrmap_module()` |
| RDL (Register) | `module_code_generator.py` | `_generate_rdl_for_base_module()` |
| RDL (Addr-Map) | `module_code_generator.py:938` | `_generate_rdl_for_addrmap_module()` |
| PeakRDL UVM | `peakrdl_wrapper.py:186` | `generate_uvm_for_module()` |
| PeakRDL C Header | `peakrdl_wrapper.py:230` | `generate_cheader_for_module()` |
| PeakRDL RTL | `peakrdl_wrapper.py:266` | `generate_rtl_for_module()` |
| C→SVH 转换 | `module_code_generator.py:253` | `_convert_cheader_to_svh()` |
| RDLExporter | `rdl_exporter.py:72` | `export_from_rdl_content()` |

---

## 七、依赖包

```bash
# 必需
pip install systemrdl-compiler

# Register 表格（全部必需）
pip install peakrdl-uvm       # UVM regmodel
pip install peakrdl-cheader   # C Header
pip install peakrdl-regblock  # RTL (AXI-Lite/APB)

# Addr-Map 表格
pip install peakrdl-uvm       # UVM（层次化生成）

# HTML 生成（可选）
pip install peakrdl-html
```
