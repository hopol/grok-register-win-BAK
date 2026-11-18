# temp-email-worker

Cloudflare Email Routing Worker used by the local-domain mailbox provider.

The worker stores each mailbox under `email:<address>` in the existing `EMAILS` KV namespace and exposes the management API consumed by `https://tempmail.liziai.cloud/`.

Required secret:

```powershell
npx wrangler secret put ADMIN_TOKEN
```

Deploy from this directory:

```powershell
npx wrangler deploy
```

The Cloudflare deployment history retains previous versions for rollback.
