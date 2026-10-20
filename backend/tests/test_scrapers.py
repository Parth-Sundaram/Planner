from datetime import date, datetime, time
from pathlib import Path

import pytest
from sqlalchemy import select

from planner.models import Task, make_session_factory
from planner.scrapers import parse_csse304, parse_csse313, sync_items

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 18)


def by_title(items):
    return {i.title: i for i in items}


def test_csse304_assignments_and_exam():
    items = by_title(parse_csse304((FIX / "csse304.html").read_text(), TODAY))
    assert items["CPSConversion"].due == datetime(2026, 10, 20, 23, 59)
    assert items["Interpreter-DefineAndSet"].due == datetime(2026, 10, 25, 23, 59)
    exam = items["Study: Exam 2 in E104"]
    assert exam.due == datetime(2026, 10, 27, 10, 0) and exam.kind == "exam"


def test_csse313_days_map_to_meeting_days():
    items = by_title(parse_csse313((FIX / "csse313.html").read_text(), TODAY, time(10), "MTRF"))
    # Explicit weekday prefix wins: "Wed. 23:59"
    assert items["CNN - Part 3/4"].due == datetime(2026, 10, 21, 23, 59)
    # Day 27 is the 4th meeting of the week (Fri)
    assert items["CNN - Part 4/4"].due == datetime(2026, 10, 23, 23, 59)
    # Day 26 is Thursday -> quiz study task due at class time
    assert items["Study: Quiz 2"].due == datetime(2026, 10, 22, 10, 0)
    # BC = beginning of class on that day (Day 28 = Mon Oct 26)
    assert items["Attention summary"].due == datetime(2026, 10, 26, 10, 0)
    assert items["Tiny LLM"].due == datetime(2026, 11, 5, 23, 59)
    assert items["Evaluating AI systems"].due == datetime(2026, 11, 3, 10, 0)


def test_sync_keeps_user_estimates_and_skips_past():
    s = make_session_factory("sqlite://")()
    items = parse_csse304((FIX / "csse304.html").read_text(), TODAY)
    now = datetime(2026, 10, 18, 12)
    r1 = sync_items(s, items, now)
    assert r1["created"] == 4  # SyntaxExpansion (Oct 16) is in the past
    t = s.scalar(select(Task).where(Task.title == "CPSConversion"))
    t.est_minutes = 240; s.commit()
    r2 = sync_items(s, items, now)
    assert r2 == {"created": 0, "updated": 0}
    assert s.scalar(select(Task).where(Task.title == "CPSConversion")).est_minutes == 240
