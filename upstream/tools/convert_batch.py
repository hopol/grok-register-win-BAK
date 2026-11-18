#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量将 email----password----sso 转换为 CPA OAuth JSON。

支持两种输入：
  1. --input <file>   指定单个 txt 文件
  2. 默认扫描 data/accounts/ 与项目根目录的 accounts_*.txt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Setup paths
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from sso2cpa_core import (
    convert_one,
    normalize_sso,
    parse_uploaded_records,
    sso_fingerprint,
    safe_filename,
)

OUTPUT_DIR = ROOT / "data" / "cpa"
PROXY = os.environ.get("GROK_PROXY", "http://127.0.0.1:7897")
DELAY = 1.0

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def load_all_records(input_file: str = "") -> list:
    """Load records from a single file or all accounts_*.txt files."""
    if input_file:
        raw = Path(input_file).read_bytes()
        records = parse_uploaded_records(raw)
        print(f"[*] Loaded {len(records)} records from {input_file}")
        return records

    # Scan the current output directory plus legacy root files.
    all_raw = b""
    files = sorted((ROOT / "data" / "accounts").glob("accounts_*.txt"))
    files.extend(sorted(ROOT.glob("accounts_*.txt")))
    print(f"[*] Found {len(files)} accounts_*.txt files")
    for f in files:
        data = f.read_bytes()
        all_raw += data + b"\n"
    records = parse_uploaded_records(all_raw)
    # Deduplicate
    seen = set()
    unique = []
    for r in records:
        s = r.get("sso", "")
        if s and s not in seen:
            seen.add(s)
            unique.append(r)
    print(f"[*] Loaded {len(records)} records ({len(unique)} unique after dedup)")
    return unique


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="", help="Input txt file (default: scan accounts_*.txt)")
    args = parser.parse_args()

    records = load_all_records(args.input)
    total = len(records)
    if not total:
        print("[!] No records found")
        return 1

    print(f"[*] Proxy: {PROXY}")
    print(f"[*] Output dir: {OUTPUT_DIR}")
    print(f"[*] Delay: {DELAY}s between conversions")
    print()

    ok_list = []
    fail_list = []
    seen_fps: set[str] = set()

    for i, rec in enumerate(records, 1):
        email = rec.get("email") or ""
        sso = rec.get("sso") or ""
        password = rec.get("password") or ""
        fp = sso_fingerprint(sso)

        if fp in seen_fps:
            print(f"[{i}/{total}] SKIP (duplicate) {email}")
            continue
        seen_fps.add(fp)

        print(f"[{i}/{total}] {email} ...", end=" ", flush=True)
        try:
            entry = convert_one(sso, email=email, proxy=PROXY)
            if password:
                entry["password"] = password
            entry["_source"] = "batch-convert"
            fname = f"xai-{safe_filename(email or 'unknown')}.json"
            out_path = OUTPUT_DIR / fname
            if out_path.exists():
                try:
                    old = json.loads(out_path.read_text(encoding="utf-8"))
                    old_fp = sso_fingerprint(normalize_sso(old.get("sso") or ""))
                    if old_fp and old_fp != fp:
                        fname = f"xai-{safe_filename(email)}-{fp[:8]}.json"
                        out_path = OUTPUT_DIR / fname
                except Exception:
                    pass
            out_path.write_text(
                json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            ok_list.append(entry)
            print(f"OK -> {fname}")
        except Exception as e:
            fail_list.append({"email": email, "error": str(e)})
            print(f"FAIL: {e}")

        if DELAY > 0 and i < total:
            time.sleep(DELAY)

    print()
    print("=" * 60)
    print(f"Done: {len(ok_list)} ok / {len(fail_list)} fail / {total} total")

    if ok_list:
        all_path = OUTPUT_DIR / "batch_all.json"
        all_path.write_text(
            json.dumps(ok_list, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[+] All OK entries: {all_path}")

    if fail_list:
        fail_path = OUTPUT_DIR / "batch_failed.jsonl"
        with open(fail_path, "a", encoding="utf-8") as f:
            for item in fail_list:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"[!] Failed entries logged: {fail_path}")
        for item in fail_list:
            print(f"     FAIL {item['email']}: {item['error']}")

    return 0 if not fail_list else 1


if __name__ == "__main__":
    raise SystemExit(main())
