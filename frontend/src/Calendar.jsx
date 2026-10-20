import React, { useEffect, useRef, useState } from "react";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import { api, minutesBetween, toLocalISO } from "./api.js";
import { Icon, Seg, useStored } from "./ui.jsx";

export default function Calendar({ ctx }) {
  const cal = useRef(null);
  const [range, setRange] = useState(null);
  const [data, setData] = useState({ blocks: [], events: [] });
  const [title, setTitle] = useState("");
  const [view, setView] = useStored("cal.view", window.innerWidth < 700 ? "timeGrid3Day" : "timeGridWeek");
  const [showRoutines, setShowRoutines] = useStored("cal.routines", true);

  useEffect(() => {
    if (!range) return;
    api.get(`/api/blocks?start=${toLocalISO(range.start)}&end=${toLocalISO(range.end)}`)
      .then(setData).catch((e) => ctx.notify(e.message, "error"));
  }, [range, ctx.version]);

  const events = [
    ...data.events.map((e) => ({ id: `e${e.id}`, start: e.start, end: e.end, title: e.title, editable: false,
      extendedProps: { block: { ...e, kind: "event" } } })),
    ...data.blocks.filter((b) => showRoutines || b.kind === "task" || b.kind === "busy").map((b) => ({
      id: `b${b.id}`, start: b.start, end: b.end, title: b.title,
      editable: b.status === "planned", extendedProps: { block: b },
    })),
  ];

  async function onChange(info) {
    const b = info.event.extendedProps.block;
    try {
      await api.patch(`/api/blocks/${b.id}`, { start: toLocalISO(info.event.start), end: toLocalISO(info.event.end) });
      ctx.notify("Pinned there. Everything else replanned around it");
      ctx.refresh();
    } catch (e) {
      info.revert();
      ctx.notify(e.message, "error");
    }
  }

  function renderEvent(arg) {
    const b = arg.event.extendedProps.block;
    const mins = minutesBetween(b.start, b.end);
    const course = ctx.courseById[b.course_id];
    const cls = `ev ${b.kind} ${b.status || ""} c-${course?.color || "blue"} ${mins < 30 ? "tiny" : ""} ${mins < 10 ? "sliver" : ""}`;
    return (
      <div className={cls} title={`${b.title} · ${arg.timeText}`}>
        <span className="ev-t">{b.title}{b.locked && <span className="pin">●</span>}</span>
        {mins >= 40 && <div className="ev-time">{arg.timeText}</div>}
      </div>
    );
  }

  const api_ = () => cal.current?.getApi();
  const changeView = (v) => { setView(v); api_()?.changeView(v); };

  return (
    <div className="page wide">
      <div className="cal-tools">
        <div className="row">
          <h1 style={{ minWidth: 180 }}>{title}</h1>
          <button className="btn sm" onClick={() => api_()?.today()}>Today</button>
          <button className="icon-btn" onClick={() => api_()?.prev()} aria-label="Previous">‹</button>
          <button className="icon-btn" onClick={() => api_()?.next()} aria-label="Next">›</button>
        </div>
        <div className="row">
          <label className="check-label muted"><input type="checkbox" checked={showRoutines} onChange={(e) => setShowRoutines(e.target.checked)} />Meals and routines</label>
          <Seg value={view} onChange={changeView} options={[["timeGridDay", "Day"], ["timeGrid3Day", "3 days"], ["timeGridWeek", "Week"]]} />
        </div>
      </div>
      <div className="cal-wrap">
        <FullCalendar
          ref={cal}
          plugins={[timeGridPlugin, interactionPlugin]}
          initialView={view}
          views={{ timeGrid3Day: { type: "timeGrid", duration: { days: 3 } } }}
          headerToolbar={false}
          firstDay={1}
          slotMinTime="07:00:00"
          slotMaxTime="24:00:00"
          scrollTime="08:00:00"
          slotDuration="00:30:00"
          slotLabelFormat={{ hour: "numeric", meridiem: "short" }}
          dayHeaderFormat={{ weekday: "short", day: "numeric" }}
          nowIndicator
          eventMinHeight={2}
          allDaySlot={false}
          snapDuration="00:05:00"
          height="100%"
          expandRows
          events={events}
          eventContent={renderEvent}
          eventDrop={onChange}
          eventResize={onChange}
          eventClick={(info) => ctx.openBlock(info.event.extendedProps.block)}
          datesSet={(arg) => { setRange({ start: arg.start, end: arg.end }); setTitle(arg.view.title); }}
        />
      </div>
      <div className="hint"><Icon name="pin" size={12} /> Drag to move, drag the bottom edge to resize, click for Done / Not now / Skip. Anything you move stays pinned.</div>
    </div>
  );
}
