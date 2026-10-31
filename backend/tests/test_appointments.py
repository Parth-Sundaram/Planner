from datetime import date, datetime, time, timedelta

import pytest

from planner import quickadd as Q
from planner import scheduler as S
from planner import service
from planner.app import create_app

TUE = datetime(2026, 10, 27, 8, 0)


def advisor():
    return S.RoutineSpec(1, "Capstone advisor update", "T", time(13), time(13, 45), 45, "class")


def test_fixed_routine_on_calendar_is_not_duplicated_or_warned():
    cal = S.Interval(datetime(2026, 10, 27, 13), datetime(2026, 10, 27, 13, 45), "Capstone check-in w/ Chenoweth")
    r = S.plan(TUE, TUE + timedelta(days=1), time(8), time(23), [cal], [advisor()], [])
    assert not [b for b in r.blocks if b.routine_id == 1]
    assert not r.warnings


def test_fixed_routine_kept_with_conflict_warning_when_something_else_is_there():
    pinned = S.Interval(datetime(2026, 10, 27, 13, 15), datetime(2026, 10, 27, 14), "CNN Part 3")
    r = S.plan(TUE, TUE + timedelta(days=1), time(8), time(23), [pinned], [advisor()], [])
    blk = [b for b in r.blocks if b.routine_id == 1]
    assert blk and blk[0].start == datetime(2026, 10, 27, 13)
    assert any("overlaps CNN Part 3" in w.message for w in r.warnings)
    assert not any("No room" in w.message for w in r.warnings)


def test_fixed_routines_go_first_so_flexible_ones_move_around_them():
    lunch = S.RoutineSpec(2, "Lunch", "T", time(12), time(14), 45, "meal")
    r = S.plan(TUE, TUE + timedelta(days=1), time(8), time(23), [], [lunch, advisor()], [])
    by = {b.routine_id: b for b in r.blocks}
    assert by[1].start == datetime(2026, 10, 27, 13)
    assert by[2].end <= by[1].start or by[2].start >= by[1].end
    assert not r.warnings


def test_parse_range_and_meeting_words():
    assert Q.parse_range("Dentist Thu 2-3pm")[:2] == (time(14), time(15))
    assert Q.parse_range("lab 11-1pm")[:2] == (time(11), time(13))
    assert Q.parse_range("call 1:30 to 2:15pm")[:2] == (time(13, 30), time(14, 15))
    assert Q.parse_range("CNN Part 4/4")[0] is None
    assert Q.is_meeting("Talk to AI professor about grades") and Q.is_meeting("Office hours with Wollowski")
    assert not Q.is_meeting("CNN Part 4")


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(service, "local_now", lambda db: datetime(2026, 10, 28, 20, 0))  # Wed night
    cl = create_app("sqlite://", testing=True).test_client()
    cl.post("/auth/dev-login")
    return cl


def test_quick_add_appointment_vs_meeting_task(c):
    r = c.post("/api/tasks", json={"text": "Dentist Thu 2-3pm"}).get_json()
    a = r["appointment"]
    assert (a["title"], a["start"], a["end"], a["category"]) == ("Dentist", "2026-10-29T14:00", "2026-10-29T15:00", "appointment")
    r = c.post("/api/tasks", json={"text": "Meet with Rishi tomorrow at 4pm for 30m"}).get_json()
    assert r["appointment"]["start"] == "2026-10-29T16:00" and r["appointment"]["end"] == "2026-10-29T16:30"
    # no fixed time -> a meeting task the planner fits in, in one piece
    t = c.post("/api/tasks", json={"text": "Talk to AI professor about grades by Thu 3:45pm"}).get_json()["task"]
    assert (t["kind"], t["est_minutes"], t["splittable"]) == ("meeting", 15, False)
    blocks = c.get("/api/blocks?start=2026-10-28T00:00&end=2026-10-30T00:00").get_json()["blocks"]
    mine = [b for b in blocks if b["task_id"] == t["id"]]
    assert len(mine) == 1 and mine[0]["category"] == "meeting"
    # work never lands on the appointment
    dentist = [b for b in blocks if b["title"] == "Dentist"][0]
    assert not any(b["kind"] == "task" and b["start"] < dentist["end"] and b["end"] > dentist["start"] for b in blocks)
    # appointments can be removed
    assert c.post(f"/api/blocks/{dentist['id']}/remove").status_code == 200


def test_class_routines_are_categorised(c):
    c.post("/api/routines", json={"title": "Prac Sec class", "days": "R", "window_start": "16:00", "window_end": "16:50",
                                  "duration": 50, "kind": "class"})
    blocks = c.get("/api/blocks?start=2026-10-29T00:00&end=2026-10-30T00:00").get_json()["blocks"]
    assert [b["category"] for b in blocks if b["title"] == "Prac Sec class"] == ["class"]