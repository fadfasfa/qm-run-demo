"""Hextech 开发与发布工具层。

这个包只放与开发、打包、清理、验证相关的工具，不承载业务运行逻辑。
工具按职责聚合：
- `build_bundle.py`：发布包构建与资源白名单。
- `cleanup_runtime.py`：构建产物、缓存和运行态残留清理。
- `dev_checks.py`：本地自检与收口验证，不属于正式测试套件。
"""

from .build_bundle import main as build_bundle_main
from .cleanup_runtime import cleanup_build_outputs, cleanup_python_caches, cleanup_runtime_outputs


def run_dev_checks() -> None:
    from .dev_checks import main

    main()

__all__ = [
    "build_bundle_main",
    "cleanup_build_outputs",
    "cleanup_python_caches",
    "cleanup_runtime_outputs",
    "run_dev_checks",
]
