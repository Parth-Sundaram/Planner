import React, { useState } from "react";
import { api, fmtDay, fmtMinutes, fmtTime, minutesBetween } from "./api.js";
import { CourseTag, Icon, Panel } from "./ui.jsx";

const KIND = { work: "Work block", meeting: "Meeting (to fit in)", appointment: "Appointment", class: "Class",
  routine: "Routine", busy: "Blocked time", event: "Google Calendar" };

// Opens when you click a block on Today or the calendar.
// From the calendar (defer): changes are saved but not rebalanced until you press Rebalance.
export default function BlockPanel({ block: b, ctx, onClose, defer = false }) {
  const [partial, setPartial] = useState(null);
  const [later, setLater] = useState(false);
  const [until, setUntil] = useState("");
  const [when, setWhen] = useState({ date: b.start.slice(0, 10), start: b.start.slice(11, 16), end: b.end.slice(11, 16) });
  const length = minutesBetween(b.start, b.end);
  const planned = b.status === "planned" || !b.status;
  const course = ctx.courseById[b.course_id];

  const q = defer ? "?replan=0" : "";
  async function act(fn, msg, logOnly = false) {
    try { await fn(); ctx.notify(defer && !logOnly ? `${msg.split(".")[0]}. Press Rebalance when you're done` : msg); onClose(); ctx.refresh(); }
    catch (e) { ctx.notify(e.message, "error"); }
  }
  const post = (path, body) => api.post(`/api/blocks/${b.id}/${path}${q}`, body);
  const moveNow = (path, body) => api.post(`/api/blocks/${b.id}/${path}`, body);  // you want to see where the work went
  const checkin = (body) => api.post(`/api/blocks/${b.id}/checkin`, body);   // logging work never needs a rebalance
  const dueDay = b.due ? b.due.slice(0, 10) : "";
  const taskId = b.task_id;  // for a step, just that step
  function deleteTask() {
    if (!confirm(`Delete "${b.title}" and all of its planned time?`)) return;
    act(() => api.del(`/api/tasks/${taskId}${q}`), "Task deleted. Everything else replanned");
  }

  function saveTimes(e) {
    e.preventDefault();
    if (when.end <= when.start) return ctx.notify("End has to be after start", "error");
    act(() => api.patch(`/api/blocks/${b.id}${q}`, { start: `${when.date}T${when.start}`, end: `${when.date}T${when.end}` }),
      "Moved and pinned. Everything else replanned around it");
  }

  return (
    <Panel onClose={onClose} actions={<span className="faint small">{KIND[b.kind === "event" ? "event" : b.category || b.kind] || KIND[b.kind]}</span>}>
      <div className="stack" style={{ gap: 6 }}>
        <h2 style={{ fontSize: 19 }}>{b.title}</h2>
        <div className="row muted small">
          {fmtDay(b.start)} · {fmtTime(b.start)}–{fmtTime(b.end)} · {fmtMinutes(length)}
          <CourseTag course={course} />
          {b.locked && <span className="tag"><Icon name="pin" size={12} />pinned</span>}
          {!planned && <span className={`tag ${b.status === "done" ? "ok" : "warn"}`}>{b.status}</span>}
        </div>
      </div>

      {b.kind === "event" && <p className="muted">This event is from your Google Calendar, so change it there. The planner works around it.</p>}

      {b.kind === "task" && planned && (
        <div>
          <div className="subhead">How did it go?</div>
          <div className="choices">
            <button className="btn ok" onClick={() => act(() => checkin({ status: "done" }), "Nice. Logged it", true)}><Icon name="check" />Done</button>
            <button className="btn" onClick={() => setPartial(partial === null ? Math.round(length / 2) : null)}>Partly done</button>
            <button className="btn" title="Free this slot. The work moves later, still before its deadline"
                    onClick={() => act(() => moveNow("not-now"), "Moved later. This slot went to other work", true)}>Not now</button>
            <button className="btn" title="It isn't open yet, or you don't know enough yet" onClick={() => setLater(!later)}>Not yet</button>
            <button className="btn danger" title="Delete the whole task, not just this block" onClick={deleteTask}><Icon name="trash" />Delete task</button>
          </div>
          {later && (
            <div className="row" style={{ marginTop: 10, flexWrap: "wrap" }}>
              <span className="muted small">Don't start until</span>
              <button className="btn sm" onClick={() => act(() => moveNow("not-yet", {}), "Held back until a couple of days before it's due", true)}>A couple of days before it's due</button>
              <input type="date" value={until} max={dueDay || undefined} onChange={(e) => setUntil(e.target.value)} aria-label="Start date" />
              <button className="btn sm primary" disabled={!until} onClick={() => act(() => moveNow("not-yet", { until }), "Held back until then", true)}>Set</button>
            </div>
          )}
          {partial !== null && (
            <form className="row" style={{ marginTop: 10 }}
                  onSubmit={(e) => { e.preventDefault(); act(() => checkin({ status: "partial", minutes: Number(partial) }), "Got it. The rest is rescheduled", true); }}>
              <input type="number" min="0" max={length} value={partial} onChange={(e) => setPartial(e.target.value)} style={{ width: 80 }} autoFocus />
              <span className="muted small">of {length} min done</span>
              <button className="btn primary sm">Save</button>
            </form>
          )}
        </div>
      )}

      {b.kind === "routine" && planned && b.category !== "class" && b.category !== "appointment" && (
        <div className="choices">
          <button className="btn ok" onClick={() => act(() => checkin({ status: "done" }), "Done", true)}><Icon name="check" />Done</button>
          <button className="btn" title="Skip for this day only. It's back tomorrow" onClick={() => act(() => post("skip"), "Skipped for today. That time is free now")}>Skip today</button>
        </div>
      )}
      {b.kind === "routine" && planned && (b.category === "class" || b.category === "appointment") && (
        <button className="btn" title="Only this day. It's back next time" onClick={() => act(() => post("skip"), "Marked cancelled for this day. That time is free now")}>Cancelled this day</button>
      )}
      {b.kind === "routine" && b.status === "skipped" && (
        <button className="btn primary" onClick={() => act(() => post("restore"), "Back in the plan")}>Put it back</button>
      )}
      {b.kind === "appointment" && (
        <button className="btn danger" onClick={() => act(() => post("remove"), "Appointment removed. Replanned")}><Icon name="trash" />Delete appointment</button>
      )}
      {b.kind === "busy" && (
        <button className="btn danger" onClick={() => act(() => post("remove"), "Removed. Replanned")}><Icon name="trash" />Remove this block</button>
      )}

      {b.kind !== "event" && planned && (
        <form onSubmit={saveTimes}>
          <div className="subhead">Exact time</div>
          <div className="row">
            <input type="date" value={when.date} onChange={(e) => setWhen({ ...when, date: e.target.value })} aria-label="Date" />
            <input type="time" value={when.start} onChange={(e) => setWhen({ ...when, start: e.target.value })} aria-label="Start" />
            <span className="faint">–</span>
            <input type="time" value={when.end} onChange={(e) => setWhen({ ...when, end: e.target.value })} aria-label="End" />
            <button className="btn sm">Move</button>
          </div>
          <div className="hint">Moving a block pins it. You can also drag it on the calendar.</div>
        </form>
      )}

      <div className="row">
        {b.task_id && <button className="btn quiet" onClick={() => { onClose(); ctx.openTask(b.parent_task_id || b.task_id); }}>Open task</button>}
        {b.locked && b.kind === "task" && planned && (
          <button className="btn quiet" onClick={() => act(() => post("unlock"), "Unpinned. The planner can move it again")}>Unpin</button>
        )}
      </div>
    </Panel>
  );
}