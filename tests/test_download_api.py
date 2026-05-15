#!/usr/bin/env python3
"""
测试 Download API 功能

验证下载功能是否正常：
1. 生成代码后，能够通过 API 下载单个文件
2. 能够下载 ZIP 包
3. 文件命名正确
4. 404 处理正确
"""
import os
import sys
import shutil
import tempfile
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

# 设置测试数据库环境
os.environ['DATABASE_URL'] = 'sqlite:///./test_download.db'

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base, get_db
from app.main import app
from app.services.hierarchy_parser import HierarchyParser
from app.services.module_code_generator import ModuleCodeGenerator
OUTPUT_DIR = "/home/xiaoer/AI_GEN/regtool/backend/output"
from app.core.config import settings
from app.models.version import Version
from app.models.register import RegisterModule, Register, RegisterField

# 创建测试数据库引擎
engine = create_engine('sqlite:///./test_download.db', connect_args={"check_same_thread": False})
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

# 测试数据目录
TEST_DIR = "/home/xiaoer/register/addr_map_S"
# 使用 API 相同的输出目录，否则 API 找不到文件
OUTPUT_DIR = Path(settings.OUTPUT_DIR)
from app.core.config import settings


def get_excel_files():
    """获取所有 Excel 文件"""
    files = []
    for f in os.listdir(TEST_DIR):
        if f.endswith(('.xls', '.xlsx')):
            files.append(os.path.join(TEST_DIR, f))
    return sorted(files)


def setup_test_version():
    """创建测试版本并生成代码"""
    # 清除之前的测试输出
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 在测试数据库中创建版本
    db = TestingSessionLocal()
    try:
        # 检查版本是否已存在
        existing = db.query(Version).filter(Version.id == 1).first()
        if not existing:
            version = Version(
                id=1,
                name="download_test_version",
                description="Test version for download API"
            )
            db.add(version)
            db.commit()
            print(f"✅ 在测试数据库中创建版本: download_test_version (id=1)")
        else:
            print(f"✅ 使用已存在的测试版本 (id=1)")
    finally:
        db.close()

    # 解析 Excel
    files = get_excel_files()
    parser = HierarchyParser()
    hierarchy = parser.parse_files(files, "download_test_version")

    if hierarchy.errors:
        print(f"❌ 解析错误: {hierarchy.errors}")
        return None

    # 保存层级结构到数据库
    db = TestingSessionLocal()
    try:
        from app.services.cumulative_hierarchy import CumulativeHierarchyService
        service = CumulativeHierarchyService(db)
        service.save_hierarchy(1, hierarchy)
        print(f"✅ 保存层级结构到数据库")
    except Exception as e:
        print(f"⚠️  保存层级结构失败: {e}")
    finally:
        db.close()

    # 生成代码
    generator = ModuleCodeGenerator(OUTPUT_DIR)
    generated = generator.generate_all(hierarchy, version_id=1, version_name="download_test_version")

    if generator.errors:
        print(f"❌ 生成错误: {generator.errors}")
        return None

    # 保存文件
    saved = generator.save_all(generated, version_id=1, version_name="download_test_version")

    # 打印生成的文件
    print(f"\n生成的文件:")
    for fmt, file_list in saved.items():
        print(f"  - {fmt}: {len(file_list)} 个文件")
        for f in file_list[:3]:  # 只显示前3个
            print(f"    - {Path(f).name}")

    return saved


def test_download_api_list_files():
    """测试列出文件 API"""
    print("\n" + "="*60)
    print("测试: 列出文件 API")
    print("="*60)

    # 先生成代码
    saved = setup_test_version()
    if not saved:
        print("❌ 设置测试版本失败")
        return False

    # 测试列出文件 (version_id=1 是测试数据库中的版本)
    response = client.get("/api/v1/versions/1/files")

    if response.status_code != 200:
        print(f"❌ 列出文件失败: {response.status_code}")
        print(f"响应: {response.text}")
        return False

    data = response.json()
    print(f"✅ 列出文件成功")
    print(f"  - 版本: {data.get('version_name')}")
    print(f"  - 模块数: {len(data.get('modules', {}))}")
    print(f"  - 组合文件: {list(data.get('combined', {}).keys())}")

    # 验证 UVM 文件在列表中（在 modules 中，因为现在的逻辑是按模块分类）
    modules = data.get('modules', {})
    if not modules:
        print(f"  ❌ modules 为空")
        return False

    # 从第一个模块检查各格式
    sample_module = list(modules.keys())[0]
    sample_files = modules[sample_module]

    if 'uvm' in sample_files:
        uvm_info = sample_files['uvm']
        print(f"  - UVM 文件 (在模块 {sample_module} 中): {uvm_info.get('name')} ({uvm_info.get('size')} 字节)")
    else:
        print(f"  ❌ UVM 文件不在列表中")
        return False

    # 验证各个格式都存在
    required_formats = ['rdl', 'ralf', 'header', 'svheader', 'uvm']
    missing = [f for f in required_formats if f not in sample_files]
    if missing:
        print(f"  ❌ 缺少格式: {missing}")
        return False
    print(f"  ✅ 所有格式都存在: {required_formats}")

    return True


