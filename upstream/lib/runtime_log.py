#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rotating diagnostic log configuration for best-effort runtime paths."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_runtime_logger(name: str, log_path: Path | str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger
