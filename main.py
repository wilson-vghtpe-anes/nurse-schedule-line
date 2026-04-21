import os
import hashlib
import hmac
import base64
import json
import io
from datetime import date, timedelta, datetime
from typing import Optional

import requests
from fastapi import FastAPI, Request, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from dotenv import load_dotenv
import openpyxl

load_dotenv()

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
LIFF_ID = os.getenv("LIFF_ID", "")
API_BASE = os.getenv("API_BASE", "")
PORT = int(os.getenv("PORT", "10001"))

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}

SHIFT_TYPES = {"7-3", "9-5", "10-6", "12-8", "3-11", "11-7", "其他", "休"}

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")

# ── LINE helpers ──────────────────────────────────────────────────────────────

def reply_message(reply_token: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"replyToken": reply_token, "messages": [{"type": "text", "text": text}]},
        timeout=5,
    )


def push_message(line_user_id: str, text: str):
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {LINE_TOKEN}", "Content-Type": "application/json"},
        json={"to": line_user_id, "messages": [{"type": "text", "text": text}]},
        timeout=5,
    )


# ── Supabase: user helpers ─────────────────────────────────────────────────────

def _sb(path: str, method="GET", params=None, json_body=None):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    r = requests.request(method, url, headers=SUPABASE_HEADERS, params=params, json=json_body, timeout=10)
    if r.status_code >= 400:
        return None
    return r.json()


def get_user_by_line_id(line_user_id: str):
    rows = _sb("nurses", params={"line_user_id": f"eq.{line_user_id}", "limit": "1"})
    return rows[0] if rows else None


def get_user_by_id(user_id: str):
    rows = _sb("nurses", params={"id": f"eq.{user_id}", "limit": "1"})
    return rows[0] if rows else None


def get_user_by_name(name: str):
    rows = _sb("nurses", params={"name": f"eq.{name}", "limit": "1"})
    return rows[0] if rows else None


def get_all_users():
    return _sb("nurses", params={"order": "name"}) or []


def get_reviewers():
    return _sb("nurses", params={"role": "in.(manager,admin)"}) or []


# ── Supabase: schedule helpers ─────────────────────────────────────────────────

def get_schedules_by_user_date_range(user_id: str, start: str, end: str):
    params = [
        ("user_id", f"eq.{user_id}"),
        ("schedule_date", f"gte.{start}"),
        ("schedule_date", f"lte.{end}"),
        ("status", "eq.active"),
        ("order", "schedule_date"),
    ]
    return _sb("schedules", params=params) or []


def _get_schedules_range(start: str, end: str, user_id: str = None):
    params = [
        ("schedule_date", f"gte.{start}"),
        ("schedule_date", f"lte.{end}"),
        ("status", "eq.active"),
        ("order", "schedule_date,user_id"),
        ("select", "id,user_id,schedule_date,shift_type,area,notes,source_version"),
    ]
    if user_id:
        params.append(("user_id", f"eq.{user_id}"))
    return _sb("schedules", params=params) or []


def get_schedules_by_date(schedule_date: str):
    return _sb("schedules", params={
        "schedule_date": f"eq.{schedule_date}",
        "status": "eq.active",
        "order": "shift_type,user_id",
        "select": "id,user_id,schedule_date,shift_type,area,notes",
    }) or []


def get_schedule_by_id(schedule_id: str):
    rows = _sb("schedules", params={"id": f"eq.{schedule_id}", "limit": "1"})
    return rows[0] if rows else None


def upsert_schedules(records: list):
    return requests.post(
        f"{SUPABASE_URL}/rest/v1/schedules",
        headers={**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=records,
        timeout=30,
    ).status_code < 400


def update_schedule_status(schedule_id: str, status: str):
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/schedules",
        headers=SUPABASE_HEADERS,
        params={"id": f"eq.{schedule_id}"},
        json={"status": status},
        timeout=10,
    )


# ── Supabase: OT priority helpers ──────────────────────────────────────────────

def get_ot_priority_by_date(priority_date: str, shift_type: str = None):
    params = {
        "priority_date": f"eq.{priority_date}",
        "status": "eq.active",
        "order": "shift_type,priority_order",
        "select": "id,priority_date,user_id,shift_type,priority_order,source_version",
    }
    if shift_type:
        params["shift_type"] = f"eq.{shift_type}"
    return _sb("ot_priority", params=params) or []


def get_ot_priority_by_id(ot_priority_id: str):
    rows = _sb("ot_priority", params={"id": f"eq.{ot_priority_id}", "limit": "1"})
    return rows[0] if rows else None


def upsert_ot_priority(records: list):
    return requests.post(
        f"{SUPABASE_URL}/rest/v1/ot_priority",
        headers={**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates,return=minimal"},
        json=records,
        timeout=30,
    ).status_code < 400


def update_ot_priority_status(ot_priority_id: str, status: str):
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/ot_priority",
        headers=SUPABASE_HEADERS,
        params={"id": f"eq.{ot_priority_id}"},
        json={"status": status},
        timeout=10,
    )


# ── Supabase: swap request helpers ────────────────────────────────────────────

def create_swap_request(data: dict):
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/swap_requests",
        headers=SUPABASE_HEADERS,
        json=data,
        timeout=10,
    )
    if r.status_code < 400:
        result = r.json()
        return result[0] if result else None
    return None


