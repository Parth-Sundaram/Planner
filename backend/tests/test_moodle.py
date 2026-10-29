from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from planner import moodle, service
from planner.app import create_app
from planner.models import Course, GradeCategory, GradeItem, Task, make_session_factory

NOW = datetime(2026, 10, 29, 9, 0)
TZ = ZoneInfo("America/New_York")
ICAL = """BEGIN:VCALENDAR\r
METHOD:PUBLISH\r
PRODID:-//Moodle Pty Ltd//NONSGML Moodle Version 2024100700//EN\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:1201@moodle.rose-hulman.edu\r
SUMMARY:Lab 5: Format strings is due\r
DESCRIPTION:Submit your exploit and a short\\, clear write-up.\r
CLASS:PUBLIC\r
LAST-MODIFIED:20261020T150000Z\r
DTSTAMP:20261028T120000Z\r
DTSTART:20261031T035900Z\r
DTEND:20261031T035900Z\r
CATEGORIES:CSSE140-01 Practical Security\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:1202@moodle.rose-hulman.edu\r
SUMMARY:Lab 6 opens\r
DTSTART:20261031T120000Z\r
CATEGORIES:CSSE140-01 Practical Security\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:1300@moodle.rose-hulman.edu\r
SUMMARY:Daily Quiz 18 closes\r
DTSTART:20261030T035900Z\r
CATEGORIES:CSSE433-01 Advanced Databases\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:1400@moodle.rose-hulman.edu\r
SUMMARY:Very long assignment name that wraps onto a second line in the export bec\r
 ause Moodle folds at 75 octets is due\r
DTSTART:20261102T045900Z\r
CATEGORIES:CSSE304-01 Programming Language Concepts\r
END:VEVENT\r
END:VCALENDAR\r
"""


@pytest.fixture
def db(monkeypatch):
    s = make_session_factory("sqlite://")()
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    from planner.scrapers import get_courses
    get_courses(s)  # PLC (csse304), AI (csse313), areas
    s.add_all([Course(code="prac-sec", name="Prac Sec", url="", parser="none"),
               Course(code="db", name="DB", url="", parser="none")])
    s.commit()
    yield s
    s.close()


def test_ical_parsing_and_titles():
    items = moodle.items_from_ical(ICAL, TZ)
    titles = [i["title"] for i in items]
    assert titles == ["Lab 5: Format strings", "Daily Quiz 18",
                      "Very long assignment name that wraps onto a second line in the export because Moodle folds at 75 octets"]
    lab = items[0]
    assert lab["due"] == datetime(2026, 10, 30, 23, 59) and lab["moodle_course"] == "CSSE140-01 Practical Security"
    assert items[1]["kind"] == "quiz"


def test_calendar_link_sync_with_mapping(db, monkeypatch):
    class R:
        text, status_code = ICAL, 200
        def raise_for_status(self): pass
    monkeypatch.setattr(moodle.requests, "get", lambda *a, **k: R())
    moodle._set(db, "moodle_ical", "https://moodle.example.edu/calendar/export_execute.php?userid=1&authtoken=x")
    r = moodle.sync(db, NOW)
    assert r["via"] == "calendar link" and r["found"] == 3
    # PLC is guessed from its code; the other two aren't mapped yet -> suggestions
    plc = db.scalar(select(Task).where(Task.source == "moodle", Task.title.like("Very long%")))
    assert plc.status == "open" and plc.course == "PLC"
    lab = db.scalar(select(Task).where(Task.title == "Lab 5: Format strings"))
    assert lab.status == "suggested" and lab.course_id is None
    seen = [s["name"] for s in moodle.config(db)["seen"]]
    assert "CSSE433-01 Advanced Databases" in seen
    # map them and sync again: the suggestion becomes a real task in the right course
    prac = db.scalar(select(Course).where(Course.name == "Prac Sec"))
    dbc = db.scalar(select(Course).where(Course.name == "DB"))
    moodle._set(db, "moodle_map", f'{{"CSSE140-01 Practical Security": {prac.id}, "CSSE433-01 Advanced Databases": {dbc.id}}}')
    db.commit()
    moodle.sync(db, NOW)
    db.refresh(lab)
    assert (lab.status, lab.course_id) == ("open", prac.id)
    assert len(db.scalars(select(Task).where(Task.source == "moodle")).all()) == 3  # no duplicates


