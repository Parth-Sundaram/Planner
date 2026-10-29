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


# ---- Exam prep from confidence -------------------------------------------------
# confidence 1 (lost) .. 5 (ready) -> (prep minutes, days before the deadline to spread it over)
CONFIDENCE_PREP = {1: (600, 7), 2: (420, 6), 3: (240, 4), 4: (120, 3), 5: (60, 1)}


def apply_confidence(t: Task, level: int | None) -> None:
    """Set an exam/quiz's prep time from how ready you feel. You can still edit the minutes after."""
    t.confidence = level
    if level is None:
        t.max_per_day = None
        return
    minutes, days = CONFIDENCE_PREP[level]
    t.est_minutes = max(minutes, t.done_minutes)
    t.needs_estimate = False
    t.max_block = min(t.max_block or 90, 90)  # study in sittings of at most 90 minutes
    t.max_per_day = None  # derived from remaining time at plan time


def prep_window(t: Task, day_start: time, now: datetime) -> tuple[datetime | None, int | None]:
    """For a task with a confidence level: earliest start and per-day cap that spread the prep
    evenly over the days left in its window (the deadline day itself counts if it's after class)."""
    if not t.confidence:
        return t.earliest_start, t.max_per_day
    _, days = CONFIDENCE_PREP[t.confidence]
    start = datetime.combine(t.due.date() - timedelta(days=days), day_start)
    first = max(start.date(), now.date())
    days_left = max(1, (t.due.date() - first).days + (1 if t.due.time() >= time(12) else 0))
    left = max(0, t.est_minutes - t.done_minutes)
    cap = t.max_per_day or (-(-left // days_left // 15) * 15 + 15)  # rounded up, plus a little slack
    es = max(start, t.earliest_start) if t.earliest_start else start
    return es, cap


# ---- Replanning ----------------------------------------------------------------

def _rules(db: Session) -> list[dict]:
    return [{"tag": r.tag.lower().lstrip("#"), "weekday": r.weekday, "time": hm(r.time)} for r in db.scalars(select(Rule))]


def task_effective_due(task: Task, rules: list[dict]) -> datetime:
    return S.effective_due(task.due, task.tag_list, rules)


def materialize_repeats(db: Session, until: datetime) -> int:
    """Repeating tasks: make sure every occurrence inside the planning window exists, so a
    daily quiz shows up every day even if yesterday's wasn't ticked off."""
    from .quickadd import next_due
    made = 0
    latest: dict[int, Task] = {}
    for t in db.scalars(select(Task).where(Task.repeat.is_not(None), Task.parent_id.is_(None))):
        sid = t.series_id or t.id
        if sid not in latest or t.due > latest[sid].due:
            latest[sid] = t
    for sid, t in latest.items():
        if t.status not in ("open", "done"):
            continue
        guard = 0
        while (nd := next_due(t.due, t.repeat)) <= until and guard < 60:
            nxt = spawn_next(db, t)
            guard += 1
            if nxt is None:
                break
            db.flush()
            t, made = nxt, made + 1
    return made


def replan(db: Session, now: datetime | None = None) -> dict:
    now = now or local_now(db)
    cfg = get_settings(db)
    horizon_end = datetime.combine(now.date() + timedelta(days=int(cfg["horizon_days"])), time.min)
    materialize_repeats(db, horizon_end)

    # 1. Throw away future planned blocks that the user hasn't touched; they get regenerated.
    db.execute(delete(Block).where(
        Block.start >= now, Block.status == "planned", Block.locked.is_(False),
        Block.kind.in_(["task", "routine"])))
    db.flush()

    # 2. Everything that stays is busy time: calendar events, locked/checked-in blocks,
    #    "something came up" blocks, and anything currently in progress.
    kept = db.scalars(select(Block).where(Block.end > now, Block.start < horizon_end)).all()
    busy = [S.Interval(max(b.start, now), b.end) for b in kept if b.status != "skipped"]  # a skipped slot is free again
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

    late_pins: list[tuple[Task, Block]] = []

    def remaining_of(t: Task) -> tuple[int, int]:
        # Only time before the deadline counts. A block pinned after the due date doesn't
        # get the work done in time, so that work is still planned before the deadline.
        placed = 0
        for b in kept:
            if b.task_id != t.id or b.status != "planned":
                continue
            end = min(b.end, t.due)
            if end > max(b.start, now):
                placed += minutes_between(max(b.start, now), end)
            if b.end > t.due:
                late_pins.append((t, b))
        return max(0, t.est_minutes - t.done_minutes - placed), placed

    specs = []
    day_start_t = hm(cfg["day_start"])
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
            earliest_start=prep_window(t, day_start_t, now)[0] or (parent.earliest_start if parent else None),
            group=parent.id if parent else None, seq=t.position, sequential=sequential,
            max_per_day=prep_window(t, day_start_t, now)[1]))

    result = S.plan(now, horizon_end, hm(cfg["day_start"]), hm(cfg["day_end"]),
                    busy, routines, specs, routine_skip)

    for b in result.blocks:
        db.add(Block(title=b.title, start=b.start, end=b.end, kind=b.kind,
                     task_id=b.task_id, routine_id=b.routine_id))

    for t, b in late_pins:
        result.warnings.append(S.Warning(
            "warn", f"{t.title}: the block you placed {S.fmt_time(b.start)} is after the deadline "
                    f"({S.fmt_dt(t.due)}), so that time was planned again before it", t.id))

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
            spawn_next(db, t)
            _close_parent_if_finished(db, t)
    replan(db)


