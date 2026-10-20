"""Block actions, course management, AI extraction and the suggestion review step."""
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from planner import scrapers, service
from planner.app import create_app
from planner.models import Block, Course, Routine, Task, make_session_factory

NOW = datetime(2026, 10, 19, 8, 0)


@pytest.fixture
def db(monkeypatch):
    s = make_session_factory("sqlite://")()
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    yield s
    s.close()


def blocks(db, **kw):
    q = select(Block).order_by(Block.start)
    for k, v in kw.items():
        q = q.where(getattr(Block, k) == v)
    return db.scalars(q).all()


# ---- Block actions -----------------------------------------------------------------

def test_not_now_pushes_work_after_the_block_and_frees_the_slot(db):
    t = Task(title="Read", due=NOW + timedelta(days=3), est_minutes=60, max_block=60)
    other = Task(title="Other", due=NOW + timedelta(days=3), est_minutes=60, max_block=60, priority=3)
    db.add_all([t, other]); db.commit()
    service.replan(db, NOW)
    first = blocks(db, task_id=t.id)[0]
    slot = (first.start, first.end)
    service.not_now(db, first.id)
    after = blocks(db, task_id=t.id)
    assert after and min(b.start for b in after) >= slot[1]
    assert sum(service.minutes_between(b.start, b.end) for b in after) == 60
    # The freed slot is usable by other work
    assert any(b.start == slot[0] for b in blocks(db, kind="task"))


def test_skip_routine_only_for_that_day_and_restore(db):
    db.add(Routine(title="Gym", days="MTWRFSU", window_start="15:00", window_end="21:00", duration=60, kind="gym"))
    db.commit()
    service.replan(db, NOW)
    today = [b for b in blocks(db, kind="routine") if b.start.date() == NOW.date()][0]
    service.skip_routine(db, today.id)
    todays = [b for b in blocks(db, kind="routine") if b.start.date() == NOW.date()]
    assert [b.status for b in todays] == ["skipped"]
    tomorrow = [b for b in blocks(db, kind="routine") if b.start.date() == NOW.date() + timedelta(days=1)]
    assert len(tomorrow) == 1 and tomorrow[0].status == "planned"
    service.restore_block(db, todays[0].id)
    todays = [b for b in blocks(db, kind="routine") if b.start.date() == NOW.date()]
    assert [b.status for b in todays] == ["planned"]


def test_skipped_routine_time_is_free_for_work(db):
    db.add(Routine(title="Gym", days="M", window_start="08:00", window_end="09:00", duration=60, kind="gym"))
    t = Task(title="Work", due=NOW + timedelta(hours=3), est_minutes=60, max_block=60)
    db.add(t); db.commit()
    service.replan(db, NOW)
    gym = blocks(db, kind="routine")[0]
    service.skip_routine(db, gym.id)
    assert blocks(db, task_id=t.id)[0].start == NOW


def test_wrong_kind_actions_are_rejected(db):
    db.add(Routine(title="Lunch", days="MTWRFSU", window_start="12:00", window_end="13:00", duration=45, kind="meal"))
    db.commit()
    service.replan(db, NOW)
    r = blocks(db, kind="routine")[0]
    with pytest.raises(ValueError):
        service.not_now(db, r.id)
    with pytest.raises(ValueError):
        service.remove_block(db, r.id)


# ---- AI extraction -----------------------------------------------------------------

PAGE = "<html><body><h1>CSSE 999</h1><table><tr><td>Week 8</td><td>HW 5 due Oct 22</td></tr></table></body></html>"


def fake_gemini(items):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return items
    return ask, calls


def test_parse_ai_normalizes_items(monkeypatch):
    ask, calls = fake_gemini([
        {"title": "HW 5", "kind": "assignment", "due_date": "2026-10-22", "due_time": None},
        {"title": "Midterm", "kind": "exam", "due_date": "2026-10-27"},
        {"title": "Daily quiz", "kind": "quiz", "due_date": "2026-10-20", "due_time": "23:59"},
        {"title": "Daily quiz", "kind": "quiz", "due_date": "2026-10-21", "due_time": "23:59"},
        {"title": "Bad date", "kind": "assignment", "due_date": "soon"},
    ])
    monkeypatch.setattr(scrapers, "ask_gemini", ask)
    items = scrapers.parse_ai(PAGE, date(2026, 10, 19), time(10), "MTRF", code="csse999", course="Security")
    by = {i.external_id: i for i in items}
    assert by["csse999:hw-5"].due == datetime(2026, 10, 22, 23, 59)
    assert by["csse999:study-midterm"].due == datetime(2026, 10, 27, 10, 0)
    assert by["csse999:study-daily-quiz"].due == datetime(2026, 10, 20, 23, 59)
    assert by["csse999:study-daily-quiz:2026-10-21"].due == datetime(2026, 10, 21, 23, 59)
    assert len(items) == 4
    assert "HW 5 due Oct 22" in calls[0] and "2026-10-19" in calls[0]


