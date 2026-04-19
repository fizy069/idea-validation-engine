# OASIS Backend

Python backend for market idea simulation, built on OASIS + FastAPI.

The project is now **backend-first** and no longer supports the old CLI flow.

## What It Exposes

- `GET /health`
- `POST /simulate/market`
- `GET /result/{slug}`
- `GET /result/{slug}/interviews`

Request/response contract details are documented in `backend_plan.md`.

## Requirements

- Python **3.10** or **3.11** (`camel-oasis` requires `<3.12`)
- OpenAI API key in environment

## Setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

Create `.env` in project root:

```dotenv
OPENAI_API_KEY=sk-...
# Optional, must be HTTPS if set
# OPENAI_API_BASE_URL=https://api.openai.com/v1

# Optional backend config
FRONTEND_ORIGIN=http://localhost:3000
SQLITE_DB_PATH=./data/validations.db
MARKET_MODEL=gpt-4o-mini
JUDGE_MODEL=gpt-4o-mini
SIMULATE_RATE_LIMIT_MAX_REQUESTS=3
SIMULATE_RATE_LIMIT_WINDOW_SECONDS=60
```

## Run the Server

```powershell
uvicorn main:app --host 0.0.0.0 --port 8000
```

Base URL in local dev: `http://localhost:8000`

## Quick API Examples

Health:

```powershell
curl http://localhost:8000/health
```

Run simulation:

```powershell
curl -X POST http://localhost:8000/simulate/market `
  -H "Content-Type: application/json" `
  -d "{\"idea\":\"A SaaS that turns Slack threads into searchable docs\",\"targetUser\":\"Engineering managers at 50-500 person companies\",\"subreddit\":\"r/SaaS\",\"numVocal\":5,\"turns\":2}"
```

Fetch saved result:

```powershell
curl http://localhost:8000/result/<slug>
curl http://localhost:8000/result/<slug>/interviews
```

## Data Storage

- Completed runs are stored in SQLite (`SQLITE_DB_PATH`, default `./data/validations.db`).
- Temporary simulation DB files are created under `./data/runs/` during a run and removed after completion.
- Ensure `./data/` is on persistent disk in production.

## Error Contract

All errors use:

```json
{ "error": "<machine_code>", "message": "<human-readable>" }
```

Main codes used:

- `invalid_request` (`400`)
- `invalid_slug` (`400`)
- `not_found` (`404`)
- `rate_limited` (`429`, includes `Retry-After`)
- `simulation_failed` (`500`)

## Smoke Test

Run offline API smoke tests (no OASIS/OpenAI calls):

```powershell
python tests/smoke_test.py
```

## Deploy to Render (Free)

This repo includes a `render.yaml` blueprint, `Procfile`, and `runtime.txt`
that make it deployable to Render's free tier in a couple of clicks.

### One-time setup

1. Push this repo to GitHub (or GitLab).
2. Create a free account at https://render.com and connect your Git provider.
3. Click **New + → Blueprint**, pick the repo, and Render will detect
   `render.yaml` automatically. Confirm the plan.
4. When prompted, set the secret env vars (these have `sync: false` in
   `render.yaml`):
   - `OPENAI_API_KEY` — your OpenAI key (required)
   - `FRONTEND_ORIGIN` — comma-separated list of allowed origins for CORS,
     e.g. `https://your-frontend.vercel.app,https://www.example.com`
5. Click **Apply**. The first build takes ~3-5 minutes.

Your API will be live at `https://oasis-backend.onrender.com` (or whatever
name Render generates). Check `GET /health` to confirm.

### What the blueprint configures

- Python 3.11.9 runtime
- `pip install -r requirements.txt` build step
- `uvicorn main:app` start command bound to Render's `$PORT`
- A 1 GB persistent disk mounted at `/var/data` so the SQLite DB survives
  restarts and redeploys (`SQLITE_DB_PATH=/var/data/validations.db`)
- `/health` as the health-check path
- Sensible defaults for `MARKET_MODEL`, `JUDGE_MODEL`, and rate limits

### Free tier notes

- The free web service **sleeps after ~15 minutes of inactivity** and takes
  ~30-60s to wake up on the next request. The `/health` endpoint is the
  cheapest way to wake it.
- 750 instance hours/month are included.
- The persistent disk is free up to 1 GB on the free tier.
- Render terminates plain HTTP and serves your app over HTTPS automatically.

### Updating the deployment

Every push to your `main` branch triggers an auto-deploy (configured by
`autoDeploy: true` in `render.yaml`). To change env vars, edit them in the
Render dashboard under **Environment**.
