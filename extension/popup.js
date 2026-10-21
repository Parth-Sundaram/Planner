import { call } from "./common.js";

const $ = (id) => document.getElementById(id);

function suggestTitle(p) {
  // GitHub issue/PR pages: "Fix login bug · Issue #12 · owner/repo" -> "owner/repo #12: Fix login bug"
  const gh = p.title.match(/^(.*?) · (?:Issue|Pull Request) #(\d+) · (.+)$/);
  if (gh) return `${gh[3]} #${gh[2]}: ${gh[1]}`;
  if (p.text && p.text.length <= 120) return p.text;
  return p.title.replace(/\s*[|·–-]\s*[^|·–-]+$/, "").slice(0, 120);
}

(async () => {
  let pending = (await chrome.storage.session.get("pending")).pending;
  await chrome.storage.session.remove("pending");
  if (!pending) {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    let sel = "";
    try {
      const [{ result }] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => getSelection().toString() });
      sel = (result || "").trim();
    } catch { /* chrome:// pages can't be read */ }
    pending = { text: sel, link: tab?.url || "", title: tab?.title || "" };
  }
  $("text").value = suggestTitle(pending);
  $("notes").value = [pending.link, pending.text && pending.text.length > 120 ? pending.text.slice(0, 1500) : ""].filter(Boolean).join("\n\n");
  try {
    const d = await call("/api/courses");
    for (const c of d.courses) $("area").append(new Option(c.name, c.id));
  } catch (e) { $("msg").textContent = e.message; $("msg").className = "msg err"; }
})();

$("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("go").disabled = true;
  try {
    const body = { text: $("text").value, notes: $("notes").value };
    if ($("area").value) body.course_id = Number($("area").value);
    const r = await call("/api/tasks", { method: "POST", body: JSON.stringify(body) });
    const due = new Date(r.task.due).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    $("msg").textContent = `Added "${r.task.title}", due ${due}. It's scheduled.`;
    $("msg").className = "msg ok";
    setTimeout(() => window.close(), 1600);
  } catch (err) {
    $("msg").textContent = err.message; $("msg").className = "msg err"; $("go").disabled = false;
  }
});
