import React, { useEffect, useRef, useState } from "react";
import { api, fmtDay, fmtDue, fmtMinutes, fmtTime, localInput } from "./api.js";
import { Field, Icon } from "./ui.jsx";
import Tasks from "./Tasks.jsx";
import { CareerModule, FinanceModule, FitnessModule } from "./Areas.jsx";

const COLORS = ["blue", "green", "purple", "orange", "pink", "teal", "yellow", "red", "gray"];
const DAYS = [["M", "Mon"], ["T", "Tue"], ["W", "Wed"], ["R", "Thu"], ["F", "Fri"], ["S", "Sat"], ["U", "Sun"]];
const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

export function CourseList({ ctx }) {
  const [name, setName] = useState("");
  async function create(e) {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const c = await api.post("/api/courses", { name });
      ctx.refresh();
      location.hash = `#/courses/${c.id}`;
    } catch (err) { ctx.notify(err.message, "error"); }
  }
  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Courses</h1><div className="sub">One page per course: deadlines, planned work, links, grades and notes.</div></div>
        <form className="row" onSubmit={create}>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="New course name" aria-label="New course name" />
          <button className="btn primary"><Icon name="plus" />New course</button>
        </form>
      </div>
      {ctx.courses.length === 0 && <div className="empty">No courses yet. Add one, or give a task a course and it shows up here.</div>}
      <div className="course-grid">
        {ctx.courses.filter((c) => c.kind === "course").map((c) => (
          <a key={c.id} className={`course-card c-${c.color}`} href={`#/courses/${c.id}`}>
            <div className="name"><span className="course-icon" style={{ width: 30, height: 30, fontSize: 12, borderRadius: 8 }}>{initials(c.name)}</span>{c.name}</div>
            <div className="nums">
              <span><b>{c.stats?.open ?? 0}</b> open</span>
              {c.stats?.grade != null && <span><b>{c.stats.grade.toFixed(1)}%</b> now</span>}
              {c.parser !== "none" && <span className={c.last_result?.error ? "err" : ""}>{c.last_result?.error ? "sync failing" : "synced from site"}</span>}
            </div>
            <div className="small muted ellipsis">{c.stats?.next_title ? <>Next: {c.stats.next_title} · {fmtDue(c.stats.next_due)}</> : "Nothing due"}</div>
          </a>
        ))}
      </div>
    </div>
  );
}

