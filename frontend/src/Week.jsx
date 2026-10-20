import React, { useEffect, useRef, useState } from "react";
import FullCalendar from "@fullcalendar/react";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import { api, toLocalISO } from "./api.js";

const COLORS = { task: "#3b6fd8", routine: "#3f9a6b", busy: "#8a8f98", event: "#b9bec7" };

export default function Week({ version, refresh, notify }) {
  const [range, setRange] = useState(null);
  const [events, setEvents] = useState([]);
  const calRef = useRef(null);

  useEffect(() => {
    if (!range) return;
    api.get(`/api/blocks?start=${toLocalISO(range.start)}&end=${toLocalISO(range.end)}`).then((d) => {
      setEvents([
        ...d.events.map((e) => ({ id: `e${e.id}`, title: e.title, start: e.start, end: e.end, editable: false,
          backgroundColor: COLORS.event, borderColor: COLORS.event, textColor: "#222" })),
        ...d.blocks.map((b) => ({
          id: `b${b.id}`, title: (b.locked ? "📌 " : "") + b.title + (b.status === "done" ? " ✓" : ""),
          start: b.start, end: b.end, editable: b.status === "planned",
          backgroundColor: COLORS[b.kind], borderColor: COLORS[b.kind],
          classNames: [b.status], extendedProps: { block: b },
        })),
      ]);
    }).catch((e) => notify(e.message, "error"));
  }, [range, version]);

  async function onChange(info) {
    const b = info.event.extendedProps.block;
    try {
      await api.patch(`/api/blocks/${b.id}`, { start: toLocalISO(info.event.start), end: toLocalISO(info.event.end) });
      notify("Pinned there; replanned everything else");
      refresh();
    } catch (e) {
      info.revert();
      notify(e.message, "error");
    }
  }

  return (
    <div className="card week">
      <FullCalendar
        ref={calRef}
        plugins={[timeGridPlugin, interactionPlugin]}
        initialView={window.innerWidth < 700 ? "timeGrid3Day" : "timeGridWeek"}
        views={{ timeGrid3Day: { type: "timeGrid", duration: { days: 3 } } }}
        headerToolbar={{ left: "prev,next today", center: "title", right: "timeGridDay,timeGrid3Day,timeGridWeek" }}
        buttonText={{ day: "Day", timeGrid3Day: "3 days", week: "Week", today: "Today" }}
        firstDay={1}
        slotMinTime="07:00:00"
        slotMaxTime="24:00:00"
        scrollTime="08:00:00"
        nowIndicator
        eventMinHeight={14}
        slotEventOverlap={false}
        allDaySlot={false}
        snapDuration="00:05:00"
        height="auto"
        events={events}
        eventDrop={onChange}
        eventResize={onChange}
        datesSet={(arg) => setRange({ start: arg.start, end: arg.end })}
      />
      <p className="muted small">Drag a block to move it or drag its bottom edge to resize. Moved blocks are pinned; everything else replans around them.</p>
    </div>
  );
}
