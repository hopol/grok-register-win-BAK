#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grok 注册机 - TTK GUI 版本
整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import datetime
import time
import os
import sys
import gc
import queue
import secrets
import struct
import random
import re
import string
import json
import base64
import select
import socket
import socketserver
import ssl
import urllib.parse
import uuid

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import requests

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(ROOT_DIR, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from local_worker_mail import LocalWorkerMailbox
from browser_proxy import (
    apply_browser_proxy_option,
    is_proxy_connection_error as browser_proxy_connection_error,
    page_has_proxy_error,
    prepare_browser_proxy as build_browser_proxy,
)
from config_store import ConfigError, JsonConfigStore
from grok2api_pool import Grok2ApiPoolPublisher, normalize_sso_token, remote_api_bases
from registration_runner import RegistrationActions, RegistrationRunner, RegistrationSummary
from runtime_log import get_runtime_logger


CONFIG_FILE = os.path.join(ROOT_DIR, "config.json")
CONFIG_STORE = JsonConfigStore(CONFIG_FILE)
ACCOUNTS_DIR = os.path.join(ROOT_DIR, "data", "accounts")
RUNTIME_LOGGER = get_runtime_logger(
    "grok-register", os.path.join(ROOT_DIR, "data", "logs", "runtime.log")
)
MEMORY_CLEANUP_INTERVAL = 5

UI_BG = "#242424"
UI_PANEL_BG = "#2b2b2b"
UI_FG = "#f2f2f2"
UI_MUTED_FG = "#b8b8b8"
UI_ENTRY_BG = "#333333"
UI_BUTTON_BG = "#3a3a3a"
UI_ACTIVE_BG = "#4a6078"

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "local_worker_api_base": "",
    "local_worker_admin_token": "",
    "local_worker_domain": "",
    "tempmailer_api_base": "https://tempmailer.cc/api",
    "tempmailer_domain": "",
    "tempmailer_domains": [],
    "email_provider": "tempmailer",
    # 邮箱故障自动切换：按 email_providers 顺序轮换（只使用已配置可用的源）
    # 注意：inboxkitten.com 已被 xAI 拒绝，不再作为内置/默认源
    "email_failover": True,
    "email_providers": ["tempmailer", "local_worker", "duckmail", "yyds", "cloudflare"],
    "proxy": "http://127.0.0.1:7890",
    # 代理失败时是否回退直连（本地版默认关闭，避免直连拿不到 grok SSO）
    "allow_proxy_fallback": False,
    "enable_nsfw": True,
    "register_count": 1,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "grok2api_auto_add_local": True,
    "grok2api_local_token_file": "",
    "grok2api_pool_name": "ssoBasic",
    "grok2api_auto_add_remote": False,
    "grok2api_remote_base": "",
    "grok2api_remote_app_key": "",
}

config = DEFAULT_CONFIG.copy()
_cf_domain_index = 0
_email_provider_index = 0
_tempmailer_domain_index = 0


class RegistrationCancelled(Exception):
    pass


class AccountRetryNeeded(Exception):
    pass


def load_config():
    global config
    try:
        config = CONFIG_STORE.load(DEFAULT_CONFIG)
    except ConfigError as exc:
        print(f"[!] 配置读取失败，使用默认配置: {exc}")
        config = DEFAULT_CONFIG.copy()
    return config


def save_config():
    try:
        CONFIG_STORE.save(config)
    except ConfigError as exc:
        print(f"保存配置失败: {exc}")


def new_accounts_output_file():
    os.makedirs(ACCOUNTS_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(ACCOUNTS_DIR, f"accounts_{timestamp}.txt")


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

load_config()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


DUCKMAIL_API_BASE = "https://api.duckmail.sbs"


def get_configured_proxy():
    return str(config.get("proxy", "") or "").strip()


def get_proxies():
    proxy = get_configured_proxy()
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def is_proxy_connection_error(exc):
    return browser_proxy_connection_error(exc, get_configured_proxy())


def stop_browser_proxy_bridge():
    global browser_proxy_bridge
    if browser_proxy_bridge is not None:
        try:
            browser_proxy_bridge.stop()
        except (OSError, RuntimeError) as exc:
            print(f"[Debug] 关闭浏览器认证代理桥失败: {exc}")
    browser_proxy_bridge = None


def prepare_browser_proxy(use_proxy=True, log_callback=None):
    return build_browser_proxy(
        get_configured_proxy(), use_proxy=use_proxy, log_callback=log_callback
    )


def get_duckmail_api_key():
    return config.get("duckmail_api_key", "")


def get_cloudflare_api_base():
    return str(config.get("cloudflare_api_base", "") or "").rstrip("/")


def get_local_worker_api_base():
    return str(config.get("local_worker_api_base", "") or "").strip().rstrip("/")


def get_local_worker_admin_token():
    return str(config.get("local_worker_admin_token", "") or "").strip()


def get_local_worker_domain():
    return str(config.get("local_worker_domain", "") or "").strip()


def local_worker_client():
    return LocalWorkerMailbox(
        api_base=get_local_worker_api_base(),
        admin_token=get_local_worker_admin_token(),
        domain=get_local_worker_domain(),
        http_get=http_get,
        http_post=http_post,
    )


def get_cloudflare_api_key():
    return config.get("cloudflare_api_key", "")


def get_cloudflare_auth_mode():
    return str(config.get("cloudflare_auth_mode", "none") or "none").lower()


def get_cloudflare_path(key, default_path):
    raw = str(config.get(key, default_path) or default_path).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def cloudflare_build_headers(content_type=False):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    return headers


def cloudflare_apply_auth_params(params=None):
    merged = dict(params or {})
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key and mode == "query-key":
        merged["key"] = key
    return merged


def cloudflare_next_default_domain():
    """按配置轮换选择 Cloudflare 临时邮箱域名。"""
    global _cf_domain_index
    domains = [x.strip() for x in str(config.get("defaultDomains", "") or "").split(",") if x.strip()]
    if not domains:
        return ""
    domain = domains[_cf_domain_index % len(domains)]
    _cf_domain_index += 1
    return domain


def cloudflare_is_admin_create_path(path):
    """判断当前创建邮箱路径是否为 cloudflare_temp_email 管理员创建接口。"""
    return str(path or "").rstrip("/").lower() == "/admin/new_address"


def _pick_list_payload(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data.get("results")
        if isinstance(data.get("hydra:member"), list):
            return data.get("hydra:member")
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("messages"), list):
            return data.get("messages")
        if isinstance(data.get("data"), dict):
            nested = data.get("data")
            if isinstance(nested.get("messages"), list):
                return nested.get("messages")
    return []


def cloudflare_create_temp_address(api_base):
    """适配 cloudflare_temp_email 新建地址接口并兼容 admin 创建模式。"""
    path = get_cloudflare_path("cloudflare_path_accounts", "/api/new_address")
    url = f"{api_base}{path}"
    domain = cloudflare_next_default_domain()
    is_admin_create = cloudflare_is_admin_create_path(path)
    if is_admin_create:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_build_headers(content_type=True)
    else:
        payload = {}
        if domain:
            payload["domain"] = domain
        headers = {"Content-Type": "application/json"}
    resp = http_post(url, json=payload, headers=headers)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare {path} 返回非JSON: {resp.text[:300]}")
    address = data.get("address")
    jwt = data.get("jwt")
    if not address or not jwt:
        raise Exception(f"Cloudflare {path} 缺少 address/jwt: {data}")
    return address, jwt


def get_user_agent():
    return config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )


def resolve_grok2api_local_token_file():
    configured = str(config.get("grok2api_local_token_file", "") or "").strip()
    if configured:
        return configured
    return os.path.join(os.path.dirname(__file__), "token.json")


def _normalize_sso_token(raw_token):
    return normalize_sso_token(raw_token)


def _grok2api_publisher():
    return Grok2ApiPoolPublisher(
        root_dir=ROOT_DIR,
        config_getter=lambda: config,
        http_get=http_get,
        http_post=http_post,
    )


def add_token_to_grok2api_local_pool(raw_token, email="", log_callback=None):
    return _grok2api_publisher().add_local(
        raw_token, email=email, log_callback=log_callback
    )


def get_grok2api_remote_api_bases(base):
    return remote_api_bases(base)


def add_token_to_grok2api_remote_pool(raw_token, email="", log_callback=None):
    return _grok2api_publisher().add_remote(
        raw_token, email=email, log_callback=log_callback
    )


def add_token_to_grok2api_pools(raw_token, email="", log_callback=None):
    return _grok2api_publisher().add(
        raw_token, email=email, log_callback=log_callback
    )


