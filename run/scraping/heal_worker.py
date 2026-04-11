"""运行时自愈调度器。

在恢复 `run/scraping/` 包路径时提供最小自愈能力，供当前 `processing/` 与 `display/`
层安全调用，不要求一次性回滚全部历史目录结构。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock

from processing.runtime_store import get_latest_csv
from scraping.augment_catalog import (
    build_augment_icon_manifest,
    is_augment_icon_prefetch_ready,
    load_augment_icon_manifest,
    manifest_has_incomplete_entries,
    run_augment_icon_prefetch,
)
from scraping.full_hextech_scraper import main_scraper
from scraping.full_synergy_scraper import main as run_synergy_scraper
from scraping.version_sync import (
    ASSET_DIR,
    CONFIG_DIR,
    AUGMENT_ICON_FILE,
    AUGMENT_MAP_FILE,
    CORE_DATA_FILE,
    VERSION_FILE,
    cleanup_missing_assets,
    load_champion_core_data,
    sync_hero_data,
)

logger = logging.getLogger(__name__)
LOCK_FILE = Path(CONFIG_DIR) / "heal_worker.lock"


@dataclass
class HealReport:
    requested: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "requested": list(self.requested),
            "repaired": list(self.repaired),
            "failed": list(self.failed),
        }


def _latest_csv_ready() -> bool:
    latest_csv = get_latest_csv()
    return bool(latest_csv and os.path.exists(latest_csv))


def _core_data_ready() -> bool:
    return all(os.path.exists(path) for path in (CORE_DATA_FILE, AUGMENT_MAP_FILE, AUGMENT_ICON_FILE, VERSION_FILE))


def _augment_manifest_ready() -> bool:
    manifest = load_augment_icon_manifest()
    return bool(manifest) and not manifest_has_incomplete_entries()


def _image_assets_ready() -> bool:
    core_data = load_champion_core_data()
    if not core_data:
        return False
    for key in core_data.keys():
        asset_path = Path(ASSET_DIR) / f"{key}.png"
        if not asset_path.exists():
            return False
    return True


def detect_missing_artifacts() -> dict:
    latest_csv = get_latest_csv()
    return {
        "hextech_rankings": not _latest_csv_ready(),
        "synergy_data": not os.path.exists(os.path.join(CONFIG_DIR, "Champion_Synergy.json")),
        "augment_catalog": not _core_data_ready() or not _augment_manifest_ready(),
        "champion_core": not os.path.exists(CORE_DATA_FILE),
        "images": not _image_assets_ready(),
        "latest_csv": latest_csv or "",
        "augment_icons_prefetched": is_augment_icon_prefetch_ready(),
    }


def _heal_hero_rankings(stop_event=None) -> bool:
    if stop_event is not None and stop_event.is_set():
        return False
    return bool(main_scraper(stop_event))


def _heal_synergy_data() -> bool:
    if not run_synergy_scraper():
        return False
    return os.path.exists(os.path.join(CONFIG_DIR, "Champion_Synergy.json"))


def _heal_augment_catalog(force: bool = False, stop_event=None) -> bool:
    if force:
        manifest = build_augment_icon_manifest(force_refresh=True)
    else:
        manifest = load_augment_icon_manifest()
        if not manifest:
            manifest = build_augment_icon_manifest(force_refresh=False)
    if not manifest:
        return False
    result = run_augment_icon_prefetch(force_refresh=force, stop_event=stop_event, max_workers=8)
    return bool(result.get("ready"))


def _heal_champion_core() -> bool:
    return bool(sync_hero_data() and os.path.exists(CORE_DATA_FILE))


def _heal_images() -> bool:
    core_data = load_champion_core_data()
    if not core_data:
        return False
    missing = cleanup_missing_assets(max_retries=3, core_data=core_data)
    return not missing


def heal_missing_artifacts(*, force: bool = False, stop_event=None, include_alias_index: bool = False) -> dict:
    del include_alias_index
    report = HealReport()
    with FileLock(str(LOCK_FILE), timeout=30):
        if force or not _core_data_ready():
            report.requested.append("champion_core")
            if _heal_champion_core():
                report.repaired.append("champion_core")
            else:
                report.failed.append("champion_core")

        if force or not _latest_csv_ready():
            report.requested.append("hextech_rankings")
            if _heal_hero_rankings(stop_event=stop_event):
                report.repaired.append("hextech_rankings")
            else:
                report.failed.append("hextech_rankings")

        if force or not os.path.exists(os.path.join(CONFIG_DIR, "Champion_Synergy.json")):
            report.requested.append("synergy_data")
            if _heal_synergy_data():
                report.repaired.append("synergy_data")
            else:
                report.failed.append("synergy_data")

        if force or not _augment_manifest_ready():
            report.requested.append("augment_catalog")
            if _heal_augment_catalog(force=force, stop_event=stop_event):
                report.repaired.append("augment_catalog")
            else:
                report.failed.append("augment_catalog")

        if force or not _image_assets_ready():
            report.requested.append("images")
            if _heal_images():
                report.repaired.append("images")
            else:
                report.failed.append("images")

    payload = report.as_dict()
    message = "heal_worker completed: %s"
    if report.failed:
        logger.error(message, json.dumps(payload, ensure_ascii=False))
    elif report.repaired or report.requested:
        logger.warning(message, json.dumps(payload, ensure_ascii=False))
    else:
        logger.info(message, json.dumps(payload, ensure_ascii=False))
    return payload


def heal_once(force: bool = False, stop_event=None) -> dict:
    return heal_missing_artifacts(force=force, stop_event=stop_event)
