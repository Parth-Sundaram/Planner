"""Weighted grade calculation for a course page.

Categories form a tree. A top-level category's weight is its share of the course (Exams 50%);
a subcategory's weight is its share of its parent (Homework -> Labs 20, Problem sets 10).
Weights are normalised among siblings, so they don't have to add to exactly 100.

A leaf category is scored by points: earned / possible over its graded items, after dropping
the lowest `drop_lowest`. An attendance category is scored from the attendance counter instead:
(present + excused + absences up to the free allowance) / meetings so far.

For each node we track, as fractions of that node:
  graded    how much of it has been graded so far
  earned    how much of it you've earned so far
  remaining how much is still ahead
"current" = earned / graded over the course; "needed" = the average you'd need on everything
still ahead (ungraded items, empty categories, future class meetings) to finish at the target.
"""
from __future__ import annotations


def _pct(item) -> float:
    return item.score / item.out_of if item.out_of else 0.0


def _leaf(c, attendance: dict | None) -> dict:
    if getattr(c, "is_attendance", False):
        a = attendance or {}
        present, absent, excused = a.get("present", 0), a.get("absent", 0), a.get("excused", 0)
        held = present + absent + excused
        if not held:
            return {"graded": 0.0, "earned": 0.0, "remaining": 1.0, "percent": None, "dropped": [], "attendance": a}
        counted_absent = max(0, absent - a.get("allowed", 0))
        pct = (held - counted_absent) / held
        expected = a.get("expected")
        done = min(1.0, held / expected) if expected else 1.0
        return {"graded": done, "earned": done * pct, "remaining": 1 - done, "percent": round(100 * pct, 2),
                "dropped": [], "attendance": a}
    items = list(getattr(c, "items", []) or [])
    graded = sorted((i for i in items if i.score is not None), key=_pct)
    dropped = graded[:c.drop_lowest] if c.drop_lowest and len(graded) > c.drop_lowest else []
    kept = [i for i in graded if i not in dropped]
    ungraded = [i for i in items if i.score is None]
    possible_kept = sum(i.out_of for i in kept)
    possible_left = sum(i.out_of for i in ungraded)
    pct = (sum(i.score for i in kept) / possible_kept) if possible_kept else None
    if possible_kept + possible_left == 0:
        return {"graded": 0.0, "earned": 0.0, "remaining": 1.0, "percent": None, "dropped": [i.id for i in dropped]}
    done = possible_kept / (possible_kept + possible_left)
    return {"graded": done if pct is not None else 0.0, "earned": done * (pct or 0), "remaining": 1 - done,
            "percent": None if pct is None else round(100 * pct, 2), "dropped": [i.id for i in dropped]}


def _node(c, children: dict, attendance, share_of_course: float) -> dict:
    kids = children.get(c.id, [])
    base = {"id": c.id, "name": c.name, "weight": c.weight, "drop_lowest": c.drop_lowest,
            "parent_id": getattr(c, "parent_id", None), "is_attendance": bool(getattr(c, "is_attendance", False)),
            "course_share": round(100 * share_of_course, 2),
            "items": [{"id": i.id, "name": i.name, "score": i.score, "out_of": i.out_of} for i in (getattr(c, "items", []) or [])]}
    if not kids:
        r = _leaf(c, attendance)
        return {**base, **r, "children": []}
    total = sum(k.weight for k in kids) or 0
    nodes, g, e, rem = [], 0.0, 0.0, 0.0
    for k in kids:
        w = (k.weight / total) if total else 1 / len(kids)
        n = _node(k, children, attendance, share_of_course * w)
        nodes.append(n)
        g, e, rem = g + w * n["graded"], e + w * n["earned"], rem + w * n["remaining"]
    return {**base, "graded": g, "earned": e, "remaining": rem,
            "percent": round(100 * e / g, 2) if g else None, "dropped": [], "children": nodes,
            "weights_total": total}


def letter_for(percent: float | None, scale: list[dict]) -> str | None:
    if percent is None or not scale:
        return None
    for row in sorted(scale, key=lambda r: -float(r["min"])):
        if percent >= float(row["min"]) - 1e-9:
            return row["letter"]
    return sorted(scale, key=lambda r: float(r["min"]))[0]["letter"]


def summarize(categories, target: float | None = None, attendance: dict | None = None,
              scale: list[dict] | None = None) -> dict:
    cats = sorted(categories, key=lambda c: (getattr(c, "position", 0), c.id))
    children: dict = {}
    for c in cats:
        children.setdefault(getattr(c, "parent_id", None), []).append(c)
    tops = children.get(None, [])
    total = sum(c.weight for c in tops) or 0
    nodes, g, e, rem = [], 0.0, 0.0, 0.0
    for c in tops:
        w = (c.weight / total) if total else 0
        n = _node(c, children, attendance, w)
        nodes.append(n)
        g, e, rem = g + w * n["graded"], e + w * n["earned"], rem + w * n["remaining"]
    current = round(100 * e / g, 2) if g else None
    needed = None
    if target is not None and rem > 1e-9:
        needed = round(100 * (target / 100 - e) / rem, 1)
    scale = scale or []
    return {"categories": nodes, "current": current, "weights_total": total,
            "graded_share": round(100 * g, 1), "target": target, "needed": needed,
            "locked_in": round(100 * e, 2), "letter": letter_for(current, scale), "scale": scale,
            "attendance": attendance}


def delete_categories(db, cats) -> None:
    """Delete categories bottom-up, flushing per level. Works whether or not the database
    cascades parent_id deletes (columns added by the in-place migration have no FK rule)."""
    left = {c.id: c for c in cats}
    while left:
        parents = {c.parent_id for c in left.values()}
        leaves = [c for cid, c in left.items() if cid not in parents]
        for c in leaves:
            db.delete(c)
            left.pop(c.id)
        db.flush()
