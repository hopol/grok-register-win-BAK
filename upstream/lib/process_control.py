#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scoped child-process lifecycle helpers."""

from __future__ import annotations

import subprocess
from typing import Callable, Optional

import psutil


LogCallback = Optional[Callable[[str], None]]


def _log(callback: LogCallback, message: str) -> None:
    if callback:
        callback(message)


def terminate_process_tree(
    process: Optional[subprocess.Popen],
    *,
    timeout: float = 5.0,
    log_callback: LogCallback = None,
) -> None:
    """Terminate only the supplied process and descendants it created."""
    if process is None or process.poll() is not None:
        return

    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
    except (psutil.Error, OSError) as exc:
        _log(log_callback, f"[Debug] 无法枚举注册进程树 pid={process.pid}: {exc}")
        children = []
        parent = None

    targets = [*reversed(children)]
    if parent is not None:
        targets.append(parent)

    for target in targets:
        try:
            target.terminate()
        except (psutil.Error, OSError) as exc:
            _log(log_callback, f"[Debug] 终止子进程 pid={target.pid} 失败: {exc}")

    if targets:
        _, alive = psutil.wait_procs(targets, timeout=timeout)
        for target in alive:
            try:
                target.kill()
            except (psutil.Error, OSError) as exc:
                _log(log_callback, f"[Debug] 强制结束子进程 pid={target.pid} 失败: {exc}")

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log(log_callback, f"[Debug] 等待注册主进程 pid={process.pid} 退出失败: {exc}")
