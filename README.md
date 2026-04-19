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