def create_browser_options(browser_proxy=""):
    options = ChromiumOptions()
    options.auto_port()
    options.set_timeouts(base=1)
    # Server-friendly browser path / flags
    for _browser_path in (
        os.environ.get("BROWSER_PATH") or "",
        "/snap/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ):
        if _browser_path and os.path.exists(_browser_path):
            try:
                options.set_browser_path(_browser_path)
            except (AttributeError, OSError, TypeError, ValueError) as exc:
                RUNTIME_LOGGER.debug("设置浏览器路径失败: %s", exc, exc_info=True)
            break
    for _arg in ("--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"):
        try:
            options.set_argument(_arg)
        except (AttributeError, TypeError, ValueError) as exc:
            RUNTIME_LOGGER.debug("设置 Chromium 参数 %s 失败: %s", _arg, exc, exc_info=True)
    apply_browser_proxy_option(options, browser_proxy)
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
    return options


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.get(url, **request_kwargs)
    except Exception as exc:
        if request_kwargs.get("proxies") and is_proxy_connection_error(exc):
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.get(url, **_build_request_kwargs(**retry_kwargs))
        raise


def http_post(url, **kwargs):
    request_kwargs = _build_request_kwargs(**kwargs)
    try:
        return requests.post(url, **request_kwargs)
    except Exception as exc:
        if request_kwargs.get("proxies") and is_proxy_connection_error(exc):
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.post(url, **_build_request_kwargs(**retry_kwargs))
        raise


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("鐢ㄦ埛鍋滄娉ㄥ唽")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def get_domains(api_key=None):
    headers = {}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = http_get(f"{DUCKMAIL_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def create_account(address, password, api_key=None, expires_in=0):
    headers = {"Content-Type": "application/json"}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = {"address": address, "password": password, "expiresIn": expires_in}
    resp = http_post(f"{DUCKMAIL_API_BASE}/accounts", json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_token(address, password):
    data = {"address": address, "password": password}
    resp = http_post(f"{DUCKMAIL_API_BASE}/token", json=data)
    resp.raise_for_status()
    return resp.json().get("token")


def get_messages(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def get_message_detail(token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_domains(api_base, api_key=None):
    headers = cloudflare_build_headers(content_type=False)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_domains", "/domains")
    params = cloudflare_apply_auth_params()
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    return _pick_list_payload(resp.json())


def cloudflare_create_account(api_base, address, password, api_key=None, expires_in=0):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    payload = {"address": address, "password": password, "expiresIn": expires_in}
    path = get_cloudflare_path("cloudflare_path_accounts", "/accounts")
    params = cloudflare_apply_auth_params()
    resp = http_post(f"{api_base}{path}", json=payload, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_token(api_base, address, password, api_key=None):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_token", "/token")
    resp = http_post(
        f"{api_base}{path}",
        json={"address": address, "password": password},
        headers=headers,
        params=cloudflare_apply_auth_params(),
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("token"):
            return data.get("token")
        if isinstance(data.get("data"), dict) and data["data"].get("token"):
            return data["data"].get("token")
    return None


def cloudflare_get_messages(api_base, token):
    headers = {"Authorization": f"Bearer {token}"}
    path = get_cloudflare_path("cloudflare_path_messages", "/messages")
    params = {"limit": 20, "offset": 0}
    params = cloudflare_apply_auth_params(params)
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare messages 返回非JSON: {resp.text[:300]}")
    return _pick_list_payload(data)


def cloudflare_get_message_detail(api_base, token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    candidates = [
        f"{api_base}/api/mail/{message_id}",
        f"{api_base}{get_cloudflare_path('cloudflare_path_messages', '/messages')}/{message_id}",
    ]
    last_err = None
    for url in candidates:
        try:
            resp = http_get(
                url,
                headers=headers,
                params=cloudflare_apply_auth_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data
        except Exception as exc:
            last_err = exc
            continue
    raise Exception(f"Cloudflare 获取邮件详情失败: {last_err}")


YYDS_API_BASE = "https://maliapi.215.im/v1"


def get_yyds_api_key():
    return config.get("yyds_api_key", "")


def get_yyds_jwt():
    return config.get("yyds_jwt", "")


def yyds_get_domains(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if data.get("success") else []


def yyds_create_account(address=None, domain=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    payload = {}
    if address:
        payload["address"] = address
    if domain:
        payload["domain"] = domain
    elif key or token:
        payload["autoDomainStrategy"] = "prefer_owned"
    resp = http_post(f"{YYDS_API_BASE}/accounts", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鍒涘缓閭澶辫触: {data}")


def yyds_get_token(address, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_post(
        f"{YYDS_API_BASE}/token", json={"address": address}, headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("token")
    raise Exception(f"YYDS 鑾峰彇token澶辫触: {data}")


def yyds_get_messages(address, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(
        f"{YYDS_API_BASE}/messages",
        params={"address": address},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("messages", [])
    return []


def yyds_get_message_detail(message_id, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 鑾峰彇閭欢璇︽儏澶辫触: {data}")


def yyds_generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def yyds_pick_domain(api_key=None, jwt=None):
    domains = yyds_get_domains(api_key=api_key, jwt=jwt)
    if not domains:
        raise Exception("YYDS 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("isVerified") and not d.get("isPublic")]
    if private:
        return private[0]["domain"]
    public = [d for d in domains if d.get("isVerified") and d.get("isPublic")]
    if public:
        return public[0]["domain"]
    verified = [d for d in domains if d.get("isVerified")]
    if verified:
        return verified[0]["domain"]
    raise Exception("YYDS 鏃犲凡楠岃瘉鍩熷悕鍙敤")


def yyds_get_email_and_token(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    if not token and not key:
        raise Exception("YYDS API Key 或 JWT 未配置")
    domain = yyds_pick_domain(api_key=key, jwt=token)
    username = yyds_generate_username(10)
    result = yyds_create_account(
        address=username, domain=domain, api_key=key, jwt=token
    )
    address = result.get("address") or f"{username}@{domain}"
    temp_token = result.get("token")
    if not temp_token:
        temp_token = yyds_get_token(address, api_key=key, jwt=token)
    if not temp_token:
        raise Exception("鑾峰彇 YYDS token 澶辫触")
    print(f"[*] 宸插垱寤?YYDS 閭: {address}")
    return address, temp_token


def yyds_get_oai_code(
    token,
    address,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    jwt=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = yyds_get_messages(address, token=token, jwt=jwt)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] YYDS 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
                detail = yyds_get_message_detail(msg_id, token=token, jwt=jwt)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] YYDS 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] YYDS 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] YYDS 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"YYDS 在 {timeout}s 内未收到验证码邮件")


def generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def pick_domain(api_key=None):
    domains = get_domains(api_key=api_key)
    if not domains:
        raise Exception("DuckMail 娌℃湁杩斿洖浠讳綍鍙敤鍩熷悕")
    private = [d for d in domains if d.get("ownerId")]
    verified_private = [d for d in private if d.get("isVerified")]
    if verified_private:
        return verified_private[0]["domain"]
    public = [d for d in domains if d.get("isVerified")]
    if public:
        return public[0]["domain"]
    raise Exception("DuckMail 鏃犲凡楠岃瘉鍩熷悕鍙敤")



# ===== tempmailer.cc provider (unofficial internal API) =====
TEMPMAILER_API_BASE_DEFAULT = "https://tempmailer.cc/api"


def get_tempmailer_api_base():
    return str(config.get("tempmailer_api_base") or TEMPMAILER_API_BASE_DEFAULT).rstrip("/")


def tempmailer_new_session_id():
    return str(uuid.uuid4())


def tempmailer_headers(session_id, content_type=True):
    headers = {
        "User-Agent": get_user_agent(),
        "Accept": "application/json",
        "Origin": "https://tempmailer.cc",
        "Referer": "https://tempmailer.cc/zh/",
        "x-tempmailer-session": session_id or tempmailer_new_session_id(),
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def tempmailer_prewarm(session_id):
    base = get_tempmailer_api_base()
    try:
        http_get(f"{base}/prewarm", headers=tempmailer_headers(session_id, content_type=False), timeout=15)
    except Exception as exc:
        RUNTIME_LOGGER.debug("Tempmailer prewarm 失败: %s", exc, exc_info=True)


def get_tempmailer_domains():
    domains = []
    raw_list = config.get("tempmailer_domains") or []
    if isinstance(raw_list, str):
        raw_list = [x.strip() for x in raw_list.split(",") if x.strip()]
    for d in raw_list:
        d = str(d).strip()
        if d and d not in domains and d != "example.com":
            domains.append(d)
    single = str(config.get("tempmailer_domain") or config.get("defaultDomains") or "").strip()
    for d in single.split(","):
        d = d.strip()
        if d and d not in domains and d != "example.com":
            domains.append(d)
    return domains


def pick_tempmailer_domain(rotate=False):
    """Pick tempmailer domain; rotate=True advances to next configured domain."""
    global _tempmailer_domain_index
    domains = get_tempmailer_domains()
    if not domains:
        return ""
    if rotate:
        _tempmailer_domain_index = (_tempmailer_domain_index + 1) % len(domains)
    return domains[_tempmailer_domain_index % len(domains)]


def tempmailer_generate_email(session_id=None, domain=None):
    """Create a temp mailbox. Returns (email, session_id). session is required for inbox."""
    base = get_tempmailer_api_base()
    sid = session_id or tempmailer_new_session_id()
    tempmailer_prewarm(sid)
    headers = tempmailer_headers(sid, content_type=True)
    payload = {}
    use_domain = (domain or pick_tempmailer_domain(rotate=False) or "").strip()
    if use_domain and use_domain not in ("example.com",):
        payload["domain"] = use_domain
    resp = http_post(f"{base}/generate", json=payload, headers=headers, timeout=30)
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"tempmailer generate 返回非JSON: HTTP {resp.status_code} {resp.text[:300]}")
    email = (data or {}).get("email")
    if not email:
        raise Exception(f"tempmailer generate 失败: HTTP {resp.status_code} {data}")
    return email, sid


def tempmailer_get_email_and_token():
    domains = get_tempmailer_domains() or [""]
    errors = []
    # 多域名时逐个尝试
    for i, _ in enumerate(domains):
        domain = pick_tempmailer_domain(rotate=(i > 0))
        try:
            email, sid = tempmailer_generate_email(domain=domain or None)
            print(f"[*] 已创建 tempmailer 邮箱: {email}" + (f" (domain={domain})" if domain else ""))
            return email, sid
        except Exception as exc:
            errors.append(f"{domain or 'auto'}: {exc}")
            continue
    raise Exception("tempmailer 创建邮箱失败: " + "; ".join(errors))


def tempmailer_inbox_url(email, message_id=None):
    base = get_tempmailer_api_base()
    encoded = urllib.parse.quote(email, safe="")
    if message_id is None:
        return f"{base}/inbox/{encoded}"
    return f"{base}/inbox/{encoded}/{urllib.parse.quote(str(message_id), safe='')}"


def tempmailer_get_messages(email, session_id):
    resp = http_get(
        tempmailer_inbox_url(email),
        headers=tempmailer_headers(session_id, content_type=False),
        timeout=20,
    )
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"tempmailer inbox 返回非JSON: HTTP {resp.status_code} {resp.text[:300]}")
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("messages", "mails", "items", "data", "inbox"):
            val = data.get(key)
            if isinstance(val, list):
                return val
        # single message object
        if data.get("id") or data.get("subject") or data.get("html_body") or data.get("text_body"):
            return [data]
    return []


def tempmailer_get_message_detail(email, message_id, session_id):
    resp = http_get(
        tempmailer_inbox_url(email, message_id),
        headers=tempmailer_headers(session_id, content_type=False),
        timeout=20,
    )
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"tempmailer mail detail 返回非JSON: HTTP {resp.status_code} {resp.text[:300]}")
    if isinstance(data, dict):
        return data
    return {}


def tempmailer_message_text(msg):
    parts = []
    if not isinstance(msg, dict):
        return ""
    for field in (
        "subject",
        "text_body",
        "html_body",
        "text",
        "html",
        "body",
        "content",
        "raw",
        "intro",
        "snippet",
        "preview",
    ):
        value = msg.get(field)
        if isinstance(value, str) and value.strip():
            if field in ("html_body", "html"):
                parts.append(re.sub(r"<[^>]+>", " ", value))
            else:
                parts.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    parts.append(re.sub(r"<[^>]+>", " ", item) if field in ("html_body", "html") else item)
    return "\n".join(parts)


def tempmailer_get_oai_code(
    session_id,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    deadline = time.time() + timeout
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = tempmailer_get_messages(email, session_id)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] tempmailer 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] tempmailer 本轮邮件数量: {len(messages)}")
        for msg in messages:
            msg_id = (
                msg.get("id")
                or msg.get("msgid")
                or msg.get("message_id")
                or msg.get("uid")
                or msg.get("mail_id")
            )
            # fallback key when id missing
            if not msg_id:
                msg_id = f"subj:{msg.get('subject','')}|from:{msg.get('from','')}|ts:{msg.get('date') or msg.get('created_at') or msg.get('timestamp') or ''}"
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 8:
                continue
            seen_attempts[msg_id] = attempt + 1
            combined = tempmailer_message_text(msg)
            subject = str(msg.get("subject", "") or "")
            # detail endpoint if available and content thin
            if msg_id and (not combined.strip() or len(combined) < 20):
                try:
                    detail = tempmailer_get_message_detail(email, msg_id, session_id)
                    combined = (combined + "\n" + tempmailer_message_text(detail)).strip()
                    if not subject:
                        subject = str(detail.get("subject", "") or "")
                except Exception as exc:
                    if log_callback:
                        log_callback(f"[Debug] tempmailer detail失败，用列表内容: {exc}")
            if log_callback:
                log_callback(f"[Debug] tempmailer 收到邮件: {subject or '(no subject)'}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] tempmailer 从邮件中提取到验证码: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"tempmailer 在 {timeout}s 内未收到验证码邮件")


# ===== end tempmailer =====


def local_worker_get_email_and_token():
    address = local_worker_client().create_address()
    # The worker uses one configured admin token; keep it out of runtime logs.
    return address, "local-worker-session"


def local_worker_get_oai_code(
    _dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    client = local_worker_client()
    deadline = time.time() + timeout
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = client.list_messages(email)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 本地域名邮箱拉取失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] 本地域名邮箱本轮邮件数量: {len(messages)}")
        for message in messages:
            message_id = message.get("id") or message.get("message_id")
            if not message_id:
                continue
            attempts = int(seen_attempts.get(str(message_id), 0))
            if attempts >= 5:
                continue
            seen_attempts[str(message_id)] = attempts + 1
            subject = str(message.get("subject") or "")
            parts = [subject]
            for field in ("bodyPreview", "body", "raw_text", "text", "content"):
                value = message.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            extracted = message.get("extracted_json")
            if extracted:
                parts.append(json.dumps(extracted, ensure_ascii=False) if not isinstance(extracted, str) else extracted)
            try:
                detail = client.get_message(str(message_id), email)
                for field in ("body", "raw_text", "bodyPreview", "text", "content"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        parts.append(value)
                if not subject:
                    subject = str(detail.get("subject") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 本地域名邮箱详情读取失败: {exc}")
            code = extract_verification_code("\n".join(parts), subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 本地域名邮箱提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"本地域名邮箱在 {timeout}s 内未收到验证码邮件")


def get_email_provider():
    p = str(config.get("email_provider") or "tempmailer").strip().lower()
    # inboxkitten.com 域名已被 xAI 拒绝，强制改用 tempmailer
    if p in ("inboxkitten", "inbox_kitten"):
        return "tempmailer"
    return p or "tempmailer"


def email_provider_ready(provider: str) -> bool:
    """判断邮箱源是否已配置到可尝试状态。"""
    p = (provider or "").strip().lower()
    # 已从内置移除：xAI 拒绝 inboxkitten.com 域名
    if p in ("inboxkitten", "inbox_kitten"):
        return False
    if p == "tempmailer":
        return bool(get_tempmailer_api_base())
    if p == "local_worker":
        return bool(get_local_worker_api_base() and get_local_worker_domain())
    if p == "duckmail":
        return bool(str(config.get("duckmail_api_key") or "").strip())
    if p == "yyds":
        return bool(
            str(config.get("yyds_api_key") or "").strip()
            or str(config.get("yyds_jwt") or "").strip()
        )
    if p == "cloudflare":
        return bool(str(config.get("cloudflare_api_base") or "").strip())
    return False


def get_email_provider_chain():
    """可用邮箱源列表：优先 config.email_providers，过滤未配置项。"""
    raw = config.get("email_providers")
    if isinstance(raw, str):
        chain = [x.strip().lower() for x in raw.split(",") if x.strip()]
    elif isinstance(raw, list):
        chain = [str(x).strip().lower() for x in raw if str(x).strip()]
    else:
        chain = []
    # 过滤已废弃的 inboxkitten
    chain = [p for p in chain if p not in ("inboxkitten", "inbox_kitten")]
    primary = get_email_provider()
    if primary and primary not in chain:
        chain.insert(0, primary)
    if not chain:
        chain = [primary or "tempmailer"]
    # 去重保持顺序
    seen = set()
    ordered = []
    for p in chain:
        if p not in seen:
            seen.add(p)
            ordered.append(p)
    ready = [p for p in ordered if email_provider_ready(p)]
    # 若一个都没 ready，仍返回 primary 以免完全不可用
    return ready or ([primary] if primary else ["tempmailer"])


def rotate_email_provider(log_callback=None, reason=""):
    """切换到下一个可用邮箱源；返回新 provider。"""
    global _email_provider_index
    chain = get_email_provider_chain()
    if not chain:
        return get_email_provider()
    if len(chain) == 1:
        # 仅一个源：若是 tempmailer 则轮换域名
        if chain[0] == "tempmailer" and len(get_tempmailer_domains()) > 1:
            domain = pick_tempmailer_domain(rotate=True)
            if log_callback:
                log_callback(
                    f"[*] 邮箱源仅 tempmailer，切换域名: {domain}"
                    + (f" ({reason})" if reason else "")
                )
            return chain[0]
        if log_callback:
            log_callback(
                f"[!] 无其它可用邮箱源可切换，继续使用 {chain[0]}"
                + (f" ({reason})" if reason else "")
            )
        config["email_provider"] = chain[0]
        return chain[0]
    _email_provider_index = (_email_provider_index + 1) % len(chain)
    new_p = chain[_email_provider_index]
    config["email_provider"] = new_p
    if log_callback:
        log_callback(
            f"[*] 邮箱源已切换为: {new_p}（链: {', '.join(chain)}）"
            + (f" · 原因: {reason}" if reason else "")
        )
    return new_p


def is_mail_related_error(exc) -> bool:
    msg = str(exc or "").lower()
    keys = (
        "验证码",
        "邮箱",
        "邮件",
        "tempmailer",
        "本地域名邮箱",
        "local_worker",
        "inboxkitten",
        "duckmail",
        "yyds",
        "未收到",
        "generate",
        "创建邮箱",
        "inbox",
        "/api/mail",
        "new_address",
        "mail_api",
        "http 429",
        "rate limit",
        "限流",
        "所有邮箱源",
    )
    return any(k.lower() in msg for k in keys)


def _get_email_and_token_once(provider, api_key=None):
    provider = (provider or "").strip().lower()
    if provider in ("inboxkitten", "inbox_kitten"):
        raise Exception(
            "InboxKitten 已移除：xAI 拒绝域名 inboxkitten.com，请改用 tempmailer 或自定义邮箱"
        )
    if provider == "tempmailer":
        return tempmailer_get_email_and_token()
    if provider == "local_worker":
        return local_worker_get_email_and_token()
    if provider == "yyds":
        return yyds_get_email_and_token(api_key=api_key, jwt=get_yyds_jwt())
    if provider == "cloudflare":
        api_base = get_cloudflare_api_base()
        if not api_base:
            raise Exception("Cloudflare API Base 未配置")
        try:
            return cloudflare_create_temp_address(api_base)
        except Exception as primary_exc:
            key = api_key or get_cloudflare_api_key()
            domains = cloudflare_get_domains(api_base, api_key=key)
            if not domains:
                raise Exception(f"Cloudflare 创建邮箱失败: {primary_exc}")
            verified = [d for d in domains if d.get("isVerified")]
            target = verified[0] if verified else domains[0]
            domain = target.get("domain")
            if not domain:
                raise Exception("Cloudflare 域名数据格式错误，缺少 domain 字段")
            username = generate_username(10)
            address = f"{username}@{domain}"
            password = secrets.token_urlsafe(12)
            cloudflare_create_account(
                api_base, address, password, api_key=key, expires_in=0
            )
            token = cloudflare_get_token(api_base, address, password, api_key=key)
            if not token:
                raise Exception("获取 Cloudflare 邮箱 token 失败")
            return address, token
    # duckmail default
    key = api_key or get_duckmail_api_key()
    if not key:
        raise Exception("DuckMail API Key 未配置")
    domain = pick_domain(api_key=key)
    username = generate_username(10)
    address = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    create_account(address, password, api_key=key, expires_in=0)
    token = get_token(address, password)
    if not token:
        raise Exception("获取 DuckMail token 失败")
    return address, token


def get_email_and_token(api_key=None, log_callback=None):
    """创建临时邮箱；失败时按 email_providers 自动切换备用源。"""
    global _email_provider_index
    chain = get_email_provider_chain()
    failover = bool(config.get("email_failover", True))
    # 对齐当前 provider 在链中的下标
    primary = get_email_provider()
    if primary in chain:
        _email_provider_index = chain.index(primary)
    errors = []
    attempts = len(chain) if failover else 1
    for i in range(attempts):
        provider = chain[(_email_provider_index + i) % len(chain)]
        config["email_provider"] = provider
        try:
            email, token = _get_email_and_token_once(provider, api_key=api_key)
            if log_callback:
                log_callback(f"[*] 邮箱源 {provider} 创建成功: {email}")
            # 记住当前成功源
            _email_provider_index = chain.index(provider) if provider in chain else _email_provider_index
            return email, token
        except Exception as exc:
            errors.append(f"{provider}: {exc}")
            if log_callback:
                log_callback(f"[!] 邮箱源 {provider} 创建失败: {exc}")
            if not failover:
                break
            # 尝试下一个
            continue
    raise Exception("所有邮箱源均创建失败: " + " | ".join(errors))


def get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    provider = get_email_provider()
    if provider in ("inboxkitten", "inbox_kitten"):
        provider = "tempmailer"
    if provider == "tempmailer":
        return tempmailer_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    if provider == "local_worker":
        return local_worker_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    if provider == "yyds":
        return yyds_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            jwt=get_yyds_jwt(),
            cancel_callback=cancel_callback,
        )
    if provider == "cloudflare":
        return cloudflare_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    return duckmail_get_oai_code(
        dev_token,
        email,
        timeout=timeout,
        poll_interval=poll_interval,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
    )


def extract_verification_code(text, subject=""):
    """从邮件主题/正文提取 xAI/Grok 验证码（对齐 grok-reg-tool 策略）。"""
    content = text or ""
    if subject:
        content = f"Subject: {subject}\n{content}"
    if not content:
        return None

    # Subject 行优先：6F2-CT8 xAI confirmation code
    match = re.search(
        r"(?:Subject:\s*)?([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI",
        content,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()

    # 独立 XXX-XXX（避免被邮箱/长串误伤）
    match = re.search(
        r"(?<![A-Z0-9-])([A-Z0-9]{3}-[A-Z0-9]{3})(?![A-Z0-9-])",
        content,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()

    # 文案旁带标签
    match = re.search(
        r"(?:verification\s+code|验证码|your\s+code|confirmation\s+code)[:\s]*[<>\s]*([A-Z0-9]{3}-[A-Z0-9]{3})\b",
        content,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()

    # HTML 灰底块常见样式
    match = re.search(
        r"background-color:\s*#F3F3F3[^>]*>[\s\S]*?([A-Z0-9]{3}-[A-Z0-9]{3})[\s\S]*?</p>",
        content,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).upper()

    patterns = [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1)

    # 6 位数字兜底（排除常见噪声）
    for code in re.findall(r">\s*(\d{6})\s*<", content):
        if code != "177010":
            return code
    for code in re.findall(r"(?<![&#\d])(\d{6})(?![&#\d])", content):
        if code != "177010":
            return code
    return None


def duckmail_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 鎷夊彇閭欢鍒楄〃澶辫触: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
                detail = get_message_detail(dev_token, msg_id)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 鑾峰彇閭欢璇︽儏澶辫触: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] 鏀跺埌閭欢: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 浠庨偖浠朵腑鎻愬彇鍒伴獙璇佺爜: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def cloudflare_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    api_base = get_cloudflare_api_base()
    if not api_base:
        raise Exception("Cloudflare API Base 未配置")
    deadline = time.time() + timeout
    # 同一封邮件正文可能延迟可读，允许多次重试解析，避免偶发漏码
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = cloudflare_get_messages(api_base, dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Cloudflare 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] Cloudflare 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            msg_addr = str(msg.get("address", "")).lower()
            # 优先匹配目标邮箱；若结构不一致也允许继续解析，避免接口字段漂移导致漏码
            address_matched = True
            if recipients:
                address_matched = email.lower() in recipients
            elif msg_addr:
                address_matched = msg_addr == email.lower()
            if not address_matched and log_callback:
                log_callback(f"[Debug] 跳过疑似非目标邮件 id={msg_id} address={msg_addr} to={recipients}")
                continue
            parts = []
            # 先直接从列表项取内容，避免 detail 接口差异导致漏码
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)
            # 再尝试 detail 接口补全内容
            try:
                detail = cloudflare_get_message_detail(api_base, dev_token, msg_id)
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Cloudflare detail接口失败，改用列表内容解析: {exc}")
            if log_callback:
                log_callback(f"[Debug] Cloudflare 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Cloudflare 从邮件中提取到验证码: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Cloudflare 在 {timeout}s 内未收到验证码邮件")


def generate_random_birthdate():
    import datetime as dt

    today = dt.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


def response_preview(res, limit=200):
    try:
        text = str(res.text or "")
    except Exception:
        text = ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def is_cloudflare_block_response(res):
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(res.headers).items()}
        text = str(res.text or "").lower()
        server = headers.get("server", "")
        content_type = headers.get("content-type", "")
        return (
            res.status_code in (403, 429, 503)
            and (
                "cloudflare" in server
                or "cloudflare" in text
                or "cf-error" in text
                or "__cf_chl" in text
                or "text/html" in content_type
            )
        )
    except Exception:
        return False


def set_birth_date(session, log_callback=None):
    url = "https://grok.com/rest/auth/set-birth-date"
    new_headers = {
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    payload = {"birthDate": generate_random_birthdate()}
    try:
        res = session.post(url, json=payload, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] set_birth_date status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_birth_date 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_birth_date HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_birth_date] 异常: {e}")
        return False, f"set_birth_date 异常: {e}"


def set_tos_accepted(session, log_callback=None):
    url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
    payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
    data = b"\x00" + struct.pack(">I", len(payload)) + payload
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": "https://accounts.x.ai",
        "referer": "https://accounts.x.ai/accept-tos",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] set_tos_accepted status: {res.status_code}")
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_tos_accepted 被 accounts.x.ai 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_tos_accepted HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_tos_accepted] 异常: {e}")
        return False, f"set_tos_accepted 异常: {e}"


def encode_grpc_nsfw_settings():
    field1_content = bytes([0x10, 0x01])
    field1 = bytes([0x0A, len(field1_content)]) + field1_content
    nsfw_string = b"always_show_nsfw_content"
    field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
    field2 = bytes([0x12, len(field2_inner)]) + field2_inner
    payload = field1 + field2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def update_nsfw_settings(session, log_callback=None):
    url = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
    data = encode_grpc_nsfw_settings()
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] update_nsfw status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "update_nsfw_settings 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"update_nsfw_settings HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[update_nsfw] 异常: {e}")
        return False, f"update_nsfw_settings 异常: {e}"


def enable_nsfw_for_token(token, cf_clearance="", log_callback=None):
    proxies = get_proxies()
    user_agent = get_user_agent()
    try:
        with requests.Session(impersonate="chrome120", proxies=proxies) as session:
            cookie_parts = [f"sso={token}", f"sso-rw={token}"]
            if cf_clearance:
                cookie_parts.append(f"cf_clearance={cf_clearance}")
            session.headers.update(
                {
                    "user-agent": user_agent,
                    "cookie": "; ".join(cookie_parts),
                }
            )
            ok, message = set_tos_accepted(session, log_callback)
            if not ok:
                return False, message
            ok, message = set_birth_date(session, log_callback)
            if not ok:
                return False, message
            ok, message = update_nsfw_settings(session, log_callback)
            if not ok:
                return False, message
            return True, "成功开启 NSFW"
    except Exception as e:
        return False, f"异常: {str(e)}"


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

browser = None
page = None
browser_proxy_bridge = None
browser_started_with_proxy = False


def setup_light_theme(root):
    try:
        root.option_add("*Background", UI_BG)
        root.option_add("*Foreground", UI_FG)
        root.option_add("*selectBackground", UI_ACTIVE_BG)
        root.option_add("*selectForeground", UI_FG)
        root.option_add("*insertBackground", UI_FG)
        root.option_add("*Entry.Background", UI_ENTRY_BG)
        root.option_add("*Text.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Foreground", UI_FG)
        style = ttk.Style(root)
        available = set(style.theme_names())
        if "clam" in available:
            style.theme_use("clam")
        elif "default" in available:
            style.theme_use("default")
        root.configure(bg=UI_BG)
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_ENTRY_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabelframe", background=UI_BG, foreground=UI_FG)
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
        style.configure("TButton", background=UI_BUTTON_BG, foreground=UI_FG)
        style.configure("TEntry", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TCombobox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TSpinbox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
    except (AttributeError, RuntimeError, TypeError) as exc:
        RUNTIME_LOGGER.debug("Tk 主题扩展配置失败: %s", exc, exc_info=True)


def tk_label(parent, text="", **kwargs):
    return tk.Label(parent, text=text, bg=kwargs.pop("bg", UI_BG), fg=kwargs.pop("fg", UI_FG), **kwargs)


def tk_entry(parent, textvariable=None, width=30, **kwargs):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        disabledbackground="#2f2f2f",
        disabledforeground=UI_MUTED_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
        **kwargs,
    )


def tk_button(parent, text="", command=None, state=tk.NORMAL, **kwargs):
    return tk.Button(
        parent,
        text=text,
        command=command,
        state=state,
        bg=UI_BUTTON_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        disabledforeground="#777777",
        relief=tk.RAISED,
        padx=10,
        pady=3,
        **kwargs,
    )


def tk_checkbutton(parent, text="", variable=None, **kwargs):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        activeforeground=UI_FG,
        selectcolor="#3d7be0",
        **kwargs,
    )


def tk_option_menu(parent, variable, values, width=12):
    menu = tk.OptionMenu(parent, variable, *values)
    menu.configure(
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
    )
    menu["menu"].configure(bg=UI_ENTRY_BG, fg=UI_FG, activebackground=UI_ACTIVE_BG, activeforeground=UI_FG)
    return menu


def start_browser(log_callback=None, use_proxy=True):
    global browser, page, browser_proxy_bridge, browser_started_with_proxy
    last_exc = None
    proxy_enabled = bool(use_proxy and get_configured_proxy())
    for attempt in range(1, 5):
        bridge = None
        try:
            browser_proxy, bridge = prepare_browser_proxy(use_proxy=use_proxy, log_callback=log_callback)
            browser = Chromium(create_browser_options(browser_proxy=browser_proxy))
            browser_proxy_bridge = bridge
            browser_started_with_proxy = bool(browser_proxy)
            tabs = browser.get_tabs()
            page = tabs[-1] if tabs else browser.new_tab()
            if log_callback and getattr(browser, "user_data_path", None):
                log_callback(f"[Debug] 当前浏览器资料目录: {browser.user_data_path}")
            if log_callback and get_configured_proxy():
                mode = "代理" if browser_started_with_proxy else "直连"
                log_callback(f"[*] 浏览器网络模式: {mode}")
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return browser, page
        except Exception as exc:
            last_exc = exc
            if bridge is not None:
                try:
                    bridge.stop()
                except (OSError, RuntimeError) as stop_exc:
                    RUNTIME_LOGGER.debug("启动失败后关闭代理桥失败: %s", stop_exc, exc_info=True)
            if log_callback:
                mode = "代理" if proxy_enabled else "直连"
                log_callback(f"[Debug] 浏览器{mode}启动失败(第{attempt}/4次): {exc}")
            try:
                if browser is not None:
                    browser.quit(del_data=True)
            except (AttributeError, OSError, RuntimeError) as quit_exc:
                RUNTIME_LOGGER.debug("启动失败后关闭浏览器失败: %s", quit_exc, exc_info=True)
            browser = None
            page = None
            browser_proxy_bridge = None
            browser_started_with_proxy = False
            time.sleep(min(1.5 * attempt, 4))
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser():
    global browser, page, browser_started_with_proxy
    if browser is not None:
        try:
            browser.quit(del_data=True)
        except (AttributeError, OSError, RuntimeError) as exc:
            RUNTIME_LOGGER.debug("关闭浏览器失败: %s", exc, exc_info=True)
    stop_browser_proxy_bridge()
    browser = None
    page = None
    browser_started_with_proxy = False


def restart_browser(log_callback=None, use_proxy=True):
    stop_browser()
    return start_browser(log_callback=log_callback, use_proxy=use_proxy)


def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    if log_callback:
        log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
    stop_browser()
    collected = gc.collect()
    if log_callback:
        log_callback(f"[*] Python GC 已回收对象数: {collected}")


def refresh_active_page():
    global browser, page
    if browser is None:
        restart_browser()
    try:
        tabs = browser.get_tabs()
        if tabs:
            page = tabs[-1]
        else:
            page = browser.new_tab()
    except Exception:
        restart_browser()
    return page


def click_email_signup_button(timeout=10, log_callback=None, cancel_callback=None):
    global page
    deadline = time.time() + timeout
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if log_callback:
            log_callback("[Debug] 尝试查找“使用邮箱注册”按钮...")

        clicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower === 'email' || lower.includes('邮箱')) return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
const target = candidates[0]?.node || null;
if (!target) {
    return false;
}
target.click();
return candidates[0].text || true;
        """)

        if clicked:
            if log_callback:
                detail = f": {clicked}" if isinstance(clicked, str) else ""
                log_callback(f"[*] 已点击「使用邮箱注册」按钮{detail}")
            sleep_with_cancel(2, cancel_callback)
            return True

        if log_callback:
            current_url = page.url if page else "none"
            log_callback(f"[Debug] 当前URL: {current_url}")

        sleep_with_cancel(1, cancel_callback)

    if log_callback:
        page_html = page.html[:500] if page else "no page"
        log_callback(f"[Debug] 页面内容片段: {page_html}")

    raise Exception("未找到「使用邮箱注册」按钮")


def open_signup_page(log_callback=None, cancel_callback=None):
    global browser, page
    raise_if_cancelled(cancel_callback)
    if browser is None:
        start_browser(log_callback=log_callback)
        if log_callback:
            log_callback("[*] 浏览器已启动")

    def _open_with_current_browser():
        global page
        try:
            page = browser.get_tab(0)
            page.get(SIGNUP_URL)
        except Exception as e:
            if log_callback:
                log_callback(f"[Debug] 打开URL异常: {e}")
            page = browser.new_tab(SIGNUP_URL)
        page.wait.doc_loaded()

    allow_fallback = bool(config.get("allow_proxy_fallback", False))

    try:
        _open_with_current_browser()
    except Exception as e:
        if browser_started_with_proxy and get_configured_proxy() and allow_fallback:
            if log_callback:
                log_callback(f"[!] 浏览器代理访问注册页失败，自动回退直连: {e}")
            restart_browser(log_callback=log_callback, use_proxy=False)
            _open_with_current_browser()
        else:
            if browser_started_with_proxy and get_configured_proxy() and not allow_fallback:
                raise Exception(
                    f"浏览器经代理打开注册页失败（已关闭直连回退，请检查 Clash 是否监听 7890）: {e}"
                ) from e
            raise

    if browser_started_with_proxy and page_has_proxy_error(page):
        if allow_fallback:
            if log_callback:
                log_callback("[!] 浏览器页面显示代理错误，自动回退直连")
            restart_browser(log_callback=log_callback, use_proxy=False)
            _open_with_current_browser()
        else:
            raise Exception(
                "浏览器页面显示代理错误（已关闭直连回退）。请先打开 Clash，确认 mixed/HTTP 端口为 7890，并选好可用节点。"
            )

    sleep_with_cancel(2, cancel_callback)
    if log_callback:
        log_callback(f"[*] 当前URL: {page.url}")
    click_email_signup_button(
        log_callback=log_callback, cancel_callback=cancel_callback
    )


def has_profile_form(log_callback=None):
    refresh_active_page()
    try:
        return bool(
            page.run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
            """
            )
        )
    except Exception:
        return False


def wait_for_email_submission_result(
    timeout=18,
    log_callback=None,
    cancel_callback=None,
):
    """Confirm that xAI accepted the email before polling the mailbox."""
    deadline = time.time() + timeout
    last_snapshot = {}
    last_log_at = 0.0
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        snapshot = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return (node.innerText || node.textContent || node.getAttribute('aria-label') || '')
        .replace(/\s+/g, ' ').trim();
}
const visibleInputs = Array.from(document.querySelectorAll('input')).filter((node) =>
    isVisible(node) && !node.disabled && !node.readOnly
);
const aggregateOtp = visibleInputs.find((node) => {
    const name = `${node.name || ''} ${node.id || ''} ${node.getAttribute('data-testid') || ''}`.toLowerCase();
    const autocomplete = String(node.autocomplete || '').toLowerCase();
    const inputmode = String(node.inputMode || '').toLowerCase();
    return node.getAttribute('data-input-otp') === 'true' || autocomplete === 'one-time-code' ||
        name.includes('code') || name.includes('otp') || inputmode === 'numeric';
});
const otpBoxes = visibleInputs.filter((node) => Number(node.maxLength || 0) === 1);
if (aggregateOtp || otpBoxes.length >= 4) {
    return { state: 'verification', url: location.href };
}
const profile = {
    given: document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]'),
    family: document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]'),
    password: document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]'),
};
if (isVisible(profile.given) && isVisible(profile.family) && isVisible(profile.password)) {
    return { state: 'profile', url: location.href };
}
const errorNodes = Array.from(document.querySelectorAll(
    '[role="alert"], [aria-live="assertive"], [data-sonner-toast], .error, [class*="error" i]'
)).filter(isVisible);
const errors = errorNodes.map(textOf).filter(Boolean).filter((text, index, all) => all.indexOf(text) === index);
const emailInput = visibleInputs.find((node) => {
    const meta = `${node.type || ''} ${node.name || ''} ${node.id || ''} ${node.placeholder || ''} ${node.autocomplete || ''}`.toLowerCase();
    return meta.includes('email') || meta.includes('e-mail') || meta.includes('mail');
});
const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled)
    .map(textOf).filter(Boolean).slice(0, 8);
return {
    state: emailInput ? 'email-form' : 'pending',
    url: location.href,
    errors: errors.slice(0, 4),
    buttons,
};
            """
        )
        if isinstance(snapshot, dict):
            last_snapshot = snapshot
        state = snapshot.get("state") if isinstance(snapshot, dict) else str(snapshot or "")
        if state in ("verification", "profile"):
            if log_callback:
                log_callback(f"[*] 邮箱提交已被接受，页面状态: {state}")
            return state
        errors = snapshot.get("errors") or [] if isinstance(snapshot, dict) else []
        if errors:
            raise Exception("邮箱提交被 xAI 拒绝: " + " | ".join(str(x) for x in errors[:3]))
        now = time.time()
        if log_callback and now - last_log_at >= 3:
            last_log_at = now
            buttons = " | ".join(snapshot.get("buttons") or []) if isinstance(snapshot, dict) else ""
            url = snapshot.get("url") if isinstance(snapshot, dict) else getattr(page, "url", "")
            log_callback(f"[Debug] 等待验证码页面: state={state or 'unknown'} url={url} buttons={buttons or 'none'}")
        sleep_with_cancel(0.5, cancel_callback)
    errors = last_snapshot.get("errors") or []
    detail = " | ".join(str(x) for x in errors[:3]) or str(last_snapshot.get("state") or "页面未变化")
    raise Exception(f"邮箱提交后未进入验证码页面: {detail}")


def fill_email_and_submit(timeout=45, log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    email, dev_token = get_email_and_token(log_callback=log_callback)
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    if log_callback:
        log_callback(f"[*] 已创建邮箱: {email}（源={get_email_provider()}）")
    deadline = time.time() + timeout
    last_diag_time = 0
    last_reclick_time = 0
    last_snapshot = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            r"""
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function describeInput(node) {
    return [
        `type=${node.getAttribute('type') || ''}`,
        `name=${node.getAttribute('name') || ''}`,
        `id=${node.getAttribute('id') || ''}`,
        `placeholder=${node.getAttribute('placeholder') || ''}`,
        `aria=${node.getAttribute('aria-label') || ''}`,
        `testid=${node.getAttribute('data-testid') || ''}`,
    ].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}
function describeAction(node) {
    return textOf(node).slice(0, 120);
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput)
    .slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map(describeAction)
    .filter(Boolean)
    .slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {
    return {
        state: 'not-ready',
        url: location.href,
        title: document.title,
        inputs: visibleInputs,
        buttons: visibleActions,
    };
}
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {
    return {
        state: 'fill-failed',
        value: input.value || '',
        valid: isValid,
        input: describeInput(input),
        url: location.href,
    };
}
input.blur();
return {
    state: 'filled',
    input: describeInput(input),
    url: location.href,
};
            """,
            email,
        )
        state = filled.get("state") if isinstance(filled, dict) else filled
        if isinstance(filled, dict):
            last_snapshot = filled
        if state == "not-ready":
            now = time.time()
            if now - last_reclick_time >= 3:
                reclicked = page.run_js(r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function nodeText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('href'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function scoreEntry(node) {
    const compact = nodeText(node).replace(/\s+/g, '');
    const lower = compact.toLowerCase();
    if (compact.includes('使用邮箱注册')) return 100;
    if (lower.includes('signupwithemail')) return 95;
    if (lower.includes('continuewithemail')) return 90;
    if (lower.includes('email') && (lower.includes('sign') || lower.includes('continue') || lower.includes('use') || lower.includes('with'))) return 80;
    if (lower === 'email' || lower.includes('邮箱')) return 70;
    return 0;
}
const candidates = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map((node) => ({ node, score: scoreEntry(node), text: nodeText(node) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
if (!candidates.length) return false;
candidates[0].node.click();
return candidates[0].text || true;
                """)
                last_reclick_time = now
                if reclicked and log_callback:
                    detail = f": {reclicked}" if isinstance(reclicked, str) else ""
                    log_callback(f"[Debug] 邮箱输入框未出现，已再次触发邮箱注册入口{detail}")
            if log_callback and now - last_diag_time >= 5:
                last_diag_time = now
                inputs = " | ".join((filled or {}).get("inputs", [])[:6]) if isinstance(filled, dict) else ""
                buttons = " | ".join((filled or {}).get("buttons", [])[:8]) if isinstance(filled, dict) else ""
                url = (filled or {}).get("url", page.url if page else "") if isinstance(filled, dict) else (page.url if page else "")
                log_callback(f"[Debug] 等待邮箱输入框: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if state != "filled":
            if log_callback:
                log_callback(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        sleep_with_cancel(0.8, cancel_callback)
        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' ||
        text.includes('注册') ||
        text.includes('继续') ||
        text.includes('下一步') ||
        text.includes('确认') ||
        lower.includes('signup') ||
        lower.includes('sign up') ||
        lower.includes('continue') ||
        lower.includes('next') ||
        lower.includes('createaccount') ||
        lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
            """
        )
        if clicked:
            if log_callback:
                detail = f" ({clicked})" if isinstance(clicked, str) else ""
                log_callback(f"[*] 已填写邮箱并提交: {email}{detail}")
            wait_for_email_submission_result(
                log_callback=log_callback,
                cancel_callback=cancel_callback,
            )
            return email, dev_token
        sleep_with_cancel(0.5, cancel_callback)
    if last_snapshot:
        inputs = " | ".join(last_snapshot.get("inputs", [])[:6])
        buttons = " | ".join(last_snapshot.get("buttons", [])[:8])
        url = last_snapshot.get("url", page.url if page else "")
        raise Exception(
            f"未找到邮箱输入框或注册按钮，最后页面: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}"
        )
    raise Exception("未找到邮箱输入框或注册按钮")


def fill_code_and_submit(email, dev_token, timeout=180, log_callback=None, cancel_callback=None):
    def _resend_code():
        page.run_js(
            r"""
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
            """
        )

    code = get_oai_code(
        dev_token,
        email,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
        resend_callback=_resend_code,
    )
    if not code:
        raise Exception("获取验证码失败")
    clean_code = str(code).replace("-", "").strip()
    deadline = time.time() + timeout

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            """
const code = String(arguments[0] || '').trim();
if (!code) return 'empty-code';

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function setInputValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp=\"true\"], input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[inputmode=\"text\"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);

if (aggregate) {
    aggregate.focus();
    aggregate.click();
    setInputValue(aggregate, code);
    return String(aggregate.value || '').replace(/\\s+/g, '') ? 'filled-aggregate' : 'aggregate-failed';
}

const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const ac = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || ac === 'one-time-code';
});

if (otpBoxes.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
        const ch = code[i] || '';
        const box = otpBoxes[i];
        box.focus();
        box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}

return 'not-ready';
            """,
            clean_code,
        )

        if filled == "not-ready":
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if "failed" in str(filled):
            if log_callback:
                log_callback(f"[Debug] 验证码填写失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const buttons = Array.from(document.querySelectorAll('button[type=\"submit\"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});

const btn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return (
        t.includes('确认邮箱') ||
        t.includes('继续') ||
        t.includes('下一步') ||
        t.includes('confirm') ||
        t.includes('continue') ||
        t.includes('next')
    );
});

if (!btn) return 'no-button';
btn.focus();
btn.click();
return 'clicked';
            """
        )

        if clicked == "clicked" or clicked == "no-button":
            if log_callback:
                log_callback(f"[*] 已填写验证码并提交: {code}")
            sleep_with_cancel(1.5, cancel_callback)
            return code

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("验证码已获取，但自动填写/提交失败")


