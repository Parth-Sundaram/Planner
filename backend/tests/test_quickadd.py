from datetime import datetime, timedelta

import pytest

from planner import quickadd as Q
from planner.app import create_app

NOW = datetime(2026, 10, 20, 19, 0)  # Tuesday evening


@pytest.mark.parametrize("text,due,repeat,title", [
    ("submit timesheet by Sunday", datetime(2026, 10, 25, 23, 59), None, "Submit timesheet"),
    ("submit timesheet every Sunday", datetime(2026, 10, 25, 23, 59), "weekly:6", "Submit timesheet"),
    ("pay rent monthly on Nov 1", datetime(2026, 11, 1, 23, 59), "monthly", "Pay rent"),
    ("email Wollowski tomorrow at 9am", datetime(2026, 10, 21, 9, 0), None, "Email Wollowski"),
    ("PLC assignment tonight", datetime(2026, 10, 20, 23, 59), None, "PLC assignment"),
    ("Stripe report Fri 5pm", datetime(2026, 10, 23, 17, 0), None, "Stripe report"),
    ("double major application by next Monday noon", datetime(2026, 10, 26, 12, 0), None, "Double major application"),
    ("CNN Part 4/4", None, None, "CNN Part 4/4"),
    ("read chapter 5 in 3 days", datetime(2026, 10, 23, 23, 59), None, "Read chapter 5"),
    ("log calories daily", datetime(2026, 10, 20, 23, 59), "daily", "Log calories"),
    ("market research by oct 30", datetime(2026, 10, 30, 23, 59), None, "Market research"),
    ("DB quiz every weekday at 4pm", datetime(2026, 10, 20, 16, 0), "weekdays", "DB quiz"),
])
def test_parse_when(text, due, repeat, title):
    d, r, t = Q.parse_when(text, NOW)
    assert (d, r, t) == (due, repeat, title)


def test_defaults_history_and_area():
    assert Q.default_minutes("Submit timesheet") == 15
    assert Q.default_minutes("Prac Sec homework") == 120
    hist = [("CSSE 304 interpreter milestone 2", 45), ("Interpreter milestone 3", 55), ("Groceries", 30)]
    assert Q.estimate_from_history("Interpreter milestone 4", hist) == 50

    class A:  # minimal stand-ins for Course rows
        def __init__(self, name, code, kind, keywords=""):
            self.name, self.code, self.kind, self.keywords = name, code, kind, keywords
    areas = [A("PLC", "csse304", "course"), A("Prac Sec", "prac-sec", "course", "kali, nmap"),
             A("Career", "career", "career"), A("Finance", "finance", "finance")]
    assert Q.guess_area("Submit timesheet", areas).name == "Career"
    assert Q.guess_area("PLC milestone", areas).name == "PLC"
    assert Q.guess_area("nmap lab writeup", areas).name == "Prac Sec"
    assert Q.guess_area("Pay rent", areas).name == "Finance"
    assert Q.guess_area("Call mom", areas) is None


def test_next_due():
    sun = datetime(2026, 10, 25, 23, 59)
    assert Q.next_due(sun, "weekly:6") == datetime(2026, 11, 1, 23, 59)
    assert Q.next_due(datetime(2026, 10, 23, 16), "weekdays") == datetime(2026, 10, 26, 16)
    assert Q.next_due(datetime(2026, 1, 31, 9), "monthly") == datetime(2026, 2, 28, 9)


def test_timesheet_end_to_end(monkeypatch):
    """'submit timesheet by Sunday' comes out complete: due, time, area, scheduled."""
    from planner import service
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    c = create_app("sqlite://", testing=True).test_client()
    c.post("/auth/dev-login")
    r = c.post("/api/tasks", json={"text": "submit timesheet every Sunday"}).get_json()
    t = r["task"]
    career = next(x for x in c.get("/api/courses").get_json()["courses"] if x["name"] == "Career")
    assert (t["title"], t["due"], t["est_minutes"], t["course_id"], t["repeat_label"], t["needs_estimate"]) == \
        ("Submit timesheet", "2026-10-25T23:59", 15, career["id"], "every Sunday", False)
    assert r["why"]["area"] == "Career"
    blocks = [b for b in c.get("/api/blocks?start=2026-10-20T00:00&end=2026-10-27T00:00").get_json()["blocks"] if b["task_id"] == t["id"]]
    assert blocks and all(datetime.fromisoformat(b["end"]) <= datetime(2026, 10, 25, 23, 59) for b in blocks)
    # Finishing it makes next week's
    c.post(f"/api/tasks/{t['id']}/finish")
    nxt = [x for x in c.get("/api/tasks").get_json() if x["title"] == "Submit timesheet"]
    assert len(nxt) == 1 and nxt[0]["due"] == "2026-11-01T23:59" and nxt[0]["repeat"] == "weekly:6"
    # Finishing twice doesn't make duplicates
    c.post(f"/api/tasks/{t['id']}/finish")
    assert len([x for x in c.get("/api/tasks").get_json() if x["title"] == "Submit timesheet"]) == 1
    # Explicit metadata and fields still win
    r = c.post("/api/tasks", json={"text": "timesheet ~30m by Sunday", "due": "2026-10-24T12:00"}).get_json()
    assert (r["task"]["est_minutes"], r["task"]["due"]) == (30, "2026-10-24T12:00")
