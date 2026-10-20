"""In-memory stand-ins for the Google Calendar and Tasks API clients (same call shapes)."""
import itertools
from datetime import datetime


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def _parse(v):
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


class FakeCalendar:
    def __init__(self):
        self.cals = {"primary": {}}
        self.ids = itertools.count(1)
        self.calls = []

    # calendar.calendars()
    def calendars(self):
        outer = self

        class C:
            def insert(self, body):
                cid = f"plan{next(outer.ids)}@group"
                outer.cals[cid] = {}
                return _Exec({"id": cid, **body})
        return C()

    def events(self):
        outer = self

        class E:
            def list(self, calendarId, timeMin, timeMax, privateExtendedProperty=None, pageToken=None, **kw):
                lo, hi = _parse(timeMin), _parse(timeMax)
                items = []
                for ev in outer.cals.get(calendarId, {}).values():
                    if privateExtendedProperty:
                        k, v = privateExtendedProperty.split("=")
                        if ev.get("extendedProperties", {}).get("private", {}).get(k) != v:
                            continue
                    s = ev["start"].get("dateTime")
                    if s:
                        st = _parse(s) if "+" in s or "Z" in s else _parse(s).replace(tzinfo=lo.tzinfo)
                        en = _parse(ev["end"]["dateTime"]) if "+" in ev["end"]["dateTime"] else _parse(ev["end"]["dateTime"]).replace(tzinfo=lo.tzinfo)
                        if en <= lo or st >= hi:
                            continue
                    items.append(dict(ev))
                return _Exec({"items": items})

            def insert(self, calendarId, body):
                eid = f"ev{next(outer.ids)}"
                outer.cals[calendarId][eid] = {"id": eid, **body}
                outer.calls.append(("insert", eid))
                return _Exec(outer.cals[calendarId][eid])

            def patch(self, calendarId, eventId, body):
                outer.cals[calendarId][eventId].update(body)
                outer.calls.append(("patch", eventId))
                return _Exec(outer.cals[calendarId][eventId])

            def delete(self, calendarId, eventId):
                outer.cals[calendarId].pop(eventId)
                outer.calls.append(("delete", eventId))
                return _Exec({})
        return E()

    def add_event(self, cal_id, summary, start, end, **kw):
        eid = f"ext{next(self.ids)}"
        self.cals.setdefault(cal_id, {})[eid] = {
            "id": eid, "summary": summary,
            "start": {"dateTime": start}, "end": {"dateTime": end}, **kw}
        return eid


class FakeTasks:
    def __init__(self, tasks):
        self._tasks = tasks

    def tasklists(self):
        class L:
            def list(self, **kw):
                return _Exec({"items": [{"id": "list1", "title": "My Tasks"}]})
        return L()

    def tasks(self):
        outer = self

        class T:
            def list(self, tasklist, **kw):
                return _Exec({"items": outer._tasks})
        return T()
