"""桌面伴生兼容入口。

保留根目录启动方式不变，并把实际实现委托给 `display.hextech_ui`。
"""

from display.hextech_ui import HextechUI, run_desktop


def main() -> None:
    import sys

    if "--web-server" in sys.argv:
        from display.web_server import run_web_server

        run_web_server()
    else:
        run_desktop()


if __name__ == "__main__":
    main()
