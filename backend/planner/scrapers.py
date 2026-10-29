"""Pull deadlines from course schedule pages and turn them into tasks.

Built-in parsers (CSSE 304, CSSE 313) work on the page's visible text row by row, so
cosmetic HTML changes don't break them. Any other page goes through the "ai" parser,
which sends the page text to Gemini's free tier and gets back a typed list of deadlines.
Pages filled in by JavaScript are rendered with a headless browser (Playwright) first.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import requests

from . import gemini
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Course, Setting, Task

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
DAY_CODES = "MTWRFSU"

DEFAULT_COURSES = [
    {"code": "csse304", "name": "PLC", "url": "https://rhit-csse.github.io/csse304/schedule.html",
     "parser": "csse304", "render_js": False, "class_time": "10:00", "class_days": "MTRF", "review": False,
     "color": "purple"},
    {"code": "csse313", "name": "AI", "url": "https://www.rose-hulman.edu/class/cs/csse313/schedule/",
     "parser": "csse313", "render_js": True, "class_time": "10:00", "class_days": "MTRF", "review": False,
     "color": "teal"},
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


def fetch_html(url: str, render_js: bool = False, timeout: int = 30, wait_for: str | None = "table tr td") -> str:
    if not render_js:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "planner/1.0"})
        r.raise_for_status()
        return r.text
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        # Small-memory settings so this fits on a free 512 MB instance.
        browser = p.chromium.launch(args=["--disable-dev-shm-usage", "--disable-gpu", "--no-zygote",
                                          "--disable-extensions", "--js-flags=--max-old-space-size=128"])
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort()
                       if route.request.resource_type in ("image", "media", "font") else route.continue_())
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            if wait_for:
                page.wait_for_selector(wait_for, timeout=timeout * 1000)
            return page.content()
        finally:
            browser.close()


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


MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def _segments(html: str) -> list[str]:
    """Short pieces of visible text: table cells and lines, with markdown links reduced to their
    text. Works whether the page is a rendered HTML table or raw markdown shown as text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    pieces: list[str] = []
    for cell in soup.find_all(["td", "th", "li", "p"]):
        if not cell.find(["td", "th", "li", "p", "table"]):
            pieces.append(" ".join(cell.get_text(" ", strip=True).split()))
    for line in MD_LINK.sub(r"\1", soup.get_text("\n")).splitlines():
        for part in line.split("|"):
            part = " ".join(part.split())
            if not part:
                continue
            if part.upper().startswith("DUE ") and pieces:
                pieces[-1] = f"{pieces[-1]} {part}"  # "Name" / "DUE Fri ..." split across lines
            else:
                pieces.append(part)
    return [MD_LINK.sub(r"\1", p) for p in pieces if p]


def parse_csse304(html: str, today: date, class_time: time = time(10)) -> list[Item]:
    items, seen = [], set()
    for text in _segments(html):
        for m in list(DUE_DMY.finditer(text)) or list(DUE_MDY.finditer(text)):
            mon = MONTHS.get(m.group("mon")[:3].lower())
            if not mon:
                continue
            title = m.group("title").strip(" -:|,;")
            title = re.split(r"\s{2,}|\s[-–]\s", title)[-1].strip() or title
            is_exam = bool(re.search(r"\bexam\b", title, re.I))
            when = _clock(m.group("hm"), m.group("ap"), class_time if is_exam else time(23, 59))
            due = datetime.combine(date(int(m.group("y")), mon, int(m.group("d"))), when)
            kind = "exam" if is_exam else "assignment"
            ext = f"csse304:{_slug(title)}"
            if ext in seen:
                continue
            seen.add(ext)
            items.append(Item(ext, ("Study: " if is_exam else "") + title, due, "PLC", kind, _estimate(kind, title)))
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


# ---- Any other page: Gemini extraction -----------------------------------------------
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
AI_KINDS = ["assignment", "quiz", "exam", "project", "reading", "presentation", "other"]
AI_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "kind": {"type": "STRING", "enum": AI_KINDS},
            "due_date": {"type": "STRING", "description": "YYYY-MM-DD"},
            "due_time": {"type": "STRING", "nullable": True, "description": "HH:MM, 24-hour, or null if not stated"},
        },
        "required": ["title", "kind", "due_date"],
    },
}