def get_swap_request_by_id(request_id: str):
    rows = _sb("swap_requests", params={"id": f"eq.{request_id}", "limit": "1"})
    return rows[0] if rows else None


def get_swap_requests_by_user(user_id: str):
    return _sb("swap_requests", params={
        "or": f"(requester_id.eq.{user_id},target_user_id.eq.{user_id})",
        "order": "created_at.desc",
        "limit": "20",
    }) or []


def get_pending_swap_requests():
    return _sb("swap_requests", params={
        "request_type": "eq.shift",
        "status": "eq.pending_admin",
        "order": "created_at",
    }) or []


def update_swap_request(request_id: str, fields: dict):
    fields["updated_at"] = datetime.utcnow().isoformat()
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/swap_requests",
        headers=SUPABASE_HEADERS,
        params={"id": f"eq.{request_id}"},
        json=fields,
        timeout=10,
    )


def _get_conflicting_swap_requests(schedule_ids: list, ot_priority_ids: list):
    """Find pending requests that conflict with the given slot IDs."""
    results = []
    active_statuses = ["submitted", "pending_peer"]

    for sid in schedule_ids:
        for field in ["schedule_id", "target_schedule_id"]:
            rows = _sb("swap_requests", params={
                field: f"eq.{sid}",
                "status": f"in.({','.join(active_statuses)})",
            }) or []
            results.extend(rows)

    for oid in ot_priority_ids:
        for field in ["ot_priority_id", "target_ot_priority_id"]:
            rows = _sb("swap_requests", params={
                field: f"eq.{oid}",
                "status": f"in.({','.join(active_statuses)})",
            }) or []
            results.extend(rows)

    seen = set()
    unique = []
    for r in results:
        if r["id"] not in seen:
            seen.add(r["id"])
            unique.append(r)
    return unique


def auto_reject_conflicts(exclude_request_id: str, schedule_ids: list, ot_priority_ids: list):
    """Auto-reject conflicting pending requests and notify affected users."""
    conflicts = _get_conflicting_swap_requests(schedule_ids, ot_priority_ids)
    for req in conflicts:
        if req["id"] == exclude_request_id:
            continue
        update_swap_request(req["id"], {"status": "conflict_rejected"})
        msg = "您的換班申請因另一筆申請已進入審核流程，已自動取消。"
        if req.get("requester_id"):
            requester = get_user_by_id(req["requester_id"])
            if requester and requester.get("line_user_id"):
                push_message(requester["line_user_id"], msg)
        if req.get("target_user_id"):
            target = get_user_by_id(req["target_user_id"])
            if target and target.get("line_user_id"):
                push_message(target["line_user_id"], msg)


# ── Excel parsing ──────────────────────────────────────────────────────────────

def parse_schedule_excel(file_bytes: bytes, version: str = "") -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)

    schedules = []
    ot_priorities = []

    if "班表" in wb.sheetnames:
        ws = wb["班表"]
        headers = [str(c.value).strip() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]
        col = {h: i for i, h in enumerate(headers)}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(row):
                continue
            raw_date = row[col.get("日期", 0)]
            name = str(row[col.get("姓名", 1)] or "").strip()
            shift = str(row[col.get("班別", 2)] or "").strip()
            area = str(row[col.get("房區", 3)] or "").strip() if "房區" in col else ""

            if not raw_date or not name or not shift:
                continue
            if isinstance(raw_date, (date, datetime)):
                date_str = raw_date.strftime("%Y-%m-%d") if isinstance(raw_date, datetime) else raw_date.isoformat()
            else:
                date_str = str(raw_date).strip()

            schedules.append({
                "date_str": date_str,
                "name": name,
                "shift_type": shift,
                "area": area,
                "source_version": version,
            })

    if "加班順位" in wb.sheetnames:
        ws = wb["加班順位"]
        headers = [str(c.value).strip() if c.value else "" for c in next(ws.iter_rows(min_row=1, max_row=1))]
        col = {h: i for i, h in enumerate(headers)}
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not any(row):
                continue
            raw_date = row[col.get("日期", 0)]
            order = row[col.get("順位", 1)]
            name = str(row[col.get("姓名", 2)] or "").strip()
            shift_type = str(row[col.get("班別", 3)] or "").strip() if len(row) > col.get("班別", 3) else ""

            if not raw_date or order is None or not name:
                continue
            if isinstance(raw_date, (date, datetime)):
                date_str = raw_date.strftime("%Y-%m-%d") if isinstance(raw_date, datetime) else raw_date.isoformat()
            else:
                date_str = str(raw_date).strip()

            ot_priorities.append({
                "date_str": date_str,
                "priority_order": int(order),
                "name": name,
                "shift_type": shift_type or None,
                "source_version": version,
            })

    return {"schedules": schedules, "ot_priority": ot_priorities}


# ── Date utilities ─────────────────────────────────────────────────────────────

