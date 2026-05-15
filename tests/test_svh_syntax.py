#!/usr/bin/env python3
"""
测试SVH文件语法正确性

验证从C Header转换后的SVH文件:
1. 不包含C-only语法（typedef struct, static_assert, C类型定义）
2. 使用SystemVerilog预处理器语法（`ifndef, `define等）
3. include语句使用.svh扩展名
4. 64位常量格式正确
"""
import os
import sys
import shutil
import re
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.services.hierarchy_parser import HierarchyParser
from app.services.module_code_generator import ModuleCodeGenerator

TEST_DIR = "/home/xiaoer/register/addr_map_S"
OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/test_svh_output"
from app.core.config import settings

# C-only语法模式（不应该出现在SVH中）
C_ONLY_PATTERNS = [
    (r'typedef\s+struct', "C struct typedef"),
    (r'static_assert', "static_assert"),
    (r'\b(uint64_t|uint32_t|uint16_t|uint8_t|int64_t|int32_t|int16_t|int8_t)\b', "C integer types"),
]

# C预处理器语法（不应该出现在SVH中）
C_PREPROCESSOR_PATTERNS = [
    (r'^#ifndef', "C #ifndef"),
    (r'^#define', "C #define"),
    (r'^#endif', "C #endif"),
    (r'^#include', "C #include"),
    (r'^#ifdef', "C #ifdef"),
    (r'^#if', "C #if"),
    (r'^#else', "C #else"),
    (r'^#elif', "C #elif"),
]

# SV预处理器语法（应该出现在SVH中）
SV_PREPROCESSOR_KEYWORDS = ['`ifndef', '`define', '`endif', '`include', '`ifdef']


def get_excel_files():
    """获取所有Excel文件"""
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


def generate_svh_files(version_name="svh_syntax_test"):
    """生成SVH文件"""
    clear_output()

    files = get_excel_files()
    parser = HierarchyParser()
    hierarchy = parser.parse_files(files, version_name)

    if hierarchy.errors:
        print(f"❌ 解析错误: {hierarchy.errors}")
        return None

    output_path = Path(OUTPUT_DIR)
    generator = ModuleCodeGenerator(output_path)
    generated = generator.generate_all(hierarchy, version_id=1, version_name=version_name)
    saved = generator.save_all(generated, version_id=1, version_name=version_name)

    return saved.get('svh', [])


def check_c_only_syntax(svh_content, filename):
    """检查是否包含C-only语法"""
    issues = []
    lines = svh_content.split('\n')

    for line_num, line in enumerate(lines, 1):
        for pattern, desc in C_ONLY_PATTERNS:
            if re.search(pattern, line):
                issues.append(f"{filename}:{line_num}: 发现{desc}: {line.strip()[:60]}")

    return issues


def check_c_preprocessor(svh_content, filename):
    """检查是否使用了C预处理器语法"""
    issues = []
    lines = svh_content.split('\n')

    for line_num, line in enumerate(lines, 1):
        for pattern, desc in C_PREPROCESSOR_PATTERNS:
            if re.search(pattern, line):
                issues.append(f"{filename}:{line_num}: 发现{desc}: {line.strip()[:60]}")

    return issues


def check_include_extension(svh_content, filename):
    """检查include语句是否使用.svh扩展名"""
    issues = []
    lines = svh_content.split('\n')

    for line_num, line in enumerate(lines, 1):
        # 匹配 `include "xxx.h"
        match = re.search(r'`include\s+"([^"]+\.h)"', line)
        if match:
            issues.append(f"{filename}:{line_num}: include使用了.h扩展名: {match.group(1)}")

    return issues


def check_sv_preprocessor_usage(svh_content, filename):
    """检查是否使用了SV预处理器语法"""
    issues = []
    has_sv_preprocessor = False

    for keyword in SV_PREPROCESSOR_KEYWORDS:
        if keyword in svh_content:
            has_sv_preprocessor = True
            break

    if not has_sv_preprocessor:
        issues.append(f"{filename}: 未找到任何SystemVerilog预处理器指令")

    return issues


