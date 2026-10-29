"""Health Connect -> planner, through the free "HC Webhook" Android app (com.hcwebhook.app).

The app reads Health Connect (which your GloryFit app writes to) and POSTs JSON here on a
schedule. Payloads are incremental and may resend edited records, so raw records are upserted
by their Health Connect id; daily numbers (sleep, steps, heart rate) are then recomputed for the
days that changed and written to the Fitness page. Exercise sessions become workout entries.

Payload reference: github.com/mcnaveen/health-connect-webhook/blob/main/docs/webhook.md
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AreaEntry, Course, HealthRecord

KG_TO_LB = 2.20462


def _dt(s: str | None, tz: ZoneInfo) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        return d
    return d.astimezone(tz).replace(tzinfo=None, microsecond=0)


def _rid(kind: str, rec: dict, key_time: str) -> str:
    meta = rec.get("metadata") or {}
    return f"{kind}:{meta.get('id') or rec.get(key_time) or rec.get('time') or rec.get('start_time')}"


def _upsert(db: Session, rec_id: str, kind: str, start: datetime, end: datetime | None, value: float | None, data: dict) -> None:
    row = db.scalar(select(HealthRecord).where(HealthRecord.rec_id == rec_id))
    if row is None:
        db.add(HealthRecord(rec_id=rec_id, type=kind, start=start, end=end, value=value, data=json.dumps(data)))
    else:
        keep = {k: v for k, v in json.loads(row.data or "{}").items() if k == "entry_id"}  # linked workout entry
        row.start, row.end, row.value, row.data = start, end, value, json.dumps({**data, **keep})


def ingest(db: Session, payload: dict, tz: ZoneInfo) -> dict:
    touched: set[date] = set()
    counts = defaultdict(int)
    for r in payload.get("steps", []) or []:
        st, en = _dt(r.get("start_time"), tz), _dt(r.get("end_time"), tz)
        if st is None:
            continue
        # aggregates (no metadata id) are keyed by their start so a resent bucket replaces the old one
        _upsert(db, _rid("steps", r, "start_time"), "steps", st, en, float(r.get("count") or 0), {})
        touched.add(st.date())
        counts["steps"] += 1
    for r in payload.get("sleep", []) or []:
        en = _dt(r.get("session_end_time"), tz)
        dur = int(r.get("duration_seconds") or 0)
        if en is None or not dur:
            continue
        awake = sum(int(s.get("duration_seconds") or 0) for s in r.get("stages") or []
                    if any(w in str(s.get("stage", "")).upper() for w in ("AWAKE", "OUT_OF_BED")))
        st = en - timedelta(seconds=dur)
        _upsert(db, _rid("sleep", r, "session_end_time"), "sleep", st, en, (dur - awake) / 60, {"awake_min": awake // 60})
        touched.add(en.date())
        counts["sleep"] += 1
    for kind in ("heart_rate", "resting_heart_rate"):
        for r in payload.get(kind, []) or []:
            t = _dt(r.get("time"), tz)
            if t is None:
                continue
            v = r.get("avg", r.get("bpm"))
            data = {k: r[k] for k in ("min", "max") if k in r}
            _upsert(db, _rid(kind, r, "time"), kind, t, None, float(v) if v is not None else None, data)
            touched.add(t.date())
            counts[kind] += 1
    for r in payload.get("weight", []) or []:
        t = _dt(r.get("time"), tz)
        if t is not None and r.get("kilograms"):
            _upsert(db, _rid("weight", r, "time"), "weight", t, None, round(float(r["kilograms"]) * KG_TO_LB, 1), {})
            touched.add(t.date())
            counts["weight"] += 1
    for r in payload.get("exercise", []) or []:
        st, en = _dt(r.get("start_time"), tz), _dt(r.get("end_time"), tz)
        if st is None:
            continue
        _upsert(db, _rid("exercise", r, "start_time"), "exercise", st, en, float(r.get("duration_seconds") or 0) / 60,
                {"type": str(r.get("type") or "Workout"), "steps": r.get("steps"), "distance_m": r.get("distance_meters")})
        touched.add(st.date())
        counts["exercise"] += 1
    for d in payload.get("deleted", []) or []:
        row = db.scalar(select(HealthRecord).where(HealthRecord.rec_id == f"{d.get('type')}:{d.get('id')}"))
        if row:
            touched.add(row.start.date())
            db.delete(row)
            counts["deleted"] += 1
    db.flush()
    days = sorted(touched)
    for d in days:
        rebuild_day(db, d)
    db.commit()
    return {"received": dict(counts), "days": [d.isoformat() for d in days]}


def _fitness(db: Session) -> Course | None:
    from .scrapers import get_courses
    return next((c for c in get_courses(db) if c.kind == "fitness"), None)


def rebuild_day(db: Session, d: date) -> dict:
    """Daily numbers from the raw records, written into that day's Fitness metric entry."""
    from . import areas as A
    lo, hi = datetime.combine(d, time()), datetime.combine(d + timedelta(days=1), time())
    rows = list(db.scalars(select(HealthRecord).where(
        (HealthRecord.start >= lo - timedelta(hours=16)) & (HealthRecord.start < hi))))
    day = [r for r in rows if lo <= r.start < hi]
    out: dict = {}
    steps = [r.value for r in day if r.type == "steps" and r.value is not None]
    if steps:
        out["steps"] = int(sum(steps))
    sleeps = [r for r in rows if r.type == "sleep" and r.end and lo <= r.end < hi]  # counted on the day you wake up
    if sleeps:
        out["sleep_minutes"] = int(round(sum(r.value or 0 for r in sleeps)))
    hr = [r for r in day if r.type == "heart_rate" and r.value]
    if hr:
        out["avg_hr"] = int(round(sum(r.value for r in hr) / len(hr)))
        out["min_hr"] = int(min(json.loads(r.data).get("min", r.value) for r in hr))
        out["max_hr"] = int(max(json.loads(r.data).get("max", r.value) for r in hr))
    rest = sorted((r for r in day if r.type == "resting_heart_rate" and r.value), key=lambda r: r.start)
    if rest:
        out["resting_hr"] = int(rest[-1].value)
    w = sorted((r for r in day if r.type == "weight" and r.value), key=lambda r: r.start)
    if w:
        out["weight"] = w[-1].value
    fit = _fitness(db)
    if fit is None:
        return out
    if out:
        A.add_entry(db, fit, "metric", lo, out)
    # Exercise sessions become workouts once (tracked by the record id)
    for r in day:
        if r.type != "exercise":
            continue
        data = json.loads(r.data)
        if data.get("entry_id") and db.get(AreaEntry, data["entry_id"]):
            continue
        kind = data["type"].replace("EXERCISE_TYPE_", "").replace("_", " ").title()
        e = A.add_entry(db, fit, "workout", r.start, {"kind": f"{kind} (watch)", "minutes": int(r.value or 0)})
        r.data = json.dumps({**data, "entry_id": e.id})
    return out