def getTurnstileToken(log_callback=None, cancel_callback=None):
    global page
    if page is None:
        raise Exception("页面未就绪，无法执行 Turnstile")

    try:
        page.run_js(
            "try { if (window.turnstile && typeof turnstile.reset === 'function') turnstile.reset(); } catch(e) {}"
        )
    except Exception as exc:
        RUNTIME_LOGGER.debug("重置 Turnstile 失败: %s", exc, exc_info=True)

    for _ in range(0, 20):
        raise_if_cancelled(cancel_callback)
        try:
            token = page.run_js(
                """
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch(e) { return ''; }
                """
            )
            token = str(token or "").strip()
            if len(token) >= 80:
                if log_callback:
                    log_callback(f"[*] Turnstile 已通过，token长度={len(token)}")
                return token

            challenge_input = page.ele("@name=cf-turnstile-response")
            if challenge_input:
                wrapper = challenge_input.parent()
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe")
                except Exception:
                    iframe = None
                if iframe:
                    try:
                        iframe.run_js(
                            """
window.dtp = 1;
function getRandomInt(min, max) { return Math.floor(Math.random() * (max - min + 1)) + min; }
let sx = getRandomInt(800, 1200);
let sy = getRandomInt(400, 700);
Object.defineProperty(MouseEvent.prototype, 'screenX', { value: sx });
Object.defineProperty(MouseEvent.prototype, 'screenY', { value: sy });
                            """
                        )
                    except Exception as exc:
                        RUNTIME_LOGGER.debug("触发 Turnstile DOM 事件失败: %s", exc, exc_info=True)
                    try:
                        body_sr = iframe.ele("tag:body").shadow_root
                        btn = body_sr.ele("tag:input")
                        if btn:
                            btn.click()
                    except Exception as exc:
                        RUNTIME_LOGGER.debug("点击 Turnstile 按钮失败: %s", exc, exc_info=True)
            else:
                # 兜底：尝试触发页面上可见的 Turnstile 容器
                page.run_js(
                    """
const nodes = Array.from(document.querySelectorAll('div,span,iframe')).filter((n) => {
  const txt = (n.className || '') + ' ' + (n.id || '') + ' ' + (n.getAttribute?.('src') || '');
  return String(txt).toLowerCase().includes('turnstile');
});
if (nodes.length && typeof nodes[0].click === 'function') nodes[0].click();
                    """
                )
        except Exception as exc:
            RUNTIME_LOGGER.debug("读取 Turnstile 状态失败: %s", exc, exc_info=True)
        sleep_with_cancel(1, cancel_callback)

    raise Exception("Turnstile 获取 token 失败")


