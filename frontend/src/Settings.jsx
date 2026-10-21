import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./ui.jsx";

const DAYS = [["M", "Mon"], ["T", "Tue"], ["W", "Wed"], ["R", "Thu"], ["F", "Fri"], ["S", "Sat"], ["U", "Sun"]];
const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const KINDS = [["class", "Class"], ["meal", "Meal"], ["gym", "Gym"], ["chore", "Chore"], ["routine", "Other"]];

export default function Settings({ ctx }) {
  const [routines, setRoutines] = useState([]);
  const [rules, setRules] = useState([]);
  const [settings, setSettings] = useState(null);

  useEffect(() => {
    Promise.all([api.get("/api/routines"), api.get("/api/rules"), api.get("/api/settings")])
      .then(([r, ru, s]) => { setRoutines(r); setRules(ru); setSettings(s); })
      .catch((e) => ctx.notify(e.message, "error"));
  }, [ctx.version]);

  async function act(fn, msg) {
    try { await fn(); if (msg) ctx.notify(msg); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }
  if (!settings) return <div className="page muted">Loading…</div>;
  const put = (k) => (e) => act(() => api.put("/api/settings", { [k]: e.target.value }), "Saved. Replanned");

  return (
    <div className="page" style={{ maxWidth: 1000 }}>
      <div className="page-head"><div><h1>Settings</h1><div className="sub">Your week's shape. Course pages hold their own settings.</div></div></div>

      <div className="set-card">
        <h3>Google</h3>
        {ctx.me.google_connected
          ? <p className="muted" style={{ margin: 0 }}>Connected as <b>{ctx.me.email}</b>. The plan goes to a calendar called <b>Plan (auto)</b>, and your other events count as busy.</p>
          : <p className="muted" style={{ margin: 0 }}>Not connected. <a href="/auth/login">Connect Google</a> to read your calendar, import Google Tasks, and publish the plan.</p>}
        <label className="field">Calendars that count as busy (IDs, comma separated)
          <input defaultValue={settings.busy_calendar_ids} onBlur={put("busy_calendar_ids")} />
        </label>
      </div>

      <div className="set-card">
        <h3>Working hours</h3>
        <div className="row">
          <label className="field inline">Plan work from<input type="time" defaultValue={settings.day_start} onBlur={put("day_start")} /></label>
          <label className="field inline">until<input type="time" defaultValue={settings.day_end} onBlur={put("day_end")} /></label>
        </div>
      </div>

      <div className="set-card">
        <h3>Nutrition targets</h3>
        <div className="row">
          <label className="field inline">Calories / day<input type="number" defaultValue={settings.calorie_target || 2500} onBlur={put("calorie_target")} style={{ width: 100 }} /></label>
          <label className="field inline">Protein / day (g)<input type="number" defaultValue={settings.protein_target || 150} onBlur={put("protein_target")} style={{ width: 90 }} /></label>
        </div>
      </div>

      <PhoneAndExtension ctx={ctx} />

      <div className="set-card">
        <h3>Weekly routine</h3>
        <p className="muted small" style={{ margin: 0 }}>Fixed things (classes) use a window equal to their length. Meals, gym and chores get a window and float inside it.</p>
        <div>
          {routines.map((r) => <RoutineRow key={r.id} r={r} act={act} />)}
          <RoutineRow r={{ title: "", days: "MTWRF", window_start: "12:00", window_end: "13:45", duration: 45, kind: "meal", active: true }} act={act} isNew />
        </div>
      </div>

      <div className="set-card">
        <h3>Deadline rules</h3>
        <p className="muted small" style={{ margin: 0 }}>Tasks with the tag must be done by that day and time of the week they're due in.</p>
        {rules.map((r) => (
          <div key={r.id} className="row between">
            <span><code>#{r.tag}</code> done by {WEEKDAYS[r.weekday]} {r.time} {r.description && <span className="muted small">· {r.description}</span>}</span>
            <button className="icon-btn" onClick={() => act(() => api.del(`/api/rules/${r.id}`), "Removed")} aria-label="Remove"><Icon name="trash" size={14} /></button>
          </div>
        ))}
        <NewRule act={act} />
      </div>
    </div>
  );
}

function RoutineRow({ r, act, isNew }) {
  const [f, setF] = useState(r);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "number" ? Number(e.target.value) : e.target.value });
  const toggleDay = (d) => setF({ ...f, days: f.days.includes(d) ? f.days.replace(d, "") : f.days + d });
  const changed = JSON.stringify(f) !== JSON.stringify(r);
  async function save() {
    if (!f.title) return;
    if (isNew) { await act(() => api.post("/api/routines", f), "Added. Replanned"); setF(r); }
    else await act(() => api.patch(`/api/routines/${r.id}`, f), "Saved. Replanned");
  }
  return (
    <div className="routine-row">
      <input value={f.title} onChange={set("title")} placeholder={isNew ? "New, e.g. Lunch" : ""} style={{ width: 170 }} />
      <select value={f.kind} onChange={set("kind")}>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      <span className="days">
        {DAYS.map(([d, label]) => (
          <button type="button" key={d} className={`day ${f.days.includes(d) ? "on" : ""}`} onClick={() => toggleDay(d)} title={label}>{label[0]}</button>
        ))}
      </span>
      <input type="time" value={f.window_start} onChange={set("window_start")} aria-label="Window start" />
      <span className="faint">–</span>
      <input type="time" value={f.window_end} onChange={set("window_end")} aria-label="Window end" />
      <input type="number" value={f.duration} onChange={set("duration")} aria-label="Minutes" style={{ width: 64 }} /><span className="faint small">min</span>
      {(isNew || changed) && <button className="btn sm primary" onClick={save}>{isNew ? "Add" : "Save"}</button>}
      {!isNew && <button className="icon-btn" onClick={() => act(() => api.del(`/api/routines/${r.id}`), "Removed")} aria-label="Remove"><Icon name="trash" size={14} /></button>}
    </div>
  );
}

