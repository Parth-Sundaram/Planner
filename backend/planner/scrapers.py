"""Pull deadlines from course schedule pages and turn them into tasks.

Each parser works on the page's visible text row by row, so cosmetic HTML changes
don't break it. CSSE 313's table is filled in by JavaScript, so it is rendered with a
headless browser (Playwright) before parsing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import requests
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Task

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_CODES = "MTWRFSU"

DEFAULT_COURSES = [
    {"code": "csse304", "name": "PLC", "url": "https://rhit-csse.github.io/csse304/schedule.html",
     "parser": "csse304", "render_js": False, "class_time": "10:00", "class_days": "MTRF"},
    {"code": "csse313", "name": "AI", "url": "https://www.rose-hulman.edu/class/cs/csse313/schedule/",
     "parser": "csse313", "render_js": True, "class_time": "10:00", "class_days": "MTRF"},
]


@dataclass
class Item:
    external_id: str
    title: str
    due: datetime
    course: str
    kind: str          # assignment | exam | summary
    est_minutes: int


def academic_year_for(month: int, today: date) -> int:
    """Fall-term pages omit the year. Aug-Dec belong to the fall's year, Jan-Jul to the next."""
    fall_year = today.year if today.month >= 8 else today.year - 1
    return fall_year if month >= 8 else fall_year + 1


def _clock(hhmm: str | None, ampm: str | None, default: time) -> time:
    if not hhmm:
        return default
    h, m = (int(x) for x in hhmm.split(":"))
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h != 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
    return time(h, m)


def fetch_html(url: str, render_js: bool = False, timeout: int = 30) -> str:
    if not render_js:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "planner/1.0"})
        r.raise_for_status()
        return r.text
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        page.wait_for_selector("table tr td", timeout=timeout * 1000)
        html = page.content()
        browser.close()
        return html


def _rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [" ".join(td.get_text(" ", strip=True).split()) for td in tr.find_all(["td", "th"])]
        if any(cells):
            rows.append(cells)
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _estimate(kind: str, title: str) -> int:
    t = title.lower()
    if kind == "exam":
        return 240
    if "quiz" in t:
        return 120
    if "summary" in t or kind == "summary":
        return 90
    return 180


# ---- CSSE 304 -------------------------------------------------------------------
# Due rows look like:  "Interpreter-SyntaxExpansion DUE Fri 16 Oct 2026 11:59 pm"
#                 or:  "Exam 2 in E104 DUE Tue, Oct 27, 2026, 11:59 pm"
DUE_DMY = re.compile(r"(?P<title>.+?)\s+DUE\s+(?:[A-Za-z]{3},?\s+)?(?P<d>\d{1,2})\s+(?P<mon>[A-Za-z]{3})[a-z]*,?\s+(?P<y>\d{4})"
                     r"(?:,?\s+(?P<hm>\d{1,2}:\d{2})\s*(?P<ap>am|pm)?)?", re.I)
DUE_MDY = re.compile(r"(?P<title>.+?)\s+DUE\s+(?:[A-Za-z]{3},?\s+)?(?P<mon>[A-Za-z]{3})[a-z]*\.?\s+(?P<d>\d{1,2}),?\s+(?P<y>\d{4})"
                     r"(?:,?\s+(?P<hm>\d{1,2}:\d{2})\s*(?P<ap>am|pm)?)?", re.I)


def parse_csse304(html: str, today: date, class_time: time = time(10)) -> list[Item]:
    items = []
    for cells in _rows(html):
        text = " ".join(c for c in cells if c)
        m = DUE_DMY.search(text) or DUE_MDY.search(text)
        if not m:
            continue
        mon = MONTHS.get(m.group("mon")[:3].lower())
        if not mon:
            continue
        title = m.group("title").strip(" -:|")
        is_exam = bool(re.search(r"\bexam\b", title, re.I))
        when = _clock(m.group("hm"), m.group("ap"), class_time if is_exam else time(23, 59))
        due = datetime.combine(date(int(m.group("y")), mon, int(m.group("d"))), when)
        kind = "exam" if is_exam else "assignment"
        items.append(Item(f"csse304:{_slug(title)}", ("Study: " if is_exam else "") + title,
                          due, "PLC", kind, _estimate(kind, title)))
    return items


# ---- CSSE 313 -------------------------------------------------------------------
# Week rows start with "7 | Oct. 19-23 | Day 24: ... | ..." and following day rows are
# "Day 25: ... | Wed. 23:59: CNN - Part 3/4". Day rows map, in order, onto the
# course's meeting days that fall inside the week's date range.
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
RANGE = re.compile(rf"^(?P<m1>{_MON})[a-z]*\.?\s*(?P<d1>\d{{1,2}})\s*-\s*(?:(?P<m2>{_MON})[a-z]*\.?\s*)?(?P<d2>\d{{1,2}})$")
DAYNUM = re.compile(r"\bDay\s+(\d+)\s*:")
ASSIGN = re.compile(r"(?:(?P<wd>Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?\s+)?(?P<when>BC|\d{1,2}:\d{2})\s*:\s*(?P<title>.+?)(?=(?:\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\.?)?\s+(?:BC|\d{1,2}:\d{2})\s*:|$)")


