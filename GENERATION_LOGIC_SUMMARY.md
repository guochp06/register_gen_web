# 寄存器工具生成逻辑总结

## 一、总体流程 (`ModuleCodeGenerator.generate_all`)

```
┌─────────────────────────────────────────────────────────────┐
│                    生成流程 (4 Passes)                       │
├─────────────────────────────────────────────────────────────┤
│  Pass 1: 识别基础模块 (排除数组实例)                         │
│  Pass 2: 生成寄存器模块 (有 registers)                       │
│  Pass 3: 生成空模块 (无 registers 无 submodules)             │
│  Pass 4: 生成地址映射模块 (有 submodules 无 registers)       │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、Register 表格类型 (基础寄存器模块)

### 2.1 特征
- 有实际的 registers (包含 fields)
- 可能有 submodules (但已在 Pass 2 处理)
- 生成所有格式: RDL, RALF, C Header, SVH, UVM, RTL
- **RTL 必须用 PeakRDL 生成，无 fallback**

### 2.2 生成步骤 (`_generate_base_module`)

```
┌──────────────────────────────────────────────────────────────┐
│                   Register Module 生成流程                   │
├──────────────────────────────────────────────────────────────┤
│  Step 1: 生成 RDL (源文件)                                   │
│          └── _generate_rdl_for_base_module()                 │
│                                                              │
│  Step 2: PeakRDL 生成 UVM、C Header、RTL                     │
│          ├── generate_uvm_for_module(rdl_content)           │
│          │   └── PeakRDL UVM Regmodel                       │
│          ├── generate_cheader_for_module(rdl_content)       │
│          │   └── PeakRDL C Header                           │
│          └── generate_rtl_for_module(rdl_content)           │
│              └── PeakRDL Regblock (AXI-Lite/APB)            │
│              └── ❌ 无 fallback，PeakRDL 必须可用           │
│                                                              │
│  Step 3: C Header → SVH 转换                                 │
│          └── _convert_cheader_to_svh()                      │
│              #ifndef → `ifndef                              │
│              /* */ → //                                     │
│              同名文件 (.h → .svh)                           │
│                                                              │
│  Step 4: RDLExporter 生成 RALF                               │
│          └── export_from_rdl_content(rdl_content)           │
│              (PeakRDL 不支持 RALF)                          │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 输出文件

| 格式 | 生成方式 | 文件名示例 |
|------|----------|-----------|
| RDL | 自定义生成 | UART.rdl |
| RALF | RDLExporter | UART.ralf |
| C Header | PeakRDL | UART.h |
| SVH | C Header 转换 | UART.svh |
| UVM | PeakRDL | UART_regmodel.sv |
| RTL | PeakRDL (regblock) | UART_regblock.sv |

### 2.4 RDL 示例 (Register Module)

```systemrdl
// SystemRDL 2.0 - Register Module
addrmap UART @0x0 {
    name = "UART";
    desc = "UART Controller";

    reg {
        name = "DATA";
        field {
            sw = rw;
            hw = r;
            desc = "Transmit/Receive Data";
        } data[7:0];
    } DATA @0x0;

    reg {
        name = "STATUS";
        field {
            sw = r;
            hw = w;
            desc = "Status Flags";
        } tx_empty[0:0];
    } STATUS @0x4;
};
```

---

## 三、Addr-Map 表格类型 (地址映射模块)

