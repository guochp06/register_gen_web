#!/usr/bin/env python3
"""
测试输出目录结构

验证所有生成的文件都遵循统一的目录结构：
OUTPUT_DIR / version_name / format/

格式目录包括：
- rdl/
- ralf/
- header/
- svh/
- uvm/
- rtl/
- html/
"""
import os
import sys
import shutil
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.services.hierarchy_parser import HierarchyParser
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


def test_directory_structure():
    """测试目录结构"""
    print("="*60)
    print("测试: 输出目录结构")
    print("="*60)
    print(f"\n预期结构: OUTPUT_DIR / version_name / format/")
    print(f"格式: rdl, ralf, header, svh, uvm, rtl\n")

    clear_output()

    version_name = "test_structure"
    files = get_excel_files()

    # 解析
    parser = HierarchyParser()
    hierarchy = parser.parse_files(files, version_name)

    if hierarchy.errors:
        print(f"❌ 解析错误: {hierarchy.errors}")
        return False

    # 生成代码
    # output_path 应该指向 OUTPUT_DIR，save_all 会创建 version_name/format/ 结构
    output_path = Path(OUTPUT_DIR)
    generator = ModuleCodeGenerator(output_path)
    generated = generator.generate_all(hierarchy, version_id=1, version_name=version_name)

    # 保存文件
    saved = generator.save_all(generated, version_id=1, version_name=version_name)

    # 验证目录结构: OUTPUT_DIR / version_name / format/
    version_output_dir = output_path / version_name

    expected_formats = {
        'rdl': {'ext': ['.rdl'], 'min_count': 1},
        'ralf': {'ext': ['.ralf'], 'min_count': 1},
        'header': {'ext': ['.h'], 'min_count': 1},
        'svh': {'ext': ['.svh'], 'min_count': 1},
        'uvm': {'ext': ['.sv'], 'min_count': 1},
        'rtl': {'ext': ['.sv'], 'min_count': 0},  # RTL 可能为空
    }

    all_passed = True
    total_files = 0

    for fmt, config in expected_formats.items():
        fmt_dir = version_output_dir / fmt

        if not fmt_dir.exists():
            if config['min_count'] > 0:
                print(f"❌ {fmt}/ 目录不存在")
                all_passed = False
            else:
                print(f"ℹ️  {fmt}/ 目录不存在 (可选)")
            continue

        files = [f for f in fmt_dir.iterdir() if f.is_file()]
        count = len(files)
        total_files += count

        if count < config['min_count']:
            print(f"❌ {fmt}/ 目录文件数不足: {count} < {config['min_count']}")
            all_passed = False
            continue

        # 验证文件扩展名
        ext_ok = True
        for f in files:
            if not any(str(f).endswith(ext) for ext in config['ext']):
                print(f"❌ {fmt}/ 下文件 {f.name} 扩展名不正确")
                ext_ok = False
                all_passed = False

        if ext_ok:
            print(f"✅ {fmt}/: {count} 个文件")

    print(f"\n总计: {total_files} 个文件")

    if all_passed:
        print("\n✅ 目录结构测试通过!")
    else:
        print("\n❌ 目录结构测试失败!")

    return all_passed


def test_no_flat_directories():
    """
    测试没有使用扁平目录结构

    旧的混乱结构:
    - output/rdl/
    - output/ralf/
    - output/uvm/

    新的统一结构:
    - output/{version_name}/rdl/
    - output/{version_name}/ralf/
    - output/{version_name}/uvm/
    """
    print("\n" + "="*60)
    print("测试: 没有扁平目录结构")
    print("="*60)

    # 检查 output 根目录下没有 format 目录
    output_root = Path(OUTPUT_DIR)
    if not output_root.exists():
        print("⚠️  输出目录不存在，跳过此测试")
        return True

    flat_dirs = ['rdl', 'ralf', 'header', 'svh', 'uvm', 'rtl', 'html']
    found_flat = []

    for d in flat_dirs:
        flat_dir = output_root / d
        if flat_dir.exists() and flat_dir.is_dir():
            found_flat.append(d)

    if found_flat:
        print(f"❌ 发现扁平目录结构: {found_flat}")
        print("   应该使用: OUTPUT_DIR / version_name / format/")
        return False
    else:
        print("✅ 没有发现扁平目录结构")
        print("✅ 使用的是: OUTPUT_DIR / version_name / format/")
        return True


def test_no_nested_duplicate_directories():
    """
    测试没有重复嵌套目录

    错误结构 (重复嵌套):
    - output/v0.86/v0.86/rdl/
    - output/v0.86/v0.86/uvm/

    正确结构:
    - output/v0.86/rdl/
    - output/v0.86/uvm/
    """
    print("\n" + "="*60)
    print("测试: 没有重复嵌套目录")
    print("="*60)

    output_root = Path(OUTPUT_DIR)
    if not output_root.exists():
        print("⚠️  输出目录不存在，跳过此测试")
        return True

    nested_issues = []

    for version_dir in output_root.iterdir():
        if not version_dir.is_dir():
            continue
        if version_dir.name == 'temp':
            continue

        # 检查是否有嵌套的同名目录
        nested_dir = version_dir / version_dir.name
        if nested_dir.exists() and nested_dir.is_dir():
            nested_issues.append(f"{version_dir.name}/{version_dir.name}/")

    if nested_issues:
        print(f"❌ 发现重复嵌套目录: {nested_issues}")
        print("   这通常是 ModuleCodeGenerator 被传递了错误路径导致的")
        print("   应该使用: OUTPUT_DIR / version_name / format/")
        print("   而不是: OUTPUT_DIR / version_name / version_name / format/")
        return False
    else:
        print("✅ 没有发现重复嵌套目录")
        return True


if __name__ == "__main__":
    success1 = test_directory_structure()
    success2 = test_no_flat_directories()
    success3 = test_no_nested_duplicate_directories()

    # 清理
    if Path(OUTPUT_DIR).exists():
        shutil.rmtree(OUTPUT_DIR)

    sys.exit(0 if (success1 and success2 and success3) else 1)
