# Domain Context

## Terms

- **Registration run**: one requested batch of Grok account registrations.
- **Registration slot**: one target position inside a registration run. A slot may retry before it becomes one success or one failure.
- **Mailbox provider**: an adapter that creates a temporary address and retrieves the verification message.
- **Web SSO**: the Grok browser session token stored in `data/accounts/accounts_*.txt`.
- **CPA entry**: the OAuth JSON generated from Web SSO for CLIProxyAPI-compatible clients.
- **Panel job**: the child CLI registration process owned by the Flask panel.

## Invariants

- A panel job may terminate only its own child process tree. It must never terminate browsers or Python processes by executable name.
- Credentials remain in ignored local files and must not appear in diagnostic output.
- GUI and CLI registration use the same registration runner so retry and accounting behavior cannot drift.
- Runtime setup installs only application dependencies. Development setup explicitly installs test dependencies.
- New registration artifacts are written only under `data/accounts/`; the root account-file path is read-only legacy compatibility.
- `CpaConversionService` owns CPA queue state and persistence. It never writes panel job state.
- `RegistrationJobManager` owns registration process and batch state. Flask routes only invoke its interface.
