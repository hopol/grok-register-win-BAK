# Architecture

## Runtime Flow

```text
start.bat
  -> launcher.py
  -> panel/app.py
  -> grok_register_ttk.py cli
  -> RegistrationRunner
  -> browser and mailbox adapters
  -> data/accounts/accounts_*.txt
  -> batch-completion CPA conversion queue
  -> data/cpa/xai-*.json
```

## Module Ownership

| Module | Owns | Does not own |
| --- | --- | --- |
| `launcher.py` | Environment bootstrap, proxy-port detection, panel process | Registration behavior |
| `panel/app.py` | Flask routes and dependency composition | Job state, CPA queue, account parsing |
| `panel/account_store.py` | Account file discovery, parsing, deduplication, safe path resolution | HTTP routes |
| `panel/cpa_service.py` | CPA queue, worker, index, failed log and batch conversion result | Panel job state |
| `panel/email_settings.py` | Public email settings and validated config updates | JSON persistence details |
| `panel/job_manager.py` | Registration child process and batch state transitions | Flask request handling, CPA persistence |
| `panel/panel_log.py` | In-memory panel log and active batch log file | Job orchestration |
| `lib/config_store.py` | JSON parsing and atomic configuration writes | Configuration policy |
| `lib/process_control.py` | Scoped termination of a panel job and its descendants | Global process cleanup |
| `lib/grok2api_pool.py` | Local and remote grok2api token-pool publishing | Registration flow |
| `lib/browser_proxy.py` | Proxy parsing, authenticated proxy bridge, Chromium proxy options | Registration page flow |
| `lib/registration_runner.py` | Shared GUI/CLI retries, counters, persistence order, browser lifecycle | Browser selectors and provider protocol details |
| `grok_register_ttk.py` | Browser page flow, mailbox adapters, GUI and CLI adapters | Duplicated orchestration |
| `lib/sso2cpa_core.py` | Web SSO to CPA OAuth conversion | Registration job control |

## Safety Boundaries

- `terminate_process_tree()` starts from the exact `Popen.pid` created by the panel and walks only its descendants with `psutil`.
- Configuration writes use a temporary file followed by `os.replace`; readers either receive a complete JSON object or a contextual `ConfigError`.
- The panel never returns configured email secrets. Manual diagnostics redact credential-bearing keys and response text.
- Background CPA conversion starts lazily on the first panel request and is guarded against duplicate worker threads.
- A panel registration job remains running with status `converting_cpa` after browser rounds finish. It becomes idle only after the batch CPA queue drains or the configured wait timeout is reached.
- Dependency direction is `panel/app.py -> RegistrationJobManager -> CpaConversionService`. The CPA module returns results and never mutates job state.

## Extension Rules

- Add mailbox implementations behind the existing mailbox-provider seam. Do not add provider-specific retries to GUI or CLI code.
- Add registration orchestration behavior to `RegistrationRunner`; keep GUI and CLI as thin adapters.
- Add panel filesystem behavior to `AccountStore`, not directly to route functions.
- Add CPA persistence or retry behavior to `CpaConversionService`; add job state transitions to `RegistrationJobManager`.
- Best-effort browser operations may catch broad external-library failures, but they must write context to `data/logs/runtime.log` rather than silently discard errors.
