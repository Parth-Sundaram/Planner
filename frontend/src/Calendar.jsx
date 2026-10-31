import React, { useEffect, useRef, useState } from "react";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import { api, minutesBetween, toLocalISO } from "./api.js";
import { Icon, Seg, useStored } from "./ui.jsx";
import { CATS, Sample, parseStyle, styleKey } from "./calstyle.jsx";

const ZOOMS = ["Fit", "75%", "100%", "140%"];
const KEYS = [["T", "Today"], ["← →", "Prev / next"], ["D", "Day view"], ["W", "Week view"], ["N", "New event"], ["R", "Rebalance"]];

export default function Calendar({ ctx }) {
  const cal = useRef(null);
  const [range, setRange] = useState(null);
  const [data, setData] = useState({ blocks: [], events: [], dirty: false });
  const [zoom, setZoom] = useStored("cal.zoom", 2);   // 0 = fit the screen, 1-3 = taller hours
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState({ month: "", range: "" });
  const [menu, setMenu] = useState(false);
  const [look, setLook] = useState(parseStyle());
  const [view, setView] = useStored("cal.view", window.innerWidth < 700 ? "timeGrid3Day" : "timeGridWeek");
  const [showRoutines, setShowRoutines] = useStored("cal.routines", true);
  const [newAppt, setNewAppt] = useState(null);   // {start, end}
  const [apptTitle, setApptTitle] = useState("");

  useEffect(() => {
    if (!range) return;
    api.get(`/api/blocks?start=${toLocalISO(range.start)}&end=${toLocalISO(range.end)}`)
      .then(setData).catch((e) => ctx.notify(e.message, "error"));
  }, [range, ctx.version]);

  useEffect(() => { api.get("/api/settings").then((s) => setLook(parseStyle(s.calendar_style))).catch(() => {}); }, [ctx.version]);

  const events = [
    ...data.events.map((e) => ({ id: `e${e.id}`, start: e.start, end: e.end, title: e.title, editable: false,
      extendedProps: { block: { ...e, kind: "event" } } })),
    ...data.blocks.filter((b) => showRoutines || b.category !== "routine").map((b) => ({
      id: `b${b.id}`, start: b.start, end: b.end, title: b.title,
      editable: b.status === "planned", extendedProps: { block: b },
    })),
  ];

  // Changes made here are saved and pinned but not rebalanced; the Rebalance button does that.
  async function onChange(info) {
    const b = info.event.extendedProps.block;
    try {
      await api.patch(`/api/blocks/${b.id}?replan=0`, { start: toLocalISO(info.event.start), end: toLocalISO(info.event.end) });
      ctx.notify("Pinned there. Press Rebalance when you're done moving things");
      ctx.refresh();
    } catch (e) {
      info.revert();
      ctx.notify(e.message, "error");
    }
  }

  async function rebalance() {
    setBusy(true);
    try { await api.post("/api/replan"); ctx.notify("Rebalanced around your changes"); ctx.refresh(); }
    catch (e) { ctx.notify(e.message, "error"); }
    finally { setBusy(false); }
  }

  // What the × on a block does, or null when it has none.
  function removal(b) {
    if (b.kind === "event" || b.status !== "planned") return null;
    if (b.kind === "task") return { label: "Delete task", run: () => confirm(`Delete "${b.title}" and all of its planned time?`)
      && api.del(`/api/tasks/${b.task_id}?replan=0`), msg: "Task deleted" };
    if (b.kind === "appointment" || b.kind === "busy") return { label: "Delete", run: () => api.post(`/api/blocks/${b.id}/remove?replan=0`), msg: "Deleted" };
    if (b.kind === "routine" && !["class", "appointment"].includes(b.category))
      return { label: "Skip this day", run: () => api.post(`/api/blocks/${b.id}/skip?replan=0`), msg: "Skipped for that day" };
    return null;
  }

  async function onClick(info) {
    const b = info.event.extendedProps.block;
    if (info.jsEvent.target.closest?.(".ev-del")) {
      const r = removal(b);
      try { if (r && (await r.run()) !== false) { ctx.notify(`${r.msg}. Press Rebalance when you're done`); ctx.refresh(); } }
      catch (e) { ctx.notify(e.message, "error"); }
      return;
    }
    ctx.openBlock(b, { defer: true });
  }

  function renderEvent(arg) {
    const b = arg.event.extendedProps.block;
    const mins = minutesBetween(b.start, b.end);
    const course = ctx.courseById[b.course_id];
    const cat = b.kind === "event" ? "event" : b.category || b.kind;
    const s = look[styleKey(cat)];
    const color = s.color === "course" ? course?.color || "blue" : s.color;
    const when = arg.isPast ? "past" : arg.isFuture ? "" : "now";
    const cls = `ev ${b.kind} cat-${cat} st-${s.style} ${b.status || ""} ${when} c-${color} ${mins < 30 ? "tiny" : ""} ${mins < 10 ? "sliver" : ""}`;
    return (
      <div className={cls} title={`${b.title} · ${arg.timeText}`}>
        {removal(b) && <button type="button" className="ev-del" title={removal(b).label} aria-label={removal(b).label}><Icon name={b.kind === "routine" ? "x" : "trash"} size={11} /></button>}
        <span className="ev-t">{["appointment", "meeting", "class"].includes(cat) && <i className={`ev-ico ${cat === "class" ? "sq" : "dia"}`} />}
          {b.title}{b.locked && cat === "work" && <span className="pin">●</span>}</span>
        {mins >= 40 && s.style !== "text" && <div className="ev-time">{arg.timeText}</div>}
      </div>
    );
  }

  // Keyboard: T today, arrows prev/next, D day, W week, N new appointment, R rebalance.
  useEffect(() => {
    function onKey(e) {
      if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select, [contenteditable]") || newAppt) return;
      const k = e.key.toLowerCase();
      if (k === "t") api_()?.today();
      else if (k === "arrowleft") api_()?.prev();
      else if (k === "arrowright") api_()?.next();
      else if (k === "d") changeView("timeGridDay");
      else if (k === "w") changeView("timeGridWeek");
      else if (k === "r") rebalance();
      else if (k === "n") {
        const s = new Date(); s.setMinutes(0, 0, 0); s.setHours(s.getHours() + 1);
        setNewAppt({ start: toLocalISO(s), end: toLocalISO(new Date(s.getTime() + 3600e3)) }); setApptTitle("");
      } else if (k === "escape") setMenu(false);
      else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const api_ = () => cal.current?.getApi();
  const changeView = (v) => { setView(v); api_()?.changeView(v); };

  return (
    <div className="page wide">
      <div className="cal-tools">
        <div className="row" style={{ gap: 14 }}>
          <h1>{title.month} <span className="cal-range">{title.range}</span></h1>
          <span className="row" style={{ gap: 2 }}>
            <button className="icon-btn" onClick={() => api_()?.prev()} aria-label="Previous"><Icon name="chevron-left" size={14} /></button>
            <button className="btn sm" onClick={() => api_()?.today()}>Today</button>
            <button className="icon-btn" onClick={() => api_()?.next()} aria-label="Next"><Icon name="chevron-right" size={14} /></button>
          </span>
        </div>
        <div className="row" style={{ gap: 8, position: "relative" }}>
          {(data.dirty || busy) && (
            <button className="btn sm primary pill" onClick={rebalance} disabled={busy}
                    title="Re-place the work the planner manages around everything you moved or pinned (R)">
              <Icon name="refresh" size={13} />{busy ? "Rebalancing…" : "Rebalance"}
            </button>
          )}
          <Seg value={view} onChange={changeView} options={[["timeGridDay", "Day"], ["timeGrid3Day", "3 days"], ["timeGridWeek", "Week"]]} />
          <button className={`icon-btn ${menu ? "on" : ""}`} onClick={() => setMenu(!menu)} aria-label="Calendar options" aria-expanded={menu}><Icon name="dots" /></button>
          {menu && (
            <>
              <div className="menu-backdrop" onClick={() => setMenu(false)} />
              <div className="cal-menu">
                <label className="cal-menu-row">Meals & routines<input type="checkbox" checked={showRoutines} onChange={(e) => setShowRoutines(e.target.checked)} /></label>
                <div className="cal-menu-row">Zoom
                  <span className="row" style={{ gap: 2 }}>
                    <button className="icon-btn" onClick={() => setZoom(Math.max(0, zoom - 1))} disabled={zoom === 0} aria-label="Zoom out">−</button>
                    <span className="zoom-val">{ZOOMS[zoom]}</span>
                    <button className="icon-btn" onClick={() => setZoom(Math.min(3, zoom + 1))} disabled={zoom === 3} aria-label="Zoom in">+</button>
                  </span>
                </div>
                <div className="sep" />
                <div className="cal-menu-head">What the colors mean <a href="#/settings" onClick={() => setMenu(false)}>Change</a></div>
                {CATS.map(([k, label]) => <div key={k} className="cal-menu-key"><Sample s={look[k]} />{label}</div>)}
                <div className="sep" />
                <div className="cal-menu-head">Shortcuts</div>
                <div className="cal-keys">{KEYS.map(([k, l]) => <span key={k}><kbd>{k}</kbd>{l}</span>)}</div>
                <div className="cal-menu-tip">Drag across empty time to add an appointment. Moved blocks stay pinned; nothing else moves until you rebalance.</div>
              </div>
            </>
          )}
        </div>
      </div>
      <div className={`cal-wrap zoom-${zoom}`}>
        <FullCalendar
          ref={cal}
          plugins={[timeGridPlugin, interactionPlugin]}
          initialView={view}
          views={{ timeGrid3Day: { type: "timeGrid", duration: { days: 3 } } }}
          headerToolbar={false}
          firstDay={1}
          slotMinTime="07:00:00"
          slotMaxTime="24:00:00"
          scrollTime={`${String(Math.max(7, new Date().getHours() - 1)).padStart(2, "0")}:00:00`}
          slotDuration="00:30:00"
          slotLabelContent={(a) => { const h = a.date.getHours(); return `${(h + 11) % 12 + 1} ${h < 12 ? "AM" : "PM"}`; }}
          dayHeaderContent={(a) => (
            <span className="dh"><span className="dow">{a.date.toLocaleDateString([], { weekday: "short" }).toUpperCase()}</span>
              <span className={`dn ${a.isToday ? "today" : ""}`}>{a.date.getDate()}</span></span>
          )}
          nowIndicator
          eventMinHeight={2}
          allDaySlot={false}
          snapDuration="00:05:00"
          height="100%"
          expandRows={zoom === 0}
          events={events}
          eventContent={renderEvent}
          eventDrop={onChange}
          eventResize={onChange}
          eventClick={onClick}
          selectable
          selectMirror
          select={(info) => { setNewAppt({ start: toLocalISO(info.start), end: toLocalISO(info.end) }); setApptTitle(""); }}
          datesSet={(arg) => {
            setRange({ start: arg.start, end: arg.end });
            const mid = new Date((arg.view.currentStart.getTime() + arg.view.currentEnd.getTime()) / 2);
            setTitle({ month: mid.toLocaleDateString([], { month: "long", year: "numeric" }),
              range: arg.view.title.replace(/, \d{4}$/, "") });
          }}
        />
      </div>
      {newAppt && (
        <div className="modal" onClick={() => { setNewAppt(null); api_()?.unselect(); }}>
          <form className="box pad stack" style={{ width: "min(380px, 100%)", gap: 10, background: "var(--surface)" }} onClick={(e) => e.stopPropagation()}
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (!apptTitle.trim()) return;
                  try { await api.post("/api/appointments?replan=0", { title: apptTitle, ...newAppt }); ctx.notify("Added. Press Rebalance to fit the plan around it"); setNewAppt(null); ctx.refresh(); }
                  catch (err) { ctx.notify(err.message, "error"); }
                }}>
            <b>New appointment</b>
            <div className="muted small">{new Date(newAppt.start).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} – {new Date(newAppt.end).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</div>
            <input autoFocus value={apptTitle} onChange={(e) => setApptTitle(e.target.value)} placeholder="Meeting with Rishi, dentist, office hours…" />
            <div className="row end"><button type="button" className="btn quiet" onClick={() => setNewAppt(null)}>Cancel</button><button className="btn primary">Add</button></div>
          </form>
        </div>
      )}
    </div>
  );
}