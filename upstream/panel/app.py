#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Grok Register 账号面板 + 启动注册（代理/节点由本机 Clash 管理）"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

# Project root = parent of panel/ (Windows / portable layout)
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent
BASE_DIR = Path(os.environ.get("GROK_REGISTER_DIR", str(_DEFAULT_ROOT))).resolve()
# 面板默认不设登录密码（本机 127.0.0.1）。若需开启：PANEL_AUTH=1 且 PANEL_PASSWORD=xxx
PANEL_AUTH = os.environ.get("PANEL_AUTH", "0").strip() not in ("0", "false", "False", "no", "")
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "admin")
HOST = os.environ.get("PANEL_HOST", "127.0.0.1")
PORT = int(os.environ.get("PANEL_PORT", "8787"))


def load_panel_secret(base_dir: Optional[Path] = None) -> str:
    configured = os.environ.get("PANEL_SECRET", "").strip()
    if configured:
        return configured
    root = Path(base_dir or BASE_DIR)
    secret_path = root / "data" / ".panel_secret"
    try:
        current = secret_path.read_text(encoding="utf-8").strip()
        if len(current) >= 32:
            return current
    except FileNotFoundError:
        pass
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    secret_path.write_text(generated + "\n", encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return generated


SECRET = load_panel_secret()
CLASH_API = os.environ.get("CLASH_API", "http://127.0.0.1:9090").rstrip("/")
CLASH_SECRET = os.environ.get("CLASH_SECRET", "")
# Prefer project venv; Windows uses Scripts\python.exe
_VENV_WIN = BASE_DIR / ".venv" / "Scripts" / "python.exe"
_VENV_UNIX = BASE_DIR / ".venv" / "bin" / "python"
_DEFAULT_PY = (
    str(_VENV_WIN)
    if _VENV_WIN.exists()
    else (str(_VENV_UNIX) if _VENV_UNIX.exists() else sys.executable)
)
VENV_PYTHON = os.environ.get("GROK_PYTHON", _DEFAULT_PY)
MAIN_SCRIPT = BASE_DIR / "grok_register_ttk.py"
CONFIG_PATH = BASE_DIR / "config.json"
PROXY_URL = os.environ.get("GROK_PROXY", "http://127.0.0.1:7897")
LOG_DIR = Path(os.environ.get("PANEL_LOG_DIR", str(BASE_DIR / "data" / "logs"))).resolve()
LOG_DIR.mkdir(parents=True, exist_ok=True)

# SSO → real CPA (CLIProxyAPI OAuth JSON)
CPA_DIR = Path(os.environ.get("CPA_DIR", str(BASE_DIR / "data" / "cpa"))).resolve()
CPA_DIR.mkdir(parents=True, exist_ok=True)
CPA_FAILED_PATH = CPA_DIR / "failed.jsonl"
SSO2CPA_PATH = Path(
    os.environ.get("SSO2CPA_PATH", str(BASE_DIR / "lib"))
).resolve()
AUTO_CPA = os.environ.get("AUTO_CPA", "1").strip() not in ("0", "false", "False", "no")
CPA_DELAY = float(os.environ.get("CPA_DELAY", "1.0"))
CPA_BATCH_WAIT_TIMEOUT = float(os.environ.get("CPA_BATCH_WAIT_TIMEOUT", "900"))
# Optional: talk to local Clash Meta external-controller for node list.
# Default: external Clash managed by user; node UI is best-effort.
ENABLE_CLASH_UI = os.environ.get("ENABLE_CLASH_UI", "1").strip() not in (
    "0",
    "false",
    "False",
    "no",
)

# import shared convert core
for _p in (str(SSO2CPA_PATH), str(BASE_DIR / "lib"), str(Path(__file__).resolve().parent)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)
try:
    from sso2cpa_core import (  # type: ignore
        convert_one,
        normalize_sso,
        safe_filename as cpa_safe_filename,
        sso_fingerprint,
    )

    _CPA_CORE_OK = True
    _CPA_CORE_ERR = ""
except Exception as _e:  # pragma: no cover
    convert_one = None  # type: ignore
    normalize_sso = lambda t: (t or "").strip()  # type: ignore
    cpa_safe_filename = lambda s: re.sub(r"[^\w.@+-]+", "_", s or "unknown")[:100]  # type: ignore
    sso_fingerprint = lambda s: hashlib.sha256((s or "").encode()).hexdigest()  # type: ignore
    _CPA_CORE_OK = False
    _CPA_CORE_ERR = str(_e)

from config_store import JsonConfigStore  # type: ignore
from process_control import terminate_process_tree  # type: ignore

try:
    from panel.account_store import AccountStore
    from panel.cpa_service import CpaConversionService
    from panel.email_settings import EmailSettings
    from panel.job_manager import RegistrationJobManager
    from panel.panel_log import PanelLog
except ImportError:  # Direct execution: python panel/app.py
    from account_store import AccountStore
    from cpa_service import CpaConversionService
    from email_settings import EmailSettings
    from job_manager import RegistrationJobManager
    from panel_log import PanelLog

HK_RE = re.compile(r"(香港|Hong\s*Kong|\bHK\b|🇭🇰)", re.I)

app = Flask(__name__)
app.secret_key = SECRET
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)

