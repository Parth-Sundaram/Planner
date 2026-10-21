from datetime import datetime
from types import SimpleNamespace as N

import pytest

from planner import service, syllabus
from planner.app import create_app
from planner.grades import letter_for, summarize

NOW = datetime(2026, 10, 20, 14, 30)  # Tuesday, after a 10:00 class


def test_subcategory_weights_are_shares_of_the_parent():
    hw = N(id=1, name="Homework", weight=30, drop_lowest=0, parent_id=None, items=[])
    labs = N(id=2, name="Labs", weight=20, drop_lowest=0, parent_id=1, items=[N(id=21, name="L1", score=10, out_of=10)])
    ps = N(id=3, name="Problem sets", weight=10, drop_lowest=0, parent_id=1, items=[N(id=31, name="P1", score=5, out_of=10)])
    ex = N(id=4, name="Exams", weight=70, drop_lowest=0, parent_id=None, items=[N(id=41, name="E1", score=80, out_of=100),
                                                                               N(id=42, name="Final", score=None, out_of=100)])
    s = summarize([hw, labs, ps, ex], target=90)
    top = {c["name"]: c for c in s["categories"]}
    sub = {c["name"]: c for c in top["Homework"]["children"]}
    assert sub["Labs"]["course_share"] == 20.0 and sub["Problem sets"]["course_share"] == 10.0
    assert top["Homework"]["percent"] == round(100 * (2 / 3 * 1 + 1 / 3 * 0.5), 2)
    # everything graded in HW, half of exams
    g = 0.3 + 0.7 * 0.5
    e = 0.3 * (2 / 3 + 1 / 6) + 0.7 * 0.5 * 0.8
    assert s["current"] == round(100 * e / g, 2)
    assert s["needed"] == round(100 * (0.9 - e) / 0.35, 1)


def test_attendance_category_uses_counter_and_free_absences():
    att = N(id=1, name="Attendance", weight=10, drop_lowest=0, parent_id=None, is_attendance=True, items=[])
    rest = N(id=2, name="Work", weight=90, drop_lowest=0, parent_id=None, items=[N(id=9, name="x", score=90, out_of=100)])
    s = summarize([att, rest], attendance={"present": 18, "absent": 3, "excused": 1, "allowed": 2, "expected": 40})
    a = s["categories"][0]
    assert a["percent"] == round(100 * 21 / 22, 2)          # 1 absence counts after 2 free ones
    assert round(a["graded"], 3) == round(22 / 40, 3)       # 22 of 40 meetings held
    empty = summarize([att], attendance={"present": 0, "absent": 0, "excused": 0})
    assert empty["current"] is None


def test_letter_scale():
    scale = [{"letter": "A", "min": 90}, {"letter": "B+", "min": 87}, {"letter": "B", "min": 80}, {"letter": "F", "min": 0}]
    assert (letter_for(91, scale), letter_for(87, scale), letter_for(86.9, scale), letter_for(12, scale)) == ("A", "B+", "B", "F")


PROPOSAL = {
    "categories": [
        {"name": "Homework", "weight": 30, "is_attendance": False, "drop_lowest": 1, "subcategories": [
            {"name": "Labs", "weight": 66.7, "is_attendance": False, "count": 3, "points_each": 10},
            {"name": "Problem sets", "weight": 33.3, "is_attendance": False}]},
        {"name": "Exams", "weight": 60, "is_attendance": False, "item_names": ["Exam 1", "Exam 2", "Final"]},
        {"name": "Attendance", "weight": 10, "is_attendance": True},
    ],
    "grade_scale": [{"letter": "A", "min": 90}, {"letter": "B", "min": 80}, {"letter": "C", "min": 70}],
    "attendance": {"free_absences": 2, "class_meetings": 40, "policy": "Each absence past 2 costs 1% of attendance"},
    "policies": "Late work: -10%/day",
}


@pytest.fixture
def c(monkeypatch):
    monkeypatch.setattr(service, "local_now", lambda db: NOW)
    cl = create_app("sqlite://", testing=True).test_client()
    cl.post("/auth/dev-login")
    return cl


