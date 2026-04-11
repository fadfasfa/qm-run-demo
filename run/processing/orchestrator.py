from __future__ import annotations

"""运行时编排层。

文件职责：
- 收敛抓取、自愈、预计算缓存重建和启动状态判断等后台编排入口

核心输入：
- 当前运行目录中的核心配置、CSV 和自愈状态

核心输出：
- 统一的后台刷新入口与就绪状态判断结果

主要依赖：
- `scraping.*`
- `processing.precomputed_cache`

维护提醒：
- 上层只应调用这里暴露的编排入口，不应在 UI 或 Web 中直接拼装多段抓取流程
"""

import os
import time

from processing.runtime_store import get_latest_csv
from scraping.full_hextech_scraper import main_scraper
from scraping.full_synergy_scraper import main as run_apex_spider
from scraping.heal_worker import heal_missing_artifacts
from processing.precomputed_cache import (
    has_precomputed_hextech_cache,
    load_precomputed_champion_list,
    rebuild_precomputed_api_cache_from_latest_csv,
)
from scraping.augment_catalog import (
    is_augment_icon_prefetch_ready,
    manifest_has_incomplete_entries,
    run_augment_icon_prefetch,
)
from scraping.version_sync import (
    AUGMENT_ICON_FILE,
    AUGMENT_MAP_FILE,
    CONFIG_DIR,
    CORE_DATA_FILE,
    sync_hero_data,
)


SYNERGY_FILE = os.path.join(CONFIG_DIR, "Champion_Synergy.json")


def is_first_run(force: bool = False) -> bool:
    if force:
        return True
    core_files_ready = all(
        os.path.exists(path)
        for path in (CORE_DATA_FILE, AUGMENT_MAP_FILE, AUGMENT_ICON_FILE, SYNERGY_FILE)
    )
    latest_csv = get_latest_csv()
    return not core_files_ready or not latest_csv or not os.path.exists(latest_csv)


def should_refresh_synergy(force: bool, stale_after_seconds: int) -> bool:
    if force or not os.path.exists(SYNERGY_FILE):
        return True
    try:
        return (os.path.getmtime(SYNERGY_FILE) + stale_after_seconds) < time.time()
    except OSError:
        return True


def run_hero_sync() -> bool:
    return bool(sync_hero_data())


def run_hextech_refresh(stop_event=None) -> bool:
    return bool(main_scraper(stop_event))


def run_synergy_refresh() -> bool:
    run_apex_spider()
    return os.path.exists(SYNERGY_FILE)


def run_augment_refresh(force_refresh: bool, stop_event=None) -> dict:
    return run_augment_icon_prefetch(
        force_refresh=force_refresh,
        stop_event=stop_event,
        max_workers=8,
    )


def current_api_cache_ready() -> bool:
    return bool(load_precomputed_champion_list()) and has_precomputed_hextech_cache()


def rebuild_api_cache_if_needed(force: bool = False) -> bool:
    latest_csv = get_latest_csv()
    if not latest_csv or not os.path.exists(latest_csv):
        return current_api_cache_ready()
    if force or not current_api_cache_ready():
        return bool(rebuild_precomputed_api_cache_from_latest_csv())
    return True


def refresh_backend_data(force: bool = False, stop_event=None) -> bool:
    """执行一次运行时自愈与后台刷新。

    这个入口用于 Web 启动、自检和桌面后台线程；它本身不直接拼接多段抓取逻辑，
    而是委托 `heal_missing_artifacts` 按缺失产物清单执行最小修复。
    """
    report = heal_missing_artifacts(force=force, stop_event=stop_event)
    repaired = set(report.get("repaired", []))
    if force or repaired:
        return True
    return bool(report.get("requested"))


def heal_runtime_artifacts(force: bool = False, stop_event=None) -> dict:
    return heal_missing_artifacts(force=force, stop_event=stop_event)


def get_startup_status_file() -> str:
    return os.path.join(CONFIG_DIR, "startup_status.json")


__all__ = [
    "SYNERGY_FILE",
    "current_api_cache_ready",
    "get_startup_status_file",
    "heal_runtime_artifacts",
    "is_augment_icon_prefetch_ready",
    "is_first_run",
    "manifest_has_incomplete_entries",
    "refresh_backend_data",
    "rebuild_api_cache_if_needed",
    "run_augment_refresh",
    "run_hero_sync",
    "run_hextech_refresh",
    "run_synergy_refresh",
    "should_refresh_synergy",
]