def page_text(html: str, limit: int = 60_000) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = [" ".join(l.split()) for l in soup.get_text("\n").splitlines()]
    return "\n".join(l for l in lines if l)[:limit]


def ask_gemini(prompt: str) -> list[dict]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY isn't set. Add a free key from Google AI Studio in Render's environment.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    r = gemini.post(model, key, timeout=90, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseSchema": AI_SCHEMA},
    })
    if r.status_code != 200:
        msg = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:200]
        raise RuntimeError(f"Gemini {r.status_code}: {msg}")
    parts = r.json()["candidates"][0]["content"]["parts"]
    return json.loads("".join(p.get("text", "") for p in parts))


def parse_ai(html: str, today: date, class_time: time = time(10), class_days: str = "MTRF",
             code: str = "course", course: str = "Course", text: str | None = None) -> list[Item]:
    text = text if text is not None else page_text(html)
    days = ", ".join(n for c, n in zip(DAY_CODES, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) if c in class_days)
    prompt = (
        f"This is the schedule page for the college course \"{course}\". Today is {today.isoformat()} "
        f"(term dates may omit the year; pick the year that puts them in the current academic term). "
        f"The class meets {days} at {class_time.strftime('%H:%M')}.\n"
        "List every deliverable with a deadline: homework, assignments, labs, projects and their milestones, "
        "quizzes, exams, presentations and required readings. For each give a short title as the page names it, "
        "its kind, the due date, and the due time if the page states one (\"beginning of class\" means the class "
        "time; otherwise null). If a row only says 'Day N' or a week, map it onto the class meeting days. "
        "Skip lecture topics with nothing due, holidays, and office hours. Don't invent items.\n\n"
        f"PAGE:\n{text}"
    )
    return normalize_raw(ask_gemini(prompt), code=code, course=course, class_time=class_time)


def normalize_raw(raws: list[dict], code: str, course: str, class_time: time) -> list[Item]:
    """Shared clean-up for AI answers and recipe rows: dates, default times, study tasks, ids."""
    items: list[Item] = []
    seen: dict[str, int] = {}
    for raw in raws:
        try:
            d = date.fromisoformat(str(raw["due_date"])[:10])
        except (KeyError, ValueError):
            continue
        title = " ".join(str(raw.get("title", "")).split())[:200]
        if not title:
            continue
        kind = raw.get("kind") if raw.get("kind") in AI_KINDS else "other"
        when = None
        if raw.get("due_time"):
            try:
                h, m = (int(x) for x in str(raw["due_time"])[:5].split(":"))
                when = time(h, m)
            except ValueError:
                pass
        if kind in ("exam", "quiz"):
            when = when or class_time
            title = title if title.lower().startswith("study") else f"Study: {title}"
        when = when or time(23, 59)
        slug = _slug(title)
        seen[slug] = seen.get(slug, 0) + 1
        ext = f"{code}:{slug}" if seen[slug] == 1 else f"{code}:{slug}:{d.isoformat()}"
        est_kind = "exam" if kind == "exam" else "summary" if kind == "reading" else "assignment"
        items.append(Item(ext, title, datetime.combine(d, when), course, est_kind,
                          120 if kind == "quiz" else 60 if kind == "presentation" else _estimate(est_kind, title)))
    return items


def _guess_kind(title: str) -> str:
    t = title.lower()
    for kind, rx in (("exam", r"\bexam|midterm|final\b"), ("quiz", r"\bquiz"), ("presentation", r"present"),
                     ("project", r"project|milestone"), ("reading", r"\bread")):
        if re.search(rx, t):
            return kind
    return "assignment"


def recipe_items(course: Course, html: str, today: date) -> list[Item]:
    from . import recipes
    rows = recipes.run(json.loads(course.recipe), html, today)
    h, m = (int(x) for x in (course.class_time or "10:00").split(":"))
    raws = [{"title": r["title"], "kind": _guess_kind(r["title"]), "due_date": r["date"].isoformat(),
             "due_time": r["time"].strftime("%H:%M") if r["time"] else None} for r in rows]
    return normalize_raw(raws, course.code, course.name, time(h, m))


