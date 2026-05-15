#!/usr/bin/env python3
"""
完整工作流程测试 - 从启动到生成多版本代码
"""
import sys
import os
import time
import subprocess
import requests
import shutil
from pathlib import Path

# 配置
BASE_DIR = Path("/home/xiaoer/AI_GEN/regtool")
BACKEND_URL = "http://localhost:8000"
FRONTEND_URL = "http://localhost:5173"
TEST_TIMEOUT = 300  # 5分钟

# 测试文件
EXCEL_FILES = [
    "/home/xiaoer/register/addr_map_S/soc_addr_map.xls",
    "/home/xiaoer/register/addr_map_S/GCS.xls",
    "/home/xiaoer/register/addr_map_S/C2C.xls",
    "/home/xiaoer/register/addr_map_S/DRAM_IF.xls",
    "/home/xiaoer/register/addr_map_S/PE.xls",
    "/home/xiaoer/register/addr_map_S/PEC.xls",
]

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

def log(msg, color=Colors.BLUE):
    print(f"{color}[TEST] {msg}{Colors.RESET}")

def check_services():
    """检查服务是否运行"""
    try:
        r = requests.get(f"{BACKEND_URL}/docs", timeout=5)
        backend_ok = r.status_code == 200
    except:
        backend_ok = False

    try:
        r = requests.get(FRONTEND_URL, timeout=5)
        frontend_ok = r.status_code == 200
    except:
        frontend_ok = False

    return backend_ok, frontend_ok

def wait_for_services(max_wait=60):
    """等待服务启动"""
    log("等待服务启动...")
    for i in range(max_wait):
        backend_ok, frontend_ok = check_services()
        if backend_ok and frontend_ok:
            log(f"服务已启动 (耗时 {i+1}s)", Colors.GREEN)
            return True
        time.sleep(1)
    log("服务启动超时！", Colors.RED)
    return False

def test_api_endpoints():
    """测试API端点"""
    log("测试API端点...")

    # 1. 创建版本
    log("  - 创建版本...")
    r = requests.post(f"{BACKEND_URL}/api/v1/versions", json={
        "name": "test_workflow_v1",
        "description": "Test workflow version 1"
    })
    assert r.status_code == 200, f"创建版本失败: {r.text}"
    version1 = r.json()
    version1_id = version1["id"]
    log(f"    ✓ 版本1创建成功 (ID: {version1_id})")

    # 2. 批量上传Excel文件
    log("  - 批量上传Excel文件...")
    files = [("files", open(f, "rb")) for f in EXCEL_FILES]
    r = requests.post(
        f"{BACKEND_URL}/api/v1/versions/{version1_id}/upload/batch",
        files=files
    )
    for _, f in files:
        f.close()

    assert r.status_code == 200, f"上传失败: {r.text}"
    result = r.json()
    assert result["success"], f"上传未成功: {result}"
    log(f"    ✓ 上传成功")
    log(f"    - 模块数: {result['modules_count']}")
    log(f"    - 寄存器数: {result['registers_count']}")
    log(f"    - 未实例化: {len(result.get('uninstantiated_modules', []))}")
    log(f"    - HTML: {result.get('html_url', 'None')}")

    if result.get('uninstantiated_modules'):
        log(f"    ⚠ 有 {len(result['uninstantiated_modules'])} 个未实例化模块", Colors.YELLOW)
        for um in result['uninstantiated_modules'][:3]:
            log(f"      - {um['name']}: {um['reason']}", Colors.YELLOW)

    # 3. 检查版本详情
    log("  - 检查版本详情...")
    r = requests.get(f"{BACKEND_URL}/api/v1/versions/{version1_id}")
    assert r.status_code == 200
    version_detail = r.json()
    log(f"    ✓ 版本详情获取成功")
    log(f"    - 层次结构: {'有' if version_detail.get('hierarchy') else '无'}")

    # 4. 检查生成的文件
    log("  - 检查生成的文件...")
    r = requests.get(f"{BACKEND_URL}/api/v1/versions/{version1_id}/files")
    assert r.status_code == 200
    files = r.json()
    log(f"    ✓ 文件列表获取成功")
    log(f"    - HTML存在: {files.get('html', {}).get('exists', False)}")
    log(f"    - 模块数: {len(files.get('modules', {}))}")

    # 5. 测试下载功能
    log("  - 测试文件下载...")
    # 下载RDL文件
    r = requests.get(f"{BACKEND_URL}/api/v1/versions/{version1_id}/download/rdl")
    if r.status_code == 200:
        log(f"    ✓ RDL下载成功")
    else:
        log(f"    ⚠ RDL下载失败: {r.status_code}", Colors.YELLOW)

    # 6. 创建第二个版本并上传
    log("  - 创建第二个版本...")
    r = requests.post(f"{BACKEND_URL}/api/v1/versions", json={
        "name": "test_workflow_v2",
        "description": "Test workflow version 2"
    })
    assert r.status_code == 200
    version2 = r.json()
    version2_id = version2["id"]
    log(f"    ✓ 版本2创建成功 (ID: {version2_id})")

    # 只上传部分文件到版本2
    log("  - 上传部分文件到版本2...")
    files = [("files", open(f, "rb")) for f in EXCEL_FILES[:3]]
    r = requests.post(
        f"{BACKEND_URL}/api/v1/versions/{version2_id}/upload/batch",
        files=files
    )
    for _, f in files:
        f.close()
    assert r.status_code == 200
    result = r.json()
    log(f"    ✓ 版本2上传成功")
    log(f"    - 模块数: {result['modules_count']}")

    # 7. 获取所有版本
    log("  - 获取所有版本...")
    r = requests.get(f"{BACKEND_URL}/api/v1/versions")
    assert r.status_code == 200
    versions = r.json()
    log(f"    ✓ 获取到 {len(versions)} 个版本")

    return version1_id, version2_id

