"""Web 服务启动壳。

这个文件只负责创建 FastAPI 应用、挂载静态目录并启动 Uvicorn。
所有路由定义委托给 `display.web_api`，所有运行时状态与后台任务委托给 `display.web_runtime`，
从而把 Web 的启动层、接口层和运行时层稳定拆开，同时控制模块数量不过度膨胀。
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .web_api import register_routes
from .web_runtime import (
    SERVER_PORT,
    find_available_port,
    get_static_dir,
    lifespan,
    logger,
    maybe_open_browser,
    set_active_web_port,
    write_active_web_port,
)

app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=get_static_dir()), name="static")
register_routes(app)


def run_web_server() -> None:
    """启动本地 Web 服务并同步当前生效端口。"""

    actual_port = find_available_port(SERVER_PORT)
    if actual_port != SERVER_PORT:
        logger.info("端口 %s 已被占用，改用端口 %s", SERVER_PORT, actual_port)

    set_active_web_port(actual_port)
    write_active_web_port(actual_port)
    maybe_open_browser(actual_port)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=actual_port,
        reload=False,
        access_log=False,
        log_level="warning",
    )


def run_web() -> None:
    """兼容保留的 Web 启动别名。"""

    run_web_server()


if __name__ == "__main__":
    run_web_server()
