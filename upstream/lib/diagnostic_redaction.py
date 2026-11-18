#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve CPA diagnostic inputs and redact credential-bearing output."""

from __future__ import annotations

import json
import re
from pathlib import Path


SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "id_token",
    "sso",
    "authorization",
    "cookie",
    "password",
}


def resolve_cpa_file(root: Path, requested: str = "") -> Path:
    if requested:
        path = Path(requested).expanduser()
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"CPA 文件不存在: {path}")
        return path
    candidates = sorted(
        (root / "data" / "cpa").glob("xai-*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("data/cpa 中没有 xai-*.json；请使用 --cpa-file 指定")
    return candidates[0]


def redact_value(value):
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key).lower() in SENSITIVE_KEYS else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    return value


def sanitize_text(text: str, limit: int = 500) -> str:
    clean = str(text or "")
    clean = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", clean)
    clean = re.sub(r"(?i)(sso(?:-rw)?=)[^;\s]+", r"\1<redacted>", clean)
    clean = re.sub(
        r'(?i)("(?:access_token|refresh_token|id_token|sso|password)"\s*:\s*")[^"]+("?)',
        r"\1<redacted>\2",
        clean,
    )
    return clean[:limit]


def load_cpa_entry(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("CPA JSON 根节点必须是对象")
    return data
