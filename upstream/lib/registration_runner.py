#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared GUI/CLI registration orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Type


LogCallback = Callable[[str], None]
StopCallback = Callable[[], bool]


@dataclass
class RegistrationSummary:
    success: int = 0
    fail: int = 0
    completed: int = 0
    results: list[dict] = field(default_factory=list)


@dataclass
class RegistrationActions:
    start_browser: Callable
    stop_browser: Callable
    restart_browser: Callable
    browser_available: Callable[[], bool]
    get_email_provider: Callable[[], str]
    open_signup_page: Callable
    fill_email_and_submit: Callable
    fill_code_and_submit: Callable
    fill_profile_and_submit: Callable
    wait_for_sso_cookie: Callable
    enable_nsfw_for_token: Callable
    add_token_to_pools: Callable
    rotate_email_provider: Callable
    is_mail_related_error: Callable[[Exception], bool]
    sleep_with_cancel: Callable
    cleanup_runtime_memory: Callable
    email_failover_enabled: Callable[[], bool]
    nsfw_enabled: Callable[[], bool]
    cancelled_error: Type[Exception]
    retry_error: Type[Exception]


class RegistrationRunner:
    def __init__(
        self,
        actions: RegistrationActions,
        *,
        log_callback: LogCallback,
        stop_callback: StopCallback,
        stats_callback: Optional[Callable[[RegistrationSummary], None]] = None,
        result_callback: Optional[Callable[[dict], None]] = None,
        memory_cleanup_interval: int = 5,
        max_mail_retry: int = 3,
        max_slot_retry: int = 3,
    ):
        self.actions = actions
        self.log = log_callback
        self.should_stop = stop_callback
        self.stats_callback = stats_callback
        self.result_callback = result_callback
        self.memory_cleanup_interval = memory_cleanup_interval
        self.max_mail_retry = max_mail_retry
        self.max_slot_retry = max_slot_retry

    def run(self, count: int, output_file: Path | str) -> RegistrationSummary:
        summary = RegistrationSummary()
        retry_count_for_slot = 0
        output_path = Path(output_file)
        self.actions.start_browser(log_callback=self.log)
        self.log("[*] 浏览器已启动")
        try:
            while summary.completed < count and not self.should_stop():
                self.log(f"--- 开始第 {summary.completed + 1}/{count} 个账号 ---")
                try:
                    record = self._register_one()
                    self._append_account(output_path, record)
                    self.actions.add_token_to_pools(
                        record["sso"], email=record["email"], log_callback=self.log
                    )
                    summary.results.append(record)
                    if self.result_callback:
                        self.result_callback(record)
                    summary.success += 1
                    summary.completed += 1
                    retry_count_for_slot = 0
                    self.log(f"[+] 注册成功: {record['email']}")
                    if (
                        summary.success % self.memory_cleanup_interval == 0
                        and summary.completed < count
                    ):
                        self.actions.cleanup_runtime_memory(
                            log_callback=self.log,
                            reason=f"已成功 {summary.success} 个账号，执行定期清理",
                        )
                except self.actions.cancelled_error:
                    self.log("[!] 注册被停止")
                    break
                except self.actions.retry_error as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= self.max_slot_retry:
                        self.log(
                            f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/"
                            f"{self.max_slot_retry} 次: {exc}"
                        )
                    else:
                        summary.fail += 1
                        summary.completed += 1
                        retry_count_for_slot = 0
                        self.log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
                except Exception as exc:
                    summary.fail += 1
                    summary.completed += 1
                    retry_count_for_slot = 0
                    self.log(f"[-] 注册失败: {exc}")
                finally:
                    self._publish_stats(summary)
                    if not self.should_stop() and summary.completed < count:
                        if self.actions.browser_available():
                            self.actions.restart_browser(log_callback=self.log)
                        else:
                            self.actions.start_browser(log_callback=self.log)
                        self.actions.sleep_with_cancel(1, self.should_stop)
        finally:
            self.actions.stop_browser()
            self._publish_stats(summary)
        return summary

    def _register_one(self) -> dict:
        email = ""
        dev_token = ""
        code = ""
        for mail_try in range(1, self.max_mail_retry + 1):
            try:
                self.log(
                    f"[*] 1. 打开注册页 (尝试 {mail_try}/{self.max_mail_retry}"
                    f" · 邮箱源={self.actions.get_email_provider()})"
                )
                self.actions.open_signup_page(
                    log_callback=self.log, cancel_callback=self.should_stop
                )
                self.log("[*] 2. 创建邮箱并提交")
                email, dev_token = self.actions.fill_email_and_submit(
                    log_callback=self.log, cancel_callback=self.should_stop
                )
                self.log(f"[*] 邮箱: {email}")
                self.log("[Debug] 邮箱凭证已建立（敏感值不记录）")
                self.log("[*] 3. 拉取验证码")
                code = self.actions.fill_code_and_submit(
                    email,
                    dev_token,
                    log_callback=self.log,
                    cancel_callback=self.should_stop,
                )
                break
            except Exception as exc:
                if self.actions.is_mail_related_error(exc) and mail_try < self.max_mail_retry:
                    message = str(exc)
                    self.log(f"[!] 邮箱/验证码失败，自动切换备用源并换邮箱重试: {message}")
                    if self.actions.email_failover_enabled():
                        self.actions.rotate_email_provider(
                            log_callback=self.log, reason=message[:120]
                        )
                    self.actions.restart_browser(log_callback=self.log)
                    self.actions.sleep_with_cancel(1, self.should_stop)
                    continue
                raise
        else:
            raise RuntimeError("验证码阶段失败，已达到最大重试次数")

        self.log(f"[*] 验证码: {code}")
        self.log("[*] 4. 填写资料")
        profile = self.actions.fill_profile_and_submit(
            log_callback=self.log, cancel_callback=self.should_stop
        )
        self.log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
        self.log("[*] 5. 等待落到 grok.com 并提取 sso cookie（优先 grok.com 域）")
        sso = self.actions.wait_for_sso_cookie(
            log_callback=self.log, cancel_callback=self.should_stop
        )
        if self.actions.nsfw_enabled():
            self.log("[*] 6. 开启 NSFW")
            ok, message = self.actions.enable_nsfw_for_token(sso, log_callback=self.log)
            self.log(f"[{'+' if ok else '!'}] NSFW {'开启成功' if ok else '未开启，继续保存账号'}: {message}")
        return {"email": email, "sso": sso, "profile": profile}

    def _append_account(self, path: Path, record: dict) -> None:
        try:
            line = (
                f"{record['email']}----{record['profile'].get('password', '')}"
                f"----{record['sso']}\n"
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as exc:
            self.log(f"[Debug] 保存账号文件失败: {exc}")

    def _publish_stats(self, summary: RegistrationSummary) -> None:
        if self.stats_callback:
            self.stats_callback(summary)
