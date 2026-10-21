"""Flask API + Google sign-in. Also serves the built React app in production."""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, g, jsonify, redirect, request, send_from_directory, session, url_for
from sqlalchemy import select
from werkzeug.middleware.proxy_fix import ProxyFix

from . import areas as A
from . import assistant
from . import syllabus
from . import grades, gsync, scrapers, service
from .quickadd import repeat_label
from .models import (AreaEntry, AttendanceLog, Block, Course, ExternalEvent, GradeCategory, GradeItem, Routine, Rule, Setting,
                     Task, make_session_factory)

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google may return scopes in a different order

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

TASK_FIELDS = {"title", "course", "est_minutes", "done_minutes", "min_total_minutes", "priority",
               "splittable", "min_block", "max_block", "notes", "status", "position", "sequential", "max_per_day",
               "repeat"}
ROUTINE_FIELDS = {"title", "days", "window_start", "window_end", "duration", "kind", "active"}
RULE_FIELDS = {"tag", "weekday", "time", "description"}
SETTING_KEYS = {"timezone", "day_start", "day_end", "horizon_days", "busy_calendar_ids", "calorie_target", "protein_target"}
COURSE_FIELDS = {"name", "url", "parser", "render_js", "class_days", "class_time", "enabled", "review",
                 "color", "instructor", "notes", "target_grade", "kind", "keywords",
                 "attend_allowed", "attend_expected", "grade_policies"}
AREA_KINDS = {"course", "career", "fitness", "finance", "personal"}


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="minutes") if dt else None


def parse_dt(v: str | None) -> datetime | None:
    if not v:
        return None
    dt = datetime.fromisoformat(v.replace("Z", ""))
    return dt.replace(tzinfo=None, second=0, microsecond=0)


def task_json(t: Task, rules) -> dict:
    return {
        "id": t.id, "title": t.title, "course": t.course, "due": iso(t.due),
        "effective_due": iso(service.task_effective_due(t, rules)),
        "est_minutes": t.est_minutes, "done_minutes": t.done_minutes,
        "min_total_minutes": t.min_total_minutes, "priority": t.priority, "splittable": t.splittable,
        "min_block": t.min_block, "max_block": t.max_block, "tags": t.tag_list,
        "earliest_start": iso(t.earliest_start), "status": t.status, "source": t.source,
        "needs_estimate": t.needs_estimate, "notes": t.notes,
        "parent_id": t.parent_id, "position": t.position, "sequential": t.sequential,
        "course_id": t.course_id, "confidence": t.confidence, "max_per_day": t.max_per_day,
        "repeat": t.repeat, "repeat_label": repeat_label(t.repeat),
        "is_study": t.title.lower().startswith("study") or any(w in t.title.lower() for w in ("exam", "quiz", "midterm", "final")),
    }


def task_tree_json(db, t: Task, rules) -> dict:
    """A task plus its subtasks. A parent's time is the sum of its steps."""
    d = task_json(t, rules)
    subs = service.subtasks_of(db, t.id)
    d["subtasks"] = [task_json(c, rules) for c in subs]
    if subs:
        d["est_minutes"] = sum(c.est_minutes for c in subs)
        d["done_minutes"] = sum(c.done_minutes if c.status == "open" else c.est_minutes for c in subs)
        d["needs_estimate"] = any(c.needs_estimate and c.status == "open" for c in subs)
    return d


def block_json(b: Block, tasks: dict | None = None) -> dict:
    t = tasks.get(b.task_id) if tasks and b.task_id else None
    return {"id": b.id, "title": b.title, "start": iso(b.start), "end": iso(b.end), "kind": b.kind,
            "task_id": b.task_id, "routine_id": b.routine_id, "locked": b.locked,
            "status": b.status, "done_minutes": b.done_minutes,
            "course_id": t.course_id if t else None, "parent_task_id": t.parent_id if t else None}


def blocks_json(db, blocks) -> list[dict]:
    ids = {b.task_id for b in blocks if b.task_id}
    tasks = {t.id: t for t in db.scalars(select(Task).where(Task.id.in_(ids)))} if ids else {}
    return [block_json(b, tasks) for b in blocks]


def course_json(c: Course) -> dict:
    return {"id": c.id, "code": c.code, "name": c.name, "url": c.url, "parser": c.parser,
            "parser_label": scrapers.PARSER_LABELS.get(c.parser, c.parser), "render_js": c.render_js,
            "class_days": c.class_days, "class_time": c.class_time, "enabled": c.enabled, "review": c.review,
            "last_sync_at": iso(c.last_sync_at), "last_result": json.loads(c.last_result or "{}"),
            "color": c.color, "instructor": c.instructor, "notes": c.notes,
            "links": json.loads(c.links or "[]"), "target_grade": c.target_grade,
            "kind": c.kind or "course", "keywords": c.keywords or "", "has_recipe": bool(c.recipe),
            "attend_allowed": c.attend_allowed or 0, "attend_expected": c.attend_expected,
            "grade_policies": c.grade_policies or "", "grade_scale": json.loads(c.grade_scale or "[]")}


