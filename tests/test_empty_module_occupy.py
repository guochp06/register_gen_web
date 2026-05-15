#!/usr/bin/env python3
"""
测试空模块 occupy 寄存器功能

验证：空模块（无寄存器、无子模块）会自动添加 occupy 占位寄存器
满足 SystemRDL 要求，使 PeakRDL 编译通过
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/home/xiaoer/AI_GEN/regtool/backend')

from app.services.module_code_generator import ModuleCodeGenerator, Module, Register, RegisterField

def test_empty_module_occupy_register():
    """测试空模块生成 occupy 寄存器"""
    print("="*60)
    print("测试：空模块 occupy 寄存器生成")
    print("="*60)

    # 创建一个空模块（无寄存器、无子模块）
    empty_module = Module(
        name="Test_Empty_Module",
        start_addr=0x1000,
        end_addr=0x1FFF,
        size=0x1000,
        registers=[],      # 无寄存器
        submodules=[]      # 无子模块
    )

    # 验证是空模块
    assert len(empty_module.registers) == 0, "模块应该无寄存器"
    assert len(empty_module.submodules) == 0, "模块应该无子模块"
    print(f"✅ 创建空模块: {empty_module.name}")

    # 使用生成器生成代码
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir)
        generator = ModuleCodeGenerator(output_path)

        # 调用空模块生成函数
        result = {
            'rdl': {},
            'ralf': {},
            'header': {},
            'svh': {},
            'rtl': {},
            'uvm': {}
        }
        generator._generate_empty_module_rdl(empty_module, result)

        # 验证生成了 occupy 寄存器
        print(f"\n检查生成的代码:")

        # 1. 检查 RDL
        assert 'Test_Empty_Module' in result['rdl'], "应该生成 RDL"
        rdl_content = result['rdl']['Test_Empty_Module']
        assert 'occupy' in rdl_content, "RDL 应该包含 occupy 寄存器"
        assert 'occupy_val[31:0]' in rdl_content, "RDL 应该包含 32-bit 字段"
        assert 'sw = rw' in rdl_content, "RDL 字段应该是 RW 访问"
        print(f"✅ RDL 生成正确，包含 occupy 寄存器")

        # 2. 检查 RALF
        assert 'Test_Empty_Module' in result['ralf'], "应该生成 RALF"
        ralf_content = result['ralf']['Test_Empty_Module']
        assert 'occupy' in ralf_content, "RALF 应该包含 occupy 寄存器"
        assert 'bits 32' in ralf_content, "RALF 字段应该是 32-bit"
        assert 'access rw' in ralf_content, "RALF 字段应该是 RW 访问"
        print(f"✅ RALF 生成正确，包含 occupy 寄存器")

        # 3. 检查 C Header
        assert 'Test_Empty_Module' in result['header'], "应该生成 C Header"
        header_content = result['header']['Test_Empty_Module']
        assert 'OCCUPY_OFFSET' in header_content, "Header 应该包含 OCCUPY_OFFSET"
        assert 'OCCUPY_VAL_MASK' in header_content, "Header 应该包含 OCCUPY_VAL_MASK"
        print(f"✅ C Header 生成正确，包含 occupy 寄存器定义")

        # 4. 检查 SVH
        assert 'Test_Empty_Module' in result['svh'], "应该生成 SVH"
        svh_content = result['svh']['Test_Empty_Module']
        assert 'OCCUPY_OFFSET' in svh_content, "SVH 应该包含 OCCUPY_OFFSET"
        print(f"✅ SVH 生成正确，包含 occupy 寄存器定义")

        # 5. 验证 RDL 语法（PeakRDL 编译）
        from systemrdl import RDLCompiler
        rdlc = RDLCompiler()

        # 写入临时文件
        temp_rdl = Path(temp_dir) / "test_empty.rdl"
        temp_rdl.write_text(rdl_content)

        try:
            rdlc.compile_file(str(temp_rdl))
            root = rdlc.elaborate()
            print(f"✅ RDL 语法正确，PeakRDL 编译通过")
        except Exception as e:
            print(f"❌ RDL 编译失败: {e}")
            print(f"RDL 内容:\n{rdl_content}")
            return False

    print(f"\n" + "="*60)
    print("✅ 所有测试通过！空模块 occupy 寄存器功能正常")
    print("="*60)
    return True


def test_non_empty_module_no_occupy():
    """测试非空模块不会生成 occupy 寄存器"""
    print("\n" + "="*60)
    print("测试：非空模块不应使用 occupy 寄存器")
    print("="*60)

    # 创建一个有寄存器的模块
    field = RegisterField(
        name="test_field",
        msb=31,
        lsb=0,
        access="RW",
        reset_value="0x0",
        description="Test field"
    )
    reg = Register(
        name="TEST_REG",
        offset=0x0,
        width=32,
        fields=[field],
        description="Test register"
    )
    non_empty_module = Module(
        name="Test_Non_Empty",
        start_addr=0x2000,
        end_addr=0x2FFF,
        size=0x1000,
        registers=[reg],   # 有寄存器
        submodules=[]
    )

    # 使用生成器
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir)
        generator = ModuleCodeGenerator(output_path)

        # 生成基础模块（非空模块应该走正常流程）
        result = {
            'rdl': {},
            'ralf': {},
            'header': {},
            'svh': {},
            'rtl': {},
            'uvm': {}
        }

        # 正常模块调用 _generate_base_module，不是 _generate_empty_module_rdl
        from app.services.hierarchy_parser import RegisterHierarchy
        hierarchy = RegisterHierarchy(version_name="test_non_empty")
        hierarchy.all_modules = {'Test_Non_Empty': non_empty_module}

        generator._generate_base_module(non_empty_module, result, hierarchy.all_modules)

        # 验证生成了正常的寄存器，不是 occupy
        assert 'Test_Non_Empty' in result['rdl'], "应该生成 RDL"
        rdl_content = result['rdl']['Test_Non_Empty']
        assert 'TEST_REG' in rdl_content, "RDL 应该包含 TEST_REG"
        assert 'occupy' not in rdl_content, "RDL 不应该包含 occupy（因为模块不是空的）"
        print(f"✅ 非空模块生成正常寄存器，无 occupy 占位")

    print(f"\n" + "="*60)
    print("✅ 测试通过！非空模块正确处理")
    print("="*60)
    return True


def main():
    """主函数"""
    success = True

    if not test_empty_module_occupy_register():
        success = False

    if not test_non_empty_module_no_occupy():
        success = False

    if success:
        print("\n🎉 所有 occupy 寄存器测试通过！")
        return 0
    else:
        print("\n❌ 有测试失败")
        return 1


if __name__ == "__main__":
    sys.exit(main())