def _week_dates(m: re.Match, today: date) -> tuple[date, date]:
    m1 = MONTHS[m.group("m1").lower()]
    m2 = MONTHS[m.group("m2").lower()] if m.group("m2") else m1
    start = date(academic_year_for(m1, today), m1, int(m.group("d1")))
    end = date(academic_year_for(m2, today), m2, int(m.group("d2")))
    return start, end


def parse_csse313(html: str, today: date, class_time: time = time(10), class_days: str = "MTRF") -> list[Item]:
    items: list[Item] = []
    week: tuple[date, date] | None = None
    meeting_days: list[date] = []
    idx = 0
    for cells in _rows(html):
        text = " | ".join(cells)
        rng = next((m for c in cells if (m := RANGE.match(c))), None)
        if rng:
            # A new week begins on rows that carry the date range cell (e.g. "Oct. 19-23").
            week = _week_dates(rng, today)
            d, meeting_days = week[0], []
            while d <= week[1]:
                if DAY_CODES[d.weekday()] in class_days:
                    meeting_days.append(d)
                d += timedelta(days=1)
            idx = 0
        dm = DAYNUM.search(text)
        if not week or not dm:
            continue
        day_date = meeting_days[idx] if idx < len(meeting_days) else week[1]
        idx += 1

        topic_cell = next((c for c in cells if DAYNUM.search(c)), "")
        topic = DAYNUM.sub("", topic_cell).strip()
        if re.search(r"\b(quiz|exam)\s*\d*\b", topic, re.I):
            q = re.search(r"\b(?:quiz|exam)\s*\d*\b", topic, re.I).group(0).strip().title()
            items.append(Item(f"csse313:{_slug(q)}", f"Study: {q}", datetime.combine(day_date, class_time),
                              "AI", "exam", _estimate("exam" if "exam" in q.lower() else "quiz", q)))

        after = cells[cells.index(topic_cell) + 1:] if topic_cell in cells else []
        assign_text = " ".join(after).strip()
        for a in ASSIGN.finditer(assign_text):
            title = a.group("title").strip(" ,;")
            if not title:
                continue
            d = day_date
            if a.group("wd"):
                target = WEEKDAYS[a.group("wd").lower()]
                d = week[0] + timedelta(days=(target - week[0].weekday()) % 7)
            if a.group("when") == "BC":
                due = datetime.combine(d, class_time)
                kind = "summary" if "summary" in title.lower() else "assignment"
            else:
                h, mi = (int(x) for x in a.group("when").split(":"))
                due = datetime.combine(d, time(h, mi))
                kind = "assignment"
            items.append(Item(f"csse313:{_slug(title)}", title, due, "AI", kind, _estimate(kind, title)))
    return items


PARSERS = {"csse304": parse_csse304, "csse313": parse_csse313}


# ---- Syncing into the database ----------------------------------------------------

def sync_items(db: Session, items: list[Item], now: datetime) -> dict:
    """Insert new items, update dates/titles on existing ones, never touch the user's estimates."""
    created = updated = 0
    for it in items:
        if it.due < now:
            continue  # don't import the past
        t = db.scalar(select(Task).where(Task.external_id == it.external_id))
        if t is None:
            db.add(Task(title=it.title, course=it.course, due=it.due, est_minutes=it.est_minutes,
                        tags=it.course.lower(), source=f"scrape:{it.external_id.split(':')[0]}",
                        external_id=it.external_id, needs_estimate=True,
                        priority=1 if it.kind == "exam" else 2,
                        splittable=True))
            created += 1
        elif (t.due, t.title) != (it.due, it.title):
            t.due, t.title = it.due, it.title
            updated += 1
    db.commit()
    return {"created": created, "updated": updated}


def sync_courses(db: Session, courses: list[dict], now: datetime) -> dict:
    report = {}
    for c in courses:
        try:
            html = fetch_html(c["url"], c.get("render_js", False))
            parser = PARSERS[c["parser"]]
            h, m = (int(x) for x in c.get("class_time", "10:00").split(":"))
            kwargs = {"class_time": time(h, m)}
            if c["parser"] == "csse313":
                kwargs["class_days"] = c.get("class_days", "MTRF")
            items = parser(html, now.date(), **kwargs)
            report[c["code"]] = {"found": len(items), **sync_items(db, items, now)}
        except Exception as e:  # one broken site shouldn't stop the others
            report[c["code"]] = {"error": f"{type(e).__name__}: {e}"}
    return report


def load_courses(raw: str | None) -> list[dict]:
    return json.loads(raw) if raw else DEFAULT_COURSES
