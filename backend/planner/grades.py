"""Weighted grade calculation for a course page.

Each category (Homework 30%, Exams 50%...) is scored by points: earned / possible over its
graded items, after dropping the lowest `drop_lowest` items. The current grade only counts
categories that have something graded. "Needed" is the average you'd need on everything not
yet graded (including categories with no items listed) to finish at the target.
"""
from __future__ import annotations


def _pct(item) -> float:
    return item.score / item.out_of if item.out_of else 0.0


def summarize(categories, target: float | None = None) -> dict:
    total_w = sum(c.weight for c in categories) or 0
    cats, earned_w, graded_w, remaining_w = [], 0.0, 0.0, 0.0
    for c in categories:
        items = list(c.items)
        graded = sorted((i for i in items if i.score is not None), key=_pct)
        dropped = graded[:c.drop_lowest] if c.drop_lowest and len(graded) > c.drop_lowest else []
        kept = [i for i in graded if i not in dropped]
        ungraded = [i for i in items if i.score is None]
        possible_kept = sum(i.out_of for i in kept)
        possible_left = sum(i.out_of for i in ungraded)
        pct = (sum(i.score for i in kept) / possible_kept) if possible_kept else None
        share = c.weight / total_w if total_w else 0
        if possible_kept + possible_left == 0:
            remaining_w += share                       # nothing listed yet: all of it is ahead
        else:
            done_frac = possible_kept / (possible_kept + possible_left)
            if pct is not None:
                earned_w += share * done_frac * pct
                graded_w += share * done_frac
            remaining_w += share * (1 - done_frac)
        cats.append({"id": c.id, "name": c.name, "weight": c.weight, "drop_lowest": c.drop_lowest,
                     "percent": None if pct is None else round(100 * pct, 2),
                     "dropped": [i.id for i in dropped],
                     "items": [{"id": i.id, "name": i.name, "score": i.score, "out_of": i.out_of} for i in items]})
    current = round(100 * earned_w / graded_w, 2) if graded_w else None
    needed = None
    if target is not None and remaining_w > 0:
        needed = round(100 * (target / 100 - earned_w) / remaining_w, 1)
    return {"categories": cats, "current": current, "weights_total": total_w,
            "graded_share": round(100 * graded_w, 1), "target": target, "needed": needed,
            "locked_in": round(100 * earned_w, 2)}
