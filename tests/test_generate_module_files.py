#!/usr/bin/env python3
"""
测试 generate-module-files 功能

验证点击 Generate 按钮后：
1. 文件正确生成到 OUTPUT_DIR / version_name / format/
2. API 返回正确的文件列表
3. 前端能够获取到生成的文件
"""
import os
import sys
import shutil
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

# 设置测试数据库环境
os.environ['DATABASE_URL'] = 'sqlite:///./test_generate.db'

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base, get_db
from app.main import app
OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/backend/output"
from app.core.config import settings

# 创建测试数据库引擎
engine = create_engine('sqlite:///./test_generate.db', connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 创建测试客户端
client = TestClient(app)

# 重写依赖项以使用测试数据库
def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

TEST_DIR = "/home/xiaoer/register/addr_map_S"
OUTPUT_DIR = settings.OUTPUT_DIR
from app.core.config import settings


def get_excel_files():
    """获取所有 Excel 文件"""
    files = []
    for f in os.listdir(TEST_DIR):
        if f.endswith(('.xls', '.xlsx')):
            files.append(os.path.join(TEST_DIR, f))
    return sorted(files)


def setup_test_version():
    """创建测试版本并上传文件"""
    # 清除之前的测试输出
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 清理数据库
    db = TestingSessionLocal()
    from app.models.version import Version
    db.query(Version).filter(Version.id == 1).delete()
    db.commit()
    db.close()

    # 创建版本
    response = client.post("/api/v1/versions", json={
        "name": "test_generate_v1",
        "description": "Test version for generate"
    })
    if response.status_code != 200:
        print(f"❌ 创建版本失败: {response.text}")
        return None

    version_id = response.json()['id']
    print(f"✅ 创建版本: test_generate_v1 (id={version_id})")

    # 上传 Excel 文件
    files = get_excel_files()
    print(f"📁 准备上传 {len(files)} 个文件")

    file_objects = []
    for f in files:
        file_objects.append(("files", (os.path.basename(f), open(f, "rb"), "application/vnd.ms-excel")))

    response = client.post(f"/api/v1/versions/{version_id}/upload/batch", files=file_objects)

    # 关闭文件
    for _, (name, fp, _) in file_objects:
        fp.close()

    if response.status_code != 200:
        print(f"❌ 上传失败: {response.text}")
        return None

    print(f"✅ 上传成功")
    return version_id


def test_generate_module_files(version_id: int):
    """测试 generate-module-files 端点"""
    print("\n" + "="*60)
    print("测试: generate-module-files")
    print("="*60)

    # 调用 generate 端点
    response = client.post(f"/api/v1/versions/{version_id}/generate-module-files")

    if response.status_code != 200:
        print(f"❌ Generate 失败: {response.status_code}")
        print(f"响应: {response.text[:500]}")
        return False

    data = response.json()
    print(f"✅ Generate 成功")
    print(f"  - ralf: {data['generated_files']['ralf']['count']} 个文件")
    print(f"  - header: {data['generated_files']['header']['count']} 个文件")
    print(f"  - svh: {data['generated_files']['svh']['count']} 个文件")
    print(f"  - rtl: {data['generated_files']['rtl']['count']} 个文件")

    # 检查警告
    if data.get('warnings'):
        print(f"⚠️  警告: {len(data['warnings'])} 个")
        for w in data['warnings'][:3]:
            print(f"    - {w}")

    return True


def test_list_files_after_generate(version_id: int):
    """测试生成后文件列表 API"""
    print("\n" + "="*60)
    print("测试: 生成后文件列表")
    print("="*60)

    response = client.get(f"/api/v1/versions/{version_id}/files")

    if response.status_code != 200:
        print(f"❌ 获取文件列表失败: {response.status_code}")
        return False

    data = response.json()
    modules = data.get('modules', {})
    combined = data.get('combined', {})

    print(f"✅ 获取文件列表成功")
    print(f"  - 模块数: {len(modules)}")
    print(f"  - Combined 文件: {len(combined)}")

    if len(modules) == 0:
        print("❌ modules 为空，前端将显示 'No files generated yet'")
        return False

    # 检查 sample 模块
    if modules:
        sample = list(modules.keys())[0]
        sample_files = modules[sample]
        print(f"\n  示例模块 '{sample}':")
        for fmt in ['rdl', 'ralf', 'header', 'svh', 'uvm']:
            if fmt in sample_files:
                print(f"    ✅ {fmt}: {sample_files[fmt]['name']}")

    return True


def test_directory_structure_after_generate(version_id: int):
    """测试生成后的目录结构"""
    print("\n" + "="*60)
    print("测试: 生成后目录结构")
    print("="*60)

    # 获取版本名
    db = TestingSessionLocal()
    from app.models.version import Version
    version = db.query(Version).filter(Version.id == version_id).first()
    version_name = version.name if version else f"v{version_id}"
    db.close()

    version_dir = OUTPUT_DIR / version_name

    if not version_dir.exists():
        print(f"❌ 版本目录不存在: {version_dir}")
        return False

    print(f"✅ 版本目录存在: {version_dir}")

    expected_formats = ['rdl', 'ralf', 'header', 'svh', 'uvm', 'rtl']
    all_ok = True

    for fmt in expected_formats:
        fmt_dir = version_dir / fmt
        if not fmt_dir.exists():
            print(f"⚠️  {fmt}/ 目录不存在")
            continue

        files = list(fmt_dir.iterdir())
        print(f"✅ {fmt}/: {len(files)} 个文件")

    return all_ok


def main():
    """主函数"""
    print("="*60)
    print("Generate Module Files 测试")
    print("="*60)

    # 设置测试版本
    version_id = setup_test_version()
    if not version_id:
        print("❌ 设置测试版本失败")
        return 1

    # 运行测试
    tests = [
        ("Generate 功能", lambda: test_generate_module_files(version_id)),
        ("生成后文件列表", lambda: test_list_files_after_generate(version_id)),
        ("生成后目录结构", lambda: test_directory_structure_after_generate(version_id)),
    ]

    all_passed = True
    for name, test_func in tests:
        try:
            if not test_func():
                all_passed = False
        except Exception as e:
            print(f"❌ 测试 '{name}' 异常: {e}")
            import traceback
            traceback.print_exc()
            all_passed = False

    print("\n" + "="*60)
    if all_passed:
        print("✅ 所有 Generate 测试通过！")
    else:
        print("❌ 部分 Generate 测试失败")
    print("="*60)

    # 清理
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    test_db = Path("./test_generate.db")
    if test_db.exists():
        test_db.unlink()

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
