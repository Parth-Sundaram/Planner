"""One-time setup of Parth's weekly routine and deadline rules. Safe to re-run (skips existing titles).

    python seed.py
"""
from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402

from planner import service  # noqa: E402
from planner.models import Routine, Rule, make_session_factory  # noqa: E402

ROUTINES = [
    # title, days, window start, window end, minutes, kind
    ("Class", "MTRF", "10:00", "12:00", 120, "class"),
    ("Class", "MTRF", "14:00", "15:00", 60, "class"),
    ("Capstone class", "W", "13:00", "15:00", 120, "class"),
    ("Capstone advisor update", "T", "13:00", "13:45", 45, "meeting"),
    ("Capstone client meeting", "F", "19:00", "19:45", 45, "meeting"),
    ("Breakfast + log calories", "MTWRFSU", "07:30", "10:00", 30, "meal"),
    ("Lunch + log calories", "MWRFSU", "12:00", "13:45", 45, "meal"),
    ("Lunch + log calories", "T", "12:00", "12:45", 45, "meal"),
    ("Dinner + log calories", "MTWRFSU", "18:00", "20:00", 45, "meal"),
    ("Gym", "MTRSU", "15:00", "21:00", 60, "gym"),
    ("Dishes", "MTWRFSU", "19:00", "23:00", 15, "chore"),
    ("Log calorie total", "MTWRFSU", "21:00", "23:00", 5, "chore"),
    ("Laundry + cleaning", "W", "07:45", "13:00", 60, "chore"),
]
RULES = [("db", 2, "23:59", "DB project work done by Wednesday night; team builds Thursday, demo Friday")]

db = make_session_factory()()
existing = {(r.title, r.days) for r in db.scalars(select(Routine))}
for title, days, ws, we, dur, kind in ROUTINES:
    if (title, days) not in existing:
        db.add(Routine(title=title, days=days, window_start=ws, window_end=we, duration=dur, kind=kind))
if not db.scalar(select(Rule).where(Rule.tag == "db")):
    for tag, wd, t, desc in RULES:
        db.add(Rule(tag=tag, weekday=wd, time=t, description=desc))
db.commit()
print(service.replan(db)["warnings"][:5])
print("Seeded routines and rules.")