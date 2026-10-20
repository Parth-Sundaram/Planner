// Tiny fetch wrapper. All times are naive local ISO strings ("2026-10-19T14:00").
async function request(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.error || res.statusText), { status: res.status });
  return data;
}

export const api = {
  get: (u) => request("GET", u),
  post: (u, b) => request("POST", u, b ?? {}),
  patch: (u, b) => request("PATCH", u, b),
  put: (u, b) => request("PUT", u, b),
  del: (u) => request("DELETE", u),
};

const pad = (n) => String(n).padStart(2, "0");
export function toLocalISO(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
export function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
export function fmtDay(iso) {
  return new Date(iso).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}
export function fmtMinutes(m) {
  const h = Math.floor(m / 60), r = m % 60;
  return h ? (r ? `${h}h ${r}m` : `${h}h`) : `${r}m`;
}
export function minutesBetween(a, b) {
  return Math.round((new Date(b) - new Date(a)) / 60000);
}
