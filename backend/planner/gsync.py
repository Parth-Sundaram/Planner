"""Google Calendar two-way sync and Google Tasks import.

Sync order matters (see `full_sync`):
  1. pull busy events from your normal calendars (classes, meetings)
  2. pull your manual edits from the "Plan" calendar (dragged/resized/deleted blocks)
  3. import Google Tasks
  4. scrape course sites
  5. replan
  6. push the plan back to the "Plan" calendar
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import service
from .models import Block, ExternalEvent, OAuthToken, Task

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/tasks.readonly",
]
APP_TAG = "planner"
PLAN_CAL_NAME = "Plan (auto)"


# ---- Credentials ------------------------------------------------------------------

def load_credentials(db: Session):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    row = db.scalar(select(OAuthToken))
    if not row:
        return None
    creds = Credentials.from_authorized_user_info(json.loads(row.token_json), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        row.token_json = creds.to_json()
        db.commit()
    return creds


def save_credentials(db: Session, email: str, creds) -> None:
    row = db.scalar(select(OAuthToken).where(OAuthToken.email == email))
    if row:
        row.token_json = creds.to_json()
    else:
        db.add(OAuthToken(email=email, token_json=creds.to_json()))
    db.commit()


def build_services(creds):
    from googleapiclient.discovery import build
    return (build("calendar", "v3", credentials=creds, cache_discovery=False),
            build("tasks", "v1", credentials=creds, cache_discovery=False))


# ---- Time helpers -------------------------------------------------------------------

def to_local(value: str, tz: ZoneInfo) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(tz).replace(tzinfo=None)


def to_rfc3339(dt: datetime, tz: ZoneInfo) -> str:
    return dt.replace(tzinfo=tz).isoformat()


def _event_times(ev: dict, tz: ZoneInfo) -> tuple[datetime, datetime] | None:
    s, e = ev.get("start", {}), ev.get("end", {})
    if "dateTime" not in s or "dateTime" not in e:
        return None  # all-day events don't block time
    return to_local(s["dateTime"], tz), to_local(e["dateTime"], tz)


def _list_events(cal, calendar_id: str, start: datetime, end: datetime, tz: ZoneInfo, **extra) -> list[dict]:
    items, token = [], None
    while True:
        resp = cal.events().list(calendarId=calendar_id, timeMin=to_rfc3339(start, tz),
                                 timeMax=to_rfc3339(end, tz), singleEvents=True,
                                 orderBy="startTime", pageToken=token, maxResults=250, **extra).execute()
        items += resp.get("items", [])
        token = resp.get("nextPageToken")
        if not token:
            return items


# ---- 1. Busy time from your normal calendars ---------------------------------------------

def pull_busy(db: Session, cal, now: datetime) -> int:
    cfg = service.get_settings(db)
    tz = ZoneInfo(cfg["timezone"])
    start, end = now - timedelta(days=1), now + timedelta(days=int(cfg["horizon_days"]) + 1)
    plan_id = cfg["plan_calendar_id"]
    seen = set()
    for cal_id in [c.strip() for c in cfg["busy_calendar_ids"].split(",") if c.strip()]:
        if cal_id == plan_id:
            continue
        for ev in _list_events(cal, cal_id, start, end, tz):
            if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                continue  # "Free" events don't block time
            times = _event_times(ev, tz)
            if not times:
                continue
            gid = f"{cal_id}:{ev['id']}"
            seen.add(gid)
            row = db.scalar(select(ExternalEvent).where(ExternalEvent.gcal_id == gid))
            if row:
                row.title, (row.start, row.end) = ev.get("summary", "Busy"), times
            else:
                db.add(ExternalEvent(gcal_id=gid, calendar_id=cal_id, title=ev.get("summary", "Busy"),
                                     start=times[0], end=times[1]))
    # Drop events that were deleted or moved out of the window.
    for row in db.scalars(select(ExternalEvent).where(ExternalEvent.end > start, ExternalEvent.start < end)):
        if row.gcal_id not in seen:
            db.delete(row)
    db.commit()
    return len(seen)


# ---- 2. Your edits on the Plan calendar ---------------------------------------------------

def ensure_plan_calendar(db: Session, cal) -> str:
    cfg = service.get_settings(db)
    if cfg["plan_calendar_id"]:
        return cfg["plan_calendar_id"]
    created = cal.calendars().insert(body={"summary": PLAN_CAL_NAME, "timeZone": cfg["timezone"]}).execute()
    service.set_setting(db, "plan_calendar_id", created["id"])
    db.commit()
    return created["id"]


def pull_plan_edits(db: Session, cal, now: datetime) -> dict:
    """If you dragged, resized or deleted a block in Google Calendar, apply it here first."""
    cfg = service.get_settings(db)
    tz = ZoneInfo(cfg["timezone"])
    plan_id = ensure_plan_calendar(db, cal)
    end = now + timedelta(days=int(cfg["horizon_days"]) + 1)
    events = {ev["id"]: ev for ev in _list_events(cal, plan_id, now - timedelta(days=1), end, tz,
                                                  privateExtendedProperty=f"app={APP_TAG}")}
    moved = removed = 0
    for b in db.scalars(select(Block).where(Block.gcal_event_id.is_not(None), Block.end > now - timedelta(days=1))):
        ev = events.get(b.gcal_event_id)
        if ev is None or ev.get("status") == "cancelled":
            if b.status == "planned" and b.start >= now:
                # You deleted it in Google Calendar: drop it; the work gets rescheduled elsewhere.
                db.delete(b)
                removed += 1
            continue
        times = _event_times(ev, tz)
        if times and (times[0] != b.start or times[1] != b.end):
            b.start, b.end, b.locked = times[0], times[1], True
            moved += 1
    db.commit()
    return {"moved": moved, "removed": removed}


# ---- 3. Google Tasks import ---------------------------------------------------------------

DUE_TIME = re.compile(r"@\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)


def import_google_tasks(db: Session, tasks_api, now: datetime) -> dict:
    created = updated = skipped = 0
    lists = tasks_api.tasklists().list(maxResults=100).execute().get("items", [])
    for tl in lists:
        token = None
        while True:
            resp = tasks_api.tasks().list(tasklist=tl["id"], showCompleted=True, showHidden=True,
                                          maxResults=100, pageToken=token).execute()
            # Parents first, so subtasks can attach to them (Google Tasks supports one level).
            items = sorted(resp.get("items", []), key=lambda x: bool(x.get("parent")))
            for gt in items:
                ext = f"gtasks:{gt['id']}"
                parent = None
                if gt.get("parent"):
                    parent = db.scalar(select(Task).where(Task.external_id == f"gtasks:{gt['parent']}"))
                local = db.scalar(select(Task).where(Task.external_id == ext))
                if gt.get("status") == "completed" or gt.get("deleted"):
                    if local and local.status != "done":
                        local.status = "done"
                        updated += 1
                    continue
                if not gt.get("due") and parent is not None:
                    gt = {**gt, "due": parent.due.date().isoformat()}  # steps inherit the parent's date
                    if "@" not in f"{gt.get('title', '')} {gt.get('notes', '')}":
                        gt["_due_time"] = parent.due.time()
                if not gt.get("due"):
                    skipped += 1   # no deadline, nothing to schedule against
                    continue
                text = f"{gt.get('title', '')} {gt.get('notes', '')}"
                # Google Tasks only stores a due *date*; "@11am" in the title/notes sets the time.
                due_day = date.fromisoformat(gt["due"][:10])
                due_t = gt.get("_due_time", time(23, 59))
                if m := DUE_TIME.search(text):
                    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
                    if ap == "pm" and h != 12:
                        h += 12
                    if ap == "am" and h == 12:
                        h = 0
                    due_t = time(h % 24, mi)
                    text = text.replace(m.group(0), " ")
                title_part, _ = service.parse_meta(gt.get("title", ""))
                title_part = DUE_TIME.sub(" ", title_part).strip() or "Untitled"
                _, meta = service.parse_meta(text)
                due = datetime.combine(due_day, due_t)
                if due < now:
                    continue
                fields = {"title": title_part, "due": due}
                if "est_minutes" in meta:
                    fields["needs_estimate"] = False
                for k in ("est_minutes", "priority", "min_total_minutes", "min_block", "splittable"):
                    if k in meta:
                        fields[k] = meta[k]
                if "tags" in meta:
                    fields["tags"] = ",".join(meta["tags"])
                if parent is not None:
                    fields["parent_id"] = parent.id
                    try:
                        fields["position"] = int(gt.get("position", "0"))
                    except ValueError:
                        pass
                if local is None:
                    fields.setdefault("needs_estimate", True)
                    db.add(Task(source="google_tasks", external_id=ext, **fields))
                    db.flush()
                    created += 1
                else:
                    changed = False
                    for k, v in fields.items():
                        if getattr(local, k) != v:
                            setattr(local, k, v)
                            changed = True
                    updated += changed
            token = resp.get("nextPageToken")
            if not token:
                break
    db.commit()
    return {"created": created, "updated": updated, "skipped_no_due": skipped}


# ---- 6. Push the plan -----------------------------------------------------------------------

COLORS = {"task": "9", "routine": "2", "busy": "8"}  # blueberry, sage, graphite


def push_plan(db: Session, cal, now: datetime) -> dict:
    cfg = service.get_settings(db)
    tz = ZoneInfo(cfg["timezone"])
    plan_id = ensure_plan_calendar(db, cal)
    start, end = now - timedelta(days=1), now + timedelta(days=int(cfg["horizon_days"]) + 1)
    existing = _list_events(cal, plan_id, start, end, tz, privateExtendedProperty=f"app={APP_TAG}")
    by_id = {ev["id"]: ev for ev in existing}
    # Reuse identical events instead of delete+recreate, so replans don't churn your calendar.
    by_shape = {}
    for ev in existing:
        times = _event_times(ev, tz)
        if times:
            by_shape.setdefault((ev.get("summary"), times[0], times[1]), []).append(ev["id"])

    blocks = db.scalars(select(Block).where(Block.end > start, Block.start < end)).all()
    keep, created, patched = set(), 0, 0
    for b in blocks:
        title = b.title + (" ✓" if b.status == "done" else "")
        body = {
            "summary": title,
            "start": {"dateTime": b.start.isoformat(), "timeZone": cfg["timezone"]},
            "end": {"dateTime": b.end.isoformat(), "timeZone": cfg["timezone"]},
            "colorId": COLORS.get(b.kind, "9"),
            "extendedProperties": {"private": {"app": APP_TAG, "block_id": str(b.id)}},
            "reminders": {"useDefault": b.kind == "task"},
        }
        if b.gcal_event_id in by_id and b.gcal_event_id not in keep:
            ev = by_id[b.gcal_event_id]
            if _event_times(ev, tz) != (b.start, b.end) or ev.get("summary") != title:
                cal.events().patch(calendarId=plan_id, eventId=b.gcal_event_id, body=body).execute()
                patched += 1
            keep.add(b.gcal_event_id)
            continue
        reuse = [i for i in by_shape.get((title, b.start, b.end), []) if i not in keep]
        if reuse:
            b.gcal_event_id = reuse[0]
            cal.events().patch(calendarId=plan_id, eventId=reuse[0],
                               body={"extendedProperties": body["extendedProperties"]}).execute()
            keep.add(reuse[0])
            continue
        ev = cal.events().insert(calendarId=plan_id, body=body).execute()
        b.gcal_event_id = ev["id"]
        keep.add(ev["id"])
        created += 1
    deleted = 0
    for ev in existing:
        if ev["id"] not in keep:
            cal.events().delete(calendarId=plan_id, eventId=ev["id"]).execute()
            deleted += 1
    db.commit()
    return {"created": created, "updated": patched, "deleted": deleted}


# ---- Everything, in order -------------------------------------------------------------------

def full_sync(db: Session, now: datetime | None = None, services=None, scrape=True) -> dict:
    from . import scrapers
    now = now or service.local_now(db)
    report: dict = {}
    cal = tasks_api = None
    if services:
        cal, tasks_api = services
    else:
        creds = load_credentials(db)
        if creds:
            cal, tasks_api = build_services(creds)
    if cal:
        report["busy_events"] = pull_busy(db, cal, now)
        report["plan_edits"] = pull_plan_edits(db, cal, now)
    if tasks_api:
        report["google_tasks"] = import_google_tasks(db, tasks_api, now)
    if scrape:
        report["courses"] = scrapers.sync_courses(db, now)
    report["plan"] = service.replan(db, now)
    if cal:
        report["pushed"] = push_plan(db, cal, now)
    if not cal:
        report["google"] = "not connected"
    return report