_panel_log = PanelLog(max_lines=2000)
log_line = _panel_log.write


def require_login():
    """默认关闭鉴权；仅当 PANEL_AUTH=1 时校验 session。"""
    if not PANEL_AUTH:
        return None
    if session.get("ok"):
        return None
    # API requests get JSON 401
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return redirect(url_for("login", next=request.path))


def csrf_token() -> str:
    token = str(session.get("csrf_token") or "")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.before_request
def protect_mutating_requests():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.endpoint == "login_post":
        return None
    expected = str(session.get("csrf_token") or "")
    supplied = str(request.headers.get("X-CSRF-Token") or "")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        return jsonify({"ok": False, "error": "invalid csrf token"}), 403
    return None


_account_store = AccountStore(BASE_DIR, log_callback=log_line)
list_account_files = _account_store.list_files
read_account_lines = _account_store.read_lines
collect_all_accounts = _account_store.collect
parse_line = _account_store.parse_line
decode_sso_meta = _account_store.decode_sso_meta
unique_accounts = _account_store.unique

_cpa_service = CpaConversionService(
    account_store=_account_store,
    cpa_dir=CPA_DIR,
    config_loader=lambda: load_config(),
    default_proxy=lambda: PROXY_URL,
    converter=convert_one,
    normalize_sso=normalize_sso,
    fingerprint=sso_fingerprint,
    safe_filename=cpa_safe_filename,
    log_callback=log_line,
    enabled=AUTO_CPA,
    core_error=_CPA_CORE_ERR,
    delay=CPA_DELAY,
)


