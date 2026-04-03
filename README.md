# qm-run-demo

一个精简的《英雄联盟》伴生查询 demo，展示英雄数据整理、海克斯推荐查询、本地 Web 页面和桌面浮窗能力。

## What it is

这是从本地 `run/` 项目整理出的公开演示版，不是完整的私有研发仓库。

它保留了最核心、最容易阅读的部分：

- 英雄与海克斯数据处理
- 本地 Web 服务和静态页面
- 桌面伴生浮窗入口
- 图标映射与本地回退逻辑

## Tech Stack

- Python 3.11+
- FastAPI
- Uvicorn
- Pandas / NumPy
- Requests
- Tkinter
- psutil

## Repository Layout

```text
qm-run-demo/
├── README.md
├── LICENSE
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

## Run

```powershell
cd qm-run-demo\run
pip install -r requirements.txt
python web_server.py
```

To open the desktop companion UI instead:

```powershell
python hextech_ui.py
```

## Notes

- This repo is intentionally trimmed for public sharing.
- Local caches, generated data, logs, build outputs, and workspace metadata were excluded.
- The desktop companion is Windows-oriented.
- First run may need network access to refresh data or icons.
