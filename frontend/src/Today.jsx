import React, { useEffect, useState } from "react";
import { api, fmtMinutes, fmtTime, minutesBetween } from "./api.js";

const KIND_LABEL = { task: "Work", routine: "Routine", busy: "Busy", event: "Calendar" };

export default function Today({ version, refresh, notify }) {
  const [data, setData] = useState(null);
  const [cameUp, setCameUp] = useState(60);

  useEffect(() => { api.get("/api/today").then(setData).catch((e) => notify(e.message, "error")); }, [version]);
  if (!data) return <div className="muted">Loading…</div>;

  const now = new Date(data.now);
  const items = [
    ...data.events.map((e) => ({ ...e, kind: "event", key: `e${e.id}` })),
    ...data.blocks.map((b) => ({ ...b, key: `b${b.id}` })),
  ].sort((a, b) => new Date(a.start) - new Date(b.start));
  const current = items.find((i) => new Date(i.start) <= now && new Date(i.end) > now);
  const pending = data.blocks.filter((b) => data.needs_checkin.includes(b.id));

  async function act(fn, msg) {
    try { await fn(); if (msg) notify(msg); refresh(); } catch (e) { notify(e.message, "error"); }
  }
  const checkin = (b, status) => {
    if (status === "partial") {
      const total = minutesBetween(b.start, b.end);
      const m = prompt(`How many of the ${total} minutes did you get done?`, Math.round(total / 2));
      if (m === null) return;
      return act(() => api.post(`/api/blocks/${b.id}/checkin`, { status, minutes: Number(m) }), "Got it, replanned the rest");
    }
    return act(() => api.post(`/api/blocks/${b.id}/checkin`, { status }), status === "done" ? "Nice" : "Rescheduled");
  };

  return (
    <div className="today">
      <section className="summary">
        <div>
          <div className="big">{fmtMinutes(data.remaining_task_minutes)}</div>
          <div className="muted">of planned work left today</div>
        </div>
        <div className="cameup">
          <select value={cameUp} onChange={(e) => setCameUp(Number(e.target.value))}>
            {[30, 60, 90, 120, 180].map((m) => <option key={m} value={m}>{fmtMinutes(m)}</option>)}
          </select>
          <button className="btn warn" onClick={() => act(() => api.post("/api/came-up", { minutes: cameUp }), `Blocked ${fmtMinutes(cameUp)} and replanned`)}>
            Something came up
          </button>
        </div>
      </section>

      {data.warnings.length > 0 && (
        <section className="warnings">
          {data.warnings.map((w, i) => <div key={i} className={`warning ${w.level}`}>{w.message}</div>)}
        </section>
      )}

      {pending.length > 0 && (
        <section className="card checkin">
          <h3>How did these go?</h3>
          {pending.map((b) => (
            <div key={b.id} className="row">
              <span><b>{b.title}</b> <span className="muted">{fmtTime(b.start)}–{fmtTime(b.end)}</span></span>
              <CheckinButtons onPick={(s) => checkin(b, s)} />
            </div>
          ))}
        </section>
      )}

      <section className="timeline">
        {items.length === 0 && <div className="muted">Nothing scheduled today.</div>}
        {items.map((i) => {
          const past = new Date(i.end) <= now;
          const isNow = current && current.key === i.key;
          return (
            <div key={i.key} className={`item ${i.kind} ${i.status || ""} ${past ? "past" : ""} ${isNow ? "now" : ""}`}>
              <div className="time">{fmtTime(i.start)}<br /><span className="muted">{fmtTime(i.end)}</span></div>
              <div className="body">
                <div className="title">
                  {i.title}
                  {i.locked && <span className="pill" title="You placed this by hand">pinned</span>}
                  {i.status && i.status !== "planned" && <span className={`pill ${i.status}`}>{i.status}</span>}
                </div>
                <div className="muted small">{KIND_LABEL[i.kind]} · {fmtMinutes(minutesBetween(i.start, i.end))}</div>
              </div>
              <div className="actions">
                {i.kind === "task" && i.status === "planned" && (isNow || past) && <CheckinButtons onPick={(s) => checkin(i, s)} />}
                {i.locked && i.kind !== "busy" && (
                  <button className="btn ghost small" onClick={() => act(() => api.post(`/api/blocks/${i.id}/unlock`), "Unpinned")}>Unpin</button>
                )}
              </div>
            </div>
          );
        })}
      </section>
      {data.planned_at && <div className="muted small">Last planned {new Date(data.planned_at).toLocaleString()}</div>}
    </div>
  );
}

function CheckinButtons({ onPick }) {
  return (
    <span className="btns">
      <button className="btn ok small" onClick={() => onPick("done")}>Done</button>
      <button className="btn ghost small" onClick={() => onPick("partial")}>Partly</button>
      <button className="btn ghost small" onClick={() => onPick("skipped")}>Not started</button>
    </span>
  );
}
