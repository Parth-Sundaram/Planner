import React, { useEffect, useRef, useState } from "react";

const PATHS = {
  today: "M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4M8 5.2a2.8 2.8 0 1 0 0 5.6 2.8 2.8 0 0 0 0-5.6z",
  calendar: "M2.5 4.5h11v9h-11zM2.5 7h11M5.5 2.5v3M10.5 2.5v3",
  tasks: "M3 4.5l1.2 1.2L6.5 3.4M3 10.5l1.2 1.2 2.3-2.3M8.5 4.5h5M8.5 10.5h5",
  book: "M3 2.5h7.5a2 2 0 0 1 2 2v9H5a2 2 0 0 1-2-2zM3 11.5a2 2 0 0 1 2-2h7.5",
  settings: "M8 5.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8zM8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1",
  sync: "M13 3.5v3h-3M3 12.5v-3h3M12.4 6.5A4.8 4.8 0 0 0 3.8 5M3.6 9.5A4.8 4.8 0 0 0 12.2 11",
  plus: "M8 3v10M3 8h10",
  x: "M4 4l8 8M12 4l-8 8",
  check: "M3.5 8.3l3 3 6-6.6",
  up: "M8 12.5v-9M4.5 7L8 3.5 11.5 7",
  down: "M8 3.5v9M4.5 9L8 12.5 11.5 9",
  link: "M6.8 9.2l2.4-2.4M7.4 4.6l1.1-1.1a2.6 2.6 0 0 1 3.7 3.7l-1.1 1.1M8.6 11.4l-1.1 1.1a2.6 2.6 0 0 1-3.7-3.7l1.1-1.1",
  menu: "M2.5 4.5h11M2.5 8h11M2.5 11.5h11",
  pin: "M8 9.5v4.5M5 3.5h6M5.8 3.5l-.8 5h6l-.8-5",
  inbox: "M2.5 9l1.8-5.5h7.4L13.5 9v4h-11zM2.5 9H6l.8 1.5h2.4L10 9h3.5",
  external: "M9.5 2.5h4v4M13.5 2.5L8 8M12 9.5v4H2.5V4h4",
  trash: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 9h5.8l.6-9",
  briefcase: "M2.5 5.5h11v7.5h-11zM6 5.5V3.5h4v2M2.5 9h11",
  heart: "M8 13.5S2.5 10.2 2.5 6.3A2.8 2.8 0 0 1 8 5a2.8 2.8 0 0 1 5.5 1.3C13.5 10.2 8 13.5 8 13.5z",
  wallet: "M2.5 4.5h10a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1h-10zM2.5 4.5l8-2v2M10.5 9h3",
  hand: "M4.5 8.5V4.5a1 1 0 0 1 2 0V8M6.5 7V3.5a1 1 0 0 1 2 0V7M8.5 7V4a1 1 0 0 1 2 0v4.5M10.5 7a1 1 0 0 1 2 0v2.5a4 4 0 0 1-4 4h-.6a3.5 3.5 0 0 1-2.8-1.4L3 9.6a1 1 0 0 1 1.5-1.3",
  spark: "M8 2v3M8 11v3M2 8h3M11 8h3M4 4l2 2M10 10l2 2M12 4l-2 2M6 10l-2 2",
  camera: "M2.5 5h2.5l1-1.5h4L11 5h2.5v7.5h-11zM8 6.8a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4z",
  send: "M2.5 8l11-5-4 11-2-4.5z",
};

export function Icon({ name, size = 16, className = "" }) {
  return (
    <svg className={`icon ${className}`} width={size} height={size} viewBox="0 0 16 16" fill="none"
         stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}

export function Panel({ onClose, children, actions }) {
  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="panel" role="dialog">
        <div className="panel-head">
          <div className="row grow">{actions}</div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <div className="panel-body">{children}</div>
      </aside>
    </>
  );
}

export function CourseTag({ course }) {
  if (!course) return null;
  return <span className={`tag course c-${course.color}`}><span className="dot" />{course.name}</span>;
}

export const CONFIDENCE = [
  [1, "Lost", "10h over a week"], [2, "Shaky", "7h over 6 days"], [3, "Okay", "4h over 4 days"],
  [4, "Good", "2h over 3 days"], [5, "Ready", "1h the day before"],
];

export function Confidence({ value, onChange }) {
  return (
    <span className="confidence" role="radiogroup" aria-label="How ready do you feel?">
      {CONFIDENCE.map(([v, label, hint]) => (
        <button key={v} type="button" className={value === v ? "on" : ""} title={hint}
                onClick={() => onChange(value === v ? null : v)}>{label}</button>
      ))}
    </span>
  );
}

// An input that saves when you leave it (Notion-style properties).
export function Field({ value, onSave, type = "text", ...rest }) {
  const [v, setV] = useState(value ?? "");
  const last = useRef(value ?? "");
  useEffect(() => { setV(value ?? ""); last.current = value ?? ""; }, [value]);
  function commit() {
    if (String(v) === String(last.current)) return;
    last.current = v;
    onSave(type === "number" ? (v === "" ? null : Number(v)) : v);
  }
  return (
    <input type={type} value={v} onChange={(e) => setV(e.target.value)} onBlur={commit}
           onKeyDown={(e) => { if (e.key === "Enter" && type !== "textarea") e.currentTarget.blur(); }} {...rest} />
  );
}

export function Seg({ value, options, onChange }) {
  return (
    <span className="seg">
      {options.map(([v, label]) => (
        <button key={v} type="button" className={value === v ? "on" : ""} onClick={() => onChange(v)}>{label}</button>
      ))}
    </span>
  );
}

export function useStored(key, initial) {
  const [v, setV] = useState(() => {
    try { const s = localStorage.getItem(key); return s === null ? initial : JSON.parse(s); } catch { return initial; }
  });
  const set = (x) => { setV(x); try { localStorage.setItem(key, JSON.stringify(x)); } catch { /* private mode */ } };
  return [v, set];
}
