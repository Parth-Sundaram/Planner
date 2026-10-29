import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./ui.jsx";

const DAYS = [["M", "Mon"], ["T", "Tue"], ["W", "Wed"], ["R", "Thu"], ["F", "Fri"], ["S", "Sat"], ["U", "Sun"]];

function useHabits(ctx) {
  const [d, setD] = useState(null);
  const load = () => api.get("/api/habits").then(setD).catch(() => {});
  useEffect(() => { load(); }, [ctx.version]);
  const tap = async (h, body = {}) => {
    try { setD(await api.post(`/api/habits/${h.id}/tap`, body)); } catch (e) { ctx.notify(e.message, "error"); }
  };
  return [d, setD, tap, load];
}

function fmtValue(h) {
  const v = h.today.value;
  if (h.source === "sleep" && v != null) return `${Math.floor(v / 60)}h ${Math.round(v % 60)}m`;
  if (h.source === "steps" && v != null) return `${Math.round(v).toLocaleString()} steps`;
  if (h.source === "meals_logged") return `${v ?? 0}/${h.target || 3} meals`;
  if (!h.source && h.target) return `${v || 0}/${h.target}`;
  return "";
}

/* The strip on Today: one tap per habit. */
export function HabitStrip({ ctx }) {
  const [d, , tap] = useHabits(ctx);
  if (!d || d.habits.length === 0) return null;
  const today = d.habits.filter((h) => h.today.applies);
  const done = today.filter((h) => h.today.done).length;
  return (
    <div>
      <div className="section-title">Habits today <span className="faint small">{done}/{today.length}</span></div>
      <div className="habit-list">
        {today.map((h) => {
          const counter = !h.source && h.target;
          return (
            <div key={h.id} className={`habit ${h.today.done ? "done" : ""}`}>
              <button className={`check ${h.today.done ? "on" : ""}`} aria-label={h.today.done ? "Undo" : "Done"}
                      onClick={() => tap(h, h.today.done && !counter ? { delta: -1 } : {})}
                      title={h.source && !h.today.done ? `Ticks itself ${h.source_label}, or tap if you did it` : ""}>
                {h.today.done && <Icon name="check" size={12} />}
              </button>
              <span className="grow ellipsis">{h.name}</span>
              {counter && !h.today.done && <button className="btn sm" onClick={() => tap(h)}>+1</button>}
              <span className="faint small">{fmtValue(h)}{h.per_week ? ` ${h.this_week}/${h.per_week} this week` : ""}</span>
              {h.streak > 1 && <span className="streak" title="Streak">{h.streak_label}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* Personal → Habits: the last 4 weeks, and adding/editing habits. */
export function HabitsPage({ ctx }) {
  const [d, setD, tap] = useHabits(ctx);
  const [f, setF] = useState({ name: "", source: "", target: "", days: "MTWRFSU", per_week: "" });
  const [editing, setEditing] = useState(null);
  if (!d) return <div className="muted">Loading…</div>;
  const names = new Set(d.habits.map((h) => h.name));
  const missing = d.suggested.filter((s) => !names.has(s.name));

  async function add(e) {
    e.preventDefault();
    if (!f.name.trim()) return;
    try { setD(await api.post("/api/habits", f)); setF({ name: "", source: "", target: "", days: "MTWRFSU", per_week: "" }); ctx.notify("Habit added"); }
    catch (err) { ctx.notify(err.message, "error"); }
  }
  async function save(h, body, msg = "Saved") {
    try { setD(await api.patch(`/api/habits/${h.id}`, body)); ctx.notify(msg); } catch (err) { ctx.notify(err.message, "error"); }
  }
  const sourceOpts = Object.entries(d.sources);

  return (
    <div className="stack" style={{ gap: 18 }}>
      {missing.length > 0 && (
        <div className="box pad stack" style={{ gap: 8 }}>
          <b>Start with these?</b>
          <div className="small muted">Built from what you said you're committing to. The ones marked "auto" tick themselves from your attendance, Health Connect and meal logs.</div>
          <div className="links">{missing.map((s) => <span key={s.name} className="tag">{s.name}{s.source ? " · auto" : ""}</span>)}</div>
          <div><button className="btn primary" onClick={async () => { setD(await api.post("/api/habits/suggested")); ctx.notify("Added. They're on Today"); }}>Add all {missing.length}</button></div>
        </div>
      )}

      {d.habits.length > 0 && (
        <div className="table-wrap">
          <table className="grid habits-grid">
            <thead>
              <tr><th>Habit</th>
                {d.habits[0].grid.map((g) => <th key={g.date} className="hg-day" title={g.date}>{new Date(g.date + "T12:00").toLocaleDateString([], { weekday: "narrow" })}</th>)}
                <th className="num">Streak</th><th className="num">Rate</th><th /></tr>
            </thead>
            <tbody>
              {d.habits.map((h) => (
                <tr key={h.id}>
                  <td style={{ minWidth: 180 }}>
                    <div>{h.name}</div>
                    <div className="faint small">{h.source ? h.source_label : h.target ? `${h.target} a day` : "tap to tick"}{h.per_week ? ` · ${h.per_week}× a week` : ""}{h.days !== "MTWRFSU" ? ` · ${h.days}` : ""}</div>
                  </td>
                  {h.grid.map((g) => (
                    <td key={g.date} className="hg-day">
                      <button className={`hg ${!g.applies ? "na" : g.done ? (g.auto ? "auto" : "yes") : "no"}`}
                              title={`${g.date}${g.applies ? (g.done ? " · done" : "") : " · not scheduled"}`}
                              disabled={!g.applies}
                              onClick={() => tap(h, g.done ? (!h.source && !h.target ? { day: g.date, delta: -1 } : { day: g.date, value: 0 }) : { day: g.date, ...(h.target && !h.source ? { value: h.target } : {}) })} />
                    </td>
                  ))}
                  <td className="num">{h.streak_label}</td>
                  <td className="num muted">{h.rate == null ? "" : `${h.rate}%`}</td>
                  <td><button className="icon-btn" onClick={() => setEditing(editing === h.id ? null : h.id)} aria-label="Edit"><Icon name="settings" size={14} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="hint">Click a square to fix a past day. Light green: ticked automatically from your data.</div>
        </div>
      )}

      {editing && (() => {
        const h = d.habits.find((x) => x.id === editing);
        if (!h) return null;
        return (
          <div className="box pad stack" style={{ gap: 8 }}>
            <b>Edit "{h.name}"</b>
            <HabitFields value={{ name: h.name, source: h.source || "", target: h.target ?? "", days: h.days, per_week: h.per_week ?? "" }}
                         sourceOpts={sourceOpts} onSave={(v) => save(h, v)} />
            <div className="row">
              <button className="btn quiet danger sm" onClick={async () => { if (confirm(`Delete "${h.name}" and its history?`)) { setD(await api.del(`/api/habits/${h.id}`)); setEditing(null); } }}>
                <Icon name="trash" size={14} />Delete habit</button>
            </div>
          </div>
        );
      })()}

      <form className="box pad stack" style={{ gap: 8 }} onSubmit={add}>
        <b>New habit</b>
        <HabitFields value={f} sourceOpts={sourceOpts} onChange={setF} />
        <div><button className="btn primary">Add habit</button></div>
      </form>
    </div>
  );
}

function HabitFields({ value, onChange, onSave, sourceOpts }) {
  const [v, setV] = useState(value);
  const set = (k, x) => { const n = { ...v, [k]: x }; setV(n); onChange && onChange(n); };
  const toggle = (dd) => set("days", v.days.includes(dd) ? v.days.replace(dd, "") : v.days + dd);
  const needsTarget = ["sleep", "steps", "meals_logged", "calories_under"].includes(v.source) || !v.source;
  return (
    <>
      <div className="row">
        <input value={v.name} onChange={(e) => set("name", e.target.value)} placeholder="e.g. Read 20 pages" className="grow" />
        <select value={v.source} onChange={(e) => set("source", e.target.value)}>
          {sourceOpts.map(([k, l]) => <option key={k} value={k}>{k ? `Auto: ${l}` : "I tick it myself"}</option>)}
        </select>
        {needsTarget && <input type="number" value={v.target} onChange={(e) => set("target", e.target.value)} style={{ width: 120 }}
          placeholder={{ sleep: "minutes, 420", steps: "steps, 8000", meals_logged: "meals, 3", calories_under: "kcal, 2500" }[v.source] || "times a day"} />}
      </div>
      <div className="row">
        <span className="days">{DAYS.map(([dd, l]) => <button type="button" key={dd} title={l} className={`day ${v.days.includes(dd) ? "on" : ""}`} onClick={() => toggle(dd)}>{l[0]}</button>)}</span>
        <label className="field inline">or just <input type="number" value={v.per_week} onChange={(e) => set("per_week", e.target.value)} style={{ width: 60 }} /> times a week</label>
        {onSave && <button type="button" className="btn sm" onClick={() => onSave(v)}>Save</button>}
      </div>
    </>
  );
}
