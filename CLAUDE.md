# nurse-schedule-line

LINE mini app for nurse shift schedule queries and swap requests.

## Commands

```bash
# Dev server
uvicorn main:app --reload --port 10001

# Install deps
pip install -r requirements.txt

# Docker
docker build -t nurse-schedule-line .
docker run -p 10001:10001 --env-file .env nurse-schedule-line
```

## Architecture

Single-file FastAPI app (`main.py`) + static LIFF pages (`docs/`).

**External services:**
- Supabase (shared with nurse-ot-line-main) via REST API
- LINE Messaging API (webhook + LIFF)

## Shared Supabase Tables

From nurse-ot-line-main (read-only):
- `nurses` — `id, name, role (nurse|manager|admin|pending), line_user_id, bound_at`

New tables owned by this service:
- `schedules` — `id, user_id, schedule_date, shift_type, area, source_version, status, notes`
- `ot_priority` — `id, priority_date, user_id, shift_type, priority_order, source_version, status`
- `swap_requests` — full audit trail for all swap actions

## Shift Types

Same as nurse-ot-line-main: `7-3 | 9-5 | 10-6 | 12-8 | 3-11 | 11-7 | 其他 | 休`

## REST API

- `GET /api/me` — current user
- `GET /api/schedules/me` — own schedule (`?mode=today|tomorrow|week|month|range&start=&end=`)
- `GET /api/schedules` — all nurses for a date (`?date=YYYY-MM-DD`)
- `GET /api/ot-priority` — OT priority list (`?date=YYYY-MM-DD`)
- `POST /api/schedules/import` — manager: upload Excel (multipart)
- `POST /api/swap-requests` — create swap request
- `GET /api/swap-requests/me` — own swap requests
- `POST /api/swap-requests/{id}/cancel` — cancel own request
- `POST /api/swap-requests/{id}/respond` — peer accept/reject
- `GET /api/swap-requests/pending` — manager: pending shift swaps
- `POST /api/swap-requests/{id}/review` — manager: approve/reject shift swap
- `POST /api/admin/direct-swap` — manager: direct swap (no approval)

## LIFF Pages

- `/index.html` — entry, role routing
- `/nurse.html` — 我的班表 / 全員班表 / 加班順位 / 我的申請
- `/manager.html` — all nurse tabs + 換班審核 / 匯入班表 / 直接調班

## Swap Logic

- Shift swap: peer confirm → manager approve → execute
- OT priority swap: peer confirm → execute immediately (no manager review)
- Manager direct swap: execute immediately, log as `manager_direct`
- Conflict: when swap progresses to pending_admin/approved, auto-reject all other pending requests touching same slots

## Code Style

PEP 8, snake_case, 4-space indent. Duration/minutes as integers. Dates as `YYYY-MM-DD` strings.
