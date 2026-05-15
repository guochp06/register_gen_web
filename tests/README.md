# 测试套件说明

## 测试集概述

本测试集包含寄存器工具的端到端测试，用于验证每次版本改动的正确性。

## 测试文件

| 测试文件 | 描述 | 执行时间 |
|---------|------|---------|
| `test_empty_module_occupy.py` | 单元测试 - 空模块 occupy 寄存器生成 | ~5s |
| `test_end_to_end.py` | addr_map_S 完整端到端测试（解析+生成） | ~30s |
| `test_download_api.py` | API 测试 - Download 下载功能 | ~15s |

## ⚠️ 重要：每次代码修改后必须运行完整测试集！

**所有代码修改完成后，必须运行完整测试集验证，确保没有破坏现有功能。**

### 运行所有测试

```bash
cd /home/xiaoer/AI_GEN/regtool
python3 tests/run_all_tests.py
```

### 运行单个测试

```bash
cd /home/xiaoer/AI_GEN/regtool
python3 tests/test_end_to_end.py
python3 tests/test_empty_module_occupy.py
```

## 测试数据

测试数据位于 `/home/xiaoer/register/addr_map_S/`，包含以下 Excel 文件：

- `soc_addr_map.xls` - 顶层地址映射表
- `C2C.xls` - C2C 模块寄存器定义
- `DRAM_IF.xls` - DRAM 接口寄存器定义
- `GCS.xls` - GCS 模块寄存器定义
- `PE.xls` - PE 模块寄存器定义
- `PEC.xls` - PEC 模块寄存器定义

## 验证内容

### 1. Excel 解析验证
- [x] 所有文件能被正确解析
- [x] 模块数组（*N格式）正确展开
- [x] 基础模块和实例关系正确
- [x] 地址范围计算正确
- [x] 无解析错误
- [x] **空模块检查** - 检测无寄存器、无子模块的模块（会导致 PeakRDL UVM 生成失败）

### 2. 代码生成验证
- [x] RDL 文件生成（SystemRDL 2.0）
- [x] RALF 文件生成
- [x] C Header 文件生成（.h）
- [x] SVH 文件生成（.svh）
- [x] UVM Regmodel 生成（.sv）
- [x] RTL 生成（.sv，PeakRDL regblock）

### 3. 下载功能验证
测试以下 API 端点：
- `GET /versions/{id}/files` - 列出生成文件
- `GET /versions/{id}/download/{format}` - 下载单个文件
- `GET /versions/{id}/download-module-files/{type}` - 下载 ZIP
- `GET /versions/{id}/rtl/files` - 列出 RTL 文件
- `GET /versions/{id}/rtl/download` - 下载 RTL

## 预期输出

测试通过时，应在 `test_output/` 目录生成：

```
test_output/
└── test_e2e/
    ├── rdl/          # 16 个 .rdl 文件
    ├── ralf/         # 16 个 .ralf 文件
    ├── header/       # 15 个 .h 文件
    ├── svh/          # 15 个 .svh 文件
    ├── uvm/          # 11 个 _regmodel.sv 文件
    └── rtl/          # 10 个 _reg.sv 文件
```

## 故障排查

### 测试失败常见原因

1. **PeakRDL 未安装**
   ```bash
   pip3 install peakrdl-uvm peakrdl-cheader peakrdl-regblock --break-system-packages
   ```

2. **输出目录权限问题**
   ```bash
   chmod -R 777 /home/xiaoer/AI_GEN/regtool/test_output
   ```

3. **Excel 文件被修改**
   - 检查 `/home/xiaoer/register/addr_map_S/` 下的文件是否完整

### 空模块处理

**已修复：空模块自动添加 occupy 寄存器**

之前的空模块（如 `Core0_ILM`、`Core1_ILM`）会导致 PeakRDL UVM 生成失败：
```
/tmp/xxx/PE.rdl:17:15: error: Address map 'Core0_ILM_inst' must contain at least one reg...
```

**解决方案：** 自动为无寄存器、无子模块的空模块添加 `occupy` 占位寄存器：

```systemrdl
addrmap Core0_ILM {
    name = "Core0_ILM";
    desc = "Module with occupy placeholder register";

    // Occupy register - placeholder for empty module
    reg occupy {
        name = "occupy";
        desc = "Placeholder register for empty module";
        field {
            sw = rw;
            hw = r;
            desc = "Placeholder field";
        } occupy_val[31:0];
    } occupy @0x0;
};
```

生成的寄存器：
- 名称：`occupy`
- 地址偏移：`0x0000`
- 位宽：32-bit
- 访问类型：RW
- 复位值：`0x00000000`

这样可以满足 SystemRDL 的要求，使 PeakRDL 编译通过。

## CI/CD 集成

建议在以下时机运行测试：
1. 每次代码提交前
2. 每次版本发布前
3. 修改解析或生成逻辑后

## 添加新测试

如需添加新测试：
1. 在 `tests/` 目录创建 `test_*.py` 文件
2. 实现 `test_*()` 函数返回 bool
3. 在 `run_all_tests.py` 的 `tests` 列表中添加测试
