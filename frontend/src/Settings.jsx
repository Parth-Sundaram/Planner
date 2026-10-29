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

      <MoodleCard ctx={ctx} />

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
        <summary>Send sleep, steps and heart rate from your phone</summary>
        <div className="small stack" style={{ gap: 6 }}>
          <HealthStatus />
          <div><b>iPhone (Shortcuts app):</b> New personal automation → Time of Day 9:00 AM daily → actions: <i>Find Health Samples</i> (Sleep, last night) and <i>Find Health Samples</i> (Steps, yesterday) → <i>Get Contents of URL</i>:</div>
          <code style={{ whiteSpace: "pre-wrap", display: "block", padding: 8 }}>{`POST ${origin}/api/health/ingest
Header  Authorization: Bearer <your key>
JSON    {"date": "<yesterday>", "sleep_minutes": <sleep total>, "steps": <steps total>}`}</code>
          <div><b>Android (Health Connect, recommended):</b></div>
          <ol style={{ margin: 0, paddingLeft: 18 }}>
            <li>In GloryFit, turn on syncing to <i>Health Connect</i> (sleep, steps, heart rate).</li>
            <li>Install <b>HC Webhook</b> (Play Store: "Health Connect Webhook", package com.hcwebhook.app) and allow it to read Steps, Sleep, Heart rate, Resting heart rate, Exercise and Weight.</li>
            <li>Add a webhook: URL <code>{origin}/api/health/connect</code>, format JSON, and a custom header <code>Authorization</code> = <code>Bearer &lt;your key&gt;</code>.</li>
            <li>Set it to sync every hour (or at least every morning), then tap <i>Test</i>.</li>
          </ol>
          <div className="faint">Sleep shows on the day you wake up; workouts from the watch appear on the Fitness page and tick your Gym habit.</div>
          <div className="faint">Either way the numbers show up on the Fitness page.</div>
        </div>
      </details>
    </div>
  );
}

function MoodleCard({ ctx }) {
  const [m, setM] = useState(null);
  const [f, setF] = useState({ ical_url: "", base_url: "", token: "" });
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/api/moodle").then((d) => { setM(d); setF({ ical_url: d.ical_url, base_url: d.base_url, token: "" }); }).catch(() => {});
  useEffect(() => { load(); }, [ctx.version]);
  if (!m) return null;
  async function save() {
    try {
      const body = { ical_url: f.ical_url, base_url: f.base_url, ...(f.token ? { token: f.token } : {}) };
      setM(await api.put("/api/moodle", body)); setF({ ...f, token: "" }); ctx.notify("Saved");
    } catch (e) { ctx.notify(e.message, "error"); }
  }
  async function sync() {
    setBusy(true);
    try {
      const r = await api.post("/api/moodle/sync");
      ctx.notify(r.error ? `Moodle: ${r.error}` : `Moodle (${r.via}): found ${r.found}, added ${r.created}` +
        `${r.suggested ? `, ${r.suggested} need a course (below)` : ""}${r.completed ? `, ${r.completed} marked done` : ""}${r.grades_filled ? `, ${r.grades_filled} grades filled` : ""}`, r.error ? "error" : "ok");
      load(); ctx.refresh();
    } catch (e) { ctx.notify(e.message, "error"); }
    setBusy(false);
  }
  async function mapTo(name, value) {
    try { setM(await api.put("/api/moodle", { map: { [name]: value === "" ? null : value === "ignore" ? "ignore" : Number(value) } })); ctx.notify("Saved. Sync to apply"); }
    catch (e) { ctx.notify(e.message, "error"); }
  }
  const courses = ctx.courses.filter((c) => c.kind === "course");
  const last = m.last || {};
  return (
    <div className="set-card">
      <h3>Moodle</h3>
      <p className="muted small" style={{ margin: 0 }}>Brings every Moodle assignment and quiz into your plan on each Sync. Use either one; the key gets more (quizzes not on the calendar, grades, and it marks things done when Moodle shows them submitted).</p>
      <label className="field">Calendar link <span className="faint">Moodle → Calendar → Export calendar → All courses, Recent and next 60 days → Get calendar URL</span>
        <input value={f.ical_url} onChange={(e) => setF({ ...f, ical_url: e.target.value })} placeholder="https://moodle.rose-hulman.edu/calendar/export_execute.php?userid=…&authtoken=…" />
      </label>
      <div className="row">
        <label className="field grow">Moodle site<input value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })} placeholder="https://moodle.rose-hulman.edu" /></label>
        <label className="field grow">Web service key {m.has_token && <span className="faint">(saved {m.token_hint})</span>}
          <input type="password" value={f.token} onChange={(e) => setF({ ...f, token: e.target.value })} placeholder={m.has_token ? "leave empty to keep" : "optional"} /></label>
      </div>
      <details className="adv">
        <summary>How to get a web service key</summary>
        <div className="small stack" style={{ gap: 6 }}>
          <div><b>Try first:</b> Moodle → your name → Preferences → <i>Security keys</i>. If there's a key for "Moodle mobile web service", copy it.</div>
          <div><b>If that page doesn't exist</b> (school sign-in sites often hide it): while logged in to Moodle, open
            <code style={{ display: "block", margin: "4px 0", whiteSpace: "pre-wrap" }}>{(f.base_url || "https://moodle.rose-hulman.edu") + "/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=12345&urlscheme=moodlemobile"}</code>
            The browser will try to open a "moodlemobile://token=…" link and fail. Press F12 → Network, find that request, and copy the part after <code>token=</code>. Paste it here as is; the planner decodes it.</div>
          <div className="faint">If the site has the mobile app turned off, use the calendar link instead.</div>
        </div>
      </details>
      <div className="row">
        <button className="btn" onClick={save}>Save</button>
        <button className="btn primary" onClick={sync} disabled={busy}>{busy ? "Syncing…" : "Sync Moodle now"}</button>
        {last.at && <span className={`small ${last.error ? "err" : "muted"}`}>Last: {last.error ? last.error : `${last.via}, ${last.found} items`} · {new Date(last.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</span>}
      </div>
      {m.seen.length > 0 && (
        <div>
          <div className="subhead" style={{ marginTop: 6 }}>Which course is which</div>
          {m.seen.map((s) => {
            const value = s.mapped ?? (s.guess ? String(s.guess) : "");
            return (
              <div key={s.name} className="row between" style={{ padding: "4px 0", borderBottom: "1px solid var(--line)" }}>
                <span className="ellipsis" style={{ maxWidth: 380 }}>{s.name}{s.fullname && s.fullname !== s.name ? <span className="faint"> · {s.fullname}</span> : null}</span>
                <select value={String(value)} onChange={(e) => mapTo(s.name, e.target.value)}>
                  <option value="">Not set{s.guess ? "" : " (items wait in Tasks for review)"}</option>
                  {courses.map((c) => <option key={c.id} value={String(c.id)}>{c.name}{!s.mapped && s.guess === c.id ? " (guessed)" : ""}</option>)}
                  <option value="ignore">Ignore this course</option>
                </select>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function HealthStatus() {
  const [h, setH] = useState(null);
  useEffect(() => { api.get("/api/health/status").then(setH).catch(() => {}); }, []);
  if (!h || !h.at) return <div className="faint">Nothing received from your phone yet.</div>;
  return <div className="muted">Last received {new Date(h.at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}: {Object.entries(h.received || {}).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ") || "no new records"}.</div>;
}
