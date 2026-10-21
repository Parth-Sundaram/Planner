"""Read a syllabus (PDF, pasted text or a web page) and propose the course's grading setup:
weighted categories with subcategories, drop rules, how many of each item there are,
the attendance policy and the letter scale. Nothing is saved until you apply the proposal.
"""
from __future__ import annotations

import base64
import json
import os

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Course, GradeCategory, GradeItem

_CAT = {
    "type": "OBJECT",
    "properties": {
        "name": {"type": "STRING"},
        "weight": {"type": "NUMBER", "description": "percent of the parent (top level: percent of the course grade)"},
        "drop_lowest": {"type": "INTEGER"},
        "count": {"type": "INTEGER", "nullable": True, "description": "how many items, if the syllabus says"},
        "points_each": {"type": "NUMBER", "nullable": True},
        "item_names": {"type": "ARRAY", "items": {"type": "STRING"}, "nullable": True},
        "is_attendance": {"type": "BOOLEAN"},
    },
    "required": ["name", "weight", "is_attendance"],
}
SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "categories": {"type": "ARRAY", "items": {
            **_CAT, "properties": {**_CAT["properties"], "subcategories": {"type": "ARRAY", "items": _CAT}}}},
        "grade_scale": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "letter": {"type": "STRING"}, "min": {"type": "NUMBER"}}, "required": ["letter", "min"]}},
        "attendance": {"type": "OBJECT", "nullable": True, "properties": {
            "free_absences": {"type": "INTEGER"}, "class_meetings": {"type": "INTEGER", "nullable": True},
            "policy": {"type": "STRING"}}},
        "policies": {"type": "STRING", "description": "late work, rounding, curves, extra credit: short bullet lines"},
        "notes": {"type": "STRING", "description": "anything ambiguous the student should double-check"},
    },
    "required": ["categories", "grade_scale"],
}

PROMPT = """Read this course syllabus and extract how the final grade is computed.

- categories: every graded component with its weight as a percent of the course. If a component is
  split further ("Homework 30%: labs 20%, problem sets 10%"), put the parts in subcategories with
  their weight as a percent of the PARENT (labs 66.7, problem sets 33.3) - convert if the syllabus
  gives them as percent of the course.
- drop_lowest: how many lowest scores are dropped in that category (0 if none).
- count / points_each / item_names: fill in only when stated (e.g. "8 labs", "Exam 1, Exam 2, Final").
- is_attendance: true for attendance or participation that is counted by showing up.
- grade_scale: letter cutoffs as minimum percents (A 90, B+ 87, ...). Empty if not stated.
- attendance: free absences allowed and the policy in one sentence, if any.
Use the syllabus's own names. Don't invent components; if weights don't add to 100, keep them as
written and say so in notes."""


def read(file_bytes: bytes | None = None, mime: str | None = None, text: str | None = None) -> dict:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Reading a syllabus needs GEMINI_API_KEY (free, from Google AI Studio) set in Render.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    parts = [{"text": PROMPT}]
    if file_bytes:
        parts.append({"inline_data": {"mime_type": mime or "application/pdf", "data": base64.b64encode(file_bytes).decode()}})
    if text:
        parts.append({"text": "SYLLABUS:\n" + text[:120_000]})
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"x-goog-api-key": key}, timeout=120, json={
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": SCHEMA},
    })
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
    out = json.loads("".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"]))
    out["check"] = check(out)
    return out


def check(p: dict) -> list[str]:
    """Plain warnings shown next to the proposal before you apply it."""
    msgs = []
    total = sum(float(c.get("weight") or 0) for c in p.get("categories", []))
    if p.get("categories") and abs(total - 100) > 0.5:
        msgs.append(f"Top-level weights add up to {round(total, 1)}%, not 100%. They'll be scaled, but check the syllabus.")
    for c in p.get("categories", []):
        subs = c.get("subcategories") or []
        st = sum(float(s.get("weight") or 0) for s in subs)
        if subs and abs(st - 100) > 0.5:
            msgs.append(f"{c['name']}: its parts add up to {round(st, 1)}% of it. They'll be scaled.")
    return msgs


def apply(db: Session, course: Course, p: dict, replace: bool = True) -> None:
    if replace:
        # Delete every category explicitly (children first): on databases migrated in place the
        # parent_id column has no ON DELETE CASCADE, so we can't rely on the database for it.
        from .grades import delete_categories
        delete_categories(db, list(db.scalars(select(GradeCategory).where(GradeCategory.course_id == course.id))))

    def add(c: dict, parent_id, pos):
        cat = GradeCategory(course_id=course.id, name=str(c.get("name") or "Category")[:100],
                            weight=float(c.get("weight") or 0), drop_lowest=int(c.get("drop_lowest") or 0),
                            parent_id=parent_id, position=pos, is_attendance=bool(c.get("is_attendance")))
        db.add(cat)
        db.flush()
        subs = c.get("subcategories") or []
        for i, s in enumerate(subs):
            add(s, cat.id, i)
        if not subs and not cat.is_attendance:
            names = [n for n in (c.get("item_names") or []) if n] or \
                [f"{cat.name.rstrip('s')} {i + 1}" for i in range(min(int(c.get("count") or 0), 40))]
            each = float(c.get("points_each") or 100)
            for n in names:
                db.add(GradeItem(category_id=cat.id, name=str(n)[:200], out_of=each))

    for i, c in enumerate(p.get("categories", [])):
        add(c, None, i)
    if p.get("grade_scale"):
        course.grade_scale = json.dumps([{"letter": s["letter"], "min": float(s["min"])} for s in p["grade_scale"]])
    att = p.get("attendance") or {}
    if att:
        course.attend_allowed = int(att.get("free_absences") or 0)
        if att.get("class_meetings"):
            course.attend_expected = int(att["class_meetings"])
    pol = "\n".join(x for x in (p.get("policies"), att.get("policy")) if x)
    if pol:
        course.grade_policies = pol
    db.commit()