def learn_recipe(course: Course, html: str, items: list[Item], today: date) -> str:
    """After the AI reads a page, ask it for a recipe and keep it only if it reproduces the answer."""
    from . import recipes
    if len(items) < 2:
        return "too few items to learn from"
    examples = [{"title": i.title.removeprefix("Study: "), "date": i.due.date()} for i in items]
    try:
        recipe = recipes.write_recipe(html, examples)
    except Exception as e:
        return f"couldn't write one ({type(e).__name__})"
    rows = recipes.run(recipe, html, today)
    score = recipes.agreement(rows, examples)
    if score >= 0.8:
        course.recipe = json.dumps(recipe)
        return f"saved (matches {round(100 * score)}%)"
    return f"not reliable enough ({round(100 * score)}% match), so the AI keeps reading this page"


PARSERS = {"csse304": parse_csse304, "csse313": parse_csse313, "ai": parse_ai}
PARSER_LABELS = {"ai": "AI extraction (Gemini)", "csse304": "Built-in: CSSE 304 page",
                 "csse313": "Built-in: CSSE 313 page", "none": "No schedule page"}
COLORS = ["blue", "green", "purple", "orange", "pink", "teal", "yellow", "red", "gray"]


# ---- Syncing into the database ----------------------------------------------------

def sync_items(db: Session, items: list[Item], now: datetime, review: bool = False, tag: str | None = None,
               course_id: int | None = None) -> dict:
    """Insert new items, update dates/titles on existing ones, never touch the user's estimates.
    With `review`, new items arrive as suggestions to accept or dismiss."""
    created = updated = 0
    seen: set[str] = set()
    for it in items:
        if it.due < now or it.external_id in seen:
            continue  # don't import the past; ignore duplicates within one page
        seen.add(it.external_id)
        t = db.scalar(select(Task).where(Task.external_id == it.external_id))
        if t is None:
            db.add(Task(title=it.title, course=it.course, due=it.due, est_minutes=it.est_minutes,
                        tags=tag if tag is not None else it.course.lower(),
                        source=f"scrape:{it.external_id.split(':')[0]}",
                        external_id=it.external_id, needs_estimate=True,
                        priority=1 if it.kind == "exam" else 2,
                        splittable=True, status="suggested" if review else "open", course_id=course_id))
            created += 1
        else:
            if course_id and t.course_id is None:
                t.course_id = course_id
            if (t.due, t.title) != (it.due, it.title):
                t.due, t.title = it.due, it.title
                updated += 1
    db.commit()
    return {"created": 0 if review else created, "suggested": created if review else 0, "updated": updated}


def get_courses(db: Session, enabled_only: bool = False) -> list[Course]:
    """Courses live in their own table. The first time, seed it from the old JSON setting or the defaults."""
    if db.get(Setting, "courses_seeded") is None:
        old = db.get(Setting, "courses")
        for c in (json.loads(old.value) if old and old.value else DEFAULT_COURSES):
            if not db.scalar(select(Course).where(Course.code == c["code"])):
                db.add(Course(code=c["code"], name=c["name"], url=c["url"], parser=c.get("parser", "ai"),
                              render_js=c.get("render_js", False), class_days=c.get("class_days", "MTRF"),
                              class_time=c.get("class_time", "10:00"), review=c.get("review", False),
                              color=c.get("color", "blue")))
        db.add(Setting(key="courses_seeded", value="1"))
        db.commit()
    if db.get(Setting, "areas_seeded") is None:
        # Areas beyond courses, so quick-add has somewhere to file "timesheet", "rent", "gym"...
        have = {c.name.lower() for c in db.scalars(select(Course))}
        for name, kind, color in (("Career", "career", "orange"), ("Fitness", "fitness", "green"), ("Finance", "finance", "yellow")):
            if name.lower() not in have:
                db.add(Course(code=kind, name=name, kind=kind, url="", parser="none", color=color,
                              review=True, links="[]", notes="", instructor="", keywords=""))
        db.add(Setting(key="areas_seeded", value="1"))
        db.commit()
    if db.get(Setting, "personal_seeded") is None:
        if not db.scalar(select(Course).where(Course.kind == "personal")):
            db.add(Course(code="personal", name="Personal", kind="personal", url="", parser="none", color="pink",
                          review=True, links="[]", notes="", instructor="", keywords=""))
        db.add(Setting(key="personal_seeded", value="1"))
        db.commit()
    link_tasks_to_courses(db)
    q = select(Course).order_by(Course.id)
    if enabled_only:
        q = q.where(Course.enabled.is_(True))
    return list(db.scalars(q))


