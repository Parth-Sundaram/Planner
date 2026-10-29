"""Habits: tap to tick, or let the planner tick them from data it already has.

Automatic sources
  attendance      every class that day marked present/excused (none absent)
  workout         a workout logged (or from the watch), or the Gym routine block checked done
  sleep           slept at least `target` minutes (Health Connect or logged)   [day you woke up]
  steps           at least `target` steps
  meals_logged    at least `target` meals logged
  calories_under  logged calories that day at or under `target`
  routine:<id>    that routine's block checked done that day
A tick you make yourself always counts, even for an automatic habit.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AreaEntry, AttendanceLog, Block, Course, Habit, HabitLog, Routine

DAY = "MTWRFSU"
SOURCES = {
    None: "you tick it",
    "attendance": "from class attendance",
    "workout": "from workouts / gym",
    "sleep": "from sleep data",
    "steps": "from step data",
    "meals_logged": "from meals logged",
    "calories_under": "from calories logged",
}

SUGGESTED = [
    {"name": "Go to every class", "source": "attendance", "days": "MTWRF"},
    {"name": "Drink water (bottles)", "source": None, "target": 3},
    {"name": "Eat clean: no delivery, no bought drinks", "source": None},
    {"name": "Sleep 7 hours", "source": "sleep", "target": 420},
    {"name": "Gym", "source": "workout", "per_week": 3},
    {"name": "Log every meal", "source": "meals_logged", "target": 3},
    {"name": "Games off by 11:30 on school nights", "source": None, "days": "UMTWR"},
    {"name": "10-minute room reset + dishes", "source": None},
]


def _midnight(d: date) -> datetime:
    return datetime.combine(d, time())


class _Data:
    """Everything the automatic sources need for a date range, loaded once."""

    def __init__(self, db: Session, start: date, end: date):
        lo, hi = _midnight(start), _midnight(end + timedelta(days=1))
        self.att = defaultdict(list)
        for l in db.scalars(select(AttendanceLog).where(AttendanceLog.day >= lo, AttendanceLog.day < hi)):
            self.att[l.day.date()].append(l.status)
        self.metric, self.meals, self.cal, self.workout = {}, defaultdict(int), defaultdict(float), set()
        fit_ids = [c.id for c in db.scalars(select(Course).where(Course.kind == "fitness"))]
        if fit_ids:
            for e in db.scalars(select(AreaEntry).where(AreaEntry.area_id.in_(fit_ids), AreaEntry.date >= lo, AreaEntry.date < hi)):
                d = e.date.date()
                data = json.loads(e.data or "{}")
                if e.type == "metric":
                    self.metric[d] = data
                elif e.type == "meal":
                    self.meals[d] += 1
                    self.cal[d] += data.get("calories", 0)
                elif e.type == "workout":
                    self.workout.add(d)
        gym_ids = {r.id for r in db.scalars(select(Routine).where(Routine.kind == "gym"))}
        self.routine_done = defaultdict(set)
        for b in db.scalars(select(Block).where(Block.kind == "routine", Block.status == "done",
                                                Block.start >= lo, Block.start < hi)):
            self.routine_done[b.start.date()].add(b.routine_id)
            if b.routine_id in gym_ids:
                self.workout.add(b.start.date())

    def value(self, h: Habit, d: date) -> tuple[float | None, bool | None]:
        """(value to show, done) from data; done None = no data / doesn't apply that day."""
        s = h.source
        if s == "attendance":
            marks = self.att.get(d)
            if not marks:
                return None, None
            return float(len(marks)), "absent" not in marks
        if s == "workout":
            return None, d in self.workout
        if s in ("sleep", "steps"):
            v = self.metric.get(d, {}).get("sleep_minutes" if s == "sleep" else "steps")
            if v is None:
                return None, None
            return float(v), v >= (h.target or (420 if s == "sleep" else 8000))
        if s == "meals_logged":
            n = self.meals.get(d, 0)
            return float(n), n >= (h.target or 3)
        if s == "calories_under":
            c = self.cal.get(d, 0)
            return (float(c), c <= (h.target or 2500)) if c else (None, None)
        if s and s.startswith("routine:"):
            return None, int(s.split(":")[1]) in self.routine_done.get(d, set())
        return None, None


def summary(db: Session, today: date, days: int = 28) -> list[dict]:
    start = today - timedelta(days=days - 1)
    data = _Data(db, start - timedelta(days=7 * 8), today)  # extra weeks for weekly streaks
    habits = list(db.scalars(select(Habit).where(Habit.active.is_(True)).order_by(Habit.position, Habit.id)))
    logs = defaultdict(dict)
    for l in db.scalars(select(HabitLog).where(HabitLog.day >= _midnight(start - timedelta(days=7 * 8)))):
        logs[l.habit_id][l.day.date()] = l
    out = []
    for h in habits:
        def state(d: date) -> dict:
            applies = DAY[d.weekday()] in (h.days or DAY)
            log = logs[h.id].get(d)
            auto_v, auto_done = data.value(h, d) if h.source else (None, None)
            if h.source:
                done = bool((log and log.value > 0) or auto_done)
                value = auto_v
            else:
                value = log.value if log else 0.0
                done = value >= (h.target or 1)
            return {"date": d.isoformat(), "applies": applies, "done": done, "value": value,
                    "auto": bool(h.source and auto_done and not (log and log.value > 0))}
        grid = [state(start + timedelta(days=i)) for i in range(days)]
        t = grid[-1]
        if h.per_week:
            def week_count(monday: date) -> int:
                return sum(state(monday + timedelta(days=i))["done"] for i in range(7) if monday + timedelta(days=i) <= today)
            this_monday = today - timedelta(days=today.weekday())
            this_week = week_count(this_monday)
            streak, wk = (1 if this_week >= h.per_week else 0), this_monday - timedelta(days=7)
            while week_count(wk) >= h.per_week and streak < 52:
                streak, wk = streak + 1, wk - timedelta(days=7)
            streak_label = f"{streak} week{'s' if streak != 1 else ''}"
        else:
            this_week = None
            streak, d = 0, today if t["done"] else today - timedelta(days=1)
            for _ in range(365):
                st = state(d)
                if not st["applies"]:
                    d -= timedelta(days=1)
                    continue
                if not st["done"]:
                    break
                streak, d = streak + 1, d - timedelta(days=1)
            streak_label = f"{streak} day{'s' if streak != 1 else ''}"
        applicable = [g for g in grid if g["applies"]]
        out.append({"id": h.id, "name": h.name, "source": h.source, "source_label": SOURCES.get(h.source) if not (h.source or "").startswith("routine:") else "from a routine",
                    "target": h.target, "days": h.days, "per_week": h.per_week, "today": t, "grid": grid,
                    "streak": streak, "streak_label": streak_label, "this_week": this_week,
                    "rate": round(100 * sum(g["done"] for g in applicable) / len(applicable)) if applicable else None})
    return out


def tap(db: Session, habit: Habit, day: date, delta: float | None = None, value: float | None = None) -> None:
    log = db.scalar(select(HabitLog).where(HabitLog.habit_id == habit.id, HabitLog.day == _midnight(day)))
    if log is None:
        log = HabitLog(habit_id=habit.id, day=_midnight(day), value=0, manual=True)
        db.add(log)
    log.value = max(0.0, value if value is not None else (log.value or 0) + (delta if delta is not None else 1))
    if not habit.source and habit.target is None and log.value > 1:
        log.value = 1.0  # a simple yes/no habit
    db.commit()
