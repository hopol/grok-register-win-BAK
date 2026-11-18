#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SSO → CPA (CLIProxyAPI xAI OAuth) conversion core.

Shared by sso2cpa web UI and grok-register panel auto-convert.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple
from urllib.parse import urlencode, urlparse

try:
    from curl_cffi import requests as cf_requests

    def _session():
        return cf_requests.Session(impersonate="chrome131")

except ImportError:
    import requests as cf_requests  # type: ignore

    def _session():
        return cf_requests.Session()


# ---- OAuth constants (Grok CLI / Build) ----
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
TOKEN_URL = f"{OIDC_ISSUER}/oauth2/token"
AUTHORIZE_URL = f"{OIDC_ISSUER}/oauth2/authorize"
REDIRECT_URI = "http://127.0.0.1:56121/callback"
DEFAULT_SCOPE = (
    "openid profile email offline_access grok-cli:access api:access "
    "conversations:read conversations:write"
)
GROK_REFERRER = "grok-build"
GROK_VERSION = "0.2.93"
GROK_TOKEN_UA = f"grok-pager/{GROK_VERSION} grok-shell/{GROK_VERSION} (linux; x86_64)"
NEXT_ACTION_ID = "401b73e22a5e68737d0037e1aa449fef82cd1b35fb"
BASE_URL = "https://cli-chat-proxy.grok.com/v1"


class ConvertError(Exception):
    pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def random_b64url(n: int = 32) -> str:
    return b64url(secrets.token_bytes(n))


def normalize_sso(token: str) -> str:
    token = (token or "").strip()
    if token.startswith("sso="):
        token = token[4:].strip()
    return token


def decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        pad = "=" * (-len(parts[1]) % 4)
        raw = base64.urlsafe_b64decode(parts[1] + pad)
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        return {}


def safe_filename(s: str) -> str:
    s = re.sub(r"[^\w.@+-]+", "_", (s or "").strip())
    return (s[:100] or "unknown")


def rfc3339(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sso_fingerprint(sso: str) -> str:
    return hashlib.sha256(normalize_sso(sso).encode("utf-8")).hexdigest()


def parse_uploaded_records(raw: bytes, filename: str = "") -> List[Dict[str, str]]:
    """Support: grok2api pool JSON / accounts array / txt lines / single sso."""
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return []

    records: List[Dict[str, str]] = []

    try:
        data = json.loads(text)
    except Exception:
        data = None

    if isinstance(data, dict):
        pool_like = False
        for _pool_name, items in data.items():
            if not isinstance(items, list):
                continue
            pool_like = True
            for item in items:
                if isinstance(item, str):
                    tok = normalize_sso(item)
                    if tok:
                        records.append({"email": "", "sso": tok})
                elif isinstance(item, dict):
                    tok = normalize_sso(
                        item.get("token") or item.get("sso") or item.get("raw") or ""
                    )
                    if not tok:
                        continue
                    email = str(
                        item.get("email") or item.get("note") or item.get("mail") or ""
                    ).strip()
                    records.append({"email": email, "sso": tok})
        if not pool_like and (data.get("sso") or data.get("token")):
            tok = normalize_sso(data.get("sso") or data.get("token") or "")
            if tok:
                records.append(
                    {
                        "email": str(data.get("email") or "").strip(),
                        "sso": tok,
                    }
                )
        if not records and isinstance(data.get("accounts"), list):
            for item in data["accounts"]:
                if not isinstance(item, dict):
                    continue
                tok = normalize_sso(item.get("sso") or item.get("token") or "")
                if tok:
                    records.append(
                        {
                            "email": str(item.get("email") or "").strip(),
                            "sso": tok,
                        }
                    )
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                tok = normalize_sso(item)
                if tok:
                    records.append({"email": "", "sso": tok})
            elif isinstance(item, dict):
                tok = normalize_sso(item.get("sso") or item.get("token") or "")
                if tok:
                    records.append(
                        {
                            "email": str(item.get("email") or "").strip(),
                            "sso": tok,
                        }
                    )

    if not records:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            email = ""
            if "----" in line:
                parts = line.split("----")
                email = parts[0].strip()
                line = parts[-1].strip()
            tok = normalize_sso(line)
            if tok:
                records.append({"email": email, "sso": tok})

    seen = set()
    out = []
    for r in records:
        s = r["sso"]
        if s in seen:
            continue
        seen.add(s)
        out.append(r)
    return out


def new_session(proxy: str = ""):
    s = _session()
    proxy = (proxy or "").strip()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def set_sso_cookies(session, sso: str):
    for domain_url in ("https://accounts.x.ai/", "https://auth.x.ai/"):
        host = urlparse(domain_url).hostname
        session.cookies.set("sso", sso, domain=host, path="/")
        session.cookies.set("sso-rw", sso, domain=host, path="/")


def browser_headers(method: str = "GET", referer: str = "", next_action: str = "") -> dict:
    h = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        ),
        "Sec-CH-UA": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Linux"',
        "Accept-Language": "en-US,en;q=0.9",
    }
    if method.upper() == "POST":
        h.update(
            {
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "Origin": "https://accounts.x.ai",
                "Referer": referer or "https://accounts.x.ai/",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
            }
        )
        if next_action:
            h["Next-Action"] = next_action
    else:
        h.update(
            {
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "application/json;q=0.8,*/*;q=0.7"
                ),
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
            }
        )
    return h


