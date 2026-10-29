"""Typed areas: what Career, Fitness and Finance pages hold beyond tasks, links and notes.

Every record is an AreaEntry with a type and a small JSON payload. Some records also keep
a task in sync so the planner schedules them:
- job with a next step and date  -> task "<Company>: <next step>" due that day
- bill (not on autopay)          -> repeating task "Pay <bill>" due each period
"""
from __future__ import annotations

import base64
import json
import os
from collections import defaultdict
from datetime import date, datetime, time, timedelta

import requests

from . import gemini
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AreaEntry, Course, Setting, Task

ENTRY_TYPES = {
    "career": {"job"},
    "fitness": {"meal", "workout", "metric"},
    "finance": {"bill", "expense", "budget"},
    "personal": {"iou", "promise", "memo"}, "course": set(),
}
JOB_STAGES = ["wishlist", "applied", "online assessment", "interview", "final round", "offer", "rejected", "withdrawn"]
NUMERIC = {"calories", "protein", "carbs", "fat", "minutes", "sleep_minutes", "steps", "weight", "resting_hr",
           "avg_hr", "min_hr", "max_hr", "amount", "limit"}


def _clean(etype: str, data: dict) -> dict:
    out = {}
    for k, v in (data or {}).items():
        if k in NUMERIC:
            if v in (None, ""):
                continue
            try:
                out[k] = float(v) if k in ("weight", "amount", "limit", "protein", "carbs", "fat") else int(float(v))
            except (TypeError, ValueError):
                raise ValueError(f"{k} must be a number")
        elif k == "closed_on" and isinstance(v, str):
            out[k] = v[:10]
        elif isinstance(v, (str, bool)) or v is None:
            out[k] = v.strip()[:500] if isinstance(v, str) else v
    if etype == "job" and out.get("stage") and out["stage"] not in JOB_STAGES:
        raise ValueError(f"stage must be one of: {', '.join(JOB_STAGES)}")
    if etype == "iou" and out.get("direction", "they_owe") not in ("they_owe", "i_owe"):
        raise ValueError("direction must be they_owe or i_owe")
    if etype == "promise" and out.get("status", "open") not in ("open", "kept", "broken"):
        raise ValueError("status must be open, kept or broken")
    if etype == "bill" and out.get("frequency", "monthly") not in ("weekly", "biweekly", "monthly", "yearly", "once"):
        raise ValueError("frequency must be weekly, biweekly, monthly, yearly or once")
    return out


def entry_json(e: AreaEntry) -> dict:
    return {"id": e.id, "area_id": e.area_id, "type": e.type, "date": e.date.isoformat(timespec="minutes") if e.date else None,
            "data": json.loads(e.data or "{}"), "task_id": e.task_id}


def _sync_task(db: Session, area: Course, e: AreaEntry) -> None:
    d = json.loads(e.data or "{}")
    want = None  # (title, due, minutes, repeat)
    if e.type == "job" and d.get("next_step") and e.date and d.get("stage") not in ("rejected", "withdrawn", "offer"):
        want = (f"{d.get('company', 'Application')}: {d['next_step']}", e.date, 45, None)
    elif e.type == "bill" and not d.get("autopay") and e.date:
        freq = d.get("frequency", "monthly")
        repeat = {"weekly": f"weekly:{e.date.weekday()}", "biweekly": "biweekly", "monthly": "monthly"}.get(freq)
        amt = f" (${d['amount']:,.2f})" if d.get("amount") else ""
        want = (f"Pay {d.get('name', 'bill')}{amt}", e.date, 10, repeat)
    elif e.type == "iou" and e.date and not d.get("settled") and d.get("amount"):
        amt = f"${d['amount']:,.2f}"
        who = d.get("person", "them")
        want = ((f"Pay {who} back {amt}", e.date, 10, None) if d.get("direction") == "i_owe"
                else (f"Remind {who} about the {amt} they owe you", e.date, 5, None))
    elif e.type == "promise" and e.date and d.get("status", "open") == "open":
        to = d.get("to")
        title = d.get("text", "Promise") + (f" (promised {to})" if to and to.lower() not in ("me", "myself") else "")
        want = (title[:300], e.date, int(d.get("minutes") or 30), None)
    t = db.get(Task, e.task_id) if e.task_id else None
    if want is None:
        if t is not None and t.status == "open":
            if e.type == "promise" and d.get("status") == "kept":
                t.status = "done"   # keep the record of having done it
            else:
                db.delete(t)
                e.task_id = None
        return
    title, due, minutes, repeat = want
    if t is None or t.status != "open":
        t = Task(title=title, due=due, est_minutes=minutes, course=area.name, course_id=area.id,
                 source=f"area:{area.kind}", repeat=repeat, needs_estimate=False, priority=2)
        db.add(t)
        db.flush()
        e.task_id = t.id
    else:
        t.title, t.due, t.repeat = title, due, repeat


