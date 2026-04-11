from __future__ import annotations

"""预计算 API 缓存。

负责把最新 CSV 转换成首页榜单和单英雄海克斯详情缓存，
降低冷启动和无实时数据场景下的接口延迟。
"""

import json
import logging
import os
import shutil
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from processing.runtime_store import get_latest_csv, normalize_runtime_df
from processing.view_adapter import process_champions_data, process_hextechs_data
from scraping.version_sync import CONFIG_DIR

logger = logging.getLogger(__name__)
CHAMPION_LIST_CACHE_FILE = os.path.join(CONFIG_DIR, "Champion_List_Cache.json")
HEXTECH_DETAIL_CACHE_FILE = os.path.join(CONFIG_DIR, "Champion_Hextech_Cache.json")
HEXTECH_DETAIL_CACHE_DIR = os.path.join(CONFIG_DIR, "Champion_Hextech_Cache")

_cache_lock = threading.Lock()
_champion_cache_state: Dict[str, Any] = {"path": "", "mtime": 0.0, "data": []}
_hextech_cache_state: Dict[str, Any] = {"path": "", "mtime": 0.0, "data": {}}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _atomic_write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def _read_wrapped_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict) and "data" in payload:
            return payload.get("data", default)
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return default


def load_precomputed_champion_list() -> List[dict]:
    with _cache_lock:
        mtime = _safe_mtime(CHAMPION_LIST_CACHE_FILE)
        if (
            mtime
            and _champion_cache_state["path"] == CHAMPION_LIST_CACHE_FILE
            and _champion_cache_state["mtime"] == mtime
        ):
            return list(_champion_cache_state["data"])

        data = _read_wrapped_json(CHAMPION_LIST_CACHE_FILE, [])
        if isinstance(data, list):
            _champion_cache_state.update(
                {"path": CHAMPION_LIST_CACHE_FILE, "mtime": mtime, "data": data}
            )
            return list(data)
        return []


def has_precomputed_hextech_cache() -> bool:
    return bool(_safe_mtime(HEXTECH_DETAIL_CACHE_FILE))


def load_precomputed_hextech_for_hero(hero_name: str) -> Optional[dict]:
    normalized = str(hero_name or "").strip()
    if not normalized:
        return None

    with _cache_lock:
        mtime = _safe_mtime(HEXTECH_DETAIL_CACHE_FILE)
        if (
            mtime
            and _hextech_cache_state["path"] == HEXTECH_DETAIL_CACHE_FILE
            and _hextech_cache_state["mtime"] == mtime
        ):
            payload = _hextech_cache_state["data"]
        else:
            payload = _read_wrapped_json(HEXTECH_DETAIL_CACHE_FILE, {})
            if isinstance(payload, dict):
                _hextech_cache_state.update(
                    {"path": HEXTECH_DETAIL_CACHE_FILE, "mtime": mtime, "data": payload}
                )
            else:
                payload = {}

    result = payload.get(normalized) if isinstance(payload, dict) else None
    return result if isinstance(result, dict) else None


def write_precomputed_champion_list(champions: List[dict], source_tag: str) -> None:
    _atomic_write_json(
        CHAMPION_LIST_CACHE_FILE,
        {"meta": {"generated_at": _now_iso(), "source": source_tag}, "data": champions},
    )


def write_precomputed_hextech_map(hextech_by_hero: Dict[str, dict], source_tag: str) -> None:
    _atomic_write_json(
        HEXTECH_DETAIL_CACHE_FILE,
        {"meta": {"generated_at": _now_iso(), "source": source_tag}, "data": hextech_by_hero},
    )
    if os.path.isdir(HEXTECH_DETAIL_CACHE_DIR):
        shutil.rmtree(HEXTECH_DETAIL_CACHE_DIR, ignore_errors=True)


def rebuild_precomputed_api_cache_from_latest_csv() -> bool:
    latest_csv = get_latest_csv()
    if not latest_csv or not os.path.exists(latest_csv):
        return False

    try:
        df = normalize_runtime_df(pd.read_csv(latest_csv))
    except Exception as exc:
        logger.warning("读取最新 CSV 失败，无法重建本地 API 缓存：%s", exc)
        return False

    if df.empty or "英雄名称" not in df.columns:
        return False

    champions = process_champions_data(df, use_runtime_cache=False, log_columns=False)
    write_precomputed_champion_list(champions, os.path.basename(latest_csv))

    from scraping.augment_catalog import build_augment_catalog_lookup

    catalog_lookup = build_augment_catalog_lookup()
    hextech_by_hero: Dict[str, dict] = {}
    for hero_name, group in df.groupby("英雄名称", sort=False):
        if pd.isna(hero_name):
            continue
        hextech_by_hero[str(hero_name)] = process_hextechs_data(
            normalize_runtime_df(group.copy()),
            str(hero_name),
            catalog_lookup=catalog_lookup,
            use_runtime_cache=False,
            log_columns=False,
        )

    if hextech_by_hero:
        write_precomputed_hextech_map(hextech_by_hero, os.path.basename(latest_csv))
    return True
