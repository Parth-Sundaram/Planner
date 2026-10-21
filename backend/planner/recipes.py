"""Reader recipes: the AI reads a course page once and writes down *how* to read it, as data
(CSS selectors and regexes), never as code. Later syncs run the recipe with no AI call.

A recipe looks like:
    {"rows": "table.schedule tr",              # one element per deadline (or per day)
     "title": {"selector": "td:nth-of-type(3)", "regex": "^(.*?)\\s+due"},
     "date":  {"selector": "td:nth-of-type(1)", "regex": null},
     "time":  {"selector": null, "regex": "(\\d{1,2}:\\d{2}\\s*[ap]m)"},
     "carry_date": true,                       # rows without a date use the last date seen
     "skip": "holiday|no class"}               # rows to ignore

It's checked against the AI's own answer before it's trusted, and dropped automatically if
the page changes so much that it stops finding things (then the AI reads the page again).
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, time

import requests
from bs4 import BeautifulSoup

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_M = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"

RECIPE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "rows": {"type": "STRING", "description": "CSS selector matching one element per deadline row"},
        "title": {"type": "OBJECT", "properties": {"selector": {"type": "STRING", "nullable": True},
                                                   "regex": {"type": "STRING", "nullable": True}}},
        "date": {"type": "OBJECT", "properties": {"selector": {"type": "STRING", "nullable": True},
                                                  "regex": {"type": "STRING", "nullable": True}}},
        "time": {"type": "OBJECT", "nullable": True, "properties": {"selector": {"type": "STRING", "nullable": True},
                                                                    "regex": {"type": "STRING", "nullable": True}}},
        "carry_date": {"type": "BOOLEAN"},
        "skip": {"type": "STRING", "nullable": True},
    },
    "required": ["rows", "title", "date", "carry_date"],
}


def parse_date_text(text: str, today: date) -> date | None:
    """Dates as course pages write them: 2026-10-27, 10/27(/26), Oct 27(, 2026), 27 Oct (2026), Tue Oct 27."""
    t = text.lower()
    if m := re.search(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", t):
        y, mo, d = map(int, m.groups())
    elif m := re.search(rf"\b{_M}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?\b", t):
        mo, d, y = MONTHS[m.group(1)[:3]], int(m.group(2)), int(m.group(3)) if m.group(3) else None
    elif m := re.search(rf"\b(\d{{1,2}})\s+{_M}(?:\s+(20\d\d))?\b", t):
        d, mo, y = int(m.group(1)), MONTHS[m.group(2)[:3]], int(m.group(3)) if m.group(3) else None
    elif m := re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t):
        mo, d = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else None
        if y and y < 100:
            y += 2000
    else:
        return None
    if y is None:  # academic year: Aug-Dec belong to the fall's year, Jan-Jul to the next
        fall = today.year if today.month >= 8 else today.year - 1
        y = fall if mo >= 8 else fall + 1
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_time_text(text: str) -> time | None:
    t = text.lower()
    if re.search(r"beginning of class|start of class|\bbc\b", t):
        return None  # caller uses class time
    if m := re.search(r"\b(\d{1,2})(?::(\d{2}))\s*(am|pm|a\.m\.|p\.m\.)?|\b(\d{1,2})\s*(am|pm)\b", t):
        h = int(m.group(1) or m.group(4))
        mi = int(m.group(2) or 0)
        ap = (m.group(3) or m.group(5) or "").replace(".", "")
        if ap == "pm" and h != 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        if h < 24 and mi < 60:
            return time(h, mi)
    if "midnight" in t:
        return time(23, 59)
    if "noon" in t:
        return time(12)
    return None


def _pick(row, spec: dict | None) -> str:
    if not spec:
        return ""
    el = row.select_one(spec["selector"]) if spec.get("selector") else row
    text = " ".join(el.get_text(" ", strip=True).split()) if el is not None else ""
    if spec.get("regex") and text:
        try:
            m = re.search(spec["regex"], text, re.I)
        except re.error:
            return ""
        text = (m.group(1) if m and m.groups() else m.group(0) if m else "").strip()
    return text


def run(recipe: dict, html: str, today: date) -> list[dict]:
    """Apply a recipe. Returns [{"title", "date", "time" or None, "text"}]."""
    soup = BeautifulSoup(html, "html.parser")
    out, last_date = [], None
    try:
        rows = soup.select(recipe["rows"])
    except Exception:
        return []
    skip = recipe.get("skip")
    for row in rows:
        text = " ".join(row.get_text(" ", strip=True).split())
        if not text or (skip and re.search(skip, text, re.I)):
            continue
        d = parse_date_text(_pick(row, recipe.get("date")) or "", today)
        if d:
            last_date = d
        elif recipe.get("carry_date"):
            d = last_date
        title = _pick(row, recipe.get("title")).strip(" -:|,;")
        if not d or not title or len(title) > 200:
            continue
        tm = parse_time_text(_pick(row, recipe.get("time")) or text)
        out.append({"title": title, "date": d, "time": tm, "text": text})
    return out


def simplify_html(html: str, limit: int = 45_000) -> str:
    """Strip a page down to structure + text so the AI can see selectors without the noise."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "img", "iframe", "link", "meta", "head"]):
        tag.decompose()
    for el in soup.find_all(True):
        el.attrs = {k: v for k, v in el.attrs.items() if k in ("class", "id")}
    body = soup.body or soup
    return re.sub(r"\s+", " ", str(body))[:limit]


def write_recipe(html: str, examples: list[dict]) -> dict:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY isn't set")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    sample = "\n".join(f"- {e['title']} | {e['date']}" for e in examples[:25])
    prompt = (
        "Below is the HTML of a course schedule page and the deadlines a reader found on it. Write a recipe that a "
        "program can apply with CSS selectors (BeautifulSoup .select) and Python regexes to find the same deadlines "
        "without you. 'rows' selects one element per deadline (or per day, if each day row holds one deadline). "
        "'title'/'date'/'time' say where inside a row to read each value: a CSS selector relative to the row (null = "
        "the whole row's text) and an optional regex whose group 1 is the value. Set carry_date if dates appear on "
        "a separate header row above the deadlines. 'skip' is a regex for rows to ignore (lecture-only rows, holidays). "
        "Prefer stable selectors (tag structure, nth-of-type) over long class chains.\n\n"
        f"DEADLINES FOUND:\n{sample}\n\nHTML:\n{simplify_html(html)}"
    )
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                      headers={"x-goog-api-key": key}, timeout=90, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": RECIPE_SCHEMA},
    })
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return json.loads("".join(p.get("text", "") for p in parts))


def _norm(s: str) -> str:
    s = re.sub(r"^study:\s*", "", s.lower())
    return re.sub(r"[^a-z0-9]+", "", s)


def agreement(recipe_rows: list[dict], expected: list[dict]) -> float:
    """Share of the AI's deadlines (title + date) that the recipe also finds, penalised if the
    recipe finds lots of extra rows."""
    if not expected:
        return 0.0
    got = {(_norm(r["title"]), r["date"]) for r in recipe_rows}
    got_titles = {t for t, _ in got}
    hits = 0
    for e in expected:
        n = _norm(e["title"])
        if (n, e["date"]) in got or any((n in t or t in n) and len(t) > 3 for t in got_titles if (t, e["date"]) in got):
            hits += 1
    score = hits / len(expected)
    if len(recipe_rows) > 1.5 * len(expected) + 2:
        score *= len(expected) / len(recipe_rows)
    return score
