# radar-backend

Compliance Radar worker backend.

## Local Setup

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Set `DATABASE_DSN_RADAR` in `.env` before running the worker.

Run one worker cycle:

```bash
python -m radar_backend.worker.runner --once
# or
radar-worker --once
```

Run continuously:

```bash
python -m radar_backend.worker.runner
# or
radar-worker
```

The worker loads environment variables from `.env` by default.

Run tests:

```bash
python -m pytest
```