def create_app(database_url: str | None = None, testing: bool = False) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)  # correct https URLs behind the host's proxy
    app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1") == "1" and not testing,
                      PERMANENT_SESSION_LIFETIME=timedelta(days=30), TESTING=testing)
    Session = make_session_factory(database_url)
    allowed = {e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()}
    dev_login = os.getenv("DEV_LOGIN") == "1" or testing

    @app.before_request
    def open_db():
        g.db = Session()

    @app.teardown_request
    def close_db(exc):
        db = g.pop("db", None)
        if db:
            if exc:
                db.rollback()
            db.close()

    @app.errorhandler(Exception)
    def api_error(e):
        """API errors always come back as JSON with a readable message."""
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            if not request.path.startswith("/api/"):
                return e
            return jsonify(error=e.description), e.code
        app.logger.exception("Unhandled error on %s", request.path)
        if not request.path.startswith("/api/"):
            return "Internal Server Error", 500
        return jsonify(error=f"Server error: {type(e).__name__}: {e}"[:300]), 500

    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not session.get("email"):
                return jsonify(error="Not signed in"), 401
            return fn(*a, **kw)
        return wrapper

    def cron_or_login(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            secret = os.getenv("CRON_SECRET")
            if secret and request.headers.get("X-Cron-Secret") == secret:
                return fn(*a, **kw)
            if not session.get("email"):
                return jsonify(error="Not signed in"), 401
            return fn(*a, **kw)
        return wrapper

    def api_token() -> str | None:
        row = g.db.get(Setting, "api_token")
        return row.value if row else None

    def token_or_login(fn):
        """For the browser extension and phone shortcuts: `Authorization: Bearer <token>` or a session."""
        @wraps(fn)
        def wrapper(*a, **kw):
            auth = request.headers.get("Authorization", "")
            tok = api_token()
            if tok and auth.startswith("Bearer ") and secrets.compare_digest(auth[7:].strip(), tok):
                return fn(*a, **kw)
            if not session.get("email"):
                return jsonify(error="Not signed in"), 401
            return fn(*a, **kw)
        return wrapper

    app.token_or_login = token_or_login

    # ---- Auth (Google sign-in doubles as the Calendar/Tasks connection) -------------------

    def oauth_flow(state=None):
        from google_auth_oauthlib.flow import Flow
        cfg = {"web": {"client_id": os.environ["GOOGLE_CLIENT_ID"], "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                       "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                       "token_uri": "https://oauth2.googleapis.com/token"}}
        flow = Flow.from_client_config(cfg, scopes=gsync.SCOPES, state=state)
        flow.redirect_uri = url_for("oauth_callback", _external=True)
        return flow

    @app.get("/auth/login")
    def oauth_login():
        flow = oauth_flow()
        auth_url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        session["oauth_state"] = state
        session["oauth_verifier"] = getattr(flow, "code_verifier", None)
        return redirect(auth_url)

    @app.get("/auth/callback")
    def oauth_callback():
        flow = oauth_flow(state=session.get("oauth_state"))
        if session.get("oauth_verifier"):
            flow.code_verifier = session["oauth_verifier"]
        flow.fetch_token(authorization_response=request.url)
        creds = flow.credentials
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request
        info = id_token.verify_oauth2_token(creds.id_token, Request(), os.environ["GOOGLE_CLIENT_ID"])
        email = info.get("email", "").lower()
        if not info.get("email_verified") or (allowed and email not in allowed):
            return "This account isn't allowed to use this planner.", 403
        gsync.save_credentials(g.db, email, creds)
        session.clear()
        session.permanent = True
        session["email"] = email
        return redirect("/")

    @app.post("/auth/dev-login")
    def dev_login_route():
        if not dev_login:
            abort(404)
        session["email"] = "dev@local"
        return jsonify(ok=True)

    @app.post("/auth/logout")
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get("/api/me")
    def me():
        return jsonify(email=session.get("email"), dev_login=dev_login,
                       google_connected=gsync.load_credentials(g.db) is not None if session.get("email") else False)

    # ---- Tasks -------------------------------------------------------------------------------

    def rules():
        return service._rules(g.db)

    @app.get("/api/tasks")
    @login_required
    def list_tasks():
        status = request.args.get("status", "open")
        q = select(Task).where(Task.parent_id.is_(None)).order_by(Task.due)
        if status == "all":
            q = q.where(Task.status.in_(["open", "done"]))
        else:
            q = q.where(Task.status == status)
        if request.args.get("course_id"):
            q = q.where(Task.course_id == int(request.args["course_id"]))
        r = rules()
        return jsonify([task_tree_json(g.db, t, r) for t in g.db.scalars(q)])

    def apply_task_fields(t: Task, data: dict):
        if "confidence" in data:
            level = data["confidence"]
            service.apply_confidence(t, int(level) if level else None)
        for k in TASK_FIELDS & data.keys():
            setattr(t, k, data[k])
        if "due" in data:
            t.due = parse_dt(data["due"])
        if "earliest_start" in data:
            t.earliest_start = parse_dt(data["earliest_start"])
        if "tags" in data:
            tags = data["tags"]
            t.tags = ",".join(x.strip().lstrip("#").lower() for x in (tags if isinstance(tags, list) else tags.split(",")) if x.strip())
        if "course_id" in data:
            c = g.db.get(Course, data["course_id"]) if data["course_id"] else None
            t.course_id, t.course = (c.id, c.name) if c else (None, None)
        if "est_minutes" in data:
            t.needs_estimate = False

    @app.post("/api/tasks")
    @token_or_login
    def create_task():
        data = request.get_json(force=True)
        why = {}
        if data.get("text"):  # quick add: "submit timesheet by Sunday", "CNN 4/4 ~4h !1 #ai"
            title, meta = service.parse_meta(data["text"])
            smart = service.smart_fill(g.db, title, service.local_now(g.db)) if not data.get("parent_id") else {"title": title}
            why = smart.pop("why", {})
            if smart.get("due"):
                smart["due"] = iso(smart["due"])
            if "est_minutes" in meta:
                smart.pop("est_minutes", None)
                why.pop("est", None)
            # explicit fields from the request win, then ~2h-style metadata, then the guesses
            data = {**smart, **meta, **{k: v for k, v in data.items() if k != "text" and v not in (None, "")}}
            if not data.get("due") and not data.get("parent_id"):
                tomorrow = (service.local_now(g.db) + timedelta(days=1)).replace(hour=23, minute=59)
                data["due"], why["due"] = iso(tomorrow), "no date given, so tomorrow night"
        parent = None
        if data.get("parent_id"):
            parent = g.db.get(Task, int(data["parent_id"]))
            if parent is None or parent.parent_id is not None:
                return jsonify(error="parent must be an existing top-level task"), 400
            data.setdefault("due", iso(parent.due))  # steps inherit the parent's deadline
        if not data.get("title") or not data.get("due"):
            return jsonify(error="title and due are required"), 400
        t = Task(title=data["title"], due=parse_dt(data["due"]))
        if parent:
            t.parent_id, t.course, t.course_id = parent.id, parent.course, parent.course_id
            t.position = service.next_position(g.db, parent.id)
            if parent.status == "done":
                parent.status = "open"
        apply_task_fields(t, data)
        if "est_minutes" not in data:
            t.est_minutes, t.needs_estimate = 60, True
        elif why.get("est"):
            t.needs_estimate = False
        g.db.add(t)
        g.db.commit()
        plan = service.replan(g.db)
        return jsonify(task=task_json(t, rules()), plan=plan, why=why), 201

    @app.patch("/api/tasks/<int:tid>")
    @login_required
    def update_task(tid):
        t = g.db.get(Task, tid) or abort(404)
        data = request.get_json(force=True)
        old_due, old_status = t.due, t.status
        if "repeat" in data and data["repeat"] not in (None, "", "daily", "weekdays", "biweekly", "monthly") \
                and not str(data["repeat"]).startswith("weekly"):
            return jsonify(error="repeat must be daily, weekdays, weekly, biweekly or monthly"), 400
        if data.get("repeat") == "weekly":
            data["repeat"] = f"weekly:{t.due.weekday()}"
        apply_task_fields(t, data)
        if t.repeat == "":
            t.repeat = None
        if old_status != "done" and t.status == "done":
            service.spawn_next(g.db, t)
        if "due" in data and t.due != old_due:
            # Steps that were following the parent's deadline move with it.
            for c in service.subtasks_of(g.db, t.id):
                if c.due == old_due:
                    c.due = t.due
        g.db.commit()
        return jsonify(task=task_tree_json(g.db, t, rules()), plan=service.replan(g.db))

    @app.delete("/api/tasks/<int:tid>")
    @login_required
    def delete_task(tid):
        t = g.db.get(Task, tid) or abort(404)
        g.db.delete(t)
        g.db.commit()
        return jsonify(plan=service.replan(g.db))

    @app.get("/api/tasks/<int:tid>")
    @login_required
    def get_task(tid):
        t = g.db.get(Task, tid) or abort(404)
        ids = [t.id] + [c.id for c in service.subtasks_of(g.db, t.id)]
        bl = g.db.scalars(select(Block).where(Block.task_id.in_(ids)).order_by(Block.start)).all()
        return jsonify(task=task_tree_json(g.db, t, rules()), blocks=blocks_json(g.db, bl))

    @app.post("/api/suggestions/accept-all")
    @login_required
    def accept_all():
        n = 0
        for t in g.db.scalars(select(Task).where(Task.status == "suggested")):
            t.status, n = "open", n + 1
        g.db.commit()
        return jsonify(accepted=n, plan=service.replan(g.db))

    @app.post("/api/tasks/<int:tid>/finish")
    @login_required
    def finish_task(tid):
        g.db.get(Task, tid) or abort(404)
        service.finish_task(g.db, tid)
        return jsonify(ok=True)

    # ---- Blocks / plan -----------------------------------------------------------------------

    @app.get("/api/blocks")
    @login_required
    def list_blocks():
        now = service.local_now(g.db)
        start = parse_dt(request.args.get("start")) or now.replace(hour=0, minute=0)
        end = parse_dt(request.args.get("end")) or start + timedelta(days=7)
        blocks = g.db.scalars(select(Block).where(Block.end > start, Block.start < end).order_by(Block.start)).all()
        events = g.db.scalars(select(ExternalEvent).where(ExternalEvent.end > start, ExternalEvent.start < end)).all()
        return jsonify(blocks=blocks_json(g.db, blocks),
                       events=[{"id": e.id, "title": e.title, "start": iso(e.start), "end": iso(e.end)} for e in events])

    @app.patch("/api/blocks/<int:bid>")
    @login_required
    def move(bid):
        g.db.get(Block, bid) or abort(404)
        data = request.get_json(force=True)
        service.move_block(g.db, bid, parse_dt(data["start"]), parse_dt(data["end"]))
        return jsonify(ok=True)

    @app.post("/api/blocks/<int:bid>/unlock")
    @login_required
    def unlock(bid):
        g.db.get(Block, bid) or abort(404)
        service.unlock_block(g.db, bid)
        return jsonify(ok=True)

    @app.delete("/api/blocks/<int:bid>")
    @login_required
    def delete_block(bid):
        b = g.db.get(Block, bid) or abort(404)
        g.db.delete(b)
        g.db.commit()
        return jsonify(plan=service.replan(g.db))

    @app.post("/api/blocks/<int:bid>/checkin")
    @login_required
    def checkin(bid):
        g.db.get(Block, bid) or abort(404)
        data = request.get_json(force=True)
        try:
            service.check_in(g.db, bid, data["status"], data.get("minutes"))
        except ValueError:
            return jsonify(error="status must be done, partial or skipped"), 400
        return jsonify(ok=True)

    def block_action(bid, fn, *args):
        g.db.get(Block, bid) or abort(404)
        try:
            fn(g.db, bid, *args)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True)

    @app.post("/api/blocks/<int:bid>/not-now")
    @login_required
    def block_not_now(bid):
        return block_action(bid, service.not_now)

    @app.post("/api/blocks/<int:bid>/skip")
    @login_required
    def block_skip(bid):
        return block_action(bid, service.skip_routine)

    @app.post("/api/blocks/<int:bid>/restore")
    @login_required
    def block_restore(bid):
        return block_action(bid, service.restore_block)

    @app.post("/api/blocks/<int:bid>/remove")
    @login_required
    def block_remove(bid):
        return block_action(bid, service.remove_block)

    @app.post("/api/came-up")
    @login_required
    def came_up():
        data = request.get_json(force=True)
        blk = service.something_came_up(g.db, int(data.get("minutes", 60)), data.get("title") or "Something came up")
        return jsonify(block=block_json(blk))

    @app.post("/api/replan")
    @login_required
    def replan():
        return jsonify(service.replan(g.db))

    @app.post("/api/sync")
    @cron_or_login
    def sync():
        scrape = request.args.get("scrape", "1") == "1"
        return jsonify(gsync.full_sync(g.db, scrape=scrape))

    def attendance_prompts(now) -> list[dict]:
        """Courses that track attendance and met today (class time has started)."""
        code = "MTWRFSU"[now.weekday()]
        day0 = now.replace(hour=0, minute=0)
        out = []
        for c in g.db.scalars(select(Course).where(Course.kind == "course")):
            tracks = g.db.scalar(select(GradeCategory.id).where(GradeCategory.course_id == c.id,
                                                                GradeCategory.is_attendance.is_(True)))
            if not tracks or code not in (c.class_days or "") or now.strftime("%H:%M") < (c.class_time or "10:00"):
                continue
            mark = g.db.scalar(select(AttendanceLog).where(AttendanceLog.course_id == c.id, AttendanceLog.day == day0))
            out.append({"course_id": c.id, "name": c.name, "status": mark.status if mark else None})
        return out

    @app.get("/api/today")
    @login_required
    def today():
        now = service.local_now(g.db)
        day_start = now.replace(hour=0, minute=0)
        day_end = day_start + timedelta(days=1)
        blocks = g.db.scalars(select(Block).where(Block.end > day_start, Block.start < day_end).order_by(Block.start)).all()
        events = g.db.scalars(select(ExternalEvent).where(ExternalEvent.end > day_start, ExternalEvent.start < day_end)).all()
        plan = json.loads(service.get_settings(g.db).get("last_plan") or "{}")
        remaining = sum(service.minutes_between(max(b.start, now), b.end) for b in blocks
                        if b.kind == "task" and b.status == "planned" and b.end > now)
        needs_checkin = [b.id for b in blocks if b.kind == "task" and b.status == "planned" and b.end <= now]
        return jsonify(now=iso(now), blocks=blocks_json(g.db, blocks),
                       events=[{"id": e.id, "title": e.title, "start": iso(e.start), "end": iso(e.end)} for e in events],
                       remaining_task_minutes=remaining, needs_checkin=needs_checkin,
                       attendance=attendance_prompts(now),
                       due_soon=[task_tree_json(g.db, t, rules()) for t in g.db.scalars(
                           select(Task).where(Task.status == "open", Task.parent_id.is_(None),
                                              Task.due < now + timedelta(days=7)).order_by(Task.due).limit(12))],
                       warnings=plan.get("warnings", []), planned_at=plan.get("planned_at"))

    # ---- Routines, rules, settings -----------------------------------------------------------

    def crud(model, fields, name):
        @app.get(f"/api/{name}", endpoint=f"list_{name}")
        @login_required
        def _list():
            return jsonify([{c: getattr(r, c) for c in ["id", *fields]} for r in g.db.scalars(select(model))])

        @app.post(f"/api/{name}", endpoint=f"create_{name}")
        @login_required
        def _create():
            data = request.get_json(force=True)
            row = model(**{k: data[k] for k in fields & data.keys()})
            g.db.add(row)
            g.db.commit()
            service.replan(g.db)
            return jsonify({c: getattr(row, c) for c in ["id", *fields]}), 201

        @app.patch(f"/api/{name}/<int:rid>", endpoint=f"update_{name}")
        @login_required
        def _update(rid):
            row = g.db.get(model, rid) or abort(404)
            data = request.get_json(force=True)
            for k in fields & data.keys():
                setattr(row, k, data[k])
            g.db.commit()
            service.replan(g.db)
            return jsonify({c: getattr(row, c) for c in ["id", *fields]})

        @app.delete(f"/api/{name}/<int:rid>", endpoint=f"delete_{name}")
        @login_required
        def _delete(rid):
            row = g.db.get(model, rid) or abort(404)
            g.db.delete(row)
            g.db.commit()
            service.replan(g.db)
            return jsonify(ok=True)

    crud(Routine, ROUTINE_FIELDS, "routines")
    crud(Rule, RULE_FIELDS, "rules")

    @app.get("/api/settings")
    @login_required
    def get_settings():
        s = service.get_settings(g.db)
        return jsonify({k: s.get(k) for k in SETTING_KEYS | {"plan_calendar_id"}})

    @app.put("/api/settings")
    @login_required
    def put_settings():
        data = request.get_json(force=True)
        for k in SETTING_KEYS & data.keys():
            service.set_setting(g.db, k, data[k] if isinstance(data[k], str) else json.dumps(data[k]))
        g.db.commit()
        return jsonify(service.replan(g.db))

    # ---- Courses (schedule pages to pull deadlines from) ----------------------------------------

    @app.get("/api/courses")
    @token_or_login
    def list_courses():
        out = []
        for c in scrapers.get_courses(g.db):
            d = course_json(c)
            open_tasks = g.db.scalars(select(Task).where(Task.course_id == c.id, Task.status == "open",
                                                         Task.parent_id.is_(None)).order_by(Task.due)).all()
            cats = g.db.scalars(select(GradeCategory).where(GradeCategory.course_id == c.id)).all()
            nxt = open_tasks[0] if open_tasks else None
            d["stats"] = {"open": len(open_tasks), "next_title": nxt.title if nxt else None,
                          "next_due": iso(nxt.due) if nxt else None,
                          "grade": grade_summary(c)["current"] if cats else None}
            out.append(d)
        return jsonify(courses=out, parsers=scrapers.PARSER_LABELS, colors=scrapers.COLORS,
                       ai_ready=bool(os.getenv("GEMINI_API_KEY")))

    def apply_course(c: Course, data: dict):
        source_changed = bool({"url", "parser", "render_js", "class_days", "class_time"} & data.keys())
        for k in COURSE_FIELDS & data.keys():
            setattr(c, k, data[k])
        if "links" in data:
            links = [{"label": (l.get("label") or "").strip()[:80], "url": (l.get("url") or "").strip()}
                     for l in data["links"] if (l.get("url") or "").strip()]
            c.links = json.dumps(links)
        c.url = (c.url or "").strip()
        if c.parser != "none" and c.parser not in scrapers.PARSERS:
            raise ValueError(f"unknown parser {c.parser}")
        if not (c.name or "").strip():
            raise ValueError("a course needs a name")
        if c.url and not c.url.startswith(("http://", "https://")):
            raise ValueError("the schedule page must be an http(s) URL")
        if c.parser != "none" and not c.url:
            c.parser = "none"
        if "name" in data:
            for t in g.db.scalars(select(Task).where(Task.course_id == c.id)):
                t.course = c.name
        if source_changed:
            c.page_hash = None  # re-read the page next sync
            if {"url", "parser"} & data.keys():
                c.recipe = None

    @app.post("/api/courses")
    @login_required
    def create_course():
        data = request.get_json(force=True)
        base = scrapers._slug(data.get("name", "")) or "course"
        code, n = base, 2
        while g.db.scalar(select(Course).where(Course.code == code)):
            code, n = f"{base}-{n}", n + 1
        used = {x.color for x in g.db.scalars(select(Course))}
        color = next((x for x in scrapers.COLORS if x not in used), "blue")
        c = Course(code=code, name="", url="", parser="ai", review=True, color=color, links="[]", notes="", instructor="")
        try:
            apply_course(c, {"parser": "ai" if data.get("url") else "none", "review": True, **data})
            if (c.kind or "course") not in AREA_KINDS:
                raise ValueError("unknown kind")
        except ValueError as e:
            return jsonify(error=str(e)), 400
        g.db.add(c)
        g.db.commit()
        return jsonify(course_json(c)), 201

    @app.patch("/api/courses/<int:cid>")
    @login_required
    def update_course(cid):
        c = g.db.get(Course, cid) or abort(404)
        try:
            apply_course(c, request.get_json(force=True))
        except ValueError as e:
            g.db.rollback()
            return jsonify(error=str(e)), 400
        g.db.commit()
        return jsonify(course_json(c))

    @app.delete("/api/courses/<int:cid>")
    @login_required
    def delete_course(cid):
        c = g.db.get(Course, cid) or abort(404)
        for t in g.db.scalars(select(Task).where(Task.course_id == c.id)):
            t.course_id, t.course = None, None  # tasks stay; they just lose the course label
        g.db.delete(c)
        g.db.commit()
        return jsonify(ok=True)

    def course_detail(c: Course) -> dict:
        r = rules()
        tasks = g.db.scalars(select(Task).where(Task.course_id == c.id, Task.parent_id.is_(None),
                                                Task.status.in_(["open", "done", "suggested"])).order_by(Task.due)).all()
        ids = [t.id for t in tasks] + [s.id for t in tasks for s in service.subtasks_of(g.db, t.id)]
        now = service.local_now(g.db)
        upcoming = g.db.scalars(select(Block).where(Block.task_id.in_(ids), Block.end > now - timedelta(days=1))
                                .order_by(Block.start).limit(40)).all() if ids else []
        return {"course": course_json(c), "tasks": [task_tree_json(g.db, t, r) for t in tasks],
                "blocks": blocks_json(g.db, upcoming), "grades": grade_summary(c)}

    def attendance_of(c: Course) -> dict:
        logs = g.db.scalars(select(AttendanceLog).where(AttendanceLog.course_id == c.id).order_by(AttendanceLog.day)).all()
        counts = {"present": 0, "absent": 0, "excused": 0}
        for l in logs:
            counts[l.status] = counts.get(l.status, 0) + 1
        return {**counts, "allowed": c.attend_allowed or 0, "expected": c.attend_expected,
                "free_left": max(0, (c.attend_allowed or 0) - counts["absent"]),
                "log": [{"id": l.id, "day": l.day.date().isoformat(), "status": l.status} for l in logs[-60:]]}

    def grade_summary(c: Course) -> dict:
        cats = g.db.scalars(select(GradeCategory).where(GradeCategory.course_id == c.id)).all()
        return grades.summarize(cats, c.target_grade, attendance_of(c), json.loads(c.grade_scale or "[]"))

    @app.get("/api/courses/<int:cid>")
    @login_required
    def get_course(cid):
        c = g.db.get(Course, cid) or abort(404)
        return jsonify(course_detail(c))

    @app.post("/api/courses/<int:cid>/grade-categories")
    @login_required
    def add_category(cid):
        c = g.db.get(Course, cid) or abort(404)
        d = request.get_json(force=True)
        n = len(g.db.scalars(select(GradeCategory).where(GradeCategory.course_id == cid)).all())
        parent = d.get("parent_id")
        if parent and (pc := g.db.get(GradeCategory, int(parent))) is None or (parent and pc.course_id != cid):
            return jsonify(error="parent category not found"), 400
        g.db.add(GradeCategory(course_id=cid, name=d.get("name") or "Category", weight=float(d.get("weight") or 0),
                               drop_lowest=int(d.get("drop_lowest") or 0), position=n,
                               parent_id=int(parent) if parent else None, is_attendance=bool(d.get("is_attendance"))))
        g.db.commit()
        return jsonify(grade_summary(c)), 201

    @app.patch("/api/grade-categories/<int:gid>")
    @login_required
    def update_category(gid):
        cat = g.db.get(GradeCategory, gid) or abort(404)
        d = request.get_json(force=True)
        for k, conv in (("name", str), ("weight", float), ("drop_lowest", int), ("position", int)):
            if k in d:
                setattr(cat, k, conv(d[k] or 0) if k != "name" else d[k])
        if "is_attendance" in d:
            cat.is_attendance = bool(d["is_attendance"])
        g.db.commit()
        return jsonify(grade_summary(g.db.get(Course, cat.course_id)))

    @app.delete("/api/grade-categories/<int:gid>")
    @login_required
    def delete_category(gid):
        cat = g.db.get(GradeCategory, gid) or abort(404)
        cid = cat.course_id
        todo, doomed = [cat.id], []
        while todo:  # subcategories too (don't rely on ON DELETE CASCADE, see syllabus.apply)
            kids = list(g.db.scalars(select(GradeCategory).where(GradeCategory.parent_id.in_(todo))))
            doomed += kids
            todo = [k.id for k in kids]
        grades.delete_categories(g.db, doomed + [cat])
        g.db.commit()
        return jsonify(grade_summary(g.db.get(Course, cid)))

    def num_or_none(v):
        return None if v in (None, "") else float(v)

    @app.post("/api/grade-categories/<int:gid>/items")
    @login_required
    def add_grade_item(gid):
        cat = g.db.get(GradeCategory, gid) or abort(404)
        d = request.get_json(force=True)
        g.db.add(GradeItem(category_id=gid, name=d.get("name") or "Item", score=num_or_none(d.get("score")),
                           out_of=float(d.get("out_of") or 100)))
        g.db.commit()
        g.db.expire(cat)
        return jsonify(grade_summary(g.db.get(Course, cat.course_id))), 201

    @app.patch("/api/grade-items/<int:iid>")
    @login_required
    def update_grade_item(iid):
        it = g.db.get(GradeItem, iid) or abort(404)
        d = request.get_json(force=True)
        if "name" in d:
            it.name = d["name"]
        if "score" in d:
            it.score = num_or_none(d["score"])
        if "out_of" in d:
            it.out_of = float(d["out_of"] or 100)
        g.db.commit()
        cat = g.db.get(GradeCategory, it.category_id)
        g.db.expire(cat)
        return jsonify(grade_summary(g.db.get(Course, cat.course_id)))

    @app.delete("/api/grade-items/<int:iid>")
    @login_required
    def delete_grade_item(iid):
        it = g.db.get(GradeItem, iid) or abort(404)
        cat = g.db.get(GradeCategory, it.category_id)
        g.db.delete(it)
        g.db.commit()
        g.db.expire(cat)
        return jsonify(grade_summary(g.db.get(Course, cat.course_id)))

    # ---- Syllabus and attendance --------------------------------------------------------------

    @app.post("/api/courses/<int:cid>/syllabus/read")
    @login_required
    def read_syllabus(cid):
        g.db.get(Course, cid) or abort(404)
        f = request.files.get("file")
        text = request.form.get("text") or (request.get_json(silent=True) or {}).get("text")
        url = request.form.get("url") or (request.get_json(silent=True) or {}).get("url")
        try:
            if f is not None:
                data = f.read()
                if len(data) > 15 * 1024 * 1024:
                    return jsonify(error="file is too big (15 MB max)"), 400
                mime = f.mimetype or "application/pdf"
                if mime.startswith("text/") or f.filename.lower().endswith((".txt", ".md", ".html", ".htm")):
                    return jsonify(syllabus.read(text=scrapers.page_text(data.decode("utf-8", "ignore"))))
                return jsonify(syllabus.read(file_bytes=data, mime=mime))
            if url:
                if not url.startswith(("http://", "https://")):
                    return jsonify(error="that isn't a web address"), 400
                import requests as rq
                r = rq.get(url, timeout=30, headers={"User-Agent": "planner/1.0"})
                r.raise_for_status()
                if "pdf" in r.headers.get("content-type", "") or url.lower().endswith(".pdf"):
                    return jsonify(syllabus.read(file_bytes=r.content, mime="application/pdf"))
                return jsonify(syllabus.read(text=scrapers.page_text(r.text)))
            if text and text.strip():
                return jsonify(syllabus.read(text=text))
        except RuntimeError as e:
            return jsonify(error=str(e)), 502
        except Exception as e:
            return jsonify(error=f"Couldn't read it: {type(e).__name__}: {e}"[:300]), 502
        return jsonify(error="upload the syllabus, paste its text, or give its link"), 400

    @app.post("/api/courses/<int:cid>/syllabus/apply")
    @login_required
    def apply_syllabus(cid):
        c = g.db.get(Course, cid) or abort(404)
        d = request.get_json(force=True)
        if not isinstance(d.get("proposal", {}).get("categories"), list):
            return jsonify(error="nothing to apply"), 400
        syllabus.apply(g.db, c, d["proposal"], replace=d.get("replace", True))
        return jsonify(grade_summary(c))

    @app.post("/api/courses/<int:cid>/attendance")
    @token_or_login
    def mark_attendance(cid):
        """{"status": "present|absent|excused", "day": "2026-10-20"} adds a meeting; {"undo": "absent"} removes the latest."""
        c = g.db.get(Course, cid) or abort(404)
        d = request.get_json(force=True)
        if d.get("undo"):
            last = g.db.scalar(select(AttendanceLog).where(AttendanceLog.course_id == cid, AttendanceLog.status == d["undo"])
                               .order_by(AttendanceLog.day.desc(), AttendanceLog.id.desc()))
            if last:
                g.db.delete(last)
        elif d.get("status") in ("present", "absent", "excused"):
            day = parse_dt(d.get("day")) or service.local_now(g.db)
            day = day.replace(hour=0, minute=0)
            if d.get("once"):  # from Today: one mark per class per day
                for l in g.db.scalars(select(AttendanceLog).where(AttendanceLog.course_id == cid, AttendanceLog.day == day)):
                    g.db.delete(l)
            g.db.add(AttendanceLog(course_id=cid, day=day, status=d["status"]))
        else:
            return jsonify(error="status must be present, absent or excused"), 400
        g.db.commit()
        return jsonify(attendance=attendance_of(c), grades=grade_summary(c))

    @app.post("/api/courses/<int:cid>/test")
    @login_required
    def test_course(cid):
        """Fetch and parse the page without saving anything, so you can see what it would add."""
        c = g.db.get(Course, cid) or abort(404)
        if c.parser == "none" or not c.url:
            return jsonify(error="Add a schedule page URL first"), 400
        now = service.local_now(g.db)
        try:
            items, _, how = scrapers.extract(c, now.date())
        except Exception as e:
            return jsonify(error=f"{type(e).__name__}: {e}"[:500]), 502
        known = {t.external_id: t.status for t in g.db.scalars(
            select(Task).where(Task.external_id.in_([i.external_id for i in items])))}
        g.db.rollback()  # a test never changes the course (e.g. dropping a stale recipe)
        return jsonify(read_with=how, items=[{"title": i.title, "due": iso(i.due), "kind": i.kind, "past": i.due < now,
                               "status": known.get(i.external_id, "new")} for i in items])

    @app.post("/api/courses/<int:cid>/sync")
    @login_required
    def sync_one_course(cid):
        c = g.db.get(Course, cid) or abort(404)
        if c.parser == "none" or not c.url:
            return jsonify(error="Add a schedule page URL first"), 400
        result = scrapers.sync_course(g.db, c, service.local_now(g.db), force=True)
        service.replan(g.db)
        return jsonify(course=course_json(c), result=result)

    # ---- Typed areas (career, fitness, finance) ---------------------------------------------

    def area_or_404(aid):
        a = g.db.get(Course, aid) or abort(404)
        return a

    @app.get("/api/areas/<int:aid>/summary")
    @login_required
    def area_summary(aid):
        return jsonify(A.summary(g.db, area_or_404(aid), service.local_now(g.db)))

    @app.post("/api/areas/<int:aid>/entries")
    @token_or_login
    def add_area_entry(aid):
        a = area_or_404(aid)
        d = request.get_json(force=True)
        try:
            e = A.add_entry(g.db, a, d.get("type", ""), parse_dt(d.get("date")), d.get("data") or {})
        except ValueError as err:
            g.db.rollback()
            return jsonify(error=str(err)), 400
        service.replan(g.db)
        return jsonify(A.entry_json(e)), 201

    @app.patch("/api/entries/<int:eid>")
    @login_required
    def update_area_entry(eid):
        e = g.db.get(AreaEntry, eid) or abort(404)
        d = request.get_json(force=True)
        try:
            A.update_entry(g.db, e, parse_dt(d.get("date")), d.get("data"), clear_date="date" in d and not d["date"])
        except ValueError as err:
            g.db.rollback()
            return jsonify(error=str(err)), 400
        service.replan(g.db)
        return jsonify(A.entry_json(e))

    @app.delete("/api/entries/<int:eid>")
    @login_required
    def delete_area_entry(eid):
        e = g.db.get(AreaEntry, eid) or abort(404)
        A.delete_entry(g.db, e)
        service.replan(g.db)
        return jsonify(ok=True)

    @app.post("/api/fitness/estimate-meal")
    @token_or_login
    def estimate_meal():
        f = request.files.get("photo")
        if f is None:
            return jsonify(error="attach a photo"), 400
        img = f.read()
        if len(img) > 8 * 1024 * 1024:
            return jsonify(error="photo is too big (8 MB max)"), 400
        try:
            return jsonify(A.estimate_meal(img, f.mimetype or "image/jpeg", request.form.get("note", "")))
        except Exception as err:
            return jsonify(error=f"{err}"[:300]), 502

    @app.post("/api/health/ingest")
    @token_or_login
    def health_ingest():
        """Phone automations post daily numbers here: {"date": "2026-10-20", "sleep_minutes": 430, "steps": 8000, ...}."""
        d = request.get_json(force=True)
        fitness = next((c for c in scrapers.get_courses(g.db) if c.kind == "fitness"), None)
        if fitness is None:
            return jsonify(error="no Fitness area"), 400
        day = parse_dt(d.get("date")) or service.local_now(g.db)
        data = {k: d[k] for k in ("sleep_minutes", "steps", "weight", "resting_hr") if k in d}
        try:
            e = A.add_entry(g.db, fitness, "metric", day, data)
        except ValueError as err:
            return jsonify(error=str(err)), 400
        return jsonify(A.entry_json(e))

    @app.get("/api/token")
    @login_required
    def get_token():
        return jsonify(token=api_token())

    @app.post("/api/token")
    @login_required
    def rotate_token():
        tok = secrets.token_urlsafe(32)
        service.set_setting(g.db, "api_token", tok)
        g.db.commit()
        return jsonify(token=tok)

    # ---- Assistant ------------------------------------------------------------------------

    @app.post("/api/assistant")
    @login_required
    def assistant_chat():
        msg = (request.get_json(force=True).get("message") or "").strip()
        if not msg:
            return jsonify(error="say something"), 400
        try:
            return jsonify(assistant.chat(g.db, msg))
        except RuntimeError as e:
            return jsonify(error=str(e)), 502

    @app.post("/api/assistant/apply")
    @login_required
    def assistant_apply():
        return jsonify(results=assistant.apply(g.db, request.get_json(force=True).get("actions") or []))

    @app.get("/api/assistant")
    @login_required
    def assistant_history():
        from .models import AssistantMessage, AssistantNote
        msgs = list(g.db.scalars(select(AssistantMessage).order_by(AssistantMessage.id.desc()).limit(30)))[::-1]
        return jsonify(messages=[{"id": m.id, "role": m.role, "text": m.text, "actions": json.loads(m.actions or "[]"),
                                  "at": iso(m.created_at)} for m in msgs],
                       notes=[{"id": n.id, "text": n.text} for n in g.db.scalars(select(AssistantNote).order_by(AssistantNote.id))])

    @app.delete("/api/assistant/notes/<int:nid>")
    @login_required
    def assistant_forget(nid):
        from .models import AssistantNote
        n = g.db.get(AssistantNote, nid) or abort(404)
        g.db.delete(n)
        g.db.commit()
        return jsonify(ok=True)

    @app.delete("/api/assistant")
    @login_required
    def assistant_clear():
        from .models import AssistantMessage
        for m in g.db.scalars(select(AssistantMessage)):
            g.db.delete(m)
        g.db.commit()
        return jsonify(ok=True)

    # ---- Frontend ----------------------------------------------------------------------------

    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def spa(path):
        if path.startswith(("api/", "auth/")):
            abort(404)
        if (FRONTEND_DIST / path).is_file():
            return send_from_directory(FRONTEND_DIST, path)
        if (FRONTEND_DIST / "index.html").is_file():
            return send_from_directory(FRONTEND_DIST, "index.html")
        return "Frontend not built yet. Run `npm run build` in frontend/.", 200

    return app

