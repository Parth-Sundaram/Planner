"""Turn a short line like "submit timesheet by Sunday" into a complete task.

Everything here is rules, not AI, so it's instant and predictable:
- when it's due ("by Sunday", "tomorrow 5pm", "Oct 27", "in 3 days", "tonight", "next Fri")
- whether it repeats ("every Sunday", "weekly", "daily", "every weekday", "monthly")
- how long it takes (your own history for similar titles, then keyword defaults)
- which course or area it belongs to (names, codes and per-area keywords)
Explicit metadata (~2h !1 #tag ...) still wins; see service.parse_meta.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
            "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
            "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_WD = r"(monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|friday|fri|saturday|sat|sunday|sun)"
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
_NAMED = {"noon": time(12), "midnight": time(23, 59), "morning": time(9), "afternoon": time(15),
          "evening": time(18), "night": time(21), "eod": time(17)}

DAY_LETTER = "MTWRFSU"


def _days_repeat(m) -> str:
    found = sorted({WEEKDAYS[w.lower()] for w in re.findall(_WD, m.group(0), re.I)})
    return f"weekly:{found[0]}" if len(found) == 1 else "days:" + "".join(DAY_LETTER[d] for d in found)


REPEAT = [
    # "every Mon, Tue, Thu and Fri" / "every mon tue thu fri"
    (re.compile(rf"\bevery\s+{_WD}(?:\s*(?:,|and|&|/)?\s*{_WD})+\b", re.I), _days_repeat),
    (re.compile(r"\bevery\s*(?:week)?day\b|\bweekdays\b", re.I), lambda m: "weekdays"),
    (re.compile(rf"\bevery\s+{_WD}\b", re.I), lambda m: f"weekly:{WEEKDAYS[m.group(1).lower()]}"),
    (re.compile(r"\b(?:every\s+day|daily)\b", re.I), lambda m: "daily"),
    (re.compile(r"\b(?:every\s+week|weekly)\b", re.I), lambda m: "weekly"),
    (re.compile(r"\b(?:every\s+other\s+week|biweekly)\b", re.I), lambda m: "biweekly"),
    (re.compile(r"\b(?:every\s+month|monthly)\b", re.I), lambda m: "monthly"),
]

# Rough default durations, minutes. First match wins, so specific phrases go first.
DEFAULT_MINUTES = [
    (r"timesheet|time sheet|hours log", 15), (r"\bemail|reply|respond|text\b|message", 10),
    (r"\bpay\b|bill|rent|venmo|transfer", 10), (r"\bsubmit\b|upload|turn in", 15),
    (r"\bschedule\b|book|register|sign up|rsvp", 15), (r"compose|post\b|discussion", 20),
    (r"daily quiz|reading quiz", 30), (r"\bquiz\b", 120), (r"\bexam\b|midterm|final\b", 240),
    (r"application|apply|cover letter", 60), (r"resume|cv\b", 60), (r"interview prep|leetcode", 60),
    (r"\bread\b|reading|chapter", 45), (r"\blab\b", 120), (r"homework|\bhw\b|problem set|pset|assignment", 120),
    (r"presentation|slides", 120), (r"report|essay|paper|write.?up", 180), (r"project|milestone", 120),
    (r"meeting|call|1:1|office hours", 30), (r"clean|laundry|groceries|errand", 45),
]

AREA_KEYWORDS = {
    "career": r"timesheet|time sheet|job|apply|application|resume|cv\b|cover letter|interview|recruiter|linkedin|"
              r"career|offer|internship|grader|\bta\b|networking|handshake|leetcode|coffee chat",
    "finance": r"\bpay\b|bill|rent|tuition|tax|budget|venmo|zelle|bank|credit card|fafsa|invoice|refund|subscription|insurance",
    "fitness": r"gym|workout|\brun\b|running|marathon|lift|stretch|mobility|meal prep|calorie|protein|physio|rehab|yoga|climb",
}


def _clock(h: str, m: str | None, ap: str | None) -> time | None:
    hour, minute = int(h), int(m or 0)
    if ap:
        ap = ap.lower()
        if ap == "pm" and hour != 12:
            hour += 12
        if ap == "am" and hour == 12:
            hour = 0
    elif hour <= 7:  # "at 5" means 5 PM for a student's to-do list
        hour += 12
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _next_weekday(today: date, wd: int, allow_today: bool = True) -> date:
    days = (wd - today.weekday()) % 7
    if days == 0 and not allow_today:
        days = 7
    return today + timedelta(days=days)


def parse_when(text: str, now: datetime) -> tuple[datetime | None, str | None, str]:
    """Returns (due, repeat, text with the date words removed)."""
    t = f" {text} "
    repeat = None
    for rx, fn in REPEAT:
        if m := rx.search(t):
            repeat = fn(m)
            t = t.replace(m.group(0), " ")
            break

    clock = None
    if m := re.search(rf"\b(?:at|@|by)?\s*{_TIME}\b(?=\s|$)", t, re.I):
        # only treat it as a time if it has am/pm or a colon, or follows "at"/"@"
        if m.group(3) or m.group(2) or re.match(r"\s*(at|@)", m.group(0), re.I):
            clock = _clock(m.group(1), m.group(2), m.group(3))
            if clock:
                t = t.replace(m.group(0), " ")
    if clock is None and (m := re.search(r"\b(?:at|by|in the)?\s*(noon|midnight|morning|afternoon|evening|night|eod|end of day)\b", t, re.I)):
        clock = _NAMED.get(m.group(1).lower(), time(17))
        t = t.replace(m.group(0), " ")

    today = now.date()
    day: date | None = None
    patterns = [
        (rf"\b(?:by|due|on|before)?\s*(today|tonight)\b", lambda m: today),
        (rf"\b(?:by|due|on|before)?\s*(tomorrow|tmrw|tmr)\b", lambda m: today + timedelta(days=1)),
        (rf"\b(?:by|due|on|before)?\s*next\s+{_WD}\b", lambda m: _next_weekday(today + timedelta(days=7 - today.weekday()), WEEKDAYS[m.group(1).lower()])),
        (rf"\b(?:by|due|on|before|this)?\s*{_WD}\b", lambda m: _next_weekday(today, WEEKDAYS[m.group(1).lower()])),
        (rf"\b(?:by|due|on|before)?\s*{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", lambda m: _month_day(today, MONTHS[m.group(1)[:3].lower()], int(m.group(2)))),
        (rf"\b(?:by|due|on|before)\s+(\d{{1,2}})/(\d{{1,2}})\b", lambda m: _month_day(today, int(m.group(1)), int(m.group(2)))),
        (r"\bin\s+(\d+)\s+(day|days|week|weeks)\b", lambda m: today + timedelta(days=int(m.group(1)) * (7 if m.group(2).startswith("w") else 1))),
        (r"\b(?:by\s+)?(?:the\s+)?end of (?:the\s+)?week\b", lambda m: _next_weekday(today, 4)),
        (r"\b(?:by\s+)?(?:this\s+)?weekend\b", lambda m: _next_weekday(today, 6)),
        (r"\bnext week\b", lambda m: _next_weekday(today + timedelta(days=7 - today.weekday()), 4)),
    ]
    for rx, fn in patterns:
        if m := re.search(rx, t, re.I):
            try:
                day = fn(m)
            except ValueError:
                continue
            if m.group(0).strip().lower() == "tonight" or "tonight" in m.group(0).lower():
                clock = clock or time(23, 59)
            t = t.replace(m.group(0), " ")
            break

    if repeat and day is None:
        if repeat.startswith("days:"):
            wanted = [DAY_LETTER.index(c) for c in repeat[5:]]
            day = min((_next_weekday(today, w) for w in wanted))
        elif repeat.startswith("weekly:"):
            day = _next_weekday(today, int(repeat.split(":")[1]))
        elif repeat == "weekdays":
            day = today if today.weekday() < 5 else _next_weekday(today, 0)
        else:
            day = today
    if repeat == "weekly" and day is not None:
        repeat = f"weekly:{day.weekday()}"
    if day is None and clock is None:
        return None, repeat, _clean(t)
    day = day or (today if clock and datetime.combine(today, clock) > now else today + timedelta(days=1))
    due = datetime.combine(day, clock or time(23, 59))
    if due <= now and day == today and clock is None:
        due = datetime.combine(day, time(23, 59))
    return due, repeat, _clean(t)


def _month_day(today: date, month: int, day: int) -> date:
    d = date(today.year, month, day)
    return d if d >= today - timedelta(days=30) else date(today.year + 1, month, day)


def _clean(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip(" ,;-")
    t = re.sub(r"\b(by|due|on|before|at)$", "", t, flags=re.I).strip(" ,;-")
    first = t.split(" ", 1)[0] if t else ""
    return t[:1].upper() + t[1:] if first.isalpha() else t


def default_minutes(title: str) -> int | None:
    for rx, minutes in DEFAULT_MINUTES:
        if re.search(rx, title, re.I):
            return minutes
    return None


def _words(title: str) -> set[str]:
    stop = {"the", "a", "an", "for", "to", "of", "and", "my", "on", "in", "part", "due"}
    return {w for w in re.findall(r"[a-z]+", title.lower()) if w not in stop and len(w) > 2}


def estimate_from_history(title: str, history: list[tuple[str, int]]) -> int | None:
    """Average time of past finished tasks that share most of their words with this one."""
    mine = _words(title)
    if not mine:
        return None
    hits = [m for t, m in history if (w := _words(t)) and len(mine & w) / len(mine | w) >= 0.5]
    if not hits:
        return None
    avg = sum(hits) / len(hits)
    return max(5, int(round(avg / 5) * 5))


def guess_area(title: str, areas: list) -> object | None:
    """Pick a course or area: its name or code in the title, its own keywords, then the area type's keywords."""
    low = f" {title.lower()} "
    for a in areas:
        names = [a.name.lower(), (a.code or "").lower().replace("-", " ")]
        if any(n and re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", low) for n in names):
            return a
    for a in areas:
        kws = [k.strip() for k in (getattr(a, "keywords", "") or "").split(",") if k.strip()]
        if any(re.search(rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])", low) for k in kws):
            return a
    for kind, rx in AREA_KEYWORDS.items():
        if re.search(rx, low, re.I):
            for a in areas:
                if getattr(a, "kind", "course") == kind:
                    return a
    return None


def next_due(due: datetime, repeat: str) -> datetime:
    if repeat.startswith("days:"):
        wanted = {DAY_LETTER.index(c) for c in repeat[5:]}
        d = due + timedelta(days=1)
        while d.weekday() not in wanted:
            d += timedelta(days=1)
        return d
    if repeat == "daily":
        return due + timedelta(days=1)
    if repeat == "weekdays":
        d = due + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d
    if repeat.startswith("weekly"):
        return due + timedelta(days=7)
    if repeat == "biweekly":
        return due + timedelta(days=14)
    if repeat == "monthly":
        m = due.month % 12 + 1
        y = due.year + (due.month == 12)
        for day in (due.day, 30, 29, 28):
            try:
                return due.replace(year=y, month=m, day=day)
            except ValueError:
                continue
    raise ValueError(repeat)


REPEAT_LABELS = {"daily": "every day", "weekdays": "every weekday", "biweekly": "every 2 weeks", "monthly": "every month"}


def repeat_label(repeat: str | None) -> str | None:
    if not repeat:
        return None
    names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if repeat.startswith("weekly:"):
        return "every " + names[int(repeat[7:])]
    if repeat.startswith("days:"):
        return "every " + ", ".join(names[DAY_LETTER.index(c)][:3] for c in repeat[5:])
    return REPEAT_LABELS.get(repeat, "every week")
