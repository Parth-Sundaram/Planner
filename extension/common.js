export async function settings() {
  const s = await chrome.storage.sync.get(["url", "token"]);
  return { url: (s.url || "").replace(/\/$/, ""), token: s.token || "" };
}
export async function call(path, opts = {}) {
  const { url, token } = await settings();
  if (!url || !token) throw new Error("Set your planner URL and key in the extension's options first.");
  const r = await fetch(url + path, { ...opts, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(opts.headers || {}) } });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Planner said ${r.status}`);
  return data;
}
