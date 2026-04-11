"""首页搜索专用的英雄别名索引读取与归一。"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from processing.alias_utils import dedupe_alias_texts, normalize_alias_token
from scraping.version_sync import CONFIG_DIR


CHAMPION_ALIAS_INDEX_FILE = os.path.join(CONFIG_DIR, "Champion_Alias_Index.json")

_ALIAS_INDEX_CACHE: tuple[str, float, list[dict]] = ("", 0.0, [])


def _normalize_record(record: dict) -> dict:
    hero_name = str(record.get("heroName") or record.get("title") or "").strip()
    title = str(record.get("title") or "").strip()
    en_name = str(record.get("enName") or record.get("en_name") or "").strip()
    hero_id = str(record.get("heroId") or record.get("id") or "").strip()
    aliases = dedupe_alias_texts(
        record.get("aliases", []),
        excluded_tokens=[hero_name, title, en_name, hero_id],
    )
    return {
        "heroName": hero_name,
        "title": title,
        "enName": en_name,
        "heroId": hero_id,
        "aliases": aliases,
    }


def load_champion_alias_index(force_refresh: bool = False) -> list[dict]:
    """读取首页搜索专用的英雄别名索引，文件缺失时返回空列表。"""
    global _ALIAS_INDEX_CACHE

    if not os.path.exists(CHAMPION_ALIAS_INDEX_FILE):
        return []

    try:
        current_mtime = os.path.getmtime(CHAMPION_ALIAS_INDEX_FILE)
        if (
            not force_refresh
            and _ALIAS_INDEX_CACHE[0] == CHAMPION_ALIAS_INDEX_FILE
            and _ALIAS_INDEX_CACHE[1] == current_mtime
            and _ALIAS_INDEX_CACHE[2]
        ):
            return _ALIAS_INDEX_CACHE[2]

        with open(CHAMPION_ALIAS_INDEX_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, list):
            payload = []
        records = [_normalize_record(item) for item in payload if isinstance(item, dict)]
        _ALIAS_INDEX_CACHE = (CHAMPION_ALIAS_INDEX_FILE, current_mtime, records)
        return records
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return []


def load_manual_alias_index(force_refresh: bool = False) -> list[dict]:
    return load_champion_alias_index(force_refresh=force_refresh)


def load_champion_alias_map(force_refresh: bool = False) -> Dict[str, List[str]]:
    """返回 `{英雄名: [别名...]}` 的映射，供首页搜索构建索引。"""
    records = load_champion_alias_index(force_refresh=force_refresh)
    return {
        str(record.get("heroName", "")).strip(): list(record.get("aliases", []))
        for record in records
        if str(record.get("heroName", "")).strip()
    }


def resolve_champion_record(query: str, force_refresh: bool = False) -> Optional[dict]:
    """按英雄名、称号、英文名、ID 或别名解析到索引记录。"""
    normalized_query = normalize_alias_token(query)
    if not normalized_query:
        return None

    records = load_champion_alias_index(force_refresh=force_refresh)
    exact_map: dict[str, dict] = {}
    fuzzy_candidates: list[tuple[int, dict]] = []

    for record in records:
        hero_name = str(record.get("heroName", "")).strip()
        title = str(record.get("title", "")).strip()
        en_name = str(record.get("enName", "")).strip()
        hero_id = str(record.get("heroId", "")).strip()
        aliases = list(record.get("aliases", []))

        tokens = dedupe_alias_texts([hero_name, title, en_name, hero_id], aliases)
        for token in tokens:
            normalized_token = normalize_alias_token(token)
            if not normalized_token:
                continue
            exact_map.setdefault(normalized_token, record)
            if normalized_query in normalized_token or normalized_token in normalized_query:
                fuzzy_candidates.append((len(normalized_token), record))

    if normalized_query in exact_map:
        return exact_map[normalized_query]

    if fuzzy_candidates:
        fuzzy_candidates.sort(key=lambda item: item[0])
        return fuzzy_candidates[0][1]

    return None


def resolve_champion_name(query: str, force_refresh: bool = False) -> Optional[str]:
    record = resolve_champion_record(query, force_refresh=force_refresh)
    if not record:
        return None
    return str(record.get("heroName", "")).strip() or None
