"""Pure scheduling logic: no database, no Flask. Everything here is testable in isolation.

Given the fixed parts of a week (busy events, locked blocks, routines) and a list of
tasks with durations and deadlines, produce time blocks and warnings.

Strategy
--------
1. Free time = each day's working window minus everything already occupied.
2. Routines (meals, gym, chores) are placed first, each at the earliest free spot
   inside its allowed window (e.g. lunch anywhere 12:00-13:45).
3. Tasks are placed earliest-deadline-first (EDF). EDF is the classic optimal rule
   for "can everything finish before its deadline" when work can be split, so if
   EDF can't fit the work, nothing can. Each task fills the earliest free slots,
   in chunks between its min and max block length.
4. If something can't fit, a second pass "compresses": tasks that allow it are
   reduced to their minimum acceptable time. Anything still short is reported as
   at risk, and work that misses a soft (rule) deadline may spill up to the real one.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

GRANULARITY = 5  # minutes; all block edges land on multiples of this


@dataclass
class Interval:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class RoutineSpec:
    id: int
    title: str
    days: str            # subset of "MTWRFSU" (R = Thursday, U = Sunday)
    window_start: time
    window_end: time
    duration: int        # minutes
    kind: str = "routine"


@dataclass
class TaskSpec:
    id: int
    title: str
    remaining: int               # minutes still to schedule
    due: datetime                # real deadline
    effective_due: datetime      # deadline after rules (e.g. #db -> Wednesday night)
    priority: int = 2            # 1 = high, 3 = low
    splittable: bool = True
    min_block: int = 30
    max_block: int = 180
    min_total: int | None = None  # minutes it can be squeezed down to, if compressible
    earliest_start: datetime | None = None
    group: int | None = None      # parent task id, for subtasks
    seq: int = 0                  # position within the parent
    sequential: bool = False      # subtasks must happen in order
    max_per_day: int | None = None  # spread work out: at most this many minutes per day


@dataclass
class PlannedBlock:
    title: str
    start: datetime
    end: datetime
    kind: str                     # "task" | "routine"
    task_id: int | None = None
    routine_id: int | None = None


@dataclass
class Warning:
    level: str      # "info" | "warn" | "danger"
    message: str
    task_id: int | None = None


@dataclass
class PlanResult:
    blocks: list[PlannedBlock] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    compressed: dict[int, int] = field(default_factory=dict)  # task_id -> minutes cut
    shortfall: dict[int, int] = field(default_factory=dict)   # task_id -> minutes unscheduled


DAY_CODES = "MTWRFSU"  # Monday=0 ... Sunday=6, matching date.weekday()


def fmt_day(d) -> str:
    # Avoid %-d / %-I: they work on Linux/macOS but crash on Windows.
    return f"{d:%a %b} {d.day}"


def fmt_dt(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{fmt_day(dt)} {hour}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def fmt_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    return f"{dt:%a} {hour}:{dt:%M} {'AM' if dt.hour < 12 else 'PM'}"


def round_up(dt: datetime) -> datetime:
    dt = dt.replace(second=0, microsecond=0)
    extra = (-dt.minute) % GRANULARITY
    return dt + timedelta(minutes=extra)


def subtract(free: list[Interval], busy: Interval) -> list[Interval]:
    out = []
    for f in free:
        if busy.end <= f.start or busy.start >= f.end:
            out.append(f)
            continue
        if busy.start > f.start:
            out.append(Interval(f.start, busy.start))
        if busy.end < f.end:
            out.append(Interval(busy.end, f.end))
    return [i for i in out if i.minutes > 0]


def build_free_time(now: datetime, horizon_end: datetime, day_start: time, day_end: time,
                    busy: list[Interval]) -> list[Interval]:
    free: list[Interval] = []
    day = now.date()
    while datetime.combine(day, time.min) < horizon_end:
        s = datetime.combine(day, day_start)
        e = datetime.combine(day, day_end)
        s = max(s, round_up(now))
        e = min(e, horizon_end)
        if e > s:
            free.append(Interval(s, e))
        day += timedelta(days=1)
    for b in sorted(busy, key=lambda i: i.start):
        free = subtract(free, b)
    return free


def place_routines(now: datetime, horizon_end: datetime, routines: list[RoutineSpec],
                   free: list[Interval], skip: set[tuple[int, date]]) -> tuple[list[PlannedBlock], list[Warning], list[Interval]]:
    """Place each routine occurrence at the earliest free spot in its window.

    `skip` holds (routine_id, date) pairs that already have a locked/done block.
    """
    blocks, warnings = [], []
    day = now.date()
    while datetime.combine(day, time.min) < horizon_end:
        code = DAY_CODES[day.weekday()]
        for r in routines:
            if code not in r.days or (r.id, day) in skip:
                continue
            w_start = datetime.combine(day, r.window_start)
            w_end = datetime.combine(day, r.window_end)
            if w_end <= round_up(now):
                continue  # this occurrence's window has already passed
            placed = False
            for f in free:
                s = max(f.start, w_start)
                e = min(f.end, w_end)
                if (e - s) >= timedelta(minutes=r.duration):
                    blk = PlannedBlock(r.title, s, s + timedelta(minutes=r.duration), "routine", routine_id=r.id)
                    blocks.append(blk)
                    free = subtract(free, Interval(blk.start, blk.end))
                    placed = True
                    break
            if not placed:
                warnings.append(Warning("warn", f"No room for {r.title} on {fmt_day(day)}"))
        day += timedelta(days=1)
    return blocks, warnings, free


def _allocate(task: TaskSpec, minutes: int, deadline: datetime, now: datetime,
              free: list[Interval], existing: list[PlannedBlock] | None = None) -> tuple[list[PlannedBlock], int, list[Interval]]:
    """Greedily fill the earliest free time before `deadline`. Returns blocks, minutes left over."""
    blocks: list[PlannedBlock] = []
    left = minutes
    start_floor = max(round_up(now), task.earliest_start or now)
    per_day: dict[date, int] = {}
    if task.max_per_day:
        for b in existing or []:
            per_day[b.start.date()] = per_day.get(b.start.date(), 0) + int((b.end - b.start).total_seconds() // 60)
    for f in list(free):
        if left <= 0:
            break
        s = max(f.start, start_floor)
        e = min(f.end, deadline)
        avail = int((e - s).total_seconds() // 60) if e > s else 0
        if task.max_per_day and avail > 0:
            avail = min(avail, max(0, task.max_per_day - per_day.get(s.date(), 0)))
        if not task.splittable:
            if avail >= left:
                blocks.append(PlannedBlock(task.title, s, s + timedelta(minutes=left), "task", task_id=task.id))
                left = 0
            continue
        while left > 0 and avail > 0:
            chunk = min(left, task.max_block, avail)
            tail = left - chunk
            if 0 < tail < task.min_block and chunk - (task.min_block - tail) >= task.min_block:
                chunk -= task.min_block - tail  # don't leave a sliver for later
            # Don't create a sliver shorter than min_block unless it finishes the task.
            if chunk < task.min_block and chunk < left:
                break
            chunk -= chunk % GRANULARITY if chunk != left else 0
            if chunk <= 0:
                break
            blocks.append(PlannedBlock(task.title, s, s + timedelta(minutes=chunk), "task", task_id=task.id))
            per_day[s.date()] = per_day.get(s.date(), 0) + chunk
            s += timedelta(minutes=chunk)
            left -= chunk
            avail -= chunk
    for b in blocks:
        free = subtract(free, Interval(b.start, b.end))
    return blocks, left, free


def _schedule_tasks(tasks: list[TaskSpec], now: datetime, free: list[Interval],
                    amounts: dict[int, int]) -> tuple[list[PlannedBlock], dict[int, int], dict[int, int], list[Interval]]:
    """One EDF pass. Returns blocks, shortfall vs soft deadline, spill-over used, remaining free."""
    blocks: list[PlannedBlock] = []
    short_soft: dict[int, int] = {}
    short_hard: dict[int, int] = {}
    order = sorted(tasks, key=lambda t: (t.effective_due, t.priority, t.seq, -t.remaining))
    group_end: dict[int, datetime] = {}  # last scheduled minute of each sequential parent so far
    for t in order:
        want = amounts[t.id]
        if want <= 0:
            continue
        if t.sequential and t.group in group_end:
            # In-order subtasks: this one can't start before the previous one finishes.
            floor = group_end[t.group]
            if t.earliest_start is None or t.earliest_start < floor:
                t = replace(t, earliest_start=floor)
        got, left, free = _allocate(t, want, t.effective_due, now, free)
        blocks += got
        if left > 0:
            short_soft[t.id] = left
            if t.effective_due < t.due:
                # Soft deadline missed: let the rest spill up to the real deadline.
                more, left, free = _allocate(t, left, t.due, now, free, existing=got)
                blocks += more
            if left > 0:
                short_hard[t.id] = left
        if t.sequential and t.group is not None:
            mine = [b.end for b in blocks if b.task_id == t.id]
            if mine:
                group_end[t.group] = max(mine + [group_end.get(t.group, mine[0])])
    return blocks, short_soft, short_hard, free


def plan(now: datetime, horizon_end: datetime, day_start: time, day_end: time,
         busy: list[Interval], routines: list[RoutineSpec], tasks: list[TaskSpec],
         routine_skip: set[tuple[int, date]] | None = None) -> PlanResult:
    result = PlanResult()
    free = build_free_time(now, horizon_end, day_start, day_end, busy)
    r_blocks, r_warn, free = place_routines(now, horizon_end, routines, free, routine_skip or set())
    result.blocks += r_blocks
    result.warnings += r_warn

    tasks = [t for t in tasks if t.remaining > 0]
    for t in tasks:
        if t.due <= now:
            result.warnings.append(Warning("danger", f"{t.title} is past due ({fmt_dt(t.due)})", t.id))
    tasks = [t for t in tasks if t.due > now]

    amounts = {t.id: t.remaining for t in tasks}
    blocks, short_soft, short_hard, _ = _schedule_tasks(tasks, now, list(free), amounts)

    if short_hard:
        # Pass 2: compress everything that allows it, then try again from scratch.
        compressed = {t.id: t.min_total for t in tasks if t.min_total is not None and t.min_total < t.remaining}
        if compressed:
            amounts2 = {t.id: compressed.get(t.id, t.remaining) for t in tasks}
            blocks2, soft2, hard2, _ = _schedule_tasks(tasks, now, list(free), amounts2)
            if sum(hard2.values()) < sum(short_hard.values()):
                blocks, short_soft, short_hard = blocks2, soft2, hard2
                result.compressed = {tid: amounts[tid] - m for tid, m in compressed.items()}
                for tid, cut in result.compressed.items():
                    title = next(t.title for t in tasks if t.id == tid)
                    result.warnings.append(Warning("warn", f"Squeezed {title} by {cut} min to make room", tid))

    by_id = {t.id: t for t in tasks}
    for tid, left in short_soft.items():
        t = by_id[tid]
        if tid not in short_hard and t.effective_due < t.due:
            result.warnings.append(Warning("warn", f"{t.title} misses its target ({fmt_time(t.effective_due)}) but makes the real deadline", tid))
    # Work due after the planning window just isn't placed yet; that's not a shortfall.
    short_hard = {tid: left for tid, left in short_hard.items() if by_id[tid].due <= horizon_end}
    for tid, left in short_hard.items():
        t = by_id[tid]
        result.warnings.append(Warning("danger", f"{t.title} is {left} min short of fitting before {fmt_dt(t.due)}", tid))
    result.shortfall = short_hard
    result.blocks += blocks
    result.blocks.sort(key=lambda b: b.start)
    return result


# ---- Deadline rules ---------------------------------------------------------

def effective_due(due: datetime, tags: list[str], rules: list[dict]) -> datetime:
    """Apply tag rules. A rule {"tag": "db", "weekday": 2, "time": time(23, 59)} means
    "work tagged #db that's due later in the week must be done by that week's
    Wednesday 11:59 PM". Work due before the rule's day keeps its real deadline."""
    best = due
    for r in rules:
        if r["tag"] not in tags:
            continue
        week_start = due.date() - timedelta(days=due.weekday())
        cand = datetime.combine(week_start + timedelta(days=r["weekday"]), r["time"])
        if cand < due:
            best = min(best, cand)
    return best
