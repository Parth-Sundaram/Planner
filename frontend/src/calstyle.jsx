import React from "react";

// How each kind of calendar block looks. Saved in settings as "calendar_style"; edited in Settings.
export const CATS = [
  ["work", "Work the planner placed"],
  ["appointment", "Meetings & appointments"],
  ["class", "Classes"],
  ["routine", "Meals & routines"],
  ["busy", "Kept free / blocked"],
  ["event", "Google Calendar"],
];
export const STYLES = [["soft", "Soft fill"], ["outline", "Outline"], ["solid", "Solid"], ["striped", "Striped"], ["text", "Text only"]];
export const COLORS = ["blue", "green", "purple", "orange", "pink", "teal", "yellow", "red", "gray"];
export const DEFAULT_STYLE = {
  work: { style: "soft", color: "course" },
  appointment: { style: "outline", color: "purple" },
  class: { style: "soft", color: "gray" },
  routine: { style: "text", color: "gray" },
  busy: { style: "striped", color: "gray" },
  event: { style: "outline", color: "gray" },
};

export function parseStyle(raw) {
  let saved = {};
  try { saved = raw ? JSON.parse(raw) : {}; } catch { /* bad value: use defaults */ }
  return Object.fromEntries(CATS.map(([k]) => [k, { ...DEFAULT_STYLE[k], ...(saved[k] || {}) }]));
}

// Block category → which style entry it uses.
export function styleKey(cat) {
  if (cat === "meeting") return "appointment";
  return CATS.some(([k]) => k === cat) ? cat : "work";
}

// Small sample of a style, for the menu and Settings.
export function Sample({ s, course = "blue" }) {
  return <span className={`ev-sample st-${s.style} c-${s.color === "course" ? course : s.color}`} aria-hidden="true" />;
}