def list_cpa_files() -> List[Path]:
    return sorted(CPA_DIR.glob("xai-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def cpa_stats() -> dict:
    return _cpa_service.status()


def enqueue_missing_accounts(limit: int = 500) -> int:
    return _cpa_service.enqueue_missing_accounts(limit)


def finalize_batch_cpa(before: Set[str], expected_success: int) -> dict:
    result = _cpa_service.finalize_batch(
        before,
        expected_success,
        timeout=CPA_BATCH_WAIT_TIMEOUT,
    )
    return result.as_dict()


def start_cpa_worker() -> None:
    _cpa_service.start()


def to_grok2api_pool(accounts: List[dict]) -> dict:
    """grok2api-style local token pool using web SSO tokens."""
    tokens = []
    for acc in accounts:
        sso = (acc.get("sso") or "").strip()
        if not sso:
            continue
        tokens.append(
            {
                "token": sso,
                "email": acc.get("email") or "",
                "status": "active",
            }
        )
    return {
        "ssoBasic": tokens,
        "ssoSuper": [],
    }


def load_config() -> dict:
    return JsonConfigStore(CONFIG_PATH).load()


def email_config_public(cfg: Optional[dict] = None) -> dict:
    return EmailSettings(JsonConfigStore(CONFIG_PATH)).public(cfg)


def apply_email_config_from_ui(data: dict) -> dict:
    return EmailSettings(JsonConfigStore(CONFIG_PATH)).apply(data)



def resolve_proxy_url() -> str:
    """Prefer config.json proxy; auto-probe common Clash ports if dead."""
    import socket
    from urllib.parse import urlparse

    def open_port(host: str, port: int, timeout: float = 0.35) -> bool:
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            return True
        except Exception:
            return False

    preferred = ""
    try:
        cfg = load_config()
        preferred = str(cfg.get("proxy") or "").strip()
    except Exception:
        preferred = ""
    preferred = preferred or os.environ.get("GROK_PROXY", "").strip() or PROXY_URL

    def ok(url: str) -> bool:
        u = urlparse(url if "://" in url else "http://" + url)
        return open_port(u.hostname or "127.0.0.1", u.port or 7897)

    if preferred and ok(preferred):
        return preferred
    for port in (7897, 7890, 7891, 7892, 10809, 20171, 1080, 2080, 8888):
        url = f"http://127.0.0.1:{port}"
        if ok(url):
            return url
    return preferred or "http://127.0.0.1:7897"


def save_config(cfg: dict):
    JsonConfigStore(CONFIG_PATH).save(cfg)


# --------------- Clash helpers (optional external controller) ---------------
def clash_request(method: str, path: str, data=None, timeout=15):
    if not ENABLE_CLASH_UI:
        raise RuntimeError("clash ui disabled")
    url = CLASH_API + path
    body = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json"}
    if CLASH_SECRET:
        headers["Authorization"] = f"Bearer {CLASH_SECRET}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw.decode())


def clash_list_nodes() -> dict:
    """Return usable non-HK leaf nodes + selectors + current."""
    try:
        prox = clash_request("GET", "/proxies")["proxies"]
        cfg = clash_request("GET", "/configs") or {}
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "nodes": [],
            "selectors": {},
            "hint": "未检测到本机 Clash API。请在自己的 Clash 里选节点；本工具默认走 http://127.0.0.1:7897",
        }

    leaves = []
    for name, v in prox.items():
        t = v.get("type") or ""
        if t in (
            "Selector",
            "URLTest",
            "Fallback",
            "LoadBalance",
            "Relay",
            "Direct",
            "Reject",
            "Compatible",
            "Pass",
            "Dns",
        ):
            continue
        if name in ("PASS-RULE", "REJECT-DROP"):
            continue
        if HK_RE.search(name):
            continue
        leaves.append({"name": name, "type": t})

    # sort by region preference
    pref = ["US", "JP", "SG", "TW", "MY", "TH", "UK"]

    def key(n):
        name = n["name"].upper()
        for i, p in enumerate(pref):
            if name.startswith(p):
                return (i, name)
        return (99, name)

    leaves.sort(key=key)

    selectors = {}
    for name, v in prox.items():
        if v.get("type") == "Selector":
            selectors[name] = {"now": v.get("now"), "all": v.get("all") or []}

    return {
        "ok": True,
        "mode": cfg.get("mode"),
        "nodes": leaves,
        "selectors": selectors,
        "global_now": (selectors.get("GLOBAL") or {}).get("now"),
        "main_now": (selectors.get("🚀 使用节点") or {}).get("now"),
    }


def clash_set_node(node: str) -> Tuple[bool, str]:
    if not node:
        return True, "未指定节点（使用外部 Clash 当前节点）"
    if not ENABLE_CLASH_UI:
        return True, "Clash UI 关闭：请在本机 Clash 客户端切换节点"
    try:
        # ensure global mode so browser always uses proxy
        try:
            clash_request("PATCH", "/configs", {"mode": "global"})
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            log_line(f"[Debug] 切换 Clash global 模式失败: {exc}")
        prox = clash_request("GET", "/proxies")["proxies"]
        set_count = 0
        for name, v in prox.items():
            if v.get("type") != "Selector":
                continue
            alln = v.get("all") or []
            if node not in alln:
                continue
            try:
                clash_request(
                    "PUT",
                    "/proxies/" + urllib.parse.quote(name, safe=""),
                    {"name": node},
                )
                set_count += 1
            except Exception as e:
                log_line(f"[Clash] set {name} fail: {e}")
        if set_count == 0:
            return False, f"节点 {node} 不在任何选择器中（也可直接在 Clash 客户端切换）"
        return True, f"已切换到 {node}（{set_count} 个选择器）"
    except Exception as e:
        # soft-fail: external Clash without API is OK
        return True, f"Clash API 不可用，跳过切换（{e}）；请在客户端自选节点"


def clash_exit_ip() -> str:
    try:
        proxy_handler = urllib.request.ProxyHandler(
            {"http": PROXY_URL, "https": PROXY_URL}
        )
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(
            "http://ip-api.com/json/?fields=country,city,query,isp", timeout=12
        ) as resp:
            d = json.loads(resp.read().decode())
            return f"{d.get('query')} {d.get('country')}/{d.get('city')} ({d.get('isp')})"
    except Exception as e:
        return f"unknown ({e})"


def set_proxy_url(proxy: str) -> None:
    global PROXY_URL
    PROXY_URL = proxy
    os.environ["GROK_PROXY"] = proxy


_job_manager = RegistrationJobManager(
    base_dir=BASE_DIR,
    log_dir=LOG_DIR,
    python=VENV_PYTHON,
    main_script=MAIN_SCRIPT,
    panel_log=_panel_log,
    account_store=_account_store,
    cpa_service=_cpa_service,
    config_loader=load_config,
    config_saver=save_config,
    proxy_resolver=resolve_proxy_url,
    proxy_setter=set_proxy_url,
    exit_ip_probe=clash_exit_ip,
    process_terminator=terminate_process_tree,
    cpa_wait_timeout=CPA_BATCH_WAIT_TIMEOUT,
)


def job_worker(count: int, node: str = "", node_mode: str = "fixed", node_list=None):
    return _job_manager.run_batch(count)


def start_job(count: int, node: str = "", node_mode: str = "fixed") -> Tuple[bool, str]:
    return _job_manager.start(count)


def stop_job() -> Tuple[bool, str]:
    return _job_manager.stop()


# --------------- routes ---------------
@app.get("/login")
def login():
    # 默认无密码：直接进面板
    if not PANEL_AUTH:
        return redirect(url_for("index"))
    if session.get("ok"):
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


@app.post("/login")
def login_post():
    if not PANEL_AUTH:
        return redirect(url_for("index"))
    if request.form.get("password") == PANEL_PASSWORD:
        session["ok"] = True
        target = str(request.args.get("next") or "")
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("index")
        return redirect(target)
    return render_template("login.html", error="密码错误"), 401


@app.get("/logout")
def logout():
    session.clear()
    if not PANEL_AUTH:
        return redirect(url_for("index"))
    return redirect(url_for("login"))


@app.get("/")
def index():
    need = require_login()
    if need:
        return need
    files_meta = []
    total = 0
    for p in list_account_files():
        lines = read_account_lines(p)
        total += len(lines)
        files_meta.append(
            {
                "name": p.name,
                "count": len(lines),
                "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )
    return render_template(
        "index.html",
        base_dir=str(BASE_DIR),
        files=files_meta,
        file_count=len(files_meta),
        account_count=total,
        cpa_files=len(list_cpa_files()),
        csrf_token=csrf_token(),
        panel_auth=PANEL_AUTH,
    )


safe_name = _account_store.resolve_file


@app.get("/preview/<name>")
def preview_file(name: str):
    need = require_login()
    if need:
        return need
    path = safe_name(name)
    if not path:
        return "文件不存在", 404
    return render_template(
        "preview.html",
        name=path.name,
        content=path.read_text(encoding="utf-8", errors="replace"),
    )


@app.get("/download/<name>")
def download_file(name: str):
    need = require_login()
    if need:
        return need
    path = safe_name(name)
    if not path:
        return "文件不存在", 404
    return send_file(
        path,
        as_attachment=True,
        download_name=path.name,
        mimetype="text/plain; charset=utf-8",
    )


def _merged_sso_txt() -> str:
    seen = set()
    lines = []
    for _, line in collect_all_accounts():
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


@app.get("/download/sso.txt")
def download_sso_txt():
    """主接口 1：全部 SSO，格式 email----password----sso"""
    need = require_login()
    if need:
        return need
    body = _merged_sso_txt()
    fname = f"sso_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    return Response(
        body,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/download/merged.txt")
def download_merged():
    """兼容旧链接 → 同 SSO txt"""
    return download_sso_txt()


@app.get("/download/all.zip")
def download_zip():
    need = require_login()
    if need:
        return need
    buf = io.BytesIO()
    files = list_account_files()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if not files:
            zf.writestr("README.txt", "暂无 accounts_*.txt\n")
        for p in files:
            zf.write(p, arcname=p.name)
        seen = set()
        merged = []
        for _, line in collect_all_accounts():
            if line not in seen:
                seen.add(line)
                merged.append(line)
        zf.writestr(
            "accounts_merged_all.txt",
            "\n".join(merged) + ("\n" if merged else ""),
        )
    buf.seek(0)
    fname = f"accounts_all_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(
        buf, as_attachment=True, download_name=fname, mimetype="application/zip"
    )


@app.get("/download/accounts.json")
def download_accounts_json():
    """All accounts as one JSON array (email/password/sso)."""
    need = require_login()
    if need:
        return need
    accounts = unique_accounts()
    body = json.dumps(accounts, ensure_ascii=False, indent=2) + "\n"
    fname = f"accounts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        body,
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/download/cpa.zip")
def download_cpa_zip():
    """主接口 2：已自动 OAuth 转换的真 CPA JSON（auth_kind=oauth）。"""
    need = require_login()
    if need:
        return need
    files = list_cpa_files()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        readme = (
            "Grok Register → 真 CPA (CLIProxyAPI) JSON\n"
            "====================================\n\n"
            "1) 每个 xai-*.json 是 OAuth 凭证（access_token + refresh_token）。\n"
            "2) auth_kind=oauth，可直接放进 CLIProxyAPI auth-dir。\n"
            "3) 由注册成功后的 web SSO 自动换票生成。\n"
            "4) all.json 为全部账号数组；failed.jsonl 为转换失败记录（若有）。\n"
            "5) 若 zip 为空：先注册，或点「补转未转换 CPA」。\n"
        )
        zf.writestr("README.txt", readme)
        all_entries = []
        for i, p in enumerate(files, 1):
            try:
                raw = p.read_text(encoding="utf-8")
                obj = json.loads(raw)
                all_entries.append(obj)
                # keep original filename
                zf.writestr(p.name, raw if raw.endswith("\n") else raw + "\n")
            except Exception as e:
                zf.writestr(f"BAD-{p.name}.txt", str(e))
        zf.writestr(
            "all.json",
            json.dumps(all_entries, ensure_ascii=False, indent=2) + "\n",
        )
        if CPA_FAILED_PATH.exists():
            try:
                zf.write(CPA_FAILED_PATH, arcname="failed.jsonl")
            except OSError as exc:
                log_line(f"[Debug] 打包 CPA 失败日志失败: {exc}")
        if not files:
            zf.writestr(
                "EMPTY.txt",
                "暂无已转换的 CPA 文件。注册成功后会自动转换，或点击面板「补转未转换 CPA」。\n",
            )
    buf.seek(0)
    fname = f"cpa_oauth_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return send_file(
        buf, as_attachment=True, download_name=fname, mimetype="application/zip"
    )


@app.get("/download/grok2api.json")
def download_grok2api_json():
    need = require_login()
    if need:
        return need
    body = (
        json.dumps(to_grok2api_pool(unique_accounts()), ensure_ascii=False, indent=2)
        + "\n"
    )
    fname = f"grok2api_pool_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    return Response(
        body,
        mimetype="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/accounts")
