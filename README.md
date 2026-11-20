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

Run `npm run build` from `frontend` to verify the production bundle.

## Monitoring worker

`backend/monitor_worker.py` runs one pass over up to 10 due, enabled targets and saves results to the configured database. The GitHub Actions workflow supports manual dispatch and requires the `MONGODB_URI` Actions secret. `MONGODB_DB_NAME` can be set as a repository variable and defaults to `uptimewatch`.

GitHub Actions scheduling and cloud deployment are not enabled yet.
