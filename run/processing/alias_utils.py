"""Shared helpers for alias normalization and deduplication."""

from __future__ import annotations

import unicodedata
from typing import List


def normalize_alias_token(value) -> str:
    # 先做 NFKC 统一，再只保留字母数字和中文，避免同义别名被拆成多份。
    token = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    return "".join(ch for ch in token if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def unique_alias_tokens(*groups) -> List[str]:
    # 只返回归一化后的唯一 token，供索引和匹配使用。
    seen = set()
    result: List[str] = []
    for group in groups:
        if not group:
            continue
        if isinstance(group, (str, bytes)):
            values = [group]
        else:
            values = list(group)
        for value in values:
            token = normalize_alias_token(value)
            if token and token not in seen:
                seen.add(token)
                result.append(token)
    return result


def dedupe_alias_texts(*groups, excluded_tokens=None) -> List[str]:
    # 保留原始显示文本，但按归一化 token 去重，并排除官方名本身。
    seen = set()
    excluded = set()
    if excluded_tokens:
        for token in excluded_tokens:
            normalized = normalize_alias_token(token)
            if normalized:
                excluded.add(normalized)

    result: List[str] = []
    for group in groups:
        if not group:
            continue
        if isinstance(group, (str, bytes)):
            values = [group]
        else:
            values = list(group)
        for alias in values:
            alias_text = str(alias).strip()
            if not alias_text:
                continue
            token = normalize_alias_token(alias_text)
            if not token or token in seen or token in excluded:
                continue
            seen.add(token)
            result.append(alias_text)
    return result
