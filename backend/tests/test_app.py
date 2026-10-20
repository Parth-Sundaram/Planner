from datetime import datetime, timedelta

import pytest

from planner.app import create_app


@pytest.fixture
def client():
    app = create_app("sqlite://", testing=True)
    c = app.test_client()
    yield c


def login(c):
    assert c.post("/auth/dev-login").status_code == 200


def soon(days=2):
    return (datetime.now() + timedelta(days=days)).replace(second=0, microsecond=0).isoformat()


def test_requires_login(client):
    assert client.get("/api/tasks").status_code == 401
    assert client.post("/api/sync").status_code == 401


def test_quick_add_creates_task_and_blocks(client):
    login(client)
    r = client.post("/api/tasks", json={"text": "CNN 4/4 ~3h !1 #ai", "due": soon()})
    assert r.status_code == 201
    t = r.get_json()["task"]
    assert (t["title"], t["est_minutes"], t["priority"], t["tags"]) == ("CNN 4/4", 180, 1, ["ai"])
    blocks = client.get("/api/blocks").get_json()["blocks"]
    assert sum((datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).seconds // 60
               for b in blocks if b["task_id"] == t["id"]) == 180


def test_checkin_move_and_came_up(client):
    login(client)
    t = client.post("/api/tasks", json={"title": "Work", "due": soon(3), "est_minutes": 60, "max_block": 60}).get_json()["task"]
    b = next(b for b in client.get("/api/blocks?start=" + datetime.now().isoformat()[:16]).get_json()["blocks"] if b["task_id"] == t["id"])
    start = datetime.fromisoformat(b["start"]) + timedelta(days=1)
    assert client.patch(f"/api/blocks/{b['id']}", json={"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}).status_code == 200
    moved = next(x for x in client.get("/api/blocks?start=" + datetime.now().isoformat()[:16]).get_json()["blocks"] if x["task_id"] == t["id"])
    assert moved["locked"] and moved["start"] == start.isoformat(timespec="minutes")
    assert client.post(f"/api/blocks/{b['id']}/checkin", json={"status": "done"}).status_code == 200
    assert client.get("/api/tasks?status=done").get_json()[0]["title"] == "Work"
    assert client.post("/api/came-up", json={"minutes": 30}).status_code == 200
    assert client.post(f"/api/blocks/{b['id']}/checkin", json={"status": "bogus"}).status_code == 400


def test_routines_rules_settings_today(client):
    login(client)
    assert client.post("/api/routines", json={"title": "Lunch", "days": "MTWRFSU", "window_start": "12:00",
                                             "window_end": "13:45", "duration": 45, "kind": "meal"}).status_code == 201
    assert client.post("/api/rules", json={"tag": "db", "weekday": 2, "time": "23:59"}).status_code == 201
    assert client.put("/api/settings", json={"day_start": "07:30"}).status_code == 200
    assert client.get("/api/settings").get_json()["day_start"] == "07:30"
    today = client.get("/api/today").get_json()
    assert "warnings" in today and "remaining_task_minutes" in today


def test_sync_without_google_still_replans(client, monkeypatch):
    login(client)
    from planner import scrapers
    monkeypatch.setattr(scrapers, "fetch_html", lambda url, render_js=False: "<table></table>")
    r = client.post("/api/sync").get_json()
    assert r["google"] == "not connected" and "plan" in r


def test_cron_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    from planner import scrapers
    monkeypatch.setattr(scrapers, "fetch_html", lambda url, render_js=False: "<table></table>")
    assert client.post("/api/sync?scrape=0", headers={"X-Cron-Secret": "wrong"}).status_code == 401
    assert client.post("/api/sync?scrape=0", headers={"X-Cron-Secret": "s3cret"}).status_code == 200


def test_subtask_api(client):
    login(client)
    p = client.post("/api/tasks", json={"text": "Stripe ~3h #capstone", "due": soon(4)}).get_json()["task"]
    a = client.post("/api/tasks", json={"text": "Read Stripe docs ~1h", "parent_id": p["id"]}).get_json()["task"]
    b = client.post("/api/tasks", json={"text": "Payment intent endpoint ~2h", "parent_id": p["id"]}).get_json()["task"]
    assert a["due"] == p["due"] and (a["position"], b["position"]) == (0, 1)
    (tree,) = client.get("/api/tasks").get_json()
    assert [s["title"] for s in tree["subtasks"]] == ["Read Stripe docs", "Payment intent endpoint"]
    assert tree["est_minutes"] == 180
    blocks = client.get("/api/blocks").get_json()["blocks"]
    assert any(x["title"] == "Stripe › Read Stripe docs" for x in blocks)
    assert not any(x["task_id"] == p["id"] for x in blocks)
    # Moving the parent's deadline moves steps that followed it.
    new_due = soon(5)
    client.patch(f"/api/tasks/{p['id']}", json={"due": new_due})
    (tree,) = client.get("/api/tasks").get_json()
    assert all(s["due"] == new_due[:16] for s in tree["subtasks"])
    client.delete(f"/api/tasks/{p['id']}")
    assert client.get("/api/tasks?status=all").get_json() == []