def verify_output_files(version_id):
    """验证输出文件"""
    log(f"验证版本 {version_id} 的输出文件...")

    output_dirs = [
        BASE_DIR / "backend" / "output" / "html" / f"test_workflow_v{version_id}",
        BASE_DIR / "backend" / "output" / "rdl" / f"test_workflow_v{version_id}",
    ]

    for d in output_dirs:
        if d.exists():
            files = list(d.iterdir())
            log(f"  ✓ {d.name}: {len(files)} 个文件")
        else:
            log(f"  ✗ {d.name}: 目录不存在", Colors.RED)

def cleanup_versions(v1_id, v2_id):
    """清理测试版本"""
    log("清理测试版本...")

    for vid in [v1_id, v2_id]:
        try:
            r = requests.delete(f"{BACKEND_URL}/api/v1/versions/{vid}")
            if r.status_code == 200:
                log(f"  ✓ 版本 {vid} 已删除")
            else:
                log(f"  ⚠ 版本 {vid} 删除失败: {r.status_code}", Colors.YELLOW)
        except Exception as e:
            log(f"  ⚠ 版本 {vid} 删除出错: {e}", Colors.YELLOW)

def main():
    log("=" * 60)
    log("开始完整工作流程测试")
    log("=" * 60)

    # 检查测试文件是否存在
    log("检查测试文件...")
    for f in EXCEL_FILES:
        if not os.path.exists(f):
            log(f"✗ 测试文件不存在: {f}", Colors.RED)
            return False
    log(f"✓ 所有 {len(EXCEL_FILES)} 个测试文件存在")

    # 检查服务状态
    backend_ok, frontend_ok = check_services()
    if not backend_ok or not frontend_ok:
        log("服务未运行，请先启动服务:", Colors.RED)
        log(f"  后端: {'✓' if backend_ok else '✗'}")
        log(f"  前端: {'✓' if frontend_ok else '✗'}")
        log("")
        log("请运行: cd /home/xiaoer/AI_GEN/regtool && bash start.sh")
        return False

    log("服务已在运行")
    log(f"  后端: {BACKEND_URL}")
    log(f"  前端: {FRONTEND_URL}")

    # 运行测试
    v1_id = None
    v2_id = None
    try:
        v1_id, v2_id = test_api_endpoints()
        verify_output_files(1)
        verify_output_files(2)

        log("=" * 60)
        log("测试完成！全部通过 ✓", Colors.GREEN)
        log("=" * 60)
        return True

    except AssertionError as e:
        log(f"测试断言失败: {e}", Colors.RED)
        return False
    except Exception as e:
        log(f"测试出错: {e}", Colors.RED)
        import traceback
        traceback.print_exc()
        return False
    finally:
        if v1_id and v2_id:
            cleanup_versions(v1_id, v2_id)

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
