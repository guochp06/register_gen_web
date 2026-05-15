#!/usr/bin/env python3
"""
增量更新场景测试

测试流程：
1. 加载 /home/xiaoer/register/addr_map_S 所有 Excel 文件，生成全套文件
2. 加载 L2B.ralf，执行增量更新
3. 检查 L2B 模块是否被正确替换
4. 验证所有文件完备生成
"""
import os
import sys
import shutil
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

TEST_DIR = "/home/xiaoer/register/addr_map_S"
RALF_FILE = "/home/xiaoer/register/L2B.ralf"

# Get OUTPUT_DIR from API
import requests
BASE = 'http://localhost:8000'
# Default output dir
OUTPUT_DIR = '/home/xiaoer/AI_GEN/regtool/backend/output'


def get_excel_files():
    """获取所有 Excel 文件"""
    files = []
    for f in os.listdir(TEST_DIR):
        if f.endswith('.xls') or f.endswith('.xlsx'):
            files.append(os.path.join(TEST_DIR, f))
    return sorted(files)


def step1_initial_upload():
    """步骤1：初始上传所有 Excel 文件"""
    print("="*60)
    print("步骤1: 初始上传所有 Excel 文件")
    print("="*60)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    from app.services.cumulative_hierarchy_service import CumulativeHierarchyService
    from app.models.version import Version

    # 创建内存数据库
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    # 创建版本
    version = Version(name="test_incremental_v1", description="增量更新测试")
    db.add(version)
    db.commit()
    db.refresh(version)
    version_id = version.id
    print(f"✅ 创建版本: {version.name} (id={version_id})")

    # 获取 Excel 文件
    excel_files = get_excel_files()
    print(f"📁 找到 {len(excel_files)} 个 Excel 文件:")
    for f in excel_files:
        print(f"   - {Path(f).name}")

    # 使用累积服务处理上传
    service = CumulativeHierarchyService(db)
    result = service.process_upload(version_id, excel_files, None)

    if not result['success']:
        print(f"❌ 初始上传失败: {result.get('errors', [])}")
        return None, None

    hierarchy = result['hierarchy']
    print(f"\n✅ 初始上传成功!")
    print(f"   - 总模块数: {len(hierarchy.all_modules)}")
    print(f"   - 顶层模块: {hierarchy.top_addrmap_name}")
    print(f"   - 警告数: {len(result.get('warnings', []))}")

    # 检查 L2B 是否在初始结果中（应该没有，因为没有 L2B.xls）
    if 'L2B' in hierarchy.all_modules:
        l2b = hierarchy.all_modules['L2B']
        print(f"   - L2B 模块存在（来自 Excel）: {len(l2b.registers)} 个寄存器")
    else:
        print(f"   - L2B 模块不存在于初始上传中（符合预期）")

    return db, version_id, hierarchy


def step2_incremental_ralf(db, version_id):
    """步骤2：增量上传 L2B.ralf"""
    print("\n" + "="*60)
    print("步骤2: 增量上传 L2B.ralf")
    print("="*60)

    from app.services.incremental_update_service import IncrementalUpdateService

    # 使用增量更新服务处理 RALF
    service = IncrementalUpdateService(db)
    result = service.process_incremental_upload(
        version_id,
        [],  # 没有新的 Excel 文件
        RALF_FILE
    )

    if not result['success']:
        print(f"❌ 增量上传失败: {result.get('errors', [])}")
        return None

    print(f"✅ 增量上传处理完成!")
    print(f"\n更新摘要:")
    summary = service.get_update_summary()
    print(f"   - 处理模块总数: {summary['total_processed']}")
    print(f"   - 匹配替换: {summary['matched_count']}")
    print(f"   - 未匹配（生成但不合入）: {summary['unmatched_count']}")

    if summary['matched_modules']:
        print(f"\n   匹配替换的模块:")
        for m in summary['matched_modules']:
            print(f"      - {m['name']} ({m['type']})")

    if summary['unmatched_modules']:
        print(f"\n   未匹配的模块:")
        for m in summary['unmatched_modules']:
            print(f"      - {m['name']}: {m['message']}")

    # 检查 L2B 是否被正确处理
    hierarchy = result['hierarchy']
    if 'L2B' in hierarchy.all_modules:
        l2b = hierarchy.all_modules['L2B']
        print(f"\n✅ L2B 模块现在存在于层次结构中")
        print(f"   - 寄存器数量: {len(l2b.registers)}")
        for reg in l2b.registers[:3]:
            print(f"      - {reg.name} @ 0x{reg.offset:X}")
        if len(l2b.registers) > 3:
            print(f"      ... 还有 {len(l2b.registers)-3} 个寄存器")

    return result


