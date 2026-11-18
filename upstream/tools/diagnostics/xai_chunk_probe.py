#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug: search consent page HTML for form/action patterns and fetch JS chunks."""

import re
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML_PATH = ROOT / "data" / "logs" / "consent_debug.html"
OUT_PATH = ROOT / "data" / "logs" / "debug_analysis.txt"

html = HTML_PATH.read_text(encoding="utf-8")
lines = []

def log(msg=""):
    lines.append(str(msg))

log(f"HTML length: {len(html)} chars")

# 1. Search for <form> elements
forms = re.findall(r'<form[^>]*>.*?</form>', html, re.S)
log(f"\n=== Found {len(forms)} <form> elements ===")
for i, f in enumerate(forms):
    log(f"  Form {i}: {f[:500]}")

# 2. Search for "action" in form context
form_action_patterns = re.findall(r'action["\s]*[:=]\s*["\']([^"\']+)["\']', html)
log(f"\n=== action= patterns: {form_action_patterns[:10]} ===")

# 3. Search in RSC flight data for "form" references
# The RSC data is in __next_f.push chunks
chunks_raw = re.findall(r'__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]', html)
log(f"\n=== {len(chunks_raw)} __next_f chunks ===")

all_flight_data = ""
for chunk in chunks_raw:
    try:
        unescaped = chunk.encode("utf-8").decode("unicode_escape")
    except Exception:
        unescaped = chunk
    all_flight_data += unescaped

log(f"Total flight data: {len(all_flight_data)} chars")

# Search for "form" in flight data
form_refs = [m.start() for m in re.finditer(r'"form"', all_flight_data, re.I)]
log(f"\n'form' references in flight data: {len(form_refs)}")
for idx in form_refs[:5]:
    ctx = all_flight_data[max(0, idx-100):idx+300]
    log(f"  pos {idx}: ...{ctx}...")

# Search for "action" in flight data
action_refs = [m.start() for m in re.finditer(r'"action"', all_flight_data, re.I)]
log(f"\n'action' references in flight data: {len(action_refs)}")
for idx in action_refs[:5]:
    ctx = all_flight_data[max(0, idx-50):idx+200]
    log(f"  pos {idx}: ...{ctx}...")

# Search for "allow" in flight data
allow_refs = [m.start() for m in re.finditer(r'allow', all_flight_data, re.I)]
log(f"\n'allow' references in flight data: {len(allow_refs)}")
for idx in allow_refs[:5]:
    ctx = all_flight_data[max(0, idx-100):idx+200]
    log(f"  pos {idx}: ...{ctx}...")

# Search for "consent" in flight data
consent_refs = [m.start() for m in re.finditer(r'consent', all_flight_data, re.I)]
log(f"\n'consent' references in flight data: {len(consent_refs)}")
for idx in consent_refs[:3]:
    ctx = all_flight_data[max(0, idx-100):idx+300]
    log(f"  pos {idx}: ...{ctx}...")

# Search for any long hex strings (32+ hex chars) in flight data
hex_in_flight = re.findall(r'[a-f0-9]{32,64}', all_flight_data)
log(f"\nHex strings (32+) in flight data: {len(hex_in_flight)}")
for h in hex_in_flight[:10]:
    log(f"  {h} (len={len(h)})")

# Search for "$F" references (action bindings in RSC)
f_refs = re.findall(r'\$F[0-9a-f]+', all_flight_data)
log(f"\n$F references in flight data: {f_refs[:10]}")

# Search for "$ACTION" references
action_refs2 = re.findall(r'\$ACTION[^"\']*', all_flight_data)
log(f"\n$ACTION references: {action_refs2[:10]}")

# 4. Extract unique JS chunk URLs and try to fetch the consent-specific ones
js_srcs = list(set(re.findall(r'src="(/_next/static/chunks/[^"]+)"', html)))
log(f"\n=== {len(js_srcs)} unique JS chunks ===")
for s in sorted(js_srcs):
    log(f"  {s}")

# 5. Try to fetch a few likely chunks and search for action IDs
from curl_cffi import requests as cf_requests

proxy = os.environ.get("GROK_PROXY", "http://127.0.0.1:7897")
session = cf_requests.Session(impersonate="chrome131")
if proxy:
    session.proxies = {"http": proxy, "https": proxy}

# Focus on chunks that might contain consent form logic
# Look for chunk names that might be page-specific (not framework chunks)
likely_chunks = [s for s in js_srcs if not any(
    x in s for x in ["turbopack", "framework", "webpack", "main", "polyfill"]
)]

log(f"\n=== Fetching {min(len(likely_chunks), 10)} likely chunks to search for action IDs ===")

# Also check ALL chunks - the action could be in any of them
# But limit to first 20 to avoid too many requests
chunks_to_fetch = sorted(js_srcs)[:20]
log(f"Fetching {len(chunks_to_fetch)} chunks...")

found_action_ids = []
for chunk_url in chunks_to_fetch:
    full_url = f"https://accounts.x.ai{chunk_url}"
    try:
        resp = session.get(full_url, timeout=15)
        if resp.status_code != 200:
            log(f"  {chunk_url}: HTTP {resp.status_code}")
            continue
        js = resp.text
        # Search for action ID patterns
        # In Turbopack, action IDs might be string literals
        hex_matches = re.findall(r'["\']([a-f0-9]{40,44})["\']', js)
        if hex_matches:
            log(f"  {chunk_url}: Found {len(hex_matches)} hex matches!")
            for h in hex_matches[:5]:
                log(f"    {h}")
                found_action_ids.append(h)
        # Also search for "action" near "consent" or "allow"
        if "consent" in js.lower() or "allow" in js.lower():
            # Find the context
            for kw in ["consent", "allow", "actionId"]:
                idx = js.lower().find(kw)
                if idx >= 0:
                    ctx = js[max(0, idx-100):idx+200]
                    log(f"  {chunk_url}: '{kw}' found at {idx}: ...{ctx[:250]}...")
    except Exception as e:
        log(f"  {chunk_url}: Error: {e}")

log(f"\n=== Found action IDs: {found_action_ids} ===")
log(f"Unique: {list(set(found_action_ids))}")

OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
print(f"Analysis saved to {OUT_PATH}")
