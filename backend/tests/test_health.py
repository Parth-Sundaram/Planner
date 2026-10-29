from datetime import datetime

import pytest

from planner import service
from planner.app import create_app

NOW = datetime(2026, 10, 29, 9, 0)


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    cl = create_app("sqlite://", testing=True).test_client()
    cl.post("/auth/dev-login")
    return cl


def fitness(c):
    return next(x for x in c.get("/api/courses").get_json()["courses"] if x["kind"] == "fitness")


PAYLOAD = {
    "timestamp": "2026-10-29T13:00:00Z", "app_version": "1.9.30", "timezone": "UTC",
    "steps": [{"count": 4000, "start_time": "2026-10-28T12:00:00Z", "end_time": "2026-10-28T16:00:00Z", "metadata": {"id": "s1"}},
              {"count": 3500, "start_time": "2026-10-28T20:00:00Z", "end_time": "2026-10-28T23:00:00Z", "metadata": {"id": "s2"}}],
    "sleep": [{"session_end_time": "2026-10-29T12:30:00Z", "duration_seconds": 25200, "metadata": {"id": "z1"},
               "stages": [{"stage": "STAGE_TYPE_AWAKE", "start_time": "2026-10-29T08:00:00Z", "end_time": "2026-10-29T08:20:00Z", "duration_seconds": 1200},
                          {"stage": "STAGE_TYPE_LIGHT", "start_time": "2026-10-29T08:20:00Z", "end_time": "2026-10-29T12:30:00Z", "duration_seconds": 15000}]}],
    "heart_rate": [{"time": "2026-10-28T14:00:00Z", "avg": 70, "min": 60, "max": 95, "bpm": 70},
                   {"time": "2026-10-28T15:00:00Z", "avg": 80, "min": 72, "max": 120, "bpm": 80}],
    "resting_heart_rate": [{"bpm": 61, "time": "2026-10-28T10:00:00Z", "metadata": {"id": "r1"}}],
    "exercise": [{"type": "EXERCISE_TYPE_WEIGHTLIFTING", "start_time": "2026-10-28T19:00:00Z", "end_time": "2026-10-28T20:00:00Z",
                  "duration_seconds": 3600, "metadata": {"id": "x1"}}],
}


def test_health_connect_webhook_builds_daily_numbers(c):
    tok = c.post("/api/token").get_json()["token"]
    c.post("/auth/logout")
    assert c.post("/api/health/connect", json=PAYLOAD).status_code == 401
    h = {"Authorization": f"Bearer {tok}"}
    r = c.post("/api/health/connect", json=PAYLOAD, headers=h).get_json()
    assert r["ok"] and r["received"]["steps"] == 2
    # resend with an edit (same ids) + a deletion: totals replace, don't double
    edited = {**PAYLOAD, "steps": [{**PAYLOAD["steps"][0], "count": 5000}], "deleted": [{"type": "steps", "id": "s2"}]}
    c.post("/api/health/connect", json=edited, headers=h)
    c.post("/auth/dev-login")
    days = {d["date"]: d for d in c.get(f"/api/areas/{fitness(c)['id']}/summary").get_json()["days"]}
    oct28, oct29 = days["2026-10-28"], days["2026-10-29"]
    assert oct28["steps"] == 5000                     # 4000 -> 5000 edit, s2 deleted
    assert oct28["avg_hr"] == 75 and oct28["resting_hr"] == 61
    assert oct29["sleep_minutes"] == 400               # 7h minus 20 min awake, counted on the wake-up day
    assert oct28["workout_minutes"] == 60              # one workout, not two, despite the resend
    assert c.get("/api/health/status").get_json()["days"]