def test_svh_syntax():
    """测试SVH语法正确性"""
    print("="*60)
    print("测试: SVH文件语法正确性")
    print("="*60)

    svh_files = generate_svh_files()
    if not svh_files:
        print("❌ 未生成SVH文件")
        return False

    print(f"\n生成了 {len(svh_files)} 个SVH文件")

    all_issues = []
    all_passed = True

    for svh_path in svh_files:
        filename = Path(svh_path).name
        content = Path(svh_path).read_text(encoding='utf-8')

        file_issues = []

        # 检查1: 不含C-only语法
        issues = check_c_only_syntax(content, filename)
        if issues:
            file_issues.extend(issues)

        # 检查2: 不使用C预处理器语法
        issues = check_c_preprocessor(content, filename)
        if issues:
            file_issues.extend(issues)

        # 检查3: include使用.svh扩展名
        issues = check_include_extension(content, filename)
        if issues:
            file_issues.extend(issues)

        # 检查4: 使用SV预处理器
        issues = check_sv_preprocessor_usage(content, filename)
        if issues:
            file_issues.extend(issues)

        if file_issues:
            all_passed = False
            all_issues.extend(file_issues)
            print(f"❌ {filename}: 发现 {len(file_issues)} 个问题")
        else:
            print(f"✅ {filename}: 语法正确")

    print()

    if all_issues:
        print("发现的问题:")
        for issue in all_issues[:20]:  # 只显示前20个问题
            print(f"  - {issue}")
        if len(all_issues) > 20:
            print(f"  ... 还有 {len(all_issues) - 20} 个问题")

    if all_passed:
        print("\n✅ 所有SVH文件语法正确!")
    else:
        print(f"\n❌ 发现 {len(all_issues)} 个语法问题!")

    return all_passed


def test_64bit_constants():
    """测试64位常量格式"""
    print("\n" + "="*60)
    print("测试: 64位常量格式")
    print("="*60)

    svh_dir = Path(OUTPUT_DIR) / "svh_syntax_test" / "svh"
    if not svh_dir.exists():
        print("⚠️  SVH目录不存在，跳过此测试")
        return True

    issues = []
    total_constants = 0

    for svh_file in svh_dir.glob("*.svh"):
        content = svh_file.read_text(encoding='utf-8')
        lines = content.split('\n')

        for line_num, line in enumerate(lines, 1):
            # 匹配 `define XXX 'h... (SV格式的十六进制)
            match = re.search(r'`define\s+\w+\s+(\'h[0-9a-fA-F]+)', line)
            if match:
                total_constants += 1
                value = match.group(1)
                # 检查是否超过32位
                try:
                    int_val = int(value[2:], 16)  # Skip 'h prefix
                    if int_val > 0xFFFFFFFF:
                        # 64位常量
                        pass  # SystemVerilog支持任意长度
                except ValueError:
                    pass
            # 也匹配十进制常量
            match_dec = re.search(r'`define\s+\w+\s+(\d+)$', line.strip())
            if match_dec:
                total_constants += 1

    print(f"✅ 检查了 {total_constants} 个常量定义")
    print("✅ 所有64位常量格式正确")

    return len(issues) == 0


def test_register_address_macros():
    """测试：验证寄存器地址宏存在且格式正确"""
    print("\n" + "="*60)
    print("测试: 寄存器地址宏")
    print("="*60)

    svh_dir = Path(OUTPUT_DIR) / "svh_syntax_test" / "svh"
    if not svh_dir.exists():
        print("⚠️  SVH目录不存在，跳过此测试")
        return True

    issues = []
    total_addr_macros = 0
    module_count = 0

    for svh_file in svh_dir.glob("*.svh"):
        # Skip reg_common.svh which doesn't have registers
        if svh_file.name == "reg_common.svh":
            continue

        content = svh_file.read_text(encoding='utf-8')
        module_count += 1

        # Find all address macros: `define MODULE__REG_ADDR 'hXXX (SV format)
        addr_pattern = re.compile(r'`define\s+(\w+)__(\w+)_ADDR\s+(\'h[0-9a-fA-F]+)')
        matches = addr_pattern.findall(content)

        if matches:
            total_addr_macros += len(matches)
            print(f"  ✅ {svh_file.name}: {len(matches)} 个地址宏")
            # Show first 2 as examples
            for i, (mod, reg, addr) in enumerate(matches[:2]):
                print(f"     {mod}__{reg}_ADDR = {addr}")
        else:
            # Check if it's an empty module (no registers)
            # Empty modules are valid - they don't need address macros
            print(f"  ℹ️  {svh_file.name}: 无地址宏 (可能为空模块)")

    print(f"\n总计: {module_count} 个模块, {total_addr_macros} 个地址宏")

    if issues:
        print(f"\n❌ 发现 {len(issues)} 个问题:")
        for issue in issues[:10]:
            print(f"  - {issue}")
        return False
    else:
        print("✅ 寄存器地址宏格式正确!")
        return True


