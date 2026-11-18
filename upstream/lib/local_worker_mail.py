#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Adapter for the local-domain Cloudflare email worker API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List


class LocalWorkerError(RuntimeError):
    pass


@dataclass
class LocalWorkerMailbox:
    api_base: str
    admin_token: str
    domain: str
    http_get: Callable[..., Any]
    http_post: Callable[..., Any]

    def __post_init__(self) -> None:
        self.api_base = (self.api_base or "").strip().rstrip("/")
        self.admin_token = (self.admin_token or "").strip()
        self.domain = (self.domain or "").strip()
        if not self.api_base:
            raise LocalWorkerError("本地域名邮箱 API 地址未配置")
        if not self.domain:
            raise LocalWorkerError("本地域名邮箱域名未配置")

    def _headers(self, json_body: bool = False) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.admin_token:
            headers["X-Admin-Token"] = self.admin_token
            headers["Authorization"] = f"Bearer {self.admin_token}"
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _json(response: Any, action: str) -> dict:
        response.raise_for_status()
        try:
            data = response.json()
        except Exception as exc:
            preview = str(getattr(response, "text", ""))[:300]
            raise LocalWorkerError(f"{action}返回非 JSON: {preview}") from exc
        if not isinstance(data, dict):
            raise LocalWorkerError(f"{action}响应格式错误")
        return data

    def create_address(self) -> str:
        response = self.http_post(
            f"{self.api_base}/api/mailboxes",
            json={"domain": self.domain, "count": 1},
            headers=self._headers(json_body=True),
        )
        data = self._json(response, "创建邮箱")
        emails = data.get("emails")
        if not isinstance(emails, list) and isinstance(data.get("data"), dict):
            emails = data["data"].get("emails")
        address = str(emails[0] if isinstance(emails, list) and emails else "").strip()
        if not address or "@" not in address:
            raise LocalWorkerError(f"创建邮箱响应缺少有效地址: {data}")
        return address

    def list_messages(self, address: str, limit: int = 100) -> List[dict]:
        response = self.http_get(
            f"{self.api_base}/admin/all",
            params={"limit": max(1, min(int(limit), 500))},
            headers=self._headers(),
        )
        data = self._json(response, "拉取邮件")
        messages = data.get("messages")
        if not isinstance(messages, list):
            return []
        target = (address or "").strip().lower()
        return [
            item
            for item in messages
            if isinstance(item, dict)
            and str(item.get("to_address") or "").strip().lower() == target
        ]

    def get_message(self, message_id: str, address: str) -> dict:
        response = self.http_get(
            f"{self.api_base}/admin/msg",
            params={"id": str(message_id), "email": address},
            headers=self._headers(),
        )
        data = self._json(response, "读取邮件")
        message = data.get("message")
        return message if isinstance(message, dict) else data

    def probe(self) -> dict:
        response = self.http_get(
            f"{self.api_base}/admin/all",
            params={"limit": 1},
            headers=self._headers(),
        )
        data = self._json(response, "检测邮箱服务")
        return {
            "messages": len(data.get("messages") or []),
            "mailboxes": len(data.get("mailboxes") or []),
        }
