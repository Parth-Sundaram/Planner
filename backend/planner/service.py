"""Glue between the database and the pure scheduler: replanning, check-ins, quick-add parsing."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import scheduler as S
from .models import DEFAULT_SETTINGS, Block, ExternalEvent, Routine, Rule, Setting, Task


# ---- Settings & time ----------------------------------------------------------

def get_settings(db: Session) -> dict:
    values = dict(DEFAULT_SETTINGS)
    for s in db.scalars(select(Setting)):
        values[s.key] = s.value
    return values


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def local_now(db: Session) -> datetime:
    """Naive datetime in the user's timezone. All stored times are naive local times."""
    tz = ZoneInfo(get_settings(db)["timezone"])
    return datetime.now(tz).replace(tzinfo=None, second=0, microsecond=0)


def hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def minutes_between(a: datetime, b: datetime) -> int:
    return int((b - a).total_seconds() // 60)


# ---- Replanning ----------------------------------------------------------------

def _rules(db: Session) -> list[dict]:
    return [{"tag": r.tag.lower().lstrip("#"), "weekday": r.weekday, "time": hm(r.time)} for r in db.scalars(select(Rule))]


def task_effective_due(task: Task, rules: list[dict]) -> datetime:
    return S.effective_due(task.due, task.tag_list, rules)


def replan(db: Session, now: datetime | None = None) -> dict:
    now = now or local_now(db)
    cfg = get_settings(db)
    horizon_end = datetime.combine(now.date() + timedelta(days=int(cfg["horizon_days"])), time.min)

    # 1. Throw away future planned blocks that the user hasn't touched; they get regenerated.
    db.execute(delete(Block).where(
        Block.start >= now, Block.status == "planned", Block.locked.is_(False),
        Block.kind.in_(["task", "routine"])))
    db.flush()

    # 2. Everything that stays is busy time: calendar events, locked/checked-in blocks,
    #    "something came up" blocks, and anything currently in progress.
    kept = db.scalars(select(Block).where(Block.end > now, Block.start < horizon_end)).all()
    busy = [S.Interval(max(b.start, now), b.end) for b in kept]
    for e in db.scalars(select(ExternalEvent).where(ExternalEvent.end > now, ExternalEvent.start < horizon_end)):
        busy.append(S.Interval(max(e.start, now), e.end))

    # A routine that already happened earlier today (or was pinned/checked in) isn't placed again.
    day0 = datetime.combine(now.date(), time.min)
    routine_skip: set[tuple[int, date]] = {
        (b.routine_id, b.start.date())
        for b in db.scalars(select(Block).where(Block.routine_id.is_not(None), Block.start >= day0, Block.start < horizon_end))}
    routines = [S.RoutineSpec(r.id, r.title, r.days, hm(r.window_start), hm(r.window_end), r.duration, r.kind)
                for r in db.scalars(select(Routine).where(Routine.active.is_(True)))]

    rules = _rules(db)
    open_tasks = db.scalars(select(Task).where(Task.status == "open")).all()
    by_id = {t.id: t for t in open_tasks}
    children: dict[int, list[Task]] = {}
    for t in open_tasks:
        if t.parent_id is not None:
            children.setdefault(t.parent_id, []).append(t)

    def remaining_of(t: Task) -> tuple[int, int]:
        placed = sum(minutes_between(max(b.start, now), b.end) for b in kept
                     if b.task_id == t.id and b.status == "planned")
        return max(0, t.est_minutes - t.done_minutes - placed), placed

    specs = []
    for t in open_tasks:
        if t.id in children:
            continue  # parents are scheduled through their subtasks
        parent = by_id.get(t.parent_id) if t.parent_id else None
        remaining, placed = remaining_of(t)
        tags = list(dict.fromkeys(t.tag_list + (parent.tag_list if parent else [])))
        eff = S.effective_due(t.due, tags, rules)
        sequential = bool(parent and parent.sequential)
        priority = parent.priority if sequential else t.priority
        if sequential:
            # A step must leave time for the steps after it: its deadline is the
            # earliest deadline of any later step.
            for sib in children.get(parent.id, []):
                if sib.position > t.position:
                    eff = min(eff, S.effective_due(sib.due, list(dict.fromkeys(sib.tag_list + parent.tag_list)), rules))
        specs.append(S.TaskSpec(
            id=t.id, title=f"{parent.title} › {t.title}" if parent else t.title,
            remaining=remaining, due=t.due, effective_due=eff, priority=priority,
            splittable=t.splittable, min_block=t.min_block, max_block=t.max_block,
            min_total=(max(0, t.min_total_minutes - t.done_minutes - placed)
                       if t.min_total_minutes is not None else None),
            earliest_start=t.earliest_start or (parent.earliest_start if parent else None),
            group=parent.id if parent else None, seq=t.position, sequential=sequential))

    result = S.plan(now, horizon_end, hm(cfg["day_start"]), hm(cfg["day_end"]),
                    busy, routines, specs, routine_skip)

    for b in result.blocks:
        db.add(Block(title=b.title, start=b.start, end=b.end, kind=b.kind,
                     task_id=b.task_id, routine_id=b.routine_id))

    for t in db.scalars(select(Task).where(Task.status == "open", Task.needs_estimate.is_(True))):
        result.warnings.append(S.Warning("info", f"{t.title} is using a default {t.est_minutes} min estimate — set a real one", t.id))

    summary = {
        "planned_at": now.isoformat(),
        "warnings": [w.__dict__ for w in result.warnings],
        "compressed": result.compressed,
        "shortfall": result.shortfall,
    }
    set_setting(db, "last_plan", json.dumps(summary))
    db.commit()
    return summary


# ---- Check-ins and adjustments ---------------------------------------------------

def check_in(db: Session, block_id: int, status: str, minutes: int | None = None) -> None:
    """status: done | partial | skipped. Partial takes the minutes actually worked."""
    b = db.get(Block, block_id)
    if b is None:
        raise KeyError(block_id)
    length = minutes_between(b.start, b.end)
    prev = b.done_minutes
    if status == "done":
        worked = length
    elif status == "partial":
        worked = max(0, min(minutes or 0, length))
    elif status == "skipped":
        worked = 0
    else:
        raise ValueError(status)
    b.status, b.done_minutes = status, worked
    if b.task_id:
        t = db.get(Task, b.task_id)
        t.done_minutes = max(0, t.done_minutes - prev + worked)
        if status == "done" and t.done_minutes >= t.est_minutes:
            t.status = "done"
            _close_parent_if_finished(db, t)
    replan(db)


def _close_parent_if_finished(db: Session, t: Task) -> None:
    if t.parent_id is None:
        return
    parent = db.get(Task, t.parent_id)
    siblings = db.scalars(select(Task).where(Task.parent_id == parent.id)).all()
    if all(s.status == "done" for s in siblings):
        parent.status = "done"


def finish_task(db: Session, task_id: int) -> None:
    """Mark a task done. Finishing a parent finishes all its subtasks; finishing the
    last open subtask finishes the parent."""
    t = db.get(Task, task_id)
    ids = [t.id] + [c.id for c in db.scalars(select(Task).where(Task.parent_id == t.id))]
    for tid in ids:
        db.get(Task, tid).status = "done"
    db.execute(delete(Block).where(Block.task_id.in_(ids), Block.status == "planned",
                                   Block.start >= local_now(db)))
    _close_parent_if_finished(db, t)
    replan(db)


def subtasks_of(db: Session, parent_id: int) -> list[Task]:
    return db.scalars(select(Task).where(Task.parent_id == parent_id).order_by(Task.position, Task.id)).all()


def next_position(db: Session, parent_id: int) -> int:
    subs = subtasks_of(db, parent_id)
    return (max(s.position for s in subs) + 1) if subs else 0


def something_came_up(db: Session, minutes: int, title: str = "Something came up") -> Block:
    now = local_now(db)
    blk = Block(title=title, start=now, end=now + timedelta(minutes=minutes), kind="busy", locked=True)
    # Anything planned that was in progress gets cut at "now"; the rest is replanned.
    for b in db.scalars(select(Block).where(Block.start < now, Block.end > now, Block.status == "planned")):
        b.end = now
    db.add(blk)
    db.flush()
    replan(db)
    return blk


def move_block(db: Session, block_id: int, start: datetime, end: datetime) -> None:
    b = db.get(Block, block_id)
    if end <= start:
        raise ValueError("end must be after start")
    b.start, b.end, b.locked = start, end, True
    replan(db)


def unlock_block(db: Session, block_id: int) -> None:
    b = db.get(Block, block_id)
    b.locked = False
    replan(db)


# ---- Quick-add / metadata parsing ---------------------------------------------------

DURATION = re.compile(r"~\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|m|min)\b", re.I)
SQUEEZE = re.compile(r"squeeze[:=]\s*(\d+(?:\.\d+)?)\s*(h|hr|hrs|m|min)\b", re.I)
MINBLOCK = re.compile(r"min[:=]\s*(\d+)\s*(h|m|min)?\b", re.I)
PRIORITY = re.compile(r"(?<![\w!])!([123])\b")
TAG = re.compile(r"(?<![\w&])#([A-Za-z][\w-]*)")
NOSPLIT = re.compile(r"\bnosplit\b", re.I)


def _to_minutes(num: str, unit: str | None) -> int:
    v = float(num)
    return int(round(v * 60)) if (unit or "m").lower().startswith("h") else int(round(v))


def parse_meta(text: str) -> tuple[str, dict]:
    """Pull planner metadata out of free text.

    "CNN 4/4 ~4h !1 #ai squeeze:3h min:45 nosplit" ->
    title "CNN 4/4", est 240, priority 1, tags ["ai"], min_total 180, min_block 45, splittable False.
    Anything not recognized stays in the title.
    """
    meta: dict = {}
    if m := SQUEEZE.search(text):
        meta["min_total_minutes"] = _to_minutes(m.group(1), m.group(2))
        text = text.replace(m.group(0), " ")
    if m := DURATION.search(text):
        meta["est_minutes"] = _to_minutes(m.group(1), m.group(2))
        text = text.replace(m.group(0), " ")
    if m := MINBLOCK.search(text):
        meta["min_block"] = _to_minutes(m.group(1), m.group(2) or "m")
        text = text.replace(m.group(0), " ")
    if m := PRIORITY.search(text):
        meta["priority"] = int(m.group(1))
        text = text.replace(m.group(0), " ")
    tags = TAG.findall(text)
    if tags:
        meta["tags"] = [t.lower() for t in tags]
        text = TAG.sub(" ", text)
    if NOSPLIT.search(text):
        meta["splittable"] = False
        text = NOSPLIT.sub(" ", text)
    return " ".join(text.split()), meta