def week_range_of(d: date):
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def month_range(year_month: str):
    y, m = int(year_month[:4]), int(year_month[5:7])
    first = date(y, m, 1)
    if m == 12:
        last = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(y, m + 1, 1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


# ── Formatting ─────────────────────────────────────────────────────────────────

def format_schedule_day(rows: list, all_users: dict) -> str:
    if not rows:
        return "（查無班表資料）"
    lines = []
    for r in rows:
        name = all_users.get(r["user_id"], {}).get("name", "?")
        shift = r["shift_type"]
        area = f" {r['area']}" if r.get("area") else ""
        lines.append(f"  {name}：{shift}{area}")
    return "\n".join(lines)


def format_own_schedule(rows: list, all_users: dict) -> str:
    if not rows:
        return "（查無班表資料）"
    lines = []
    for r in rows:
        name = all_users.get(r["user_id"], {}).get("name", "?")
        shift = r["shift_type"]
        area = f" {r['area']}" if r.get("area") else ""
        lines.append(f"  {r['schedule_date']} {name}：{shift}{area}")
    return "\n".join(lines)


def format_ot_priority(rows: list, all_users: dict) -> str:
    if not rows:
        return "（查無加班順位資料）"
    lines = []
    current_shift = None
    for r in rows:
        name = all_users.get(r["user_id"], {}).get("name", "?")
        shift = r.get("shift_type") or "—"
        if shift != current_shift:
            lines.append(f"【{shift}】")
            current_shift = shift
        lines.append(f"  {r['priority_order']}. {name}")
    return "\n".join(lines)


def format_swap_request_line(req: dict, all_users: dict) -> str:
    rtype = "換班" if req["request_type"] == "shift" else ("直接調班" if req["request_type"] == "manager_direct" else "換加班順位")
    status_map = {
        "submitted": "已提交",
        "pending_peer": "等待對方確認",
        "pending_admin": "等待主管審核",
        "approved": "已核准",
        "rejected": "已拒絕",
        "cancelled": "已取消",
        "conflict_rejected": "因衝突自動取消",
        "peer_rejected": "對方已拒絕",
    }
    status = status_map.get(req["status"], req["status"])
    req_name = all_users.get(req.get("requester_id"), {}).get("name", "?") if req.get("requester_id") else "系統"
    tgt_name = all_users.get(req.get("target_user_id"), {}).get("name", "?") if req.get("target_user_id") else "未指定"
    short_id = str(req["id"])[:8]
    return f"[{short_id}] {rtype} {req_name}→{tgt_name} ({status})"


def _build_users_map():
    users = get_all_users()
    return {u["id"]: u for u in users}


# ── Auth dependencies ──────────────────────────────────────────────────────────

async def get_current_user(request: Request):
    line_user_id = request.headers.get("X-Line-User-Id")
    if not line_user_id:
        raise HTTPException(status_code=401, detail="Missing X-Line-User-Id header")
    user = get_user_by_line_id(line_user_id)
    if not user:
        raise HTTPException(status_code=403, detail="User not bound")
    if user["role"] == "pending":
        raise HTTPException(status_code=403, detail="Account pending approval")
    return user


def require_manager(user=Depends(get_current_user)):
    if user["role"] not in ("manager", "admin"):
        raise HTTPException(status_code=403, detail="Manager or admin required")
    return user


# ── Pydantic models ────────────────────────────────────────────────────────────

class SwapRequestCreate(BaseModel):
    request_type: str  # shift | ot_priority
    schedule_id: Optional[str] = None
    ot_priority_id: Optional[str] = None
    target_user_id: str
    target_schedule_id: Optional[str] = None
    target_ot_priority_id: Optional[str] = None
    reason: Optional[str] = None


class SwapRespond(BaseModel):
    response: str  # accepted | rejected


class SwapReview(BaseModel):
    decision: str  # approved | rejected
    comment: Optional[str] = None


class DirectSwapBody(BaseModel):
    swap_type: str  # shift | ot_priority
    slot_a_id: str
    slot_b_id: str
    reason: Optional[str] = None


# ── REST API endpoints ─────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "nurse-schedule-line", "status": "ok"}


@app.get("/health")
async def health():
    issues = []
    if not LINE_TOKEN:
        issues.append("LINE_CHANNEL_ACCESS_TOKEN missing")
    if not SUPABASE_URL or not SUPABASE_KEY:
        issues.append("Supabase credentials missing")
    if issues:
        return JSONResponse(status_code=500, content={"status": "error", "issues": issues})
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/nurses",
            headers=SUPABASE_HEADERS,
            params={"limit": "1"},
            timeout=5,
        )
        db_ok = r.status_code < 400
    except Exception:
        db_ok = False
    if not db_ok:
        return JSONResponse(status_code=500, content={"status": "error", "issues": ["DB unreachable"]})
    return {"status": "ok"}


@app.get("/api/me")
async def api_me(user=Depends(get_current_user)):
    return {"id": user["id"], "name": user["name"], "role": user["role"]}


@app.get("/api/schedules/me")
async def api_schedules_me(
    mode: str = "week",
    start: Optional[str] = None,
    end: Optional[str] = None,
    user=Depends(get_current_user),
):
    today = date.today()
    if mode == "today":
        s, e = today.isoformat(), today.isoformat()
    elif mode == "tomorrow":
        t = today + timedelta(days=1)
        s, e = t.isoformat(), t.isoformat()
    elif mode == "week":
        s, e = week_range_of(today)
    elif mode == "month":
        s, e = month_range(today.strftime("%Y-%m"))
    elif mode == "range" and start and end:
        s, e = start, end
    else:
        s, e = week_range_of(today)

    rows = _get_schedules_range(s, e, user_id=user["id"])
    users_map = _build_users_map()
    return {"schedules": rows, "users": {uid: u["name"] for uid, u in users_map.items()}}


