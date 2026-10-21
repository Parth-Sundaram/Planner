from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from planner import service
from planner.models import Block, ExternalEvent, Routine, Rule, Task, make_session_factory

NOW = datetime(2026, 10, 19, 8, 0)


@pytest.fixture
def db(monkeypatch):
    Session = make_session_factory("sqlite://")
    s = Session()
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    yield s
    s.close()


def blocks_for(db, task_id):
    return db.scalars(select(Block).where(Block.task_id == task_id).order_by(Block.start)).all()


def test_replan_creates_blocks_and_respects_calendar(db):
    db.add(ExternalEvent(gcal_id="c1", calendar_id="primary", title="Class", start=NOW.replace(hour=10), end=NOW.replace(hour=12)))
    t = Task(title="CPS", due=NOW + timedelta(days=1, hours=15), est_minutes=180)
    db.add(t); db.commit()
    service.replan(db, NOW)
    bs = blocks_for(db, t.id)
    assert sum(service.minutes_between(b.start, b.end) for b in bs) == 180
    assert all(not (b.start < NOW.replace(hour=12) and b.end > NOW.replace(hour=10)) for b in bs)


def test_replan_is_idempotent(db):
    db.add(Task(title="A", due=NOW + timedelta(days=2), est_minutes=120)); db.commit()
    service.replan(db, NOW); n1 = len(db.scalars(select(Block)).all())
    service.replan(db, NOW); n2 = len(db.scalars(select(Block)).all())
    assert n1 == n2


def test_rule_applies_by_tag(db):
    db.add(Rule(tag="db", weekday=2, time="23:59"))
    t = Task(title="M3", due=datetime(2026, 10, 23, 23, 55), est_minutes=90, tags="db")
    db.add(t); db.commit()
    service.replan(db, NOW)
    assert max(b.end for b in blocks_for(db, t.id)) <= datetime(2026, 10, 21, 23, 59)


def test_partial_checkin_reschedules_rest(db):
    t = Task(title="CNN", due=NOW + timedelta(days=3), est_minutes=120, max_block=120)
    db.add(t); db.commit()
    service.replan(db, NOW)
    first = blocks_for(db, t.id)[0]
    service.check_in(db, first.id, "partial", 30)
    db.refresh(t)
    assert t.done_minutes == 30
    planned = [b for b in blocks_for(db, t.id) if b.status == "planned"]
    assert sum(service.minutes_between(b.start, b.end) for b in planned) == 90


def test_done_checkin_completes_task(db):
    t = Task(title="Quiz", due=NOW + timedelta(days=1), est_minutes=30)
    db.add(t); db.commit()
    service.replan(db, NOW)
    service.check_in(db, blocks_for(db, t.id)[0].id, "done")
    db.refresh(t)
    assert t.status == "done"


def test_moved_block_is_locked_and_kept(db):
    t = Task(title="Slides", due=NOW + timedelta(days=3), est_minutes=60, max_block=60)
    db.add(t); db.commit()
    service.replan(db, NOW)
    b = blocks_for(db, t.id)[0]
    new_start = NOW.replace(hour=20)
    service.move_block(db, b.id, new_start, new_start + timedelta(minutes=60))
    bs = blocks_for(db, t.id)
    assert len(bs) == 1 and bs[0].locked and bs[0].start == new_start


def test_something_came_up_pushes_work(db):
    t = Task(title="Work", due=NOW + timedelta(days=2), est_minutes=60, max_block=60)
    db.add(t); db.commit()
    service.replan(db, NOW)
    assert blocks_for(db, t.id)[0].start == NOW
    service.something_came_up(db, 120)
    assert blocks_for(db, t.id)[0].start >= NOW + timedelta(minutes=120)


def test_routines_placed_and_not_duplicated(db):
    db.add(Routine(title="Lunch", days="MTWRF", window_start="12:00", window_end="13:45", duration=45, kind="meal"))
    db.commit()
    service.replan(db, NOW); service.replan(db, NOW)
    lunches = db.scalars(select(Block).where(Block.kind == "routine")).all()
    days = [b.start.date() for b in lunches]
    assert len(days) == len(set(days))