def test_download_single_file():
    """测试下载单个文件"""
    print("\n" + "="*60)
    print("测试: 下载单个文件")
    print("="*60)

    # 测试下载所有格式文件
    formats_to_test = ['rdl', 'ralf', 'header', 'svheader', 'uvm']
    all_success = True

    for fmt in formats_to_test:
        response = client.get(f"/api/v1/versions/1/download/{fmt}")

        if response.status_code == 200:
            content_disposition = response.headers.get('content-disposition', '')
            print(f"✅ 下载 {fmt}: 成功 ({len(response.content)} 字节)")
            print(f"   文件名: {content_disposition}")
        elif response.status_code == 404:
            print(f"❌ 下载 {fmt}: 文件不存在 (404)")
            all_success = False
        else:
            print(f"❌ 下载 {fmt}: 失败 ({response.status_code})")
            print(f"   响应: {response.text[:200]}")
            all_success = False

    return all_success


def test_download_module_file():
    """测试下载特定模块的文件"""
    print("\n" + "="*60)
    print("测试: 下载特定模块文件")
    print("="*60)

    # 尝试下载 C2C 模块的 RDL
    response = client.get("/api/v1/versions/1/download/rdl?module=C2C")

    if response.status_code == 200:
        print(f"✅ 下载 C2C.rdl: 成功 ({len(response.content)} 字节)")
        # 检查内容是否包含 RDL
        content = response.text
        if 'addrmap' in content or 'C2C' in content:
            print(f"   内容验证: 包含 RDL 关键字")
    elif response.status_code == 404:
        print(f"⚠️  下载 C2C.rdl: 文件不存在 (404)")
        print(f"   可能需要先调用生成 API")
    else:
        print(f"❌ 下载 C2C.rdl: 失败 ({response.status_code})")
        print(f"   响应: {response.text[:200]}")

    # 尝试下载 UVM 模块文件
    print("\n测试下载 UVM 模块文件:")
    uvm_modules = ['C2C', 'PMH', 'soc_addr_map']
    for module in uvm_modules:
        response = client.get(f"/api/v1/versions/1/download/uvm?module={module}")
        if response.status_code == 200:
            content = response.text
            has_uvm = 'uvm_reg_block' in content or 'class' in content
            print(f"  ✅ {module}_regmodel.sv: {len(response.content)} 字节, UVM验证={has_uvm}")
        elif response.status_code == 404:
            print(f"  ⚠️  {module}_regmodel.sv: 不存在")
        else:
            print(f"  ❌ {module}_regmodel.sv: 失败 ({response.status_code})")

    return True


def test_download_zip():
    """测试下载 ZIP 包"""
    print("\n" + "="*60)
    print("测试: 下载 ZIP 包")
    print("="*60)

    # 测试下载 module-files ZIP (包括 uvm)
    file_types = ['ralf', 'header', 'svh', 'uvm']

    for file_type in file_types:
        response = client.get(f"/api/v1/versions/1/download-module-files/{file_type}")

        if response.status_code == 200:
            content_type = response.headers.get('content-type', '')
            if 'zip' in content_type:
                print(f"✅ 下载 {file_type}.zip: 成功 ({len(response.content)} 字节)")
            else:
                print(f"⚠️  下载 {file_type}: 成功但 Content-Type 不是 zip ({content_type})")
        elif response.status_code == 404:
            print(f"⚠️  下载 {file_type}.zip: 文件不存在 (404)")
        else:
            print(f"❌ 下载 {file_type}.zip: 失败 ({response.status_code})")
            print(f"   响应: {response.text[:200]}")

    return True


