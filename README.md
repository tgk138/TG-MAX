# TG→MAX

**Сервис миграции контента из Telegram в мессенджер MAX.**

Переносите каналы и группы из Telegram в MAX через единый `/setup`: авторизация Telegram (по номеру или QR), подключение MAX, выбор каналов, запуск импорта и публикации. Порядок постов и альбомы сохраняются.

## Возможности

- Подключение Telegram (код в приложении или вход по QR-коду, поддержка 2FA)
- Выбор канала-источника из списка доступных
- Фоновый импорт постов и медиа с группировкой альбомов
- Подключение бота MAX по токену и выбор целевого чата
- Публикация в MAX с повторными попытками при сбоях

## Стек

- **Backend:** FastAPI, SQLAlchemy (async), Celery, Redis, PostgreSQL
- **Telegram:** Telethon (MTProto)
- **MAX:** HTTP API (platform-api.max.ru)
- **UI:** Jinja2, HTMX

## Быстрый старт

### Требования

- Python 3.12+
- PostgreSQL
- Redis
- Учётные данные Telegram API (api_id, api_hash с [my.telegram.org](https://my.telegram.org))

### Установка

```bash
# Клонирование и переход в каталог
cd TG&MAX

# Виртуальное окружение (Windows)
python -m venv venv
venv\Scripts\activate

# Зависимости
pip install -e .   # из корня проекта (pyproject.toml)

# Конфигурация
cp .env.example .env
# Отредактируйте .env: DATABASE_URL, REDIS_URL, TELEGRAM_API_ID, TELEGRAM_API_HASH, SECRET_KEY
```

### Запуск (локально)

```bash
# PostgreSQL и Redis должны быть запущены

# Применение миграций БД (обязательно перед стартом API/worker)
alembic upgrade head

# API и веб-интерфейс
uvicorn app.main:app --reload --app-dir src

# Воркеры Celery (в отдельных терминалах)
cd src && celery -A app.celery_app worker -l info -Q import --concurrency=4
cd src && celery -A app.celery_app worker -l info -Q publish --concurrency=2
cd src && celery -A app.celery_app beat -l info
```

Сервис будет доступен по адресу http://127.0.0.1:8000 .

### Тесты

```bash
pip install -e ".[dev]"
pytest
```

Запуск из корня репозитория; путь `src` для импорта `app` задаётся в `pyproject.toml` (pytest.ini_options.pythonpath).

### Запуск через Docker

```bash
docker compose up -d
```

API и UI — порт 8000, import/publish воркеры и БД/Redis поднимаются композом.
Перед запуском API/worker внутри контейнеров автоматически выполняется `alembic upgrade head`.

## Конфигурация (.env)

| Переменная | Описание |
|------------|----------|
| `DATABASE_URL` | PostgreSQL (async driver: `postgresql+asyncpg://...`) |
| `REDIS_URL` | Redis для Celery |
| `SECRET_KEY` | Ключ шифрования (Fernet) для сессий Telegram |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | [my.telegram.org](https://my.telegram.org) |
| `MEDIA_ROOT` | Каталог для загруженных медиа |
| `MAX_API_BASE` | Базовый URL API MAX (по умолчанию https://platform-api.max.ru) |

Остальные параметры см. в `.env.example`.

## Структура проекта

- `src/app/` — приложение FastAPI
  - `api/` — роутеры (telegram, max, migrations, pages)
  - `models/` — SQLAlchemy-модели
  - `services/` — Telegram (Telethon), MAX API, хранилище, шифрование
  - `worker/` — задачи Celery (импорт, публикация)
  - `templates/` — Jinja2-шаблоны
- `scripts/` — вспомогательные скрипты (например, отправка кода Telegram)
- `docs/` — документация и планы

## Лицензия и продукт

TG→MAX — продукт для переноса контента между Telegram и MAX. Использование сервиса регламентируется [Пользовательским соглашением](/terms) и [Конфиденциальностью](/privacy). По вопросам поддержки см. [Поддержка](/support).
