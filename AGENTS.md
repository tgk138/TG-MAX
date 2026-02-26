# AGENTS.md

## Cursor Cloud specific instructions

### Overview

TG→MAX is a Python 3.12 FastAPI + Celery application for migrating Telegram channel content to the MAX messenger. See `README.md` for the full stack description and standard commands.

### Infrastructure services

PostgreSQL 16 and Redis 7 are required. Start them via:

```bash
sudo docker compose up -d postgres redis
```

Both services expose their default ports (5432, 6379) on localhost.

### Database bootstrap

The Alembic migrations are incremental — they assume the base schema already exists. On a fresh database, run the schema bootstrapper **before** Alembic:

```bash
cd src && python3 -m app.db_init
cd /workspace && alembic stamp head
```

After that, future migrations can be applied normally with `alembic upgrade head`.

### Running the app

```bash
uvicorn app.main:app --reload --app-dir src --host 0.0.0.0 --port 8000
```

The `.env` file must exist in the workspace root (copy from `.env.example` and set `DATABASE_URL` and `REDIS_URL` to localhost). `MEDIA_ROOT` should point to an existing directory (e.g. `/tmp/tgmax_media`).

### Lint / Test / Build

- **Lint:** `ruff check .` (6 pre-existing warnings in the codebase)
- **Tests:** `pytest` (all tests mock the DB, no running PostgreSQL/Redis required for tests)
- **Build:** No separate build step; the app runs directly via uvicorn

### Gotchas

- Pip installs to `~/.local/bin` by default; ensure it is on `PATH` (`export PATH="$HOME/.local/bin:$PATH"`).
- The `MEDIA_ROOT` directory must exist before the app starts (it mounts a `StaticFiles` directory at startup).
- Celery workers require Redis to be running but are not needed for basic API/UI testing.