@app.get("/api/schedules")
async def api_schedules(
    date: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    user=Depends(get_current_user),
):
    today_str = datetime.today().strftime("%Y-%m-%d")
    if date:
        rows = get_schedules_by_date(date)
    elif start and end:
        rows = _get_schedules_range(start, end)
    else:
        rows = get_schedules_by_date(today_str)
    users_map = _build_users_map()
    return {"schedules": rows, "users": {uid: u["name"] for uid, u in users_map.items()}}


@app.get("/api/ot-priority")
async def api_ot_priority(
    date: Optional[str] = None,
    shift_type: Optional[str] = None,
    user=Depends(get_current_user),
):
    target_date = date or datetime.today().strftime("%Y-%m-%d")
    rows = get_ot_priority_by_date(target_date, shift_type)
    users_map = _build_users_map()
    return {"date": target_date, "ot_priority": rows, "users": {uid: u["name"] for uid, u in users_map.items()}}


@app.post("/api/schedules/import")
async def api_import_schedules(
    file: UploadFile = File(...),
    version: str = "",
    manager=Depends(require_manager),
):
    content = await file.read()
    if not version:
        version = file.filename.replace(".xlsx", "").replace(".xls", "")

    parsed = parse_schedule_excel(content, version)
    users_map = {u["name"]: u["id"] for u in get_all_users()}

    schedule_records = []
    ot_records = []
    unmatched_names = set()
    invalid_shifts = set()

    for item in parsed["schedules"]:
        uid = users_map.get(item["name"])
        if not uid:
            unmatched_names.add(item["name"])
            continue
        if item["shift_type"] not in SHIFT_TYPES:
            invalid_shifts.add(item["shift_type"])
            continue
        schedule_records.append({
            "user_id": uid,
            "schedule_date": item["date_str"],
            "shift_type": item["shift_type"],
            "area": item["area"] or None,
            "source_version": item["source_version"],
            "status": "active",
        })

    for item in parsed["ot_priority"]:
        uid = users_map.get(item["name"])
        if not uid:
            unmatched_names.add(item["name"])
            continue
        ot_records.append({
            "priority_date": item["date_str"],
            "user_id": uid,
            "shift_type": item.get("shift_type"),
            "priority_order": item["priority_order"],
            "source_version": item["source_version"],
            "status": "active",
        })

    sched_ok = upsert_schedules(schedule_records) if schedule_records else True
    ot_ok = upsert_ot_priority(ot_records) if ot_records else True

    return {
        "schedules_imported": len(schedule_records),
        "ot_priority_imported": len(ot_records),
        "unmatched_names": list(unmatched_names),
        "invalid_shifts": list(invalid_shifts),
        "success": sched_ok and ot_ok,
    }


@app.post("/api/swap-requests")
async def api_create_swap_request(
    body: SwapRequestCreate,
    user=Depends(get_current_user),
):
    if body.request_type not in ("shift", "ot_priority"):
        raise HTTPException(status_code=400, detail="request_type must be shift or ot_priority")

    if body.request_type == "shift":
        if not body.schedule_id or not body.target_schedule_id:
            raise HTTPException(status_code=400, detail="schedule_id and target_schedule_id required")
        my_slot = get_schedule_by_id(body.schedule_id)
        their_slot = get_schedule_by_id(body.target_schedule_id)
        if not my_slot or my_slot["user_id"] != user["id"]:
            raise HTTPException(status_code=400, detail="schedule_id does not belong to you")
        if not their_slot or their_slot["user_id"] != body.target_user_id:
            raise HTTPException(status_code=400, detail="target_schedule_id does not belong to target_user_id")
    else:
        if not body.ot_priority_id or not body.target_ot_priority_id:
            raise HTTPException(status_code=400, detail="ot_priority_id and target_ot_priority_id required")
        my_slot = get_ot_priority_by_id(body.ot_priority_id)
        their_slot = get_ot_priority_by_id(body.target_ot_priority_id)
        if not my_slot or my_slot["user_id"] != user["id"]:
            raise HTTPException(status_code=400, detail="ot_priority_id does not belong to you")
        if not their_slot or their_slot["user_id"] != body.target_user_id:
            raise HTTPException(status_code=400, detail="target_ot_priority_id does not belong to target_user_id")

    data = {
        "request_type": body.request_type,
        "status": "pending_peer",
        "requester_id": user["id"],
        "target_user_id": body.target_user_id,
        "reason": body.reason,
    }
    if body.request_type == "shift":
        data["schedule_id"] = body.schedule_id
        data["target_schedule_id"] = body.target_schedule_id
    else:
        data["ot_priority_id"] = body.ot_priority_id
        data["target_ot_priority_id"] = body.target_ot_priority_id

    req = create_swap_request(data)
    if not req:
        raise HTTPException(status_code=500, detail="Failed to create swap request")

    target = get_user_by_id(body.target_user_id)
    if target and target.get("line_user_id"):
        rtype = "換班" if body.request_type == "shift" else "換加班順位"
        push_message(
            target["line_user_id"],
            f"【{rtype}請求】{user['name']} 希望與您換班，請至 LINE app 查看並確認。\n申請 ID：{str(req['id'])[:8]}",
        )

    return req