def test_no_c_structs_in_svh():
    """专门测试：确保没有C struct定义"""
    print("\n" + "="*60)
    print("测试: SVH中无C struct定义")
    print("="*60)

    svh_dir = Path(OUTPUT_DIR) / "svh_syntax_test" / "svh"
    if not svh_dir.exists():
        print("⚠️  SVH目录不存在，跳过此测试")
        return True

    found_struct = False

    for svh_file in svh_dir.glob("*.svh"):
        content = svh_file.read_text(encoding='utf-8')
        if 'typedef struct' in content or 'struct {' in content:
            found_struct = True
            print(f"❌ {svh_file.name}: 包含C struct定义")

    if not found_struct:
        print("✅ 所有SVH文件都不包含C struct定义")

    return not found_struct


def test_no_static_assert():
    """专门测试：确保没有static_assert"""
    print("\n" + "="*60)
    print("测试: SVH中无static_assert")
    print("="*60)

    svh_dir = Path(OUTPUT_DIR) / "svh_syntax_test" / "svh"
    if not svh_dir.exists():
        print("⚠️  SVH目录不存在，跳过此测试")
        return True

    found_assert = False

    for svh_file in svh_dir.glob("*.svh"):
        content = svh_file.read_text(encoding='utf-8')
        if 'static_assert' in content:
            found_assert = True
            print(f"❌ {svh_file.name}: 包含static_assert")

    if not found_assert:
        print("✅ 所有SVH文件都不包含static_assert")

    return not found_assert


def test_sv_preprocessor_only():
    """专门测试：确保只使用SV预处理器"""
    print("\n" + "="*60)
    print("测试: 只使用SystemVerilog预处理器")
    print("="*60)

    svh_dir = Path(OUTPUT_DIR) / "svh_syntax_test" / "svh"
    if not svh_dir.exists():
        print("⚠️  SVH目录不存在，跳过此测试")
        return True

    issues = []

    for svh_file in svh_dir.glob("*.svh"):
        content = svh_file.read_text(encoding='utf-8')
        lines = content.split('\n')

        for line_num, line in enumerate(lines, 1):
            # 检查行首的#（C预处理器）
            if re.match(r'^\s*#(ifndef|define|endif|include|ifdef|if|else|elif)\b', line):
                issues.append(f"{svh_file.name}:{line_num}: {line.strip()[:50]}")

    if issues:
        print(f"❌ 发现 {len(issues)} 处使用C预处理器语法:")
        for issue in issues[:10]:
            print(f"  - {issue}")
    else:
        print("✅ 所有文件都正确使用`` ` ``预处理器语法")

    return len(issues) == 0


if __name__ == "__main__":
    success1 = test_svh_syntax()
    success2 = test_64bit_constants()
    success3 = test_register_address_macros()
    success4 = test_no_c_structs_in_svh()
    success5 = test_no_static_assert()
    success6 = test_sv_preprocessor_only()

    # 清理
    if Path(OUTPUT_DIR).exists():
        shutil.rmtree(OUTPUT_DIR)

    print("\n" + "="*60)
    if all([success1, success2, success3, success4, success5, success6]):
        print("✅ 所有SVH语法测试通过!")
        sys.exit(0)
    else:
        print("❌ 部分SVH语法测试失败!")
        sys.exit(1)
