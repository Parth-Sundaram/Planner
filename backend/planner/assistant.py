"""The planner assistant: free (Gemini free tier), quick, and it never changes anything on its own.

Each message is sent with a compact snapshot of your plan (next few days of blocks, open
deadlines, routines, areas) and the notes it has saved about how you work. The model answers
with text plus proposed actions (function calls). Saving or deleting a note happens right
away; everything else waits for you to approve it in the UI, then runs through the same
service functions the rest of the app uses, so replanning and deadline rules still apply.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import service
from .models import AssistantMessage, AssistantNote, Block, Course, Routine, Task

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

S, I, B = {"type": "STRING"}, {"type": "INTEGER"}, {"type": "BOOLEAN"}
TOOLS = [
    {"name": "add_task", "description": "Create a task with a deadline. The planner schedules it.",
     "parameters": {"type": "OBJECT", "properties": {
         "title": S, "due": {**S, "description": "YYYY-MM-DDTHH:MM local time"}, "est_minutes": I,
         "area_id": {**I, "description": "course or area id from the snapshot"}, "priority": {**I, "description": "1 high, 2 normal, 3 low"},
         "repeat": {**S, "description": "daily | weekdays | weekly | biweekly | monthly"}, "notes": S},
         "required": ["title", "due"]}},
    {"name": "update_task", "description": "Change a task: deadline, time needed, priority, confidence for a test (1 lost..5 ready), title, or mark it done.",
     "parameters": {"type": "OBJECT", "properties": {
         "task_id": I, "due": S, "est_minutes": I, "done_minutes": I, "priority": I, "confidence": I, "title": S,
         "status": {**S, "description": "open | done"}}, "required": ["task_id"]}},
    {"name": "move_block", "description": "Move a planned block to an exact time. It gets pinned there.",
     "parameters": {"type": "OBJECT", "properties": {"block_id": I, "start": S, "end": S}, "required": ["block_id", "start", "end"]}},
    {"name": "not_now", "description": "Free a work block's slot; its work moves later (still before the deadline).",
     "parameters": {"type": "OBJECT", "properties": {"block_id": I}, "required": ["block_id"]}},
    {"name": "skip_routine", "description": "Skip a routine block (meal, gym, chore) for that day only.",
     "parameters": {"type": "OBJECT", "properties": {"block_id": I}, "required": ["block_id"]}},
    {"name": "block_time", "description": "Block off time (sick, errand, meeting not on the calendar) so nothing is planned then.",
     "parameters": {"type": "OBJECT", "properties": {"start": S, "minutes": I, "title": S}, "required": ["start", "minutes"]}},
    {"name": "update_routine", "description": "Change a weekly routine: days (subset of MTWRFSU), window, length, or turn it off.",
     "parameters": {"type": "OBJECT", "properties": {"routine_id": I, "days": S, "window_start": S, "window_end": S,
                                                     "duration": I, "active": B}, "required": ["routine_id"]}},
    {"name": "remember", "description": "Save a durable fact about how the user works or what they prefer, for future planning.",
     "parameters": {"type": "OBJECT", "properties": {"note": S}, "required": ["note"]}},
    {"name": "forget", "description": "Delete a saved note that is wrong or outdated.",
     "parameters": {"type": "OBJECT", "properties": {"note_id": I}, "required": ["note_id"]}},
]
IMMEDIATE = {"remember", "forget"}

SYSTEM = """You are the planning assistant inside a student's personal planner. The planner already
schedules work automatically (earliest deadline first, around classes, meals and routines) and
replans after every change, so you usually change inputs (deadlines, estimates, priorities,
blocked time, skips) rather than placing every block by hand.

