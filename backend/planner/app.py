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

from . import gsync, service
from .models import Block, ExternalEvent, Routine, Rule, Task, make_session_factory

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # Google may return scopes in a different order

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

TASK_FIELDS = {"title", "course", "est_minutes", "done_minutes", "min_total_minutes", "priority",
               "splittable", "min_block", "max_block", "notes", "status", "position", "sequential"}
ROUTINE_FIELDS = {"title", "days", "window_start", "window_end", "duration", "kind", "active"}
RULE_FIELDS = {"tag", "weekday", "time", "description"}
SETTING_KEYS = {"timezone", "day_start", "day_end", "horizon_days", "busy_calendar_ids", "courses"}


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


def block_json(b: Block) -> dict:
    return {"id": b.id, "title": b.title, "start": iso(b.start), "end": iso(b.end), "kind": b.kind,
            "task_id": b.task_id, "routine_id": b.routine_id, "locked": b.locked,
            "status": b.status, "done_minutes": b.done_minutes}


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
        if status != "all":
            q = q.where(Task.status == status)
        r = rules()
        return jsonify([task_tree_json(g.db, t, r) for t in g.db.scalars(q)])

    def apply_task_fields(t: Task, data: dict):
        for k in TASK_FIELDS & data.keys():
            setattr(t, k, data[k])
        if "due" in data:
            t.due = parse_dt(data["due"])
        if "earliest_start" in data:
            t.earliest_start = parse_dt(data["earliest_start"])
        if "tags" in data:
            tags = data["tags"]
            t.tags = ",".join(x.strip().lstrip("#").lower() for x in (tags if isinstance(tags, list) else tags.split(",")) if x.strip())
        if "est_minutes" in data:
            t.needs_estimate = False

    @app.post("/api/tasks")
    @login_required
    def create_task():
        data = request.get_json(force=True)
        if data.get("text"):  # quick add: "CNN 4/4 ~4h !1 #ai"
            title, meta = service.parse_meta(data["text"])
            data = {**meta, **{k: v for k, v in data.items() if k != "text"}, "title": title}
            if isinstance(data.get("tags"), list):
                data["tags"] = data["tags"]
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
            t.parent_id, t.course = parent.id, parent.course
            t.position = service.next_position(g.db, parent.id)
            if parent.status == "done":
                parent.status = "open"
        apply_task_fields(t, data)
        if "est_minutes" not in data:
            t.est_minutes, t.needs_estimate = 60, True
        g.db.add(t)
        g.db.commit()
        plan = service.replan(g.db)
        return jsonify(task=task_json(t, rules()), plan=plan), 201

    @app.patch("/api/tasks/<int:tid>")
    @login_required
    def update_task(tid):
        t = g.db.get(Task, tid) or abort(404)
        data = request.get_json(force=True)
        old_due = t.due
        apply_task_fields(t, data)
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
        return jsonify(blocks=[block_json(b) for b in blocks],
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
        return jsonify(now=iso(now), blocks=[block_json(b) for b in blocks],
                       events=[{"id": e.id, "title": e.title, "start": iso(e.start), "end": iso(e.end)} for e in events],
                       remaining_task_minutes=remaining, needs_checkin=needs_checkin,
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