function NewRule({ act }) {
  const [f, setF] = useState({ tag: "", weekday: 2, time: "23:59", description: "" });
  return (
    <div className="row">
      <input value={f.tag} onChange={(e) => setF({ ...f, tag: e.target.value.replace("#", "") })} placeholder="tag, e.g. db" style={{ width: 120 }} />
      <select value={f.weekday} onChange={(e) => setF({ ...f, weekday: Number(e.target.value) })}>
        {WEEKDAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
      </select>
      <input type="time" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} />
      <input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} placeholder="why (optional)" className="grow" />
      <button className="btn sm" onClick={() => f.tag && act(() => api.post("/api/rules", f), "Rule added").then(() => setF({ ...f, tag: "", description: "" }))}>Add rule</button>
    </div>
  );
}

function PhoneAndExtension({ ctx }) {
  const [tok, setTok] = useState(null);
  const [shown, setShown] = useState(false);
  useEffect(() => { api.get("/api/token").then((d) => setTok(d.token)).catch(() => {}); }, []);
  const origin = window.location.origin;
  async function rotate() {
    if (tok && !confirm("Make a new key? Anything using the old one (phone shortcut, extension) stops working until you update it.")) return;
    const d = await api.post("/api/token"); setTok(d.token); setShown(true); ctx.notify("New key created");
  }
  return (
    <div className="set-card">
      <h3>Phone & extension</h3>
      <p className="muted small" style={{ margin: 0 }}>A private key lets your phone send sleep and steps, and lets the browser extension add tasks, without your Google login. Treat it like a password.</p>
      <div className="row">
        <code style={{ padding: "5px 8px" }}>{tok ? (shown ? tok : "••••••••••••" + tok.slice(-4)) : "no key yet"}</code>
        {tok && <button className="btn sm" onClick={() => setShown(!shown)}>{shown ? "Hide" : "Show"}</button>}
        {tok && <button className="btn sm" onClick={() => { navigator.clipboard?.writeText(tok); ctx.notify("Copied"); }}>Copy</button>}
        <button className="btn sm" onClick={rotate}>{tok ? "Make a new key" : "Create key"}</button>
      </div>
      <details className="adv">
        <summary>Send sleep and steps from your phone</summary>
        <div className="small stack" style={{ gap: 6 }}>
          <div><b>iPhone (Shortcuts app):</b> New personal automation → Time of Day 9:00 AM daily → actions: <i>Find Health Samples</i> (Sleep, last night) and <i>Find Health Samples</i> (Steps, yesterday) → <i>Get Contents of URL</i>:</div>
          <code style={{ whiteSpace: "pre-wrap", display: "block", padding: 8 }}>{`POST ${origin}/api/health/ingest
Header  Authorization: Bearer <your key>
JSON    {"date": "<yesterday>", "sleep_minutes": <sleep total>, "steps": <steps total>}`}</code>
          <div><b>Android:</b> GloryFit writes to Health Connect. Any "Health Connect to webhook" exporter can POST the same JSON on a schedule.</div>
          <div className="faint">Either way the numbers show up on the Fitness page.</div>
        </div>
      </details>
    </div>
  );
}