def test_parse_meta():
    title, meta = service.parse_meta("CNN 4/4 ~4h !1 #ai #cnn squeeze:3h min:45 nosplit")
    assert title == "CNN 4/4"
    assert meta == {"est_minutes": 240, "priority": 1, "tags": ["ai", "cnn"],
                    "min_total_minutes": 180, "min_block": 45, "splittable": False}
    assert service.parse_meta("Read paper ~90m")[1] == {"est_minutes": 90}


def test_subtasks_scheduled_in_order_and_parent_not_scheduled(db):
    parent = Task(title="Stripe", due=NOW + timedelta(days=4), est_minutes=600, sequential=True)
    db.add(parent); db.flush()
    steps = [Task(title=n, due=parent.due, est_minutes=m, parent_id=parent.id, position=i, max_block=60)
             for i, (n, m) in enumerate([("Design", 60), ("Build", 120), ("Test", 60)])]
    db.add_all(steps); db.commit()
    service.replan(db, NOW)
    assert not blocks_for(db, parent.id)
    spans = [(min(b.start for b in blocks_for(db, s.id)), max(b.end for b in blocks_for(db, s.id))) for s in steps]
    assert spans[0][1] <= spans[1][0] and spans[1][1] <= spans[2][0]
    assert blocks_for(db, steps[0].id)[0].title == "Stripe › Design"


def test_finishing_all_steps_closes_parent_and_finish_parent_closes_steps(db):
    p = Task(title="P", due=NOW + timedelta(days=2), est_minutes=60)
    db.add(p); db.flush()
    a = Task(title="a", due=p.due, est_minutes=30, parent_id=p.id, position=0)
    b = Task(title="b", due=p.due, est_minutes=30, parent_id=p.id, position=1)
    db.add_all([a, b]); db.commit()
    service.replan(db, NOW)
    service.check_in(db, blocks_for(db, a.id)[0].id, "done")
    db.refresh(p); assert p.status == "open"
    service.check_in(db, blocks_for(db, b.id)[0].id, "done")
    db.refresh(p); assert p.status == "done"
    p2 = Task(title="P2", due=NOW + timedelta(days=2), est_minutes=60)
    db.add(p2); db.flush()
    c = Task(title="c", due=p2.due, est_minutes=30, parent_id=p2.id)
    db.add(c); db.commit()
    service.finish_task(db, p2.id)
    db.refresh(c); assert c.status == "done"


def test_sequential_step_deadline_leaves_room_for_later_steps(db):
    p = Task(title="P", due=NOW + timedelta(days=5), est_minutes=120, sequential=True)
    db.add(p); db.flush()
    early = Task(title="first", due=p.due, est_minutes=60, parent_id=p.id, position=0)
    late = Task(title="second", due=NOW + timedelta(days=1), est_minutes=60, parent_id=p.id, position=1)
    db.add_all([early, late]); db.commit()
    service.replan(db, NOW)
    assert max(b.end for b in blocks_for(db, early.id)) <= min(b.start for b in blocks_for(db, late.id))
    assert max(b.end for b in blocks_for(db, late.id)) <= late.due


def test_routine_that_already_happened_today_is_not_replaced(db):
    db.add(Routine(title="Breakfast", days="MTWRFSU", window_start="07:30", window_end="10:00", duration=30, kind="meal"))
    db.commit()
    service.replan(db, NOW)                      # placed 8:00-8:30
    later = NOW.replace(hour=8, minute=45)
    service.replan(db, later)                    # breakfast already over; must not reappear at 8:45
    today = [b for b in db.scalars(select(Block).where(Block.kind == "routine")) if b.start.date() == NOW.date()]
    assert len(today) == 1 and today[0].start == NOW


def test_block_pinned_after_deadline_doesnt_count(db):
    t = Task(title="Quiz prep", due=NOW + timedelta(days=1), est_minutes=60, max_block=60)
    db.add(t); db.commit()
    service.replan(db, NOW)
    b = blocks_for(db, t.id)[0]
    late = NOW + timedelta(days=2)
    service.move_block(db, b.id, late, late + timedelta(minutes=60))
    bs = blocks_for(db, t.id)
    before = [x for x in bs if x.end <= t.due]
    assert sum(service.minutes_between(x.start, x.end) for x in before) == 60
    plan = service.replan(db, NOW)
    assert any("after the deadline" in w["message"] for w in plan["warnings"])