def link_tasks_to_courses(db: Session) -> None:
    """Every course name used on a task gets a course page, and tasks point at it."""
    loose = list(db.scalars(select(Task).where(Task.course_id.is_(None), Task.course.is_not(None), Task.course != "")))
    if not loose:
        return
    by_name = {c.name.lower(): c for c in db.scalars(select(Course))}
    used = [c.color for c in by_name.values()]
    for t in loose:
        c = by_name.get(t.course.strip().lower())
        if c is None:
            code, n = _slug(t.course) or "course", 2
            while db.scalar(select(Course).where(Course.code == code)):
                code, n = f"{_slug(t.course)}-{n}", n + 1
            color = next((x for x in COLORS if x not in used), COLORS[len(used) % len(COLORS)])
            used.append(color)
            c = Course(code=code, name=t.course.strip(), url="", parser="none", review=True, color=color)
            db.add(c)
            db.flush()
            by_name[c.name.lower()] = c
        t.course_id = c.id
    db.commit()


def extract(course: Course, today: date, html: str | None = None, use_recipe: bool = True) -> tuple[list[Item], str, str]:
    """Fetch and parse one course page. Returns the items, a hash of the page text, and how it was read."""
    if html is None:
        html = fetch_html(course.url, course.render_js,
                          wait_for="table tr td" if course.parser == "csse313" else None)
    h, m = (int(x) for x in (course.class_time or "10:00").split(":"))
    text = page_text(html)
    digest = hashlib.sha256(text.encode()).hexdigest()
    if course.parser == "ai" and use_recipe and course.recipe:
        items = recipe_items(course, html, today)
        prev = json.loads(course.last_result or "{}").get("found") or 0
        if items and len(items) >= 0.5 * prev:
            return items, digest, "recipe"
        course.recipe = None  # the page changed shape: read it with the AI and learn again
    kwargs = {"class_time": time(h, m)}
    if course.parser in ("csse313", "ai"):
        kwargs["class_days"] = course.class_days or "MTRF"
    if course.parser == "ai":
        kwargs.update(code=course.code, course=course.name, text=text)
    items = PARSERS[course.parser](html, today, **kwargs)
    if course.parser != "ai":  # built-in parsers name their own course; use the user's name for it
        items = [Item(i.external_id, i.title, i.due, course.name, i.kind, i.est_minutes) for i in items]
        return items, digest, "built-in"
    return items, digest, "ai"


def sync_course(db: Session, course: Course, now: datetime, force: bool = False) -> dict:
    try:
        html = fetch_html(course.url, course.render_js,
                          wait_for="table tr td" if course.parser == "csse313" else None)
        digest = hashlib.sha256(page_text(html).encode()).hexdigest()
        prev = json.loads(course.last_result or "{}")
        if course.parser == "ai" and not force and digest == course.page_hash and "error" not in prev:
            # Nothing changed on the page: no AI call, nothing to add.
            result = {"found": prev.get("found", 0), "created": 0, "suggested": 0, "updated": 0, "unchanged": True,
                      "read_with": prev.get("read_with")}
            course.last_sync_at = now
            course.last_result = json.dumps(result)
            db.commit()
            return result
        items, digest, how = extract(course, now.date(), html)
        result = {"found": len(items), **sync_items(db, items, now, review=course.review,
                                                    tag=_slug(course.name), course_id=course.id), "read_with": how}
        if how == "ai":
            result["recipe"] = learn_recipe(course, html, items, now.date())
        course.page_hash = digest
    except Exception as e:  # one broken site shouldn't stop the others
        db.rollback()
        result = {"error": f"{type(e).__name__}: {e}"[:500]}
    course.last_sync_at = now
    course.last_result = json.dumps(result)
    db.commit()
    return result


def sync_courses(db: Session, now: datetime) -> dict:
    return {c.name: sync_course(db, c, now) for c in get_courses(db, enabled_only=True)
            if c.parser != "none" and c.url}