def test_ai_course_sync_suggests_and_skips_unchanged_pages(db, monkeypatch):
    ask, calls = fake_gemini([{"title": "HW 5", "kind": "assignment", "due_date": "2026-10-22"}])
    monkeypatch.setattr(scrapers, "ask_gemini", ask)
    monkeypatch.setattr(scrapers, "fetch_html", lambda *a, **k: PAGE)
    c = Course(code="sec", name="Security", url="https://example.edu/s", parser="ai", review=True)
    db.add(c); db.commit()
    r1 = scrapers.sync_course(db, c, NOW)
    assert r1["suggested"] == 1 and r1["created"] == 0
    t = db.scalar(select(Task).where(Task.external_id == "sec:hw-5"))
    assert t.status == "suggested" and t.tags == "security"
    service.replan(db, NOW)
    assert not blocks(db, task_id=t.id)  # suggestions aren't scheduled
    r2 = scrapers.sync_course(db, c, NOW)
    assert r2.get("unchanged") and len(calls) == 1  # no second Gemini call


def test_dismissed_items_stay_dismissed(db, monkeypatch):
    ask, _ = fake_gemini([{"title": "HW 5", "kind": "assignment", "due_date": "2026-10-22"}])
    monkeypatch.setattr(scrapers, "ask_gemini", ask)
    monkeypatch.setattr(scrapers, "fetch_html", lambda *a, **k: PAGE)
    c = Course(code="sec", name="Security", url="https://example.edu/s", parser="ai", review=True)
    db.add(c); db.commit()
    scrapers.sync_course(db, c, NOW)
    t = db.scalar(select(Task).where(Task.external_id == "sec:hw-5"))
    t.status = "dismissed"; db.commit()
    scrapers.sync_course(db, c, NOW, force=True)
    db.refresh(t)
    assert t.status == "dismissed"
    assert len(db.scalars(select(Task)).all()) == 1