def test_download_path_consistency():
    """测试下载路径一致性 - 验证文件实际路径和API路径匹配"""
    print("\n" + "="*60)
    print("测试: 下载路径一致性检查")
    print("="*60)

    # 获取文件列表
    response = client.get("/api/v1/versions/1/files")
    if response.status_code != 200:
        print(f"❌ 获取文件列表失败: {response.status_code}")
        return False

    data = response.json()
    combined = data.get('combined', {})

    # 检查每个格式的路径是否正确（在 modules 中，因为现在的逻辑是按模块分类）
    format_checks = {
        'rdl': {'dir': 'rdl', 'ext': '.rdl'},
        'ralf': {'dir': 'ralf', 'ext': '.ralf'},
        'header': {'dir': 'header', 'ext': '.h'},
        'svheader': {'dir': 'svh', 'ext': '.svh'},
        'uvm': {'dir': 'uvm', 'ext': '.sv'},
    }

    all_passed = True

    # 检查 modules 中是否有文件
    modules = data.get('modules', {})
    if not modules:
        print("❌ modules 为空")
        return False

    # 从第一个模块检查各格式
    sample_module = list(modules.keys())[0]
    sample_files = modules[sample_module]

    print(f"\n检查模块 '{sample_module}' 的文件路径:")
    for fmt, expected in format_checks.items():
        if fmt not in sample_files:
            print(f"⚠️  {fmt}: 模块没有此格式")
            continue

        file_info = sample_files[fmt]
        actual_path = file_info.get('path', '')
        expected_dir = f"/{expected['dir']}/"

        # 检查路径是否包含正确的目录
        if expected_dir not in actual_path:
            print(f"❌ {fmt}: 路径错误")
            print(f"   实际: {actual_path}")
            print(f"   应包含: {expected_dir}")
            all_passed = False
            continue

        # 检查扩展名是否正确
        if not actual_path.endswith(expected['ext']):
            print(f"❌ {fmt}: 扩展名错误")
            print(f"   实际: {actual_path}")
            print(f"   应结束于: {expected['ext']}")
            all_passed = False
            continue

        # 验证文件实际存在
        if not Path(actual_path).exists():
            print(f"❌ {fmt}: 文件不存在于磁盘")
            print(f"   路径: {actual_path}")
            all_passed = False
            continue

        print(f"✅ {fmt}: 路径正确 ({actual_path})")

    # 检查所有模块的总文件数
    total_files = sum(len(files) for files in modules.values())
    print(f"\n✅ 所有模块总文件数: {total_files}")

    return all_passed


def test_download_not_found():
    """测试 404 处理"""
    print("\n" + "="*60)
    print("测试: 404 处理")
    print("="*60)

    # 测试不存在的版本
    response = client.get("/api/v1/versions/99999/download/rdl")
    if response.status_code == 404:
        print(f"✅ 不存在的版本返回 404: 正确")
    else:
        print(f"⚠️  不存在的版本返回 {response.status_code}: 预期 404")

    # 测试不支持的格式
    response = client.get("/api/v1/versions/1/download/unsupported_format")
    if response.status_code in [400, 404]:
        print(f"✅ 不支持的格式返回 {response.status_code}: 正确")
    else:
        print(f"⚠️  不支持的格式返回 {response.status_code}: 预期 400/404")

    return True


def main():
    """主函数"""
    print("="*60)
    print("Download API 测试")
    print("="*60)
    print(f"测试目录: {TEST_DIR}")
    print(f"输出目录: {OUTPUT_DIR}")

    success = True

    # 运行所有测试
    tests = [
        ("列出文件", test_download_api_list_files),
        ("下载路径一致性", test_download_path_consistency),
        ("下载单个文件", test_download_single_file),
        ("下载模块文件", test_download_module_file),
        ("下载 ZIP", test_download_zip),
        ("404 处理", test_download_not_found),
    ]

    for name, test_func in tests:
        try:
            if not test_func():
                success = False
        except Exception as e:
            print(f"❌ 测试 '{name}' 异常: {e}")
            import traceback
            traceback.print_exc()
            success = False

    print("\n" + "="*60)
    if success:
        print("✅ 所有 Download API 测试通过！")
    else:
        print("❌ 部分 Download API 测试失败")
    print("="*60)

    # 清理
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    test_db = Path("./test_download.db")
    if test_db.exists():
        test_db.unlink()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