def extract_next_action_id(html: str, fallback: str = NEXT_ACTION_ID) -> str:
    """Dynamically extract Next.js Server Action ID from consent page HTML.

    Next.js generates action IDs at build time; they change on each deploy.
    The hardcoded NEXT_ACTION_ID may become stale, so we try to extract the
    current ID from the page's RSC flight data.
    """
    if not html:
        return fallback

    # Pattern 1: Explicit $ACTION_ID_ marker (Next.js internal format)
    m = re.search(r'\$ACTION_ID_([a-f0-9]{32,64})', html)
    if m:
        return m.group(1)

    # Pattern 2: "actionId" or "id" key with hex value in JSON structures
    for pat in (
        r'"actionId"\s*:\s*"([a-f0-9]{32,64})"',
        r'"id"\s*:\s*"([a-f0-9]{32,64})"\s*[,}]',
    ):
        m = re.search(pat, html)
        if m:
            return m.group(1)

    # Pattern 3: Hex strings in __next_f.push() flight data chunks
    for chunk in re.findall(r'__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]', html):
        try:
            unescaped = chunk.encode('utf-8').decode('unicode_escape')
        except Exception:
            unescaped = chunk
        candidates = re.findall(r'([a-f0-9]{40,44})', unescaped)
        if candidates:
            return candidates[0]

    # Pattern 4: Broad search for hex strings near consent/allow keywords
    lower_html = html.lower()
    for keyword in ("consent", "allow", "authorize"):
        idx = lower_html.find(keyword)
        if idx >= 0:
            window = html[max(0, idx - 3000):idx + 3000]
            candidates = re.findall(r'["\']([a-f0-9]{40,44})["\']', window)
            if candidates:
                return candidates[0]

    # Pattern 5: Search JS chunk files referenced in the consent page HTML.
    # In Next.js with Turbopack, action IDs live in JS chunks, not RSC flight data.
    js_srcs = re.findall(r'src="(/_next/static/chunks/[^"]+\.js)"', html)
    for chunk_path in js_srcs[:30]:  # limit to avoid too many requests
        try:
            chunk_url = f"https://accounts.x.ai{chunk_path}"
            _session = _session()
            _resp = _session.get(chunk_url, timeout=15)
            if _resp.status_code != 200:
                continue
            _js = _resp.text
            # Look for createServerReference with hex action ID
            m = re.search(r'createServerReference\("([a-f0-9]{32,64})"', _js)
            if m:
                # Verify it's related to consent/submit
                if "consent" in _js.lower() or "submit" in _js.lower():
                    return m.group(1)
            # Fallback: any hex string 40-44 chars in a chunk containing ConsentScreen
            if "ConsentScreen" in _js or "submitOAuth2Consent" in _js:
                hex_matches = re.findall(r'["\']([a-f0-9]{40,44})["\']', _js)
                if hex_matches:
                    return hex_matches[0]
        except Exception:
            continue

    return fallback


