from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from planner import gsync, service
from planner.models import Block, ExternalEvent, Task, make_session_factory

from .fakes import FakeCalendar, FakeTasks

NOW = datetime(2026, 10, 19, 8, 0)


@pytest.fixture
def db(monkeypatch):
    s = make_session_factory("sqlite://")()
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    yield s
    s.close()


def sync(db, cal, tasks=None):
    return gsync.full_sync(db, NOW, services=(cal, tasks or FakeTasks([])), scrape=False)


def plan_events(cal, db):
    return list(cal.cals[service.get_settings(db)["plan_calendar_id"]].values())


def test_busy_events_imported_and_avoided(db):
    cal = FakeCalendar()
    cal.add_event("primary", "Class", "2026-10-19T10:00:00-04:00", "2026-10-19T12:00:00-04:00")
    cal.add_event("primary", "Free thing", "2026-10-19T08:00:00-04:00", "2026-10-19T23:00:00-04:00", transparency="transparent")
    db.add(Task(title="CPS", due=NOW + timedelta(days=1), est_minutes=300)); db.commit()
    sync(db, cal)
    assert [e.title for e in db.scalars(select(ExternalEvent))] == ["Class"]
    for b in db.scalars(select(Block).where(Block.kind == "task")):
        assert b.end <= NOW.replace(hour=10) or b.start >= NOW.replace(hour=12)


def test_plan_pushed_to_plan_calendar_and_stable(db):
    cal = FakeCalendar()
    db.add(Task(title="CNN", due=NOW + timedelta(days=2), est_minutes=120)); db.commit()
    r1 = sync(db, cal)
    assert r1["pushed"]["created"] >= 1
    n = len(plan_events(cal, db))
    cal.calls.clear()
    r2 = sync(db, cal)  # nothing changed -> no churn
    assert r2["pushed"] == {"created": 0, "updated": 0, "deleted": 0}
    assert len(plan_events(cal, db)) == n


def test_drag_in_google_calendar_locks_block(db):
    cal = FakeCalendar()
    t = Task(title="Slides", due=NOW + timedelta(days=2), est_minutes=60, max_block=60)
    db.add(t); db.commit()
    sync(db, cal)
    ev = next(e for e in plan_events(cal, db) if e["summary"] == "Slides")
    ev["start"] = {"dateTime": "2026-10-19T20:00:00-04:00"}
    ev["end"] = {"dateTime": "2026-10-19T21:00:00-04:00"}
    r = sync(db, cal)
    assert r["plan_edits"]["moved"] == 1
    (b,) = db.scalars(select(Block).where(Block.task_id == t.id)).all()
    assert b.locked and b.start == NOW.replace(hour=20)


def test_delete_in_google_calendar_reschedules(db):
    cal = FakeCalendar()
    t = Task(title="Read", due=NOW + timedelta(days=2), est_minutes=60, max_block=60)
    db.add(t); db.commit()
    sync(db, cal)
    pid = service.get_settings(db)["plan_calendar_id"]
    ev = next(e for e in plan_events(cal, db) if e["summary"] == "Read")
    del cal.cals[pid][ev["id"]]
    r = sync(db, cal)
    assert r["plan_edits"]["removed"] == 1
    assert db.scalars(select(Block).where(Block.task_id == t.id)).all()  # replanned


def test_google_tasks_import_with_metadata(db):
    cal = FakeCalendar()
    gt = [
        {"id": "a", "title": "Prac Sec HW @2pm", "notes": "~2h !1 #prac", "due": "2026-10-22T00:00:00.000Z", "status": "needsAction"},
        {"id": "b", "title": "No due date", "status": "needsAction"},
        {"id": "c", "title": "Old", "due": "2026-10-01T00:00:00.000Z", "status": "needsAction"},
    ]
    r = sync(db, cal, FakeTasks(gt))
    assert r["google_tasks"]["created"] == 1 and r["google_tasks"]["skipped_no_due"] == 1
    t = db.scalar(select(Task).where(Task.external_id == "gtasks:a"))
    assert (t.title, t.due, t.est_minutes, t.priority, t.tags, t.needs_estimate) == \
        ("Prac Sec HW", datetime(2026, 10, 22, 14, 0), 120, 1, "prac", False)
    gt[0]["status"] = "completed"
    sync(db, cal, FakeTasks(gt))
    db.refresh(t)
    assert t.status == "done"


def test_google_subtasks_attach_to_parent(db):
    cal = FakeCalendar()
    gt = [
        {"id": "s1", "title": "Webhook ~1h", "parent": "p", "position": "00000000000000000001", "status": "needsAction"},
        {"id": "p", "title": "Stripe @5pm", "notes": "#capstone", "due": "2026-10-23T00:00:00.000Z", "status": "needsAction"},
    ]
    sync(db, cal, FakeTasks(gt))
    parent = db.scalar(select(Task).where(Task.external_id == "gtasks:p"))
    child = db.scalar(select(Task).where(Task.external_id == "gtasks:s1"))
    assert child.parent_id == parent.id and child.due == datetime(2026, 10, 23, 17, 0)
    assert child.est_minutes == 60
