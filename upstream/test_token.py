#!/usr/bin/env python3
"""Compatibility entrypoint for the CPA token diagnostic."""

from tools.diagnostics.cpa_token_probe import main


if __name__ == "__main__":
    raise SystemExit(main())
