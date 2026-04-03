"""Shared helpers for augment icon lookup and caching."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from html import unescape
from typing import Dict, Optional, Tuple
from urllib.parse import quote

import requests


_ICON_MAP_CACHE: Tuple[str, float, dict] = ("", 0.0, {})
_APEXLOL_MAP_CACHE: Tuple[str, float, dict] = ("", 0.0, {})
_ICON_MAP_LOCK = threading.Lock()
_APEXLOL_MAP_LOCK = threading.Lock()


def _default_runtime_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_config_dir(config_dir: Optional[str]) -> str:
    return config_dir or os.path.join(_default_runtime_dir(), "config")


def _resolve_assets_dir(asset_dir: Optional[str]) -> str:
    return asset_dir or os.path.join(_default_runtime_dir(), "assets")


def normalize_augment_name(name: str) -> str:
    # 图标名只做轻量归一化，保留语义，方便本地缓存和远程映射同时命中。
    name = str(name).lower()
    for token in (" ", "-", "_", "(", ")", "[", "]", "'", '"', "."):
        name = name.replace(token, "")
    return name


def normalize_augment_filename(value: str) -> str:
    return os.path.basename(str(value).strip()).lower()


def find_existing_augment_asset_filename(asset_dir: Optional[str], candidate_filename: str) -> Optional[str]:
    """在本地资源目录中解析出实际存在的增强器图标文件名。"""
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
            if (
                best_match is None
                and (
                    entry_stem_norm.startswith(normalized_candidate_stem)
                    or normalized_candidate_stem.startswith(entry_stem_norm)
                )
            ):
                best_match = entry_name
    except OSError:
        return None

    return best_match


def load_augment_icon_map(config_dir: Optional[str] = None, force_refresh: bool = False) -> dict:
    global _ICON_MAP_CACHE
    config_dir = _resolve_config_dir(config_dir)
    icon_map_path = os.path.join(config_dir, "Augment_Icon_Map.json")

    # 优先复用本地缓存，只有文件变更或强制刷新时才重新读盘。
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


def find_augment_icon_filename(
    icon_map: dict,
    lookup_name: str,
    asset_dir: Optional[str] = None,
) -> Optional[str]:
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
    # 本地页面只暴露 /assets 路径，避免前端直接拼接远程资源。
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


def ensure_augment_icon_cached(
    icon_filename: str,
    asset_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> Optional[str]:
    asset_dir = _resolve_assets_dir(asset_dir)
    normalized_filename = normalize_augment_filename(icon_filename)
    if not normalized_filename:
        return None

    target_path = os.path.join(asset_dir, normalized_filename)
    # 已存在且非空时直接复用缓存，避免重复下载。
    if not force_refresh and os.path.exists(target_path) and os.path.getsize(target_path) > 0:
        return target_path

    os.makedirs(asset_dir, exist_ok=True)
    tmp_path = target_path + ".tmp"
    for url in _iter_augment_icon_urls(normalized_filename):
        try:
            # 依次尝试多个 CommunityDragon 变体地址，哪个可用就落哪个。
            response = requests.get(url, stream=True, timeout=15)
            if response.status_code != 200:
                continue

            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            os.replace(tmp_path, target_path)
            return target_path
        except Exception:
            pass
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    return None


def _normalize_apexlol_hextech_slug(value: str) -> str:
    value = str(value).strip()
    return value.lstrip("/").split("?")[0].split("#")[0]


def load_apexlol_hextech_map(config_dir: Optional[str] = None, force_refresh: bool = False) -> dict:
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
            # 本地映射文件命中时优先返回，只有缺失或损坏才回退到网页抓取。
            with open(map_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                with _APEXLOL_MAP_LOCK:
                    _APEXLOL_MAP_CACHE = (map_path, current_mtime, data)
                return data
        except Exception:
            pass

    try:
        # Apexlol 页面是远程兜底数据源，失败时直接返回最后一次可用缓存。
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
        # 解析页面文本时去掉 HTML 标签和多余空白，得到可稳定匹配的中文名。
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
                globals()["_APEXLOL_MAP_CACHE"] = (map_path, time.time(), name_to_slug)
        except Exception:
            pass
        return name_to_slug

    return _APEXLOL_MAP_CACHE[2]


def resolve_apexlol_hextech_icon_url(hextech_name: str, config_dir: Optional[str] = None) -> str:
    # 同时尝试原始名和归一化名，兼容页面文本与内部 key 的差异。
    slug_map = load_apexlol_hextech_map(config_dir=config_dir)
    candidates = [str(hextech_name).strip(), normalize_augment_name(hextech_name)]
    for candidate in candidates:
        slug = slug_map.get(candidate)
        if slug:
            return f"https://apexlol.info/images/hextech/{slug}.webp"
    return ""
