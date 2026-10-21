import React, { useEffect, useState } from "react";
import { api, BUCKETS, dayBucket, fmtDue, fmtMinutes } from "./api.js";
import { CourseTag, Icon, Seg } from "./ui.jsx";

export default function Tasks({ ctx, courseId = null, embedded = false }) {
  const [tasks, setTasks] = useState([]);
  const [suggested, setSuggested] = useState([]);
  const [status, setStatus] = useState("open");
  const [filter, setFilter] = useState(courseId ?? "");

  const q = `status=${status}${filter ? `&course_id=${filter}` : ""}`;
  useEffect(() => { api.get(`/api/tasks?${q}`).then(setTasks).catch((e) => ctx.notify(e.message, "error")); }, [ctx.version, q]);
  useEffect(() => {
    api.get(`/api/tasks?status=suggested${filter ? `&course_id=${filter}` : ""}`).then(setSuggested).catch(() => {});
  }, [ctx.version, filter]);

  async function act(fn, msg) {
    try { await fn(); if (msg) ctx.notify(msg); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }

  const groups = {};
  for (const t of tasks) (groups[status === "done" ? "Done" : dayBucket(t.due)] ||= []).push(t);
  const order = status === "done" ? ["Done"] : BUCKETS;

  const body = (
    <>
      <QuickAdd ctx={ctx} courseId={filter || null} />

      {suggested.length > 0 && (
        <div className="inbox" style={{ marginTop: 18 }}>
          <div className="inbox-head">
            <span className="row"><Icon name="inbox" /><b>{suggested.length} new from course sites</b><span className="muted small">not scheduled until you accept</span></span>
            <button className="btn ok sm" onClick={() => act(() => api.post("/api/suggestions/accept-all"), "Added and scheduled")}>Accept all</button>
          </div>
          {suggested.map((t) => (
            <div key={t.id} className="inbox-row">
              <span className="grow" style={{ minWidth: 0, cursor: "pointer" }} onClick={() => ctx.openTask(t.id)}>
                <div className="ellipsis">{t.title}</div>
                <span className="row small muted" style={{ gap: 6 }}>{fmtDue(t.due)} · {fmtMinutes(t.est_minutes)}<CourseTag course={ctx.courseById[t.course_id]} /></span>
              </span>
              <button className="btn sm ok" onClick={() => act(() => api.patch(`/api/tasks/${t.id}`, { status: "open" }), "Added and scheduled")}>Accept</button>
              <button className="btn sm quiet" onClick={() => act(() => api.patch(`/api/tasks/${t.id}`, { status: "dismissed" }), "Dismissed. It won't come back")}>Dismiss</button>
            </div>
          ))}
        </div>
      )}

      <div className="row between" style={{ margin: "18px 0 4px" }}>
        <Seg value={status} onChange={setStatus} options={[["open", "To do"], ["done", "Done"]]} />
        {!embedded && (
          <select value={filter} onChange={(e) => setFilter(e.target.value ? Number(e.target.value) : "")} aria-label="Course">
            <option value="">All courses</option>
            {ctx.courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        )}
      </div>

      {tasks.length === 0 && <div className="empty">{status === "open" ? "Nothing to do here. Nice." : "Nothing finished yet."}</div>}
      {order.filter((g) => groups[g]).map((g) => (
        <div key={g}>
          <div className={`group-title ${g === "Overdue" ? "overdue" : ""}`}>{g}<span className="faint small">{groups[g].length}</span></div>
          {groups[g].map((t) => <TaskLine key={t.id} t={t} ctx={ctx} act={act} />)}
        </div>
      ))}
    </>
  );

  if (embedded) return body;
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Tasks</h1><div className="sub">Everything with a deadline. The planner finds the time.</div></div>
      </div>
      {body}
    </div>
  );
}

export function TaskLine({ t, ctx, act }) {
  const course = ctx.courseById[t.course_id];
  const steps = t.subtasks || [];
  const doneSteps = steps.filter((s) => s.status === "done").length;
  const pct = Math.min(100, (100 * t.done_minutes) / Math.max(1, t.est_minutes));
  const courseTags = [course?.code, (course?.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")];
  return (
    <div className={`task-line ${t.status}`} onClick={() => ctx.openTask(t.id)}>
      <button className={`check ${t.status === "done" ? "on" : ""}`} aria-label="Mark done"
              onClick={(e) => { e.stopPropagation(); if (t.status !== "done") act(() => api.post(`/api/tasks/${t.id}/finish`), "Done. Nice"); }}>
        {t.status === "done" && <Icon name="check" size={12} />}
      </button>
      <div style={{ minWidth: 0 }}>
        <div className="tt ellipsis">{t.title}</div>
        <div className="meta">
          <CourseTag course={course} />
          {steps.length > 0 && <span className="tag">{doneSteps}/{steps.length} steps</span>}
          {t.is_study && !t.confidence && t.status === "open" && <span className="tag accent">set confidence</span>}
          {t.needs_estimate && !t.is_study && <span className="tag warn">guess: {fmtMinutes(t.est_minutes)}</span>}
          {t.priority === 1 && <span className="tag danger">high</span>}
          {t.tags.filter((x) => !courseTags.includes(x)).map((x) => <span key={x} className="tag">#{x}</span>)}
        </div>
      </div>
      <div className="right">
        <span className="meter" title={`${fmtMinutes(t.done_minutes)} of ${fmtMinutes(t.est_minutes)}`}><i style={{ width: `${pct}%` }} /></span>
        <span>{fmtMinutes(t.est_minutes)}</span>
        <span style={{ minWidth: 112, textAlign: "right" }}>{fmtDue(t.due)}</span>
      </div>
    </div>
  );
}

export function QuickAdd({ ctx, courseId }) {
  const [text, setText] = useState("");
  const [due, setDue] = useState("");
  const [course, setCourse] = useState(courseId || "");
  useEffect(() => setCourse(courseId || ""), [courseId]);
  async function add(e) {
    e.preventDefault();
    if (!text.trim()) return;
    try {
      const r = await api.post("/api/tasks", { text, ...(due ? { due } : {}), ...(course ? { course_id: Number(course) } : {}) });
      const t = r.task, area = ctx.courseById[t.course_id];
      ctx.notify(`Added "${t.title}": due ${fmtDue(t.due)} · ${fmtMinutes(t.est_minutes)}${t.needs_estimate ? " (guess)" : ""}` +
        `${area ? ` · ${area.name}` : ""}${t.repeat_label ? ` · ${t.repeat_label}` : ""}`);
      setText(""); setDue("");
      ctx.refresh();
      if (r.task.is_study) ctx.openTask(r.task.id);  // ask how ready they feel
    } catch (err) { ctx.notify(err.message, "error"); }
  }
  return (
    <form onSubmit={add}>
      <div className="quickadd">
        <Icon name="plus" className="faint" />
        <input className="q" value={text} onChange={(e) => setText(e.target.value)} placeholder="Add anything: submit timesheet every Sunday, HW 6 Fri 5pm ~2h" aria-label="New task" />
        <select value={course} onChange={(e) => setCourse(e.target.value)} aria-label="Course">
          <option value="">Auto</option>
          {ctx.courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due (optional)" title="Optional. Leave empty and write the date in the text" />
        <button className="btn primary">Add</button>
      </div>
      <div className="hint">Dates, repeats, time and course are worked out from the text · <code>~2h</code> time needed · <code>!1</code> high priority · <code>#tag</code> · <code>squeeze:1h</code> can shrink to · <code>min:45</code> shortest sitting · <code>nosplit</code></div>
    </form>
  );
}
