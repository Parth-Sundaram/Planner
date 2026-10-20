import React, { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import Today from "./Today.jsx";
import Week from "./Week.jsx";
import Tasks from "./Tasks.jsx";
import Settings from "./Settings.jsx";

const TABS = [
  ["today", "Today"],
  ["week", "Week"],
  ["tasks", "Tasks"],
  ["settings", "Settings"],
];

function tabFromHash() {
  const h = window.location.hash.replace("#", "");
  return TABS.some(([k]) => k === h) ? h : "today";
}

export default function App() {
  const [me, setMe] = useState(null);
  const [tab, setTab] = useState(tabFromHash);
  const [syncing, setSyncing] = useState(false);
  const [toast, setToast] = useState(null);
  const [version, setVersion] = useState(0); // bump to make views reload
  const refresh = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => { api.get("/api/me").then(setMe).catch(() => setMe({})); }, []);
  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const notify = useCallback((msg, kind = "ok") => {
    setToast({ msg, kind });
    setTimeout(() => setToast(null), 3500);
  }, []);

  async function sync() {
    setSyncing(true);
    try {
      const r = await api.post("/api/sync");
      const parts = [];
      for (const [code, c] of Object.entries(r.courses || {})) {
        parts.push(c.error ? `${code}: failed` : `${code}: +${c.created}`);
      }
      if (r.google_tasks) parts.push(`Google Tasks: +${r.google_tasks.created}`);
      if (r.google === "not connected") parts.push("Google not connected");
      notify(`Synced. ${parts.join(", ")}`);
      refresh();
    } catch (e) {
      notify(e.message, "error");
    } finally {
      setSyncing(false);
    }
  }

  if (!me) return <div className="center muted">Loading…</div>;
  if (!me.email) return <Login devLogin={me.dev_login} />;

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Planner</span>
        <nav>
          {TABS.map(([k, label]) => (
            <a key={k} href={`#${k}`} className={tab === k ? "active" : ""}>{label}</a>
          ))}
        </nav>
        <button className="btn ghost" onClick={sync} disabled={syncing}>
          {syncing ? "Syncing…" : "Sync"}
        </button>
      </header>
      <main>
        {tab === "today" && <Today version={version} refresh={refresh} notify={notify} />}
        {tab === "week" && <Week version={version} refresh={refresh} notify={notify} />}
        {tab === "tasks" && <Tasks version={version} refresh={refresh} notify={notify} />}
        {tab === "settings" && <Settings version={version} refresh={refresh} notify={notify} me={me} />}
      </main>
      {toast && <div className={`toast ${toast.kind}`}>{toast.msg}</div>}
    </div>
  );
}

function Login({ devLogin }) {
  return (
    <div className="center">
      <div className="card login">
        <h1>Planner</h1>
        <p className="muted">Your tasks and course deadlines, turned into a day plan that fixes itself.</p>
        <a className="btn primary" href="/auth/login">Sign in with Google</a>
        {devLogin && (
          <button className="btn ghost" onClick={async () => { await api.post("/auth/dev-login"); location.reload(); }}>
            Dev login (local only)
          </button>
        )}
      </div>
    </div>
  );
}