def build_profile():
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian",
    ]
    family_name_pool = [
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=120, log_callback=None, cancel_callback=None):
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    form_filled_once = False
    wait_cf_since = None
    last_cf_retry_at = 0.0

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if not form_filled_once:
            filled = page.run_js(
                """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[aria-label*="名"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[aria-label*="姓"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');

if (!givenInput || !familyInput || !passwordInput) return 'not-ready';

const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);

if (!ok1 || !ok2 || !ok3) return 'fill-failed';

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});

// 必须等待 Cloudflare 校验通过后再提交
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

if (submitBtn) {
    return 'ready-to-submit';
}
return 'filled-no-submit';
            """,
                given_name,
                family_name,
                password,
            )

            if isinstance(filled, str) and filled.startswith("wait-cloudflare"):
                form_filled_once = True
                if log_callback:
                    token_len = filled.split(":", 1)[1] if ":" in filled else "0"
                    log_callback(f"[*] 资料已填写，等待 Cloudflare 人机验证通过... 当前token长度={token_len}")
                if token_len == "0":
                    pause_seconds = random.uniform(1, 3)
                    if log_callback:
                        log_callback(f"[*] Cloudflare token 为空，暂停 {pause_seconds:.1f}s 后继续检测")
                    sleep_with_cancel(pause_seconds, cancel_callback)
                now = time.time()
                if wait_cf_since is None:
                    wait_cf_since = now
                # 卡住后自动二次复用 Turnstile 组件
                if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 8:
                    if log_callback:
                        log_callback("[*] Cloudflare 验证卡住，开始二次复用 Turnstile...")
                    try:
                        token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                        if token:
                            synced = page.run_js(
                                """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                """,
                                token,
                            )
                            if log_callback:
                                log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                    last_cf_retry_at = now
                sleep_with_cancel(0.8, cancel_callback)
                continue

            if filled in ("ready-to-submit", "filled-no-submit"):
                form_filled_once = True
            elif filled == "fill-failed" and log_callback:
                log_callback("[Debug] 资料输入失败，重试中...")
                sleep_with_cancel(0.5, cancel_callback)
                continue
            elif filled == "not-ready":
                sleep_with_cancel(0.5, cancel_callback)
                continue

        submit_state = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solvedByToken = token.length >= 80;
    if (!solvedByToken) return 'wait-cloudflare:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'no-submit-button:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'submitted';
            """
        )

        if isinstance(submit_state, str) and submit_state.startswith("wait-cloudflare"):
            if log_callback:
                token_len = submit_state.split(":", 1)[1] if ":" in submit_state else "0"
                log_callback(f"[*] 等待 Cloudflare 人机验证通过后再提交... 当前token长度={token_len}")
            now = time.time()
            if wait_cf_since is None:
                wait_cf_since = now
            if now - wait_cf_since >= 12 and now - last_cf_retry_at >= 8:
                if log_callback:
                    log_callback("[*] 提交前仍卡住，自动再次复用 Turnstile...")
                try:
                    token = getTurnstileToken(log_callback=log_callback, cancel_callback=cancel_callback)
                    if token:
                        synced = page.run_js(
                            """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                            """,
                            token,
                        )
                        if log_callback:
                            log_callback(f"[*] Turnstile 二次复用完成，回填长度={synced}")
                except Exception as cf_exc:
                    if log_callback:
                        log_callback(f"[Debug] Turnstile 二次复用失败: {cf_exc}")
                last_cf_retry_at = now
            sleep_with_cancel(0.8, cancel_callback)
            continue

        if submit_state == "submitted":
            if log_callback:
                log_callback(f"[*] 已填写注册资料并提交: {given_name} {family_name}")
            return {"given_name": given_name, "family_name": family_name, "password": password}
        wait_cf_since = None
        if isinstance(submit_state, str) and submit_state.startswith("no-submit-button") and log_callback:
            visible_buttons = submit_state.split(":", 1)[1] if ":" in submit_state else ""
            suffix = f" 可见按钮: {visible_buttons}" if visible_buttons else ""
            log_callback(f"[Debug] 未找到提交按钮，继续等待页面稳定...{suffix}")

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("最终注册页资料填写失败")


def _cookie_name_domain_value(item):
    if isinstance(item, dict):
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()
        domain = str(item.get("domain", "") or "").strip().lstrip(".")
    else:
        name = str(getattr(item, "name", "") or "").strip()
        value = str(getattr(item, "value", "") or "").strip()
        domain = str(getattr(item, "domain", "") or "").strip().lstrip(".")
    return name, domain, value


def _iter_cookie_sources():
    """优先 grok.com 标签页，再扫其它标签页与当前 page（对齐 grok-reg-tool）。"""
    tabs = []
    try:
        if browser is not None and hasattr(browser, "get_tabs"):
            for tab in browser.get_tabs() or []:
                try:
                    url = tab.url or ""
                except Exception:
                    url = ""
                tabs.append((url, tab))
    except Exception as exc:
        RUNTIME_LOGGER.debug("枚举浏览器 Cookie 来源失败: %s", exc, exc_info=True)

    # grok.com 标签优先
    ordered = []
    for url, tab in tabs:
        if "grok.com" in (url or ""):
            ordered.append(tab)
    for url, tab in tabs:
        if "grok.com" not in (url or ""):
            ordered.append(tab)
    if page is not None and page not in ordered:
        ordered.append(page)

    seen = set()
    for tab in ordered:
        try:
            key = id(tab)
            if key in seen:
                continue
            seen.add(key)
            cookies = tab.cookies(all_domains=True, all_info=True) or []
            yield tab, cookies
        except Exception:
            continue


def wait_for_grok_com_landing(timeout=90, log_callback=None, cancel_callback=None):
    """
    注册完成后等浏览器经 SSO 重定向落到 grok.com 登录态。
    grok.com 与 accounts.x.ai 不共享 cookie；过早取 accounts 域 sso 质量差。
    """
    global page
    deadline = time.time() + timeout
    last_url = ""
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            refresh_active_page()
            # 优先切到已打开的 grok.com 标签
            try:
                if browser is not None and hasattr(browser, "get_tabs"):
                    for tab in browser.get_tabs() or []:
                        try:
                            url = tab.url or ""
                        except Exception:
                            url = ""
                        if "grok.com" in url:
                            page = tab
                            break
            except Exception as exc:
                RUNTIME_LOGGER.debug("检查 grok.com 标签页失败: %s", exc, exc_info=True)

            if page is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            current_url = ""
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            if current_url != last_url:
                if log_callback:
                    log_callback(f"[*] 等待重定向到 grok.com，当前: {current_url}")
                last_url = current_url

            if "grok.com" in current_url:
                logged_in = False
                try:
                    logged_in = bool(
                        page.run_js(
                            r"""
