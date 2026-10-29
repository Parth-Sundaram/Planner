"""Moodle: assignments, quizzes and grades, so nothing lives only on Moodle.

Two ways in (either or both):

1. Calendar link (easiest, no login): Moodle -> Calendar -> Export calendar -> "All courses",
   "Recent and next 60 days" -> Get calendar URL. Gives due dates for everything that has one.
2. Web service key (more complete): base URL + a mobile-app token. Adds assignments and quizzes
   that never appear on the calendar, your grades, and it notices when Moodle says something is
   already done (it disappears from Moodle's timeline). When a key is set, the calendar link is
   only used as a fallback.

Moodle courses are matched to planner courses by a mapping you set once (Settings -> Moodle);
items from unmapped Moodle courses arrive as suggestions so they aren't silently dropped.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Course, GradeCategory, GradeItem, Setting, Task

TIMEOUT = 30


# ---- settings ---------------------------------------------------------------------------

def _get(db: Session, key: str, default=None):
    row = db.get(Setting, key)
    return row.value if row and row.value not in (None, "") else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))


def clean_token(raw: str) -> str:
    """Accept a plain key or what the mobile-app launch hands back (base64 of 'siteid:::token:::...')."""
    import base64
    t = (raw or "").strip()
    t = t.split("token=", 1)[-1].strip()
    if re.fullmatch(r"[0-9a-f]{32}", t):
        return t
    try:
        decoded = base64.b64decode(t + "=" * (-len(t) % 4)).decode("utf-8", "ignore")
        parts = decoded.split(":::")
        if len(parts) >= 2 and re.fullmatch(r"[0-9a-f]{32}", parts[1]):
            return parts[1]
    except Exception:
        pass
    return t


def config(db: Session) -> dict:
    return {"ical_url": _get(db, "moodle_ical", ""), "base_url": (_get(db, "moodle_url", "") or "").rstrip("/"),
            "token": _get(db, "moodle_token", ""), "map": json.loads(_get(db, "moodle_map", "{}")),
            "seen": json.loads(_get(db, "moodle_seen", "[]")), "last": json.loads(_get(db, "moodle_last", "{}"))}


def configured(db: Session) -> bool:
    c = config(db)
    return bool(c["ical_url"] or (c["base_url"] and c["token"]))


def _tz(db: Session) -> ZoneInfo:
    return ZoneInfo(_get(db, "timezone", "America/New_York"))


def _local(ts: int | float, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz).replace(tzinfo=None, second=0, microsecond=0)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def guess_course(name: str, courses: list[Course]) -> Course | None:
    """'CSSE304-01 Programming Language Concepts' -> the planner course whose code, schedule URL
    or name appears in it."""
    n = _norm(name)
    for c in courses:
        if (c.kind or "course") != "course":
            continue
        keys = {_norm(c.code)} | set(re.findall(r"[a-z]{2,4}\d{3}", (c.url or "").lower()))
        if any(k and len(k) >= 5 and k in n for k in keys):
            return c
    for c in courses:
        if (c.kind or "course") == "course" and len(_norm(c.name)) >= 4 and _norm(c.name) in n:
            return c
    return None


def _course_for(db: Session, moodle_name: str, cfg: dict, courses: list[Course]) -> Course | None:
    cid = cfg["map"].get(moodle_name)
    if cid == "ignore":
        return None
    if cid:
        return db.get(Course, int(cid))
    return guess_course(moodle_name, courses)


# ---- calendar link (iCal) -----------------------------------------------------------------

def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _ical_value(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\").strip()


def _ical_time(name: str, value: str, tz: ZoneInfo) -> datetime | None:
    m = re.match(r"(\d{8})(?:T(\d{6})(Z)?)?$", value.strip())
    if not m:
        return None
    if not m.group(2):  # all-day
        return datetime.strptime(m.group(1), "%Y%m%d").replace(hour=23, minute=59)
    dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    if m.group(3):
        return dt.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None, second=0)
    tzid = re.search(r"TZID=([^;:]+)", name)
    if tzid:
        try:
            return dt.replace(tzinfo=ZoneInfo(tzid.group(1))).astimezone(tz).replace(tzinfo=None, second=0)
        except Exception:
            pass
    return dt.replace(second=0)


def parse_ical(text: str, tz: ZoneInfo) -> list[dict]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            name, value = line.split(":", 1)
            key = name.split(";")[0].upper()
            if key in ("DTSTART", "DTEND"):
                cur[key.lower()] = _ical_time(name, value, tz)
            elif key in ("SUMMARY", "UID", "CATEGORIES", "DESCRIPTION"):
                cur[key.lower()] = _ical_value(value)
    return events


SKIP = re.compile(r"\b(opens|is open|starts|available from)\b", re.I)
TAIL = re.compile(r"\s*(?:\(|-|:)?\s*(?:is due|is due to be graded|due|closes|should be completed|deadline)\)?\s*$", re.I)


def clean_title(summary: str) -> str:
    t = TAIL.sub("", summary.strip())
    return t[:1].upper() + t[1:] if t else summary


def items_from_ical(text: str, tz: ZoneInfo) -> list[dict]:
    out = []
    for e in parse_ical(text, tz):
        s = e.get("summary") or ""
        if not s or SKIP.search(s) or not e.get("dtstart"):
            continue
        out.append({"key": f"moodle:ical:{e.get('uid') or _norm(s) + e['dtstart'].isoformat()}", "title": clean_title(s),
                    "due": e["dtstart"], "moodle_course": (e.get("categories") or "").split(",")[0].strip(),
                    "kind": "quiz" if re.search(r"quiz", s, re.I) else "assignment"})
    return out


# ---- web services --------------------------------------------------------------------------

class MoodleError(RuntimeError):
    pass


def _flatten(prefix: str, value, out: dict) -> None:
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]", v, out)
    else:
        out[prefix] = value


def ws(base_url: str, token: str, function: str, **params):
    data = {"wstoken": token, "wsfunction": function, "moodlewsrestformat": "json"}
    for k, v in params.items():
        _flatten(k, v, data)
    r = requests.post(f"{base_url}/webservice/rest/server.php", data=data, timeout=TIMEOUT,
                      headers={"User-Agent": "planner/1.0"})
    r.raise_for_status()
    body = r.json()
    if isinstance(body, dict) and body.get("exception"):
        raise MoodleError(f"{function}: {body.get('message') or body.get('errorcode')}")
    return body


def items_from_ws(base: str, token: str, now: datetime, tz: ZoneInfo) -> tuple[list[dict], dict, list[dict]]:
    """Returns (items, grades by moodle course name, moodle courses)."""
    site = ws(base, token, "core_webservice_get_site_info")
    uid = site["userid"]
    courses = ws(base, token, "core_enrol_get_users_courses", userid=uid)
    nowts = now.replace(tzinfo=tz).timestamp()
    current = [c for c in courses if not c.get("enddate") or c["enddate"] > nowts - 14 * 86400]
    by_id = {c["id"]: c for c in current}
    name = lambda cid: by_id.get(cid, {}).get("shortname") or by_id.get(cid, {}).get("fullname") or str(cid)
    items: dict[str, dict] = {}
    ids = list(by_id)
    if ids:
        try:
            for c in ws(base, token, "mod_assign_get_assignments", courseids=ids).get("courses", []):
                for a in c.get("assignments", []):
                    if a.get("duedate"):
                        items[f"assign:{a['id']}"] = {"key": f"moodle:assign:{a['id']}", "title": a["name"],
                                                      "due": _local(a["duedate"], tz), "moodle_course": name(c["id"]),
                                                      "kind": "assignment", "module": ("assign", a["id"])}
        except MoodleError:
            pass  # function disabled on this site; the timeline below still covers most of it
        try:
            for q in ws(base, token, "mod_quiz_get_quizzes_by_courses", courseids=ids).get("quizzes", []):
                if q.get("timeclose"):
                    items[f"quiz:{q['id']}"] = {"key": f"moodle:quiz:{q['id']}", "title": q["name"],
                                                "due": _local(q["timeclose"], tz), "moodle_course": name(q["course"]),
                                                "kind": "quiz", "module": ("quiz", q["id"])}
        except MoodleError:
            pass
    # The timeline lists only what still needs doing; anything due inside its window but missing
    # from it has been submitted/finished on Moodle.
    timeline_window = None
    try:
        tl = ws(base, token, "core_calendar_get_action_events_by_timesort",
                timesortfrom=int(nowts) - 86400, limitnum=50)
        events = tl.get("events", [])
        pending = set()
        for e in events:
            mod = (e.get("modulename"), e.get("instance"))
            pending.add(mod)
            k = f"{mod[0]}:{mod[1]}"
            if k not in items and e.get("timesort"):
                items[k] = {"key": f"moodle:{mod[0]}:{mod[1]}", "title": clean_title(e.get("activityname") or e["name"]),
                            "due": _local(e["timesort"], tz),
                            "moodle_course": name((e.get("course") or {}).get("id")) if e.get("course") else "",
                            "kind": "quiz" if mod[0] == "quiz" else "assignment", "module": mod}
        last = max((e["timesort"] for e in events), default=None)
        timeline_window = (_local(last, tz) if len(events) >= 50 and last else now + timedelta(days=3650))
        for it in items.values():
            it["done_on_moodle"] = bool(it.get("module")) and it["module"] not in pending and it["due"] <= timeline_window
    except MoodleError:
        pass
    grades = {}
    for cid in ids:
        try:
            g = ws(base, token, "gradereport_user_get_grade_items", courseid=cid, userid=uid)
        except MoodleError:
            continue
        for ug in g.get("usergrades", []):
            grades[name(cid)] = [{"name": gi.get("itemname") or ("Course total" if gi.get("itemtype") == "course" else ""),
                                  "type": gi.get("itemtype"), "score": gi.get("graderaw"), "max": gi.get("grademax"),
                                  "percent": gi.get("percentageformatted")} for gi in ug.get("gradeitems", [])]
    return list(items.values()), grades, [{"id": c["id"], "name": name(c["id"]), "fullname": c.get("fullname")} for c in current]


# ---- syncing ------------------------------------------------------------------------------

def apply_items(db: Session, items: list[dict], cfg: dict, now: datetime) -> dict:
    courses = list(db.scalars(select(Course)))
    created = updated = suggested = completed = 0
    for it in items:
        if it["due"] < now - timedelta(days=1):
            continue
        course = _course_for(db, it["moodle_course"], cfg, courses)
        if cfg["map"].get(it["moodle_course"]) == "ignore":
            continue
        t = db.scalar(select(Task).where(Task.external_id == it["key"]))
        is_quiz = it["kind"] == "quiz"
        if t is None:
            if it.get("done_on_moodle"):
                continue
            t = Task(title=it["title"], due=it["due"], est_minutes=30 if is_quiz else 90, needs_estimate=True,
                     course=course.name if course else None, course_id=course.id if course else None,
                     tags=_norm(course.name) if course else "", source="moodle", external_id=it["key"],
                     priority=2, splittable=not is_quiz,
                     status="open" if course else "suggested",
                     notes="" if course else f"From Moodle course {it['moodle_course']}. Map it in Settings → Moodle.")
            db.add(t)
            created += course is not None
            suggested += course is None
        else:
            if (t.due, t.title) != (it["due"], it["title"]):
                t.due, t.title = it["due"], it["title"]
                updated += 1
            if course and t.course_id is None:
                t.course_id, t.course = course.id, course.name
                if t.status == "suggested":
                    t.status = "open"
            if it.get("done_on_moodle") and t.status == "open":
                t.status = "done"
                t.notes = (t.notes + "\n" if t.notes else "") + "Marked done: Moodle shows it as completed."
                completed += 1
    db.commit()
    return {"created": created, "suggested": suggested, "updated": updated, "completed": completed}


def _match_items(cats: list[GradeCategory]) -> dict[str, GradeItem]:
    out = {}
    for c in cats:
        for i in c.items:
            out[_norm(i.name)] = i
    return out


def apply_grades(db: Session, grades: dict, cfg: dict) -> dict:
    """Put Moodle scores into matching grade-calculator items (by name); keep the full list for the course page."""
    courses = list(db.scalars(select(Course)))
    filled = 0
    for mname, rows in grades.items():
        course = _course_for(db, mname, cfg, courses)
        if not course:
            continue
        _set(db, f"moodle_grades:{course.id}", json.dumps(rows))
        mine = _match_items(list(db.scalars(select(GradeCategory).where(GradeCategory.course_id == course.id))))
        for r in rows:
            if r["type"] in ("course", "category") or r["score"] is None or not r["name"]:
                continue
            item = mine.get(_norm(r["name"]))
            if item is None:  # "Lab 3" vs "Lab 3: Buffer overflows"
                item = next((v for k, v in mine.items() if k and (k in _norm(r["name"]) or _norm(r["name"]) in k)
                             and min(len(k), len(_norm(r["name"]))) >= 4), None)
            if item and (item.score != r["score"] or (r["max"] and item.out_of != r["max"])):
                item.score = float(r["score"])
                if r["max"]:
                    item.out_of = float(r["max"])
                filled += 1
    db.commit()
    return {"grades_filled": filled}


def sync(db: Session, now: datetime) -> dict:
    cfg = config(db)
    tz = _tz(db)
    report: dict = {}
    items: list[dict] = []
    seen: dict[str, dict] = {s["name"]: s for s in cfg["seen"]}
    try:
        if cfg["base_url"] and cfg["token"]:
            ws_items, grades, mcourses = items_from_ws(cfg["base_url"], cfg["token"], now, tz)
            items += ws_items
            for c in mcourses:
                seen[c["name"]] = c
            report["via"] = "web service"
            report.update(apply_grades(db, grades, cfg))
        elif cfg["ical_url"]:
            r = requests.get(cfg["ical_url"], timeout=TIMEOUT, headers={"User-Agent": "planner/1.0"})
            r.raise_for_status()
            if "BEGIN:VCALENDAR" not in r.text:
                raise MoodleError("that link didn't return a calendar (it may have expired; get a new one in Moodle)")
            items += items_from_ical(r.text, tz)
            report["via"] = "calendar link"
        else:
            return {"skipped": "not set up"}
        for it in items:
            if it["moodle_course"] and it["moodle_course"] not in seen:
                seen[it["moodle_course"]] = {"name": it["moodle_course"]}
        report.update(found=len(items), **apply_items(db, items, cfg, now))
    except Exception as e:
        db.rollback()
        report = {"error": f"{type(e).__name__}: {e}"[:300]}
    _set(db, "moodle_seen", json.dumps(sorted(seen.values(), key=lambda s: s["name"])))
    _set(db, "moodle_last", json.dumps({**report, "at": now.isoformat(timespec="minutes")}))
    db.commit()
    return report