def test_missing_key_is_reported_not_raised(db, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(scrapers, "fetch_html", lambda *a, **k: PAGE)
    c = Course(code="sec", name="Security", url="https://example.edu/s", parser="ai")
    db.add(c); db.commit()
    r = scrapers.sync_course(db, c, NOW)
    assert "GEMINI_API_KEY" in r["error"]


def test_courses_seeded_from_defaults_once(db):
    codes = [c.code for c in scrapers.get_courses(db)]
    assert codes == ["csse304", "csse313"]
    for c in scrapers.get_courses(db):
        db.delete(c)
    db.commit()
    assert scrapers.get_courses(db) == []  # deleting them sticks


# ---- API -------------------------------------------------------------------------

@pytest.fixture
def client():
    c = create_app("sqlite://", testing=True).test_client()
    assert c.post("/auth/dev-login").status_code == 200
    return c


def test_course_api_and_preview(client, monkeypatch):
    ask, _ = fake_gemini([{"title": "HW 5", "kind": "assignment", "due_date": "2099-10-22"}])
    monkeypatch.setattr(scrapers, "ask_gemini", ask)
    monkeypatch.setattr(scrapers, "fetch_html", lambda *a, **k: PAGE)
    assert len(client.get("/api/courses").get_json()["courses"]) == 2
    r = client.post("/api/courses", json={"name": "Prac Sec", "url": "https://example.edu/sec", "class_days": "MTRF"})
    assert r.status_code == 201
    c = r.get_json()
    assert (c["code"], c["parser"], c["review"]) == ("prac-sec", "ai", True)
    assert client.post("/api/courses", json={"name": "x", "url": "nope"}).status_code == 400
    prev = client.post(f"/api/courses/{c['id']}/test").get_json()
    assert prev["items"][0]["title"] == "HW 5" and prev["items"][0]["status"] == "new"
    assert client.get("/api/tasks?status=all").get_json() == []  # preview saves nothing
    res = client.post(f"/api/courses/{c['id']}/sync").get_json()
    assert res["result"]["suggested"] == 1
    sug = client.get("/api/tasks?status=suggested").get_json()
    assert len(sug) == 1
    assert client.post("/api/suggestions/accept-all").get_json()["accepted"] == 1
    assert len(client.get("/api/tasks").get_json()) == 1
    assert client.patch(f"/api/courses/{c['id']}", json={"enabled": False}).get_json()["enabled"] is False
    assert client.delete(f"/api/courses/{c['id']}").status_code == 200


def test_api_errors_are_json(client, monkeypatch):
    def boom(db, bid):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(service, "not_now", boom)
    soon = (datetime.now() + timedelta(days=2)).replace(second=0, microsecond=0).isoformat()
    t = client.post("/api/tasks", json={"text": "X ~1h", "due": soon}).get_json()["task"]
    b = [b for b in client.get("/api/blocks").get_json()["blocks"] if b["task_id"] == t["id"]][0]
    r = client.post(f"/api/blocks/{b['id']}/not-now")
    assert r.status_code == 500 and "kaboom" in r.get_json()["error"]
    assert client.get("/api/nope").get_json()["error"]


# ---- Confidence-based exam prep -----------------------------------------------------

def test_low_confidence_exam_gets_more_prep_spread_over_days(db):
    exam = Task(title="Study: Exam 2", due=datetime(2026, 10, 27, 10, 0), est_minutes=240)
    db.add(exam); db.commit()
    service.apply_confidence(exam, 2); db.commit()
    assert exam.est_minutes == 420
    service.replan(db, NOW)
    bs = blocks(db, task_id=exam.id)
    assert sum(service.minutes_between(b.start, b.end) for b in bs) == 420
    days = {b.start.date() for b in bs}
    assert len(days) >= 4                                   # spread, not crammed
    assert min(days) >= date(2026, 10, 21)                  # starts 6 days before
    assert all(b.end <= exam.due for b in bs)
    assert all(service.minutes_between(b.start, b.end) <= 90 for b in bs)


def test_high_confidence_is_a_short_review_close_to_the_exam(db):
    exam = Task(title="Study: Quiz 2", due=datetime(2026, 10, 22, 10, 0), est_minutes=120)
    db.add(exam); db.commit()
    service.apply_confidence(exam, 5); db.commit()
    service.replan(db, NOW)
    bs = blocks(db, task_id=exam.id)
    assert sum(service.minutes_between(b.start, b.end) for b in bs) == 60
    assert min(b.start for b in bs).date() == date(2026, 10, 21)


# ---- Course pages and grades -------------------------------------------------------

def test_grade_summary_math():
    from types import SimpleNamespace as N
    from planner.grades import summarize
    hw = N(id=1, name="HW", weight=40, drop_lowest=1, items=[
        N(id=11, name="1", score=90, out_of=100), N(id=12, name="2", score=50, out_of=100),
        N(id=13, name="3", score=100, out_of=100), N(id=14, name="4", score=None, out_of=100)])
    ex = N(id=2, name="Exams", weight=60, drop_lowest=0, items=[N(id=21, name="E1", score=80, out_of=100),
                                                                N(id=22, name="Final", score=None, out_of=100)])
    s = summarize([hw, ex], target=90)
    assert s["categories"][0]["percent"] == 95.0 and s["categories"][0]["dropped"] == [12]
    # HW: 2 kept of 3 counted items -> share .4*2/3 at 95%; exams: .6*.5 at 80%
    assert s["current"] == round(100 * (0.4 * 2 / 3 * 0.95 + 0.3 * 0.8) / (0.4 * 2 / 3 + 0.3), 2)
    assert s["needed"] > 90


def test_course_page_api(client):
    soon = (datetime.now() + timedelta(days=3)).replace(second=0, microsecond=0).isoformat()
    t = client.post("/api/tasks", json={"text": "HW 7 ~2h", "due": soon}).get_json()["task"]
    c = client.post("/api/courses", json={"name": "Prac Sec"}).get_json()
    assert c["parser"] == "none" and c["url"] == ""
    client.patch(f"/api/tasks/{t['id']}", json={"course_id": c["id"]})
    client.patch(f"/api/courses/{c['id']}", json={"links": [{"label": "Repo", "url": "https://github.com/x/y"}, {"url": ""}],
                                                  "target_grade": 93, "notes": "Labs on Kali"})
    gr = client.post(f"/api/courses/{c['id']}/grade-categories", json={"name": "Labs", "weight": 50}).get_json()
    cat = gr["categories"][0]["id"]
    gr = client.post(f"/api/grade-categories/{cat}/items", json={"name": "Lab 1", "score": 9, "out_of": 10}).get_json()
    assert gr["current"] == 90.0 and gr["target"] == 93
    d = client.get(f"/api/courses/{c['id']}").get_json()
    assert d["course"]["links"] == [{"label": "Repo", "url": "https://github.com/x/y"}]
    assert [x["title"] for x in d["tasks"]] == ["HW 7"] and d["tasks"][0]["course_id"] == c["id"]
    assert d["blocks"] and all(b["task_id"] == t["id"] for b in d["blocks"])
    assert client.post(f"/api/courses/{c['id']}/test").status_code == 400
    # confidence through the API
    e = client.post("/api/tasks", json={"text": "Study: Exam 2", "due": soon}).get_json()["task"]
    e = client.patch(f"/api/tasks/{e['id']}", json={"confidence": 1}).get_json()["task"]
    assert e["est_minutes"] == 600 and e["confidence"] == 1 and e["is_study"]
    # deleting the course keeps the task and doesn't resurrect the course
    client.delete(f"/api/courses/{c['id']}")
    assert all(x["name"] != "Prac Sec" for x in client.get("/api/courses").get_json()["courses"])


def test_task_course_names_become_course_pages(db):
    db.add(Task(title="Stripe", course="Capstone", due=NOW + timedelta(days=3), est_minutes=60)); db.commit()
    names = [c.name for c in scrapers.get_courses(db)]
    assert "Capstone" in names
    cap = next(c for c in scrapers.get_courses(db) if c.name == "Capstone")
    assert cap.parser == "none" and db.scalar(select(Task)).course_id == cap.id
