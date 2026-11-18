#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Publish Web SSO tokens to local and remote grok2api pools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional


def normalize_sso_token(raw_token) -> str:
    token = str(raw_token or "").strip()
    return token[4:] if token.startswith("sso=") else token


def remote_api_bases(base) -> list[str]:
    normalized = str(base or "").strip().rstrip("/")
    if not normalized:
        return []
    candidates = [normalized]
    lower = normalized.lower()
    if lower.endswith("/admin/api"):
        return candidates
    candidates.append(f"{normalized}/api" if lower.endswith("/admin") else f"{normalized}/admin/api")
    return list(dict.fromkeys(candidates))


class Grok2ApiPoolPublisher:
    def __init__(
        self,
        *,
        root_dir: Path,
        config_getter: Callable[[], dict],
        http_get: Callable,
        http_post: Callable,
    ):
        self.root_dir = Path(root_dir)
        self.config_getter = config_getter
        self.http_get = http_get
        self.http_post = http_post

    def add(self, raw_token, email="", log_callback: Optional[Callable[[str], None]] = None) -> None:
        config = self.config_getter()
        if config.get("grok2api_auto_add_local", True):
            try:
                self.add_local(raw_token, email=email, log_callback=log_callback)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                if log_callback:
                    log_callback(f"[Debug] 写入 grok2api 本地池失败: {exc}")
        if config.get("grok2api_auto_add_remote", False):
            try:
                self.add_remote(raw_token, email=email, log_callback=log_callback)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 写入 grok2api 远端池失败: {exc}")

    def add_local(self, raw_token, email="", log_callback=None) -> bool:
        token = normalize_sso_token(raw_token)
        if not token:
            return False
        config = self.config_getter()
        configured = str(config.get("grok2api_local_token_file", "") or "").strip()
        token_file = Path(configured) if configured else self.root_dir / "token.json"
        pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip() or "ssoBasic"
        token_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if token_file.exists():
            data = json.loads(token_file.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            data = {}
        pool = data.get(pool_name)
        if not isinstance(pool, list):
            pool = []
        existing = {
            normalize_sso_token(item if isinstance(item, str) else item.get("token", ""))
            for item in pool
            if isinstance(item, (str, dict))
        }
        if token not in existing:
            pool.append({"token": token, "tags": ["auto-register"], "note": email})
            data[pool_name] = pool
            token_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        if log_callback:
            log_callback(f"[+] grok2api 本地池已同步: {pool_name} ({token_file})")
        return True

    def add_remote(self, raw_token, email="", log_callback=None) -> bool:
        token = normalize_sso_token(raw_token)
        if not token:
            return False
        config = self.config_getter()
        base = str(config.get("grok2api_remote_base", "") or "").strip().rstrip("/")
        app_key = str(config.get("grok2api_remote_app_key", "") or "").strip()
        pool_name = str(config.get("grok2api_pool_name", "ssoBasic") or "ssoBasic").strip() or "ssoBasic"
        if not base or not app_key:
            if log_callback:
                log_callback("[Debug] grok2api 远端未配置 base/app_key，跳过")
            return False
        headers = {"Content-Type": "application/json"}
        query = {"app_key": app_key}
        remote_pool = {"ssoBasic": "basic", "ssoSuper": "super"}.get(pool_name, "basic")
        api_bases = remote_api_bases(base)
        add_errors = []
        for api_base in api_bases:
            endpoint = f"{api_base}/tokens/add"
            try:
                response = self.http_post(
                    endpoint,
                    headers=headers,
                    params=query,
                    json={"tokens": [token], "pool": remote_pool, "tags": ["auto-register"]},
                    timeout=30,
                )
                response.raise_for_status()
                if log_callback:
                    log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({endpoint})")
                return True
            except Exception as exc:
                add_errors.append(f"{endpoint}: {exc}")
        if log_callback:
            log_callback(f"[Debug] /tokens/add 写入失败，尝试 /tokens 全量模式: {'; '.join(add_errors)}")

        current = {}
        fallback_base = api_bases[0] if api_bases else base
        for api_base in api_bases or [base]:
            try:
                response = self.http_get(
                    f"{api_base}/tokens", headers=headers, params=query, timeout=20
                )
                if response.status_code == 200:
                    payload = response.json()
                    current = payload.get("tokens", {}) if isinstance(payload, dict) else {}
                    fallback_base = api_base
                    break
            except Exception:
                continue
        if not isinstance(current, dict):
            current = {}
        pool = current.get(pool_name)
        if not isinstance(pool, list):
            pool = []
        existing = {
            normalize_sso_token(item if isinstance(item, str) else item.get("token", ""))
            for item in pool
            if isinstance(item, (str, dict))
        }
        if token not in existing:
            pool.append({"token": token, "tags": ["auto-register"], "note": email})
        current[pool_name] = pool
        save_errors = []
        for api_base in list(dict.fromkeys([fallback_base, *(api_bases or [base])])):
            try:
                response = self.http_post(
                    f"{api_base}/tokens",
                    headers=headers,
                    params=query,
                    json=current,
                    timeout=30,
                )
                response.raise_for_status()
                if log_callback:
                    log_callback(f"[+] 已写入 grok2api 远端池: {pool_name} ({api_base}/tokens)")
                return True
            except Exception as exc:
                save_errors.append(f"{api_base}/tokens: {exc}")
        raise RuntimeError(f"grok2api 远端 /tokens 全量模式写入失败: {'; '.join(save_errors)}")
