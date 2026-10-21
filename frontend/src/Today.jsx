import React, { useEffect, useState } from "react";
import { api, fmtDue, fmtMinutes, fmtTime, minutesBetween } from "./api.js";
import { CourseTag, Icon } from "./ui.jsx";

export default function Today({ ctx }) {
  const [data, setData] = useState(null);
  const [cameUp, setCameUp] = useState(60);

  useEffect(() => { api.get("/api/today").then(setData).catch((e) => ctx.notify(e.message, "error")); }, [ctx.version]);
  if (!data) return <div className="page muted">Loading…</div>;

  const now = new Date(data.now);
  const items = [
    ...data.events.map((e) => ({ ...e, kind: "event", key: `e${e.id}` })),
    ...data.blocks.map((b) => ({ ...b, key: `b${b.id}` })),
  ].sort((a, b) => new Date(a.start) - new Date(b.start));
  const current = items.find((i) => new Date(i.start) <= now && new Date(i.end) > now && i.kind !== "event");
  const next = items.find((i) => new Date(i.start) > now && i.kind === "task");
  const pending = data.blocks.filter((b) => data.needs_checkin.includes(b.id));
  const nowIdx = items.findIndex((i) => new Date(i.start) > now);
  const needConfidence = data.due_soon.filter((t) => t.is_study && !t.confidence);

  async function act(fn, msg) {
    try { await fn(); if (msg) ctx.notify(msg); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{now.toLocaleDateString([], { weekday: "long" })}</h1>
          <div className="sub">{now.toLocaleDateString([], { month: "long", day: "numeric" })}</div>
        </div>
        <div className="stats">
          <div className="stat"><b>{fmtMinutes(data.remaining_task_minutes)}</b><span>work left today</span></div>
          <div className="stat">
            <b className="ellipsis" style={{ maxWidth: 220 }}>{current ? current.title : next ? fmtTime(next.start) : "Free"}</b>
            <span>{current ? `now, until ${fmtTime(current.end)}` : next ? `next: ${next.title}` : "nothing else planned"}</span>
          </div>
        </div>
      </div>

      <div className="today-grid">
        <div className="stack">
          {pending.length > 0 && (
            <div className="checkin">
              <b>How did these go?</b>
              {pending.map((b) => (
                <div key={b.id} className="row between">
                  <span>{b.title} <span className="muted small">{fmtTime(b.start)}–{fmtTime(b.end)}</span></span>
                  <span className="row" style={{ gap: 4 }}>
                    <button className="btn ok sm" onClick={() => act(() => api.post(`/api/blocks/${b.id}/checkin`, { status: "done" }), "Nice")}>Done</button>
                    <button className="btn sm" onClick={() => ctx.openBlock(b)}>Partly</button>
                    <button className="btn sm" onClick={() => act(() => api.post(`/api/blocks/${b.id}/checkin`, { status: "skipped" }), "Rescheduled")}>Didn't</button>
                  </span>
                </div>
              ))}
            </div>
          )}

          <div className="timeline">
            {items.length === 0 && <div className="empty">Nothing planned today. Add tasks or hit Sync.</div>}
            {items.map((i, idx) => {
              const past = new Date(i.end) <= now;
              const course = ctx.courseById[i.course_id];
              return (
                <React.Fragment key={i.key}>
                  {idx === nowIdx && !current && <div className="nowline"><span>{fmtTime(data.now)}</span><i /></div>}
                  <div className={`tl ${i.kind} ${i.status || ""} ${past ? "past" : ""} ${current?.key === i.key ? "now" : ""} c-${course?.color || "none"}`}
                       onClick={() => ctx.openBlock(i)}>
                    <div className="t">{fmtTime(i.start)}</div>
                    <div className="bar" />
                    <div className="what">
                      <div className="ttl ellipsis">{i.title}</div>
                      <div className="row small muted" style={{ gap: 6 }}>
                        {fmtMinutes(minutesBetween(i.start, i.end))}
                        {course && <CourseTag course={course} />}
                        {i.locked && <Icon name="pin" size={12} />}
                        {i.status && i.status !== "planned" && <span className={`tag ${i.status === "done" ? "ok" : "warn"}`}>{i.status}</span>}
                      </div>
                    </div>
                    <div className="faint small">{fmtTime(i.end)}</div>
                  </div>
                </React.Fragment>
              );
            })}
          </div>
        </div>

        <aside className="rail">
          {data.warnings.length > 0 && (
            <div>
              <div className="section-title">Heads up</div>
              <div className="stack" style={{ gap: 4 }}>
                {data.warnings.slice(0, 6).map((w, i) => (
                  <div key={i} className={`warn-line ${w.level}`} onClick={() => w.task_id && ctx.openTask(w.task_id)} style={{ cursor: w.task_id ? "pointer" : "default" }}>{w.message}</div>
                ))}
              </div>
            </div>
          )}
          {data.attendance?.length > 0 && (
            <div>
              <div className="section-title">Were you in class?</div>
              <div className="stack" style={{ gap: 6 }}>
                {data.attendance.map((a) => (
                  <div key={a.course_id} className="row between">
                    <span>{a.name}</span>
                    <span className="seg">
                      {[["present", "Yes"], ["absent", "No"], ["excused", "Excused"]].map(([st, l]) => (
                        <button key={st} className={a.status === st ? "on" : ""}
                                onClick={() => act(() => api.post(`/api/courses/${a.course_id}/attendance`, { status: st, once: true }), `${a.name}: ${l === "Yes" ? "present" : st}`)}>{l}</button>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {needConfidence.length > 0 && (
            <div>
              <div className="section-title">How ready do you feel?</div>
              <div className="mini-list">
                {needConfidence.map((t) => (
                  <div key={t.id} className="mini" onClick={() => ctx.openTask(t.id)}>
                    <span className="ellipsis">{t.title}</span><span className="tag accent">set</span>
                  </div>
                ))}
              </div>
              <div className="hint">Your answer sets how much study time gets planned, and how early it starts.</div>
            </div>
          )}
          <div>
            <div className="section-title">Due in the next 7 days</div>
            <div className="mini-list">
              {data.due_soon.length === 0 && <div className="faint small">Nothing due.</div>}
              {data.due_soon.map((t) => (
                <div key={t.id} className={`mini c-${ctx.courseById[t.course_id]?.color || "none"}`} onClick={() => ctx.openTask(t.id)}>
                  <span className="dot" style={{ marginTop: 6 }} />
                  <span className="grow" style={{ minWidth: 0 }}>
                    <div className="ellipsis">{t.title}</div>
                    <div className="meter" style={{ width: "100%", marginTop: 4 }}><i style={{ width: `${Math.min(100, (100 * t.done_minutes) / Math.max(1, t.est_minutes))}%` }} /></div>
                  </span>
                  <span className="when">{fmtDue(t.due, now)}</span>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div className="section-title">Something came up?</div>
            <div className="row">
              <select value={cameUp} onChange={(e) => setCameUp(Number(e.target.value))} aria-label="How long">
                {[30, 60, 90, 120, 180, 240].map((m) => <option key={m} value={m}>{fmtMinutes(m)}</option>)}
              </select>
              <button className="btn" onClick={() => act(() => api.post("/api/came-up", { minutes: cameUp }), `Blocked ${fmtMinutes(cameUp)} from now and replanned`)}>Block it off</button>
            </div>
          </div>
          {data.planned_at && <div className="faint small">Plan updated {new Date(data.planned_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</div>}
        </aside>
      </div>
    </div>
  );
}
