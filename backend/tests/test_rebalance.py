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