def add_entry(db: Session, area: Course, etype: str, when: datetime | None, data: dict) -> AreaEntry:
    if etype not in ENTRY_TYPES.get(area.kind or "course", set()):
        raise ValueError(f"{area.name} doesn't hold {etype} entries")
    e = AreaEntry(area_id=area.id, type=etype, date=when, data=json.dumps(_clean(etype, data)))
    if etype == "metric" and when:
        # one metric row per day: merge into it
        day = datetime.combine(when.date(), time())
        existing = db.scalar(select(AreaEntry).where(AreaEntry.area_id == area.id, AreaEntry.type == "metric",
                                                     AreaEntry.date == day))
        if existing:
            merged = {**json.loads(existing.data or "{}"), **_clean(etype, data)}
            existing.data = json.dumps(merged)
            db.commit()
            return existing
        e.date = day
    db.add(e)
    db.flush()
    _sync_task(db, area, e)
    db.commit()
    return e


def update_entry(db: Session, e: AreaEntry, when: datetime | None, data: dict | None, clear_date: bool = False) -> AreaEntry:
    if data is not None and e.type == "promise" and data.get("status") in ("kept", "broken"):
        data = {**data, "closed_on": datetime.now().date().isoformat()}
    if data is not None:
        e.data = json.dumps({**json.loads(e.data or "{}"), **_clean(e.type, data)})
    if when is not None or clear_date:
        e.date = when
    _sync_task(db, db.get(Course, e.area_id), e)
    db.commit()
    return e


def delete_entry(db: Session, e: AreaEntry) -> None:
    t = db.get(Task, e.task_id) if e.task_id else None
    if t is not None and t.status == "open":
        db.delete(t)
    db.delete(e)
    db.commit()


def _setting(db: Session, key: str, default: str) -> str:
    row = db.get(Setting, key)
    return row.value if row else default


