#!/usr/bin/env python3
"""
测试套件运行器 - 运行所有测试
"""
import os
import sys
import subprocess
from pathlib import Path
import time

# 测试目录
TESTS_DIR = Path(__file__).parent
PROJECT_DIR = TESTS_DIR.parent

# 使用 venv 中的 Python
VENV_PYTHON = PROJECT_DIR / "backend" / "venv" / "bin" / "python"
if not VENV_PYTHON.exists():
    VENV_PYTHON = PROJECT_DIR / "backend" / "venv" / "Scripts" / "python.exe"  # Windows
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path(sys.executable)  # Fallback to system python

def run_test(test_file: str, description: str) -> bool:
    """运行单个测试"""
    print(f"\n{'='*60}")
    print(f"运行: {description}")
    print(f"文件: {test_file}")
    print(f"Python: {VENV_PYTHON}")
    print(f"{'='*60}")

    test_path = TESTS_DIR / test_file
    if not test_path.exists():
        print(f"❌ 测试文件不存在: {test_path}")
        return False

    start_time = time.time()
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), str(test_path)],
            cwd=PROJECT_DIR,
            capture_output=False,
            timeout=300
        )
        elapsed = time.time() - start_time

        if result.returncode == 0:
            print(f"\n✅ {description} 通过 ({elapsed:.1f}s)")
            return True
        else:
            print(f"\n❌ {description} 失败 ({elapsed:.1f}s)")
            return False
    except subprocess.TimeoutExpired:
        print(f"\n❌ {description} 超时 (300s)")
        return False
    except Exception as e:
        print(f"\n❌ {description} 异常: {e}")
        return False

def main():
    """主函数"""
    print("="*60)
    print("寄存器工具测试套件")
    print("="*60)
    print(f"项目目录: {PROJECT_DIR}")
    print(f"测试目录: {TESTS_DIR}")

    # 定义测试列表
    # ⚠️ 每次代码修改后必须运行完整测试集！
    tests = [
        ("test_empty_module_occupy.py", "单元测试 - 空模块 occupy 寄存器"),
        ("test_output_directory_structure.py", "目录结构测试 - 统一路径格式"),
        ("test_api_response_structure.py", "API 响应结构测试 - 文件分类逻辑"),
        ("test_svh_syntax.py", "SVH 语法测试 - C Header 转 SVH 正确性"),
        ("test_end_to_end.py", "端到端测试 - addr_map_S"),
        ("test_generate_module_files.py", "Generate 功能测试 - 模块文件生成"),
        ("test_download_api.py", "API 测试 - Download 功能"),
        ("test_incremental_scenario.py", "增量更新测试 - L2B.ralf 上传替换"),
        ("test_pec_reupload.py", "增量更新测试 - PEC.xls 重新上传"),
        ("test_ralf_upload_comprehensive.py", "RALF 综合测试 - 7个场景"),
        ("test_single_module_rtl.py", "单模块RTL测试 - 仅register类型页签"),
        ("test_rtl_file_detection.py", "RTL文件检测测试 - 子目录结构支持"),
        ("test_mixed_ralf_excel_upload.py", "混合上传测试 - RALF+Excel同时上传"),
    ]

    # 检查测试文件
    available_tests = []
    for test_file, description in tests:
        test_path = TESTS_DIR / test_file
        if test_path.exists():
            available_tests.append((test_file, description))
        else:
            print(f"⚠️  跳过不存在的测试: {test_file}")

    print(f"\n共 {len(available_tests)} 个测试")

    # 运行所有测试
    results = []
    start_time = time.time()

    for test_file, description in available_tests:
        passed = run_test(test_file, description)
        results.append((description, passed))

    total_time = time.time() - start_time

    # 打印结果汇总
    print(f"\n{'='*60}")
    print("测试汇总")
    print(f"{'='*60}")

    passed_count = sum(1 for _, passed in results if passed)
    failed_count = len(results) - passed_count

    for description, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {status} - {description}")

    print(f"\n总计: {len(results)} 个测试")
    print(f"  通过: {passed_count}")
    print(f"  失败: {failed_count}")
    print(f"  用时: {total_time:.1f}s")

    if failed_count == 0:
        print(f"\n🎉 所有测试通过！")
        return 0
    else:
        print(f"\n❌ 有 {failed_count} 个测试失败")
        return 1

if __name__ == "__main__":
    sys.exit(main())
