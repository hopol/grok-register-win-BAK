#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live CPA token diagnostic. This file is not a pytest test module."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))

from curl_cffi import requests as cf_requests

from diagnostic_redaction import load_cpa_entry, redact_value, resolve_cpa_file, sanitize_text


def request_session(proxy: str):
    session = cf_requests.Session(impersonate="chrome131")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def report(label: str, response) -> None:
    print(f"{label}: HTTP {response.status_code}")
    print(sanitize_text(response.text))


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断一个 CPA OAuth JSON")
    parser.add_argument("--cpa-file", default="", help="CPA JSON 路径；默认使用 data/cpa 最新文件")
    args = parser.parse_args()

    path = resolve_cpa_file(ROOT, args.cpa_file)
    entry = load_cpa_entry(path)
    access_token = str(entry.get("access_token") or "")
    refresh_token = str(entry.get("refresh_token") or "")
    base_url = str(entry.get("base_url") or "https://cli-chat-proxy.grok.com/v1").rstrip("/")
    headers = dict(entry.get("headers") or {})
    proxy = os.environ.get("GROK_PROXY", "http://127.0.0.1:7897")

    print(f"CPA file: {path.name}")
    print(f"Email: {entry.get('email') or '<unknown>'}")
    print(f"Base URL: {base_url}")
    print(f"Metadata: {redact_value(headers)}")
    print(f"Access token set: {bool(access_token)}; refresh token set: {bool(refresh_token)}")

    session = request_session(proxy)
    if refresh_token:
        response = session.post(
            "https://auth.x.ai/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": "b1a00492-073a-47ea-816f-4c329264a828",
            },
            headers={
                "User-Agent": "grok-pager/0.2.93 grok-shell/0.2.93 (linux; x86_64)",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
        report("Refresh", response)

    api_headers = {**headers, "Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    response = session.post(
        f"{base_url}/chat/completions",
        json={"model": "grok-3", "messages": [{"role": "user", "content": "hi"}], "stream": False, "max_tokens": 10},
        headers=api_headers,
        timeout=30,
    )
    report("Chat completion", response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
