import { call } from "./common.js";
const $ = (id) => document.getElementById(id);
chrome.storage.sync.get(["url", "token"]).then((s) => { $("url").value = s.url || ""; $("token").value = s.token || ""; });
$("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({ url: $("url").value.trim(), token: $("token").value.trim() });
  try { await call("/api/courses"); $("msg").textContent = "Saved. Connected."; $("msg").className = "msg ok"; }
  catch (e) { $("msg").textContent = `Saved, but: ${e.message}`; $("msg").className = "msg err"; }
});
