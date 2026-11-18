#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared panel log buffer and active batch log file."""

from __future__ import annotations

import sys
import threading
from collections import deque
from datetime import datetime
from pathlib import Path


class PanelLog:
    def __init__(self, max_lines: int = 2000):
        self._lines = deque(maxlen=max_lines)
        self._path: Path | None = None
        self._lock = threading.Lock()

    def set_path(self, path: Path | None) -> None:
        with self._lock:
            self._path = Path(path) if path else None

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()

    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def write(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        with self._lock:
            self._lines.append(line)
            path = self._path
        if path:
            try:
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError as exc:
                print(f"[Debug] 写入面板日志失败: {exc}", file=sys.stderr)