Be brief and concrete: one or two sentences, then propose actions. Use ids from the snapshot.
Times are local, formatted YYYY-MM-DDTHH:MM. Never invent tasks or meetings the user didn't
mention. If a request is ambiguous in a way that matters, ask one short question instead of
acting. When the user tells you something durable about how they work, call remember.
If everything can't fit, say what slips and suggest the trade-off; don't silently drop things."""


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%a %m-%d %H:%M") if dt else "-"


def snapshot(db: Session, now: datetime) -> str:
    """A compact text picture of the plan. Kept small so replies stay fast (and free)."""
    horizon = now + timedelta(days=4)
    lines = [f"NOW: {now:%A %Y-%m-%d %H:%M}"]
    areas = list(db.scalars(select(Course)))
    names = {a.id: a.name for a in areas}
    lines.append("AREAS: " + "; ".join(f"{a.id}={a.name} ({a.kind or 'course'})" for a in areas))
    lines.append("BLOCKS (next 4 days; id | when | title | kind/status):")
    for b in db.scalars(select(Block).where(Block.end > now, Block.start < horizon).order_by(Block.start)):
        flags = "/".join(x for x in (b.kind, b.status if b.status != "planned" else "", "pinned" if b.locked else "") if x)
        lines.append(f"  b{b.id} | {_fmt(b.start)}-{b.end:%H:%M} | {b.title} | {flags}")
    lines.append("OPEN TASKS (id | due | title | area | done/needed min | priority | extra):")
    rules = service._rules(db)
    for t in db.scalars(select(Task).where(Task.status == "open").order_by(Task.due).limit(60)):
        extra = []
        eff = service.task_effective_due(t, rules)
        if eff != t.due:
            extra.append(f"target {_fmt(eff)}")
        if t.confidence:
            extra.append(f"confidence {t.confidence}/5")
        if t.repeat:
            extra.append(f"repeats {t.repeat}")
        if t.parent_id:
            extra.append(f"step of t{t.parent_id}")
        if t.needs_estimate:
            extra.append("estimate is a guess")
        lines.append(f"  t{t.id} | {_fmt(t.due)} | {t.title} | {names.get(t.course_id, '-')} | "
                     f"{t.done_minutes}/{t.est_minutes} | p{t.priority} | {', '.join(extra)}")
    lines.append("ROUTINES (id | days | window | minutes):")
    for r in db.scalars(select(Routine).where(Routine.active.is_(True))):
        lines.append(f"  r{r.id} | {r.title} | {r.days} | {r.window_start}-{r.window_end} | {r.duration}")
    plan = json.loads(service.get_settings(db).get("last_plan") or "{}")
    if plan.get("warnings"):
        lines.append("PLANNER WARNINGS: " + " / ".join(w["message"] for w in plan["warnings"][:8]))
    notes = list(db.scalars(select(AssistantNote).order_by(AssistantNote.id)))
    if notes:
        lines.append("NOTES YOU SAVED EARLIER (id: note):")
        lines += [f"  n{n.id}: {n.text}" for n in notes[-40:]]
    return "\n".join(lines)


def _call(contents: list, system: str) -> dict:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("The assistant needs GEMINI_API_KEY (free, from Google AI Studio) set in Render.")
    model = os.getenv("GEMINI_ASSISTANT_MODEL", "gemini-2.5-flash-lite")
    gen = {"temperature": 0.3}
    if "2.5" in model:
        gen["thinkingConfig"] = {"thinkingBudget": 0}  # fastest replies; the snapshot does the heavy lifting
    r = requests.post(GEMINI_URL.format(model=model), headers={"x-goog-api-key": key}, timeout=45, json={
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "tools": [{"functionDeclarations": TOOLS}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "generationConfig": gen,
    })
    if r.status_code == 429:
        raise RuntimeError("Gemini's free limit is used up for the moment. Try again in a minute.")
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
    return r.json()


def _ids(v) -> int:
    return int(str(v).lstrip("tbrn"))


def describe(db: Session, name: str, args: dict) -> str:
    def blk(i):
        b = db.get(Block, _ids(i))
        return f"{b.title} ({_fmt(b.start)})" if b else f"block {i}"

    def task(i):
        t = db.get(Task, _ids(i))
        return t.title if t else f"task {i}"
    if name == "add_task":
        return f"Add \"{args.get('title')}\" due {args.get('due', '?').replace('T', ' ')}" + (
            f", {args['est_minutes']} min" if args.get("est_minutes") else "")
    if name == "update_task":
        changes = ", ".join(f"{k.replace('_', ' ')} → {v}" for k, v in args.items() if k != "task_id")
        return f"Update {task(args.get('task_id'))}: {changes}"
    if name == "move_block":
        return f"Move {blk(args.get('block_id'))} to {str(args.get('start', '')).replace('T', ' ')}–{str(args.get('end', ''))[-5:]}"
    if name == "not_now":
        return f"Not now: {blk(args.get('block_id'))}"
    if name == "skip_routine":
        return f"Skip {blk(args.get('block_id'))}"
    if name == "block_time":
        return f"Block {args.get('minutes')} min at {str(args.get('start', '')).replace('T', ' ')}: {args.get('title') or 'Busy'}"
    if name == "update_routine":
        r = db.get(Routine, _ids(args.get("routine_id", 0)))
        changes = ", ".join(f"{k.replace('_', ' ')} → {v}" for k, v in args.items() if k != "routine_id")
        return f"Change routine {r.title if r else args.get('routine_id')}: {changes}"
    if name == "remember":
        return f"Noted: {args.get('note')}"
    if name == "forget":
        return f"Forgot note {args.get('note_id')}"
    return name


def chat(db: Session, message: str, now: datetime | None = None) -> dict:
    now = now or service.local_now(db)
    history = list(db.scalars(select(AssistantMessage).order_by(AssistantMessage.id.desc()).limit(10)))[::-1]
    contents = [{"role": m.role, "parts": [{"text": m.text or "(proposed changes)"}]} for m in history]
    contents.append({"role": "user", "parts": [{"text": f"{message}\n\n--- PLAN SNAPSHOT ---\n{snapshot(db, now)}"}]})
    resp = _call(contents, SYSTEM)
    parts = (resp.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = " ".join(p["text"].strip() for p in parts if p.get("text")).strip()
    actions = []
    for i, p in enumerate(parts):
        fc = p.get("functionCall")
        if not fc or fc.get("name") not in {t["name"] for t in TOOLS}:
            continue
        args = fc.get("args") or {}
        a = {"id": i, "name": fc["name"], "args": args, "summary": describe(db, fc["name"], args)}
        if fc["name"] in IMMEDIATE:
            a["result"] = apply_one(db, a)
            a["applied"] = True
        actions.append(a)
    db.add(AssistantMessage(role="user", text=message))
    db.add(AssistantMessage(role="model", text=text or "; ".join(a["summary"] for a in actions),
                            actions=json.dumps(actions)))
    db.commit()
    return {"reply": text, "actions": actions}


def _dt(v) -> datetime:
    return datetime.fromisoformat(str(v).replace("Z", "")[:16])


def apply_one(db: Session, a: dict) -> str:
    n, x = a["name"], a.get("args") or {}
    if n == "add_task":
        t = Task(title=x["title"], due=_dt(x["due"]), est_minutes=int(x.get("est_minutes") or 60),
                 needs_estimate=not x.get("est_minutes"), priority=int(x.get("priority") or 2), notes=x.get("notes") or "")
        if x.get("area_id") and (c := db.get(Course, _ids(x["area_id"]))):
            t.course_id, t.course = c.id, c.name
        rep = x.get("repeat")
        if rep:
            t.repeat = f"weekly:{t.due.weekday()}" if rep == "weekly" else rep
        db.add(t)
        db.commit()
        return f"added t{t.id}"
    if n == "update_task":
        t = db.get(Task, _ids(x["task_id"]))
        if t is None:
            raise ValueError("that task doesn't exist")
        if "confidence" in x:
            service.apply_confidence(t, int(x["confidence"]) or None)
        for k in ("title", "est_minutes", "done_minutes", "priority"):
            if k in x:
                setattr(t, k, x[k])
        if "est_minutes" in x:
            t.needs_estimate = False
        if x.get("due"):
            t.due = _dt(x["due"])
        if x.get("status") == "done" and t.status != "done":
            service.finish_task(db, t.id)
            return "done"
        if x.get("status") == "open":
            t.status = "open"
        db.commit()
        return "updated"
    if n == "move_block":
        service.move_block(db, _ids(x["block_id"]), _dt(x["start"]), _dt(x["end"]))
        return "moved"
    if n == "not_now":
        service.not_now(db, _ids(x["block_id"]))
        return "pushed later"
    if n == "skip_routine":
        service.skip_routine(db, _ids(x["block_id"]))
        return "skipped"
    if n == "block_time":
        service.block_time(db, _dt(x["start"]), int(x["minutes"]), x.get("title") or "Busy")
        return "blocked"
    if n == "update_routine":
        r = db.get(Routine, _ids(x["routine_id"]))
        if r is None:
            raise ValueError("that routine doesn't exist")
        for k in ("days", "window_start", "window_end", "duration", "active"):
            if k in x:
                setattr(r, k, x[k])
        db.commit()
        return "routine changed"
    if n == "remember":
        db.add(AssistantNote(text=str(x["note"])[:500]))
        db.commit()
        return "saved"
    if n == "forget":
        note = db.get(AssistantNote, _ids(x["note_id"]))
        if note:
            db.delete(note)
            db.commit()
        return "forgotten"
    raise ValueError(f"unknown action {n}")


def _signature(db: Session, bid) -> tuple | None:
    b = db.get(Block, _ids(bid))
    return (b.task_id, b.routine_id, b.start, b.kind) if b else None


def _resolve(db: Session, sig: tuple) -> int | None:
    """Each change replans, which can renumber blocks. Find the same block again by what it is."""
    task_id, routine_id, start, kind = sig
    q = select(Block).where(Block.kind == kind)
    q = q.where(Block.task_id == task_id) if task_id else q.where(Block.routine_id == routine_id) if routine_id else q
    cands = list(db.scalars(q))
    same_day = [b for b in cands if b.start.date() == start.date()]
    pick = min(same_day or cands, key=lambda b: abs((b.start - start).total_seconds()), default=None)
    return pick.id if pick else None


def apply(db: Session, actions: list[dict]) -> list[dict]:
    results = []
    sigs = {a.get("id"): _signature(db, a["args"]["block_id"]) for a in actions
            if isinstance(a.get("args"), dict) and "block_id" in a["args"]}
    for a in actions:
        sig = sigs.get(a.get("id"))
        if sig:
            a = {**a, "args": {**a["args"], "block_id": _resolve(db, sig) or a["args"]["block_id"]}}
        if a.get("applied") or a.get("name") in IMMEDIATE:
            continue
        try:
            results.append({"id": a.get("id"), "ok": True, "result": apply_one(db, a)})
        except Exception as e:
            db.rollback()
            results.append({"id": a.get("id"), "ok": False, "error": f"{e}"[:200]})
    service.replan(db)
    return results