function isVisible(n) {
  if (!n) return false;
  const s = window.getComputedStyle(n);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = n.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
if (/grok\.com\/(chat|c)\//.test(location.href)) return true;
const ta = Array.from(document.querySelectorAll('textarea, [contenteditable="true"]'))
  .find(n => isVisible(n) && !n.disabled && !n.readOnly);
if (ta) return true;
// 有 grok.com 域 sso cookie 也视为基本登录
return document.cookie.split(';').some(c => c.trim().startsWith('sso='));
"""
                        )
                    )
                except Exception:
                    logged_in = "grok.com" in current_url
                if logged_in:
                    if log_callback:
                        log_callback(f"[*] 已落到 grok.com 并登录: {current_url}")
                    return True
        except PageDisconnectedError:
            refresh_active_page()
        except RegistrationCancelled:
            raise
        except Exception as exc:
            RUNTIME_LOGGER.debug("等待 grok.com 落地页状态失败: %s", exc, exc_info=True)
        sleep_with_cancel(1, cancel_callback)

    if log_callback:
        log_callback(f"[Warn] 等待 grok.com 登录超时，最后 URL: {last_url}")
    return False


def dismiss_cookie_and_consent_banners(log_callback=None):
    """Click OneTrust / cookie consent / continue buttons that block SSO landing."""
    global page
    if page is None:
        return ""
    try:
        result = page.run_js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
  return [
    node.innerText,
    node.textContent,
    node.getAttribute('value'),
    node.getAttribute('aria-label'),
    node.getAttribute('title'),
  ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
// Prefer known OneTrust / cookie SDK ids
const idCandidates = [
  'onetrust-accept-btn-handler',
  'accept-recommended-btn-handler',
  'onetrust-reject-all-handler',
];
for (const id of idCandidates) {
  const el = document.getElementById(id);
  if (el && isVisible(el) && id.includes('accept')) {
    el.click();
    return 'clicked-id:' + id;
  }
}
const needles = [
  '接受所有 cookie',
  '接受所有cookie',
  '接受全部 cookie',
  '接受全部cookie',
  '全部接受',
  '接受全部',
  '同意全部',
  '同意并继续',
  'allow all',
  'accept all',
  'accept all cookies',
  'accept cookies',
  'i agree',
  'got it',
  '继续',
  'continue',
];
const nodes = Array.from(document.querySelectorAll(
  'button, [role="button"], input[type="button"], input[type="submit"], a'
)).filter(isVisible);
for (const node of nodes) {
  const t = textOf(node).toLowerCase().replace(/\s+/g, '');
  if (!t) continue;
  for (const n of needles) {
    const nn = n.toLowerCase().replace(/\s+/g, '');
    if (t.includes(nn)) {
      // avoid rejecting all when accept is available
      if (nn.includes('拒绝') || nn.includes('reject') || nn.includes('deny')) continue;
      node.click();
      return 'clicked-text:' + textOf(node).slice(0, 40);
    }
  }
}
// Chinese exact-ish: 接受所有 Cookie (mixed case / spacing)
for (const node of nodes) {
  const raw = textOf(node);
  if (/接受\s*所有\s*Cookie/i.test(raw) || /Accept\s*All/i.test(raw)) {
    node.click();
    return 'clicked-regex:' + raw.slice(0, 40);
  }
}
return '';
"""
        )
        if result and log_callback:
            log_callback(f"[*] 已处理同意/Cookie 弹窗: {result}")
        return result or ""
    except Exception as e:
        if log_callback:
            log_callback(f"[Debug] 处理 Cookie 弹窗失败: {e}")
        return ""


def wait_for_sso_cookie(
    timeout=120,
    log_callback=None,
    cancel_callback=None,
    prefer_domain="grok.com",
):
    """
    取网页 SSO cookie。
    优先 grok.com 域（grok2api/chat 同域）；accounts.x.ai 仅作回退。
    对齐 FengZi1221/grok-reg-tool 策略。
    """
    deadline = time.time() + timeout
    last_seen_names = set()
    last_submit_retry = 0.0
    last_cf_retry_at = 0.0
    last_consent_retry = 0.0
    final_no_submit_state = ""
    final_no_submit_since = None
    final_no_submit_timeout = 25
    fallback_sso = ""
    fallback_domain = ""
    grok_wait_done = False

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            refresh_active_page()
            if page is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            now = time.time()
            # Cookie / “您正在登录” 同意墙：不点就永远没有 sso
            if now - last_consent_retry >= 1.5:
                dismiss_cookie_and_consent_banners(log_callback=log_callback)
                last_consent_retry = now

            # 仍停留在“完成注册”页时，若 Cloudflare 已通过，周期性重试点击提交
            if now - last_submit_retry >= 2.5:
                retried = page.run_js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span')).find((el) => {
    const t = (el.textContent || '').replace(/\s+/g, '');
    const lower = t.toLowerCase();
    return t.includes('完成注册') || lower.includes('completeyoursignup') || lower.includes('completesignup');
});
if (!titleHit) return 'not-final-page';

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const cfPresent = !!cfInput
  || !!document.querySelector('iframe[src*="turnstile"], div.cf-turnstile, [data-sitekey], script[src*="turnstile"]');
