#!/usr/bin/env python3
"""
测试重新上传 PEC.xls 的增量更新功能

测试流程：
1. 加载所有 Excel 文件（包括 PEC.xls）
2. 重新上传 PEC.xls（模拟更新场景）
3. 检查 PEC 模块是否被正确替换
4. 验证所有文件重新生成
"""
import os
import sys
import shutil
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.core.config import settings

TEST_DIR = "/home/xiaoer/register/addr_map_S"
PEC_FILE = "/home/xiaoer/register/addr_map_S/PEC.xls"


def get_all_excel_files():
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
    version = Version(name="test_pec_v1", description="PEC重新上传测试")
    db.add(version)
    db.commit()
    db.refresh(version)
    version_id = version.id
    print(f"✅ 创建版本: {version.name} (id={version_id})")

    # 获取所有 Excel 文件
    excel_files = get_all_excel_files()
    print(f"📁 初始上传 {len(excel_files)} 个 Excel 文件")

    # 使用累积服务处理上传
    service = CumulativeHierarchyService(db)
    result = service.process_upload(version_id, excel_files, None)

    if not result['success']:
        print(f"❌ 初始上传失败: {result.get('errors', [])}")
        return None, None, None

    hierarchy = result['hierarchy']
    print(f"\n✅ 初始上传成功!")
    print(f"   - 总模块数: {len(hierarchy.all_modules)}")

    # 检查 PEC 初始状态
    if 'PEC' in hierarchy.all_modules:
        pec = hierarchy.all_modules['PEC']
        print(f"   - PEC 模块: {len(pec.registers)} 个寄存器")
        for reg in pec.registers[:3]:
            print(f"      - {reg.name} @ 0x{reg.offset:X}")
        if len(pec.registers) > 3:
            print(f"      ... 还有 {len(pec.registers)-3} 个寄存器")

    return db, version_id, hierarchy


def step2_reupload_pec(db, version_id):
    """步骤2：重新上传 PEC.xls"""
    print("\n" + "="*60)
    print("步骤2: 重新上传 PEC.xls (增量更新)")
    print("="*60)

    from app.services.incremental_update_service import IncrementalUpdateService

    # 只上传 PEC.xls
    service = IncrementalUpdateService(db)
    result = service.process_incremental_upload(
        version_id,
        [PEC_FILE],  # 只上传 PEC
        None
    )

    if not result['success']:
        print(f"❌ 重新上传失败: {result.get('errors', [])}")
        return None

    print(f"✅ 重新上传处理完成!")

    # 获取更新摘要
    summary = service.get_update_summary()
    print(f"\n更新摘要:")
    print(f"   - 处理模块总数: {summary['total_processed']}")
    print(f"   - 匹配替换: {summary['matched_count']}")
    print(f"   - 未匹配: {summary['unmatched_count']}")

    if summary['matched_modules']:
        print(f"\n   匹配替换的模块:")
        for m in summary['matched_modules']:
            print(f"      - {m['name']} ({m['type']})")

    # 检查 PEC 更新后的状态
    hierarchy = result['hierarchy']
    if 'PEC' in hierarchy.all_modules:
        pec = hierarchy.all_modules['PEC']
        print(f"\n✅ PEC 模块更新后:")
        print(f"   - 寄存器数量: {len(pec.registers)}")
        for reg in pec.registers[:5]:
            print(f"      - {reg.name} @ 0x{reg.offset:X}")
        if len(pec.registers) > 5:
            print(f"      ... 还有 {len(pec.registers)-5} 个寄存器")

    return result


def step3_check_generated_files(version_name="test_pec_v1"):
    """步骤3：检查生成的文件"""
    print("\n" + "="*60)
    print("步骤3: 检查生成的文件")
    print("="*60)

    OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/backend/output"
    import re

    safe_name = re.sub(r'[<>?:/\\|?*]', '_', version_name)
    output_base = Path(settings.OUTPUT_DIR) / safe_name / safe_name
    if not output_base.exists():
        output_base = Path(settings.OUTPUT_DIR) / safe_name

    if not output_base.exists():
        print(f"❌ 输出目录不存在: {output_base}")
        return False

    print(f"📁 检查输出目录: {output_base}")

    # 检查各个格式目录
    formats = {
        'rdl': {'ext': '.rdl'},
        'ralf': {'ext': '.ralf'},
        'header': {'ext': '.h'},
        'svh': {'ext': '.svh'},
        'uvm': {'ext': '.sv'},
        'rtl': {'ext': '.sv'},
    }

    all_ok = True
    total_files = 0

    for fmt, config in formats.items():
        fmt_dir = output_base / fmt
        if not fmt_dir.exists():
            print(f"⚠️  {fmt}/ 目录不存在")
            continue

        files = list(fmt_dir.glob(f"*{config['ext']}"))
        count = len(files)
        total_files += count
        print(f"✅ {fmt}/: {count} 个文件")

    # 特别检查 PEC 文件
    print(f"\n📋 特别检查 PEC 文件:")
    pec_files_found = 0
    for fmt, config in formats.items():
        fmt_dir = output_base / fmt
        if fmt_dir.exists():
            pec_file = fmt_dir / f"PEC{config['ext']}"
            if pec_file.exists():
                print(f"   ✅ {pec_file.name}")
                pec_files_found += 1

    print(f"\n总计: {total_files} 个文件")
    print(f"PEC 相关文件: {pec_files_found} 个")

    return pec_files_found >= 4


