#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared JSON configuration persistence with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Mapping, Optional


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or persisted."""


class JsonConfigStore:
    """Own JSON parsing and atomic persistence behind a small interface."""

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self, defaults: Optional[Mapping] = None) -> dict:
        base = dict(defaults or {})
        if not self.path.exists():
            return base
        try:
            raw = self.path.read_text(encoding="utf-8-sig")
            loaded = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"无法读取配置 {self.path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ConfigError(f"配置根节点必须是 JSON 对象: {self.path}")
        base.update(loaded)
        return base

    def save(self, data: Mapping) -> None:
        if not isinstance(data, Mapping):
            raise ConfigError("配置内容必须是映射对象")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(dict(data), ensure_ascii=False, indent=2) + "\n"
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temp_path = Path(handle.name)
            os.replace(temp_path, self.path)
        except OSError as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise ConfigError(f"无法保存配置 {self.path}: {exc}") from exc