def summary(db: Session, area: Course, now: datetime) -> dict:
    rows = db.scalars(select(AreaEntry).where(AreaEntry.area_id == area.id).order_by(AreaEntry.date.desc().nullslast(),
                                                                                       AreaEntry.id.desc())).all()
    by = defaultdict(list)
    for r in rows:
        by[r.type].append(r)
    kind = area.kind or "course"
    out: dict = {"kind": kind}
    if kind == "fitness":
        target = float(_setting(db, "calorie_target", "2500"))
        protein_target = float(_setting(db, "protein_target", "150"))
        days = [(now.date() - timedelta(days=i)) for i in range(13, -1, -1)]
        cal = defaultdict(float)
        prot = defaultdict(float)
        for m in by["meal"]:
            if m.date:
                d = json.loads(m.data)
                cal[m.date.date()] += d.get("calories", 0)
                prot[m.date.date()] += d.get("protein", 0)
        metrics = {m.date.date(): json.loads(m.data) for m in by["metric"] if m.date}
        workouts = defaultdict(int)
        for w in by["workout"]:
            if w.date:
                workouts[w.date.date()] += json.loads(w.data).get("minutes", 0)
        out.update(
            calorie_target=target, protein_target=protein_target,
            today={"calories": cal[now.date()], "protein": prot[now.date()],
                   "meals": [entry_json(m) for m in by["meal"] if m.date and m.date.date() == now.date()]},
            days=[{"date": d.isoformat(), "calories": round(cal[d]), "protein": round(prot[d]),
                   "sleep_minutes": metrics.get(d, {}).get("sleep_minutes"), "steps": metrics.get(d, {}).get("steps"),
                   "weight": metrics.get(d, {}).get("weight"), "resting_hr": metrics.get(d, {}).get("resting_hr"),
                   "avg_hr": metrics.get(d, {}).get("avg_hr"), "workout_minutes": workouts[d]} for d in days],
            workouts=[entry_json(w) for w in by["workout"][:20]],
        )
    elif kind == "career":
        jobs = [entry_json(j) for j in by["job"]]
        counts = defaultdict(int)
        for j in jobs:
            counts[j["data"].get("stage", "wishlist")] += 1
        out.update(jobs=jobs, stages=JOB_STAGES, counts=counts)
    elif kind == "personal":
        balances: dict[str, float] = defaultdict(float)
        open_ious = []
        for x in by["iou"]:
            d = json.loads(x.data)
            if d.get("settled"):
                continue
            amt = d.get("amount", 0) * (1 if d.get("direction", "they_owe") == "they_owe" else -1)
            balances[d.get("person") or "Someone"] += amt
            open_ious.append(entry_json(x))
        promises = [entry_json(p) for p in by["promise"]]
        recent = [p for p in promises if p["data"].get("status") in ("kept", "broken")
                  and (p["data"].get("closed_on") or "") >= (now - timedelta(days=60)).date().isoformat()]
        kept = sum(p["data"].get("status") == "kept" for p in recent)
        memos = sorted((entry_json(m) for m in by["memo"]), key=lambda m: (not m["data"].get("pinned"), -m["id"]))
        out.update(
            balances=[{"person": k, "net": round(v, 2)} for k, v in sorted(balances.items(), key=lambda kv: -abs(kv[1])) if abs(v) > 0.004],
            owed_to_me=round(sum(v for v in balances.values() if v > 0), 2),
            i_owe=round(-sum(v for v in balances.values() if v < 0), 2),
            ious=open_ious, settled=[entry_json(x) for x in by["iou"] if json.loads(x.data).get("settled")][:20],
            promises_open=sorted([p for p in promises if p["data"].get("status", "open") == "open"],
                                 key=lambda p: p["date"] or "9999"),
            promises_closed=[p for p in promises if p["data"].get("status") in ("kept", "broken")][:30],
            word={"kept": kept, "total": len(recent)},
            memos=memos,
        )
    elif kind == "finance":
        month0 = now.date().replace(day=1)
        spent = defaultdict(float)
        for x in by["expense"]:
            if x.date and x.date.date() >= month0:
                d = json.loads(x.data)
                spent[d.get("category") or "Other"] += d.get("amount", 0)
        budgets = [entry_json(b) for b in by["budget"]]
        out.update(
            bills=sorted([entry_json(b) for b in by["bill"]], key=lambda b: b["date"] or "9999"),
            expenses=[entry_json(x) for x in by["expense"][:50]],
            budgets=budgets, spent=spent, month_total=round(sum(spent.values()), 2),
            budget_total=round(sum(b["data"].get("limit", 0) for b in budgets), 2),
        )
    return out


# ---- Photo meal estimates (Gemini vision, free tier) ------------------------------------
MEAL_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING", "description": "short name for the meal"},
        "items": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "food": {"type": "STRING"}, "portion": {"type": "STRING"}, "calories": {"type": "INTEGER"}},
            "required": ["food", "calories"]}},
        "calories": {"type": "INTEGER"}, "protein": {"type": "NUMBER"}, "carbs": {"type": "NUMBER"}, "fat": {"type": "NUMBER"},
        "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]},
    },
    "required": ["name", "items", "calories", "protein", "carbs", "fat", "confidence"],
}


def estimate_meal(image: bytes, mime: str, note: str = "") -> dict:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY isn't set. Add a free key from Google AI Studio in Render's environment.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    prompt = ("Estimate the nutrition of the food in this photo. Identify each item and its portion from visual cues "
              "(plate size, utensils, packaging). Use typical US dining-hall portions when unsure. Totals must equal the "
              "sum of the items." + (f" The person says: {note}" if note else ""))
    r = gemini.post(model, key, timeout=60, json={
        "contents": [{"parts": [{"inline_data": {"mime_type": mime, "data": base64.b64encode(image).decode()}},
                                {"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "responseSchema": MEAL_SCHEMA},
    })
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return json.loads("".join(p.get("text", "") for p in parts))
