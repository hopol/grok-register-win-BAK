import importlib
import subprocess
import sys
from pathlib import Path

import grok_register_ttk as register


ROOT = Path(__file__).resolve().parent.parent


def test_account_output_uses_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(register, "ACCOUNTS_DIR", str(tmp_path / "accounts"))
    output = Path(register.new_accounts_output_file())
    assert output.parent == tmp_path / "accounts"
    assert output.name.startswith("accounts_")


def test_diagnostic_entrypoints_expose_help_without_network():
    for relative in (
        "tools/diagnostics/cpa_token_probe.py",
        "tools/diagnostics/cpa_endpoint_probe.py",
        "tools/convert_batch.py",
        "test_token.py",
        "test_endpoints.py",
        "convert_batch.py",
    ):
        result = subprocess.run(
            [sys.executable, str(ROOT / relative), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (relative, result.stderr)


def test_debug_compatibility_wrapper_has_no_import_side_effect():
    importlib.import_module("debug_chunks2")