def api_accounts():
    need = require_login()
    if need:
        return need
    data = []
    for source, line in collect_all_accounts():
        info = parse_line(line)
        info["source"] = source
        data.append(info)
    return jsonify(
        {
            "count": len(data),
            "files": [p.name for p in list_account_files()],
            "accounts": data,
        }
    )


@app.get("/api/nodes")
def api_nodes():
    need = require_login()
    if need:
        return need
    return jsonify(clash_list_nodes())


@app.post("/api/nodes/select")
def api_nodes_select():
    need = require_login()
    if need:
        return need
    data = request.get_json(force=True, silent=True) or {}
    node = str(data.get("node") or "").strip()
    if not node:
        return jsonify({"ok": False, "error": "node required"}), 400
    ok, msg = clash_set_node(node)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg, "exit": clash_exit_ip()})


@app.post("/api/accounts/delete")
def api_accounts_delete():
    """Delete selected accounts_*.txt files (after user downloaded them)."""
    need = require_login()
    if need:
        return need
    data = request.get_json(force=True, silent=True) or {}
    names = data.get("files") or data.get("names") or []
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        return jsonify({"ok": False, "error": "files required"}), 400

    deleted = []
    missing = []
    errors = []
    for name in names:
        name = str(name or "").strip()
        path = safe_name(name)
        if not path:
            missing.append(name)
            continue
        try:
            path.unlink()
            deleted.append(path.name)
            log_line(f"[*] 已删除账号文件: {path.name}")
        except Exception as e:
            errors.append(f"{name}: {e}")

    if not deleted and errors:
        return jsonify({"ok": False, "error": "; ".join(errors)}), 400
    return jsonify(
        {
            "ok": True,
            "deleted": deleted,
            "missing": missing,
            "errors": errors,
            "message": f"已删除 {len(deleted)} 个文件"
            + (f"，跳过 {len(missing)}" if missing else ""),
        }
    )


