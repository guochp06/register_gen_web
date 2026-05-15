"""
Output Directory Validator
检查输出目录结构，防止重复嵌套问题
"""
import os
from pathlib import Path
from typing import List, Tuple


class OutputValidator:
    """验证输出目录结构是否正确"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.issues: List[str] = []

    def validate(self) -> Tuple[bool, List[str]]:
        """
        验证输出目录结构

        Returns:
            (is_valid: bool, issues: list of issue descriptions)
        """
        self.issues = []

        if not self.output_dir.exists():
            return True, []  # 空目录算有效

        # 检查每个版本目录
        for version_dir in self.output_dir.iterdir():
            if not version_dir.is_dir():
                continue

            # 跳过临时目录
            if version_dir.name == 'temp':
                continue

            self._check_version_dir(version_dir)

        return len(self.issues) == 0, self.issues

    def _check_version_dir(self, version_dir: Path):
        """检查单个版本目录"""
        version_name = version_dir.name

        # 检查是否有嵌套的同名目录 (v0.86/v0.86/)
        nested_dir = version_dir / version_name
        if nested_dir.exists() and nested_dir.is_dir():
            self.issues.append(
                f"重复目录: {version_dir.relative_to(self.output_dir)} 包含嵌套的同名子目录 "
                f"{version_name}/{version_name}/，这会导致文件生成到错误位置"
            )

        # 检查预期的格式子目录
        expected_formats = ['rdl', 'ralf', 'header', 'svh', 'uvm', 'rtl', 'html']
        found_formats = []

        for fmt in expected_formats:
            fmt_dir = version_dir / fmt
            if fmt_dir.exists() and fmt_dir.is_dir():
                found_formats.append(fmt)

        # 如果没有找到任何格式目录，可能生成位置有问题
        if not found_formats:
            # 检查是否有文件直接在版本目录下
            files_in_root = list(version_dir.iterdir())
            if files_in_root:
                files_str = ', '.join([f.name for f in files_in_root[:5]])
                self.issues.append(
                    f"目录结构异常: {version_name}/ 下没有找到格式子目录，"
                    f"但有文件/目录: {files_str}..."
                )

    def fix_nested_directories(self) -> List[str]:
        """
        自动修复嵌套目录问题

        Returns:
            list of fix actions taken
        """
        import shutil

        fixes = []

        for version_dir in self.output_dir.iterdir():
            if not version_dir.is_dir() or version_dir.name == 'temp':
                continue

            nested_dir = version_dir / version_dir.name
            if nested_dir.exists() and nested_dir.is_dir():
                # 移动嵌套目录中的文件到正确的位置
                for fmt_dir in nested_dir.iterdir():
                    if not fmt_dir.is_dir():
                        continue

                    target_dir = version_dir / fmt_dir.name

                    # 如果目标已存在，合并内容
                    if target_dir.exists():
                        for file in fmt_dir.iterdir():
                            if file.is_file():
                                target_file = target_dir / file.name
                                if not target_file.exists():
                                    shutil.move(str(file), str(target_file))
                                    fixes.append(f"Moved {file} to {target_file}")
                    else:
                        # 直接移动整个目录
                        shutil.move(str(fmt_dir), str(target_dir))
                        fixes.append(f"Moved {fmt_dir} to {target_dir}")

                # 删除空的嵌套目录
                if not any(nested_dir.iterdir()):
                    nested_dir.rmdir()
                    fixes.append(f"Removed empty nested dir {nested_dir}")
                else:
                    fixes.append(f"Warning: nested dir not empty {nested_dir}")

        return fixes


def validate_output_directory(output_dir: Path) -> bool:
    """
    快速验证输出目录，发现问题时打印警告

    Usage:
        from app.services.output_validator import validate_output_directory
        validate_output_directory(settings.OUTPUT_DIR)
    """
    validator = OutputValidator(output_dir)
    is_valid, issues = validator.validate()

    if not is_valid:
        print("⚠️  Output directory validation failed:")
        for issue in issues:
            print(f"   - {issue}")
        print("\nAttempting to fix...")
        fixes = validator.fix_nested_directories()
        if fixes:
            print("Fixes applied:")
            for fix in fixes:
                print(f"   - {fix}")
        else:
            print("No fixes needed or could not fix automatically")
        return False

    return True
