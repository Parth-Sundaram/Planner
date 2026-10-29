from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from planner import assistant, service
from planner.app import create_app
from planner.models import AssistantNote, Block, Routine, Task, make_session_factory

NOW = datetime(2026, 10, 20, 19, 0)


@pytest.fixture
def db(monkeypatch):
    s = make_session_factory("sqlite://")()
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    s.add(Routine(title="Gym", days="MTWRFSU", window_start="15:00", window_end="21:00", duration=60, kind="gym"))
    s.add(Task(title="CNN Part 3", due=datetime(2026, 10, 21, 23, 59), est_minutes=150))
    s.commit()
    service.replan(s, NOW)
    yield s
    s.close()


def fake(parts, seen):
    def call(contents, system):
        seen.append(contents)
        return {"candidates": [{"content": {"parts": parts}}]}
    return call


def test_snapshot_is_compact_and_has_ids(db):
    snap = assistant.snapshot(db, NOW)
    assert "NOW: Tuesday 2026-10-20 19:00" in snap
    assert "| CNN Part 3 |" in snap and snap.count("\n") < 80
    assert any(line.strip().startswith("b") for line in snap.splitlines())


def test_chat_proposes_then_apply_runs_and_notes_save_immediately(db, monkeypatch):
    gym = db.scalar(select(Block).where(Block.kind == "routine", Block.start >= datetime(2026, 10, 21)))
    task = db.scalar(select(Task))
    seen = []
    monkeypatch.setattr(assistant, "_call", fake([
        {"text": "Skipping tomorrow's gym and giving CNN more time."},
        {"functionCall": {"name": "skip_routine", "args": {"block_id": gym.id}}},
        {"functionCall": {"name": "update_task", "args": {"task_id": f"t{task.id}", "est_minutes": 210}}},
        {"functionCall": {"name": "remember", "args": {"note": "CNN parts take ~3.5h"}}},
        {"functionCall": {"name": "drop_database", "args": {}}},
    ], seen))
    r = assistant.chat(db, "skip gym tomorrow, CNN will take longer")
    assert r["reply"].startswith("Skipping")
    assert [a["name"] for a in r["actions"]] == ["skip_routine", "update_task", "remember"]
    assert r["actions"][2]["applied"] and db.scalar(select(AssistantNote)).text == "CNN parts take ~3.5h"
    db.refresh(gym); db.refresh(task)
    assert gym.status == "planned" and task.est_minutes == 150   # nothing changed until approved
    res = assistant.apply(db, r["actions"])
    assert all(x["ok"] for x in res)
    db.refresh(task)
    assert task.est_minutes == 210
    assert db.get(Block, gym.id).status == "skipped"
    # the note comes back in the next request's snapshot, plus history
    assistant.chat(db, "what's left?")
    last = seen[-1]
    assert "CNN parts take ~3.5h" in last[-1]["parts"][0]["text"] and len(last) == 3


def test_bad_action_reports_error_without_breaking_others(db):
    task = db.scalar(select(Task))
    res = assistant.apply(db, [{"id": 0, "name": "update_task", "args": {"task_id": 9999, "priority": 1}},
                               {"id": 1, "name": "add_task", "args": {"title": "Email prof", "due": "2026-10-22T12:00", "est_minutes": 10}}])
    assert res[0]["ok"] is False and res[1]["ok"] is True
    assert db.scalar(select(Task).where(Task.title == "Email prof")).due == datetime(2026, 10, 22, 12, 0)


def test_api_without_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    c = create_app("sqlite://", testing=True).test_client()
    c.post("/auth/dev-login")
    r = c.post("/api/assistant", json={"message": "hi"})
    assert r.status_code == 502 and "GEMINI_API_KEY" in r.get_json()["error"]
    assert c.get("/api/assistant").get_json() == {"messages": [], "notes": []}


def test_several_block_actions_survive_replans_in_between(db):
    gym_tmrw, gym_next = db.scalars(select(Block).where(Block.kind == "routine", Block.start >= datetime(2026, 10, 21))
                                    .order_by(Block.start).limit(2)).all()
    res = assistant.apply(db, [{"id": 0, "name": "skip_routine", "args": {"block_id": gym_tmrw.id}},
                               {"id": 1, "name": "skip_routine", "args": {"block_id": gym_next.id}}])
    assert all(r["ok"] for r in res), res
    skipped = {b.start.date() for b in db.scalars(select(Block).where(Block.status == "skipped"))}
    assert skipped == {gym_tmrw.start.date(), gym_next.start.date()}


def test_money_and_promise_tools(db):
    from planner.scrapers import get_courses
    get_courses(db)  # seeds the Personal area
    res = assistant.apply(db, [
        {"id": 0, "name": "log_money", "args": {"person": "Sanil", "amount": 18, "direction": "i_owe", "note": "Chipotle"}},
        {"id": 1, "name": "make_promise", "args": {"text": "Send demo video", "to": "Rishi", "by": "2026-10-22T18:00"}},
        {"id": 2, "name": "pin_memo", "args": {"text": "Bring laptop charger Friday"}}])
    assert all(r["ok"] for r in res), res
    snap = assistant.snapshot(db, NOW)
    assert "Sanil | $18.00 | user owes them" in snap and "Send demo video" in snap and "Bring laptop charger" in snap
    eid = int(snap.split("MONEY")[1].splitlines()[1].split("|")[0].strip()[1:])
    assert assistant.apply(db, [{"id": 3, "name": "settle_money", "args": {"entry_id": f"e{eid}"}}])[0]["ok"]
    assert "Sanil | $18.00" not in assistant.snapshot(db, NOW)


def test_tick_habit_tool(db):
    from planner.models import Habit
    from planner import habits as HB
    db.add(Habit(name="Drink water (bottles)", target=3)); db.commit()
    snap = assistant.snapshot(db, NOW)
    assert "Drink water (bottles) | no (0.0/3.0)" in snap
    hid = db.scalar(select(Habit)).id
    assert assistant.apply(db, [{"id": 0, "name": "tick_habit", "args": {"habit_id": f"h{hid}", "amount": 2}}])[0]["ok"]
    h = HB.summary(db, NOW.date(), 7)[0]
    assert h["today"]["value"] == 2.0 and not h["today"]["done"]
