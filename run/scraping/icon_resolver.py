"""海克斯图标查找、缓存与远端兜底。

文件职责：
- 统一解析海克斯图标文件名、清单缓存和本地资源路径
- 在本地图标缺失时执行 CommunityDragon / apexlol 远端回退

核心输入：
- `Augment_Icon_Map.json`
- `Augment_Apexlol_Map.json`
- 本地 `assets/` 目录

核心输出：
- 本地图标文件名
- 本地图标 URL 或远端兜底 URL
- 批量预取结果

主要依赖：
- 本地 `config/` 和 `assets/`
- CommunityDragon 与 apexlol

维护提醒：
- 资源缓存策略和失败 TTL 要与 Web / UI 热路径一起评估，避免重复下载
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from typing import Dict, Iterable, Optional, Tuple
from urllib.parse import quote

import requests


_ICON_MAP_CACHE: Tuple[str, float, dict] = ("", 0.0, {})
_APEXLOL_MAP_CACHE: Tuple[str, float, dict] = ("", 0.0, {})
_ICON_MAP_LOCK = threading.Lock()
_APEXLOL_MAP_LOCK = threading.Lock()
_THREAD_LOCAL = threading.local()
_ICON_FAILURE_CACHE: Dict[str, float] = {}
_ICON_FAILURE_CACHE_LOCK = threading.Lock()
_FAILURE_TTL_SECONDS = 180


def _default_runtime_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_config_dir(config_dir: Optional[str]) -> str:
    return config_dir or os.path.join(_default_runtime_dir(), "config")


def _resolve_assets_dir(asset_dir: Optional[str]) -> str:
    return asset_dir or os.path.join(_default_runtime_dir(), "assets")


def normalize_augment_name(name: str) -> str:
    name = str(name).lower()
    for token in (" ", "-", "_", "(", ")", "[", "]", "'", '"', "."):
        name = name.replace(token, "")
    return name


def normalize_augment_filename(value: str) -> str:
    return os.path.basename(str(value).strip()).lower()


def find_existing_augment_asset_filename(asset_dir: Optional[str], candidate_filename: str) -> Optional[str]:
    """在本地资源目录中查找最匹配的海克斯图标文件名。"""
    asset_dir = _resolve_assets_dir(asset_dir)
    candidate = normalize_augment_filename(candidate_filename)
    if not candidate:
        return None

    direct_path = os.path.join(asset_dir, candidate)
    if os.path.exists(direct_path):
        return os.path.basename(direct_path)

    candidate_stem = os.path.splitext(candidate)[0]
    normalized_candidate_stem = normalize_augment_name(candidate_stem)
    best_match = None
    try:
        for entry in os.scandir(asset_dir):
            if not entry.is_file():
                continue
            entry_name = entry.name
            entry_norm = normalize_augment_filename(entry_name)
            if entry_norm == candidate:
                return entry_name
            entry_stem = os.path.splitext(entry_name)[0]
            entry_stem_norm = normalize_augment_name(entry_stem)
            if entry_stem_norm == normalized_candidate_stem:
                return entry_name
            if best_match is None and (
                entry_stem_norm.startswith(normalized_candidate_stem)
                or normalized_candidate_stem.startswith(entry_stem_norm)
            ):
                best_match = entry_name
    except OSError:
        return None
    return best_match


def load_augment_icon_map(config_dir: Optional[str] = None, force_refresh: bool = False) -> dict:
    """读取海克斯图标映射并按文件 mtime 做内存缓存。"""
    global _ICON_MAP_CACHE
    config_dir = _resolve_config_dir(config_dir)
    icon_map_path = os.path.join(config_dir, "Augment_Icon_Map.json")

    with _ICON_MAP_LOCK:
        cached_path, cached_mtime, cached_data = _ICON_MAP_CACHE
        if not force_refresh and cached_path == icon_map_path and cached_data:
            try:
                if os.path.getmtime(icon_map_path) == cached_mtime:
                    return cached_data
            except OSError:
                return cached_data

    try:
        current_mtime = os.path.getmtime(icon_map_path)
    except OSError:
        return _ICON_MAP_CACHE[2]

    try:
        with open(icon_map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _ICON_MAP_LOCK:
                _ICON_MAP_CACHE = (icon_map_path, current_mtime, data)
            return data
    except Exception:
        pass

    return _ICON_MAP_CACHE[2]


def find_augment_icon_filename(icon_map: dict, lookup_name: str, asset_dir: Optional[str] = None) -> Optional[str]:
    if not icon_map or not lookup_name:
        return None

    direct = icon_map.get(lookup_name)
    if direct:
        local_filename = find_existing_augment_asset_filename(asset_dir, direct)
        return local_filename or normalize_augment_filename(direct)

    normalized_lookup = normalize_augment_name(lookup_name)
    for key, value in icon_map.items():
        if normalize_augment_name(key) == normalized_lookup:
            local_filename = find_existing_augment_asset_filename(asset_dir, value)
            return local_filename or normalize_augment_filename(value)
    return None


def build_local_augment_icon_url(hextech_name: str, config_dir: Optional[str] = None) -> str:
    """优先返回本地图标 URL；本地无命中时回退 apexlol 远端图标地址。"""
    config_dir = _resolve_config_dir(config_dir)
    request_name = str(hextech_name).strip()
    asset_name = request_name

    icon_map = load_augment_icon_map(config_dir=config_dir)
    if icon_map:
        mapped_value = icon_map.get(request_name)
        if mapped_value is None:
            normalized_name = normalize_augment_name(request_name)
            for key, value in icon_map.items():
                if normalize_augment_name(key) == normalized_name:
                    mapped_value = value
                    break
        if mapped_value:
            resolved_name = find_existing_augment_asset_filename(
                os.path.join(config_dir, "..", "assets"),
                str(mapped_value).split("/")[-1].strip(),
            )
            asset_name = resolved_name or str(mapped_value).split("/")[-1].strip()
        else:
            apexlol_url = resolve_apexlol_hextech_icon_url(request_name, config_dir=config_dir)
            if apexlol_url:
                return apexlol_url

    if not asset_name.lower().endswith(".png"):
        asset_name = f"{asset_name}.png"
    return f"/assets/{quote(asset_name, safe='')}"


def _iter_augment_icon_urls(icon_filename: str):
    filename = normalize_augment_filename(icon_filename)
    templates = [
        "https://raw.communitydragon.org/latest/game/assets/ux/augments/{filename}",
        "https://raw.communitydragon.org/pbe/game/assets/ux/cherry/augments/icons/{filename}",
        "https://raw.communitydragon.org/pbe/game/assets/ux/augments/{filename}",
    ]
    for template in templates:
        yield template.format(filename=filename)


def _get_download_session() -> requests.Session:
    session = getattr(_THREAD_LOCAL, "download_session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        _THREAD_LOCAL.download_session = session
    return session


def _clear_augment_icon_failure(icon_filename: str) -> None:
    with _ICON_FAILURE_CACHE_LOCK:
        _ICON_FAILURE_CACHE.pop(normalize_augment_filename(icon_filename), None)


def _should_skip_failed_icon(icon_filename: str, force_refresh: bool) -> bool:
    if force_refresh:
        return False
    normalized = normalize_augment_filename(icon_filename)
    now = time.time()
    with _ICON_FAILURE_CACHE_LOCK:
        failed_at = _ICON_FAILURE_CACHE.get(normalized)
        if failed_at is None:
            return False
        if (now - failed_at) < _FAILURE_TTL_SECONDS:
            return True
        _ICON_FAILURE_CACHE.pop(normalized, None)
    return False


def _mark_augment_icon_failure(icon_filename: str) -> None:
    with _ICON_FAILURE_CACHE_LOCK:
        _ICON_FAILURE_CACHE[normalize_augment_filename(icon_filename)] = time.time()


def ensure_augment_icon_cached(icon_filename: str, asset_dir: Optional[str] = None, force_refresh: bool = False) -> Optional[str]:
    """确保指定海克斯图标已缓存在本地资源目录，必要时执行远端下载。"""
    asset_dir = _resolve_assets_dir(asset_dir)
    normalized_filename = normalize_augment_filename(icon_filename)
    if not normalized_filename:
        return None

    target_path = os.path.join(asset_dir, normalized_filename)
    if not force_refresh and os.path.exists(target_path) and os.path.getsize(target_path) > 0:
        _clear_augment_icon_failure(normalized_filename)
        return target_path
    if _should_skip_failed_icon(normalized_filename, force_refresh):
        return None

    os.makedirs(asset_dir, exist_ok=True)
    tmp_path = target_path + ".tmp"
    for url in _iter_augment_icon_urls(normalized_filename):
        try:
            response = _get_download_session().get(url, stream=True, timeout=15)
            if response.status_code != 200:
                continue
            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp_path, target_path)
            _clear_augment_icon_failure(normalized_filename)
            return target_path
        except Exception:
            pass
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    _mark_augment_icon_failure(normalized_filename)
    return None


def batch_prefetch_augment_icons(
    icon_filenames: Iterable[str],
    asset_dir: Optional[str] = None,
    force_refresh: bool = False,
    max_workers: int = 8,
    stop_event=None,
) -> dict:
    """并发预取一批海克斯图标，并返回成功/失败统计。"""
    asset_dir = _resolve_assets_dir(asset_dir)
    unique_filenames = []
    seen = set()
    for raw_name in icon_filenames:
        normalized = normalize_augment_filename(raw_name)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_filenames.append(normalized)

    result = {"total": len(unique_filenames), "success": 0, "failed": 0, "failed_files": []}
    if not unique_filenames:
        return result

    workers = max(1, min(max_workers, len(unique_filenames)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_filename = {
            executor.submit(ensure_augment_icon_cached, filename, asset_dir, force_refresh): filename
            for filename in unique_filenames
        }
        for future in as_completed(future_to_filename):
            if stop_event is not None and stop_event.is_set():
                for pending in future_to_filename:
                    pending.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
                break

            filename = future_to_filename[future]
            try:
                cached_path = future.result()
                if cached_path and os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
                    result["success"] += 1
                else:
                    result["failed"] += 1
                    result["failed_files"].append(filename)
            except Exception:
                result["failed"] += 1
                result["failed_files"].append(filename)
    return result


def _normalize_apexlol_hextech_slug(value: str) -> str:
    value = str(value).strip()
    return value.lstrip("/").split("?")[0].split("#")[0]


def load_apexlol_hextech_map(config_dir: Optional[str] = None, force_refresh: bool = False) -> dict:
    """加载或抓取 apexlol 海克斯 slug 映射，供远端图标兜底使用。"""
    global _APEXLOL_MAP_CACHE
    config_dir = _resolve_config_dir(config_dir)
    map_path = os.path.join(config_dir, "Augment_Apexlol_Map.json")

    with _APEXLOL_MAP_LOCK:
        cached_path, cached_mtime, cached_data = _APEXLOL_MAP_CACHE
        if not force_refresh and cached_path == map_path and cached_data:
            try:
                if os.path.getmtime(map_path) == cached_mtime:
                    return cached_data
            except OSError:
                return cached_data

    try:
        current_mtime = os.path.getmtime(map_path)
    except OSError:
        current_mtime = 0.0

    if not force_refresh and current_mtime and os.path.exists(map_path):
        try:
            with open(map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                with _APEXLOL_MAP_LOCK:
                    _APEXLOL_MAP_CACHE = (map_path, current_mtime, data)
                return data
        except Exception:
            pass

    try:
        response = requests.get(
            "https://apexlol.info/zh/hextech/",
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        response.raise_for_status()
        html = response.text
    except Exception:
        return _APEXLOL_MAP_CACHE[2]

    name_to_slug: Dict[str, str] = {}
    for match in re.finditer(r'href="/zh/hextech/([^"]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        slug = _normalize_apexlol_hextech_slug(match.group(1))
        inner_html = match.group(2)
        title = re.sub(r"<[^>]+>", " ", inner_html)
        title = unescape(title)
        title = re.sub(r"\s+", " ", title).strip()
        if not slug or not title:
            continue
        name_to_slug.setdefault(title, slug)
        name_to_slug.setdefault(normalize_augment_name(title), slug)

    if name_to_slug:
        try:
            os.makedirs(config_dir, exist_ok=True)
            tmp_path = map_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(name_to_slug, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, map_path)
            with _APEXLOL_MAP_LOCK:
                _APEXLOL_MAP_CACHE = (map_path, time.time(), name_to_slug)
        except Exception:
            pass
        return name_to_slug

    return _APEXLOL_MAP_CACHE[2]


def resolve_apexlol_hextech_icon_url(hextech_name: str, config_dir: Optional[str] = None) -> str:
    """把海克斯名称解析成 apexlol 图标 URL，作为本地图标缺失时的最终回退。"""
    slug_map = load_apexlol_hextech_map(config_dir=config_dir)
    candidates = [str(hextech_name).strip(), normalize_augment_name(hextech_name)]
    for candidate in candidates:
        slug = slug_map.get(candidate)
        if slug:
            return f"https://apexlol.info/images/hextech/{slug}.webp"
    return ""