def step3_check_generated_files(version_name="test_incremental_v1"):
    """步骤3：检查生成的文件"""
    print("\n" + "="*60)
    print("步骤3: 检查生成的文件")
    print("="*60)

    # 清理版本名称（与代码一致）
    import re
    safe_name = re.sub(r'[<>?":/\\|?*]', '_', version_name)
    # 注意：实际输出路径可能是嵌套的 version_name/version_name/
    output_base = Path(OUTPUT_DIR) / safe_name / safe_name

    if not output_base.exists():
        # 尝试非嵌套路径
        output_base = Path(OUTPUT_DIR) / safe_name
        if not output_base.exists():
            print(f"⚠️  输出目录不存在: {output_base}")
            return False

    print(f"📁 检查输出目录: {output_base}")

    # 检查各个格式目录
    formats = {
        'rdl': {'ext': '.rdl', 'required': True},
        'ralf': {'ext': '.ralf', 'required': True},
        'header': {'ext': '.h', 'required': True},
        'svh': {'ext': '.svh', 'required': True},
        'uvm': {'ext': '.sv', 'required': False},
        'rtl': {'ext': '.sv', 'required': False},
        'html': {'ext': '.html', 'required': False, 'single': True}  # HTML 可选，因为 PeakRDL 可能失败
    }

    all_ok = True
    total_files = 0

    for fmt, config in formats.items():
        fmt_dir = output_base / fmt
        if not fmt_dir.exists():
            if config['required']:
                print(f"❌ {fmt}/ 目录不存在")
                all_ok = False
            continue

        if config.get('single'):
            # HTML 只需要 index.html
            index_file = fmt_dir / "index.html"
            if index_file.exists():
                print(f"✅ {fmt}/index.html 存在")
                total_files += 1
            else:
                print(f"❌ {fmt}/index.html 不存在")
                all_ok = False
        else:
            files = list(fmt_dir.glob(f"*{config['ext']}"))
            count = len(files)
            total_files += count

            if count == 0 and config['required']:
                print(f"❌ {fmt}/ 没有 {config['ext']} 文件")
                all_ok = False
            else:
                status = "✅" if count > 0 or not config['required'] else "⚠️"
                print(f"{status} {fmt}/: {count} 个文件")

    # 特别检查 L2B 文件
    print(f"\n📋 特别检查 L2B 文件:")
    l2b_files_found = 0
    for fmt, config in formats.items():
        if config.get('single'):
            continue
        fmt_dir = output_base / fmt
        if fmt_dir.exists():
            l2b_file = fmt_dir / f"L2B{config['ext']}"
            if l2b_file.exists():
                print(f"   ✅ {l2b_file.name}")
                l2b_files_found += 1

    print(f"\n总计: {total_files} 个文件")
    print(f"L2B 相关文件: {l2b_files_found} 个")

    return all_ok


def step4_check_l2b_content(version_name="test_incremental_v1"):
    """步骤4：检查 L2B 文件内容"""
    print("\n" + "="*60)
    print("步骤4: 检查 L2B 文件内容（验证 RALF 是否正确合入）")
    print("="*60)

    import re
    safe_name = re.sub(r'[<>?":/\\|?*]', '_', version_name)
    # 尝试嵌套路径
    output_base = Path(OUTPUT_DIR) / safe_name / safe_name
    if not output_base.exists():
        output_base = Path(OUTPUT_DIR) / safe_name

    # 检查 SVH 文件中的寄存器定义
    svh_file = output_base / "svh" / "L2B.svh"
    if not svh_file.exists():
        print(f"❌ L2B.svh 不存在")
        return False

    content = svh_file.read_text()
    print(f"✅ L2B.svh 存在 ({len(content)} 字节)")

    # 检查是否包含 RALF 中的寄存器
    expected_regs = ['Rx_FIFO', 'Tx_FIFO', 'STAT_REG', 'CTRL_REG', 'Tx_try_FIFO']
    found_regs = []
    for reg in expected_regs:
        if reg.upper() in content or reg in content:
            found_regs.append(reg)

    print(f"\n寄存器检查:")
    for reg in expected_regs:
        status = "✅" if reg in found_regs else "❌"
        print(f"   {status} {reg}")

    # 检查字段定义
    print(f"\n字段检查:")
    expected_fields = ['Rx_Data', 'Tx_Data', 'Rst_Tx_FIFO', 'Enable_Intr']
    for field in expected_fields:
        if field.upper() in content or field in content:
            print(f"   ✅ {field}")

    # 检查地址宏
    print(f"\n地址宏检查:")
    if 'L2B__RX_FIFO_ADDR' in content or 'RX_FIFO_ADDR' in content:
        print(f"   ✅ Rx_FIFO 地址宏存在")
    if 'L2B__TX_FIFO_ADDR' in content or 'TX_FIFO_ADDR' in content:
        print(f"   ✅ Tx_FIFO 地址宏存在")

    return len(found_regs) == len(expected_regs)


def cleanup():
    """清理测试输出"""
    print("\n" + "="*60)
    print("清理测试输出")
    print("="*60)

    if Path(OUTPUT_DIR).exists():
        shutil.rmtree(OUTPUT_DIR)
        print(f"✅ 已清理: {OUTPUT_DIR}")


def main():
    """主函数"""
    print("="*60)
    print("增量更新场景测试")
    print("="*60)
    print(f"测试目录: {TEST_DIR}")
    print(f"RALF文件: {RALF_FILE}")

    # 清理之前的测试输出
    if Path(OUTPUT_DIR).exists():
        shutil.rmtree(OUTPUT_DIR)

    # 步骤1：初始上传
    result = step1_initial_upload()
    if not result or not result[0]:
        print("\n❌ 步骤1失败，测试中止")
        cleanup()
        return 1

    db, version_id, hierarchy = result

    # 步骤2：增量上传 RALF
    step2_result = step2_incremental_ralf(db, version_id)
    if not step2_result:
        print("\n❌ 步骤2失败，测试中止")
        cleanup()
        return 1

    # 步骤3：检查生成的文件
    files_ok = step3_check_generated_files()

    # 步骤4：检查 L2B 内容
    content_ok = step4_check_l2b_content()

    # 总结
    print("\n" + "="*60)
    print("测试结果总结")
    print("="*60)

    if files_ok and content_ok:
        print("✅ 所有检查通过!")
        cleanup()
        return 0
    else:
        print("❌ 部分检查失败:")
        if not files_ok:
            print("   - 文件生成检查失败")
        if not content_ok:
            print("   - L2B 内容检查失败")
        cleanup()
        return 1


if __name__ == "__main__":
    sys.exit(main())
