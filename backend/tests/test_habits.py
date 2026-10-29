from datetime import datetime

import pytest

from planner import service
from planner.app import create_app

NOW = datetime(2026, 10, 29, 21, 0)  # Thursday night


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    cl = create_app("sqlite://", testing=True).test_client()
    cl.post("/auth/dev-login")
    return cl


def by_name(r):
    return {h["name"]: h for h in r["habits"]}


def test_suggested_manual_counters_and_streaks(c):
    r = c.post("/api/habits/suggested").get_json()
    hs = by_name(r)
    assert "Go to every class" in hs and len(hs) == 8
    assert len(c.post("/api/habits/suggested").get_json()["habits"]) == 8   # no duplicates
    water = hs["Drink water (bottles)"]
    for _ in range(2):
        r = c.post(f"/api/habits/{water['id']}/tap").get_json()
    assert by_name(r)["Drink water (bottles)"]["today"]["done"] is False   # 2 of 3
    r = c.post(f"/api/habits/{water['id']}/tap").get_json()
    w = by_name(r)["Drink water (bottles)"]
    assert w["today"]["done"] and w["today"]["value"] == 3
    # yesterday and the day before too -> 3-day streak
    for day in ("2026-10-28", "2026-10-27"):
        c.post(f"/api/habits/{water['id']}/tap", json={"day": day, "value": 3})
    assert by_name(c.get("/api/habits").get_json())["Drink water (bottles)"]["streak"] == 3
    # yes/no habit can't go above 1; untick works
    clean = hs["Eat clean: no delivery, no bought drinks"]
    c.post(f"/api/habits/{clean['id']}/tap"); c.post(f"/api/habits/{clean['id']}/tap")
    r = c.post(f"/api/habits/{clean['id']}/tap", json={"delta": -1}).get_json()
    assert by_name(r)["Eat clean: no delivery, no bought drinks"]["today"]["done"] is False
    # school-night habit doesn't apply on Friday/Saturday
    games = by_name(r)["Games off by 11:30 on school nights"]
    assert [g["applies"] for g in games["grid"] if g["date"] in ("2026-10-23", "2026-10-24", "2026-10-25")] == [False, False, True]


def test_automatic_habits_from_data(c):
    hs = by_name(c.post("/api/habits/suggested").get_json())
    courses = c.get("/api/courses").get_json()["courses"]
    fit = next(x for x in courses if x["kind"] == "fitness")
    plc = next(x for x in courses if x["name"] == "PLC")
    c.post(f"/api/courses/{plc['id']}/attendance", json={"status": "present", "once": True})
    c.post("/api/health/ingest", json={"date": "2026-10-29", "sleep_minutes": 430, "steps": 9000})
    c.post(f"/api/areas/{fit['id']}/entries", json={"type": "workout", "date": "2026-10-29T15:00", "data": {"kind": "Push A", "minutes": 60}})
    for t in ("08:00", "12:30", "19:00"):
        c.post(f"/api/areas/{fit['id']}/entries", json={"type": "meal", "date": f"2026-10-29T{t}", "data": {"calories": 700}})
    hs = by_name(c.get("/api/habits").get_json())
    assert hs["Go to every class"]["today"]["done"] and hs["Go to every class"]["today"]["auto"]
    assert hs["Sleep 7 hours"]["today"]["done"] and hs["Sleep 7 hours"]["today"]["value"] == 430
    assert hs["Log every meal"]["today"]["done"]
    gym = hs["Gym"]
    assert gym["today"]["done"] and gym["this_week"] == 1 and gym["per_week"] == 3
    # an absence breaks attendance even with a present in another class
    ai = next(x for x in courses if x["name"] == "AI")
    c.post(f"/api/courses/{ai['id']}/attendance", json={"status": "absent", "once": True})
    assert by_name(c.get("/api/habits").get_json())["Go to every class"]["today"]["done"] is False


def test_custom_habit_validation(c):
    assert c.post("/api/habits", json={"name": "x", "source": "vibes"}).status_code == 400
    r = c.post("/api/habits", json={"name": "Read 20 pages", "days": "MTWRF", "target": ""})
    h = by_name(r.get_json())["Read 20 pages"]
    assert (h["days"], h["target"], h["source"]) == ("MTWRF", None, None)
    r = c.patch(f"/api/habits/{h['id']}", json={"per_week": 4}).get_json()
    assert by_name(r)["Read 20 pages"]["per_week"] == 4
    assert c.delete(f"/api/habits/{h['id']}").get_json()["habits"] == []
