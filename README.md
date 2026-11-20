# UptimeWatch

UptimeWatch is a website availability dashboard. The React and Vite frontend uses a Flask API backed by MongoDB Atlas. Atlas stores registered targets, check history, rate limits, and admin accounts and sessions.

- **Check Website** runs a one-time check of a public HTTPS URL without adding it to monitoring history.
- **Monitored Websites** lists enabled targets and lets visitors run a check that saves a result. The dashboard shows recent results, observed uptime, and response times.
- **Admin** lets one administrator sign in, add targets, and enable or disable monitoring. The app uses password hashing, server-side sessions, and CSRF protection for admin changes.

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

Open [the local dashboard](http://localhost:5173) and select **Admin** to sign in. Vite proxies API requests to Flask at `http://127.0.0.1:5000`. Leave `VITE_API_URL` unset for local development.

## Local checks

Run the backend unit tests from `backend`. The tests mock database and outbound network operations.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

From `frontend`, run `node --test tests/vercel.test.mjs` to validate the rewrite configuration and `npm run build` to verify the production bundle.

## Render and Vercel setup

The repository contains the production entrypoint and rewrites to the live Render backend. Verify the Vercel project and admin flow after import. Use a separate staging database when testing changes that write data.

### Render backend

- Service type and runtime — Python web service, Python 3.13.
- Root directory — `backend`.
- Build command — `python -m pip install -r requirements.txt`.
- Start command — `gunicorn --bind "0.0.0.0:$PORT" run:app`.
- Health check path — `/health`. This checks Flask liveness. Use `/api/targets` separately to verify Atlas access.
- Environment variable names — `UPTIMEWATCH_ENV`, `UPTIMEWATCH_FRONTEND_ORIGIN`, `MONGODB_URI`, `MONGODB_DB_NAME`, and optionally `ADMIN_API_TOKEN` for CLI clients. Render supplies `PORT`.

Set `UPTIMEWATCH_ENV` to `production` and `UPTIMEWATCH_FRONTEND_ORIGIN` to the exact stable Vercel HTTPS origin. Missing or invalid production origins disable browser admin access. Set `MONGODB_URI` as a Render secret. The database name defaults to `uptimewatch`. Bootstrap the admin once against the intended database using `python bootstrap_admin.py` from an authorized interactive terminal with its own narrowly scoped Atlas access. Enter the password at the prompt. [Render Flask guide](https://render.com/docs/deploy-flask)

Production uses shared rate-limit buckets per operation for login (5/minute), one-time checks (5/minute), and registered checks (6/minute), alongside the existing global limits and registered-target cooldown. Flask ignores forwarded client-address headers. These quotas are shared by all visitors until a trusted ingress and a verified client-IP policy are in place.

### Vercel frontend

- Framework preset and runtime — Vite, Node.js 24.
- Root directory — `frontend`.
- Install command — `npm ci`.
- Build command — `npm run build`.
- Output directory — `dist`.
- Frontend environment — leave `VITE_API_URL` unset in Vercel. The static rewrites require no backend-origin environment variable.

`frontend/vercel.json` rewrites `/api/*` and `/health` to `https://uptime-watch-q67r.onrender.com`. The browser uses relative URLs on the Vercel origin for API calls, cookies, and CSRF-protected requests. `VITE_API_URL` is optional in local development. Keep MongoDB settings, admin credentials, and tokens off Vercel and out of `VITE_*` variables. For previews that need admin access, point their rewrites at a separate staging backend configured for the exact frontend HTTPS origin. [External rewrites](https://vercel.com/docs/routing/rewrites)

After deployment is authorized, smoke-test through the Vercel URL. Check `/health` and `/api/targets`, run a one-time check against an approved test URL, then sign in, reload, change a staging target, and sign out. Confirm the session cookie is `Secure`, `HttpOnly`, `SameSite=Lax`, host-only, and scoped to `/`. Confirm admin responses are `no-store`, and a target change without valid CSRF is rejected. Keep browser admin access disabled if the hosted proxy fails to preserve the expected origin, cookie, or CSRF behavior.

## Monitoring worker

The workflow is manual-only, with read-only repository permissions, a 10-minute job timeout, and one active monitoring job at a time. Configure `MONGODB_URI` as an Actions secret. Set `MONGODB_DB_NAME` as an optional secret or repository variable. The secret takes precedence and the default is `uptimewatch`.

Each run processes up to 10 due, enabled targets. An offline result is a saved observation. Disabled or rate-limited targets are skipped, and an unsafe destination, database failure, or transport setup failure makes the job fail. Runs with no due targets can succeed without saving anything. With more than 10 due targets, a single run cannot sample them all. GitHub schedules can also run late.

Keep the 15-minute UTC cron example commented out until an authorized manual dispatch from `main` uses the approved runner route and saves at least one result in the intended database. Use a dedicated worker credential and an enabled staging target you have permission to check. Confirm the saved timestamp and status in the dashboard before considering a separate change to enable scheduling.

## Atlas access

- Use separate database users for Render and the worker, scoped to the intended database. A database-scoped `readWrite` role supports the application and index creation. A custom worker role can narrow that to reading/updating `monitoring_targets`, inserting `monitoring_checks`, and reading/upserting `monitoring_rate_limits`. The web app also writes `admin_accounts` during bootstrap and creates unique or TTL indexes for targets, sessions, and rate limits. [Atlas database roles](https://www.mongodb.com/docs/atlas/security-add-mongodb-roles/)
- Allowlist Render's service outbound CIDRs under **Connect → Outbound**. These ranges are shared within the region. Exclusive dedicated egress requires an eligible paid workspace and an additional monthly fee. [Render outbound IPs](https://render.com/docs/outbound-ip-addresses) · [Dedicated IPs](https://render.com/docs/dedicated-ips)
- The workflow's current `ubuntu-latest` runner has no dedicated static egress. Before dispatch, route the job through a runner with approved fixed egress or private network access. Static-IP GitHub larger runners require an eligible organization plan and are metered, including for public repositories. A separate private automation repository with an isolated self-hosted runner and fixed NAT egress is another option, with compute, networking, and maintenance costs. Keep self-hosted runners away from untrusted public workflows. [GitHub runner IP guidance](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#ip-addresses) · [Larger runners](https://docs.github.com/en/actions/concepts/runners/larger-runners) · [Secure use](https://docs.github.com/en/actions/reference/security/secure-use)
- Allowlist only approved egress ranges. Private endpoints require a compatible network and an Atlas dedicated cluster at M10 or higher. Keep Atlas public access restricted and defer the manual workflow run while runner egress is unresolved. [Atlas IP access list](https://www.mongodb.com/docs/atlas/security/ip-access-list/) · [Atlas private endpoints](https://www.mongodb.com/docs/atlas/security-private-endpoint/)

GitHub Actions scheduling remains disabled.
