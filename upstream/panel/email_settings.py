#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Email-provider configuration presented by the web panel."""

from __future__ import annotations

try:
    from lib.config_store import JsonConfigStore
except ImportError:  # Direct execution through panel/app.py
    from config_store import JsonConfigStore


class EmailSettings:
    def __init__(self, store: JsonConfigStore):
        self.store = store

    def public(self, config: dict | None = None) -> dict:
        current = config if isinstance(config, dict) else self.store.load()
        provider = str(current.get("email_provider") or "tempmailer").strip().lower()
        if provider in ("inboxkitten", "inbox_kitten"):
            provider = "tempmailer"
        if provider == "local_worker":
            ui_provider = "local_worker"
        elif provider == "cloudflare":
            ui_provider = "custom"
        elif provider == "tempmailer":
            ui_provider = "tempmailer"
        else:
            ui_provider = "custom" if current.get("cloudflare_api_base") else "tempmailer"
        return {
            "provider": ui_provider,
            "email_failover": bool(current.get("email_failover", True)),
            "tempmailer_domain": str(
                current.get("tempmailer_domain") or current.get("defaultDomains") or ""
            ).strip(),
            "local_worker_api_base": str(current.get("local_worker_api_base") or "").strip(),
            "local_worker_token_set": bool(
                str(current.get("local_worker_admin_token") or "").strip()
            ),
            "local_worker_domain": str(current.get("local_worker_domain") or "").strip(),
            "custom_api_base": str(current.get("cloudflare_api_base") or "").strip(),
            "custom_api_key_set": bool(str(current.get("cloudflare_api_key") or "").strip()),
            "custom_auth_mode": self._public_auth_mode(current),
            "custom_domain": str(current.get("defaultDomains") or "").strip(),
            "custom_path_accounts": str(
                current.get("cloudflare_path_accounts") or "/admin/new_address"
            ).strip(),
            "custom_path_messages": str(
                current.get("cloudflare_path_messages") or "/api/mails"
            ).strip(),
            "custom_path_token": str(
                current.get("cloudflare_path_token") or "/api/token"
            ).strip(),
            "hint": (
                "本地域名邮箱、Tempmailer 与自定义 API 可按故障链切换。"
                "敏感 Token 仅保存在本地 config.json，不会返回浏览器。"
            ),
        }

    @staticmethod
    def _public_auth_mode(config: dict) -> str:
        mode = str(config.get("cloudflare_auth_mode") or "").strip().lower()
        return "bearer" if mode in ("auth", "bearer", "authorization") else (mode or "x-admin-auth")

    def apply(self, data: dict) -> dict:
        config = self.store.load()
        provider = str(data.get("provider") or "tempmailer").strip().lower()
        if provider in ("inboxkitten", "inbox_kitten"):
            provider = "tempmailer"
        if provider not in ("tempmailer", "local_worker", "custom"):
            raise ValueError("provider 必须是 tempmailer / local_worker / custom")

        config["email_failover"] = bool(data.get("email_failover", True))
        if provider == "tempmailer":
            self._apply_tempmailer(config, data)
        elif provider == "local_worker":
            self._apply_local_worker(config, data)
        else:
            self._apply_custom(config, data)

        self.store.save(config)
        return self.public(config)

    @staticmethod
    def _apply_tempmailer(config: dict, data: dict) -> None:
        domain = str(
            data.get("tempmailer_domain")
            or config.get("tempmailer_domain")
            or "bluenode.cc"
        ).strip()
        config["email_provider"] = "tempmailer"
        config["tempmailer_domain"] = domain
        config["tempmailer_domains"] = [domain] if domain else config.get("tempmailer_domains") or []
        config["defaultDomains"] = domain or config.get("defaultDomains") or ""
        chain = ["tempmailer"]
        if str(config.get("cloudflare_api_base") or "").strip():
            chain.append("cloudflare")
        config["email_providers"] = chain

    @staticmethod
    def _apply_local_worker(config: dict, data: dict) -> None:
        api_base = str(data.get("local_worker_api_base") or "").strip().rstrip("/")
        domain = str(data.get("local_worker_domain") or "").strip()
        if not api_base:
            raise ValueError("本地域名邮箱必须填写 API 地址")
        if not domain:
            raise ValueError("本地域名邮箱必须填写域名")
        config.update(
            email_provider="local_worker",
            local_worker_api_base=api_base,
            local_worker_domain=domain,
        )
        incoming_token = str(data.get("local_worker_admin_token") or "").strip()
        if incoming_token:
            config["local_worker_admin_token"] = incoming_token
        elif data.get("clear_local_worker_admin_token"):
            config["local_worker_admin_token"] = ""
        config["email_providers"] = (
            ["local_worker", "tempmailer"] if config.get("email_failover") else ["local_worker"]
        )

    @staticmethod
    def _apply_custom(config: dict, data: dict) -> None:
        api_base = str(data.get("custom_api_base") or "").strip().rstrip("/")
        if not api_base:
            raise ValueError("自定义模式必须填写 API 地址 cloudflare_api_base")
        config["email_provider"] = "cloudflare"
        config["cloudflare_api_base"] = api_base
        incoming_key = str(data.get("custom_api_key") or "").strip()
        if incoming_key:
            config["cloudflare_api_key"] = incoming_key
        elif data.get("clear_custom_api_key"):
            config["cloudflare_api_key"] = ""
        mode = str(data.get("custom_auth_mode") or "x-admin-auth").strip().lower()
        if mode not in ("none", "bearer", "x-api-key", "x-admin-auth", "query-key"):
            mode = "x-admin-auth"
        config["cloudflare_auth_mode"] = "auth" if mode == "bearer" else mode
        config["defaultDomains"] = str(data.get("custom_domain") or "").strip()
        config["cloudflare_path_accounts"] = str(
            data.get("custom_path_accounts") or "/admin/new_address"
        ).strip() or "/admin/new_address"
        config["cloudflare_path_messages"] = str(
            data.get("custom_path_messages") or "/api/mails"
        ).strip() or "/api/mails"
        config["cloudflare_path_token"] = str(
            data.get("custom_path_token") or "/api/token"
        ).strip() or "/api/token"
        config["email_providers"] = (
            ["cloudflare", "tempmailer"] if config.get("email_failover") else ["cloudflare"]
        )