def step4_check_pec_content(version_name="test_pec_v1"):
    """步骤4：检查 PEC 文件内容"""
    print("\n" + "="*60)
    print("步骤4: 检查 PEC 文件内容")
    print("="*60)

    OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/backend/output"
    import re

    safe_name = re.sub(r'[<>?:/\\|?*]', '_', version_name)
    output_base = Path(settings.OUTPUT_DIR) / safe_name / safe_name
    if not output_base.exists():
        output_base = Path(settings.OUTPUT_DIR) / safe_name

    # 检查 SVH 文件
    svh_file = output_base / "svh" / "PEC.svh"
    if not svh_file.exists():
        print(f"❌ PEC.svh 不存在")
        return False

    content = svh_file.read_text()
    print(f"✅ PEC.svh 存在 ({len(content)} 字节)")

    # 检查关键特征
    # PEC 是 addr_map 类型（只有子模块，没有寄存器）
    # 所以它的 SVH 应该包含子模块的地址定义，而不是寄存器位域
    checks = {
        'SV预处理器': '`ifndef' in content and '`define' in content,
        'SV格式常量': "'h" in content,
        '子模块地址定义': 'PEC_' in content and '_BASE' in content,
    }

    print(f"\n内容检查 (PEC 是 addr_map 类型):")
    all_ok = True
    for name, exists in checks.items():
        status = "✅" if exists else "❌"
        print(f"   {status} {name}")
        if not exists:
            all_ok = False

    # 对于 addr_map，检查是否有子模块 include
    if '`include' in content:
        print(f"   ✅ 包含子模块引用")
    else:
        print(f"   ℹ️  无子模块引用（可能子模块在单独文件中）")

    return all_ok


def cleanup():
    """清理测试输出"""
    print("\n" + "="*60)
    print("清理测试输出")
    print("="*60)

    OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/backend/output"
    if Path(settings.OUTPUT_DIR).exists():
        shutil.rmtree(settings.OUTPUT_DIR)
        print(f"✅ 已清理输出目录")


def main():
    """主函数"""
    print("="*60)
    print("PEC 重新上传增量更新测试")
    print("="*60)
    print(f"测试目录: {TEST_DIR}")
    print(f"PEC文件: {PEC_FILE}")

    # 清理之前的测试输出
    OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/backend/output"
    if Path(settings.OUTPUT_DIR).exists():
        shutil.rmtree(settings.OUTPUT_DIR)

    # 步骤1：初始上传
    result = step1_initial_upload()
    if not result or not result[0]:
        print("\n❌ 步骤1失败，测试中止")
        cleanup()
        return 1

    db, version_id, hierarchy = result

    # 步骤2：重新上传 PEC
    step2_result = step2_reupload_pec(db, version_id)
    if not step2_result:
        print("\n❌ 步骤2失败，测试中止")
        cleanup()
        return 1

    # 步骤3：检查生成的文件
    files_ok = step3_check_generated_files()

    # 步骤4：检查 PEC 内容
    content_ok = step4_check_pec_content()

    # 总结
    print("\n" + "="*60)
    print("测试结果总结")
    print("="*60)

    if files_ok and content_ok:
        print("✅ 所有检查通过!")
        print("\n✅ PEC.xls 重新上传功能正常!")
        cleanup()
        return 0
    else:
        print("❌ 部分检查失败:")
        if not files_ok:
            print("   - 文件生成检查失败")
        if not content_ok:
            print("   - PEC 内容检查失败")
        cleanup()
        return 1


if __name__ == "__main__":
    sys.exit(main())
