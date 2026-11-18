#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser proxy parsing, authenticated-proxy bridging, and option wiring."""

from __future__ import annotations

import base64
import select
import socket
import socketserver
import ssl
import threading
import urllib.parse
from typing import Callable, Optional


def parse_proxy_url(proxy):
    raw = str(proxy or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        return urllib.parse.urlsplit(raw)
    except ValueError:
        return None


def safe_proxy_port(parsed):
    try:
        return parsed.port
    except ValueError:
        return None


def proxy_has_auth(proxy):
    parsed = parse_proxy_url(proxy)
    return bool(parsed and parsed.hostname and (parsed.username is not None or parsed.password is not None))


def strip_proxy_auth(proxy):
    raw = str(proxy or "").strip()
    parsed = parse_proxy_url(raw)
    if not parsed or not parsed.hostname:
        return raw
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = safe_proxy_port(parsed)
    netloc = f"{host}:{port}" if port else host
    stripped = urllib.parse.urlunsplit(
        (parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment)
    )
    return stripped if "://" in raw else stripped.split("://", 1)[1]


def proxy_endpoint_terms(proxy):
    parsed = parse_proxy_url(proxy)
    if not parsed or not parsed.hostname:
        return []
    terms = [parsed.hostname]
    port = safe_proxy_port(parsed)
    if port:
        terms.extend((f"{parsed.hostname}:{port}", f"port {port}"))
    return [term.lower() for term in terms if term]


def is_proxy_connection_error(exc, proxy):
    if not proxy:
        return False
    error = str(exc or "").lower()
    if not error:
        return False
    if any(marker in error for marker in ("proxy", "tunnel", "socks")):
        return True
    markers = (
        "could not connect",
        "failed to connect",
        "connection refused",
        "connection reset",
        "connect error",
        "timed out",
        "timeout",
    )
    terms = proxy_endpoint_terms(proxy)
    return any(marker in error for marker in markers) and (
        not terms or any(term in error for term in terms)
    )


def page_has_proxy_error(page_obj):
    try:
        url = str(getattr(page_obj, "url", "") or "")
        title = str(page_obj.run_js("return document.title || ''") or "")
        body = str(
            page_obj.run_js(
                "return document.body ? document.body.innerText.slice(0, 2000) : ''"
            )
            or ""
        )
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False
    text = f"{url}\n{title}\n{body}".lower()
    return any(
        marker in text
        for marker in (
            "err_proxy",
            "proxy connection failed",
            "proxy server",
            "proxy authentication",
            "tunnel connection failed",
            "无法连接到代理服务器",
            "代理服务器",
        )
    )


class _ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _recv_until_headers(sock, timeout=20, limit=65536):
    sock.settimeout(timeout)
    data = b""
    while b"\r\n\r\n" not in data and len(data) < limit:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data


def _relay(left, right, timeout=60):
    left.settimeout(timeout)
    right.settimeout(timeout)
    sockets = [left, right]
    while True:
        readable, _, _ = select.select(sockets, [], [], timeout)
        if not readable:
            return
        for current in readable:
            data = current.recv(65536)
            if not data:
                return
            (right if current is left else left).sendall(data)


class _LocalAuthProxyBridgeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        bridge = self.server.bridge
        upstream = None
        try:
            initial = _recv_until_headers(self.request, timeout=bridge.timeout)
            if not initial:
                return
            first_line = initial.split(b"\r\n", 1)[0].decode("latin1", "ignore")
            upstream = bridge.open_upstream()
            if first_line.upper().startswith("CONNECT "):
                target = first_line.split()[1]
                request_lines = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
                if bridge.auth_header:
                    request_lines.append(f"Proxy-Authorization: Basic {bridge.auth_header}")
                upstream.sendall(("\r\n".join(request_lines) + "\r\n\r\n").encode("latin1"))
                response = _recv_until_headers(upstream, timeout=bridge.timeout)
                if response:
                    self.request.sendall(response)
                if b" 200 " not in response.split(b"\r\n", 1)[0]:
                    return
            else:
                upstream.sendall(bridge.inject_proxy_auth(initial))
            _relay(self.request, upstream, timeout=bridge.relay_timeout)
        except (OSError, ssl.SSLError, ValueError):
            return
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except OSError:
                    pass


class LocalAuthProxyBridge:
    def __init__(self, proxy_url):
        parsed = parse_proxy_url(proxy_url)
        if not parsed or not parsed.hostname:
            raise ValueError("认证代理地址格式无效")
        if (parsed.scheme or "http").lower() not in ("http", "https"):
            raise ValueError("Chromium 本地认证代理桥仅支持 http/https 上游代理")
        self.upstream_scheme = (parsed.scheme or "http").lower()
        self.upstream_host = parsed.hostname
        self.upstream_port = safe_proxy_port(parsed) or (
            443 if self.upstream_scheme == "https" else 80
        )
        username = urllib.parse.unquote(parsed.username or "")
        password = urllib.parse.unquote(parsed.password or "")
        raw_auth = f"{username}:{password}".encode("utf-8")
        self.auth_header = base64.b64encode(raw_auth).decode("ascii") if (username or password) else ""
        self.timeout = 20
        self.relay_timeout = 90
        self.server = None
        self.thread = None
        self.local_proxy = ""

    def open_upstream(self):
        connection = socket.create_connection(
            (self.upstream_host, self.upstream_port), timeout=self.timeout
        )
        if self.upstream_scheme == "https":
            context = ssl.create_default_context()
            connection = context.wrap_socket(connection, server_hostname=self.upstream_host)
        connection.settimeout(self.timeout)
        return connection

    def inject_proxy_auth(self, data):
        if not self.auth_header or b"\r\n\r\n" not in data:
            return data
        if b"\r\nproxy-authorization:" in data.lower():
            return data
        head, body = data.split(b"\r\n\r\n", 1)
        auth_line = f"Proxy-Authorization: Basic {self.auth_header}".encode("latin1")
        return head + b"\r\n" + auth_line + b"\r\n\r\n" + body

    def start(self):
        self.server = _ReusableThreadingTCPServer(
            ("127.0.0.1", 0), _LocalAuthProxyBridgeHandler
        )
        self.server.bridge = self
        self.local_proxy = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self.local_proxy

    def stop(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        self.server = None
        self.thread = None
        self.local_proxy = ""


def prepare_browser_proxy(proxy, use_proxy=True, log_callback: Optional[Callable[[str], None]] = None):
    if not use_proxy or not proxy:
        return "", None
    if proxy_has_auth(proxy):
        parsed = parse_proxy_url(proxy)
        scheme = (parsed.scheme or "http").lower() if parsed else ""
        if scheme in ("http", "https"):
            bridge = LocalAuthProxyBridge(proxy)
            browser_proxy = bridge.start()
            if log_callback:
                log_callback(f"[*] 已为 Chromium 启动本地认证代理桥: {browser_proxy}")
            return browser_proxy, bridge
        stripped = strip_proxy_auth(proxy)
        if log_callback:
            log_callback("[!] Chromium 不直接支持该认证代理协议，使用去认证代理地址")
        return stripped, None
    return proxy, None


def apply_browser_proxy_option(options, proxy):
    if not proxy:
        return
    if hasattr(options, "set_proxy"):
        try:
            options.set_proxy(proxy)
            return
        except (AttributeError, TypeError, ValueError):
            pass
    if not hasattr(options, "set_argument"):
        raise AttributeError("当前 DrissionPage ChromiumOptions 不支持设置浏览器代理")
    try:
        options.set_argument(f"--proxy-server={proxy}")
    except TypeError:
        options.set_argument("--proxy-server", proxy)
