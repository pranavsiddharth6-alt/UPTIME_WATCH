# UptimeWatch

UptimeWatch is a website availability dashboard. The React and Vite frontend uses a Flask API backed by MongoDB Atlas. Atlas stores registered targets, check history, rate limits, accounts, and sessions.

- **Check Website** runs a one-time check of a public HTTPS URL without adding it to monitoring history.
- **Monitored Websites** lists enabled targets and lets visitors run a check that saves a result. The dashboard shows recent results, observed uptime, and response times.
- **Sign In** accepts the administrator's bootstrapped UptimeWatch credentials or Google sign-in for regular users. The backend assigns the role. Only the administrator sees **Manage Targets** and can register, enable, or disable targets.

## Local setup

Use Python 3.13, Node.js 24 with npm, and a development MongoDB Atlas database. In Atlas, create a database user for the development database and allow access from your current IP.

From the project root, set up the backend in PowerShell.

```powershell
cd backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

In `backend/.env`, replace the `MONGODB_URI` placeholder with your development Atlas connection string. Set `MONGODB_DB_NAME` to your development database, keep `UPTIMEWATCH_ENV` set to `development`, and use `http://localhost:5173` as `UPTIMEWATCH_FRONTEND_ORIGIN`. Leave `ADMIN_API_TOKEN` blank for browser-only administration. Keep `.env` private.

Create the single admin account once, then start Flask. The interactive bootstrap prompts for the username and password.

```powershell
.\.venv\Scripts\python.exe bootstrap_admin.py
.\.venv\Scripts\python.exe run.py
```

In a second terminal, from the project root, start Vite.

```powershell
cd frontend
npm ci
npm run dev
```

