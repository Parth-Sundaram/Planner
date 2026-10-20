"""Add this week's tasks (week of Oct 19, 2026). Safe to re-run: tasks are matched by key
and existing ones are left alone, so edits you make in the app aren't overwritten.

    python add_week.py            # uses DATABASE_URL from .env (Neon)

Routines (classes, meals, gym, chores) and the #db rule come from seed.py; run that first
if you haven't. Course-site deadlines (PLC, AI) come in through Sync, so they aren't here.
"""
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from planner import service  # noqa: E402
from planner.models import Task, make_session_factory  # noqa: E402

# key, title, course, due, minutes, priority (1 high .. 3 low), tags, steps [(title, minutes)]
TASKS = [
    ("stripe", "Stripe report", "Capstone", datetime(2026, 10, 23, 17, 0), 240, 1, "capstone", [
        ("Draft sections", 150),
        ("Revise with team feedback", 60),
        ("Final pass + submit", 30),
    ]),
    ("prac-hw", "Practical Security homework", "Prac Sec", datetime(2026, 10, 22, 23, 59), 120, 1, "prac", []),
    ("prac-lab", "Practical Security lab", "Prac Sec", datetime(2026, 10, 22, 23, 59), 120, 1, "prac", []),
    ("prac-disc", "Practical Security attendance discussion", "Prac Sec", datetime(2026, 10, 22, 23, 59), 30, 2, "prac", []),
    ("vapor-week", "Vapor: this week's project work", "DB", datetime(2026, 10, 23, 12, 0), 180, 1, "db", []),
    ("double-major", "Double major application", None, datetime(2026, 10, 26, 17, 0), 90, 1, "admin", []),
    ("job-apps", "Job applications", None, datetime(2026, 10, 25, 23, 0), 120, 3, "career", []),
]


def main() -> None:
    db = make_session_factory()()
    added = 0
    for key, title, course, due, minutes, prio, tags, steps in TASKS:
        ext = f"manual:{key}"
        if db.scalar(select(Task).where(Task.external_id == ext)):
            continue
        parent = Task(title=title, course=course, due=due, est_minutes=minutes, priority=prio,
                      tags=tags, external_id=ext, source="manual", sequential=True)
        db.add(parent)
        db.flush()
        for i, (step, step_min) in enumerate(steps):
            db.add(Task(title=step, course=course, due=due, est_minutes=step_min, priority=prio,
                        tags=tags, parent_id=parent.id, position=i,
                        external_id=f"{ext}:{i}", source="manual"))
        added += 1
    db.commit()
    summary = service.replan(db)
    print(f"Added {added} task(s).")
    for w in summary["warnings"]:
        print(f"  [{w['level']}] {w['message']}")


if __name__ == "__main__":
    main()