def fake_ws(calls):
    ts = lambda y, m, d, h, mi: int(datetime(y, m, d, h, mi, tzinfo=TZ).timestamp())
    def ws(base, token, fn, **p):
        calls.append(fn)
        if fn == "core_webservice_get_site_info":
            return {"userid": 7, "sitename": "Rose Moodle"}
        if fn == "core_enrol_get_users_courses":
            return [{"id": 11, "shortname": "CSSE140-01", "fullname": "Practical Security", "enddate": 0},
                    {"id": 12, "shortname": "CSSE433-01", "fullname": "Advanced Databases", "enddate": 0}]
        if fn == "mod_assign_get_assignments":
            return {"courses": [{"id": 11, "assignments": [
                {"id": 501, "name": "Lab 5", "duedate": ts(2026, 10, 30, 23, 59)},
                {"id": 502, "name": "Lab 4", "duedate": ts(2026, 10, 29, 16, 0)},   # already submitted
                {"id": 503, "name": "Reflection", "duedate": 0}]}]}
        if fn == "mod_quiz_get_quizzes_by_courses":
            return {"quizzes": [{"id": 900, "course": 12, "name": "Daily Quiz 18", "timeclose": ts(2026, 10, 29, 23, 59)}]}
        if fn == "core_calendar_get_action_events_by_timesort":
            return {"events": [{"name": "Lab 5 is due", "modulename": "assign", "instance": 501, "timesort": ts(2026, 10, 30, 23, 59),
                                "course": {"id": 11}},
                               {"name": "Daily Quiz 18 closes", "modulename": "quiz", "instance": 900, "timesort": ts(2026, 10, 29, 23, 59),
                                "course": {"id": 12}},
                               {"name": "Peer review is due", "activityname": "Peer review", "modulename": "workshop", "instance": 77,
                                "timesort": ts(2026, 11, 1, 12, 0), "course": {"id": 11}}]}
        if fn == "gradereport_user_get_grade_items":
            if p["courseid"] == 11:
                return {"usergrades": [{"gradeitems": [
                    {"itemname": "Lab 1: Setup", "itemtype": "mod", "graderaw": 9.5, "grademax": 10},
                    {"itemname": "Lab 2", "itemtype": "mod", "graderaw": None, "grademax": 10},
                    {"itemname": None, "itemtype": "course", "graderaw": 95.0, "grademax": 100, "percentageformatted": "95.00 %"}]}]}
            raise moodle.MoodleError("gradereport_user_get_grade_items: not allowed")
        raise AssertionError(fn)
    return ws


def test_web_service_sync(db, monkeypatch):
    calls = []
    monkeypatch.setattr(moodle, "ws", fake_ws(calls))
    prac = db.scalar(select(Course).where(Course.name == "Prac Sec"))
    dbc = db.scalar(select(Course).where(Course.name == "DB"))
    cat = GradeCategory(course_id=prac.id, name="Labs", weight=50)
    db.add(cat); db.flush()
    db.add_all([GradeItem(category_id=cat.id, name="Lab 1", out_of=100), GradeItem(category_id=cat.id, name="Lab 2", out_of=100)])
    # an earlier sync already created Lab 4; Moodle now says it's done
    db.add(Task(title="Lab 4", due=datetime(2026, 10, 29, 16, 0), est_minutes=90, external_id="moodle:assign:502",
                source="moodle", course_id=prac.id, course="Prac Sec"))
    moodle._set(db, "moodle_url", "https://moodle.example.edu")
    moodle._set(db, "moodle_token", "abc")
    moodle._set(db, "moodle_map", f'{{"CSSE140-01": {prac.id}, "CSSE433-01": {dbc.id}}}')
    db.commit()
    r = moodle.sync(db, NOW)
    assert r["via"] == "web service" and r["completed"] == 1 and r["grades_filled"] == 1, r
    titles = {t.title: t for t in db.scalars(select(Task).where(Task.source == "moodle"))}
    assert set(titles) == {"Lab 5", "Lab 4", "Daily Quiz 18", "Peer review"}
    assert titles["Lab 4"].status == "done" and titles["Daily Quiz 18"].course_id == dbc.id
    assert titles["Daily Quiz 18"].due == datetime(2026, 10, 29, 23, 59) and not titles["Daily Quiz 18"].splittable
    lab1 = db.scalar(select(GradeItem).where(GradeItem.name == "Lab 1"))
    assert (lab1.score, lab1.out_of) == (9.5, 10)


def test_api_settings_mask_token_and_mapping(monkeypatch):
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    def offline(*a, **k):
        raise moodle.MoodleError("offline")
    monkeypatch.setattr(moodle, "ws", offline)
    c = create_app("sqlite://", testing=True).test_client()
    c.post("/auth/dev-login")
    assert c.put("/api/moodle", json={"base_url": "moodle.rose-hulman.edu"}).status_code == 400
    d = c.put("/api/moodle", json={"base_url": "https://moodle.rose-hulman.edu/", "token": "secret-token-1234",
                                   "ical_url": "webcal://moodle.rose-hulman.edu/calendar/export_execute.php?x=1"}).get_json()
    assert d["has_token"] and d["token_hint"] == "…1234" and "secret" not in str(d)
    assert d["ical_url"].startswith("https://")
    r = c.post("/api/moodle/sync").get_json()
    assert "offline" in r["error"] and "offline" in c.get("/api/moodle").get_json()["last"]["error"]


def test_clean_token_accepts_mobile_launch_value():
    import base64
    tok = "0123456789abcdef0123456789abcdef"
    launch = base64.b64encode(f"3a5c7e:::{tok}:::privtok".encode()).decode()
    assert moodle.clean_token(f"moodlemobile://token={launch}") == tok
    assert moodle.clean_token(f" {tok} ") == tok
