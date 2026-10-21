import React, { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./ui.jsx";

const EXAMPLES = ["I'm sick today, push everything to tomorrow", "Skip gym tomorrow and give CNN Part 3 more time",
  "I have a dentist appointment Thursday 2-3pm", "What should I do right now?"];

export default function Assistant({ ctx }) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState([]);
  const [notes, setNotes] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState({});   // message id -> Set of action ids to apply
  const [showNotes, setShowNotes] = useState(false);
  const end = useRef(null);

  const load = () => api.get("/api/assistant").then((d) => { setMsgs(d.messages); setNotes(d.notes); }).catch(() => {});
  useEffect(() => { if (open) load(); }, [open]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy, open]);
  useEffect(() => {
    const onKey = (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function send(e, preset) {
    e?.preventDefault();
    const m = (preset ?? text).trim();
    if (!m || busy) return;
    setText(""); setBusy(true);
    setMsgs((x) => [...x, { id: `tmp${Date.now()}`, role: "user", text: m, actions: [] }]);
    try {
      await api.post("/api/assistant", { message: m });
      await load();
    } catch (err) {
      setMsgs((x) => [...x, { id: `err${Date.now()}`, role: "model", text: err.message, actions: [], error: true }]);
    }
    setBusy(false);
  }
  async function apply(msg) {
    const chosen = msg.actions.filter((a) => !a.applied && (picked[msg.id] ? picked[msg.id].has(a.id) : true));
    if (!chosen.length) return;
    try {
      const r = await api.post("/api/assistant/apply", { actions: chosen });
      const bad = r.results.filter((x) => !x.ok);
      ctx.notify(bad.length ? `Applied ${r.results.length - bad.length}; ${bad.length} failed: ${bad[0].error}` : "Done. Replanned", bad.length ? "error" : "ok");
      setMsgs((x) => x.map((mm) => mm.id === msg.id ? { ...mm, actions: mm.actions.map((a) => chosen.some((c) => c.id === a.id) ? { ...a, applied: true } : a) } : mm));
      ctx.refresh();
    } catch (err) { ctx.notify(err.message, "error"); }
  }
  const toggle = (msg, a) => setPicked((p) => {
    const cur = new Set(p[msg.id] ?? msg.actions.filter((x) => !x.applied).map((x) => x.id));
    cur.has(a.id) ? cur.delete(a.id) : cur.add(a.id);
    return { ...p, [msg.id]: cur };
  });

  if (!open) {
    return (
      <button className="assist-fab" onClick={() => setOpen(true)} title="Ask the planner (Ctrl/⌘ K)">
        <Icon name="spark" /> Ask
      </button>
    );
  }
  return (
    <aside className="assist" role="dialog" aria-label="Assistant">
      <div className="panel-head">
        <b className="grow row"><Icon name="spark" />Assistant</b>
        <button className="btn quiet sm" onClick={() => setShowNotes(!showNotes)}>{showNotes ? "Chat" : `What it knows (${notes.length})`}</button>
        {!showNotes && msgs.length > 0 && <button className="btn quiet sm" onClick={async () => { await api.del("/api/assistant"); load(); }}>Clear</button>}
        <button className="icon-btn" onClick={() => setOpen(false)} aria-label="Close"><Icon name="x" /></button>
      </div>
      {showNotes ? (
        <div className="assist-body">
          <p className="muted small">Things it has saved about how you work. It reads these on every request. Remove anything that's wrong.</p>
          {notes.length === 0 && <div className="faint small">Nothing yet. Tell it things like "PLC always takes me longer than I estimate".</div>}
          {notes.map((n) => (
            <div key={n.id} className="mini"><span className="grow">{n.text}</span>
              <button className="icon-btn" onClick={async () => { await api.del(`/api/assistant/notes/${n.id}`); load(); }} aria-label="Forget"><Icon name="x" size={12} /></button></div>
          ))}
        </div>
      ) : (
        <div className="assist-body">
          {msgs.length === 0 && (
            <div className="stack" style={{ gap: 6 }}>
              <div className="muted small">It sees your plan for the next few days and every open deadline. Nothing changes until you approve it.</div>
              {EXAMPLES.map((x) => <button key={x} className="btn quiet sm" style={{ justifyContent: "flex-start", whiteSpace: "normal", textAlign: "left" }} onClick={() => send(null, x)}>{x}</button>)}
            </div>
          )}
          {msgs.map((m) => (
            <div key={m.id} className={`bubble ${m.role} ${m.error ? "error" : ""}`}>
              {m.text && <div>{m.text}</div>}
              {m.actions?.length > 0 && (
                <div className="proposals">
                  {m.actions.map((a) => {
                    const sel = picked[m.id] ? picked[m.id].has(a.id) : true;
                    return (
                      <label key={a.id} className={`proposal ${a.applied ? "applied" : ""}`}>
                        {a.applied ? <Icon name="check" size={14} /> : <input type="checkbox" checked={sel} onChange={() => toggle(m, a)} />}
                        <span>{a.summary}</span>
                      </label>
                    );
                  })}
                  {m.actions.some((a) => !a.applied) && <button className="btn primary sm" onClick={() => apply(m)}>Apply</button>}
                </div>
              )}
            </div>
          ))}
          {busy && <div className="bubble model faint">Thinking…</div>}
          <div ref={end} />
        </div>
      )}
      {!showNotes && (
        <form className="assist-input" onSubmit={send}>
          <input autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask or tell it what changed…" />
          <button className="icon-btn" disabled={busy || !text.trim()} aria-label="Send"><Icon name="send" /></button>
        </form>
      )}
    </aside>
  );
}
