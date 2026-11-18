#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Account-file parsing and safe filesystem access for the panel."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple


class AccountStore:
    def __init__(
        self,
        base_dir: Path,
        log_callback: Optional[Callable[[str], None]] = None,
        accounts_dir: Optional[Path] = None,
    ):
        self.base_dir = Path(base_dir).resolve()
        self.accounts_dir = Path(
            accounts_dir or (self.base_dir / "data" / "accounts")
        ).resolve()
        self.accounts_dir.mkdir(parents=True, exist_ok=True)
        self.log_callback = log_callback

    def _log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def list_files(self) -> List[Path]:
        files = list(self.accounts_dir.glob("accounts_*.txt"))
        files.extend(self.base_dir.glob("accounts_*.txt"))
        files = list(dict.fromkeys(path.resolve() for path in files))
        try:
            return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError as exc:
            self._log(f"[Debug] 读取账号文件时间失败: {exc}")
            return sorted(files, key=lambda path: path.name, reverse=True)

    def read_lines(self, path: Path) -> List[str]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            self._log(f"[Debug] 读取账号文件 {path.name} 失败: {exc}")
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    def collect(self) -> List[Tuple[str, str]]:
        return [
            (path.name, line)
            for path in self.list_files()
            for line in self.read_lines(path)
        ]

    @staticmethod
    def parse_line(line: str) -> dict:
        parts = line.split("----")
        if len(parts) >= 3:
            return {
                "email": parts[0],
                "password": parts[1],
                "sso": "----".join(parts[2:]),
                "raw": line,
            }
        return {"email": line, "password": "", "sso": "", "raw": line}

    @staticmethod
    def decode_sso_meta(sso: str) -> dict:
        if not sso or sso.count(".") < 2:
            return {}
        segment = sso.split(".")[1]
        try:
            pad = "=" * (-len(segment) % 4)
            raw = base64.urlsafe_b64decode(segment + pad)
            data = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def unique(self) -> List[dict]:
        seen = set()
        accounts = []
        for source, line in self.collect():
            if line in seen:
                continue
            seen.add(line)
            info = self.parse_line(line)
            info["source"] = source
            meta = self.decode_sso_meta(info.get("sso") or "")
            info["session_id"] = meta.get("session_id") or meta.get("sid") or ""
            accounts.append(info)
        return accounts

    def line_set(self) -> set[str]:
        return {line for _, line in self.collect()}

    def resolve_file(self, name: str) -> Optional[Path]:
        if not name or "/" in name or "\\" in name or ".." in name:
            return None
        if not re.fullmatch(r"accounts_[\w.-]+\.txt", name):
            return None
        for directory in (self.accounts_dir, self.base_dir):
            path = (directory / name).resolve()
            if path.parent == directory and path.exists():
                return path
        return None
