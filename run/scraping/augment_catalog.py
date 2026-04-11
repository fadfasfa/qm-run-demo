"""海克斯统一目录、图像预缓存与审计任务。"""

from __future__ import annotations

import ast
import json
import logging
import operator
import os
import re
import time
from html import unescape
from typing import Dict, Iterable, Optional

from scraping.full_hextech_scraper import _clean_augment_text, _extract_augment_meta
from scraping.version_sync import ASSET_DIR, CONFIG_DIR, get_advanced_session
from scraping.icon_resolver import (
    batch_prefetch_augment_icons,
    ensure_augment_icon_cached,
    find_existing_augment_asset_filename,
    load_augment_icon_map,
    normalize_augment_name,
    resolve_apexlol_hextech_icon_url,
)

logger = logging.getLogger(__name__)

AUGMENT_ICON_SOURCE_FILE = os.path.join(CONFIG_DIR, "augment_icon_source.txt")
AUGMENT_ICON_AUDIT_FILE = os.path.join(CONFIG_DIR, "augment_icon_audit.jsonl")
AUGMENT_ICON_MANIFEST_FILE = os.path.join(CONFIG_DIR, "Augment_Icon_Manifest.json")
AUGMENT_ICON_SOURCE_ID = "apexlol"
AUGMENT_METADATA_URL = "https://hextech.dtodo.cn/data/aram-mayhem-augments.zh_cn.json"
MANIFEST_SCHEMA_VERSION = 2
_AUGMENT_ICON_MANIFEST_CACHE: tuple[str, float, list[dict]] = ("", 0.0, [])
_AUGMENT_LOOKUP_CACHE: tuple[str, float, dict] = ("", 0.0, {})
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_PLACEHOLDER_PATTERN = re.compile(r"@([^@]+)@")
_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def _append_augment_icon_audit(record: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    payload = dict(record)
    payload.setdefault("ts", _now_iso())
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with open(AUGMENT_ICON_AUDIT_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_augment_icon_source_marker() -> str:
    try:
        with open(AUGMENT_ICON_SOURCE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (OSError, IOError):
        return ""


def write_augment_icon_source_marker(source_id: str) -> None:
    tmp_path = AUGMENT_ICON_SOURCE_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(source_id)
    os.replace(tmp_path, AUGMENT_ICON_SOURCE_FILE)


def _strip_html_text(raw_text: object) -> str:
    text = _clean_augment_text(raw_text)
    if not text:
        return ""
    text = unescape(text)
    text = _HTML_TAG_PATTERN.sub("", text)
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _eval_safe_expr(expr: str) -> Optional[float]:
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError:
        return None

    def _calc(current):
        if isinstance(current, ast.BinOp):
            left = _calc(current.left)
            right = _calc(current.right)
            op = _SAFE_OPS.get(type(current.op))
            if op is None:
                raise ValueError("unsupported operator")
            return op(left, right)
        if isinstance(current, ast.UnaryOp):
            operand = _calc(current.operand)
            op = _SAFE_OPS.get(type(current.op))
            if op is None:
                raise ValueError("unsupported unary operator")
            return op(operand)
        if isinstance(current, ast.Constant) and isinstance(current.value, (int, float)):
            return float(current.value)
        raise ValueError("unsupported node")

    try:
        return float(_calc(node))
    except Exception:
        return None


def _resolve_placeholder_token(token: str, value_map: Dict[str, float]) -> str:
    content = token.strip()
    if not content:
        return "?"
    if content in value_map:
        return _format_number(value_map[content])

    expr = content
    for name in sorted(value_map.keys(), key=len, reverse=True):
        expr = re.sub(rf"\b{re.escape(name)}\b", str(value_map[name]), expr)
    if not re.fullmatch(r"[0-9eE\.\+\-\*/\(\)\s]+", expr):
        return "?"

    value = _eval_safe_expr(expr)
    if value is None:
        return "?"
    return _format_number(value)


def _render_tooltip_plain(raw_tooltip: object, spell_values: Dict[str, float]) -> str:
    base_text = _strip_html_text(raw_tooltip)
    if not base_text:
        return ""

    def repl(match):
        return _resolve_placeholder_token(match.group(1), spell_values)

    return _PLACEHOLDER_PATTERN.sub(repl, base_text)


def _load_full_map(config_dir: str) -> dict:
    full_map_path = os.path.join(config_dir, "Augment_Full_Map.json")
    try:
        with open(full_map_path, "r", encoding="utf-8") as f:
            raw_full_map = json.load(f)
        if isinstance(raw_full_map, dict):
            return raw_full_map
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {}


def _fetch_remote_augment_metadata() -> dict:
    metadata = {}
    session = get_advanced_session()
    try:
        response = session.get(AUGMENT_METADATA_URL, timeout=12, verify=True)
        response.raise_for_status()
        payload = response.json()
    except Exception as e:
        logger.warning("海克斯远端元数据拉取失败：%s", e)
        return metadata

    if not isinstance(payload, dict):
        return metadata

    for raw_item in payload.values():
        item = raw_item if isinstance(raw_item, dict) else {}
        name = _clean_augment_text(item.get("displayName"))
        if not name:
            continue
        meta = _extract_augment_meta(item)
        spell_values = meta.get("spell_values", {})
        normalized = {
            "description": _clean_augment_text(meta.get("description")),
            "tooltip": _clean_augment_text(meta.get("tooltip")),
            "spell_values": spell_values if isinstance(spell_values, dict) else {},
        }
        normalized["tooltip_plain"] = _render_tooltip_plain(
            normalized["tooltip"] or normalized["description"],
            normalized["spell_values"],
        )
        metadata[name] = normalized
        metadata[normalize_augment_name(name)] = normalized
    return metadata


def _normalize_manifest_entry(item: dict, config_dir: str) -> dict:
    name = _clean_augment_text(item.get("name"))
    filename = os.path.basename(str(item.get("filename", "")).strip()).lower()
    local_path = item.get("local_path") or (os.path.join(ASSET_DIR, filename) if filename else "")
    icon_url = _clean_augment_text(item.get("icon_url"))
    if not icon_url and filename:
        icon_url = f"/assets/{filename}"
    if not icon_url and name:
        icon_url = resolve_apexlol_hextech_icon_url(name, config_dir=config_dir)

    raw_values = item.get("spell_values", {})
    if not isinstance(raw_values, dict):
        raw_values = {}
    spell_values = {}
    for key, value in raw_values.items():
        try:
            spell_values[str(key)] = float(value)
        except (TypeError, ValueError):
            continue

    description = _clean_augment_text(item.get("description"))
    tooltip = _clean_augment_text(item.get("tooltip"))
    tooltip_plain = _clean_augment_text(item.get("tooltip_plain"))
    if not tooltip_plain:
        tooltip_plain = _render_tooltip_plain(tooltip or description, spell_values)

    status = _clean_augment_text(item.get("status"))
    if not status:
        status = "ready" if (tooltip or description) else "minimal"

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "name": name,
        "tier": _clean_augment_text(item.get("tier")),
        "filename": filename,
        "local_path": local_path,
        "icon_url": icon_url,
        "description": description,
        "tooltip": tooltip,
        "tooltip_plain": tooltip_plain,
        "spell_values": spell_values,
        "status": status,
        "updated_at": _clean_augment_text(item.get("updated_at")) or _now_iso(),
    }


def _read_manifest_file(manifest_path: str, config_dir: str) -> list[dict]:
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [
                _normalize_manifest_entry(item, config_dir)
                for item in data
                if isinstance(item, dict) and _clean_augment_text(item.get("name"))
            ]
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return []


def _write_augment_icon_manifest(manifest: list[dict]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp_path = AUGMENT_ICON_MANIFEST_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, AUGMENT_ICON_MANIFEST_FILE)
    global _AUGMENT_ICON_MANIFEST_CACHE, _AUGMENT_LOOKUP_CACHE
    now = os.path.getmtime(AUGMENT_ICON_MANIFEST_FILE)
    _AUGMENT_ICON_MANIFEST_CACHE = (AUGMENT_ICON_MANIFEST_FILE, now, manifest)
    _AUGMENT_LOOKUP_CACHE = ("", 0.0, {})


def _manifest_needs_rebuild(manifest: list[dict]) -> bool:
    if not manifest:
        return True
    sample = manifest[0]
    required = {"schema_version", "icon_url", "description", "tooltip_plain", "spell_values", "status", "updated_at"}
    return any(field not in sample for field in required)


def _manifest_is_stale(manifest_path: str) -> bool:
    try:
        manifest_mtime = os.path.getmtime(manifest_path)
    except OSError:
        return True

    source_paths = [
        os.path.join(CONFIG_DIR, "Augment_Icon_Map.json"),
        os.path.join(CONFIG_DIR, "Augment_Full_Map.json"),
    ]
    for source_path in source_paths:
        try:
            if os.path.getmtime(source_path) > manifest_mtime:
                return True
        except OSError:
            continue

    manifest = _read_manifest_file(manifest_path, CONFIG_DIR)
    return _manifest_needs_rebuild(manifest)


def build_augment_icon_manifest(
    config_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> list[dict]:
    config_dir = config_dir or CONFIG_DIR
    icon_map = load_augment_icon_map(config_dir, force_refresh=force_refresh)
    full_map = _load_full_map(config_dir)
    existing_manifest = _read_manifest_file(os.path.join(config_dir, "Augment_Icon_Manifest.json"), config_dir)
    existing_by_name = {item["name"]: item for item in existing_manifest if item.get("name")}
    remote_metadata = _fetch_remote_augment_metadata() if force_refresh else {}

    all_names = sorted(set(icon_map) | set(full_map) | set(existing_by_name))
    manifest = []
    for name in all_names:
        existing = existing_by_name.get(name, {})
        raw_filename = icon_map.get(name) or existing.get("filename", "")
        filename = os.path.basename(str(raw_filename).strip()).lower()
        remote_meta = remote_metadata.get(name) or remote_metadata.get(normalize_augment_name(name)) or {}

        entry = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "name": name,
            "tier": _clean_augment_text(full_map.get(name) or existing.get("tier")),
            "filename": filename,
            "local_path": os.path.join(ASSET_DIR, filename) if filename else "",
            "icon_url": f"/assets/{filename}" if filename else resolve_apexlol_hextech_icon_url(name, config_dir=config_dir),
            "description": _clean_augment_text(remote_meta.get("description") or existing.get("description")),
            "tooltip": _clean_augment_text(remote_meta.get("tooltip") or existing.get("tooltip")),
            "tooltip_plain": _clean_augment_text(remote_meta.get("tooltip_plain") or existing.get("tooltip_plain")),
            "spell_values": remote_meta.get("spell_values") or existing.get("spell_values") or {},
            "status": "ready" if remote_meta else existing.get("status", "minimal"),
            "updated_at": _now_iso(),
        }
        manifest.append(_normalize_manifest_entry(entry, config_dir))

    _write_augment_icon_manifest(manifest)
    return manifest


def load_augment_icon_manifest(
    config_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> list[dict]:
    config_dir = config_dir or CONFIG_DIR
    manifest_path = os.path.join(config_dir, "Augment_Icon_Manifest.json")

    global _AUGMENT_ICON_MANIFEST_CACHE
    cached_path, cached_mtime, cached_data = _AUGMENT_ICON_MANIFEST_CACHE
    if not force_refresh and cached_path == manifest_path and cached_data:
        try:
            if os.path.getmtime(manifest_path) == cached_mtime and not _manifest_is_stale(manifest_path):
                return cached_data
        except OSError:
            return cached_data

    manifest = _read_manifest_file(manifest_path, config_dir)
    if manifest and not force_refresh and not _manifest_is_stale(manifest_path):
        _AUGMENT_ICON_MANIFEST_CACHE = (manifest_path, os.path.getmtime(manifest_path), manifest)
        return manifest

    return build_augment_icon_manifest(config_dir=config_dir, force_refresh=force_refresh)


def build_augment_catalog_lookup(
    config_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    config_dir = config_dir or CONFIG_DIR
    manifest_path = os.path.join(config_dir, "Augment_Icon_Manifest.json")
    manifest = load_augment_icon_manifest(config_dir=config_dir, force_refresh=force_refresh)
    try:
        mtime = os.path.getmtime(manifest_path)
    except OSError:
        mtime = 0.0

    global _AUGMENT_LOOKUP_CACHE
    cached_path, cached_mtime, cached_lookup = _AUGMENT_LOOKUP_CACHE
    if not force_refresh and cached_path == manifest_path and cached_lookup and cached_mtime == mtime:
        return cached_lookup

    lookup = {}
    for item in manifest:
        name = item.get("name", "")
        filename = str(item.get("filename", "")).strip().lower()
        if not name:
            continue
        lookup[name] = item
        lookup[normalize_augment_name(name)] = item
        if filename:
            lookup[filename] = item
            lookup[os.path.splitext(filename)[0]] = item
    _AUGMENT_LOOKUP_CACHE = (manifest_path, mtime, lookup)
    return lookup


def find_augment_catalog_entry(
    lookup_name: str,
    config_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> Optional[dict]:
    if not lookup_name:
        return None
    lookup = build_augment_catalog_lookup(config_dir=config_dir, force_refresh=force_refresh)
    return lookup.get(str(lookup_name).strip()) or lookup.get(normalize_augment_name(lookup_name))


def list_augment_icon_filenames(config_dir: Optional[str] = None, force_refresh: bool = False) -> list[str]:
    manifest = load_augment_icon_manifest(config_dir=config_dir, force_refresh=force_refresh)
    return [item["filename"] for item in manifest if item.get("filename")]


def list_missing_augment_icons(
    config_dir: Optional[str] = None,
    asset_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> list[str]:
    asset_dir = asset_dir or ASSET_DIR
    filenames = list_augment_icon_filenames(config_dir=config_dir, force_refresh=force_refresh)
    if not filenames:
        return []

    missing = []
    for filename in filenames:
        target_path = os.path.join(asset_dir, filename)
        if not os.path.exists(target_path) or os.path.getsize(target_path) <= 0:
            missing.append(filename)
    return missing


def is_augment_icon_prefetch_ready(
    config_dir: Optional[str] = None,
    asset_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> bool:
    missing = list_missing_augment_icons(config_dir=config_dir, asset_dir=asset_dir, force_refresh=force_refresh)
    return bool(list_augment_icon_filenames(config_dir=config_dir, force_refresh=force_refresh)) and not missing


def manifest_has_incomplete_entries(
    config_dir: Optional[str] = None,
    force_refresh: bool = False,
) -> bool:
    manifest = load_augment_icon_manifest(config_dir=config_dir, force_refresh=force_refresh)
    if not manifest:
        return True
    for item in manifest:
        if str(item.get("status", "")).strip() != "ready":
            return True
    return False


def audit_and_repair_augment_icons(
    force_map_refresh: bool = False,
    config_dir: Optional[str] = None,
    asset_dir: Optional[str] = None,
) -> dict:
    start_time = time.time()
    config_dir = config_dir or CONFIG_DIR
    asset_dir = asset_dir or ASSET_DIR
    manifest = []
    repaired_items = []
    failed_items = []

    try:
        manifest = build_augment_icon_manifest(config_dir=config_dir, force_refresh=force_map_refresh)
    except Exception as e:
        record = {
            "kind": "augment_icon_audit",
            "status": "error",
            "force_map_refresh": force_map_refresh,
            "error": str(e),
            "duration_ms": round((time.time() - start_time) * 1000, 2),
        }
        _append_augment_icon_audit(record)
        logger.error("海克斯图标自检失败：%s", e)
        return record

    missing_before = []
    checked = 0
    for item in manifest:
        checked += 1
        raw_name = str(item.get("name", "")).strip()
        filename = str(item.get("filename", "")).strip()
        if not filename:
            failed_items.append({
                "name": raw_name,
                "reason": "无法解析图标文件名",
            })
            continue

        asset_path = os.path.join(asset_dir, filename)
        if os.path.exists(asset_path) and os.path.getsize(asset_path) > 0:
            continue

        missing_before.append({
            "name": raw_name,
            "filename": filename,
        })
        try:
            cached_path = ensure_augment_icon_cached(filename, asset_dir=asset_dir)
            if cached_path and os.path.exists(cached_path) and os.path.getsize(cached_path) > 0:
                repaired_items.append({
                    "name": raw_name,
                    "filename": filename,
                    "path": os.path.basename(cached_path),
                })
            else:
                failed_items.append({
                    "name": raw_name,
                    "filename": filename,
                    "reason": "远程抓取未返回可用文件",
                })
        except Exception as e:
            failed_items.append({
                "name": raw_name,
                "filename": filename,
                "reason": str(e),
            })

    record = {
        "kind": "augment_icon_audit",
        "status": "ok" if not failed_items else "partial_failure",
        "force_map_refresh": force_map_refresh,
        "catalog_entries": len(manifest),
        "checked": checked,
        "missing_before_count": len(missing_before),
        "missing_before_sample": missing_before[:20],
        "repaired_count": len(repaired_items),
        "repaired_sample": repaired_items[:20],
        "failed_count": len(failed_items),
        "failed_items": failed_items,
        "duration_ms": round((time.time() - start_time) * 1000, 2),
    }
    _append_augment_icon_audit(record)

    if failed_items:
        logger.error(
            "海克斯图标自检存在失败：缺失=%s，修复=%s，失败=%s",
            len(missing_before),
            len(repaired_items),
            len(failed_items),
        )
    return record


def run_augment_icon_prefetch(
    force_refresh: bool = False,
    stop_event=None,
    config_dir: Optional[str] = None,
    asset_dir: Optional[str] = None,
    max_workers: int = 8,
) -> dict:
    config_dir = config_dir or CONFIG_DIR
    asset_dir = asset_dir or ASSET_DIR
    start_time = time.time()
    manifest = load_augment_icon_manifest(config_dir=config_dir, force_refresh=force_refresh)
    missing = [
        item["filename"]
        for item in manifest
        if item.get("filename")
        and (not os.path.exists(os.path.join(asset_dir, item["filename"])) or os.path.getsize(os.path.join(asset_dir, item["filename"])) <= 0)
    ]
    if not missing:
        return {
            "kind": "augment_icon_prefetch",
            "mode": "startup_prefetch" if force_refresh else "runtime_on_demand_repair",
            "total": 0,
            "success": 0,
            "failed": 0,
            "failed_files": [],
            "ready": True,
            "duration_ms": round((time.time() - start_time) * 1000, 2),
        }

    result = batch_prefetch_augment_icons(
        missing,
        asset_dir=asset_dir,
        force_refresh=force_refresh,
        max_workers=max_workers,
        stop_event=stop_event,
    )
    ready = not list_missing_augment_icons(config_dir=config_dir, asset_dir=asset_dir)
    result.update({
        "kind": "augment_icon_prefetch",
        "mode": "startup_prefetch" if force_refresh else "runtime_on_demand_repair",
        "ready": ready,
        "duration_ms": round((time.time() - start_time) * 1000, 2),
    })
    return result
