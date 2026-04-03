# 清理项目工作痕迹和临时文件。

import os
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).parent


def print_step(msg: str):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}")


def cleanup_pycache():
    # 清理缓存目录和编译文件。
    print_step("清理 Python 缓存文件")
    count = 0

    for pycache in BASE_DIR.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache)
            count += 1
            print(f"  [删除] {pycache.relative_to(BASE_DIR)}")

    for pyc in BASE_DIR.rglob("*.pyc"):
        if pyc.is_file():
            pyc.unlink()
            count += 1
            print(f"  [删除] {pyc.relative_to(BASE_DIR)}")

    if count == 0:
        print("  [成功] 无可清理的缓存文件")
    else:
        print(f"  [成功] 已清理 {count} 个缓存文件/目录")


def cleanup_build_artifacts():
    # 清理打包生成的临时文件。
    print_step("清理打包临时文件")

    files_to_delete = [
        "Hextech.spec",
        "Hextech伴生终端.spec",
        "version_info.txt",
    ]

    count = 0
    for filename in files_to_delete:
        file_path = BASE_DIR / filename
        if file_path.exists():
            file_path.unlink()
            count += 1
            print(f"  [删除] {filename}")

    if count == 0:
        print("  [成功] 无可清理的打包临时文件")
    else:
        print(f"  [成功] 已清理 {count} 个打包临时文件")


def cleanup_log_files():
    # 清理日志文件。
    print_step("清理日志文件")

    config_dir = BASE_DIR / "config"
    if config_dir.exists():
        for log_file in config_dir.glob("*.log"):
            log_file.unlink()
            print(f"  [删除] {log_file.relative_to(BASE_DIR)}")

    dist_dir = BASE_DIR / "dist"
    if dist_dir.exists():
        for log_file in dist_dir.rglob("*.log"):
            log_file.unlink()
            print(f"  [删除] {log_file.relative_to(BASE_DIR)}")

    print("  [成功] 已清理日志文件")


def cleanup_build_dir():
    # 清理构建目录。
    print_step("清理 build 目录")

    build_dir = BASE_DIR / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
        print(f"  [删除] {build_dir}")
    else:
        print("  [成功] build 目录不存在")


def check_remaining_files():
    # 检查剩余的关键文件。
    print_step("检查剩余关键文件")

    essential_files = [
        "hextech_ui.py",
        "web_server.py",
        "hextech_query.py",
        "hextech_scraper.py",
        "backend_refresh.py",
        "hero_sync.py",
        "data_processor.py",
        "apex_spider.py",
        "build.py",
        "requirements.txt",
        "PROJECT.md",
        "assets/",
        "config/",
        "static/",
        "dist/",
    ]

    for item in essential_files:
        path = BASE_DIR / item.rstrip('/')
        exists = path.exists()
        status = "[保留]" if exists else "[缺失]"
        print(f"  {status} {item}")

    print("  [成功] 关键文件检查完成")


def main():
    print("\n" + "="*60)
    print("  Hextech 项目清理工具")
    print("="*60)

    try:
        cleanup_pycache()
        cleanup_build_artifacts()
        cleanup_log_files()
        cleanup_build_dir()
        check_remaining_files()

        print_step("清理完成")
        print("\n  提示:")
        print("  - 项目源代码和数据文件已保留")
        print("  - 打包产物保留在 dist/ 目录")
        print("  - 如需重新打包，请运行: python build.py")

    except Exception as e:
        print(f"\n  [错误] 清理失败：{e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