def spawn_next(db: Session, t: Task) -> Task | None:
    """When a repeating task is finished, create the next one (once)."""
    from .quickadd import next_due
    if not t.repeat or t.parent_id:
        return None
    series = t.series_id or t.id
    due = next_due(t.due, t.repeat)
    ext = f"repeat:{series}:{due.date().isoformat()}"
    if db.scalar(select(Task).where(Task.external_id == ext)):
        return None
    nxt = Task(title=t.title, course=t.course, course_id=t.course_id, due=due, est_minutes=t.est_minutes,
               priority=t.priority, splittable=t.splittable, min_block=t.min_block, max_block=t.max_block,
               tags=t.tags, notes=t.notes, repeat=t.repeat, series_id=series, external_id=ext,
               source=t.source if t.source == "manual" else "manual", needs_estimate=False,
               earliest_start=(t.earliest_start + (due - t.due)) if t.earliest_start else None)
    if t.series_id is None:
        t.series_id = series
    db.add(nxt)
    return nxt


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
    spawn_next(db, t)
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


def block_time(db: Session, start: datetime, minutes: int, title: str = "Busy") -> Block:
    """Block off time at a given moment (not just now) and replan around it."""
    blk = Block(title=title, start=start, end=start + timedelta(minutes=minutes), kind="busy", locked=True)
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


def not_now(db: Session, block_id: int) -> None:
    """Drop a work block and push that task's work to after it. The slot goes to other work."""
    b = db.get(Block, block_id)
    if b is None or b.kind != "task":
        raise ValueError("Not now only applies to work blocks")
    t = db.get(Task, b.task_id)
    t.earliest_start = max(t.earliest_start or b.end, b.end)
    db.delete(b)
    replan(db)


def skip_routine(db: Session, block_id: int) -> None:
    """Skip a routine for that day only. The block stays (as skipped) so it isn't placed again."""
    b = db.get(Block, block_id)
    if b is None or b.kind != "routine":
        raise ValueError("Skip only applies to routines")
    b.status, b.locked = "skipped", False
    replan(db)


def restore_block(db: Session, block_id: int) -> None:
    """Undo a skip: the routine goes back into the plan for that day."""
    b = db.get(Block, block_id)
    if b.kind == "routine":
        db.delete(b)
    else:
        b.status, b.done_minutes = "planned", 0
    replan(db)


def remove_block(db: Session, block_id: int) -> None:
    """Delete a "something came up" block (or any busy block) outright."""
    b = db.get(Block, block_id)
    if b.kind != "busy":
        raise ValueError("Use Not now or Skip for planned blocks")
    db.delete(b)
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


# ---- Smart quick-add -------------------------------------------------------------------

def smart_fill(db: Session, title: str, now: datetime) -> dict:
    """Fill in due date, repeat, estimate and course/area from a short line. Returns the
    cleaned title plus whatever it could work out, and how ("why") for the UI."""
    from . import quickadd as Q
    due, repeat, clean = Q.parse_when(title, now)
    out: dict = {"title": clean or title, "why": {}}
    if due:
        out["due"], out["why"]["due"] = due, "from your text"
    if repeat:
        out["repeat"] = repeat
    history = [(t.title, t.est_minutes) for t in db.scalars(
        select(Task).where(Task.status == "done", Task.parent_id.is_(None)).order_by(Task.id.desc()).limit(300))]
    est = Q.estimate_from_history(out["title"], history)
    if est:
        out["est_minutes"], out["why"]["est"] = est, "like similar tasks you've finished"
    elif (est := Q.default_minutes(out["title"])):
        out["est_minutes"], out["why"]["est"] = est, "typical for this kind of task"
    from .scrapers import get_courses
    area = Q.guess_area(out["title"], get_courses(db))
    if area:
        out["course_id"], out["why"]["area"] = area.id, area.name
    return out
