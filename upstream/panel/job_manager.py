#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registration child-process orchestration and batch state transitions."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


class RegistrationJobManager:
    def __init__(
        self,
        *,
        base_dir: Path,
        log_dir: Path,
        python: str,
        main_script: Path,
        panel_log,
        account_store,
        cpa_service,
        config_loader: Callable[[], dict],
        config_saver: Callable[[dict], None],
        proxy_resolver: Callable[[], str],
        proxy_setter: Callable[[str], None],
        exit_ip_probe: Callable[[], str],
        process_terminator: Callable,
        cpa_wait_timeout: float,
        popen_factory=subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.base_dir = Path(base_dir)
        self.log_dir = Path(log_dir)
        self.python = python
        self.main_script = Path(main_script)
        self.panel_log = panel_log
        self.account_store = account_store
        self.cpa_service = cpa_service
        self.config_loader = config_loader
        self.config_saver = config_saver
        self.proxy_resolver = proxy_resolver
        self.proxy_setter = proxy_setter
        self.exit_ip_probe = exit_ip_probe
        self.process_terminator = process_terminator
        self.cpa_wait_timeout = cpa_wait_timeout
        self.popen_factory = popen_factory
        self.sleep = sleep
        self.lock = threading.Lock()
        self.process: Optional[subprocess.Popen] = None
        self.current_log_path: Optional[Path] = None
        self.state = self._idle_state()

    @staticmethod
    def _idle_state() -> dict:
        return {
            "running": False,
            "stop": False,
            "pid": None,
            "started_at": None,
            "finished_at": None,
            "count": 0,
            "success": 0,
            "fail": 0,
            "current_round": 0,
            "current_node": "",
            "node_mode": "external",
            "node_list": [],
            "node_index": 0,
            "log_path": "",
            "last_error": "",
            "status": "idle",
            "cpa_queued": 0,
            "cpa_ok": 0,
            "cpa_fail": 0,
        }

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def start(self, count: int) -> tuple[bool, str]:
        with self.lock:
            if self.state.get("running"):
                return False, "已有任务在运行"
        if count < 1 or count > 500:
            return False, "轮数范围 1-500"

        log_path = self.log_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        self.current_log_path = log_path
        self.panel_log.set_path(log_path)
        self.panel_log.clear()
        self.panel_log.write(f"任务创建：轮数={count}（节点由本机 Clash 管理）")
        with self.lock:
            self.state["running"] = True
            self.state["status"] = "starting"
        thread = threading.Thread(target=self.run_batch, args=(count,), daemon=True)
        try:
            thread.start()
        except RuntimeError as exc:
            with self.lock:
                self.state["running"] = False
                self.state["status"] = "idle"
                self.state["last_error"] = str(exc)
            return False, f"启动任务线程失败: {exc}"
        return True, "已启动"

    def stop(self) -> tuple[bool, str]:
        with self.lock:
            if not self.state.get("running"):
                return False, "当前没有运行中的任务"
            self.state["stop"] = True
            process = self.process
        self.panel_log.write("[!] 正在停止…")
        self.process_terminator(process, log_callback=self.panel_log.write)
        return True, "已发送停止"

    def run_batch(self, count: int) -> None:
        try:
            with self.lock:
                self.state.update(
                    running=True,
                    stop=False,
                    status="running",
                    count=count,
                    success=0,
                    fail=0,
                    current_round=0,
                    current_node="external-clash",
                    started_at=datetime.now().isoformat(timespec="seconds"),
                    finished_at=None,
                    last_error="",
                    cpa_queued=0,
                    cpa_ok=0,
                    cpa_fail=0,
                )
                self.state["log_path"] = str(self.current_log_path or "")

            proxy = self.proxy_resolver()
            self.proxy_setter(proxy)
            try:
                config = self.config_loader()
                config["proxy"] = proxy
                self.config_saver(config)
            except Exception as exc:
                self.panel_log.write(f"[Debug] 保存自动探测代理失败: {exc}")
            self.panel_log.write(f"[*] 使用外部 Clash 代理: {proxy}（节点请在 Clash 客户端选择）")
            self.panel_log.write(f"[*] 出口探测: {self.exit_ip_probe()}")

            batch_before = self.account_store.line_set()
            for round_number in range(1, count + 1):
                with self.lock:
                    if self.state.get("stop"):
                        self.panel_log.write("[!] 用户停止，结束任务")
                        break
                    self.state["current_round"] = round_number
                success = self._run_round(round_number, count, proxy)
                with self.lock:
                    key = "success" if success else "fail"
                    self.state[key] = int(self.state.get(key) or 0) + 1
                    current = self.state[key]
                if success:
                    self.panel_log.write(f"[+] 第 {round_number} 轮成功（累计成功 {current}）")
                else:
                    self.panel_log.write(f"[-] 第 {round_number} 轮失败（累计失败 {current}），继续下一轮")
                self.sleep(1)

            with self.lock:
                successful = int(self.state.get("success") or 0)
                if successful:
                    self.state["status"] = "converting_cpa"
            if successful:
                result = self.cpa_service.finalize_batch(
                    batch_before,
                    successful,
                    timeout=self.cpa_wait_timeout,
                )
                with self.lock:
                    self.state["cpa_queued"] = result.queued
                    self.state["cpa_ok"] = result.ok
                    self.state["cpa_fail"] = result.fail

            snapshot = self.snapshot()
            self.panel_log.write(
                f"[*] 全部结束：成功 {snapshot['success']} | 失败 {snapshot['fail']} / 目标 {count}"
            )
        except Exception as exc:
            self.panel_log.write(f"[!] 任务异常: {exc}")
            with self.lock:
                self.state["last_error"] = str(exc)
        finally:
            with self.lock:
                self.state["running"] = False
                self.state["status"] = "idle"
                self.state["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self.state["pid"] = None
                self.process = None

    def _run_round(self, round_number: int, total: int, proxy: str) -> bool:
        config = self.config_loader()
        config["register_count"] = 1
        config["proxy"] = proxy
        config.setdefault("email_provider", "tempmailer")
        self.config_saver(config)

        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        if os.name == "nt" and not environment.get("BROWSER_PATH"):
            for candidate in (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ):
                if Path(candidate).exists():
                    environment["BROWSER_PATH"] = candidate
                    break
        elif os.name != "nt":
            environment["DISPLAY"] = environment.get("DISPLAY") or ":0"

        self.panel_log.write(
            f"=== 第 {round_number}/{total} 轮开始 · 节点 external-clash ==="
        )
        command = [self.python, "-u", str(self.main_script), "cli"]
        try:
            process = self.popen_factory(
                command,
                cwd=str(self.base_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=environment,
                text=True,
                bufsize=1,
            )
        except (OSError, ValueError) as exc:
            self.panel_log.write(f"[!] 启动失败: {exc}")
            return False
        with self.lock:
            self.process = process
            self.state["pid"] = process.pid

        try:
            assert process.stdin is not None
            process.stdin.write("start\n")
            process.stdin.flush()
        except (OSError, AssertionError) as exc:
            self.panel_log.write(f"[!] 写入 start 失败: {exc}")

        success = False
        failed = False
        assert process.stdout is not None
        for line in process.stdout:
            with self.lock:
                should_stop = bool(self.state.get("stop"))
            if should_stop:
                self.panel_log.write("[!] 收到停止指令，终止当前轮")
                self.process_terminator(process, log_callback=self.panel_log.write)
                break
            line = line.rstrip("\n")
            if not line:
                continue
            self.panel_log.write(line)
            success = success or "注册成功" in line
            failed = failed or "注册失败" in line

        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.panel_log.write(f"[Debug] 注册子进程 pid={process.pid} 超时未退出，清理其进程树")
            self.process_terminator(process, log_callback=self.panel_log.write)
        with self.lock:
            self.state["pid"] = None
            self.process = None
        return success