@app.get("/api/swap-requests/me")
async def api_my_swap_requests(user=Depends(get_current_user)):
    reqs = get_swap_requests_by_user(user["id"])
    users_map = _build_users_map()
    return {
        "swap_requests": reqs,
        "users": {uid: u["name"] for uid, u in users_map.items()},
    }


@app.post("/api/swap-requests/{request_id}/cancel")
async def api_cancel_swap_request(request_id: str, user=Depends(get_current_user)):
    req = get_swap_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["requester_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your request")
    if req["status"] not in ("submitted", "pending_peer"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel request in status {req['status']}")
    update_swap_request(request_id, {"status": "cancelled"})
    return {"status": "cancelled"}


@app.post("/api/swap-requests/{request_id}/respond")
async def api_respond_swap_request(
    request_id: str,
    body: SwapRespond,
    user=Depends(get_current_user),
):
    req = get_swap_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["target_user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your request to respond")
    if req["status"] != "pending_peer":
        raise HTTPException(status_code=400, detail=f"Request is not awaiting peer response (status: {req['status']})")

    if body.response == "rejected":
        update_swap_request(request_id, {
            "status": "peer_rejected",
            "peer_response": "rejected",
            "peer_responded_at": datetime.utcnow().isoformat(),
        })
        requester = get_user_by_id(req["requester_id"])
        if requester and requester.get("line_user_id"):
            push_message(requester["line_user_id"], f"您的換班申請 [{str(request_id)[:8]}] 已被對方拒絕。")
        return {"status": "peer_rejected"}

    if body.response != "accepted":
        raise HTTPException(status_code=400, detail="response must be accepted or rejected")

    if req["request_type"] == "ot_priority":
        # OT priority swap: execute immediately
        ot_a = get_ot_priority_by_id(req["ot_priority_id"])
        ot_b = get_ot_priority_by_id(req["target_ot_priority_id"])
        if not ot_a or not ot_b:
            raise HTTPException(status_code=400, detail="OT priority slots not found")

        requests.patch(
            f"{SUPABASE_URL}/rest/v1/ot_priority",
            headers=SUPABASE_HEADERS,
            params={"id": f"eq.{ot_a['id']}"},
            json={"user_id": ot_b["user_id"]},
            timeout=10,
        )
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/ot_priority",
            headers=SUPABASE_HEADERS,
            params={"id": f"eq.{ot_b['id']}"},
            json={"user_id": ot_a["user_id"]},
            timeout=10,
        )
        update_swap_request(request_id, {
            "status": "approved",
            "peer_response": "accepted",
            "peer_responded_at": datetime.utcnow().isoformat(),
        })
        auto_reject_conflicts(
            request_id,
            schedule_ids=[],
            ot_priority_ids=[ot_a["id"], ot_b["id"]],
        )

        # Notify requester
        requester = get_user_by_id(req["requester_id"])
        if requester and requester.get("line_user_id"):
            push_message(requester["line_user_id"], f"您的換加班順位申請 [{str(request_id)[:8]}] 已完成，對方已同意並執行。")
        push_message(user["line_user_id"], f"換加班順位申請 [{str(request_id)[:8]}] 已完成。")
        return {"status": "approved"}

    else:
        # Shift swap: peer accepted → send to manager
        update_swap_request(request_id, {
            "status": "pending_admin",
            "peer_response": "accepted",
            "peer_responded_at": datetime.utcnow().isoformat(),
        })
        requester = get_user_by_id(req["requester_id"])
        if requester and requester.get("line_user_id"):
            push_message(requester["line_user_id"], f"您的換班申請 [{str(request_id)[:8]}] 對方已同意，等待主管審核。")
        for reviewer in get_reviewers():
            if reviewer.get("line_user_id"):
                push_message(reviewer["line_user_id"], f"【換班審核】有新的換班申請待審核，申請 ID：{str(request_id)[:8]}。")
        return {"status": "pending_admin"}


@app.get("/api/swap-requests/pending")
async def api_pending_swap_requests(manager=Depends(require_manager)):
    reqs = get_pending_swap_requests()
    users_map = _build_users_map()
    return {
        "swap_requests": reqs,
        "users": {uid: u["name"] for uid, u in users_map.items()},
    }