export function CoursePage({ id, ctx }) {
  const [d, setD] = useState(null);
  const [tab, setTab] = useState("overview");
  const load = () => api.get(`/api/courses/${id}`).then(setD).catch((e) => ctx.notify(e.message, "error"));
  useEffect(() => { load(); }, [id, ctx.version]);
  if (!d) return <div className="page muted">Loading…</div>;
  const c = d.course;
  if (c.kind !== "course") return <AreaPage d={d} ctx={ctx} reload={load} />;

  async function save(body, msg = "Saved") {
    try { await api.patch(`/api/courses/${c.id}`, body); ctx.notify(msg); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }

  const open = d.tasks.filter((t) => t.status === "open");
  const now = new Date();
  const upcoming = d.blocks.filter((b) => new Date(b.end) > now && b.status === "planned");
  const plannedMin = upcoming.reduce((n, b) => n + (new Date(b.end) - new Date(b.start)) / 60000, 0);

  return (
    <div className={`page c-${c.color}`}>
      <div className="course-head">
        <div className="course-icon">{initials(c.name)}</div>
        <Field className="course-title" value={c.name} onSave={(v) => v && save({ name: v }, "Renamed")} aria-label="Course name" />
        <div className="row muted small">
          <span><b style={{ color: "var(--text)" }}>{open.length}</b> open</span>·
          <span><b style={{ color: "var(--text)" }}>{fmtMinutes(Math.round(plannedMin))}</b> of work planned</span>
          {d.grades.current != null && <>·<span><b style={{ color: "var(--text)" }}>{d.grades.current.toFixed(1)}%</b> current grade</span></>}
        </div>
      </div>

      <div className="tabs">
        {[["overview", "Overview"], ["tasks", `Deadlines (${open.length})`], ["grades", "Grades"], ["notes", "Notes"], ["source", "Schedule page"]].map(([k, l]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>

      {tab === "overview" && <Overview d={d} ctx={ctx} save={save} setTab={setTab} />}
      {tab === "tasks" && <Tasks ctx={ctx} courseId={c.id} embedded />}
      {tab === "grades" && <Grades d={d} ctx={ctx} reload={load} save={save} />}
      {tab === "notes" && <Notes c={c} save={save} />}
      {tab === "source" && <Source c={c} ctx={ctx} save={save} reload={load} />}
    </div>
  );
}

function Overview({ d, ctx, save, setTab }) {
  const c = d.course;
  const now = new Date();
  const open = d.tasks.filter((t) => t.status === "open");
  const upcoming = d.blocks.filter((b) => new Date(b.end) > now && b.status === "planned").slice(0, 8);
  return (
    <div className="stack" style={{ gap: 24 }}>
      <div className="props" style={{ maxWidth: 640 }}>
        <div className="k">Instructor</div>
        <div className="v"><Field value={c.instructor} placeholder="Empty" onSave={(v) => save({ instructor: v })} /></div>
        <div className="k">Meets</div>
        <div className="v"><DayPicker value={c.class_days} onChange={(v) => save({ class_days: v })} />
          <Field type="time" value={c.class_time} onSave={(v) => v && save({ class_time: v })} /></div>
        <div className="k">Color</div>
        <div className="v swatches">
          {COLORS.map((x) => <button key={x} className={`swatch c-${x} ${c.color === x ? "on" : ""}`} onClick={() => save({ color: x })} aria-label={x} />)}
        </div>
        <div className="k">Schedule page</div>
        <div className="v small">
          {c.parser === "none" ? <button className="btn quiet sm" onClick={() => setTab("source")}>Add one to pull deadlines</button>
            : <span className={c.last_result?.error ? "err" : "muted"}>{c.last_result?.error ? "Last sync failed. See Schedule page" : c.last_sync_at ? `Synced ${fmtDue(c.last_sync_at)}` : "Not synced yet"}</span>}
        </div>
      </div>

      <div>
        <div className="section-title">Links</div>
        <Links c={c} save={save} />
      </div>

      <div className="today-grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
        <div>
          <div className="section-title">Coming up<button className="btn quiet sm" onClick={() => setTab("tasks")}>All deadlines</button></div>
          <div className="mini-list">
            {open.length === 0 && <div className="faint small">Nothing due.</div>}
            {open.slice(0, 8).map((t) => (
              <div key={t.id} className="mini" onClick={() => ctx.openTask(t.id)}>
                <span className="ellipsis grow">{t.title}{t.is_study && !t.confidence && <span className="tag accent" style={{ marginLeft: 6 }}>set confidence</span>}</span>
                <span className="when">{fmtDue(t.due)}</span>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div className="section-title">Planned work</div>
          <div className="blocks-list">
            {upcoming.length === 0 && <div className="faint small">No work blocks planned.</div>}
            {upcoming.map((b) => (
              <div key={b.id} className="blk" onClick={() => ctx.openBlock(b)}>
                <span style={{ minWidth: 92 }}>{fmtDay(b.start)}</span><span className="muted">{fmtTime(b.start)}</span>
                <span className="ellipsis">{b.title}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function Links({ c, save }) {
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ label: "", url: "" });
  function add(e) {
    e.preventDefault();
    if (!f.url.trim()) return;
    const url = /^https?:\/\//.test(f.url) ? f.url : `https://${f.url}`;
    const label = f.label || url.replace(/^https?:\/\/(www\.)?/, "").split("/").slice(0, 3).join("/");
    save({ links: [...c.links, { label, url }] }, "Link added");
    setF({ label: "", url: "" }); setAdding(false);
  }
  return (
    <div className="links">
      {c.links.map((l, i) => (
        <span key={i} className="link-chip">
          <Icon name={/github\.com/.test(l.url) ? "external" : "link"} size={14} />
          <a href={l.url} target="_blank" rel="noreferrer" style={{ textDecoration: "none" }}>{l.label || l.url}</a>
          <button className="icon-btn" aria-label="Remove link" onClick={() => save({ links: c.links.filter((_, j) => j !== i) }, "Removed")}><Icon name="x" size={12} /></button>
        </span>
      ))}
      {adding ? (
        <form className="row" onSubmit={add}>
          <input autoFocus value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} placeholder="github.com/you/repo" />
          <input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} placeholder="Label (optional)" />
          <button className="btn sm primary">Add</button>
          <button type="button" className="btn sm quiet" onClick={() => setAdding(false)}>Cancel</button>
        </form>
      ) : <button className="btn quiet sm" onClick={() => setAdding(true)}><Icon name="plus" size={14} />Add link (repo, Moodle, docs…)</button>}
    </div>
  );
}

function Grades({ d, ctx, reload, save }) {
  const g = d.grades;
  const c = d.course;
  const [importing, setImporting] = useState(false);
  async function call(fn, msg) {
    try { await fn(); if (msg) ctx.notify(msg); reload(); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }
  const neededText = g.needed == null ? "—" : g.needed <= 0 ? "Locked in" : `${g.needed}%`;
  const hasAttendance = (function walk(list) { return list.some((x) => x.is_attendance || walk(x.children || [])); })(g.categories);

  return (
    <div>
      <div className="grade-cards">
        <div className="grade-card"><div className="lbl">Current grade</div>
          <div className="big">{g.current == null ? "—" : `${g.current.toFixed(1)}%`}{g.letter && <span className="letter">{g.letter}</span>}</div>
          <div className="lbl">on the {g.graded_share}% of the course graded so far</div></div>
        <div className="grade-card"><div className="lbl">Target</div>
          <div className="row" style={{ gap: 2 }}><Field type="number" value={c.target_grade ?? ""} placeholder="90" onSave={(v) => save({ target_grade: v })} /><span className="big">%</span></div>
          <div className="lbl">{g.scale.length ? `cutoffs: ${g.scale.map((x) => `${x.letter} ${x.min}`).join(" · ")}` : "what you're aiming for"}</div></div>
        <div className="grade-card"><div className="lbl">You need</div>
          <div className={`big ${g.needed > 100 ? "err" : ""}`}>{c.target_grade == null ? "—" : neededText}</div>
          <div className="lbl">{c.target_grade == null ? "set a target" : g.needed > 100 ? "on average on what's left, which isn't reachable" : "average on everything still ahead"}</div></div>
      </div>

      <div className="row between" style={{ marginBottom: 12 }}>
        <span className="muted small">{g.categories.length ? "Weights in grey are each part's share of the whole course." : "No grading set up yet."}</span>
        <button className="btn" onClick={() => setImporting(!importing)}><Icon name="spark" size={14} />{g.categories.length ? "Re-read syllabus" : "Set up from syllabus"}</button>
      </div>
      {importing && <SyllabusImport c={c} ctx={ctx} replacing={g.categories.length > 0} onDone={() => { setImporting(false); reload(); ctx.refresh(); }} />}
      {g.weights_total > 0 && Math.round(g.weights_total) !== 100 && <div className="warn-line" style={{ marginBottom: 12 }}>Top-level weights add up to {g.weights_total}%. They're scaled to 100%, but check the syllabus.</div>}

      {hasAttendance && <AttendanceCard c={c} a={g.attendance} ctx={ctx} reload={reload} save={save} />}

      {g.categories.map((cat) => <CategoryNode key={cat.id} cat={cat} depth={0} call={call} courseId={c.id} />)}

      <NewCategory courseId={c.id} call={call} />
      {c.grade_policies && <div className="box pad" style={{ marginTop: 16 }}><div className="subhead">From the syllabus</div>
        <div className="small" style={{ whiteSpace: "pre-wrap" }}>{c.grade_policies}</div></div>}
      <div className="hint">Leave a score empty if it isn't graded yet. "You need" counts everything ungraded, empty categories and the class meetings still to come.</div>
    </div>
  );
}

function CategoryNode({ cat, depth, call, courseId }) {
  const [addingSub, setAddingSub] = useState(false);
  const leaf = cat.children.length === 0;
  return (
    <div className="cat" style={{ marginLeft: depth * 18 }}>
      <div className="cat-head">
        <Field className="name" value={cat.name} onSave={(v) => call(() => api.patch(`/api/grade-categories/${cat.id}`, { name: v }))} aria-label="Category" />
        <span className="row small muted" style={{ gap: 2 }}>
          <Field type="number" style={{ width: 56 }} value={cat.weight} onSave={(v) => call(() => api.patch(`/api/grade-categories/${cat.id}`, { weight: v }))} aria-label="Weight" />
          {depth === 0 ? "% of grade" : `% of parent`}
          {depth > 0 && <span className="faint">({cat.course_share}% overall)</span>}
        </span>
        {leaf && !cat.is_attendance && <span className="row small muted" style={{ gap: 2 }}>drop lowest <Field type="number" style={{ width: 44 }} value={cat.drop_lowest} onSave={(v) => call(() => api.patch(`/api/grade-categories/${cat.id}`, { drop_lowest: v }))} aria-label="Drop lowest" /></span>}
        {leaf && <label className="check-label small muted" title="Score this from the attendance counter"><input type="checkbox" checked={cat.is_attendance} onChange={(e) => call(() => api.patch(`/api/grade-categories/${cat.id}`, { is_attendance: e.target.checked }))} />attendance</label>}
        <b style={{ minWidth: 64, textAlign: "right" }}>{cat.percent == null ? "—" : `${cat.percent}%`}</b>
        {!cat.is_attendance && <button className="icon-btn" title="Add a subcategory" onClick={() => setAddingSub(!addingSub)}><Icon name="plus" size={14} /></button>}
        <button className="icon-btn" onClick={() => confirm(`Remove ${cat.name}${cat.children.length ? " and its parts" : ""}?`) && call(() => api.del(`/api/grade-categories/${cat.id}`), "Removed")} aria-label="Remove category"><Icon name="trash" size={14} /></button>
      </div>
      {addingSub && <div style={{ padding: "8px 10px" }}><NewCategory courseId={courseId} parentId={cat.id} call={call} onDone={() => setAddingSub(false)} sub /></div>}
      {leaf && cat.is_attendance && <div className="small muted" style={{ padding: "8px 12px" }}>Scored from the attendance counter above.</div>}
      {leaf && !cat.is_attendance && (
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>Item</th><th className="num" style={{ width: 90 }}>Score</th><th className="num" style={{ width: 90 }}>Out of</th><th className="num" style={{ width: 70 }}>%</th><th style={{ width: 30 }} /></tr></thead>
            <tbody>
              {cat.items.map((it) => (
                <tr key={it.id} className={cat.dropped.includes(it.id) ? "dropped" : ""} title={cat.dropped.includes(it.id) ? "Dropped (lowest)" : ""}>
                  <td><Field value={it.name} onSave={(v) => call(() => api.patch(`/api/grade-items/${it.id}`, { name: v }))} /></td>
                  <td><Field type="number" className="num" value={it.score ?? ""} placeholder="—" onSave={(v) => call(() => api.patch(`/api/grade-items/${it.id}`, { score: v }))} /></td>
                  <td><Field type="number" className="num" value={it.out_of} onSave={(v) => call(() => api.patch(`/api/grade-items/${it.id}`, { out_of: v }))} /></td>
                  <td className="num muted">{it.score == null ? "" : `${Math.round((1000 * it.score) / (it.out_of || 1)) / 10}`}</td>
                  <td><button className="icon-btn" onClick={() => call(() => api.del(`/api/grade-items/${it.id}`))} aria-label="Remove"><Icon name="x" size={12} /></button></td>
                </tr>
              ))}
              <AddItem onAdd={(f) => call(() => api.post(`/api/grade-categories/${cat.id}/items`, f))} />
            </tbody>
          </table>
        </div>
      )}
      {!leaf && <div style={{ padding: "8px 8px 0" }}>{cat.children.map((k) => <CategoryNode key={k.id} cat={k} depth={1} call={call} courseId={courseId} />)}</div>}
    </div>
  );
}

function NewCategory({ courseId, parentId, call, onDone, sub }) {
  const [name, setName] = useState("");
  const [weight, setWeight] = useState("");
  return (
    <form className="row" onSubmit={(e) => {
      e.preventDefault();
      if (!name) return;
      call(() => api.post(`/api/courses/${courseId}/grade-categories`, { name, weight: Number(weight) || 0, ...(parentId ? { parent_id: parentId } : {}) }), sub ? "Part added" : "Category added")
        .then(() => { setName(""); setWeight(""); onDone && onDone(); });
    }}>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder={sub ? "Part, e.g. Labs" : "Category, e.g. Homework"} autoFocus={sub} />
      <input type="number" value={weight} onChange={(e) => setWeight(e.target.value)} placeholder={sub ? "% of parent" : "Weight %"} style={{ width: 110 }} />
      <button className="btn"><Icon name="plus" size={14} />{sub ? "Add part" : "Add category"}</button>
    </form>
  );
}

function AttendanceCard({ c, a, ctx, reload, save }) {
  async function mark(body, msg) {
    try { await api.post(`/api/courses/${c.id}/attendance`, body); if (msg) ctx.notify(msg); reload(); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }
  const held = a.present + a.absent + a.excused;
  const counter = (status, label) => (
    <div className="counter">
      <div className="lbl">{label}</div>
      <div className="row" style={{ gap: 4 }}>
        <button className="icon-btn" disabled={!a[status]} onClick={() => mark({ undo: status })} aria-label={`One less ${label}`}>−</button>
        <b>{a[status]}</b>
        <button className="icon-btn" onClick={() => mark({ status }, `Marked ${status}`)} aria-label={`One more ${label}`}>+</button>
      </div>
    </div>
  );
  return (
    <div className="box pad" style={{ marginBottom: 12 }}>
      <div className="row between">
        <b>Attendance</b>
        <span className="small muted">{held} of {a.expected || "?"} meetings{a.allowed ? ` · ${a.free_left} free absence${a.free_left === 1 ? "" : "s"} left` : ""}</span>
      </div>
      <div className="row" style={{ gap: 24, marginTop: 8 }}>
        {counter("present", "Present")}{counter("absent", "Absent")}{counter("excused", "Excused")}
        <div className="counter"><div className="lbl">Free absences</div><Field type="number" style={{ width: 60 }} value={c.attend_allowed} onSave={(v) => save({ attend_allowed: v ?? 0 })} /></div>
        <div className="counter"><div className="lbl">Meetings in term</div><Field type="number" style={{ width: 70 }} value={c.attend_expected ?? ""} placeholder="?" onSave={(v) => save({ attend_expected: v })} /></div>
      </div>
      {a.absent > a.allowed && <div className="warn-line" style={{ marginTop: 10 }}>{a.absent - a.allowed} absence{a.absent - a.allowed > 1 ? "s" : ""} past the free ones now count against you.</div>}
      <div className="hint">Today's page asks after each class, or use + here.</div>
    </div>
  );
}

function SyllabusImport({ c, ctx, replacing, onDone }) {
  const [mode, setMode] = useState("file");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [prop, setProp] = useState(null);
  const file = useRef(null);
  async function read(e) {
    e?.preventDefault();
    setBusy(true); setProp(null);
    try {
      let res;
      if (mode === "file") {
        const f = file.current?.files?.[0];
        if (!f) { setBusy(false); return ctx.notify("Pick the syllabus file first", "error"); }
        const fd = new FormData(); fd.append("file", f);
        const r = await fetch(`/api/courses/${c.id}/syllabus/read`, { method: "POST", body: fd, credentials: "same-origin" });
        res = await r.json(); if (!r.ok) throw new Error(res.error || "Couldn't read it");
      } else {
        res = await api.post(`/api/courses/${c.id}/syllabus/read`, mode === "text" ? { text } : { url });
      }
      setProp(res);
    } catch (err) { ctx.notify(err.message, "error"); }
    setBusy(false);
  }
  async function apply() {
    try { await api.post(`/api/courses/${c.id}/syllabus/apply`, { proposal: prop, replace: true }); ctx.notify("Grading set up from the syllabus"); onDone(); }
    catch (err) { ctx.notify(err.message, "error"); }
  }
  const Tree = ({ list, depth = 0 }) => list.map((x, i) => (
    <React.Fragment key={i}>
      <div className="previewrow" style={{ paddingLeft: depth * 16 }}>
        <span>{x.name}{x.is_attendance ? " (attendance)" : ""}{x.drop_lowest ? ` · drop lowest ${x.drop_lowest}` : ""}
          {x.item_names?.length ? ` · ${x.item_names.join(", ")}` : x.count ? ` · ${x.count} items` : ""}</span>
        <b>{x.weight}%{depth ? " of parent" : ""}</b>
      </div>
      {x.subcategories?.length > 0 && <Tree list={x.subcategories} depth={depth + 1} />}
    </React.Fragment>
  ));
  return (
    <div className="box pad stack" style={{ gap: 10, marginBottom: 16 }}>
      <div className="row between"><b>Read the syllabus</b>
        <span className="seg">
          {[["file", "Upload"], ["text", "Paste"], ["url", "Link"]].map(([k, l]) => <button key={k} className={mode === k ? "on" : ""} onClick={() => setMode(k)}>{l}</button>)}
        </span>
      </div>
      <form className="row" onSubmit={read}>
        {mode === "file" && <input ref={file} type="file" accept=".pdf,.txt,.md,.html,.htm,image/*" className="grow" />}
        {mode === "text" && <textarea value={text} onChange={(e) => setText(e.target.value)} rows={6} className="grow" placeholder="Paste the grading section (or the whole syllabus)" />}
        {mode === "url" && <input value={url} onChange={(e) => setUrl(e.target.value)} className="grow" placeholder="https://…/syllabus.pdf (public pages only; Moodle files need Upload)" />}
        <button className="btn primary" disabled={busy}>{busy ? "Reading…" : "Read it"}</button>
      </form>
      {prop && (
        <div className="stack" style={{ gap: 8 }}>
          <div className="preview" style={{ maxHeight: "none" }}><Tree list={prop.categories} /></div>
          {prop.grade_scale?.length > 0 && <div className="small muted">Letter cutoffs: {prop.grade_scale.map((x) => `${x.letter} ${x.min}`).join(" · ")}</div>}
          {prop.attendance && <div className="small muted">Attendance: {prop.attendance.free_absences ?? 0} free absences{prop.attendance.class_meetings ? `, ${prop.attendance.class_meetings} meetings` : ""}{prop.attendance.policy ? `. ${prop.attendance.policy}` : ""}</div>}
          {[...(prop.check || []), ...(prop.notes ? [prop.notes] : [])].map((m, i) => <div key={i} className="warn-line">{m}</div>)}
          <div className="row">
            <button className="btn ok" onClick={apply}>{replacing ? "Replace my grading setup with this" : "Use this"}</button>
            <span className="faint small">{replacing ? "Scores you've entered in the old categories will be removed." : "You can edit anything afterwards."}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function AddItem({ onAdd }) {
  const [f, setF] = useState({ name: "", score: "", out_of: "100" });
  function add(e) {
    e.preventDefault();
    if (!f.name) return;
    onAdd(f);
    setF({ name: "", score: "", out_of: f.out_of });
  }
  return (
    <tr>
      <td><input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="+ Add item" onKeyDown={(e) => e.key === "Enter" && add(e)} /></td>
      <td><input type="number" className="num" value={f.score} onChange={(e) => setF({ ...f, score: e.target.value })} placeholder="score" onKeyDown={(e) => e.key === "Enter" && add(e)} /></td>
      <td><input type="number" className="num" value={f.out_of} onChange={(e) => setF({ ...f, out_of: e.target.value })} onKeyDown={(e) => e.key === "Enter" && add(e)} /></td>
      <td colSpan={2}><button className="btn sm quiet" onClick={add} disabled={!f.name}>Add</button></td>
    </tr>
  );
}

function Notes({ c, save }) {
  const [v, setV] = useState(c.notes || "");
  const [dirty, setDirty] = useState(false);
  useEffect(() => { setV(c.notes || ""); setDirty(false); }, [c.notes]);
  return (
    <div>
      <textarea className="notes" value={v} placeholder="Office hours, exam rules, things the professor stressed, repo setup…"
                onChange={(e) => { setV(e.target.value); setDirty(true); }}
                onBlur={() => { if (dirty) { save({ notes: v }, "Notes saved"); setDirty(false); } }} />
      <div className="faint small">{dirty ? "Unsaved. Click outside to save" : "Saved"}</div>
    </div>
  );
}

function Source({ c, ctx, save, reload }) {
  const [url, setUrl] = useState(c.url);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const r = c.last_result || {};
  useEffect(() => setUrl(c.url), [c.url]);

  async function test() {
    setPreview({ loading: true });
    try { setPreview({ items: (await api.post(`/api/courses/${c.id}/test`)).items }); }
    catch (e) { setPreview({ error: e.message }); }
  }
  async function syncNow() {
    setBusy(true);
    try {
      const x = (await api.post(`/api/courses/${c.id}/sync`)).result;
      ctx.notify(x.error ? `Couldn't read it: ${x.error}` : `Found ${x.found}: ${x.created} added${x.suggested ? `, ${x.suggested} to review in Tasks` : ""}`, x.error ? "error" : "ok");
      reload(); ctx.refresh();
    } catch (e) { ctx.notify(e.message, "error"); }
    setBusy(false);
  }
  const parsers = { ai: "AI extraction (works on any page)", csse304: "Built-in reader: CSSE 304", csse313: "Built-in reader: CSSE 313" };

  return (
    <div className="stack" style={{ maxWidth: 720, gap: 18 }}>
      <p className="muted" style={{ margin: 0 }}>Paste the course's schedule or calendar page and every Sync pulls new deadlines from it. New quarter, new page: change it here or switch it off.</p>
      <div className="props">
        <div className="k">Page URL</div>
        <div className="v"><input className="grow" value={url} placeholder="https://…/schedule" onChange={(e) => setUrl(e.target.value)}
          onBlur={() => url !== c.url && save({ url, ...(url && c.parser === "none" ? { parser: "ai" } : {}) }, url ? "Saved. Hit Test" : "Removed")} /></div>
        <div className="k">Read it with</div>
        <div className="v">
          <select value={c.parser === "none" ? "ai" : c.parser} disabled={!c.url} onChange={(e) => save({ parser: e.target.value })}>
            {Object.entries(parsers).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          {c.parser === "ai" && !ctx.aiReady && <span className="tag warn">needs GEMINI_API_KEY on Render</span>}
        </div>
        <div className="k">Page uses JavaScript</div>
        <div className="v"><input type="checkbox" checked={c.render_js} onChange={(e) => save({ render_js: e.target.checked })} />
          <span className="faint small">Turn on if Test finds nothing. Slower, since it opens a headless browser.</span></div>
        <div className="k">Review first</div>
        <div className="v"><input type="checkbox" checked={c.review} onChange={(e) => save({ review: e.target.checked })} />
          <span className="faint small">New items wait in Tasks for you to accept</span></div>
        <div className="k">Sync it</div>
        <div className="v"><input type="checkbox" checked={c.enabled} onChange={(e) => save({ enabled: e.target.checked }, e.target.checked ? "On" : "Off. Kept the tasks it already added")} /></div>
        <div className="k">Last sync</div>
        <div className={`v small ${r.error ? "err" : "muted"}`} style={{ alignItems: "flex-start", paddingTop: 7 }}>
          {!c.last_sync_at ? "Never" : r.error ? r.error : `${fmtDue(c.last_sync_at)}: found ${r.found}, added ${r.created || 0}${r.suggested ? `, ${r.suggested} to review` : ""}${r.unchanged ? " (page unchanged)" : ""}`}
        </div>
      </div>
      <div className="row">
        <button className="btn" onClick={test} disabled={!c.url || preview?.loading}>{preview?.loading ? "Reading the page…" : "Test (saves nothing)"}</button>
        <button className="btn primary" onClick={syncNow} disabled={!c.url || busy}>{busy ? "Syncing…" : "Sync now"}</button>
        <span className="grow" />
        <button className="btn quiet danger sm" onClick={() => confirm(`Delete the ${c.name} course page? Its tasks stay, without a course.`) &&
          api.del(`/api/courses/${c.id}`).then(() => { ctx.refresh(); location.hash = "#/courses"; })}>Delete course</button>
      </div>
      {preview && !preview.loading && (
        <div className="preview">
          {preview.error && <div className="err small">{preview.error}</div>}
          {preview.items?.length === 0 && <div className="muted small">No deadlines found. Try turning on "Page uses JavaScript", or AI extraction.</div>}
          {preview.items?.length > 0 && <div className="muted small">Found {preview.items.length}:</div>}
          {preview.items?.map((i, k) => (
            <div key={k} className={`previewrow ${i.past ? "faint" : ""}`}>
              <span>{i.title}</span>
              <span className="muted">{fmtDay(i.due)} {fmtTime(i.due)} · {i.past ? "past, skipped" : i.status === "new" ? "new" : `already ${i.status}`}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DayPicker({ value, onChange }) {
  const toggle = (d) => onChange(value.includes(d) ? value.replace(d, "") : value + d);
  return (
    <span className="days">
      {DAYS.map(([d, l]) => <button type="button" key={d} className={`day ${value.includes(d) ? "on" : ""}`} onClick={() => toggle(d)} title={l}>{l[0]}</button>)}
    </span>
  );
}


const AREA_TABS = {
  career: [["main", "Applications"], ["tasks", "To do"], ["notes", "Notes"]],
  fitness: [["main", "Log"], ["tasks", "To do"], ["notes", "Notes"]],
  finance: [["main", "Money"], ["tasks", "To do"], ["notes", "Notes"]],
  personal: [["tasks", "To do"], ["notes", "Notes"]],
};

function AreaPage({ d, ctx, reload }) {
  const c = d.course;
  const tabs = AREA_TABS[c.kind] || AREA_TABS.personal;
  const [tab, setTab] = useState(tabs[0][0]);
  const open = d.tasks.filter((t) => t.status === "open");
  async function save(body, msg = "Saved") {
    try { await api.patch(`/api/courses/${c.id}`, body); ctx.notify(msg); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
  }
  return (
    <div className={`page c-${c.color}`}>
      <div className="course-head">
        <div className="course-icon"><Icon name={{ career: "briefcase", fitness: "heart", finance: "wallet" }[c.kind] || "book"} size={20} /></div>
        <Field className="course-title" value={c.name} onSave={(v) => v && save({ name: v }, "Renamed")} aria-label="Area name" />
        <div className="row muted small"><span><b style={{ color: "var(--text)" }}>{open.length}</b> open</span>
          <span className="faint">·</span>
          <span className="row" style={{ gap: 4 }}>Quick-add files tasks here when they mention
            <Field value={c.keywords} placeholder="keywords, comma separated" style={{ width: 240 }} onSave={(v) => save({ keywords: v }, "Keywords saved")} /></span>
        </div>
        <Links c={c} save={save} />
      </div>
      <div className="tabs">
        {tabs.map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l === "To do" ? `To do (${open.length})` : l}</button>)}
      </div>
      {tab === "main" && c.kind === "career" && <CareerModule area={c} ctx={ctx} />}
      {tab === "main" && c.kind === "fitness" && <FitnessModule area={c} ctx={ctx} />}
      {tab === "main" && c.kind === "finance" && <FinanceModule area={c} ctx={ctx} />}
      {tab === "tasks" && <Tasks ctx={ctx} courseId={c.id} embedded />}
      {tab === "notes" && <Notes c={c} save={save} />}
    </div>
  );
}