if (cfPresent) {
    const token = String((cfInput && cfInput.value) || '').trim();
    const solved = token.length >= 80;
    if (!solved) return 'final-page-wait-cf:' + token.length;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    return t.includes('完成注册') || t.includes('创建账户') || t.includes('signup') || t.includes('createaccount');
});
if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'final-page-no-submit:' + visibleTexts;
}
submitBtn.focus();
submitBtn.click();
return 'final-page-clicked-submit';
                    """
                )
                last_submit_retry = now
                if log_callback and (
                    retried == "final-page-clicked-submit"
                    or (isinstance(retried, str) and retried.startswith("final-page-no-submit"))
                ):
                    log_callback(f"[Debug] 最终页状态: {retried}")
                if isinstance(retried, str) and retried.startswith("final-page-no-submit"):
                    if retried != final_no_submit_state:
                        final_no_submit_state = retried
                        final_no_submit_since = now
                    elif final_no_submit_since and now - final_no_submit_since >= final_no_submit_timeout:
                        raise AccountRetryNeeded(
                            f"最终注册页状态 {final_no_submit_timeout}s 未变化且未找到提交按钮，重试当前账号: {retried}"
                        )
                else:
                    final_no_submit_state = ""
                    final_no_submit_since = None
                if log_callback and isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    token_len = retried.split(":", 1)[1] if ":" in retried else "0"
                    log_callback(f"[Debug] 最终页状态: final-page-wait-cf, token长度={token_len}")
                    if now - last_cf_retry_at >= 10:
                        if log_callback:
                            log_callback("[*] 最终页 Cloudflare 卡住，自动二次复用 Turnstile...")
                        try:
                            token = getTurnstileToken(
                                log_callback=log_callback, cancel_callback=cancel_callback
                            )
                            if token:
                                synced = page.run_js(
                                    """
