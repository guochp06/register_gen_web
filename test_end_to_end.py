#!/usr/bin/env python3
"""
端到端测试脚本 - 测试 addr_map_S 目录下的所有 Excel 文件
- 分批次读入所有文件
- 重复3次
- 验证生成所有代码格式
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

def test_round(round_num, files):
    """执行一轮测试"""
    print(f"\n{'='*60}")
    print(f"第 {round_num}/3 轮测试")
    print(f"{'='*60}")

    version_name = f"test_round_{round_num}"
    parser = HierarchyParser()

    # 解析所有文件
    print(f"\n1. 解析 {len(files)} 个 Excel 文件...")
    for f in files:
        print(f"   - {os.path.basename(f)}")

    hierarchy = parser.parse_files(files, version_name)

    # 检查结果
    print(f"\n2. 解析结果:")
    print(f"   - 顶层模块: {hierarchy.top_addrmap_name}")
    print(f"   - 顶级模块数: {len(hierarchy.top_modules)}")
    print(f"   - 总模块数: {len(hierarchy.all_modules)}")
    print(f"   - 错误数: {len(hierarchy.errors)}")
    print(f"   - 警告数: {len(hierarchy.warnings)}")

    # 打印所有模块名
    print(f"\n3. 模块列表:")
    for name in sorted(hierarchy.all_modules.keys()):
        mod = hierarchy.all_modules[name]
        flags = []
        if mod.is_array:
            flags.append(f"array[{mod.array_count}]")
        if mod.is_array_instance:
            flags.append(f"instance-of[{mod.base_module_name}]")
        if mod.registers:
            flags.append(f"regs[{len(mod.registers)}]")
        if mod.submodules:
            flags.append(f"submods[{len(mod.submodules)}]")
        flag_str = ", ".join(flags) if flags else "empty"
        print(f"   - {name}: 0x{mod.start_addr:08X}-0x{mod.end_addr:08X} ({flag_str})")

    # 检查错误
    if hierarchy.errors:
        print(f"\n❌ 发现错误:")
        for err in hierarchy.errors:
            print(f"   ERROR: {err}")
        return False

    # 检查警告
    if hierarchy.warnings:
        print(f"\n⚠️  警告:")
        for warn in hierarchy.warnings[:10]:  # 只显示前10个
            print(f"   WARN: {warn}")
        if len(hierarchy.warnings) > 10:
            print(f"   ... 还有 {len(hierarchy.warnings)-10} 个警告")

    # 生成代码
    print(f"\n4. 生成代码...")

    # 创建生成器
    output_path = Path(OUTPUT_DIR) / version_name
    generator = ModuleCodeGenerator(output_path)

    # 生成所有代码
    generated = generator.generate_all(hierarchy, version_id=round_num, version_name=version_name)

    # 检查结果
    print(f"\n5. 生成结果:")
    print(f"   - RDL: {len(generated.get('rdl', {}))} 个文件")
    print(f"   - RALF: {len(generated.get('ralf', {}))} 个文件")
    print(f"   - C Header: {len(generated.get('header', {}))} 个文件")
    print(f"   - SVH: {len(generated.get('svh', {}))} 个文件")
    print(f"   - UVM: {len(generated.get('uvm', {}))} 个文件")
    print(f"   - RTL: {len(generated.get('rtl', {}))} 个文件")

    if generator.errors:
        print(f"\n❌ 生成错误:")
        for err in generator.errors:
            print(f"   ERROR: {err}")
        return False

    if generator.warnings:
        print(f"\n⚠️  生成警告:")
        for warn in generator.warnings[:10]:
            print(f"   WARN: {warn}")

    # 保存到文件
    print(f"\n6. 保存文件到 {output_path}...")
    saved = generator.save_all(generated, version_id=round_num, version_name=version_name)

    # 验证文件
    print(f"\n7. 验证生成的文件:")
    total_files = 0
    for fmt, files in saved.items():
        count = len(files)
        total_files += count
        print(f"   - {fmt}: {count} 个文件")

    print(f"\n✅ 第 {round_num} 轮测试通过!")
    print(f"   总文件数: {total_files}")

    return True

def main():
    """主函数"""
    print("="*60)
    print("端到端测试 - addr_map_S 完整测试")
    print("="*60)

    # 获取文件列表
    files = get_excel_files()
    print(f"\n发现 {len(files)} 个 Excel 文件:")
    for f in files:
        print(f"  - {os.path.basename(f)}")

    # 检查 PeakRDL 可用性
    print(f"\n检查 PeakRDL 可用性...")
    peakrdl = PeakRDLGenerator()
    if peakrdl.is_available():
        print("✅ PeakRDL 可用")
    else:
        print("❌ PeakRDL 不可用")
        return 1

    # 清除输出目录
    clear_output()
    print(f"✅ 输出目录已清除: {OUTPUT_DIR}")

    # 执行3轮测试
    all_passed = True
    for i in range(1, 4):
        if not test_round(i, files):
            all_passed = False
            print(f"\n❌ 第 {i} 轮测试失败!")
            break

    if all_passed:
        print(f"\n{'='*60}")
        print("🎉 全部 3 轮测试通过!")
        print(f"{'='*60}")
        print(f"\n输出目录: {OUTPUT_DIR}")
        return 0
    else:
        print(f"\n{'='*60}")
        print("❌ 测试失败!")
        print(f"{'='*60}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
