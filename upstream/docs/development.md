# Development

## Setup

Runtime-only installation:

```bat
setup.bat
```

Development installation:

```bat
setup-dev.bat
```

`setup.bat --dev` is equivalent. Development requirements include the locked runtime dependencies plus pytest.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
node --test workers/temp-email-worker/test/otp.test.js
```

`pytest.ini` restricts collection to `tests/`. Live network diagnostics live under `tools/diagnostics/` and are never imported by the test suite.

## Diagnostics

```powershell
.venv\Scripts\python.exe tools\diagnostics\cpa_token_probe.py --cpa-file data\cpa\xai-example.json
.venv\Scripts\python.exe tools\diagnostics\cpa_endpoint_probe.py --cpa-file data\cpa\xai-example.json
```

When `--cpa-file` is omitted, the newest `data/cpa/xai-*.json` is selected. Output must report only file name, endpoint, status and sanitized response text. Never print Token prefixes, decoded JWT payloads, SSO cookies or passwords.

## Verification Before Commit

```powershell
.venv\Scripts\python.exe -m compileall -q launcher.py grok_register_ttk.py panel lib tools tests
.venv\Scripts\python.exe -m pytest -q
node --test workers/temp-email-worker/test/otp.test.js
git diff --check
```

## Windows Autostart

`scripts/install-autostart.ps1` registers a per-user `AtLogOn` scheduled task. The task runs `scripts/autostart.ps1`, which checks `/health` before starting anything, launches `launcher.py` through `pythonw.exe`, and verifies that the panel becomes healthy.

Autostart mode sets `GROK_AUTOSTART=1` and `GROK_OPEN_BROWSER=0`. The launcher passes `CREATE_NO_WINDOW` to the panel child so neither process creates a console window.
