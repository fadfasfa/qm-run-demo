from __future__ import annotations

"""运行时数据定位与 DataFrame 缓存。

文件职责：
- 统一管理运行时文件定位、CSV 读取优先级和 DataFrame 缓存

核心输入：
- 运行目录 `config/`
- 打包内稳定资源目录 `RESOURCE_DIR`

核心输出：
- 标准化后的 DataFrame
- 运行时文件路径解析结果

主要依赖：
- `scraping.version_sync`
- `processing.precomputed_cache`

维护提醒：
- Web 和 UI 对 CSV 的读取都应经由这里，避免各自实现路径和缓存策略
"""

import glob
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Tuple

import pandas as pd

from scraping.version_sync import CONFIG_DIR, RESOURCE_DIR


def runtime_priority_paths(relative_name: str) -> list[str]:
    """返回运行时优先路径列表，先查本地持久化目录，再查 bundle 内置资源。"""
    runtime_path = Path(CONFIG_DIR) / relative_name
    bundled_path = Path(RESOURCE_DIR) / "config" / relative_name
    candidates = [str(runtime_path)]
    bundled = str(bundled_path)
    if bundled not in candidates:
        candidates.append(bundled)
    return candidates


def resolve_runtime_file(relative_name: str) -> Optional[str]:
    """按运行时优先级解析一个文件的实际可用路径。"""
    for candidate in runtime_priority_paths(relative_name):
        if os.path.exists(candidate):
            return candidate
    return None


def get_latest_csv() -> Optional[str]:
    """返回最新战报 CSV 的路径，供 Web、UI 和预计算缓存共用。"""
    files = glob.glob(os.path.join(CONFIG_DIR, "Hextech_Data_*.csv"))
    if not files:
        return None
    files.sort(key=os.path.getmtime, reverse=True)
    return files[0]


def detect_hero_id_column(df: pd.DataFrame) -> Optional[str]:
    for col in df.columns:
        name = str(col)
        if "英雄ID" in name or name == "ID":
            return name
    return None


def normalize_runtime_df(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名和英雄 ID 字段格式，降低上层视图适配分支。"""
    if df.empty:
        return df

    normalized = df.copy()
    normalized.columns = normalized.columns.astype(str).str.replace(" ", "", regex=False)
    id_col = detect_hero_id_column(normalized)
    if id_col:
        normalized[id_col] = (
            normalized[id_col]
            .astype(str)
            .str.strip()
            .str.replace(".0", "", regex=False)
        )
    return normalized


@dataclass
class DataFrameCache:
    path: str = ""
    mtime: float = 0.0
    df: pd.DataFrame = field(default_factory=pd.DataFrame)


class CachedDataFrameLoader:
    def __init__(self, latest_path_getter: Callable[[], Optional[str]] = get_latest_csv):
        self._latest_path_getter = latest_path_getter
        self._cache = DataFrameCache()
        self._lock = threading.Lock()

    @property
    def cache_key(self) -> Tuple[str, float]:
        return self._cache.path, self._cache.mtime

    @property
    def cached_path(self) -> str:
        return self._cache.path

    @property
    def cached_mtime(self) -> float:
        return self._cache.mtime

    def get_df(self, force_refresh: bool = False) -> pd.DataFrame:
        """按文件路径与 mtime 做缓存，必要时重新解析最新 CSV。"""
        latest = self._latest_path_getter()
        if not latest:
            return pd.DataFrame()

        try:
            current_mtime = os.path.getmtime(latest)
        except OSError:
            return self._cache.df

        with self._lock:
            if (
                force_refresh
                or latest != self._cache.path
                or current_mtime != self._cache.mtime
            ):
                df = pd.read_csv(latest)
                self._cache = DataFrameCache(
                    path=latest,
                    mtime=current_mtime,
                    df=normalize_runtime_df(df),
                )
            return self._cache.df


def load_precomputed_champion_list():
    from processing.precomputed_cache import load_precomputed_champion_list as _load

    return _load()


def load_precomputed_hextech_for_hero(hero_name: str):
    from processing.precomputed_cache import load_precomputed_hextech_for_hero as _load

    return _load(hero_name)


def has_precomputed_hextech_cache() -> bool:
    from processing.precomputed_cache import has_precomputed_hextech_cache as _has

    return _has()


__all__ = [
    "CachedDataFrameLoader",
    "DataFrameCache",
    "detect_hero_id_column",
    "get_latest_csv",
    "has_precomputed_hextech_cache",
    "load_precomputed_champion_list",
    "load_precomputed_hextech_for_hero",
    "normalize_runtime_df",
    "resolve_runtime_file",
    "runtime_priority_paths",
]
