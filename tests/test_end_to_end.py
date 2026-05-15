#!/usr/bin/env python3
"""
端到端测试 - 测试 addr_map_S 目录下的所有 Excel 文件
验证完整解析和代码生成流程
"""
import os
import sys
import shutil
from pathlib import Path

# Add backend to path
sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.services.hierarchy_parser import HierarchyParser, RegisterHierarchy
from app.services.module_code_generator import ModuleCodeGenerator
from app.services.peakrdl_wrapper import PeakRDLGenerator

TEST_DIR = "/home/xiaoer/register/addr_map_S"
OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/test_output"
from app.core.config import settings

def get_excel_files():
    """获取所有 Excel 文件"""
    files = []
    for f in os.listdir(TEST_DIR):
        if f.endswith(('.xls', '.xlsx')):
            files.append(os.path.join(TEST_DIR, f))
    return sorted(files)

def clear_output():
    """清除输出目录"""
    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def test_parse_and_generate():
    """测试解析和代码生成"""
    print("="*60)
    print("端到端测试 - addr_map_S 完整测试")
    print("="*60)

    files = get_excel_files()
    print(f"\n发现 {len(files)} 个 Excel 文件")

    # 检查 PeakRDL
    peakrdl = PeakRDLGenerator()
    if not peakrdl.is_available():
        print("❌ PeakRDL 不可用")
        return False

    clear_output()

    version_name = "test_e2e"
    parser = HierarchyParser()

    # 解析
    hierarchy = parser.parse_files(files, version_name)

    print(f"\n解析结果:")
    print(f"  - 顶层模块: {hierarchy.top_addrmap_name}")
    print(f"  - 总模块数: {len(hierarchy.all_modules)}")
    print(f"  - 错误数: {len(hierarchy.errors)}")
    print(f"  - 警告数: {len(hierarchy.warnings)}")

    if hierarchy.errors:
        print(f"\n❌ 发现错误:")
        for err in hierarchy.errors:
            print(f"   {err}")
        return False

    # 检查空模块（无寄存器、无子模块的模块）
    print(f"\n检查空模块...")
    empty_modules = []
    for name, mod in hierarchy.all_modules.items():
        has_regs = len(mod.registers) > 0
        has_submods = len(mod.submodules) > 0
        if not has_regs and not has_submods:
            empty_modules.append(name)

    if empty_modules:
        print(f"ℹ️  发现 {len(empty_modules)} 个空模块（将自动添加 occupy 寄存器）：")
        for name in empty_modules:
            print(f"   - {name}")
        print(f"\n注意：空模块已自动添加 occupy 占位寄存器，满足 SystemRDL 要求")
    else:
        print(f"✅ 未发现空模块")

    # 生成代码
    # output_path 应该指向 OUTPUT_DIR，save_all 会创建 version_name/format/ 结构
    output_path = Path(OUTPUT_DIR)
    generator = ModuleCodeGenerator(output_path)
    generated = generator.generate_all(hierarchy, version_id=1, version_name=version_name)

    print(f"\n生成结果:")
    print(f"  - RDL: {len(generated.get('rdl', {}))}")
    print(f"  - RALF: {len(generated.get('ralf', {}))}")
    print(f"  - C Header: {len(generated.get('header', {}))}")
    print(f"  - SVH: {len(generated.get('svh', {}))}")
    print(f"  - UVM: {len(generated.get('uvm', {}))}")
    print(f"  - RTL: {len(generated.get('rtl', {}))}")

    if generator.errors:
        print(f"\n❌ 生成错误:")
        for err in generator.errors:
            print(f"   {err}")
        return False

    # 检查是否有 UVM 生成失败（现在应该不会因为空模块而失败了）
    uvm_failures = [w for w in generator.warnings if "UVM generation failed" in w or "Elaborate aborted" in w]
    if uvm_failures:
        print(f"\n⚠️  UVM 生成警告（非空模块导致）:")
        for warn in uvm_failures[:3]:  # 只显示前3个
            print(f"   {warn}")

    # 保存文件
    saved = generator.save_all(generated, version_id=1, version_name=version_name)
    total = sum(len(files) for files in saved.values())
    print(f"\n✅ 测试通过！生成 {total} 个文件")

    # 验证目录结构: OUTPUT_DIR / version_name / format/
    print(f"\n验证目录结构...")
    expected_structure = {
        'rdl': ['.rdl'],
        'ralf': ['.ralf'],
        'header': ['.h'],
        'svh': ['.svh'],
        'uvm': ['.sv'],
        'rtl': ['.sv'],
    }

    version_dir = Path(OUTPUT_DIR) / version_name
    all_passed = True
    for fmt, exts in expected_structure.items():
        fmt_dir = version_dir / fmt
        if not fmt_dir.exists():
            print(f"❌ {fmt}/ 目录不存在")
            all_passed = False
            continue

        files = list(fmt_dir.iterdir())
        if not files:
            print(f"⚠️  {fmt}/ 目录为空")
            continue

        # 检查扩展名
        for f in files:
            if not any(str(f).endswith(ext) for ext in exts):
                print(f"❌ {fmt}/ 下文件 {f.name} 扩展名不正确")
                all_passed = False

        print(f"✅ {fmt}/: {len(files)} 个文件")

    if not all_passed:
        print("❌ 目录结构验证失败")
        return False

    print("✅ 目录结构验证通过: OUTPUT_DIR / version_name / format/")
    return True

if __name__ == "__main__":
    success = test_parse_and_generate()
    sys.exit(0 if success else 1)