@app.post("/api/swap-requests/{request_id}/review")
async def api_review_swap_request(
    request_id: str,
    body: SwapReview,
    manager=Depends(require_manager),
):
    req = get_swap_request_by_id(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req["status"] != "pending_admin":
        raise HTTPException(status_code=400, detail=f"Request is not pending admin review (status: {req['status']})")
    if req["request_type"] != "shift":
        raise HTTPException(status_code=400, detail="Only shift swaps require admin review")

    if body.decision == "rejected":
        update_swap_request(request_id, {
            "status": "rejected",
            "admin_decision": "rejected",
            "admin_comment": body.comment,
            "admin_decided_at": datetime.utcnow().isoformat(),
        })
        for uid in [req.get("requester_id"), req.get("target_user_id")]:
            if uid:
                person = get_user_by_id(uid)
                if person and person.get("line_user_id"):
                    push_message(person["line_user_id"], f"換班申請 [{str(request_id)[:8]}] 已被主管拒絕。{body.comment or ''}")
        return {"status": "rejected"}

    if body.decision != "approved":
        raise HTTPException(status_code=400, detail="decision must be approved or rejected")

    sched_a = get_schedule_by_id(req["schedule_id"])
    sched_b = get_schedule_by_id(req["target_schedule_id"])
    if not sched_a or not sched_b:
        raise HTTPException(status_code=400, detail="Schedule slots not found")

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/schedules",
        headers=SUPABASE_HEADERS,
        params={"id": f"eq.{sched_a['id']}"},
        json={"user_id": sched_b["user_id"]},
        timeout=10,
    )
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/schedules",
        headers=SUPABASE_HEADERS,
        params={"id": f"eq.{sched_b['id']}"},
        json={"user_id": sched_a["user_id"]},
        timeout=10,
    )
    update_swap_request(request_id, {
        "status": "approved",
        "admin_decision": "approved",
        "admin_comment": body.comment,
        "admin_decided_at": datetime.utcnow().isoformat(),
    })
    auto_reject_conflicts(
        request_id,
        schedule_ids=[sched_a["id"], sched_b["id"]],
        ot_priority_ids=[],
    )

    for uid in [req.get("requester_id"), req.get("target_user_id")]:
        if uid:
            person = get_user_by_id(uid)
            if person and person.get("line_user_id"):
                push_message(person["line_user_id"], f"換班申請 [{str(request_id)[:8]}] 已獲主管核准，班表已更新。")

    return {"status": "approved"}


@app.post("/api/admin/direct-swap")
async def api_direct_swap(body: DirectSwapBody, manager=Depends(require_manager)):
    if body.swap_type == "shift":
        slot_a = get_schedule_by_id(body.slot_a_id)
        slot_b = get_schedule_by_id(body.slot_b_id)
        if not slot_a or not slot_b:
            raise HTTPException(status_code=404, detail="Schedule slot(s) not found")

        requests.patch(
            f"{SUPABASE_URL}/rest/v1/schedules",
            headers=SUPABASE_HEADERS,
            params={"id": f"eq.{slot_a['id']}"},
            json={"user_id": slot_b["user_id"]},
            timeout=10,
        )
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/schedules",
            headers=SUPABASE_HEADERS,
            params={"id": f"eq.{slot_b['id']}"},
            json={"user_id": slot_a["user_id"]},
            timeout=10,
        )
        create_swap_request({
            "request_type": "manager_direct",
            "status": "approved",
            "schedule_id": slot_a["id"],
            "target_schedule_id": slot_b["id"],
            "target_user_id": slot_b["user_id"],
            "reason": body.reason,
            "performed_by": manager["id"],
            "admin_decision": "approved",
            "admin_decided_at": datetime.utcnow().isoformat(),
        })
        auto_reject_conflicts("", schedule_ids=[slot_a["id"], slot_b["id"]], ot_priority_ids=[])
        for uid in [slot_a["user_id"], slot_b["user_id"]]:
            person = get_user_by_id(uid)
            if person and person.get("line_user_id"):
                push_message(person["line_user_id"], f"主管已直接調整您的班表，請至 LINE app 查看最新班表。")
        return {"status": "done"}

    elif body.swap_type == "ot_priority":
        slot_a = get_ot_priority_by_id(body.slot_a_id)
        slot_b = get_ot_priority_by_id(body.slot_b_id)
        if not slot_a or not slot_b:
            raise HTTPException(status_code=404, detail="OT priority slot(s) not found")

        requests.patch(
            f"{SUPABASE_URL}/rest/v1/ot_priority",
            headers=SUPABASE_HEADERS,
            params={"id": f"eq.{slot_a['id']}"},
            json={"user_id": slot_b["user_id"]},
            timeout=10,
        )
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/ot_priority",
            headers=SUPABASE_HEADERS,
            params={"id": f"eq.{slot_b['id']}"},
            json={"user_id": slot_a["user_id"]},
            timeout=10,
        )
        create_swap_request({
            "request_type": "manager_direct",
            "status": "approved",
            "ot_priority_id": slot_a["id"],
            "target_ot_priority_id": slot_b["id"],
            "target_user_id": slot_b["user_id"],
            "reason": body.reason,
            "performed_by": manager["id"],
            "admin_decision": "approved",
            "admin_decided_at": datetime.utcnow().isoformat(),
        })
        auto_reject_conflicts("", schedule_ids=[], ot_priority_ids=[slot_a["id"], slot_b["id"]])
        for uid in [slot_a["user_id"], slot_b["user_id"]]:
            person = get_user_by_id(uid)
            if person and person.get("line_user_id"):
                push_message(person["line_user_id"], f"主管已直接調整您的加班順位，請至 LINE app 查看最新資料。")
        return {"status": "done"}

    raise HTTPException(status_code=400, detail="swap_type must be shift or ot_priority")


# ── Static file serving ────────────────────────────────────────────────────────

