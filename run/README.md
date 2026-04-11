# Hextech 伴生系统运行指南

主项目文档位于当前目录的 `PROJECT.md`。

## 项目概述

`run/` 是 Hextech 伴生系统的实际运行目录，负责：

- 同步英雄核心数据、海克斯数据、协同数据和图标目录
- 提供桌面伴生窗口
- 提供本地 Web 页面与 API
- 产出带稳定资源白名单的打包结果

当前结构以真实代码为准，核心分为四层：

- `display/`：展示与启动层
- `processing/`：本地数据处理与视图适配层
- `scraping/`：远端抓取、自愈和稳定资源同步层
- `tools/`：打包、清理、日志与开发自检工具层

## 快速开始

### 安装依赖

```powershell
pip install -r requirements.txt
```

### 启动方式

```powershell
# 桌面伴生模式
python hextech_ui.py

# 仅启动 Web 服务
python web_server.py
```

### 打包

```powershell
python build.py
```

## 打包说明

- 默认产物为 `PyInstaller --onedir`
- 打包白名单会内置：
  - `display/static/`
  - 稳定 `config/` 资源
  - 稳定 `assets/` 图片资源
- 默认内置的稳定配置包括：
  - `Champion_Core_Data.json`
  - `Champion_Alias_Index.json`
  - `Augment_Icon_Manifest.json`
  - 兼容图标映射文件
- 以下运行态文件不会打包：
  - `Hextech_Data_*.csv`
  - `Champion_Hextech_Cache.json`
  - `Champion_List_Cache.json`
  - `Champion_Synergy.json`
  - `startup_status.json`
  - `web_server_port.txt`
  - 运行日志
- 运行时读取顺序为：包内稳定资源 -> 本地运行目录覆盖资源 -> 在线刷新生成资源

## 目录结构

```text
run/
├── build.py                       # 打包入口薄壳
├── hextech_ui.py                  # 桌面入口薄壳
├── web_server.py                  # Web 入口薄壳
├── display/                       # 展示与启动层
│   ├── hextech_ui.py              # 桌面 UI 主类与界面结构
│   ├── ui_runtime.py              # 桌面后台协同、线程、窗口同步、头像加载
│   ├── web_server.py              # Web 启动壳
│   ├── web_api.py                 # FastAPI 路由与接口编排
│   ├── web_runtime.py             # Web 运行时状态、LCU、缓存、浏览器与生命周期
│   └── static/                    # Web 静态页面与样式
├── processing/                    # 本地数据处理层
│   ├── runtime_store.py           # 运行时文件定位、CSV 缓存与 DataFrame 归一
│   ├── view_adapter.py            # 首页榜单与海克斯详情数据适配
│   ├── precomputed_cache.py       # 预计算 API 缓存
│   ├── query_terminal.py          # 终端查询输出
│   ├── alias_search.py            # 首页别名索引读取
│   ├── alias_utils.py             # 别名归一与去重
│   └── orchestrator.py            # 后台刷新与自愈统一编排入口
├── scraping/                      # 远端抓取与稳定资源同步层
│   ├── version_sync.py            # 稳定资源同步与运行环境引导
│   ├── full_hextech_scraper.py    # 海克斯数据抓取
│   ├── full_synergy_scraper.py    # 协同数据抓取
│   ├── augment_catalog.py         # 海克斯统一目录与预缓存
│   ├── icon_resolver.py           # 海克斯图标查找、缓存与远端回退
│   ├── heal_worker.py             # 缺失产物自愈修复
│   └── augment_common.py          # 海克斯目录公共辅助
└── tools/                         # 工具层
    ├── build_bundle.py            # 打包主流程
    ├── bundle_manifest.py         # 稳定资源白名单生成
    ├── runtime_bundle.py          # 打包后稳定资源播种
    ├── cleanup_runtime.py         # 构建与运行残留清理
    ├── log_utils.py               # 统一日志与 UTF-8 输出工具
    └── dev_checks.py              # 本地开发自检
```

## 常用接口

- `GET /api/champions`：英雄列表
- `GET /api/champion/{name}/hextechs`：英雄海克斯推荐
- `GET /api/champion_aliases`：首页搜索专用英雄别名索引
- `GET /api/augment_icon_map`：海克斯图标映射
- `GET /api/live_state`：当前 LCU 英雄选择状态
- `GET /api/synergies/{champ_id}`：英雄协同数据
- `POST /api/redirect`：浏览器跳转控制
- `GET /ws`：实时事件推送

## 维护说明

### 注释规范

- `run/` 下所有 Python 文件统一使用模块头注释说明职责、输入、输出、依赖和维护提醒
- 关键函数使用短 docstring 精准描述边界，不用冗长注释复述代码
- 重点覆盖 Web 启动、生命周期钩子、LCU 轮询、CSV/快照读取、UI 后台线程、资源缓存回退和打包主流程

### Web / UI 分层

- `display/web_server.py` 只负责起服，不承载业务路由
- `display/web_api.py` 只负责路由和接口编排
- `display/web_runtime.py` 负责 Web 运行时状态和热路径辅助
- `display/hextech_ui.py` 保留桌面 UI 主类和控件结构
- `display/ui_runtime.py` 承载桌面后台协同逻辑

### 工具文件说明

- `tools/build_bundle.py`
  负责打包主流程、版本文件生成、白名单构建和产物目录整理
- `tools/bundle_manifest.py`
  负责枚举稳定 `config/`、`assets/`、`display/static/` 并生成 manifest
- `tools/runtime_bundle.py`
  负责打包产物首次运行时把稳定资源播种到运行目录
- `tools/cleanup_runtime.py`
  负责清理构建产物、运行态缓存、端口文件、日志和 Python 缓存
- `tools/log_utils.py`
  负责统一日志过滤、source 标识和 UTF-8 终端输出
- `tools/dev_checks.py`
  负责本地结构校验、日志契约校验和打包配置自检，不属于正式测试框架

### 运行约束

- `Champion_Alias_Index.json` 是首页搜索专用静态索引，只读使用，不在运行时写回
- `Augment_Icon_Manifest.json` 是海克斯统一目录主链路
- 新增 Web 路由优先落在 `display/web_api.py`
- 新增 Web 生命周期、端口、浏览器、LCU、缓存逻辑优先落在 `display/web_runtime.py`
- 新增桌面后台线程、轮询、跳转或资源加载逻辑优先落在 `display/ui_runtime.py`
- 新增纯数据变换逻辑优先落在 `processing/`
- 新增远端同步、自愈和资源修复逻辑优先落在 `scraping/`
