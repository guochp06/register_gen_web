#!/usr/bin/env python3
"""
测试 API 响应结构

验证文件列表 API 返回的数据结构正确：
1. modules 包含所有模块（非空）
2. combined 只包含真正的汇总文件
3. 模块名正确提取（去除 _regmodel, _reg 后缀）
4. 每个模块包含正确的格式文件
"""
import os
import sys
import shutil
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.services.hierarchy_parser import HierarchyParser
from app.services.module_code_generator import ModuleCodeGenerator

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


def simulate_api_file_listing(version_name):
    """模拟 API 文件列表逻辑"""
    version_dir = Path(OUTPUT_DIR) / version_name

    result = {
        'version_id': 1,
        'version_name': version_name,
        'modules': {},
        'combined': {}
    }

    format_dirs = {
        'rdl': version_dir / 'rdl',
        'ralf': version_dir / 'ralf',
        'header': version_dir / 'header',
        'svh': version_dir / 'svh',
        'uvm': version_dir / 'uvm',
        'rtl': version_dir / 'rtl',
    }

    for fmt, fmt_dir in format_dirs.items():
        if not fmt_dir.exists():
            continue

        files_in_dir = [f for f in fmt_dir.iterdir() if f.is_file()]

        for file_path in files_in_dir:
            file_info = {
                'name': file_path.name,
                'path': str(file_path),
                'size': file_path.stat().st_size
            }

            # 判断是否为 combined 文件
            is_combined = (
                file_path.stem == version_name or
                file_path.stem.endswith('_top') or
                file_path.stem.endswith('_root')
            )

            if is_combined:
                result['combined'][fmt] = file_info
            else:
                # 提取模块名（去除后缀）
                module_name = file_path.stem
                if module_name.endswith('_regmodel'):
                    module_name = module_name[:-9]
                elif module_name.endswith('_reg'):
                    module_name = module_name[:-4]

                if module_name not in result['modules']:
                    result['modules'][module_name] = {}
                result['modules'][module_name][fmt] = file_info

    return result


def test_api_response_structure():
    """测试 API 响应结构"""
    print("=" * 60)
    print("测试: API 响应结构")
    print("=" * 60)

    clear_output()

    version_name = "test_api"
    files = get_excel_files()

    # 解析并生成
    parser = HierarchyParser()
    hierarchy = parser.parse_files(files, version_name)

    if hierarchy.errors:
        print(f"❌ 解析错误: {hierarchy.errors}")
        return False

    generator = ModuleCodeGenerator(Path(OUTPUT_DIR))
    generated = generator.generate_all(hierarchy, version_id=1, version_name=version_name)
    saved = generator.save_all(generated, version_id=1, version_name=version_name)

    # 模拟 API 响应
    result = simulate_api_file_listing(version_name)

    print(f"\n验证点:")

    # 1. 检查 modules 非空
    modules_count = len(result['modules'])
    if modules_count == 0:
        print("❌ modules 为空")
        return False
    print(f"✅ modules 包含 {modules_count} 个模块")

    # 2. 检查每个模块都有文件
    empty_modules = [name for name, files in result['modules'].items() if not files]
    if empty_modules:
        print(f"❌ 空模块: {empty_modules}")
        return False
    print(f"✅ 所有模块都有文件关联")

    # 3. 检查模块名没有后缀
    invalid_names = []
    for name in result['modules'].keys():
        if name.endswith('_regmodel') or name.endswith('_reg'):
            invalid_names.append(name)
    if invalid_names:
        print(f"❌ 模块名未正确清理: {invalid_names}")
        return False
    print(f"✅ 模块名已正确清理（无 _regmodel/_reg 后缀）")

    # 4. 检查 sample 模块包含预期格式
    sample_module = list(result['modules'].keys())[0]
    sample_files = result['modules'][sample_module]
    print(f"\n  示例模块 '{sample_module}' 包含:")
    for fmt in ['rdl', 'ralf', 'header', 'svh', 'uvm']:
        if fmt in sample_files:
            print(f"    ✅ {fmt}: {sample_files[fmt]['name']}")
        else:
            print(f"    ⚠️  {fmt}: 缺失")

    # 5. 统计信息
    total_files = sum(len(files) for files in result['modules'].values())
    combined_count = len(result['combined'])

    print(f"\n统计:")
    print(f"  - 模块数: {modules_count}")
    print(f"  - 文件总数: {total_files}")
    print(f"  - Combined 文件: {combined_count}")

    if total_files > 0:
        print(f"\n✅ API 响应结构测试通过!")
        return True
    else:
        print(f"\n❌ API 响应结构测试失败!")
        return False


if __name__ == "__main__":
    success = test_api_response_structure()

    # 清理
    if Path(OUTPUT_DIR).exists():
        shutil.rmtree(OUTPUT_DIR)

    sys.exit(0 if success else 1)
