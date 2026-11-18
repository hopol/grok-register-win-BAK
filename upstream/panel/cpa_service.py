#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPA conversion queue, persistence, and worker lifecycle."""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional


@dataclass(frozen=True)
class BatchCpaResult:
    queued: int
    ok: int
    fail: int
    completed: bool

    def as_dict(self) -> dict:
        return {
            "queued": self.queued,
            "ok": self.ok,
            "fail": self.fail,
            "completed": self.completed,
        }


class CpaConversionService:
    """Own all mutable CPA queue and index state."""

    def __init__(
        self,
        *,
        account_store,
        cpa_dir: Path,
        config_loader: Callable[[], dict],
        default_proxy: Callable[[], str],
        converter,
        normalize_sso: Callable[[str], str],
        fingerprint: Callable[[str], str],
        safe_filename: Callable[[str], str],
        log_callback: Callable[[str], None],
        enabled: bool = True,
        core_error: str = "",
        delay: float = 1.0,
    ):
        self.account_store = account_store
        self.cpa_dir = Path(cpa_dir).resolve()
        self.cpa_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.cpa_dir / "index.json"
        self.failed_path = self.cpa_dir / "failed.jsonl"
        self.config_loader = config_loader
        self.default_proxy = default_proxy
        self.converter = converter
        self.normalize_sso = normalize_sso
        self.fingerprint = fingerprint
        self.safe_filename = safe_filename
        self.log = log_callback
        self.enabled = enabled
        self.core_error = core_error
        self.delay = delay
        self.core_ok = converter is not None and not core_error

        self.lock = threading.Lock()
        self.queue: "queue.Queue[Optional[dict]]" = queue.Queue()
        self.state = {
            "enabled": enabled,
            "core_ok": self.core_ok,
            "core_error": core_error,
            "pending": 0,
            "ok": 0,
            "fail": 0,
            "running": False,
            "last_error": "",
            "last_ok_email": "",
        }
        self.done: set[str] = set()
        self.inflight: set[str] = set()
        self._start_lock = threading.Lock()
        self._started = False

    def start(self) -> None:
        with self._start_lock:
            if self._started:
                return
            self._load_index()
            thread = threading.Thread(
                target=self._worker_loop, name="cpa-worker", daemon=True
            )
            thread.start()
            self._started = True

    def status(self) -> dict:
        with self.lock:
            state = dict(self.state)
            state["done"] = len(self.done)
            state["inflight"] = len(self.inflight)
        files = list(self.cpa_dir.glob("xai-*.json"))
        state.update(
            {
                "dir": str(self.cpa_dir),
                "files": len(files),
                "index": str(self.index_path),
                "failed_log": str(self.failed_path),
            }
        )
        return state

    def enqueue(
        self,
        *,
        email: str,
        sso: str,
        password: str = "",
        source: str = "",
        force: bool = False,
    ) -> tuple[bool, str]:
        token = self.normalize_sso(sso)
        if not token:
            return False, "empty sso"
        if not self.enabled and not force:
            return False, "auto_cpa disabled"
        fingerprint = self.fingerprint(token)
        with self.lock:
            if not force and (fingerprint in self.done or fingerprint in self.inflight):
                return False, "already queued/done"
            self.inflight.add(fingerprint)
            self.state["pending"] = int(self.state.get("pending") or 0) + 1
        self.queue.put(
            {
                "email": email or "",
                "sso": token,
                "password": password or "",
                "source": source or "",
                "fp": fingerprint,
                "force": force,
            }
        )
        return True, "queued"

    def enqueue_new_accounts(self, before: set[str]) -> int:
        new_lines = self.account_store.line_set() - before
        count = 0
        for line in new_lines:
            info = self.account_store.parse_line(line)
            queued, _ = self.enqueue(
                email=info.get("email") or "",
                sso=info.get("sso") or "",
                password=info.get("password") or "",
                source="register",
            )
            count += int(queued)
        return count

    def enqueue_missing_accounts(self, limit: int = 500) -> int:
        count = 0
        for account in self.account_store.unique():
            if count >= limit:
                break
            queued, _ = self.enqueue(
                email=account.get("email") or "",
                sso=account.get("sso") or "",
                password=account.get("password") or "",
                source=account.get("source") or "",
            )
            count += int(queued)
        return count

    def wait_until_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            with self.lock:
                inflight = len(self.inflight)
            if self.queue.unfinished_tasks == 0 and inflight == 0:
                return True
            time.sleep(0.25)
        return False

    def finalize_batch(
        self,
        before: set[str],
        expected_success: int,
        *,
        timeout: float,
    ) -> BatchCpaResult:
        if not self.enabled or expected_success <= 0:
            return BatchCpaResult(0, 0, 0, True)
        if not self.core_ok:
            self.log(f"[CPA] 批次转换跳过，转换核心不可用: {self.core_error}")
            return BatchCpaResult(0, 0, expected_success, True)

        self.start()
        with self.lock:
            ok_before = int(self.state.get("ok") or 0)
            fail_before = int(self.state.get("fail") or 0)

        queued = self.enqueue_new_accounts(before)
        if queued < expected_success:
            queued += self.enqueue_missing_accounts(expected_success - queued)
        self.log(f"[CPA] 注册批次结束，开始自动转换: 已入队 {queued} 个账号")

        completed = self.wait_until_idle(timeout)
        with self.lock:
            ok = max(0, int(self.state.get("ok") or 0) - ok_before)
            fail = max(0, int(self.state.get("fail") or 0) - fail_before)
        if completed:
            self.log(f"[CPA] 批次自动转换完成: 成功 {ok} | 失败 {fail} | 入队 {queued}")
        else:
            self.log(
                f"[CPA] 批次自动转换等待超时（{timeout:.0f}s）: "
                f"已完成 {ok} | 失败 {fail} | 入队 {queued}"
            )
        return BatchCpaResult(queued, ok, fail, completed)

    def _load_index(self) -> None:
        done: set[str] = set()
        if self.index_path.exists():
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                items = data.get("items") if isinstance(data, dict) else data
                if isinstance(items, dict):
                    done.update(items)
                elif isinstance(items, list):
                    done.update(
                        str(item["fp"])
                        for item in items
                        if isinstance(item, dict) and item.get("fp")
                    )
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
                self.log(f"[Debug] 读取 CPA 索引失败，将扫描现有文件: {exc}")
        for path in self.cpa_dir.glob("xai-*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                token = self.normalize_sso(item.get("sso") or "")
                if token:
                    done.add(self.fingerprint(token))
            except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
                self.log(f"[Debug] 跳过无法解析的 CPA 文件 {path.name}: {exc}")
        with self.lock:
            self.done = done
            self.state["ok"] = max(int(self.state.get("ok") or 0), len(done))

    def _save_index_item(self, fingerprint: str, metadata: dict) -> None:
        with self.lock:
            items = {}
            if self.index_path.exists():
                try:
                    current = json.loads(self.index_path.read_text(encoding="utf-8"))
                    items = current.get("items", {}) if isinstance(current, dict) else {}
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
                    self.log(f"[Debug] 读取旧 CPA 索引失败，将重建: {exc}")
            if not isinstance(items, dict):
                items = {}
            items[fingerprint] = metadata
            payload = {
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "items": items,
            }
            temporary = self.index_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.index_path)

    def _worker_loop(self) -> None:
        self.log(
            f"[CPA] worker start · core={'ok' if self.core_ok else 'FAIL'} "
            f"· auto={self.enabled} · dir={self.cpa_dir}"
        )
        while True:
            item = self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            self._convert_item(item)
            if self.delay > 0:
                time.sleep(self.delay)
            self.queue.task_done()

    def _convert_item(self, item: dict) -> None:
        email = item.get("email") or ""
        token = item.get("sso") or ""
        fingerprint = item.get("fp") or self.fingerprint(token)
        with self.lock:
            self.state["running"] = True
            self.state["pending"] = max(0, int(self.state.get("pending") or 0) - 1)
        try:
            if self.converter is None:
                raise RuntimeError(f"core missing: {self.core_error}")
            try:
                proxy = str(self.config_loader().get("proxy") or "").strip()
            except Exception as exc:
                self.log(f"[Debug] CPA 读取代理配置失败，使用当前代理: {exc}")
                proxy = ""
            entry = self.converter(token, email=email, proxy=proxy or self.default_proxy())
            if item.get("password") and not entry.get("password"):
                entry["password"] = item["password"]
            entry["_source"] = "grok-register-auto-cpa"
            entry["_source_file"] = item.get("source") or ""
            output_email = entry.get("email") or email or "unknown"
            filename = f"xai-{self.safe_filename(output_email)}.json"
            path = self.cpa_dir / filename
            if path.exists():
                try:
                    old = json.loads(path.read_text(encoding="utf-8"))
                    old_fp = self.fingerprint(self.normalize_sso(old.get("sso") or ""))
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
                    old_fp = ""
                if old_fp and old_fp != fingerprint:
                    filename = f"xai-{self.safe_filename(output_email)}-{fingerprint[:8]}.json"
                    path = self.cpa_dir / filename
            path.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            self._save_index_item(
                fingerprint,
                {
                    "email": output_email,
                    "file": filename,
                    "at": datetime.now().isoformat(timespec="seconds"),
                    "auth_kind": entry.get("auth_kind"),
                },
            )
            with self.lock:
                self.done.add(fingerprint)
                self.inflight.discard(fingerprint)
                self.state["ok"] = int(self.state.get("ok") or 0) + 1
                self.state["last_ok_email"] = output_email
                self.state["last_error"] = ""
            self.log(f"[CPA] OK {output_email} -> {filename}")
        except Exception as exc:
            error = str(exc)
            with self.lock:
                self.inflight.discard(fingerprint)
                self.state["fail"] = int(self.state.get("fail") or 0) + 1
                self.state["last_error"] = error
            try:
                with self.failed_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "at": datetime.now().isoformat(timespec="seconds"),
                                "email": email,
                                "fp": fingerprint,
                                "error": error,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError as log_exc:
                self.log(f"[Debug] 写入 CPA 失败记录失败: {log_exc}")
            self.log(f"[CPA] FAIL {email or fingerprint[:12]}: {error}")
        finally:
            with self.lock:
                self.state["running"] = not self.queue.empty()
