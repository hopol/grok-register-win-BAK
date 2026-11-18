#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe known Grok endpoints using a selected CPA file."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "lib"))

from curl_cffi import requests as cf_requests

from diagnostic_redaction import load_cpa_entry, resolve_cpa_file, sanitize_text


ENDPOINTS = (
    ("https://grok.com/v1/chat/completions", "grok-3"),
    ("https://api.grok.com/v1/chat/completions", "grok-3"),
    ("https://cli-chat-proxy.grok.com/v1/chat/completions", "grok-3"),
    ("https://cli-chat-proxy.grok.com/v1/chat/completions", "grok-4"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 Grok API 端点")
    parser.add_argument("--cpa-file", default="", help="CPA JSON 路径；默认使用 data/cpa 最新文件")
    args = parser.parse_args()

    path = resolve_cpa_file(ROOT, args.cpa_file)
    entry = load_cpa_entry(path)
    access_token = str(entry.get("access_token") or "")
    proxy = os.environ.get("GROK_PROXY", "http://127.0.0.1:7897")
    session = cf_requests.Session(impersonate="chrome131")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    print(f"CPA file: {path.name}; token set: {bool(access_token)}")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "grok-pager/0.2.93 grok-shell/0.2.93 (linux; x86_64)",
        "x-grok-client-version": "0.2.93",
        "x-xai-token-auth": "xai-grok-cli",
        "x-grok-client-identifier": "grok-pager",
    }
    for url, model in ENDPOINTS:
        try:
            response = session.post(
                url,
                json={"model": model, "messages": [{"role": "user", "content": "say hi"}], "stream": False, "max_tokens": 10},
                headers=headers,
                timeout=20,
            )
            print(f"{url} model={model}: HTTP {response.status_code}")
            print(sanitize_text(response.text, limit=400))
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"{url} model={model}: {type(exc).__name__}: {sanitize_text(str(exc), 200)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
