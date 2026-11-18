# Directory Layout

## Root Entrypoints

The repository root contains only Windows launchers, compatibility entrypoints and top-level configuration:

- `start.bat`, `启动.bat`, `launcher.py`: normal application startup.
- `setup.bat`, `setup-dev.bat`, `test.bat`: environment and verification commands.
- `grok_register_ttk.py`: stable GUI/CLI registration entrypoint.
- `config.example.json`, `requirements*.txt`, `pytest.ini`: project configuration.
- `convert_batch.py`, `test_token.py`, `test_endpoints.py`, `debug_chunks2.py`: compatibility wrappers for commands now implemented under `tools/`.

## Source Modules

- `panel/`: web routes and panel-specific application modules.
- `lib/`: reusable registration, proxy, persistence, conversion and publishing modules.
- `workers/`: independently deployable Cloudflare Email Worker.

Dependencies point inward toward `lib/`. `lib/` must not import Flask routes or panel job state.

## Operational Tools

- `tools/convert_batch.py`: offline/batch SSO to CPA conversion.
- `tools/diagnostics/`: live credential-safe probes for xAI and CPA endpoints.
- `scripts/`: Windows Scheduled Task installation and removal scripts.

Tools may import `lib/`, but production modules never import tools.

## Runtime Data

- `data/accounts/`: new `accounts_*.txt` registration output.
- `data/cpa/`: generated CPA OAuth JSON, index and failed conversion log.
- `data/logs/`: launcher, panel, registration and diagnostic logs.
- `data/.panel_secret`: local Flask session secret.

Runtime data is Git-ignored. `AccountStore` still reads legacy root-level `accounts_*.txt` files, but all new writes go to `data/accounts/`.
