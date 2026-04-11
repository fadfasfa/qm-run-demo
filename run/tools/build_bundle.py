from __future__ import annotations

"""发布包构建工具。

文件职责：
- 串联构建前清理、bundle 白名单准备、PyInstaller 打包和产物整理

核心输入：
- `tools.bundle_manifest`
- `tools.cleanup_runtime`
- 根目录稳定资源和入口脚本

核心输出：
- `dist/` 下按日期整理的可分发目录

主要依赖：
- PyInstaller
- bundle manifest 和 runtime bundle 准备逻辑

维护提醒：
- 这里负责打包主流程，不负责运行时资源读写
- 新增隐藏依赖、白名单或排除模块时，要同步更新文档和开发自检
"""

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from tools.bundle_manifest import prepare_bundle_runtime
from tools.cleanup_runtime import cleanup_build_outputs, cleanup_python_caches


BASE_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = BASE_DIR / "dist"
BUILD_DIR = BASE_DIR / "build"
EXCLUDED_MODULES = [
    "tkinter.test",
    "unittest",
    "pydoc",
    "scipy",
    "matplotlib",
    "botocore",
    "boto3",
    "s3transfer",
    "jmespath",
]


def print_step(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}\n")


def print_check(msg: str):
    print(f"  [成功] {msg}")


def print_error(msg: str):
    print(f"  [失败] {msg}")


def print_warn(msg: str):
    print(f"  [警告] {msg}")


def cleanup() -> None:
    """清理历史构建输出和 Python 缓存，保证打包环境干净。"""
    print_step("清理旧构建文件")
    for target in cleanup_build_outputs():
        print_check(f"已删除：{target}")
    removed_dirs, removed_files = cleanup_python_caches()
    print_check(f"已清理 Python 缓存目录 {removed_dirs} 个，缓存文件 {removed_files} 个")


def generate_version_info() -> Path:
    """生成供 PyInstaller 注入的 Windows 版本信息文件。"""
    print_step("生成版本信息")
    version_file = BASE_DIR / "version_info.txt"
    version_content = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({datetime.now().year}, 4, 7, 0),
    prodvers=({datetime.now().year}, 4, 7, 0),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Hextech Nexus'),
          StringStruct('FileDescription', 'Hextech 伴生系统 - 英雄联盟海克斯数据分析工具'),
          StringStruct('FileVersion', '{datetime.now().strftime("%Y.%m.%d.%H")}'),
          StringStruct('InternalName', 'HextechTerminal'),
          StringStruct('LegalCopyright', 'Copyright © Hextech Nexus'),
          StringStruct('OriginalFilename', 'Hextech伴生终端.exe'),
          StringStruct('ProductName', 'Hextech Companion'),
          StringStruct('ProductVersion', '{datetime.now().strftime("%Y.%m.%d")}'),
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""
    version_file.write_text(version_content, encoding="utf-8")
    print_check(f"版本信息已生成: {version_file}")
    return version_file


def prepare_runtime_bundle() -> Path:
    """生成 bundle 运行时白名单目录，供构建命令直接打入产物。"""
    print_step("准备稳定基础资源")
    bundle_root = prepare_bundle_runtime(BASE_DIR, BUILD_DIR)
    print_check("静态页面已加入打包白名单")
    print_check("稳定 config 已加入打包白名单")
    print_check("稳定 assets 已加入打包白名单")
    print_warn("高频战报 CSV、预计算缓存、协同数据和运行日志不会打包")
    return bundle_root


def build_exe(version_file: Path, bundle_root: Path) -> Path:
    """执行 PyInstaller 主构建流程，并返回原始产物目录。"""
    print_step("构建可执行文件")
    cmd = [
        "pyinstaller",
        "--clean",
        "--noconfirm",
        "--name", "Hextech伴生终端",
        "--onedir",
        "--console",
        "--icon", "NONE",
        "--version-file", str(version_file),
        "--add-data", f"{bundle_root / 'static'};static",
        "--add-data", f"{bundle_root / 'config'};config",
        "--add-data", f"{bundle_root / 'assets'};assets",
        "--add-data", f"{bundle_root / 'bundle_manifest.json'};.",
        "--hidden-import", "pandas",
        "--hidden-import", "numpy",
        "--hidden-import", "requests",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL.ImageTk",
        "--hidden-import", "win32gui",
        "--hidden-import", "psutil",
        "--hidden-import", "fastapi",
        "--hidden-import", "uvicorn",
        "--hidden-import", "filelock",
        "--collect-submodules", "uvicorn",
        "hextech_ui.py",
    ]
    for module_name in EXCLUDED_MODULES:
        cmd.extend(["--exclude-module", module_name])

    try:
        subprocess.run(
            cmd,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print_error(f"构建失败：\n{exc.stderr}")
        sys.exit(1)

    print_check("构建成功")
    return DIST_DIR / "Hextech伴生终端"


def finalize_output(exe_dir: Path) -> Path:
    """整理最终输出目录，移除旧重复目录并按日期命名产物。"""
    print_step("最终优化")
    final_dir = DIST_DIR / f"Hextech_伴生系统_{datetime.now().strftime('%Y%m%d')}"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.move(str(exe_dir), str(final_dir))
    legacy_duplicate_dir = BUILD_DIR / "Hextech伴生终端"
    if legacy_duplicate_dir.exists():
        shutil.rmtree(legacy_duplicate_dir, ignore_errors=True)
    print_check(f"输出目录：{final_dir}")
    return final_dir


def main():
    """打包工具主流程入口。"""
    print("\n" + "=" * 60)
    print("  Hextech 伴生系统打包程序")
    print(f"  构建时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    cleanup()
    bundle_root = prepare_runtime_bundle()
    version_file = generate_version_info()
    exe_dir = build_exe(version_file, bundle_root)
    final_dir = finalize_output(exe_dir)
    print_step("打包完成")
    print(f"  输出目录：{final_dir}")
    print(f"  主程序：{final_dir / 'Hextech伴生终端.exe'}")