def plc(c):
    return next(x for x in c.get("/api/courses").get_json()["courses"] if x["name"] == "PLC")


def test_syllabus_read_apply_and_attendance(c, monkeypatch):
    seen = {}
    def fake_read(file_bytes=None, mime=None, text=None):
        seen.update(file=file_bytes, mime=mime, text=text)
        return {**PROPOSAL, "check": syllabus.check(PROPOSAL)}
    monkeypatch.setattr(syllabus, "read", fake_read)
    course = plc(c)
    import io
    r = c.post(f"/api/courses/{course['id']}/syllabus/read",
               data={"file": (io.BytesIO(b"%PDF-1.4 fake"), "syllabus.pdf", "application/pdf")}, content_type="multipart/form-data")
    prop = r.get_json()
    assert seen["mime"] == "application/pdf" and prop["check"] == []
    assert c.post(f"/api/courses/{course['id']}/syllabus/read", json={}).status_code == 400
    g = c.post(f"/api/courses/{course['id']}/syllabus/apply", json={"proposal": prop}).get_json()
    names = [x["name"] for x in g["categories"]]
    assert names == ["Homework", "Exams", "Attendance"]
    hw = g["categories"][0]
    assert [x["name"] for x in hw["children"]] == ["Labs", "Problem sets"]
    assert [i["name"] for i in hw["children"][0]["items"]] == ["Lab 1", "Lab 2", "Lab 3"]
    assert [i["name"] for i in g["categories"][1]["items"]] == ["Exam 1", "Exam 2", "Final"]
    d = c.get(f"/api/courses/{course['id']}").get_json()["course"]
    assert (d["attend_allowed"], d["attend_expected"]) == (2, 40) and d["grade_scale"][0]["letter"] == "A"
    assert "Late work" in d["grade_policies"]
    # attendance counter
    for st in ["present"] * 5 + ["absent"] * 3:
        r = c.post(f"/api/courses/{course['id']}/attendance", json={"status": st}).get_json()
    assert (r["attendance"]["present"], r["attendance"]["absent"], r["attendance"]["free_left"]) == (5, 3, 0)
    att = next(x for x in r["grades"]["categories"] if x["is_attendance"])
    assert att["percent"] == round(100 * 7 / 8, 2)
    r = c.post(f"/api/courses/{course['id']}/attendance", json={"undo": "absent"}).get_json()
    assert r["attendance"]["absent"] == 2
    # re-applying replaces, doesn't duplicate
    g = c.post(f"/api/courses/{course['id']}/syllabus/apply", json={"proposal": prop}).get_json()
    assert len(g["categories"]) == 3


def test_today_asks_about_attendance_once_class_has_started(c):
    course = plc(c)  # PLC meets MTRF at 10:00 by default
    c.post(f"/api/courses/{course['id']}/grade-categories", json={"name": "Attendance", "weight": 5, "is_attendance": True})
    t = c.get("/api/today").get_json()
    assert t["attendance"] == [{"course_id": course["id"], "name": "PLC", "status": None}]
    c.post(f"/api/courses/{course['id']}/attendance", json={"status": "present", "once": True})
    c.post(f"/api/courses/{course['id']}/attendance", json={"status": "excused", "once": True})
    t = c.get("/api/today").get_json()
    assert t["attendance"][0]["status"] == "excused"
    a = c.get(f"/api/courses/{course['id']}").get_json()["grades"]["attendance"]
    assert (a["present"], a["excused"]) == (0, 1)


def test_subcategory_api(c):
    course = plc(c)
    g = c.post(f"/api/courses/{course['id']}/grade-categories", json={"name": "Homework", "weight": 40}).get_json()
    hw = g["categories"][0]["id"]
    g = c.post(f"/api/courses/{course['id']}/grade-categories", json={"name": "Labs", "weight": 50, "parent_id": hw}).get_json()
    assert g["categories"][0]["children"][0]["name"] == "Labs"
    assert c.post(f"/api/courses/{course['id']}/grade-categories", json={"name": "x", "parent_id": 9999}).status_code == 400
