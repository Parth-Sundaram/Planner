import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmtTime } from "./api.js";
import { Icon } from "./ui.jsx";
import Today from "./Today.jsx";
import Calendar from "./Calendar.jsx";
import Tasks from "./Tasks.jsx";
import { CourseList, CoursePage } from "./Courses.jsx";
import Settings from "./Settings.jsx";
import TaskPanel from "./TaskPanel.jsx";
import BlockPanel from "./BlockPanel.jsx";

function parseRoute() {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [page, id] = h.split("/");
  if (page === "week") return { page: "calendar" };
  if (["today", "calendar", "tasks", "courses", "settings"].includes(page)) return { page, id: id ? Number(id) : null };
  return { page: "today" };
}

export default function App() {
  const [me, setMe] = useState(null);
  const [route, setRoute] = useState(parseRoute);
  const [version, setVersion] = useState(0);
  const [toast, setToast] = useState(null);
  const [courses, setCourses] = useState([]);
  const [aiReady, setAiReady] = useState(false);
  const [inbox, setInbox] = useState(0);
  const [panel, setPanel] = useState(null);   // {type: "task", id} | {type: "block", block}
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState(null);
  const [navOpen, setNavOpen] = useState(false);

  const refresh = useCallback(() => setVersion((v) => v + 1), []);
  const notify = useCallback((msg, kind = "ok") => {
    setToast({ msg, kind });
    clearTimeout(window.__toastT);
    window.__toastT = setTimeout(() => setToast(null), kind === "error" ? 7000 : 3500);
  }, []);

  useEffect(() => { api.get("/api/me").then(setMe).catch(() => setMe({})); }, []);
  useEffect(() => {
    const on = () => { setRoute(parseRoute()); setNavOpen(false); };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  useEffect(() => {
    if (!me?.email) return;
    api.get("/api/courses").then((d) => { setCourses(d.courses); setAiReady(d.ai_ready); }).catch(() => {});
    api.get("/api/tasks?status=suggested").then((t) => setInbox(t.length)).catch(() => {});
  }, [me, version]);

  const courseById = useMemo(() => Object.fromEntries(courses.map((c) => [c.id, c])), [courses]);

  async function sync() {
    setSyncing(true);
    try {
      const r = await api.post("/api/sync");
      const parts = [];
      const failed = Object.entries(r.courses || {}).filter(([, c]) => c.error).map(([n]) => n);
      const added = Object.values(r.courses || {}).reduce((n, c) => n + (c.created || 0), 0);
      const review = Object.values(r.courses || {}).reduce((n, c) => n + (c.suggested || 0), 0);
      if (added) parts.push(`${added} new deadline${added > 1 ? "s" : ""}`);
      if (review) parts.push(`${review} to review`);
      if (r.google_tasks?.created) parts.push(`${r.google_tasks.created} from Google Tasks`);
      if (r.google === "not connected") parts.push("Google not connected");
      if (failed.length) parts.push(`couldn't read ${failed.join(", ")} (see its course page)`);
      notify(`Synced${parts.length ? ": " + parts.join(", ") : ". Everything's up to date"}`, failed.length ? "error" : "ok");
      setLastSync(new Date());
      refresh();
    } catch (e) {
      notify(e.message, "error");
    } finally {
      setSyncing(false);
    }
  }

  if (!me) return <div className="center muted">Loading…</div>;
  if (!me.email) return <Login devLogin={me.dev_login} />;

  const ctx = {
    version, refresh, notify, courses, courseById, aiReady, me,
    openTask: (id) => setPanel({ type: "task", id }),
    openBlock: (block) => setPanel({ type: "block", block }),
  };
  const nav = (href, icon, label, active, extra) => (
    <a className={`nav-item ${active ? "active" : ""}`} href={href}>
      <Icon name={icon} />{label}{extra}
    </a>
  );

  return (
    <div className="shell">
      <div className="mobile-bar">
        <button className="icon-btn" onClick={() => setNavOpen(true)} aria-label="Menu"><Icon name="menu" /></button>
        <b>Planner</b>
        <span className="grow" />
        <button className="btn sm" onClick={sync} disabled={syncing}><Icon name="sync" />{syncing ? "Syncing…" : "Sync"}</button>
      </div>
      {navOpen && <div className="scrim" onClick={() => setNavOpen(false)} />}
      <nav className={`sidebar ${navOpen ? "open" : ""}`}>
        <div className="brand"><span className="brand-mark">P</span>Planner</div>
        {nav("#/today", "today", "Today", route.page === "today")}
        {nav("#/calendar", "calendar", "Calendar", route.page === "calendar")}
        {nav("#/tasks", "tasks", "Tasks", route.page === "tasks", inbox > 0 && <span className="count" title="Waiting for review">{inbox}</span>)}
        <div className="nav-label">
          <a href="#/courses" style={{ textDecoration: "none" }}>Courses</a>
          <a className="icon-btn" href="#/courses" title="All courses"><Icon name="book" size={14} /></a>
        </div>
        {courses.map((c) => (
          <a key={c.id} className={`nav-item c-${c.color} ${route.page === "courses" && route.id === c.id ? "active" : ""}`} href={`#/courses/${c.id}`}>
            <span className="dot" /><span className="ellipsis">{c.name}</span>
          </a>
        ))}
        <div className="sidebar-foot">
          <button className="nav-item" onClick={sync} disabled={syncing} style={{ border: 0, background: "transparent", width: "100%" }}>
            <Icon name="sync" />{syncing ? "Syncing…" : "Sync now"}
            <span className="faint small" style={{ marginLeft: "auto" }}>{lastSync ? fmtTime(lastSync.toISOString()) : ""}</span>
          </button>
          {nav("#/settings", "settings", "Settings", route.page === "settings")}
          <div className="faint small ellipsis" style={{ padding: "2px 8px" }}>{me.email}</div>
        </div>
      </nav>
      <main className="main">
        {route.page === "today" && <Today ctx={ctx} />}
        {route.page === "calendar" && <Calendar ctx={ctx} />}
        {route.page === "tasks" && <Tasks ctx={ctx} />}
        {route.page === "courses" && (route.id ? <CoursePage key={route.id} id={route.id} ctx={ctx} /> : <CourseList ctx={ctx} />)}
        {route.page === "settings" && <Settings ctx={ctx} />}
      </main>
      {panel?.type === "task" && <TaskPanel key={panel.id} id={panel.id} ctx={ctx} onClose={() => setPanel(null)} />}
      {panel?.type === "block" && <BlockPanel key={panel.block.id + panel.block.kind} block={panel.block} ctx={ctx} onClose={() => setPanel(null)} />}
      {toast && <div className={`toast ${toast.kind}`} role="status">{toast.msg}</div>}
    </div>
  );
}

function Login({ devLogin }) {
  return (
    <div className="center">
      <div className="login">
        <div className="brand" style={{ justifyContent: "center" }}><span className="brand-mark">P</span>Planner</div>
        <p className="muted">Your tasks and course deadlines, turned into a day plan that fixes itself.</p>
        <a className="btn primary" href="/auth/login">Sign in with Google</a>
        {devLogin && (
          <button className="btn" onClick={async () => { await api.post("/auth/dev-login"); location.reload(); }}>
            Dev login (local only)
          </button>
        )}
      </div>
    </div>
  );
}
