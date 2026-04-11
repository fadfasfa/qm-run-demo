"""Web 服务兼容入口。

保留根目录启动方式不变，并把实际实现委托给 `display.web_server`。
"""

from display.web_server import app, run_web, run_web_server


if __name__ == "__main__":
    run_web_server()
