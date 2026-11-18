#!/usr/bin/env python3
"""Compatibility entrypoint for the CPA endpoint diagnostic."""

from tools.diagnostics.cpa_endpoint_probe import main


if __name__ == "__main__":
    raise SystemExit(main())
