import React, { useEffect, useState } from "react";
import { api, fmtDay, fmtMinutes, fmtTime } from "./api.js";

const PRIORITY = { 1: "High", 2: "Normal", 3: "Low" };

function defaultDue() {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(23, 59, 0, 0);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T23:59`;
}

export default function Tasks({ version, refresh, notify }) {
  const [tasks, setTasks] = useState([]);
  const [status, setStatus] = useState("open");
  const [text, setText] = useState("");
  const [due, setDue] = useState(defaultDue);
  const [editing, setEditing] = useState(null);

  useEffect(() => { api.get(`/api/tasks?status=${status}`).then(setTasks).catch((e) => notify(e.message, "error")); }, [version, status]);

  async function act(fn, msg) {
    try { await fn(); if (msg) notify(msg); refresh(); } catch (e) { notify(e.message, "error"); }
  }

  async function quickAdd(e) {
    e.preventDefault();
    if (!text.trim()) return;
    await act(() => api.post("/api/tasks", { text, due }), "Added and scheduled");
    setText("");
  }

  return (
    <div className="tasks">
      <form className="card quickadd" onSubmit={quickAdd}>
        <input value={text} onChange={(e) => setText(e.target.value)}
               placeholder="CNN 4/4 ~4h !1 #ai squeeze:3h" aria-label="New task" />
        <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due" />
        <button className="btn primary">Add</button>
        <div className="hint muted small">
          <code>~3h</code> time needed · <code>!1</code>–<code>!3</code> priority · <code>#tag</code> for rules
          · <code>squeeze:2h</code> can shrink to · <code>min:45</code> shortest block · <code>nosplit</code>
        </div>
      </form>

      <div className="filters">
        {["open", "done", "all"].map((s) => (
          <button key={s} className={`btn small ${status === s ? "primary" : "ghost"}`} onClick={() => setStatus(s)}>{s}</button>
        ))}
      </div>

      <div className="card">
        {tasks.length === 0 && <div className="muted">No tasks.</div>}
        {tasks.map((t) => (
          <TaskRow key={t.id} t={t} act={act} onEdit={setEditing} />
        ))}
      </div>
      {editing && <EditTask task={editing} onClose={() => setEditing(null)}
                            onSave={(data) => act(() => api.patch(`/api/tasks/${editing.id}`, data), "Saved and replanned").then(() => setEditing(null))} />}
    </div>
  );
}

function TaskRow({ t, act, onEdit }) {
  const [open, setOpen] = useState(t.subtasks.length > 0);
  const [stepText, setStepText] = useState("");
  const hasSubs = t.subtasks.length > 0;
  const openSubs = t.subtasks.filter((s) => s.status === "open");

  async function addStep(e) {
    e.preventDefault();
    if (!stepText.trim()) return;
    await act(() => api.post("/api/tasks", { text: stepText, parent_id: t.id }), "Step added and scheduled");
    setStepText("");
  }
  function move(sub, dir) {
    const list = t.subtasks;
    const i = list.findIndex((x) => x.id === sub.id);
    const j = i + dir;
    if (j < 0 || j >= list.length) return;
    const other = list[j];
    act(async () => {
      await api.patch(`/api/tasks/${sub.id}`, { position: other.position });
      await api.patch(`/api/tasks/${other.id}`, { position: sub.position === other.position ? sub.position + dir : sub.position });
    });
  }

  return (
    <div className={`task-row ${t.status}`}>
      <div className="main">
        <div className="title">
          {t.title}
          {t.course && <span className="pill">{t.course}</span>}
          {t.tags.map((g) => <span key={g} className="pill tag">#{g}</span>)}
          {t.needs_estimate && <span className="pill warn" title="Imported with a default estimate">set estimate</span>}
          {hasSubs && <span className="pill">{t.subtasks.length - openSubs.length}/{t.subtasks.length} steps</span>}
        </div>
        <div className="muted small">
          Due {fmtDay(t.due)} {fmtTime(t.due)}
          {t.effective_due !== t.due && <> · target {fmtDay(t.effective_due)} {fmtTime(t.effective_due)}</>}
          {" · "}{fmtMinutes(t.done_minutes)} / {fmtMinutes(t.est_minutes)} done · {PRIORITY[t.priority]}
          {t.source !== "manual" && <> · from {t.source.replace("scrape:", "")}</>}
        </div>
        <div className="progress"><div style={{ width: `${Math.min(100, (100 * t.done_minutes) / Math.max(1, t.est_minutes))}%` }} /></div>

        <button className="linkish small" onClick={() => setOpen(!open)}>
          {open ? "▾" : "▸"} {hasSubs ? `Steps (${t.subtasks.length})` : "Add steps"}
        </button>
        {open && (
          <div className="subtasks">
            {hasSubs && (
              <label className="check small muted">
                <input type="checkbox" checked={t.sequential}
                       onChange={(e) => act(() => api.patch(`/api/tasks/${t.id}`, { sequential: e.target.checked }), "Saved")} />
                Do steps in order
              </label>
            )}
            {t.subtasks.map((s, i) => (
              <div key={s.id} className={`subtask ${s.status}`}>
                <span className="num muted">{i + 1}.</span>
                <span className="stitle">{s.title}</span>
                <span className="muted small">
                  {fmtMinutes(s.est_minutes)}
                  {s.due !== t.due && <> · due {fmtDay(s.due)} {fmtTime(s.due)}</>}
                </span>
                <span className="sactions">
                  {t.sequential && <>
                    <button className="btn ghost small" disabled={i === 0} onClick={() => move(s, -1)} aria-label="Move up">↑</button>
                    <button className="btn ghost small" disabled={i === t.subtasks.length - 1} onClick={() => move(s, 1)} aria-label="Move down">↓</button>
                  </>}
                  {s.status === "open" && <button className="btn ok small" onClick={() => act(() => api.post(`/api/tasks/${s.id}/finish`), "Step done")}>Done</button>}
                  <button className="btn ghost small" onClick={() => onEdit(s)}>Edit</button>
                  <button className="btn ghost small" onClick={() => act(() => api.del(`/api/tasks/${s.id}`), "Step removed")}>✕</button>
                </span>
              </div>
            ))}
            {t.status === "open" && (
              <form className="addstep" onSubmit={addStep}>
                <input value={stepText} onChange={(e) => setStepText(e.target.value)} placeholder="New step, e.g. Webhook handler ~1h" />
                <button className="btn primary small">Add step</button>
              </form>
            )}
          </div>
        )}
      </div>
      <div className="actions">
        {t.status === "open" && <button className="btn ok small" onClick={() => act(() => api.post(`/api/tasks/${t.id}/finish`), "Marked done")}>Finish</button>}
        <button className="btn ghost small" onClick={() => onEdit(t)}>Edit</button>
        <button className="btn ghost small" onClick={() => confirm(`Delete "${t.title}"${hasSubs ? " and its steps" : ""}?`) && act(() => api.del(`/api/tasks/${t.id}`), "Deleted")}>Delete</button>
      </div>
    </div>
  );
}

function EditTask({ task, onClose, onSave }) {
  const [f, setF] = useState({
    title: task.title, due: task.due, est_minutes: task.est_minutes, done_minutes: task.done_minutes,
    priority: task.priority, tags: task.tags.join(", "), min_block: task.min_block, max_block: task.max_block,
    splittable: task.splittable, min_total_minutes: task.min_total_minutes ?? "", earliest_start: task.earliest_start ?? "",
  });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  function save(e) {
    e.preventDefault();
    onSave({
      ...f,
      est_minutes: Number(f.est_minutes), done_minutes: Number(f.done_minutes), priority: Number(f.priority),
      min_block: Number(f.min_block), max_block: Number(f.max_block),
      min_total_minutes: f.min_total_minutes === "" ? null : Number(f.min_total_minutes),
      earliest_start: f.earliest_start || null,
    });
  }
  return (
    <div className="modal" onClick={onClose}>
      <form className="card form" onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <h3>Edit task</h3>
        <label>Title<input value={f.title} onChange={set("title")} /></label>
        <label>Due<input type="datetime-local" value={f.due} onChange={set("due")} /></label>
        <div className="grid2">
          <label>Time needed (min)<input type="number" value={f.est_minutes} onChange={set("est_minutes")} /></label>
          <label>Already done (min)<input type="number" value={f.done_minutes} onChange={set("done_minutes")} /></label>
          <label>Can squeeze to (min)<input type="number" placeholder="no" value={f.min_total_minutes} onChange={set("min_total_minutes")} /></label>
          <label>Priority
            <select value={f.priority} onChange={set("priority")}>
              <option value={1}>High</option><option value={2}>Normal</option><option value={3}>Low</option>
            </select>
          </label>
          <label>Shortest block (min)<input type="number" value={f.min_block} onChange={set("min_block")} /></label>
          <label>Longest block (min)<input type="number" value={f.max_block} onChange={set("max_block")} /></label>
        </div>
        <label>Tags<input value={f.tags} onChange={set("tags")} placeholder="db, ai" /></label>
        <label>Don't start before<input type="datetime-local" value={f.earliest_start} onChange={set("earliest_start")} /></label>
        <label className="check"><input type="checkbox" checked={f.splittable} onChange={set("splittable")} /> Can be split across blocks</label>
        <div className="row end">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn primary">Save</button>
        </div>
      </form>
    </div>
  );
}
