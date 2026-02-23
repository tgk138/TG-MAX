# Дизайн: Telegram -> MAX Migration Service

**Дата:** 2026-02-22
**Статус:** Одобрен

---

## 1. Обзор

SaaS-сервис для переноса контента из Telegram-каналов в MAX мессенджер.
Модель монетизации: 5000 руб. за перенос канала, в будущем -- подписка на репост новых постов.

## 2. Архитектура

```
Browser (Jinja2+HTMX)
    |
Nginx/Caddy (reverse proxy)
    |
FastAPI API + Jinja2 UI --> Postgres (источник истины)
    |
    | enqueue
    v
Redis (только брокер, без состояния)
    |
    | consume
    v
Celery Worker (import/publish) --> Storage Layer (disk сейчас, S3 потом)
    |
    +--- Telegram MTProto (Telethon)
    +--- MAX Bot API (свой httpx-клиент)
```

**Docker-сервисы:** nginx, api, worker, postgres, redis (5 контейнеров)

### Ключевые правила

1. **Postgres = источник истины** -- все статусы, прогресс, идемпотентность
2. **Redis = только брокер** -- никакого состояния
3. **Storage Layer** -- абстракция: `LocalStorage` сейчас, `S3Storage` потом
4. **State machine миграции** с транзакционными переходами
5. **Rate limits в воркере** -- concurrency=1 на миграцию, задержки между запросами
6. **Шифрование секретов** через единый сервис `secrets.py`

## 3. MAX API клиент

Свой `MaxClient` на httpx (~8 методов):

| Метод | Эндпоинт | Назначение |
|-------|----------|------------|
| `get_me()` | `GET /me` | Проверка токена |
| `get_chats()` | `GET /chats` | Список чатов бота |
| `get_chat(id)` | `GET /chats/{id}` | Детали чата |
| `get_chat_membership(id)` | `GET /chats/{id}/members/me` | Проверка прав |
| `request_upload(type)` | `POST /uploads` | Получить URL загрузки |
| `upload_file(url, path)` | `PUT <upload_url>` | Загрузить файл |
| `send_message(chat_id, ...)` | `POST /messages` | Отправить сообщение |
| `get_message(id)` | `GET /messages/{id}` | Проверить отправку |

**Базовый URL:** `https://platform-api.max.ru`
**Авторизация:** заголовок `Authorization: <token>`

### Особенности загрузки файлов

1. `POST /uploads?type=image|video|audio|file` -> получить `{url, token}`
2. Загрузить файл по `url` (multipart, макс 4 ГБ)
3. Для video/audio: использовать `token` в attachments
4. Ошибка `attachment.not.ready` -- retry с backoff (0.5s, 1s, 2s, 4s)

## 4. State Machine миграции

```
draft --[start_import]--> importing --[complete]--> imported
                              |                        |
                              +--[fail]--> failed       |
                                            ^     [start_publish]
                                            |          |
                                            +-------- publishing --[complete]--> done
```

Переходы -- только через транзакцию в Postgres с проверкой текущего статуса.

## 5. Data flow

### Импорт (Telegram -> Postgres + Storage)

1. API: проверяет status=draft, транзакционно ставит status=importing
2. Enqueue celery task `import_channel(migration_id)`
3. Worker: Telethon iter_messages -> группировка по grouped_id -> tg_posts + tg_media
4. Скачивание медиа -> StorageLayer.save()
5. Обновление счётчиков в Postgres
6. По завершении: status=imported

### Публикация (Postgres + Storage -> MAX)

1. API: проверяет status=imported, ставит status=publishing
2. Enqueue celery task `publish_to_max(migration_id)`
3. Worker: SELECT publish_units WHERE status=pending ORDER BY order_index, unit_index
4. Для каждого unit: upload медиа -> send_message -> update status=sent
5. Idempotency key: `(migration_id, order_index, unit_index)` с уникальным индексом
6. Все units sent -> status=done

## 6. Изменения относительно исходного ТЗ

| Было | Стало | Причина |
|------|-------|---------|
| Long polling GET /updates для целей | GET /chats напрямую | API MAX поддерживает |
| React/Vue/Next фронтенд | Jinja2 + HTMX | MVP, меньше компонентов |
| MaxClient 3 метода | MaxClient ~8 методов (httpx) | Полное покрытие |
| Медиа на диск напрямую | StorageLayer абстракция | disk/S3 |
| Без reverse proxy | Nginx/Caddy | Прод-готовность |
| Без мониторинга | Flower + structured logs | Наблюдаемость |
| Без лога событий | Таблица job_events | Детальный прогресс |

## 7. Дополнения по итогам ревью

1. **Celery Beat** -- добавить если появятся периодические задачи (retry sweep, cleanup)
2. **Таблица `job_events`** -- лог ошибок и прогресса по шагам, не только counters
3. **Idempotency key** -- `(migration_id, order_index, unit_index)` уникальный индекс
4. **Dead-letter** -- failed задачи с ручным retry из UI
5. **Rate limits** -- на Telegram (Telethon) и MAX (httpx), worker concurrency=1..N
6. **secrets.py** -- единый сервис шифрования для Telegram session и MAX token