@app.get("/api/config/email")
def api_get_email_config():
    need = require_login()
    if need:
        return need
    return jsonify({"ok": True, "email": email_config_public()})


@app.post("/api/config/email")
def api_set_email_config():
    need = require_login()
    if need:
        return need
    data = request.get_json(force=True, silent=True) or {}
    try:
        email = apply_email_config_from_ui(data)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, "message": "邮箱设置已保存", "email": email})


@app.get("/api/job/status")
def api_job_status():
    need = require_login()
    if need:
        return need
    return jsonify(
        {
            "ok": True,
            "job": _job_manager.snapshot(),
            "logs": _panel_log.lines(),
            "cpa": cpa_stats(),
        }
    )


@app.get("/api/cpa/status")
def api_cpa_status():
    need = require_login()
    if need:
        return need
    return jsonify({"ok": True, "cpa": cpa_stats()})


@app.post("/api/cpa/backfill")
def api_cpa_backfill():
    need = require_login()
    if need:
        return need
    data = request.get_json(force=True, silent=True) or {}
    try:
        limit = int(data.get("limit") or 200)
    except Exception:
        limit = 200
    limit = max(1, min(limit, 1000))
    if not _CPA_CORE_OK:
        return jsonify({"ok": False, "error": f"core unavailable: {_CPA_CORE_ERR}"}), 500
    n = enqueue_missing_accounts(limit=limit)
    log_line(f"[CPA] 手动补转入队: {n}")
    return jsonify({"ok": True, "queued": n, "message": f"已入队 {n} 个待转换 SSO"})


@app.post("/api/job/start")
def api_job_start():
    need = require_login()
    if need:
        return need
    data = request.get_json(force=True, silent=True) or {}
    try:
        count = int(data.get("count") or 1)
    except Exception:
        count = 1
    ok, msg = start_job(count)
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})


@app.post("/api/job/stop")
def api_job_stop():
    need = require_login()
    if need:
        return need
    ok, msg = stop_job()
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "message": msg})


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "files": len(list_account_files()),
            "running": bool(_job_manager.snapshot().get("running")),
            "cpa": cpa_stats(),
        }
    )


@app.before_request
def ensure_background_services():
    start_cpa_worker()


def validate_panel_bind() -> None:
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if HOST.strip().lower() not in loopback_hosts and not PANEL_AUTH:
        raise RuntimeError("拒绝在非本机地址关闭鉴权；请设置 PANEL_AUTH=1")


if __name__ == "__main__":
    validate_panel_bind()
    print(f"Grok Register Panel -> http://{HOST}:{PORT}")
    print(f"CPA auto-convert dir -> {CPA_DIR} core={_CPA_CORE_OK}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