Open the local dashboard and select **Sign In**. Use the UptimeWatch username and password created by `bootstrap_admin.py` for management access. Vite proxies API requests to Flask at `http://127.0.0.1:5000`. Leave `VITE_API_URL` unset for local development. [Local dashboard](http://localhost:5173)

## Local checks

Run the backend unit tests from `backend`. The tests mock database and outbound network operations.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

From `frontend`, run `node --test tests/account.test.mjs tests/vercel.test.mjs` to check role-based navigation, account states, and the Vercel configuration. Run `npm run build` to verify the production bundle.

## Google sign-in setup

1. In Google Cloud Console, select a project and go to **Google Auth Platform → Branding**. Configure the consent screen with the app name, support email, homepage, authorized domain, and privacy policy. Under **Audience**, choose **External** for public Google accounts. Configure access for your test accounts if Google requires it before publishing.
2. Under **Clients**, create an OAuth client of type **Web application**. Add `http://localhost` and `http://localhost:5173` as authorized JavaScript origins for local development. Add the exact stable Vercel production origin from the project's **Domains** page, including `https://` and without a path. If Google requires domain verification for publication, use a domain you control and update the authorized origin in Google and Render together.
3. Leave **Authorized redirect URIs** empty for this integration. The official Google button uses a popup and a JavaScript callback. It requests identity only (`openid`, `email`, and `profile`) and uses no Google API access or refresh token.
4. Set `GOOGLE_CLIENT_ID` on Render and optionally in `backend/.env` for authorized local testing. Use the corresponding Web client ID for each environment. Flask publishes only that public ID with the short-lived challenge, so Vercel needs no Google variable. Leave `GOOGLE_CLIENT_ID` empty to keep password sign-in available with a Google sign-in unavailable message. Keep the OAuth client secret out of the app and browser. [Google Identity Services setup](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid)

Flask verifies Google's ID-token signature, issuer, audience, expiry, verified email, and nonce. The nonce is tied to an HttpOnly cookie and consumed once. Google accounts are keyed by Google's stable `sub`, and every Google account has the `user` role, even if its email matches the local administrator. Both sign-in methods use the same four-hour MongoDB-backed session and CSRF protection. Existing admin accounts and active sessions remain valid.

- `POST /api/auth/local` signs in with the bootstrapped username and password.
- `POST /api/auth/google/challenge` starts an attempt. `POST /api/auth/google` authenticates the resulting ID token.
- `GET /api/auth/session` returns the current profile, role, expiry, and CSRF token. `POST /api/auth/logout` requires the session's CSRF token and revokes it.
- `/api/admin/targets` and target registration, enable, disable, and deletion require administrator authorization. The original admin endpoints remain compatible. The optional `ADMIN_API_TOKEN` is server-only for CLI use.

## Render and Vercel setup

The repository contains the production entrypoint and rewrites to the live Render backend. Verify the Vercel project and admin flow after import. Use a separate staging database when testing changes that write data.

### Render backend

- Service type and runtime — Python web service, Python 3.13.
- Root directory — `backend`.
- Build command — `python -m pip install -r requirements.txt`.
- Start command — `gunicorn --bind "0.0.0.0:$PORT" run:app`.
- Health check path — `/health`. This checks Flask liveness. Use `/api/targets` separately to verify Atlas access.
- Environment variable names — `UPTIMEWATCH_ENV`, `UPTIMEWATCH_FRONTEND_ORIGIN`, `MONGODB_URI`, `MONGODB_DB_NAME`, and optionally `GOOGLE_CLIENT_ID`, `ADMIN_API_TOKEN`, and `WORKER_TRIGGER_SECRET`. Render supplies `PORT`.

Set `UPTIMEWATCH_ENV` to `production` and `UPTIMEWATCH_FRONTEND_ORIGIN` to the exact stable Vercel HTTPS origin. Missing or invalid production origins disable browser sign-in. Set `MONGODB_URI` as a Render secret. The database name defaults to `uptimewatch`. Bootstrap the admin once against the intended database using `python bootstrap_admin.py` from an authorized interactive terminal with its own narrowly scoped Atlas access. Enter the password at the prompt. [Render Flask guide](https://render.com/docs/deploy-flask)

Production uses shared rate-limit buckets per operation for password login (5/minute), Google challenges and verification (20/minute each), one-time checks (5/minute), and registered checks (6/minute), alongside the existing global limits and registered-target cooldown. Flask ignores forwarded client-address headers. These quotas are shared by all visitors until a trusted ingress and a verified client-IP policy are in place.

### Vercel frontend

- Framework preset and runtime — Vite, Node.js 24.
- Root directory — `frontend`.
- Install command — `npm ci`.
- Build command — `npm run build`.
- Output directory — `dist`.
- Frontend environment — leave `VITE_API_URL` unset in Vercel. The static rewrites require no backend-origin environment variable.

`frontend/vercel.json` rewrites `/api/*` and `/health` to `https://uptime-watch-q67r.onrender.com`. The browser uses relative URLs on the Vercel origin for API calls, cookies, and CSRF-protected requests. `VITE_API_URL` is optional in local development. Keep MongoDB settings, admin credentials, and tokens off Vercel and out of `VITE_*` variables. For previews that need admin access, point their rewrites at a separate staging backend configured for the exact frontend HTTPS origin. [External rewrites](https://vercel.com/docs/routing/rewrites)

After deploying both tiers, smoke-test through the Vercel URL with an authorized test account before public rollout. Check `/health` and `/api/targets`, run a one-time check against an approved test URL, then test both sign-in methods. Reload each account to verify session persistence. Confirm only the password account sees **Manage Targets**, Google sessions get `403` from management APIs, and requests without valid CSRF cannot make protected changes or sign out. Verify that sign-out clears the session. Confirm the session cookie is `Secure`, `HttpOnly`, `SameSite=Lax`, host-only, and scoped to `/`, and that account responses use `no-store`.

## Monitoring workflow

GitHub Actions sends one signed HTTPS `POST` to `https://uptime-watch-q67r.onrender.com/internal/monitor/run`. Render authenticates the request before launching a Linux watchdog-bounded child process that reuses the existing worker and approved-target checks. Each dispatch processes at most **one** due, enabled target to leave room for MongoDB operations and the five-second HTTPS probe below Gunicorn's 30-second sync timeout. Render supervises the child for at most 18 seconds and its OS alarm terminates monitoring after 17 seconds. Standalone `backend/monitor_worker.py` remains available for local maintenance and shares the existing per-target cooldown.

The trigger accepts an empty body. It requires `X-UptimeWatch-Timestamp` (Unix seconds, within ±120 seconds), `X-UptimeWatch-Request-Id` (32 lowercase hex characters), and `X-UptimeWatch-Signature` (lowercase hex HMAC-SHA256). Sign the ASCII bytes `v1\nPOST\n/internal/monitor/run\n<timestamp>\n<request-id>\n` followed by the exact empty body. MongoDB keeps each accepted request ID for five minutes and grants one dispatch lease at a time. The 60-second lease outlives the child deadline and expires after a crash. Duplicate and overlapping requests receive HTTP `409` before any probe. The only results returned are status codes and short status labels.

### Scheduled runs

The workflow uses `7,22,37,52 * * * *` to run about four times an hour, at minutes 07, 22, 37, and 52 UTC. Scheduled runs use the latest commit on the default branch. GitHub may delay or skip runs. Each successful dispatch checks at most one due, enabled website, so several targets can mean several intervals between checks for each site. [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

To pause scheduled runs, remove or comment out the `schedule:` block in `.github/workflows/monitor.yml` on the default branch. Keep `workflow_dispatch:` to retain manual runs.

### Manual setup and verification

1. Generate a dedicated key privately with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Use a fresh key for this environment and keep it out of chat, Git, screenshots, and logs.
2. Add the same value as `WORKER_TRIGGER_SECRET` in the Render service's **Environment** and in GitHub **Settings → Secrets and variables → Actions → Repository secrets**. Keep Render's `MONGODB_URI` and optional `MONGODB_DB_NAME` server-side. The workflow needs only `WORKER_TRIGGER_SECRET` and no repository variables. Verify Render is running the intended commit, its database user can create the TTL index and write to `monitoring_dispatch`, and Gunicorn uses the documented sync start command. If one sync worker delays dashboard traffic during testing, add a second worker if the service has enough memory.
3. Select an enabled, due test target you have permission to monitor. Open **Actions → UptimeWatch monitoring → Run workflow**, select `main`, and run it once after authorization. The workflow has a four-minute job deadline, a 100-second HTTPS socket timeout, and no automatic retry. Free Render instances can sleep after 15 minutes idle and can take about a minute to wake. [Render free instance behavior](https://render.com/docs/free)
4. Check that the job finishes with `Monitoring dispatch: saved.` and a new dashboard history entry for the expected test target and run time. An `offline` result can still be a saved observation. `no_due_targets` and `skipped` are successful dispatches without a new observation. If the run timed out, inspect history before trying again because the result may have been saved before the response was interrupted. Confirm normal dashboard traffic and admin/Google sign-in still work through the canonical Vercel origin.

## Atlas access

- Scope the Render user to the intended database. A database-scoped `readWrite` role supports target and history management, session and challenge storage, and the dispatch collection's replay and lease operations. Custom roles must allow `createIndex` on `monitoring_dispatch` in addition to insert, update, and delete. These additions require no destructive migration. [Atlas database roles](https://www.mongodb.com/docs/atlas/security-add-mongodb-roles/)
- Allowlist Render's service outbound CIDRs under **Connect → Outbound**. These ranges are shared within the region. Exclusive dedicated egress requires an eligible paid workspace and an additional monthly fee. [Render outbound IPs](https://render.com/docs/outbound-ip-addresses) · [Dedicated IPs](https://render.com/docs/dedicated-ips)
- Keep Atlas restricted to approved egress. GitHub-hosted runners reach the Render HTTPS service and need no Atlas IP access entries or MongoDB secrets. Retire any obsolete GitHub Actions database settings once this workflow is in use. [Atlas IP access list](https://www.mongodb.com/docs/atlas/security/ip-access-list/)