const token = String(arguments[0] || '').trim();
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
if (!cfInput || !token) return false;
const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
if (nativeSetter) nativeSetter.call(cfInput, token);
else cfInput.value = token;
cfInput.dispatchEvent(new Event('input', { bubbles: true }));
cfInput.dispatchEvent(new Event('change', { bubbles: true }));
return String(cfInput.value || '').trim().length;
                                    """,
                                    token,
                                )
                                if log_callback:
                                    log_callback(
                                        f"[*] 最终页 Turnstile 二次复用完成，回填长度={synced}"
                                    )
                        except Exception as cf_exc:
                            if log_callback:
                                log_callback(f"[Debug] 最终页 Turnstile 二次复用失败: {cf_exc}")
                        last_cf_retry_at = now

            # 离开注册页后，只尝试一次等待落到 grok.com 登录态
            try:
                cur = (page.url or "") if page is not None else ""
            except Exception:
                cur = ""
            left_signup = bool(
                cur
                and ("sign-up" not in cur)
                and not cur.rstrip("/").endswith("accounts.x.ai")
            )
            if (not grok_wait_done) and (left_signup or "grok.com" in cur):
                remain = max(8, min(70, int(deadline - time.time()) - 5))
                if remain > 0:
                    wait_for_grok_com_landing(
                        timeout=remain,
                        log_callback=log_callback,
                        cancel_callback=cancel_callback,
                    )
                grok_wait_done = True

            preferred_hit = None
            for _tab, cookies in _iter_cookie_sources():
                for item in cookies:
                    name, domain, value = _cookie_name_domain_value(item)
                    if name:
                        last_seen_names.add(f"{name}@{domain}" if domain else name)
                    if name != "sso" or not value:
                        continue
                    # 优先 grok.com
                    if prefer_domain and prefer_domain in domain:
                        preferred_hit = (domain, value)
                        break
                    # 次优：非 accounts.x.ai 的 sso
                    if not fallback_sso:
                        fallback_sso = value
                        fallback_domain = domain or "?"
                    elif "accounts.x.ai" in (fallback_domain or "") and "accounts.x.ai" not in domain:
                        fallback_sso = value
                        fallback_domain = domain or "?"
                    # accounts.x.ai 仅记录，不优先
                    if "accounts.x.ai" in domain and not fallback_sso:
                        fallback_sso = value
                        fallback_domain = domain
                if preferred_hit:
                    break

            if preferred_hit:
                domain, value = preferred_hit
                if log_callback:
                    log_callback(f"[*] 已获取到 {domain} 域的 sso cookie（优先 grok.com）")
                return value

        except PageDisconnectedError:
            refresh_active_page()
        except AccountRetryNeeded:
            raise
        except RegistrationCancelled:
            raise
        except Exception as exc:
            RUNTIME_LOGGER.debug("读取 SSO Cookie 轮询状态失败: %s", exc, exc_info=True)

        sleep_with_cancel(1, cancel_callback)

    # 超时前再扫一轮；允许用非 prefer 域回退
    try:
        for _tab, cookies in _iter_cookie_sources():
            for item in cookies:
                name, domain, value = _cookie_name_domain_value(item)
                if name == "sso" and value:
                    if prefer_domain and prefer_domain in domain:
                        if log_callback:
                            log_callback(f"[*] 已获取到 {domain} 域的 sso cookie")
                        return value
                    if not fallback_sso:
                        fallback_sso = value
                        fallback_domain = domain or "?"
    except Exception as exc:
        RUNTIME_LOGGER.debug("读取 SSO Cookie 备用来源失败: %s", exc, exc_info=True)

    if fallback_sso:
        if log_callback:
            log_callback(
                f"[Warn] 未拿到 {prefer_domain} 域 sso，回退使用 {fallback_domain} 域（质量可能较差）"
            )
        return fallback_sso

    raise Exception(
        f"等待超时：未获取到 sso cookie。已看到 cookies: {sorted(last_seen_names)}"
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()

    def setup_ui(self):
        load_config()
        main_frame = tk.Frame(self.root, bg=UI_BG, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(3, weight=1)

        config_frame = tk.LabelFrame(
            main_frame,
            text="配置",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=10,
            pady=10,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        config_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        config_frame.grid_columnconfigure(1, weight=1, minsize=260)
        config_frame.grid_columnconfigure(3, weight=1, minsize=260)

        def add_label(row, column, text):
            tk_label(config_frame, text=text, bg=UI_PANEL_BG).grid(
                row=row,
                column=column,
                sticky=tk.W,
                padx=(0, 6),
                pady=3,
            )

        def add_field(widget, row, column, columnspan=1, sticky=tk.EW):
            widget.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky=sticky,
                padx=(0, 14),
                pady=3,
            )

        add_label(0, 0, "邮箱服务商:")
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = tk_option_menu(
            config_frame,
            self.email_provider_var,
            ["tempmailer", "duckmail", "yyds", "cloudflare"],
            width=12,
        )
        add_field(self.email_provider_combo, 0, 1, sticky=tk.W)

        add_label(0, 2, "注册数量:")
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=2500,
            width=8,
            textvariable=self.count_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.count_spinbox, 0, 3, sticky=tk.W)

        add_label(1, 0, "注册选项:")
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.nsfw_check = tk_checkbutton(config_frame, text="注册后开启 NSFW", variable=self.nsfw_var)
        add_field(self.nsfw_check, 1, 1, sticky=tk.W)

        add_label(1, 2, "代理（可选）:")
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = tk_entry(config_frame, textvariable=self.proxy_var, width=34)
        add_field(self.proxy_entry, 1, 3)

        add_label(2, 0, "DuckMail API Key:")
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = tk_entry(config_frame, textvariable=self.api_key_var, width=34)
        add_field(self.api_key_entry, 2, 1)

        add_label(2, 2, "Cloudflare 鉴权模式:")
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "none"))
        self.cloudflare_auth_mode_combo = tk_option_menu(
            config_frame, self.cloudflare_auth_mode_var, ["query-key", "bearer", "x-api-key", "x-admin-auth", "none"], width=12
        )
        add_field(self.cloudflare_auth_mode_combo, 2, 3, sticky=tk.W)

        add_label(3, 0, "Cloudflare API Base:")
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_base_var, width=72)
        add_field(self.cloudflare_api_base_entry, 3, 1, columnspan=3)

        add_label(4, 0, "Cloudflare API Key:")
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_key_var, width=34)
        add_field(self.cloudflare_api_key_entry, 4, 1)

        add_label(4, 2, "CF 路径:")
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/api/domains"),
                    config.get("cloudflare_path_accounts", "/api/new_address"),
                    config.get("cloudflare_path_token", "/api/token"),
                    config.get("cloudflare_path_messages", "/api/mails"),
                ]
            )
        )
        self.cloudflare_paths_entry = tk_entry(config_frame, textvariable=self.cloudflare_paths_var, width=34)
        add_field(self.cloudflare_paths_entry, 4, 3)

        add_label(5, 0, "grok2api 本地入池:")
        self.grok2api_local_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_local", True)))
        self.grok2api_local_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_local_auto_var)
        add_field(self.grok2api_local_auto_check, 5, 1, sticky=tk.W)

        add_label(5, 2, "grok2api 池名:")
        self.grok2api_pool_name_var = tk.StringVar(value=str(config.get("grok2api_pool_name", "ssoBasic")))
        self.grok2api_pool_name_combo = tk_option_menu(
            config_frame, self.grok2api_pool_name_var, ["ssoBasic", "ssoSuper"], width=12
        )
        add_field(self.grok2api_pool_name_combo, 5, 3, sticky=tk.W)

        add_label(6, 0, "本地 token.json:")
        self.grok2api_local_file_var = tk.StringVar(value=str(config.get("grok2api_local_token_file", "")))
        self.grok2api_local_file_entry = tk_entry(config_frame, textvariable=self.grok2api_local_file_var, width=72)
        add_field(self.grok2api_local_file_entry, 6, 1, columnspan=3)

        add_label(7, 0, "grok2api 远端入池:")
        self.grok2api_remote_auto_var = tk.BooleanVar(value=bool(config.get("grok2api_auto_add_remote", False)))
        self.grok2api_remote_auto_check = tk_checkbutton(config_frame, variable=self.grok2api_remote_auto_var)
        add_field(self.grok2api_remote_auto_check, 7, 1, sticky=tk.W)

        add_label(8, 0, "grok2api 远端 Base:")
        self.grok2api_remote_base_var = tk.StringVar(value=str(config.get("grok2api_remote_base", "")))
        self.grok2api_remote_base_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_base_var, width=72)
        add_field(self.grok2api_remote_base_entry, 8, 1, columnspan=3)

        add_label(9, 0, "grok2api 远端 app_key:")
        self.grok2api_remote_key_var = tk.StringVar(value=str(config.get("grok2api_remote_app_key", "")))
        self.grok2api_remote_key_entry = tk_entry(config_frame, textvariable=self.grok2api_remote_key_var, width=72)
        add_field(self.grok2api_remote_key_entry, 9, 1, columnspan=3)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
        btn_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.start_btn = tk_button(btn_frame, text="开始注册", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk_button(btn_frame, text="停止", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk_button(btn_frame, text="清空日志", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(main_frame, bg=UI_BG)
        status_frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tk_label(status_frame, text="状态: ").pack(side=tk.LEFT)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, bg=UI_BG, fg="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")
        tk.Label(status_frame, textvariable=self.stats_var, bg=UI_BG, fg=UI_FG).pack(side=tk.RIGHT)
        log_frame = tk.LabelFrame(
            main_frame,
            text="日志",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=5,
            pady=5,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        log_frame.grid(row=3, column=0, sticky=tk.NSEW)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=18,
            width=60,
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            selectbackground="#345a8a",
            selectforeground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log("[*] GUI 已就绪，配置已加载")
        self.log(f"[*] 当前邮箱服务商: {self.email_provider_var.get()} | 注册数量: {self.count_var.get()}")

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.log_text.insert(tk.END, f"{line}\n")
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")

    def should_stop(self):
        return self.stop_requested or not self.is_running

    def start_registration(self):
        if self.is_running:
            self.log("[!] 当前已有任务在运行")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get())
        config["proxy"] = self.proxy_var.get().strip()
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "none"
        config["grok2api_auto_add_local"] = bool(self.grok2api_local_auto_var.get())
        config["grok2api_local_token_file"] = self.grok2api_local_file_var.get().strip()
        config["grok2api_pool_name"] = self.grok2api_pool_name_var.get().strip() or "ssoBasic"
        config["grok2api_auto_add_remote"] = bool(self.grok2api_remote_auto_var.get())
        config["grok2api_remote_base"] = self.grok2api_remote_base_var.get().strip()
        config["grok2api_remote_app_key"] = self.grok2api_remote_key_var.get().strip()
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        save_config()
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        config["register_count"] = count
        save_config()
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.accounts_output_file = new_accounts_output_file()
        self.update_stats()
        self._set_running_ui(True)
        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")
        self.log(f"[*] 成功账号将实时保存到: {self.accounts_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count,),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] 用户停止注册")

    def run_registration(self, count):
        def sync_stats(summary):
            self.success_count = summary.success
            self.fail_count = summary.fail
            self.update_stats()

        def record_result(record):
            self.results.append(record)

        runner = RegistrationRunner(
            build_registration_actions(),
            log_callback=self.log,
            stop_callback=self.should_stop,
            stats_callback=sync_stats,
            result_callback=record_result,
            memory_cleanup_interval=MEMORY_CLEANUP_INTERVAL,
        )
        try:
            runner.run(count, self.accounts_output_file)
        except Exception as exc:
            self.log(f"[!] 任务异常: {exc}")
        finally:
            self._set_running_ui(False)
            self.log("[*] 任务结束")


class CliStopController:
    def __init__(self):
        self.stop_requested = False

    def should_stop(self):
        return self.stop_requested

    def stop(self):
        self.stop_requested = True


def cli_log(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def build_registration_actions():
    return RegistrationActions(
        start_browser=start_browser,
        stop_browser=stop_browser,
        restart_browser=restart_browser,
        browser_available=lambda: browser is not None,
        get_email_provider=get_email_provider,
        open_signup_page=open_signup_page,
        fill_email_and_submit=fill_email_and_submit,
        fill_code_and_submit=fill_code_and_submit,
        fill_profile_and_submit=fill_profile_and_submit,
        wait_for_sso_cookie=wait_for_sso_cookie,
        enable_nsfw_for_token=enable_nsfw_for_token,
        add_token_to_pools=add_token_to_grok2api_pools,
        rotate_email_provider=rotate_email_provider,
        is_mail_related_error=is_mail_related_error,
        sleep_with_cancel=sleep_with_cancel,
        cleanup_runtime_memory=cleanup_runtime_memory,
        email_failover_enabled=lambda: bool(config.get("email_failover", True)),
        nsfw_enabled=lambda: bool(config.get("enable_nsfw", True)),
        cancelled_error=RegistrationCancelled,
        retry_error=AccountRetryNeeded,
    )


def run_registration_cli(count):
    controller = CliStopController()
    accounts_output_file = new_accounts_output_file()
    cli_log(f"[*] 终端模式启动，目标数量: {count}")
    cli_log(f"[*] 成功账号将实时保存到: {accounts_output_file}")
    summary = RegistrationSummary()

    def report_stats(current):
        nonlocal summary
        summary = current
        cli_log(f"[*] 当前统计: 成功 {current.success} | 失败 {current.fail}")

    runner = RegistrationRunner(
        build_registration_actions(),
        log_callback=cli_log,
        stop_callback=controller.should_stop,
        stats_callback=report_stats,
        memory_cleanup_interval=MEMORY_CLEANUP_INTERVAL,
    )
    try:
        summary = runner.run(count, accounts_output_file)
    except KeyboardInterrupt:
        controller.stop()
        cli_log("[!] 收到 Ctrl+C，正在停止并清理")
    except Exception as exc:
        cli_log(f"[!] 任务异常: {exc}")
    finally:
        cleanup_runtime_memory(log_callback=cli_log, reason="任务结束")
        cli_log(f"[*] 任务结束。成功 {summary.success} | 失败 {summary.fail}")


def main_cli():
    load_config()
    count = int(config.get("register_count", 1) or 1)
    cli_log("[*] CLI 已加载配置")
    cli_log(f"[*] 当前邮箱服务商: {config.get('email_provider', 'duckmail')} | 注册数量: {count}")
    cli_log("[*] 输入 start 后开始；按 Ctrl+C 可强制停止")
    try:
        command = input("> ").strip().lower()
    except KeyboardInterrupt:
        cli_log("[!] 已取消")
        return
    if command != "start":
        cli_log("[!] 未输入 start，已退出")
        return
    run_registration_cli(count)


def main():
    if len(sys.argv) > 1 and sys.argv[1].strip().lower() in ("start", "cli", "--cli"):
        main_cli()
        return
    root = tk.Tk()
    setup_light_theme(root)
    app = GrokRegisterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
