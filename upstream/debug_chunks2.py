#!/usr/bin/env python3
"""Compatibility launcher for the xAI consent chunk diagnostic."""

import runpy


if __name__ == "__main__":
    runpy.run_module("tools.diagnostics.xai_chunk_probe", run_name="__main__")
