import React, { useEffect, useState } from "react";
import { api } from "./api.js";

const DAYS = [["M", "Mon"], ["T", "Tue"], ["W", "Wed"], ["R", "Thu"], ["F", "Fri"], ["S", "Sat"], ["U", "Sun"]];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const KINDS = ["class", "meal", "gym", "chore", "routine"];

export default function Settings({ version, refresh, notify, me }) {
  const [routines, setRoutines] = useState([]);
  const [rules, setRules] = useState([]);
  const [settings, setSettings] = useState(null);

  useEffect(() => {
    Promise.all([api.get("/api/routines"), api.get("/api/rules"), api.get("/api/settings")])
      .then(([r, ru, s]) => { setRoutines(r); setRules(ru); setSettings(s); })
      .catch((e) => notify(e.message, "error"));
  }, [version]);

  async function act(fn, msg) {
    try { await fn(); if (msg) notify(msg); refresh(); } catch (e) { notify(e.message, "error"); }
  }
  if (!settings) return <div className="muted">Loading…</div>;

  return (
    <div className="settings">
      <section className="card">
        <h3>Google</h3>
        {me.google_connected
          ? <p>Connected as <b>{me.email}</b>. Your plan is written to the <b>Plan (auto)</b> calendar.</p>
          : <p className="muted">Not connected. <a href="/auth/login">Connect Google</a> to read your calendar, import Google Tasks, and push the plan.</p>}
        <label>Calendars to treat as busy (comma separated IDs)
          <input defaultValue={settings.busy_calendar_ids}
                 onBlur={(e) => act(() => api.put("/api/settings", { busy_calendar_ids: e.target.value }), "Saved")} />
        </label>
      </section>

      <section className="card">
        <h3>Working hours</h3>
        <div className="grid2">
          <label>Day starts<input type="time" defaultValue={settings.day_start}
                 onBlur={(e) => act(() => api.put("/api/settings", { day_start: e.target.value }), "Saved")} /></label>
          <label>Day ends<input type="time" defaultValue={settings.day_end}
                 onBlur={(e) => act(() => api.put("/api/settings", { day_end: e.target.value }), "Saved")} /></label>
        </div>
      </section>

      <section className="card">
        <h3>Weekly routine</h3>
        <p className="muted small">Fixed classes use the same start and end as their length. Meals and gym get a window and float inside it.</p>
        {routines.map((r) => <RoutineRow key={r.id} r={r} act={act} />)}
        <RoutineRow r={{ title: "", days: "MTWRF", window_start: "12:00", window_end: "13:45", duration: 45, kind: "meal", active: true }} act={act} isNew />
      </section>

      <section className="card">
        <h3>Deadline rules</h3>
        <p className="muted small">Tasks with the tag must be done by this day and time of their due week.</p>
        {rules.map((r) => (
          <div key={r.id} className="row">
            <span><code>#{r.tag}</code> → done by {WEEKDAYS[r.weekday]} {r.time} {r.description && <span className="muted">({r.description})</span>}</span>
            <button className="btn ghost small" onClick={() => act(() => api.del(`/api/rules/${r.id}`), "Removed")}>Remove</button>
          </div>
        ))}
        <NewRule act={act} />
      </section>
    </div>
  );
}

function RoutineRow({ r, act, isNew }) {
  const [f, setF] = useState(r);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "number" ? Number(e.target.value) : e.target.value });
  const toggleDay = (d) => setF({ ...f, days: f.days.includes(d) ? f.days.replace(d, "") : f.days + d });
  async function save() {
    if (!f.title) return;
    if (isNew) { await act(() => api.post("/api/routines", f), "Added"); setF(r); }
    else await act(() => api.patch(`/api/routines/${r.id}`, f), "Saved");
  }
  return (
    <div className="routine">
      <input value={f.title} onChange={set("title")} placeholder={isNew ? "New: e.g. Lunch" : ""} />
      <select value={f.kind} onChange={set("kind")}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select>
      <span className="days">
        {DAYS.map(([d, label]) => (
          <button type="button" key={d} className={`day ${f.days.includes(d) ? "on" : ""}`} onClick={() => toggleDay(d)}>{label[0]}</button>
        ))}
      </span>
      <input type="time" value={f.window_start} onChange={set("window_start")} aria-label="Window start" />
      <span className="muted">to</span>
      <input type="time" value={f.window_end} onChange={set("window_end")} aria-label="Window end" />
      <input type="number" value={f.duration} onChange={set("duration")} aria-label="Minutes" style={{ width: 70 }} /> <span className="muted small">min</span>
      <button className="btn small primary" onClick={save}>{isNew ? "Add" : "Save"}</button>
      {!isNew && <button className="btn small ghost" onClick={() => act(() => api.del(`/api/routines/${r.id}`), "Removed")}>Remove</button>}
    </div>
  );
}

function NewRule({ act }) {
  const [f, setF] = useState({ tag: "", weekday: 2, time: "23:59", description: "" });
  return (
    <div className="row">
      <input value={f.tag} onChange={(e) => setF({ ...f, tag: e.target.value.replace("#", "") })} placeholder="tag, e.g. db" />
      <select value={f.weekday} onChange={(e) => setF({ ...f, weekday: Number(e.target.value) })}>
        {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
      </select>
      <input type="time" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} />
      <input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} placeholder="why (optional)" />
      <button className="btn small primary" onClick={() => f.tag && act(() => api.post("/api/rules", f), "Rule added").then(() => setF({ ...f, tag: "", description: "" }))}>Add rule</button>
    </div>
  );
}
