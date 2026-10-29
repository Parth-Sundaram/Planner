import React, { useEffect, useState } from "react";
import { api, fmtDay, fmtMinutes, fmtTime, localInput } from "./api.js";
import { CONFIDENCE, Confidence, CourseTag, Field, Icon, Panel } from "./ui.jsx";

export default function TaskPanel({ id, ctx, onClose }) {
  const [data, setData] = useState(null);
  const [step, setStep] = useState("");

  const load = () => api.get(`/api/tasks/${id}`).then(setData).catch((e) => ctx.notify(e.message, "error"));
  useEffect(() => { load(); }, [id, ctx.version]);
  if (!data) return <Panel onClose={onClose}><div className="muted">Loading…</div></Panel>;
  const t = data.task;

  async function save(body, msg = "Saved. Replanned") {
    try { await api.patch(`/api/tasks/${t.id}`, body); ctx.notify(msg); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }
  async function act(fn, msg, close) {
    try { await fn(); ctx.notify(msg); if (close) onClose(); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }
  async function addStep(e) {
    e.preventDefault();
    if (!step.trim()) return;
    await act(() => api.post("/api/tasks", { text: step, parent_id: t.id }), "Step added and scheduled");
    setStep("");
  }
  function moveStep(i, dir) {
    const list = t.subtasks, a = list[i], b = list[i + dir];
    if (!b) return;
    act(async () => {
      await api.patch(`/api/tasks/${a.id}`, { position: b.position });
      await api.patch(`/api/tasks/${b.id}`, { position: a.position === b.position ? a.position + dir : a.position });
    }, "Reordered");
  }

  const hasSteps = t.subtasks.length > 0;
  const future = data.blocks.filter((b) => b.status === "planned" && new Date(b.end) > new Date());
  const conf = CONFIDENCE.find(([v]) => v === t.confidence);

  return (
    <Panel onClose={onClose} actions={<>
      {t.status === "suggested" && <>
        <button className="btn ok sm" onClick={() => act(() => api.patch(`/api/tasks/${t.id}`, { status: "open" }), "Added and scheduled")}>Accept</button>
        <button className="btn sm" onClick={() => act(() => api.patch(`/api/tasks/${t.id}`, { status: "dismissed" }), "Dismissed", true)}>Dismiss</button>
      </>}
      {t.status === "open" && <button className="btn ok sm" onClick={() => act(() => api.post(`/api/tasks/${t.id}/finish`), "Done. Nice", true)}><Icon name="check" size={14} />Mark done</button>}
      {t.status === "done" && <button className="btn sm" onClick={() => save({ status: "open" }, "Reopened")}>Reopen</button>}
    </>}>
      <Field className="title-input" value={t.title} onSave={(v) => v && save({ title: v })} aria-label="Title" />

      {t.is_study && t.status !== "done" && (
        <div className="box pad stack" style={{ gap: 8 }}>
          <b>How ready do you feel?</b>
          <Confidence value={t.confidence} onChange={(v) => save({ confidence: v }, v ? "Prep time planned" : "Cleared")} />
          <div className="muted small">
            {conf ? <>Planning {fmtMinutes(t.est_minutes)} of study ({conf[2].split(" over ")[1] ? `spread over ${conf[2].split(" over ")[1]}` : conf[2]}), at most 90 minutes at a time. You can change it anytime.</>
                  : "Pick one and the planner adds study sessions in the days before. Less ready means more time, starting earlier."}
          </div>
        </div>
      )}

      <div className="props">
        <div className="k">Due</div>
        <div className="v"><Field type="datetime-local" value={localInput(t.due)} onSave={(v) => v && save({ due: v })} /></div>
        {t.effective_due !== t.due && <><div className="k">Target</div><div className="v muted small">{fmtDay(t.effective_due)} {fmtTime(t.effective_due)} (deadline rule)</div></>}
        <div className="k">Course / area</div>
        <div className="v">
          <select value={t.course_id || ""} onChange={(e) => save({ course_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">None</option>
            {ctx.courses.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div className="k">Time needed</div>
        <div className="v">
          {hasSteps ? <span>{fmtMinutes(t.est_minutes)} <span className="faint small">(sum of steps)</span></span>
            : <><Field type="number" min="5" step="15" value={t.est_minutes} onSave={(v) => v && save({ est_minutes: v })} /><span className="faint small">min</span>
               {t.needs_estimate && <span className="tag warn">a guess, so check it</span>}</>}
        </div>
        <div className="k">Done so far</div>
        <div className="v">{hasSteps ? fmtMinutes(t.done_minutes) : <><Field type="number" min="0" step="15" value={t.done_minutes} onSave={(v) => save({ done_minutes: v ?? 0 })} /><span className="faint small">min</span></>}</div>
        <div className="k">Type</div>
        <div className="v">
          <select value={t.kind || "work"} onChange={(e) => save({ kind: e.target.value })}>
            <option value="work">Work (can be split up)</option><option value="meeting">Meeting / talk (one sitting)</option>
          </select>
        </div>
        <div className="k">Priority</div>
        <div className="v">
          <select value={t.priority} onChange={(e) => save({ priority: Number(e.target.value) })}>
            <option value={1}>High</option><option value={2}>Normal</option><option value={3}>Low</option>
          </select>
        </div>
        {!t.parent_id && <>
          <div className="k">Repeats</div>
          <div className="v">
            <select value={t.repeat ? (t.repeat.startsWith("weekly") ? "weekly" : t.repeat.startsWith("days:") ? "days" : t.repeat) : ""}
                    onChange={(e) => save({ repeat: e.target.value === "days" ? `days:${"MTWRFSU"[(new Date(t.due).getDay() + 6) % 7]}` : e.target.value || null })}>
              <option value="">Doesn't repeat</option><option value="daily">Every day</option><option value="weekdays">Every weekday</option>
              <option value="days">On certain days…</option>
              <option value="weekly">Every week</option><option value="biweekly">Every 2 weeks</option><option value="monthly">Every month</option>
            </select>
            {t.repeat?.startsWith("days:") && (
              <span className="days">
                {[["M", "Mon"], ["T", "Tue"], ["W", "Wed"], ["R", "Thu"], ["F", "Fri"], ["S", "Sat"], ["U", "Sun"]].map(([d, l]) => {
                  const on = t.repeat.slice(5).includes(d);
                  const next = "MTWRFSU".split("").filter((x) => (x === d ? !on : t.repeat.slice(5).includes(x))).join("");
                  return <button type="button" key={d} title={l} className={`day ${on ? "on" : ""}`} onClick={() => next && save({ repeat: `days:${next}` })}>{l[0]}</button>;
                })}
              </span>
            )}
            {t.repeat && <span className="faint small">next one appears when you finish this</span>}
          </div>
        </>}
        <div className="k">Tags</div>
        <div className="v"><Field value={t.tags.join(", ")} placeholder="Add tags" onSave={(v) => save({ tags: v })} /></div>
        {t.source !== "manual" && <><div className="k">From</div><div className="v muted small">{t.source.replace("scrape:", "course site: ").replace("google_tasks", "Google Tasks")}</div></>}
      </div>

      <div>
        <div className="subhead">Steps</div>
        <div className="steps">
          {t.subtasks.map((s, i) => (
            <div key={s.id} className={`step ${s.status}`}>
              <button className={`check ${s.status === "done" ? "on" : ""}`} aria-label="Step done"
                      onClick={() => s.status !== "done" && act(() => api.post(`/api/tasks/${s.id}/finish`), "Step done")}>
                {s.status === "done" && <Icon name="check" size={12} />}
              </button>
              <span className="stt ellipsis">{s.title} <span className="faint small">{fmtMinutes(s.est_minutes)}</span></span>
              <span className="acts">
                {t.sequential && <>
                  <button className="icon-btn" disabled={i === 0} onClick={() => moveStep(i, -1)} aria-label="Up"><Icon name="up" size={14} /></button>
                  <button className="icon-btn" disabled={i === t.subtasks.length - 1} onClick={() => moveStep(i, 1)} aria-label="Down"><Icon name="down" size={14} /></button>
                </>}
                <button className="icon-btn" onClick={() => ctx.openTask(s.id)} aria-label="Open step"><Icon name="external" size={14} /></button>
                <button className="icon-btn" onClick={() => act(() => api.del(`/api/tasks/${s.id}`), "Step removed")} aria-label="Remove"><Icon name="x" size={14} /></button>
              </span>
            </div>
          ))}
        </div>
        {t.status !== "done" && !t.parent_id && (
          <form className="row" style={{ marginTop: 6 }} onSubmit={addStep}>
            <input className="grow" value={step} onChange={(e) => setStep(e.target.value)} placeholder="Add a step, e.g. Outline ~45m" />
            <button className="btn sm">Add</button>
          </form>
        )}
        {hasSteps && (
          <label className="check-label muted" style={{ marginTop: 8 }}>
            <input type="checkbox" checked={t.sequential} onChange={(e) => save({ sequential: e.target.checked })} />Do steps in order
          </label>
        )}
      </div>

      <div>
        <div className="subhead">Scheduled</div>
        {future.length === 0 && <div className="faint small">{t.status === "open" ? "Nothing planned yet. It may not fit before the deadline (check Today's heads-up)." : "Not scheduled."}</div>}
        <div className="blocks-list">
          {future.map((b) => (
            <div key={b.id} className="blk" onClick={() => { onClose(); ctx.openBlock(b); }}>
              <span>{fmtDay(b.start)}</span><span className="muted">{fmtTime(b.start)}–{fmtTime(b.end)}</span>
              {b.title !== t.title && <span className="faint ellipsis">{b.title.split(" › ").pop()}</span>}
              {b.locked && <Icon name="pin" size={12} />}
            </div>
          ))}
        </div>
      </div>

      <details className="adv">
        <summary>Scheduling options</summary>
        <div className="props">
          <div className="k">Shortest sitting</div><div className="v"><Field type="number" value={t.min_block} onSave={(v) => v && save({ min_block: v })} /><span className="faint small">min</span></div>
          <div className="k">Longest sitting</div><div className="v"><Field type="number" value={t.max_block} onSave={(v) => v && save({ max_block: v })} /><span className="faint small">min</span></div>
          <div className="k">Max per day</div><div className="v"><Field type="number" value={t.max_per_day ?? ""} placeholder="any" onSave={(v) => save({ max_per_day: v })} /><span className="faint small">min</span></div>
          <div className="k">Can squeeze to</div><div className="v"><Field type="number" value={t.min_total_minutes ?? ""} placeholder="no" onSave={(v) => save({ min_total_minutes: v })} /><span className="faint small">min if time runs out</span></div>
          <div className="k">Don't start before</div><div className="v"><Field type="datetime-local" value={localInput(t.earliest_start)} onSave={(v) => save({ earliest_start: v || null })} /></div>
          <div className="k">Split it up</div><div className="v"><input type="checkbox" checked={t.splittable} onChange={(e) => save({ splittable: e.target.checked })} /></div>
        </div>
      </details>

      <div>
        <div className="subhead">Notes</div>
        <NotesBox value={t.notes} onSave={(v) => save({ notes: v }, "Saved")} />
      </div>

      <div className="row">
        <CourseTag course={ctx.courseById[t.course_id]} />
        <span className="grow" />
        <button className="btn quiet danger sm" onClick={() => confirm(`Delete "${t.title}"${hasSteps ? " and its steps" : ""}?`) && act(() => api.del(`/api/tasks/${t.id}`), "Deleted", true)}>
          <Icon name="trash" size={14} />Delete
        </button>
      </div>
    </Panel>
  );
}

function NotesBox({ value, onSave }) {
  const [v, setV] = useState(value || "");
  useEffect(() => setV(value || ""), [value]);
  return <textarea rows={4} style={{ width: "100%" }} value={v} placeholder="Links, rubric notes, anything…"
                   onChange={(e) => setV(e.target.value)} onBlur={() => v !== (value || "") && onSave(v)} />;
}