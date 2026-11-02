"""Calendar changes with ?replan=0 are saved but nothing else moves until /api/replan."""
from datetime import datetime

import pytest

from planner import service
from planner.app import create_app

NOW = datetime(2026, 10, 28, 8, 0)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    cl = create_app("sqlite://", testing=True).test_client()
    cl.post("/auth/dev-login")
    return cl


def blocks(cl):
    return cl.get("/api/blocks?start=2026-10-28T00:00&end=2026-11-04T00:00").get_json()


def test_deferred_move_and_delete_wait_for_rebalance(client):
    a = client.post("/api/tasks", json={"title": "Essay", "due": "2026-10-30T17:00", "est_minutes": 120}).get_json()["task"]
    b = client.post("/api/tasks", json={"title": "Lab", "due": "2026-10-30T17:00", "est_minutes": 60}).get_json()["task"]
    d = blocks(client)
    assert d["dirty"] is False
    essay = next(x for x in d["blocks"] if x["task_id"] == a["id"])
    before = {x["id"]: (x["start"], x["end"]) for x in d["blocks"] if x["kind"] == "task"}

    # move the essay block onto the lab's time: saved and pinned, nothing else moves
    lab = next(x for x in d["blocks"] if x["task_id"] == b["id"])
    assert client.patch(f"/api/blocks/{essay['id']}?replan=0", json={"start": lab["start"], "end": lab["end"]}).status_code == 200
    d = blocks(client)
    assert d["dirty"] is True
    assert next(x for x in d["blocks"] if x["id"] == lab["id"])["start"] == before[lab["id"]][0]   # overlap until rebalance
    moved = next(x for x in d["blocks"] if x["id"] == essay["id"])
    assert moved["start"] == lab["start"] and moved["locked"]

    # deleting a task also waits, but its planned blocks go right away
    assert client.delete(f"/api/tasks/{b['id']}?replan=0").status_code == 200
    d = blocks(client)
    assert d["dirty"] and not any(x["task_id"] == b["id"] for x in d["blocks"])

    client.post("/api/replan")
    d = blocks(client)
    assert d["dirty"] is False
    assert next(x for x in d["blocks"] if x["id"] == essay["id"])["start"] == lab["start"]   # pin kept


def test_normal_requests_still_replan(client):
    a = client.post("/api/tasks", json={"title": "Essay", "due": "2026-10-30T17:00", "est_minutes": 60}).get_json()["task"]
    blk = next(x for x in blocks(client)["blocks"] if x["task_id"] == a["id"])
    client.patch(f"/api/blocks/{blk['id']}", json={"start": "2026-10-29T13:00", "end": "2026-10-29T14:00"})
    assert blocks(client)["dirty"] is False


def test_start_window_keeps_far_off_work_out_of_this_week(client):
    small = client.post("/api/tasks", json={"title": "Reading", "due": "2026-11-03T17:00", "est_minutes": 45}).get_json()["task"]
    big = client.post("/api/tasks", json={"title": "Project", "due": "2026-11-03T17:00", "est_minutes": 300}).get_json()["task"]
    bl = blocks(client)["blocks"]
    first = lambda tid: min(x["start"] for x in bl if x["task_id"] == tid)
    assert first(small["id"]) >= "2026-11-01"      # ~2 days ahead
    assert first(big["id"]) < "2026-11-01"         # big work starts earlier


def test_window_relaxes_when_work_would_not_fit(client):
    # Fri to Tue are fully booked, so a 4-day window (starting Fri) has no room
    client.post("/api/appointments", json={"title": "Trip", "start": "2026-10-30T00:00", "end": "2026-11-03T23:00"})
    t = client.post("/api/tasks", json={"title": "Lab report", "due": "2026-11-03T23:30", "est_minutes": 150}).get_json()["task"]
    bl = [x for x in blocks(client)["blocks"] if x["task_id"] == t["id"]]
    assert bl and all(x["start"] < "2026-10-30" for x in bl)
    plan = client.post("/api/replan").get_json()
    assert any("Lab report starts earlier than usual" in w["message"] for w in plan["warnings"])


def test_area_lead_days_and_not_yet(client):
    cs = client.get("/api/courses").get_json()
    c = (cs["courses"] if isinstance(cs, dict) else cs)[0]
    client.patch(f"/api/courses/{c['id']}", json={"lead_days": 1})
    t = client.post("/api/tasks", json={"title": "Lab", "due": "2026-11-02T17:00", "est_minutes": 60, "course_id": c["id"]}).get_json()["task"]
    assert min(x["start"] for x in blocks(client)["blocks"] if x["task_id"] == t["id"]) >= "2026-11-01"

    u = client.post("/api/tasks", json={"title": "Essay", "due": "2026-10-29T23:00", "est_minutes": 60}).get_json()["task"]
    blk = next(x for x in blocks(client)["blocks"] if x["task_id"] == u["id"])
    assert client.post(f"/api/blocks/{blk['id']}/not-yet", json={"until": "2026-10-31"}).status_code == 400  # after due
    assert client.post(f"/api/blocks/{blk['id']}/not-yet", json={}).status_code == 200
    assert all(x["start"] >= "2026-10-29" for x in blocks(client)["blocks"] if x["task_id"] == u["id"])


def test_keep_free_slot_stays_empty_and_pins_can_be_undone(client):
    r = client.post("/api/appointments", json={"title": "Keep free", "kind": "free", "start": "2026-10-28T08:00", "end": "2026-10-28T12:00"})
    assert r.status_code == 201 and r.get_json()["appointment"]["kind"] == "busy"
    t = client.post("/api/tasks", json={"title": "Essay", "due": "2026-10-29T17:00", "est_minutes": 60, "priority": 1}).get_json()["task"]
    blk = next(x for x in blocks(client)["blocks"] if x["task_id"] == t["id"])
    assert blk["start"] >= "2026-10-28T12:00" and blk["priority"] == 1
    client.patch(f"/api/blocks/{blk['id']}", json={"start": "2026-10-29T09:00", "end": "2026-10-29T10:00"})
    assert next(x for x in blocks(client)["blocks"] if x["id"] == blk["id"])["locked"]
    client.post(f"/api/blocks/{blk['id']}/unlock?replan=0")
    d = blocks(client)
    assert d["dirty"] and not next(x for x in d["blocks"] if x["id"] == blk["id"])["locked"]