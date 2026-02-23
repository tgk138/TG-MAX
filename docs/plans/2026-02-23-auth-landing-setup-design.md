# Auth + Landing + Setup Page Design

**Date:** 2026-02-23
**Status:** Approved

---

## Overview

Redesign the TG→MAX web UI to:
1. Show a beautiful landing page to unauthenticated users
2. Provide a single unified `/setup` page for TG auth + MAX connection + channel selection
3. Implement real per-user session auth (cookie-based) using Telegram phone+code as the identity provider
4. Protect all app pages, redirect unauthenticated users to `/`

---

## 1. Authentication Architecture

### Approach: Session cookie + Postgres

- **Identity:** Telegram account (`tg_id bigint`) — sole identity provider
- **Session storage:** `user_sessions` table in Postgres
- **Transport:** httponly `session_key` cookie (30-day expiry)

### New DB table: `user_sessions`

```sql
id          uuid        PRIMARY KEY
user_id     uuid        REFERENCES users(id) ON DELETE CASCADE
session_key text        UNIQUE NOT NULL  -- 32 random bytes hex
created_at  timestamp   NOT NULL DEFAULT now()
expires_at  timestamp   NOT NULL         -- now() + 30 days
```

### Changes to `users` table

Add `tg_id bigint UNIQUE` — set on first successful TG login.

### Auth flow

```
POST /api/tg/send_code   → sends TG code (no session yet)
POST /api/tg/confirm_code → on authed:
    1. upsert User by tg_id
    2. create UserSession (random key, 30d expiry)
    3. set cookie session_key=<key>; HttpOnly; SameSite=Lax; Path=/
    4. return { authed: true }
POST /api/tg/confirm_password → same as confirm_code on success
POST /api/tg/qr_* → same on success

POST /api/auth/logout →
    1. delete UserSession by cookie value
    2. clear cookie
    3. redirect /
```

### `get_current_user` dependency

- Reads `session_key` cookie
- Looks up `user_sessions` by key where `expires_at > now()`
- Returns `User` or `None`
- Never auto-creates a user

### Route protection

| URL | Auth required | Behavior if unauthed |
|-----|--------------|---------------------|
| `/` | No | Show landing (if authed → redirect `/cabinet`) |
| `/setup` | No | Show setup (if authed → redirect `/cabinet`) |
| `/privacy`, `/terms`, `/support` | No | Show page |
| `/cabinet` | Yes | Redirect `/` |
| `/migration` | Yes | Redirect `/` |
| `/channels` | Yes | Redirect `/` |
| `/connect-tg` | Yes | Redirect `/` |
| `/connect-max` | Yes | Redirect `/` |

---

## 2. `/setup` Page — Unified Wizard

Single URL `/setup`. Multi-step accordion on one page. All steps use HTMX. JS only for accordion open/close state.

### Steps

```
[ ✓ ] 1. Войти через Telegram       ← closes after success
[ ▼ ] 2. Подключить MAX бота        ← opens after step 1
[   ] 3. Выбрать каналы             ← opens after step 2
```

**Step 1 — Telegram login:**
- Phone input → POST /api/tg/send_code
- Code input → POST /api/tg/confirm_code
- Optional password (2FA) → POST /api/tg/confirm_password
- QR-code alternative (existing flow)
- On success: session cookie set, accordion step 1 closes with ✓, step 2 opens

**Step 2 — MAX bot:**
- Token input → POST /api/max/connect
- On success: step 2 closes with ✓, step 3 opens
- "Пропустить" button → skip to step 3 (MAX can be added later from cabinet)

**Step 3 — Channel selection:**
- TG channel: [Загрузить каналы] GET /api/tg/channels → select
- MAX target: [Загрузить чаты] POST /api/max/sync_targets → GET /api/max/targets → select
- [Создать миграцию →] POST /api/migrations → redirect /cabinet
- "Пропустить" → redirect /cabinet directly

**Behavior on reload:**
- If user has valid session (cookie): step 1 shown as completed (✓), step 2 opens
- If MAX already connected: step 2 shown as completed (✓), step 3 opens

---

## 3. Landing Page (`/`)

Unauthenticated users see the landing. Authenticated users → redirect `/cabinet`.

### Sections

1. **Navbar** — logo "TG→MAX", [Войти] button → `/setup`
2. **Hero** — headline, subheadline, CTA [Начать бесплатно] → `/setup`
   - Three.js canvas background: animated particle network with floating TG+MAX icons
   - Mouse parallax on desktop
   - Static CSS gradient fallback on mobile (canvas disabled via `matchMedia`)
3. **How it works** — 3-column: 1. Войдите в TG / 2. Выберите каналы / 3. Готово
4. **Features** — 2×2 grid: Альбомы / Порядок постов / Все медиа / Resume после сбоя
5. **Pricing** — single card: 5 000 ₽ за перенос канала, [Начать]
6. **Footer** — Соглашение / Конфиденциальность / Поддержка

### 3D Implementation

- Vanilla Three.js via CDN `<script type="importmap">` — no npm/bundler
- Canvas `position: absolute` behind hero text, `pointer-events: none`
- Scene: ~200 particles (Points), connecting lines (LineSegments) within distance threshold
- Two sprites: Telegram logo + MAX logo, floating with sine animation
- Arrow particle stream between the two logos
- Mobile: `window.matchMedia('(max-width: 768px)')` → skip Three.js, show CSS gradient
- Lazy: canvas init deferred via `requestIdleCallback`

---

## 4. Cabinet (`/cabinet`)

Main page for authenticated users. Redesigned layout:

### Sections

1. **Navbar** — logo, [Кабинет] active, [Выйти] → POST /api/auth/logout
2. **Profile card** — TG avatar, full name, @username, phone
3. **Connections status**
   - Telegram: ✓ подключён (phone)
   - MAX: ✓ botname / [Подключить MAX →] → /connect-max
4. **Migrations list**
   - [+ Новая миграция] → /setup
   - Each row: channel title → MAX target, status badge, [→] link to /migration?mid=...

### New API endpoint

`GET /api/migrations` — returns list of user's migrations (id, tg_channel_title, max_target, status, created_at)

---

## 5. Alembic Migration

New migration file adds:
- `users.tg_id bigint UNIQUE`
- `user_sessions` table

Existing `get_current_user` default-user behavior removed. MVP single-user assumption dropped.

---

## 6. Files Changed

| File | Change |
|------|--------|
| `src/app/models/user.py` | add `tg_id` field |
| `src/app/models/user_session.py` | new model |
| `src/app/api/deps.py` | real session-based `get_current_user` |
| `src/app/api/telegram.py` | set cookie on auth success |
| `src/app/api/pages.py` | add auth guards + redirect logic + `/setup` route |
| `src/app/api/__init__.py` | register logout endpoint |
| `src/app/templates/base.html` | updated navbar (auth-aware) |
| `src/app/templates/home.html` | full redesign + Three.js |
| `src/app/templates/setup.html` | new unified wizard page |
| `src/app/templates/cabinet.html` | redesign with migrations list |
| `alembic/versions/xxxx_add_auth.py` | new migration |

---

## 7. Out of Scope

- Email/password auth
- OAuth providers other than Telegram
- Admin panel
- Email notifications
- Paid activation flow (manual, as before)