### 3.1 特征
- 无 registers (只有 submodules)
- 有 submodules (子模块实例)
- 不生成 RTL (只有地址映射,无实际寄存器)
- 使用 `` `include `` 引用子模块

### 3.2 生成步骤 (`_generate_addrmap_module`)

```
┌──────────────────────────────────────────────────────────────┐
│                   Addr-Map Module 生成流程                   │
├──────────────────────────────────────────────────────────────┤
│  Step 1: 生成 RALF (含 include 引用)                         │
│          └── _generate_ralf_for_addrmap_module()            │
│                                                              │
│  Step 2: 生成 C Header (基地址宏定义)                        │
│          └── _generate_c_header_for_addrmap_module()        │
│                                                              │
│  Step 3: C Header → SVH 转换                                 │
│          └── _convert_cheader_to_svh()                      │
│                                                              │
│  Step 4: 生成 RDL (含 `include 引用)                         │
│          └── _generate_rdl_for_addrmap_module()             │
│                                                              │
│  Step 5: PeakRDL 生成 UVM (层次化实例化)                     │
│          └── 创建临时目录,写入所有子模块 RDL                  │
│          └── generate_uvm_for_module(rdl, include_paths)    │
│                                                              │
│  [无 RTL 生成]                                               │
└──────────────────────────────────────────────────────────────┘
```

### 3.3 输出文件

| 格式 | 生成方式 | 文件名示例 |
|------|----------|-----------|
| RDL | 自定义生成 (含 include) | SOC.rdl |
| RALF | 自定义生成 (含 include) | SOC.ralf |
| C Header | 自定义生成 (基地址宏) | SOC.h |
| SVH | C Header 转换 | SOC.svh |
| UVM | PeakRDL (含子模块实例) | SOC_regmodel.sv |
| RTL | ❌ 不生成 | - |

### 3.4 RDL 示例 (Addr-Map Module)

```systemrdl
// SystemRDL 2.0 - Addr-Map Module
`include "UART.rdl"
`include "GPIO.rdl"
`include "TIMER.rdl"

addrmap SOC @0x0 {
    name = "SOC";
    desc = "System on Chip";

    // Instance of UART0
    UART UART0_inst @0x00001000;

    // Instance of UART1
    UART UART1_inst @0x00002000;

    // Instance of GPIO
    GPIO GPIO_inst @0x00003000;
};
```

---

## 四、关键差异对比

| 特性 | Register 表格 | Addr-Map 表格 |
|------|---------------|---------------|
| **数据来源** | Excel 定义 registers/fields | Excel 定义 submodule 实例关系 |
| **RDL 内容** | 实际 reg/field 定义 | `` `include `` + 实例化语句 |
| **RALF** | 自定义生成 (含 reg/field) | 自定义生成 (含 include) |
| **C Header** | PeakRDL 生成 (含寄存器偏移) | 自定义生成 (含子模块基地址) |
| **SVH** | C Header 转换 | C Header 转换 |
| **UVM** | PeakRDL 生成 (regmodel) | PeakRDL 生成 (层次化含实例) |
| **RTL** | PeakRDL 生成 (regblock) | ❌ 不生成 |
| **数组展开** | 基础模块只生成一次 | 引用基础模块,多实例展开 |

---

## 五、数组实例处理逻辑

### 5.1 Base Module vs Array Instance

```
Excel 定义: PEC*N(N=4)
           ↓
解析结果:
    PEC (base module)      → 生成代码 (registers)
    PEC0 (array instance)  → 不生成代码,引用 PEC
    PEC1 (array instance)  → 不生成代码,引用 PEC
    PEC2 (array instance)  → 不生成代码,引用 PEC
    PEC3 (array instance)  → 不生成代码,引用 PEC
```

### 5.2 地址计算

```python
# 每个实例的地址 = base_addr + index * size_per_instance
# size 列表示每个实例的大小 (不是总大小)

示例: PMH size=4K, N=4
    PMH0 @ 0x0000_0000  (base + 0 * 4K)
    PMH1 @ 0x0000_1000  (base + 1 * 4K)
    PMH2 @ 0x0000_2000  (base + 2 * 4K)
    PMH3 @ 0x0000_3000  (base + 3 * 4K)
```

---

## 六、生成工具链

```
┌──────────────────────────────────────────────────────────────┐
│                        工具链关系                            │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Excel Parser ──→ Module (RegisterHierarchy)                 │
│                      │                                       │
│                      ▼                                       │
│           ┌─────────────────┐                                │
│           │  ModuleCodeGenerator                             │
│           │  (generate_all)                                  │
│           └────────┬────────┘                                │
│                    │                                         │
│       ┌────────────┼────────────┐                           │
│       ▼            ▼            ▼                           │
│  ┌────────┐   ┌────────┐   ┌────────┐                      │
│  │Register│   │ Empty  │   │Addr-Map│                      │
│  │Module  │   │Module  │   │Module  │                      │
│  └───┬────┘   └───┬────┘   └───┬────┘                      │
│      │            │            │                            │
│      ▼            ▼            ▼                            │
│  ┌────────┐   ┌────────┐   ┌────────┐                      │
│  │ PeakRDL│   │Fallback│   │Custom  │                      │
│  │UVM/CHdr│   │Generator│  │Generator│                      │
│  │  RTL   │   │        │   │        │                      │
│  └────────┘   └────────┘   └────────┘                      │
│      │                         │                            │
│      └────────┬────────────────┘                            │
│               ▼                                              │
│        ┌────────────┐                                        │
│        │RDLExporter │ (RALF only)                            │
│        └────────────┘                                        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 七、关键代码路径

| 功能 | 文件 | 函数 |
|------|------|------|
| 主入口 | `module_code_generator.py:33` | `generate_all()` |
| Register Module | `module_code_generator.py:155` | `_generate_base_module()` |
| Addr-Map Module | `module_code_generator.py:350` | `_generate_addrmap_module()` |
| RDL (Register) | `module_code_generator.py` | `_generate_rdl_for_base_module()` |
| RDL (Addr-Map) | `module_code_generator.py:938` | `_generate_rdl_for_addrmap_module()` |
| C→SVH 转换 | `module_code_generator.py:253` | `_convert_cheader_to_svh()` |
| PeakRDL UVM | `peakrdl_wrapper.py:186` | `generate_uvm_for_module()` |
| PeakRDL CHeader | `peakrdl_wrapper.py:230` | `generate_cheader_for_module()` |
| PeakRDL RTL | `peakrdl_wrapper.py:266` | `generate_rtl_for_module()` |
| RDLExporter | `rdl_exporter.py:72` | `export_from_rdl_content()` |

---

## 八、依赖包

```bash
# 必需
pip install systemrdl-compiler

# Register 表格生成需要
pip install peakrdl-uvm      # UVM regmodel
pip install peakrdl-cheader  # C Header
pip install peakrdl-regblock # RTL (AXI-Lite/APB)

# Addr-Map UVM 生成需要 (同上)
# PeakRDL 编译 hierarchical RDL

# RTL 生成 (on-demand API, 已用 PeakRDL)
# peakrdl-regblock 已包含在 Register 表格依赖中

# HTML 生成
pip install peakrdl-html
```
