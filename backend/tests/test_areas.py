from datetime import datetime, timedelta

import pytest

from planner import areas as A
from planner import service
from planner.app import create_app

NOW = datetime(2026, 10, 20, 19, 0)


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    client = create_app("sqlite://", testing=True).test_client()
    client.post("/auth/dev-login")
    return client


def area(c, kind):
    return next(x for x in c.get("/api/courses").get_json()["courses"] if x["kind"] == kind)


def tasks(c):
    return c.get("/api/tasks").get_json()


def test_job_next_step_becomes_a_task_and_follows_edits(c):
    car = area(c, "career")
    e = c.post(f"/api/areas/{car['id']}/entries", json={"type": "job", "date": "2026-10-23T17:00",
               "data": {"company": "Stripe", "role": "New grad SWE", "stage": "applied", "next_step": "Do OA"}}).get_json()
    t = next(t for t in tasks(c) if t["id"] == e["task_id"])
    assert (t["title"], t["due"], t["course_id"]) == ("Stripe: Do OA", "2026-10-23T17:00", car["id"])
    c.patch(f"/api/entries/{e['id']}", json={"data": {"stage": "rejected"}})
    assert all(t["id"] != e["task_id"] for t in tasks(c))
    s = c.get(f"/api/areas/{car['id']}/summary").get_json()
    assert s["counts"] == {"rejected": 1}
    assert c.post(f"/api/areas/{car['id']}/entries", json={"type": "job", "data": {"stage": "ghosted"}}).status_code == 400


def test_bill_becomes_repeating_pay_task_unless_autopay(c):
    fin = area(c, "finance")
    e = c.post(f"/api/areas/{fin['id']}/entries", json={"type": "bill", "date": "2026-11-01T12:00",
               "data": {"name": "Rent", "amount": "950", "frequency": "monthly"}}).get_json()
    t = next(t for t in tasks(c) if t["id"] == e["task_id"])
    assert t["title"] == "Pay Rent ($950.00)" and t["repeat"] == "monthly"
    c.patch(f"/api/entries/{e['id']}", json={"data": {"autopay": True}})
    assert not any(t["title"].startswith("Pay Rent") for t in tasks(c))
    c.post(f"/api/areas/{fin['id']}/entries", json={"type": "budget", "data": {"category": "Food", "limit": 300}})
    c.post(f"/api/areas/{fin['id']}/entries", json={"type": "expense", "date": "2026-10-19T12:00", "data": {"category": "Food", "amount": 12.5}})
    s = c.get(f"/api/areas/{fin['id']}/summary").get_json()
    assert s["spent"] == {"Food": 12.5} and s["budget_total"] == 300


def test_fitness_meals_metrics_and_token_ingest(c):
    fit = area(c, "fitness")
    c.post(f"/api/areas/{fit['id']}/entries", json={"type": "meal", "date": "2026-10-20T12:30",
           "data": {"name": "Chicken bowl", "calories": 750, "protein": 45}})
    assert c.post(f"/api/areas/{fit['id']}/entries", json={"type": "meal", "data": {"calories": "lots"}}).status_code == 400
    tok = c.post("/api/token").get_json()["token"]
    anon = create_app("sqlite://", testing=True).test_client()  # separate app: no session
    assert anon.post("/api/health/ingest", json={"steps": 1}).status_code == 401
    r = c.post("/api/health/ingest", json={"date": "2026-10-20", "sleep_minutes": 410},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    c.post("/api/health/ingest", json={"date": "2026-10-20", "steps": 9000})
    s = c.get(f"/api/areas/{fit['id']}/summary").get_json()
    today = s["days"][-1]
    assert (today["calories"], today["protein"], today["sleep_minutes"], today["steps"]) == (750, 45, 410, 9000)
    assert s["today"]["calories"] == 750
    # a course doesn't take meals
    course = next(x for x in c.get("/api/courses").get_json()["courses"] if x["kind"] == "course")
    assert c.post(f"/api/areas/{course['id']}/entries", json={"type": "meal", "data": {}}).status_code == 400


def test_token_lets_extension_add_tasks(c):
    tok = c.post("/api/token").get_json()["token"]
    c.post("/auth/logout")
    assert c.post("/api/tasks", json={"text": "Review PR #12 by Friday"}).status_code == 401
    r = c.post("/api/tasks", json={"text": "Review PR #12 by Friday"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 201 and r.get_json()["task"]["due"] == "2026-10-23T23:59"
    assert c.post("/api/tasks", json={"text": "x"}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_meal_estimate_needs_key(c, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    import io
    r = c.post("/api/fitness/estimate-meal", data={"photo": (io.BytesIO(b"\xff\xd8fake"), "m.jpg", "image/jpeg")},
               content_type="multipart/form-data")
    assert r.status_code == 502 and "GEMINI_API_KEY" in r.get_json()["error"]


def test_money_promises_and_memos(c):
    me = area(c, "personal")
    post = lambda body: c.post(f"/api/areas/{me['id']}/entries", json=body)
    a = post({"type": "iou", "date": "2026-10-23T12:00", "data": {"person": "Sanil", "amount": 20, "direction": "i_owe", "note": "pizza"}}).get_json()
    post({"type": "iou", "data": {"person": "Sanil", "amount": 5, "direction": "they_owe"}})
    post({"type": "iou", "data": {"person": "Mark", "amount": 12.5, "direction": "they_owe"}})
    assert post({"type": "iou", "data": {"direction": "sideways"}}).status_code == 400
    s = c.get(f"/api/areas/{me['id']}/summary").get_json()
    assert {b["person"]: b["net"] for b in s["balances"]} == {"Sanil": -15.0, "Mark": 12.5}
    assert (s["owed_to_me"], s["i_owe"]) == (12.5, 15.0)
    assert any(t["title"] == "Pay Sanil back $20.00" for t in tasks(c))
    c.patch(f"/api/entries/{a['id']}", json={"data": {"settled": True}})
    assert not any(t["title"].startswith("Pay Sanil") for t in tasks(c))

    p = post({"type": "promise", "date": "2026-10-22T18:00", "data": {"text": "Send Rishi the demo video", "to": "Rishi"}}).get_json()
    t = next(t for t in tasks(c) if t["id"] == p["task_id"])
    assert t["title"] == "Send Rishi the demo video (promised Rishi)"
    c.patch(f"/api/entries/{p['id']}", json={"data": {"status": "kept"}})
    assert all(t["id"] != p["task_id"] for t in tasks(c))           # done, not deleted
    assert any(t["id"] == p["task_id"] for t in c.get("/api/tasks?status=done").get_json())
    q = post({"type": "promise", "data": {"text": "Gym 3x this week", "to": "myself"}}).get_json()
    c.patch(f"/api/entries/{q['id']}", json={"data": {"status": "broken"}})
    s = c.get(f"/api/areas/{me['id']}/summary").get_json()
    assert s["word"] == {"kept": 1, "total": 2}

    post({"type": "memo", "data": {"text": "Can't miss any more PLC classes", "pinned": True}})
    post({"type": "memo", "data": {"text": "Locker code 1234"}})
    today = c.get("/api/today").get_json()["personal"]
    assert [m["data"]["text"] for m in today["pinned"]] == ["Can't miss any more PLC classes"]
    assert today["word"] == {"kept": 1, "total": 2}
