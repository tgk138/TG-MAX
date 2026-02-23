# Telegram -> MAX Migration Service: План реализации

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Построить SaaS-сервис для миграции контента из Telegram-каналов в MAX мессенджер.

**Architecture:** FastAPI + Celery + PostgreSQL + Redis (broker) + Jinja2/HTMX UI. Два независимых процесса: импорт (Telethon) и публикация (MaxClient на httpx). Docker Compose с 5 сервисами.

**Tech Stack:** Python 3.12, FastAPI, Celery, PostgreSQL, Redis, Telethon, httpx, Jinja2, HTMX, SQLAlchemy, Alembic, Docker

---

## Task 1: Скелет проекта и конфигурация

**Files:**
- Create: `src/app/__init__.py`
- Create: `src/app/config.py`
- Create: `src/app/main.py`
- Create: `src/app/celery_app.py`
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `Dockerfile`
- Create: `docker-compose.yml`

**Step 1: Создать структуру директорий**

```
src/
  app/
    __init__.py
    config.py
    main.py
    celery_app.py
    api/
      __init__.py
    models/
      __init__.py
    services/
      __init__.py
    templates/
      base.html
    worker/
      __init__.py
tests/
  __init__.py
```

**Step 2: pyproject.toml с зависимостями**

Зависимости: fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, celery[redis], redis, telethon, httpx, jinja2, python-multipart, cryptography, pydantic-settings

**Step 3: config.py — Pydantic Settings**

Все env-переменные из раздела 2.7 ТЗ.

**Step 4: main.py — FastAPI app**

Минимальный app с healthcheck `GET /health`.

**Step 5: celery_app.py — Celery**

Celery app с Redis broker из config.

**Step 6: .env.example**

**Step 7: Dockerfile (multi-stage)**

**Step 8: docker-compose.yml (5 сервисов)**

nginx, api, worker, postgres, redis + volumes для медиа.

**Step 9: Коммит**

```
git add -A && git commit -m "feat: project skeleton with FastAPI, Celery, Docker Compose"
```

---

## Task 2: Модели БД и миграции (Alembic)

**Files:**
- Create: `src/app/models/base.py`
- Create: `src/app/models/user.py`
- Create: `src/app/models/tg_connection.py`
- Create: `src/app/models/max_connection.py`
- Create: `src/app/models/max_target.py`
- Create: `src/app/models/migration.py`
- Create: `src/app/models/tg_post.py`
- Create: `src/app/models/tg_media.py`
- Create: `src/app/models/publish_unit.py`
- Create: `src/app/models/job_event.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`

Все 9 таблиц из раздела 2.5 ТЗ. SQLAlchemy 2.0 mapped_column. Enum types. Unique constraints для idempotency.

**Step 1: base.py — Base, async engine, session**

**Step 2: Модели по одной (users, tg_connections, max_connections, max_targets, migrations, tg_posts, tg_media, publish_units, job_events)**

**Step 3: Alembic init + первая миграция**

```
alembic init alembic
alembic revision --autogenerate -m "initial schema"
```

**Step 4: Коммит**

---

## Task 3: secrets.py — шифрование секретов

**Files:**
- Create: `src/app/services/secrets.py`
- Create: `tests/test_secrets.py`

Fernet encryption. Методы: `encrypt(plaintext) -> str`, `decrypt(ciphertext) -> str`. Использует SECRET_KEY из config.

---

## Task 4: Storage Layer

**Files:**
- Create: `src/app/services/storage.py`
- Create: `tests/test_storage.py`

ABC `StorageBackend` с методами save/read/delete/exists. `LocalStorage` реализация. Ключи: `{migration_id}/{media_id}/{filename}`.

---

## Task 5: MAX Client (httpx)

**Files:**
- Create: `src/app/services/max_client.py`
- Create: `tests/test_max_client.py`

8 методов из дизайн-документа. Async httpx. Retry с backoff для `attachment.not.ready`. Rate limiting.

---

## Task 6: Telegram Service (Telethon)

**Files:**
- Create: `src/app/services/telegram.py`
- Create: `tests/test_telegram.py`

TelegramService: send_code, sign_in_code, sign_in_password, list_channels, export_channel. Группировка альбомов по grouped_id.

---

## Task 7: State Machine миграции

**Files:**
- Create: `src/app/services/migration_sm.py`
- Create: `tests/test_migration_sm.py`

Транзакционные переходы: draft→importing→imported→publishing→done, fail из importing/publishing.

---

## Task 8: Celery tasks (import + publish)

**Files:**
- Create: `src/app/worker/tasks.py`
- Create: `tests/test_tasks.py`

`import_channel(migration_id)` и `publish_to_max(migration_id)`. Rate limits, счётчики, job_events.

---

## Task 9: FastAPI API endpoints

**Files:**
- Create: `src/app/api/telegram.py`
- Create: `src/app/api/max.py`
- Create: `src/app/api/migrations.py`
- Create: `src/app/api/deps.py`

15 эндпоинтов из раздела 2.8 ТЗ.

---

## Task 10: Jinja2 + HTMX UI

**Files:**
- Create: `src/app/templates/base.html`
- Create: `src/app/templates/connect_tg.html`
- Create: `src/app/templates/select_channel.html`
- Create: `src/app/templates/connect_max.html`
- Create: `src/app/templates/migration.html`
- Create: `src/app/api/pages.py`

4 страницы из раздела 2.13 ТЗ. HTMX polling для прогресса.

---

## Task 11: Nginx + финальная сборка Docker

**Files:**
- Create: `nginx/nginx.conf`
- Modify: `docker-compose.yml`

Reverse proxy, static files, проверка всех 5 контейнеров.
