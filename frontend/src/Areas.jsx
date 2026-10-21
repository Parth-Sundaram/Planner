import React, { useEffect, useRef, useState } from "react";
import { api, fmtDay, fmtDue, localInput } from "./api.js";
import { Field, Icon } from "./ui.jsx";

function useSummary(area, ctx) {
  const [s, setS] = useState(null);
  const load = () => api.get(`/api/areas/${area.id}/summary`).then(setS).catch((e) => ctx.notify(e.message, "error"));
  useEffect(() => { load(); }, [area.id, ctx.version]);
  return [s, load];
}

function useEntryActions(area, ctx, reload) {
  return {
    add: async (type, data, date, msg = "Saved") => {
      try { await api.post(`/api/areas/${area.id}/entries`, { type, data, date }); ctx.notify(msg); reload(); ctx.refresh(); return true; }
      catch (e) { ctx.notify(e.message, "error"); return false; }
    },
    patch: async (id, body, msg) => {
      try { await api.patch(`/api/entries/${id}`, body); if (msg) ctx.notify(msg); reload(); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
    },
    del: async (id, msg = "Removed") => {
      try { await api.del(`/api/entries/${id}`); ctx.notify(msg); reload(); ctx.refresh(); } catch (e) { ctx.notify(e.message, "error"); }
    },
  };
}

const nowLocal = () => {
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};
const money = (n) => (n == null ? "" : `$${Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

/* ---------------- Career: application tracker ---------------- */
export function CareerModule({ area, ctx }) {
  const [s, reload] = useSummary(area, ctx);
  const act = useEntryActions(area, ctx, reload);
  const [f, setF] = useState({ company: "", role: "", link: "", stage: "applied", next_step: "", next_date: "" });
  const [show, setShow] = useState("active");
  if (!s) return <div className="muted">Loading…</div>;
  const active = (j) => !["rejected", "withdrawn"].includes(j.data.stage);
  const jobs = s.jobs.filter((j) => (show === "active" ? active(j) : !active(j)));

  async function add(e) {
    e.preventDefault();
    if (!f.company) return;
    const { next_date, ...data } = f;
    if (await act.add("job", data, next_date || null, data.next_step && next_date ? "Added. The next step is on your plan" : "Added")) {
      setF({ company: "", role: "", link: "", stage: "applied", next_step: "", next_date: "" });
    }
  }
  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="stats">
        {s.stages.filter((st) => s.counts[st]).map((st) => (
          <div key={st} className="stat"><b>{s.counts[st]}</b><span>{st}</span></div>
        ))}
        {Object.keys(s.counts).length === 0 && <div className="muted">No applications yet. Add the first one below.</div>}
      </div>
      <form className="box pad stack" style={{ gap: 8 }} onSubmit={add}>
        <b>Add an application</b>
        <div className="row">
          <input value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} placeholder="Company" required />
          <input value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} placeholder="Role" />
          <select value={f.stage} onChange={(e) => setF({ ...f, stage: e.target.value })}>{s.stages.map((x) => <option key={x}>{x}</option>)}</select>
          <input value={f.link} onChange={(e) => setF({ ...f, link: e.target.value })} placeholder="Posting link" className="grow" />
        </div>
        <div className="row">
          <input value={f.next_step} onChange={(e) => setF({ ...f, next_step: e.target.value })} placeholder="Next step, e.g. finish OA" className="grow" />
          <input type="datetime-local" value={f.next_date} onChange={(e) => setF({ ...f, next_date: e.target.value })} aria-label="Next step due" />
          <button className="btn primary">Add</button>
        </div>
        <div className="hint">A next step with a date becomes a task, so the planner makes time for it.</div>
      </form>
      <div className="row between">
        <div className="seg">
          <button className={show === "active" ? "on" : ""} onClick={() => setShow("active")}>Active</button>
          <button className={show === "closed" ? "on" : ""} onClick={() => setShow("closed")}>Closed</button>
        </div>
      </div>
      <div className="table-wrap">
        <table className="grid">
          <thead><tr><th>Company</th><th>Role</th><th style={{ width: 150 }}>Stage</th><th>Next step</th><th style={{ width: 190 }}>By</th><th style={{ width: 30 }} /></tr></thead>
          <tbody>
            {jobs.length === 0 && <tr><td colSpan={6} className="faint">Nothing here.</td></tr>}
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>{j.data.link ? <a href={j.data.link} target="_blank" rel="noreferrer">{j.data.company}</a> : j.data.company}</td>
                <td><Field value={j.data.role || ""} placeholder="—" onSave={(v) => act.patch(j.id, { data: { role: v } })} /></td>
                <td><select value={j.data.stage || "wishlist"} onChange={(e) => act.patch(j.id, { data: { stage: e.target.value } }, "Updated")}>{s.stages.map((x) => <option key={x}>{x}</option>)}</select></td>
                <td><Field value={j.data.next_step || ""} placeholder="—" onSave={(v) => act.patch(j.id, { data: { next_step: v } }, "Updated")} /></td>
                <td><Field type="datetime-local" value={localInput(j.date)} onSave={(v) => act.patch(j.id, { date: v || null }, "Updated")} /></td>
                <td><button className="icon-btn" onClick={() => confirm(`Remove ${j.data.company}?`) && act.del(j.id)} aria-label="Remove"><Icon name="x" size={12} /></button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ---------------- Fitness: meals (with photo estimates), workouts, sleep/steps ---------------- */
export function FitnessModule({ area, ctx }) {
  const [s, reload] = useSummary(area, ctx);
  const act = useEntryActions(area, ctx, reload);
  const [meal, setMeal] = useState({ name: "", calories: "", protein: "", carbs: "", fat: "" });
  const [est, setEst] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [wk, setWk] = useState({ kind: "", minutes: "" });
  const [met, setMet] = useState({ sleep_hours: "", steps: "", weight: "" });
  const file = useRef(null);
  if (!s) return <div className="muted">Loading…</div>;

  async function photo(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true); setEst(null);
    const fd = new FormData();
    fd.append("photo", f); fd.append("note", note);
    try {
      const res = await fetch("/api/fitness/estimate-meal", { method: "POST", body: fd, credentials: "same-origin" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Couldn't read the photo");
      setEst(data);
      setMeal({ name: data.name, calories: data.calories, protein: data.protein, carbs: data.carbs, fat: data.fat });
    } catch (err) { ctx.notify(err.message, "error"); }
    setBusy(false);
    e.target.value = "";
  }
  async function saveMeal(e) {
    e.preventDefault();
    if (!meal.calories) return;
    if (await act.add("meal", meal, nowLocal(), "Meal logged")) { setMeal({ name: "", calories: "", protein: "", carbs: "", fat: "" }); setEst(null); setNote(""); }
  }
  const pct = (v, t) => Math.min(100, Math.round((100 * v) / Math.max(1, t)));
  const maxCal = Math.max(s.calorie_target * 1.25, ...s.days.map((d) => d.calories));

  return (
    <div className="stack" style={{ gap: 22 }}>
      <div className="grade-cards">
        <div className="grade-card"><div className="lbl">Calories today</div><div className="big">{Math.round(s.today.calories)}</div>
          <div className="meter" style={{ width: "100%", margin: "6px 0" }}><i style={{ width: `${pct(s.today.calories, s.calorie_target)}%` }} /></div>
          <div className="lbl">of {s.calorie_target} · {Math.max(0, Math.round(s.calorie_target - s.today.calories))} left</div></div>
        <div className="grade-card"><div className="lbl">Protein today</div><div className="big">{Math.round(s.today.protein)}g</div>
          <div className="meter" style={{ width: "100%", margin: "6px 0" }}><i style={{ width: `${pct(s.today.protein, s.protein_target)}%` }} /></div>
          <div className="lbl">of {s.protein_target}g</div></div>
        <div className="grade-card"><div className="lbl">Last night's sleep</div>
          <div className="big">{(() => { const m = [...s.days].reverse().find((d) => d.sleep_minutes)?.sleep_minutes; return m ? `${Math.floor(m / 60)}h ${m % 60}m` : "—"; })()}</div>
          <div className="lbl">from your phone, or log it below</div></div>
      </div>

      <form className="box pad stack" style={{ gap: 8 }} onSubmit={saveMeal}>
        <div className="row between"><b>Log a meal</b>
          <span className="row">
            <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="optional hint, e.g. 2 scoops rice" style={{ width: 220 }} />
            <input ref={file} type="file" accept="image/*" capture="environment" hidden onChange={photo} />
            <button type="button" className="btn" onClick={() => file.current?.click()} disabled={busy}><Icon name="camera" />{busy ? "Reading photo…" : "From a photo"}</button>
          </span>
        </div>
        {est && (
          <div className="preview">
            <div className="small muted">Estimate ({est.confidence} confidence). Check the numbers, then save.</div>
            {est.items.map((it, i) => <div key={i} className="previewrow"><span>{it.food}{it.portion ? ` · ${it.portion}` : ""}</span><span className="muted">{it.calories} kcal</span></div>)}
          </div>
        )}
        <div className="row">
          <input value={meal.name} onChange={(e) => setMeal({ ...meal, name: e.target.value })} placeholder="What" className="grow" />
          {["calories", "protein", "carbs", "fat"].map((k) => (
            <input key={k} type="number" value={meal[k]} onChange={(e) => setMeal({ ...meal, [k]: e.target.value })} placeholder={k === "calories" ? "kcal" : `${k} g`} style={{ width: 86 }} />
          ))}
          <button className="btn primary">Save</button>
        </div>
      </form>

      <div>
        <div className="section-title">Today's meals</div>
        <div className="mini-list">
          {s.today.meals.length === 0 && <div className="faint small">Nothing logged yet today.</div>}
          {s.today.meals.map((m) => (
            <div key={m.id} className="mini">
              <span className="grow ellipsis">{m.data.name || "Meal"}</span>
              <span className="when">{m.data.calories} kcal · {m.data.protein ?? 0}g protein</span>
              <button className="icon-btn" onClick={() => act.del(m.id)} aria-label="Remove"><Icon name="x" size={12} /></button>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="section-title">Last 14 days</div>
        <div className="box pad">
          <svg viewBox="0 0 700 170" width="100%" role="img" aria-label="Calories per day, last 14 days">
            {(() => {
              const y = (v) => 140 - (130 * v) / maxCal;
              const bw = 700 / s.days.length;
              return (
                <g>
                  <line x1="0" x2="700" y1={y(s.calorie_target)} y2={y(s.calorie_target)} stroke="var(--text-3)" strokeDasharray="4 4" />
                  <text x="2" y={y(s.calorie_target) - 5} fontSize="10" fill="var(--text-2)" style={{ paintOrder: "stroke", stroke: "var(--surface)", strokeWidth: 3 }}>target {s.calorie_target}</text>
                  {s.days.map((d, i) => (
                    <g key={d.date}>
                      <rect x={i * bw + 6} width={bw - 12} y={y(d.calories)} height={140 - y(d.calories)} rx="3"
                            fill={d.calories > s.calorie_target * 1.05 ? "var(--warn)" : "var(--c-green)"} opacity={d.calories ? 0.85 : 0.15}>
                        <title>{`${d.date}: ${d.calories} kcal${d.sleep_minutes ? `, slept ${Math.round(d.sleep_minutes / 6) / 10}h` : ""}${d.steps ? `, ${d.steps} steps` : ""}`}</title>
                      </rect>
                      <text x={i * bw + bw / 2} y="158" textAnchor="middle" fontSize="10" fill="var(--text-3)">{new Date(d.date + "T12:00").toLocaleDateString([], { weekday: "narrow" })}</text>
                      {d.sleep_minutes && <circle cx={i * bw + bw / 2} cy={y(maxCal * Math.min(1, d.sleep_minutes / 600))} r="3" fill="var(--accent)"><title>{`${Math.round(d.sleep_minutes / 6) / 10}h sleep`}</title></circle>}
                    </g>
                  ))}
                </g>
              );
            })()}
          </svg>
          <div className="hint">Bars: calories (orange when over target). Blue dots: hours slept, where 10h is the top.</div>
        </div>
      </div>

      <div className="today-grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
        <form className="box pad stack" style={{ gap: 8 }} onSubmit={async (e) => { e.preventDefault(); if (wk.kind && wk.minutes && await act.add("workout", wk, nowLocal(), "Workout logged")) setWk({ kind: "", minutes: "" }); }}>
          <b>Log a workout</b>
          <div className="row">
            <input value={wk.kind} onChange={(e) => setWk({ ...wk, kind: e.target.value })} placeholder="Push A, Z2 bike, walk/jog…" className="grow" />
            <input type="number" value={wk.minutes} onChange={(e) => setWk({ ...wk, minutes: e.target.value })} placeholder="min" style={{ width: 70 }} />
            <button className="btn">Save</button>
          </div>
          <div className="mini-list">
            {s.workouts.slice(0, 5).map((w) => <div key={w.id} className="mini"><span className="grow">{w.data.kind}</span><span className="when">{w.data.minutes} min · {fmtDay(w.date)}</span></div>)}
          </div>
        </form>
        <form className="box pad stack" style={{ gap: 8 }} onSubmit={async (e) => {
          e.preventDefault();
          const data = { ...(met.sleep_hours ? { sleep_minutes: Math.round(Number(met.sleep_hours) * 60) } : {}), ...(met.steps ? { steps: met.steps } : {}), ...(met.weight ? { weight: met.weight } : {}) };
          if (Object.keys(data).length && await act.add("metric", data, nowLocal(), "Saved")) setMet({ sleep_hours: "", steps: "", weight: "" });
        }}>
          <b>Sleep, steps, weight (today)</b>
          <div className="row">
            <input type="number" step="0.25" value={met.sleep_hours} onChange={(e) => setMet({ ...met, sleep_hours: e.target.value })} placeholder="sleep h" style={{ width: 84 }} />
            <input type="number" value={met.steps} onChange={(e) => setMet({ ...met, steps: e.target.value })} placeholder="steps" style={{ width: 90 }} />
            <input type="number" step="0.1" value={met.weight} onChange={(e) => setMet({ ...met, weight: e.target.value })} placeholder="weight" style={{ width: 84 }} />
            <button className="btn">Save</button>
          </div>
          <div className="hint">Or let your phone send these automatically. See Settings → Phone & extension.</div>
        </form>
      </div>
    </div>
  );
}

/* ---------------- Finance: bills, budget, expenses ---------------- */
export function FinanceModule({ area, ctx }) {
  const [s, reload] = useSummary(area, ctx);
  const act = useEntryActions(area, ctx, reload);
  const [bill, setBill] = useState({ name: "", amount: "", frequency: "monthly", autopay: false, date: "" });
  const [exp, setExp] = useState({ amount: "", category: "", note: "" });
  const [bud, setBud] = useState({ category: "", limit: "" });
  if (!s) return <div className="muted">Loading…</div>;
  const cats = Array.from(new Set([...s.budgets.map((b) => b.data.category), ...Object.keys(s.spent)]));

  return (
    <div className="stack" style={{ gap: 22 }}>
      <div className="grade-cards">
        <div className="grade-card"><div className="lbl">Spent this month</div><div className="big">{money(s.month_total)}</div>
          <div className="lbl">{s.budget_total ? `of ${money(s.budget_total)} budgeted` : "set budgets below"}</div></div>
        <div className="grade-card"><div className="lbl">Next bill</div>
          <div className="big" style={{ fontSize: 20 }}>{s.bills[0] ? s.bills[0].data.name : "—"}</div>
          <div className="lbl">{s.bills[0] ? `${money(s.bills[0].data.amount)} · ${s.bills[0].date ? fmtDue(s.bills[0].date) : "no date"}${s.bills[0].data.autopay ? " · autopay" : ""}` : "add your bills below"}</div></div>
        <div className="grade-card"><div className="lbl">Bills tracked</div><div className="big">{s.bills.length}</div>
          <div className="lbl">non-autopay ones become "Pay …" tasks</div></div>
      </div>

      <div>
        <div className="section-title">Bills</div>
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>Bill</th><th className="num" style={{ width: 110 }}>Amount</th><th style={{ width: 120 }}>Repeats</th><th style={{ width: 200 }}>Next due</th><th style={{ width: 80 }}>Autopay</th><th style={{ width: 30 }} /></tr></thead>
            <tbody>
              {s.bills.map((b) => (
                <tr key={b.id}>
                  <td><Field value={b.data.name} onSave={(v) => act.patch(b.id, { data: { name: v } })} /></td>
                  <td><Field type="number" className="num" value={b.data.amount ?? ""} onSave={(v) => act.patch(b.id, { data: { amount: v } })} /></td>
                  <td><select value={b.data.frequency || "monthly"} onChange={(e) => act.patch(b.id, { data: { frequency: e.target.value } })}>
                    {["weekly", "biweekly", "monthly", "yearly", "once"].map((x) => <option key={x}>{x}</option>)}</select></td>
                  <td><Field type="datetime-local" value={localInput(b.date)} onSave={(v) => act.patch(b.id, { date: v || null })} /></td>
                  <td><input type="checkbox" checked={!!b.data.autopay} onChange={(e) => act.patch(b.id, { data: { autopay: e.target.checked } }, e.target.checked ? "Autopay: no reminder task" : "You'll get a Pay task")} /></td>
                  <td><button className="icon-btn" onClick={() => act.del(b.id)} aria-label="Remove"><Icon name="x" size={12} /></button></td>
                </tr>
              ))}
              <tr>
                <td><input value={bill.name} onChange={(e) => setBill({ ...bill, name: e.target.value })} placeholder="+ Rent, phone, Spotify…" /></td>
                <td><input type="number" className="num" value={bill.amount} onChange={(e) => setBill({ ...bill, amount: e.target.value })} placeholder="0.00" /></td>
                <td><select value={bill.frequency} onChange={(e) => setBill({ ...bill, frequency: e.target.value })}>{["weekly", "biweekly", "monthly", "yearly", "once"].map((x) => <option key={x}>{x}</option>)}</select></td>
                <td><input type="datetime-local" value={bill.date} onChange={(e) => setBill({ ...bill, date: e.target.value })} /></td>
                <td><input type="checkbox" checked={bill.autopay} onChange={(e) => setBill({ ...bill, autopay: e.target.checked })} /></td>
                <td><button className="btn sm" disabled={!bill.name} onClick={async () => {
                  const { date, ...data } = bill;
                  if (await act.add("bill", data, date || null, "Bill added")) setBill({ name: "", amount: "", frequency: "monthly", autopay: false, date: "" });
                }}>Add</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="today-grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)" }}>
        <div>
          <div className="section-title">Budget this month</div>
          <div className="stack" style={{ gap: 8 }}>
            {cats.length === 0 && <div className="faint small">Add a category with a monthly limit.</div>}
            {cats.map((cat) => {
              const b = s.budgets.find((x) => x.data.category === cat);
              const spent = s.spent[cat] || 0, limit = b?.data.limit || 0;
              return (
                <div key={cat}>
                  <div className="row between small"><span>{cat}</span><span className={limit && spent > limit ? "err" : "muted"}>{money(spent)}{limit ? ` / ${money(limit)}` : ""}</span></div>
                  <div className="meter" style={{ width: "100%" }}><i style={{ width: `${limit ? Math.min(100, (100 * spent) / limit) : 0}%`, background: limit && spent > limit ? "var(--danger)" : undefined }} /></div>
                </div>
              );
            })}
            <form className="row" onSubmit={async (e) => { e.preventDefault(); if (bud.category && bud.limit && await act.add("budget", bud, null, "Budget set")) setBud({ category: "", limit: "" }); }}>
              <input value={bud.category} onChange={(e) => setBud({ ...bud, category: e.target.value })} placeholder="Category" />
              <input type="number" value={bud.limit} onChange={(e) => setBud({ ...bud, limit: e.target.value })} placeholder="Monthly $" style={{ width: 100 }} />
              <button className="btn sm">Add</button>
            </form>
          </div>
        </div>
        <div>
          <div className="section-title">Spending</div>
          <form className="row" onSubmit={async (e) => { e.preventDefault(); if (exp.amount && await act.add("expense", exp, nowLocal(), "Logged")) setExp({ amount: "", category: exp.category, note: "" }); }}>
            <input type="number" step="0.01" value={exp.amount} onChange={(e) => setExp({ ...exp, amount: e.target.value })} placeholder="$" style={{ width: 80 }} />
            <input list="fin-cats" value={exp.category} onChange={(e) => setExp({ ...exp, category: e.target.value })} placeholder="Category" style={{ width: 120 }} />
            <datalist id="fin-cats">{cats.map((c) => <option key={c} value={c} />)}</datalist>
            <input value={exp.note} onChange={(e) => setExp({ ...exp, note: e.target.value })} placeholder="What" className="grow" />
            <button className="btn sm">Log</button>
          </form>
          <div className="mini-list" style={{ marginTop: 8 }}>
            {s.expenses.slice(0, 10).map((x) => (
              <div key={x.id} className="mini"><span className="grow ellipsis">{x.data.note || x.data.category || "Expense"}</span>
                <span className="when">{money(x.data.amount)} · {x.data.category || "Other"} · {fmtDay(x.date)}</span>
                <button className="icon-btn" onClick={() => act.del(x.id)} aria-label="Remove"><Icon name="x" size={12} /></button></div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