@app.get("/{filename}.html")
async def serve_html(filename: str):
    path = os.path.join(DOCS_DIR, f"{filename}.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404)
    content = open(path, encoding="utf-8").read()
    content = content.replace("{{LIFF_ID}}", LIFF_ID)
    content = content.replace("{{API_BASE}}", API_BASE)
    return Response(content, media_type="text/html")


@app.get("/share.js")
async def serve_js():
    path = os.path.join(DOCS_DIR, "share.js")
    if os.path.exists(path):
        return FileResponse(path, media_type="application/javascript")
    raise HTTPException(status_code=404)


# ── LINE Webhook ───────────────────────────────────────────────────────────────

def verify_line_signature(body_bytes: bytes, signature: str) -> bool:
    if not LINE_SECRET:
        return True
    digest = hmac.new(LINE_SECRET.encode(), body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


@app.post("/webhook")
async def webhook(request: Request):
    body_bytes = await request.body()
    sig = request.headers.get("X-Line-Signature", "")
    if not verify_line_signature(body_bytes, sig):
        raise HTTPException(status_code=400, detail="Invalid signature")

    body = json.loads(body_bytes)
    for event in body.get("events", []):
        _handle_event(event)
    return {"status": "ok"}


def _handle_event(event: dict):
    etype = event.get("type")
    reply_token = event.get("replyToken", "")

    if etype == "follow":
        reply_message(reply_token, (
            "歡迎使用護理排班系統！\n\n"
            "常用指令：\n"
            "・我的班表 → 本週班表\n"
            "・今天班表 → 今日全員班表\n"
            "・明天班表 → 明日全員班表\n"
            "・查班表 MM/DD → 指定日期全員班表\n"
            "・加班順位 今天 → 今日加班順位\n"
            "・加班順位 MM/DD → 指定日期加班順位\n"
            "・我的申請 → 查看換班申請\n"
            "・取消申請 [ID] → 取消申請\n\n"
            "換班功能請使用 LINE app 操作。"
        ))
        return

    if etype != "message":
        return

    msg = event.get("message", {})
    if msg.get("type") != "text":
        return

    text = msg.get("text", "").strip()
    source = event.get("source", {})
    line_user_id = source.get("userId", "")

    if not line_user_id:
        return

    user = get_user_by_line_id(line_user_id)
    if not user or user["role"] == "pending":
        reply_message(reply_token, "您的帳號尚未綁定或待審核，請聯絡管理者。")
        return

    _dispatch_command(text, user, reply_token)


def _dispatch_command(text: str, user: dict, reply_token: str):
    today = date.today()
    users_map = _build_users_map()

    # ── 我的班表
    if text == "我的班表":
        s, e = week_range_of(today)
        rows = _get_schedules_range(s, e, user_id=user["id"])
        body = format_own_schedule(rows, users_map)
        reply_message(reply_token, f"本週班表（{s} ~ {e}）：\n{body}")
        return

    # ── 本週班表
    if text == "本週班表":
        s, e = week_range_of(today)
        rows = _get_schedules_range(s, e, user_id=user["id"])
        body = format_own_schedule(rows, users_map)
        reply_message(reply_token, f"本週班表（{s} ~ {e}）：\n{body}")
        return

    # ── 今天班表
    if text == "今天班表":
        rows = get_schedules_by_date(today.isoformat())
        body = format_schedule_day(rows, users_map)
        reply_message(reply_token, f"今天（{today.isoformat()}）全員班表：\n{body}")
        return

    # ── 明天班表
    if text == "明天班表":
        tomorrow = (today + timedelta(days=1)).isoformat()
        rows = get_schedules_by_date(tomorrow)
        body = format_schedule_day(rows, users_map)
        reply_message(reply_token, f"明天（{tomorrow}）全員班表：\n{body}")
        return

    # ── 查班表 MM/DD 或 YYYY-MM-DD
    if text.startswith("查班表"):
        parts = text.split()
        if len(parts) < 2:
            reply_message(reply_token, "格式：查班表 MM/DD 或 查班表 YYYY-MM-DD")
            return
        date_str = _parse_date_arg(parts[1], today)
        if not date_str:
            reply_message(reply_token, "日期格式錯誤，請用 MM/DD 或 YYYY-MM-DD。")
            return
        rows = get_schedules_by_date(date_str)
        body = format_schedule_day(rows, users_map)
        reply_message(reply_token, f"{date_str} 全員班表：\n{body}")
        return

    # ── 加班順位 今天 / MM/DD / YYYY-MM-DD
    if text.startswith("加班順位"):
        parts = text.split()
        if len(parts) < 2 or parts[1] == "今天":
            target_date = today.isoformat()
        else:
            target_date = _parse_date_arg(parts[1], today)
            if not target_date:
                reply_message(reply_token, "日期格式錯誤，請用 今天、MM/DD 或 YYYY-MM-DD。")
                return
        rows = get_ot_priority_by_date(target_date)
        body = format_ot_priority(rows, users_map)
        reply_message(reply_token, f"{target_date} 加班順位：\n{body}")
        return

    # ── 我的申請
    if text == "我的申請":
        reqs = get_swap_requests_by_user(user["id"])
        if not reqs:
            reply_message(reply_token, "目前無換班申請記錄。")
            return
        lines = [format_swap_request_line(r, users_map) for r in reqs[:10]]
        reply_message(reply_token, "近期換班申請：\n" + "\n".join(lines))
        return

    # ── 取消申請 [ID]
    if text.startswith("取消申請"):
        parts = text.split()
        if len(parts) < 2:
            reply_message(reply_token, "格式：取消申請 [申請ID前8碼]")
            return
        short_id = parts[1].strip()
        matched = _find_request_by_short_id(short_id, user["id"])
        if not matched:
            reply_message(reply_token, f"找不到申請 {short_id}，或不屬於您。")
            return
        if matched["status"] not in ("submitted", "pending_peer"):
            reply_message(reply_token, f"申請目前狀態為「{matched['status']}」，無法取消。")
            return
        update_swap_request(matched["id"], {"status": "cancelled"})
        reply_message(reply_token, f"申請 {short_id} 已取消。")
        return

    # ── Manager: 待審換班
    if text == "待審換班" and user["role"] in ("manager", "admin"):
        reqs = get_pending_swap_requests()
        if not reqs:
            reply_message(reply_token, "目前無待審換班申請。")
            return
        lines = [format_swap_request_line(r, users_map) for r in reqs[:10]]
        reply_message(reply_token, "待審換班申請：\n" + "\n".join(lines) + "\n\n回覆「核准換班 [ID]」或「拒絕換班 [ID] [原因]」")
        return

    # ── Manager: 核准換班 [ID]
    if text.startswith("核准換班") and user["role"] in ("manager", "admin"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            reply_message(reply_token, "格式：核准換班 [申請ID前8碼]")
            return
        short_id = parts[1].strip()
        req = _find_swap_request_by_short_id_any(short_id)
        if not req or req["status"] != "pending_admin":
            reply_message(reply_token, f"找不到待審核申請 {short_id}。")
            return
        _execute_shift_swap_approval(req, user, comment=None)
        reply_message(reply_token, f"換班申請 {short_id} 已核准，班表已更新。")
        return

    # ── Manager: 拒絕換班 [ID] [原因]
    if text.startswith("拒絕換班") and user["role"] in ("manager", "admin"):
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            reply_message(reply_token, "格式：拒絕換班 [申請ID前8碼] [原因(選填)]")
            return
        short_id = parts[1].strip()
        comment = parts[2].strip() if len(parts) > 2 else ""
        req = _find_swap_request_by_short_id_any(short_id)
        if not req or req["status"] != "pending_admin":
            reply_message(reply_token, f"找不到待審核申請 {short_id}。")
            return
        update_swap_request(req["id"], {
            "status": "rejected",
            "admin_decision": "rejected",
            "admin_comment": comment,
            "admin_decided_at": datetime.utcnow().isoformat(),
        })
        for uid in [req.get("requester_id"), req.get("target_user_id")]:
            if uid:
                person = get_user_by_id(uid)
                if person and person.get("line_user_id"):
                    push_message(person["line_user_id"], f"換班申請 [{short_id}] 已被主管拒絕。{comment}")
        reply_message(reply_token, f"換班申請 {short_id} 已拒絕。")
        return

    reply_message(reply_token, (
        "指令不認識，常用指令：\n"
        "・我的班表\n"
        "・今天班表 / 明天班表\n"
        "・查班表 MM/DD\n"
        "・加班順位 今天\n"
        "・我的申請\n"
        "・取消申請 [ID]"
    ))


def _parse_date_arg(s: str, today: date) -> Optional[str]:
    s = s.strip()
    if "/" in s and len(s) <= 5:
        parts = s.split("/")
        try:
            return date(today.year, int(parts[0]), int(parts[1])).isoformat()
        except Exception:
            return None
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    return None


def _find_request_by_short_id(short_id: str, user_id: str):
    reqs = get_swap_requests_by_user(user_id)
    for r in reqs:
        if str(r["id"]).startswith(short_id):
            return r
    return None


def _find_swap_request_by_short_id_any(short_id: str):
    rows = _sb("swap_requests", params={
        "status": "eq.pending_admin",
        "order": "created_at.desc",
        "limit": "100",
    }) or []
    for r in rows:
        if str(r["id"]).startswith(short_id):
            return r
    return None


def _execute_shift_swap_approval(req: dict, manager: dict, comment: Optional[str]):
    sched_a = get_schedule_by_id(req["schedule_id"])
    sched_b = get_schedule_by_id(req["target_schedule_id"])
    if not sched_a or not sched_b:
        return

    requests.patch(
        f"{SUPABASE_URL}/rest/v1/schedules",
        headers=SUPABASE_HEADERS,
        params={"id": f"eq.{sched_a['id']}"},
        json={"user_id": sched_b["user_id"]},
        timeout=10,
    )
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/schedules",
        headers=SUPABASE_HEADERS,
        params={"id": f"eq.{sched_b['id']}"},
        json={"user_id": sched_a["user_id"]},
        timeout=10,
    )
    update_swap_request(req["id"], {
        "status": "approved",
        "admin_decision": "approved",
        "admin_comment": comment,
        "admin_decided_at": datetime.utcnow().isoformat(),
    })
    auto_reject_conflicts(req["id"], schedule_ids=[sched_a["id"], sched_b["id"]], ot_priority_ids=[])
    for uid in [req.get("requester_id"), req.get("target_user_id")]:
        if uid:
            person = get_user_by_id(uid)
            if person and person.get("line_user_id"):
                push_message(person["line_user_id"], f"換班申請 [{str(req['id'])[:8]}] 已獲主管核准，班表已更新。")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
