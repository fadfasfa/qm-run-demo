# qm-run-demo

一个面向公开展示的精简版《英雄联盟》伴生查询 demo，保留了 `run/` 里的核心源码、Web 页面和桌面浮窗入口，去掉了本地缓存与私有运行产物。

## 项目背景 / 用途

这个仓库是从本地 `run` 项目整理出来的公开演示版。目标不是完整迁移私有仓库，而是保留一套外部读者能快速看懂、也能在本地启动的最小可用示例。

适合用来展示：

- 英雄与海克斯数据的本地整理流程
- Web 查询页面与 API 服务
- 桌面伴生浮窗
- 图标映射和本地缓存的组织方式

## 主要功能

- 提供本地 Web 服务，输出英雄列表、海克斯推荐和协同数据
- 提供桌面伴生界面，跟随客户端状态展示信息
- 将英雄别名、海克斯图标和展示数据做统一整理
- 支持本地图标回退和远程数据源补全
- 提供整理、清理和打包相关脚本

## 技术栈

- Python 3.11+
- FastAPI
- Uvicorn
- Pandas / NumPy
- Requests
- Tkinter
- psutil
- PyInstaller

## 仓库结构

```text
qm-run-demo/
├── README.md
├── docs/
│   └── project_summary.md
└── run/
    ├── alias_utils.py
    ├── apex_spider.py
    ├── backend_refresh.py
    ├── build.py
    ├── cleanup.py
    ├── data_processor.py
    ├── hero_sync.py
    ├── hextech_query.py
    ├── hextech_scraper.py
    ├── hextech_ui.py
    ├── icon_resolver.py
    ├── requirements.txt
    ├── static/
    └── web_server.py
```

## 如何运行

1. 进入代码目录：

```powershell
cd qm-run-demo\run
```

2. 安装依赖：

```powershell
pip install -r requirements.txt
```

3. 启动 Web 服务：

```powershell
python web_server.py
```

4. 或启动桌面伴生界面：

```powershell
python hextech_ui.py
```

## 我在这个仓库中负责的整理内容

- 将 `run/` 提炼为公开演示版，只保留核心源码和 `static/` 前端资源
- 删除本地缓存、日志、配置、构建产物和工作区元文件
- 新建面向外部读者的 README
- 补充 `.gitignore` 和简短项目说明文档

## 公开演示版说明

这是一个公开展示版，不是完整的私有研发仓库。

为了避免泄露本地环境信息或无关内容，以下内容没有保留：

- `config/`
- `assets/`
- `logs/`
- `temp/`
- `tmp/`
- `dist/`
- `build/`
- 工作区元文件和本地缓存
- 无关的旁路项目和临时文件

因此，仓库中的数据展示更适合演示和阅读，不包含完整的私有运行缓存。

## 注意事项

- 桌面伴生界面偏向 Windows 环境，相关依赖包含 `pywin32`。
- 首次运行可能需要本地生成缓存或拉取外部数据。
- 如果你只想看 Web 版本，优先运行 `web_server.py`。
