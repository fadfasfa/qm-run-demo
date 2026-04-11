"""Hextech 展示层聚合出口。

这里汇总桌面 UI 与 Web 服务的稳定入口，方便根目录薄壳与其他模块复用。
"""

from .hextech_ui import HextechUI, run_desktop
from .web_server import app, run_web, run_web_server

__all__ = ["HextechUI", "app", "run_desktop", "run_web", "run_web_server"]