def parse_consent_code(body: str) -> str:
    for line in (body or "").splitlines():
        idx = line.find("{")
        if idx < 0:
            continue
        try:
            obj = json.loads(line[idx:])
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("code"):
            if obj.get("success") is False:
                raise ConvertError(
                    f"consent 失败: {obj.get('error') or obj.get('action')}"
                )
            return str(obj["code"])
        if isinstance(obj, list):
            for it in obj:
                if isinstance(it, dict) and it.get("code"):
                    return str(it["code"])
    m = re.search(r'"code"\s*:\s*"([^"]+)"', body or "")
    return m.group(1) if m else ""


def sso_to_token(sso: str, proxy: str = "") -> dict:
    sso = normalize_sso(sso)
    if not sso:
        raise ConvertError("空 SSO")

    session = new_session(proxy)
    set_sso_cookies(session, sso)

    verifier = random_b64url(32)
    state = random_b64url(16)
    nonce = random_b64url(16)
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": DEFAULT_SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "nonce": nonce,
        "referrer": GROK_REFERRER,
    }
    auth_url = AUTHORIZE_URL + "?" + urlencode(params)

    try:
        resp = session.get(
            auth_url,
            headers=browser_headers("GET"),
            allow_redirects=True,
            timeout=30,
        )
    except Exception as e:
        raise ConvertError(f"authorize 请求失败: {e}") from e

    final_url = str(resp.url)
    if "sign-in" in final_url or "sign-up" in final_url:
        raise ConvertError("SSO 无效或已过期（跳到登录页）")
    if "/oauth2/consent" not in final_url:
        raise ConvertError(f"authorize 未进入 consent 页: {final_url}")

    # Dynamically extract Next.js Server Action ID from consent page HTML.
    # The hardcoded NEXT_ACTION_ID may be stale after xAI redeploys accounts.x.ai.
    consent_html = resp.text or ""
    action_id = extract_next_action_id(consent_html, fallback=NEXT_ACTION_ID)
    if action_id == NEXT_ACTION_ID and consent_html:
        # Extraction failed; will likely get 404 but try anyway
        pass

    payload = [
        {
            "action": "allow",
            "clientId": CLIENT_ID,
            "redirectUri": REDIRECT_URI,
            "scope": DEFAULT_SCOPE,
            "state": state,
            "codeChallenge": challenge,
            "codeChallengeMethod": "S256",
            "nonce": nonce,
            "principalType": "User",
            "principalId": "",
            "referrer": GROK_REFERRER,
        }
    ]
    try:
        cres = session.post(
            final_url,
            data=json.dumps(payload, separators=(",", ":")),
            headers=browser_headers("POST", referer=final_url, next_action=action_id),
            timeout=30,
        )
    except Exception as e:
        raise ConvertError(f"consent 请求失败: {e}") from e

    if cres.status_code < 200 or cres.status_code >= 300:
        # Fallback: submit consent as a regular form POST (progressive enhancement)
        # The consent page has a <form action="https://auth.x.ai/oauth2/authorize" method="POST">
        # with hidden inputs + name="action" value="allow" submit button.
        try:
            form_data = {
                "client_id": CLIENT_ID,
                "redirect_uri": REDIRECT_URI,
                "scope": DEFAULT_SCOPE,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "nonce": nonce,
                "principal_type": "User",
                "principal_id": "",
                "referrer": GROK_REFERRER,
                "action": "allow",
            }
            fres = session.post(
                "https://auth.x.ai/oauth2/authorize",
                data=form_data,
                headers={
                    "User-Agent": browser_headers("GET")["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml,application/json;q=0.8,*/*;q=0.7",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": "https://accounts.x.ai",
                    "Referer": final_url,
                },
                allow_redirects=False,
                timeout=30,
            )
            # Check redirect Location for code
            location = str(fres.headers.get("Location") or "")
            if "code=" in location:
                code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code", [""])[0]
                if code:
                    # Skip to token exchange
                    pass
            if not code and fres.status_code >= 300 and fres.status_code < 400:
                raise ConvertError(f"consent form POST redirect without code: {location[:300]}")
            if not code:
                raise ConvertError(f"consent form POST failed: HTTP {fres.status_code}: {fres.text[:300]}")
        except ConvertError:
            raise
        except Exception as e:
            raise ConvertError(
                f"consent HTTP {cres.status_code}: {cres.text[:300]}"
                f" (action_id={action_id[:16]}... extracted={action_id != NEXT_ACTION_ID})"
                f" | form fallback also failed: {e}"
            ) from e
    else:
        code = parse_consent_code(cres.text)
        if not code:
            raise ConvertError(f"consent 响应缺少 code: {cres.text[:300]}")

    try:
        tres = session.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={
                "User-Agent": GROK_TOKEN_UA,
                "Accept": "*/*",
                "X-Grok-Client-Version": GROK_VERSION,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            timeout=30,
        )
    except Exception as e:
        raise ConvertError(f"token 请求失败: {e}") from e

    if tres.status_code < 200 or tres.status_code >= 300:
        raise ConvertError(f"token HTTP {tres.status_code}: {tres.text[:300]}")

    try:
        token = tres.json()
    except Exception as e:
        raise ConvertError(f"token 非 JSON: {tres.text[:200]}") from e

    if not token.get("access_token"):
        raise ConvertError(f"token 响应无 access_token: {token}")

    token.setdefault("expires_in", 21600)
    token.setdefault("token_type", "Bearer")
    return token


def token_to_cpa_entry(token: dict, sso: str, email_hint: str = "") -> dict:
    access = decode_jwt_payload(token.get("access_token") or "")
    idp = decode_jwt_payload(token.get("id_token") or "")
    sub = str(access.get("sub") or access.get("principal_id") or "").strip()
    email = str(idp.get("email") or email_hint or "").strip()
    expires_in = int(token.get("expires_in") or 21600)
    exp = access.get("exp")
    if isinstance(exp, (int, float)) and exp > 0:
        expired = datetime.fromtimestamp(float(exp), tz=timezone.utc)
    else:
        expired = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() + expires_in, tz=timezone.utc
        )
    now = datetime.now(timezone.utc)
    return {
        "type": "xai",
        "auth_kind": "oauth",
        "access_token": token.get("access_token") or "",
        "refresh_token": token.get("refresh_token") or "",
        "id_token": token.get("id_token") or "",
        "token_type": token.get("token_type") or "Bearer",
        "expired": rfc3339(expired),
        "last_refresh": rfc3339(now),
        "email": email,
        "sub": sub,
        "base_url": BASE_URL,
        "token_endpoint": TOKEN_URL,
        "redirect_uri": REDIRECT_URI,
        "disabled": False,
        "headers": {
            "x-grok-client-version": GROK_VERSION,
            "x-xai-token-auth": "xai-grok-cli",
            "x-authenticateresponse": "authenticate-response",
            "x-grok-client-identifier": "grok-pager",
            "User-Agent": GROK_TOKEN_UA,
        },
        "sso": normalize_sso(sso),
    }


def convert_one(sso: str, email: str = "", proxy: str = "") -> dict:
    token = sso_to_token(sso, proxy=proxy)
    return token_to_cpa_entry(token, sso, email_hint=email)


def convert_records(
    records: List[Dict[str, str]],
    proxy: str,
    delay: float = 0.5,
) -> Tuple[List[dict], List[dict]]:
    ok_list: List[dict] = []
    fail_list: List[dict] = []
    for i, rec in enumerate(records, 1):
        email = rec.get("email") or ""
        sso = rec.get("sso") or ""
        try:
            entry = convert_one(sso, email=email, proxy=proxy)
            ok_list.append(entry)
        except Exception as e:
            fail_list.append(
                {
                    "email": email,
                    "sso_short": (sso[:24] + "...") if len(sso) > 24 else sso,
                    "error": str(e),
                }
            )
        if delay > 0 and i < len(records):
            time.sleep(delay)
    return ok_list, fail_list
