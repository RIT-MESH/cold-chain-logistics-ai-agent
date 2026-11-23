import { useEffect, useMemo, useState } from "react";
import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { LineChart, Line, XAxis, YAxis, Tooltip, Legend, ReferenceLine, ResponsiveContainer, BarChart, Bar } from "recharts";
import { Dataset, aggregates, buildStore, loadDataset, Shipment, Store } from "./data/dataset";
import { askOffline } from "./assistant/engine";
import { toCsv, download } from "./export";
import { ErrorBoundary } from "./ErrorBoundary";
import "./styles.css";

const Label = () => <span className="synth">SYNTHETIC TEST DATA</span>;
const chart = (c: string) => c;

function useDataset() {
  const [ds, setDs] = useState<Dataset | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { loadDataset().then(setDs).catch((e) => setErr(String(e))); }, []);
  return { ds, err };
}

// ---------- theme (dark default; light persisted in localStorage) ----------
function useTheme() {
  const [dark, setDark] = useState(() => localStorage.getItem("cc-theme") !== "light");
  useEffect(() => { document.documentElement.dataset.theme = dark ? "dark" : "light"; localStorage.setItem("cc-theme", dark ? "dark" : "light"); }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

function Dashboard() {
  const { ds, err } = useDataset();
  const [monthly] = useState<{ m: string; n: number }[]>([]);
  if (err) return <div className="card error">Dataset failed to load: {err}</div>;
  if (!ds) return <p>Loading dataset…</p>;
  const a = aggregates(ds);
  return <section><h2>Dashboard <Label /></h2>
    <div className="grid g4">
      <div className="kpi"><div className="val">{a.shipments.toLocaleString()}</div><div className="lbl">Shipments</div></div>
      <div className="kpi"><div className="val">{a.telemetry.toLocaleString()}</div><div className="lbl">Telemetry readings (full dataset)</div></div>
      <div className="kpi"><div className="val">{a.alerts.toLocaleString()}</div><div className="lbl">Alerts</div></div>
      <div className="kpi"><div className="val">{a.custody.toLocaleString()}</div><div className="lbl">Custody handoffs</div></div>
    </div>
    <p>Verdicts: {a.accepted.toLocaleString()} accepted · {a.withNote.toLocaleString()} accepted with note · {a.rejected.toLocaleString()} rejected.</p>
    <p>Cargo categories: {a.cargoTypes.map(([k, v]) => `${k} (${v})`).join(" · ")}</p>
    {monthly.length > 0 && <div className="chartbox"><ResponsiveContainer width="100%" height={240}>
      <BarChart data={monthly}><XAxis dataKey="m" /><YAxis /><Tooltip /><Bar dataKey="n" name="departures" fill="#4da3ff" /></BarChart>
    </ResponsiveContainer></div>}
    <p className="note">Integrity: shipments sha256 {ds.meta.integrity.shipments_sha256} · telemetry rows sha256 {ds.meta.integrity.telemetry_rows_sha256}.</p>
  </section>;
}

type SortKey = "id" | "cargo" | "verdict" | "env";
function ShipmentExplorer() {
  const { ds, err } = useDataset();
  const [q, setQ] = useState(""); const [sel, setSel] = useState<string | null>(null);
  const [cmp, setCmp] = useState<string[]>([]);
  const [page, setPage] = useState(0); const [sort, setSort] = useState<{ k: SortKey; asc: boolean }>({ k: "id", asc: true });
  const store = useMemo(() => (ds ? buildStore(ds) : null), [ds]);
  if (err) return <div className="card error">Dataset failed to load: {err}</div>;
  if (!ds || !store) return <p>Loading dataset…</p>;
  const val = (x: Shipment, k: SortKey) => k === "id" ? x.id : k === "cargo" ? x.cargo : k === "verdict" ? x.verdict : x.minC;
  const filtered = (q ? store.ids.filter((id) => id.includes(q.toUpperCase())) : [...store.ids])
    .map((id) => store.byId.get(id)!)
    .sort((a, b) => { const r = val(a, sort.k) < val(b, sort.k) ? -1 : 1; return sort.asc ? r : -r; });
  const PAGE = 25, pageIds = filtered.slice(page * PAGE, page * PAGE + PAGE);
  const s = sel ? store.byId.get(sel) : null;
  const t = s ? store.stats(s.id) : null;
  const readings = s ? store.readings(s.id) : [];
  const chartData = readings.map((r) => ({ at: r.at.slice(5, 16), temp: r.tempC, hum: r.hum }));
  const exportShipments = () => download("shipments.csv", toCsv(
    ["shipment_id", "cargo", "vehicle", "departed", "arrived", "verdict", "min_c", "max_c", "pallets"],
    filtered.map((x) => [x.id, x.cargo, x.vehicle, x.departed, x.arrived, x.verdict, x.minC, x.maxC, x.pallets])));
  const exportTelemetry = (id: string) => download(`telemetry-${id}.csv`, toCsv(
    ["recorded_at", "temperature_c", "humidity_pct", "latitude", "longitude", "risk_level"],
    store.readings(id).map((r) => [r.at, r.tempC, r.hum, r.lat, r.lon, r.risk])));
  const toggleCmp = (id: string) => setCmp((c) => c.includes(id) ? c.filter((x) => x !== id) : c.length < 2 ? [...c, id] : [c[1], id]);
  const Th = ({ k, children }: { k: SortKey; children: string }) =>
    <th role="columnheader" aria-sort={sort.k === k ? (sort.asc ? "ascending" : "descending") : "none"}
      onClick={() => setSort((s0) => s0.k === k ? { k, asc: !s0.asc } : { k, asc: true })} style={{ cursor: "pointer" }}>
      {children}{sort.k === k ? (sort.asc ? " ▲" : " ▼") : ""}</th>;
  return <section><h2>Shipment Explorer <Label /></h2>
    <input placeholder="Search shipment ID (UC-…)" value={q} onChange={(e) => { setQ(e.target.value.slice(0, 20)); setPage(0); }} aria-label="Search shipment ID" />
    <button onClick={exportShipments}>Export CSV</button>
    <button onClick={() => download("shipments.json", JSON.stringify(filtered.map((x) => ({ id: x.id, cargo: x.cargo, verdict: x.verdict, envelope: [x.minC, x.maxC] })), null, 1), "application/json")}>Export JSON</button>
    {cmp.length === 2 && <button onClick={() => download(`comparison-${cmp[0]}-${cmp[1]}.csv`, toCsv(
      ["metric", cmp[0], cmp[1]],
      [["cargo", store.byId.get(cmp[0])?.cargo, store.byId.get(cmp[1])?.cargo],
       ["envelope_min", store.byId.get(cmp[0])?.minC, store.byId.get(cmp[1])?.minC],
       ["envelope_max", store.byId.get(cmp[0])?.maxC, store.byId.get(cmp[1])?.maxC],
       ["min_temp", store.stats(cmp[0]).min.toFixed(2), store.stats(cmp[1]).min.toFixed(2)],
       ["max_temp", store.stats(cmp[0]).max.toFixed(2), store.stats(cmp[1]).max.toFixed(2)],
       ["avg_temp", store.stats(cmp[0]).avg.toFixed(2), store.stats(cmp[1]).avg.toFixed(2)],
       ["alerts", store.alertsOf(cmp[0]).length, store.alertsOf(cmp[1]).length]]))}>Export comparison</button>}
    <p className="note">{filtered.length} shipments · page {page + 1} / {Math.max(1, Math.ceil(filtered.length / PAGE))} · click column headers to sort · checkbox selects up to 2 for comparison</p>
    {filtered.length === 0 ? <div className="card"><p>No shipments match this filter.</p></div> : (
      <table><thead><tr><th></th><Th k="id">ID</Th><Th k="cargo">Cargo</Th><Th k="verdict">Verdict</Th><Th k="env">Envelope °C</Th></tr></thead><tbody>
        {pageIds.map((x) => (
          <tr key={x.id} className="row"><td><input type="checkbox" aria-label={`compare ${x.id}`} checked={cmp.includes(x.id)} onChange={() => toggleCmp(x.id)} onClick={(e) => e.stopPropagation()} /></td>
            <td onClick={() => setSel(x.id)}><b>{x.id}</b></td><td onClick={() => setSel(x.id)}>{x.cargo}</td><td onClick={() => setSel(x.id)}>{x.verdict}</td><td onClick={() => setSel(x.id)}>{x.minC} – {x.maxC}</td></tr>))}
      </tbody></table>)}
    <div className="pager">{page > 0 && <button onClick={() => setPage(page - 1)}>← Prev</button>} {(page + 1) * PAGE < filtered.length && <button onClick={() => setPage(page + 1)}>Next →</button>}</div>
    {cmp.length === 2 && (() => { const A = store.byId.get(cmp[0])!, B = store.byId.get(cmp[1])!, ta = store.stats(A.id), tb = store.stats(B.id);
      const outA = ta.min < A.minC || ta.max > A.maxC, outB = tb.min < B.minC || tb.max > B.maxC;
      return <div className="card detail"><h3>Comparison: {A.id} vs {B.id}</h3>
        <p>{A.id}: {A.cargo}, envelope {A.minC}–{A.maxC} °C, observed {ta.min.toFixed(2)}–{ta.max.toFixed(2)} °C (avg {ta.avg.toFixed(2)}), {store.alertsOf(A.id).length} alerts, verdict {A.verdict}.</p>
        <p>{B.id}: {B.cargo}, envelope {B.minC}–{B.maxC} °C, observed {tb.min.toFixed(2)}–{tb.max.toFixed(2)} °C (avg {tb.avg.toFixed(2)}), {store.alertsOf(B.id).length} alerts, verdict {B.verdict}.</p>
        <p>{outA === outB ? "On envelope compliance, neither clearly shows more confirmed issues." : outA ? `On envelope compliance, ${A.id} shows out-of-envelope readings while ${B.id} does not.` : `On envelope compliance, ${B.id} shows out-of-envelope readings while ${A.id} does not.`}</p>
      </div>; })()}
    {s && <div className="card detail"><h3>{s.id} — {s.cargo}</h3>
      <p>Vehicle {s.vehicle} · {s.pallets} pallets · departed {s.departed} · arrived {s.arrived} · verdict <b>{s.verdict}</b>.</p>
      <p>Telemetry: {t!.n} readings, min {t!.min.toFixed(2)} °C, max {t!.max.toFixed(2)} °C, avg {t!.avg.toFixed(2)} °C (envelope {s.minC}–{s.maxC} °C).</p>
      {chartData.length > 0 && <div className="chartbox"><ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartData}>
          <XAxis dataKey="at" /><YAxis yAxisId="t" /><YAxis yAxisId="h" orientation="right" />
          <Tooltip /><Legend />
          <ReferenceLine yAxisId="t" y={s.minC} stroke="#ffb454" strokeDasharray="4 4" label="min" />
          <ReferenceLine yAxisId="t" y={s.maxC} stroke="#ffb454" strokeDasharray="4 4" label="max" />
          <Line yAxisId="t" type="monotone" dataKey="temp" name="temp °C" stroke="#4da3ff" dot={false} />
          <Line yAxisId="h" type="monotone" dataKey="hum" name="humidity %" stroke="#40e0a0" dot={false} />
        </LineChart></ResponsiveContainer></div>}
      <button onClick={() => exportTelemetry(s.id)}>Export telemetry CSV</button>
      <p>Alerts ({store.alertsOf(s.id).length}):</p>
      <ul>{store.alertsOf(s.id).slice(0, 6).map((a, i) => <li key={i}>{a.at} {a.type} ({a.severity}): {a.message}</li>)}</ul>
      <p>Custody ({store.custodyOf(s.id).length}):</p>
      <div className="tl">{store.custodyOf(s.id).map((c, i) => <div className="tl-item" key={i}><div className="t">{c.at}</div><div className="v">Handoff {c.no}: {c.by} — seal {c.sealIntact === "True" || c.sealIntact === "1" ? "intact" : "NOT intact"}</div></div>)}</div>
      <button className="outline" onClick={() => setSel(null)}>Close</button></div>}
  </section>;
}

function AIAssistant() {
  const { ds } = useDataset();
  const [q, setQ] = useState(""); const [log, setLog] = useState<{ who: "you" | "ai"; text: string }[]>([
    { who: "ai", text: "Offline AI assistant — deterministic, evidence-grounded, no live LLM. Ask about any approved synthetic shipment, e.g. \"UC-003241 report\" or \"compare UC-003241 and UC-003916\". Recorded model demonstrations are shown in Engineering Evidence; Live NVIDIA inference is a separate gated mode (currently disabled)." }]);
  const store = useMemo(() => (ds ? buildStore(ds) : null), [ds]);
  const send = () => { if (!store || !q.trim()) return; const reply = askOffline(store as never, q); setLog([...log, { who: "you", text: q }, { who: "ai", text: reply.text }]); setQ(""); };
  return <section><h2>AI Assistant — Offline Deterministic Mode <Label /></h2>
    <div className="chat" role="log">{log.map((m, i) => <div key={i} className={"msg " + (m.who === "you" ? "user" : "ai")}>{m.text}</div>)}</div>
    <div className="chat-input"><input value={q} maxLength={400} aria-label="Question" placeholder="e.g. temperature history for UC-003916"
      onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} />
      <button onClick={send} disabled={!store}>Ask</button></div>
    <p className="note">Answers are computed from the approved synthetic dataset (all {ds?.meta.counts.telemetry.toLocaleString()} telemetry rows). No network calls are made. {store ? "" : "Loading dataset…"}</p>
  </section>;
}

function IncidentCenter() {
  const { ds } = useDataset();
  const [sel, setSel] = useState<string | null>(null);
  if (!ds) return <p>Loading dataset…</p>;
  const withAlerts = Object.entries(ds.alerts).sort((a, b) => b[1].length - a[1].length);
  if (withAlerts.length === 0) return <div className="card"><p>No alert records in the dataset.</p></div>;
  const top = withAlerts.slice(0, 15);
  const alerts = sel ? (ds.alerts[sel] ?? []) : null;
  return <section><h2>Incident Center <Label /></h2>
    <p>Shipments with recorded alerts: {Object.keys(ds.alerts).length} of {ds.shipments.length}.</p>
    <table><thead><tr><th>Shipment</th><th>Alerts</th><th>Latest alert</th><th></th></tr></thead><tbody>
      {top.map(([id, list]) => (
        <tr key={id} className="row"><td><b>{id}</b></td><td>{list.length}</td><td>{list[list.length - 1][0]}</td>
          <td><button onClick={() => setSel(id === sel ? null : id)}>{sel === id ? "Hide" : "Investigate"}</button></td></tr>))}
    </tbody></table>
    {alerts && <div className="card detail"><h3>Investigation timeline — {sel}</h3>
      <div className="tl">{alerts.map((a, i) => <div className="tl-item" key={i}><div className="t">{a[0]}</div><div className="v">{a[1]} ({a[2]}): {a[3]}</div></div>)}</div>
      <button onClick={() => download(`alerts-${sel}.csv`, toCsv(["alert_at", "alert_type", "severity", "message"], alerts.map((a) => [a[0], a[1], a[2], a[3]])))}>Export alerts CSV</button>
    </div>}
  </section>;
}

function SOPKB() {
  const { ds } = useDataset();
  const [q, setQ] = useState("");
  if (!ds) return <p>Loading dataset…</p>;
  const hits = q ? ds.sops.filter((s) => (s.title + " " + s.excerpt).toLowerCase().includes(q.toLowerCase())) : ds.sops;
  return <section><h2>SOP Knowledge Base <Label /></h2>
    <input placeholder="Search SOPs…" value={q} onChange={(e) => setQ(e.target.value.slice(0, 60))} aria-label="Search SOPs" />
    {hits.length === 0 ? <div className="card"><p>No SOP documents match this search.</p></div> :
      hits.map((s) => <div key={s.file} className="card"><h3>{s.title} — section {s.section}</h3><p>{s.excerpt.slice(0, 300)}…</p>
        <p className="note">[{s.title} | section {s.section}] — SYNTHETIC TEST DOCUMENT: procedural reference only; requires human review; not an approved operational policy.</p></div>)}
  </section>;
}

function ScenarioSimulator({ store }: { store: Store | null }) {
  const [id, setId] = useState("UC-003241"); const [temp, setTemp] = useState("6.2"); const [hum, setHum] = useState("55");
  const s = store?.byId.get(id.toUpperCase()) ?? null;
  const t = parseFloat(temp), h = parseFloat(hum);
  const out = s && !Number.isNaN(t) ? (t < s.minC ? `BELOW envelope by ${(s.minC - t).toFixed(2)} °C` : t > s.maxC ? `ABOVE envelope by ${(t - s.maxC).toFixed(2)} °C` : `within envelope (${s.minC}–${s.maxC} °C)`) : "";
  const observed = store?.stats(s?.id ?? "").min ?? NaN;
  return <section><h2>Scenario Simulator <Label /></h2>
    <p>Simulate a sensor reading against any shipment's real configured envelope (educational, read-only — no records are modified).</p>
    <div className="card"><p>
      Shipment <input value={id} maxLength={9} onChange={(e) => setId(e.target.value)} aria-label="Shipment ID" />
      simulated temperature °C <input value={temp} maxLength={6} onChange={(e) => setTemp(e.target.value)} aria-label="Simulated temperature" />
      simulated humidity % <input value={hum} maxLength={6} onChange={(e) => setHum(e.target.value)} aria-label="Simulated humidity" /></p>
      {s ? <>
        <p><b>{s.id} ({s.cargo}): envelope {s.minC}–{s.maxC} °C → simulated reading {Number.isNaN(t) ? "?" : t} °C is <b>{out}</b>.</b></p>
        <p>Recorded telemetry for comparison: min {store!.stats(s.id).min.toFixed(2)} °C, max {store!.stats(s.id).max.toFixed(2)} °C, avg {store!.stats(s.id).avg.toFixed(2)} °C — the simulated reading {Number.isNaN(t) ? "?" : t < (store!.stats(s.id).min) ? "is below every recorded reading" : t > store!.stats(s.id).max ? "exceeds every recorded reading" : "falls within the recorded range"}.</p>
        <p>Humidity {Number.isNaN(h) ? "?" : h + "%"} — the dataset's humidity records for this shipment {store!.readings(s.id).some((r) => r.hum !== null) ? `span ${Math.min(...store!.readings(s.id).filter((r) => r.hum !== null).map((r) => r.hum!)).toFixed(1)}–${Math.max(...store!.readings(s.id).filter((r) => r.hum !== null).map((r) => r.hum!)).toFixed(1)} %` : "are not available"}. Recorded alerts for this shipment: {store!.alertsOf(s.id).length}.</p>
      </> : <p>Unknown shipment ID — enter a valid UC-xxxxxx from the dataset.</p>}
    </div>
  </section>;
}

const Architecture = () => <section><h2>System Architecture</h2>
  <div className="card"><p><b>Offline AI (this app — always available):</b> static React build + full synthetic dataset bundle (all 64,120 telemetry rows, manifest-verified) + deterministic assistant. No backend, no network calls.</p></div>
  <div className="card"><p><b>Recorded AI:</b> verified model demonstrations and evidence are shown in Engineering Evidence — clearly labeled recordings, not live inference.</p></div>
  <div className="card"><p><b>Live AI (planned, gated — currently disabled):</b> Google OAuth (Supabase, openid+email only) → server-side quota enforcement (10/user/day, 5/hr, 2/min, 1 user-concurrent, 2 global, 100/day) → NeMo Guardrails input/output rails (Python runtime — local prototype only) → approved read-only synthetic-data tools + existing Pinecone SOP index (coldchain-sop-index-nim, namespace sops, unchanged) → NVIDIA NIM (secret-bound, allowlisted model). Enabled only after a deployed, verified guardrail runtime + explicit approval.</p></div>
  <div className="card"><p><b>Trust boundaries:</b> authentication and gateway authorization are independent layers; CORS is not access control; all gateway routes require verified identity; retrieved SOP content is untrusted data; fail-closed on guardrail outage.</p></div>
</section>;

const EngineeringEvidence = () => <section><h2>Engineering Evidence</h2>
  <div className="card"><h3>Recorded AI demonstrations (not live)</h3><p>Verified model outputs are preserved in the project repository evidence documents (qualification battery, post-repair validation, acceptance battery 8/8 + isolation 6/6). These are <b>recorded results</b>, reproduced here as descriptions — no live model is called on this page.</p></div>
  <div className="card"><p><b>Verified:</b> 382-test offline regression suite (SQL guard, intent-based validators, context resolution, evidence fallback, dashboard guardrails); model qualification 14/15 complete-correct; dataset published with manifest integrity hashes; Stage-3 browser suite: 8 views, keyboard navigation, injection safety, offline operation.</p></div>
  <div className="card"><p><b>Security architecture:</b> hardened SQL guard (allowlisted views, read-only identities, TLS); NeMo Guardrails design (input/dialog/retrieval/tool/output rails) — local prototype status, not deployed; deterministic evidence validation; fail-closed inference.</p></div>
  <div className="card"><p><b>Known limitations:</b> live inference disabled; guardrails not deployed; historical GPS data intentionally region-mixed (no place labels); risk classifications have no documented derivation.</p></div>
</section>;

const Nav = () => (
  <nav className="tabs" aria-label="Main">
    {[["/", "Dashboard"], ["/shipments", "Shipment Explorer"], ["/assistant", "AI Assistant"], ["/incidents", "Incident Center"], ["/sops", "SOP Knowledge Base"], ["/scenario", "Scenario Simulator"], ["/architecture", "Architecture"], ["/evidence", "Engineering Evidence"]]
      .map(([to, label]) => <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => "tab" + (isActive ? " on" : "")}>{label}</NavLink>)}
  </nav>);

function Shell() {
  const { ds } = useDataset();
  const theme = useTheme();
  const store = useMemo(() => (ds ? buildStore(ds) : null), [ds]);
  return <div className="wrap">
    <header className="hero"><h1>Cold-Chain Logistics <span>AI Agent</span></h1>
      <p className="sub">Evidence-grounded shipment intelligence over an approved synthetic dataset (2021–2026). <Label /></p>
      <button className="theme" onClick={theme.toggle} aria-label="Toggle dark or light mode">{theme.dark ? "☀ Light mode" : "☾ Dark mode"}</button></header>
    <Nav />
    <main><ErrorBoundary><Routes>
      <Route path="/" element={<Dashboard />} /><Route path="/shipments" element={<ShipmentExplorer />} />
      <Route path="/assistant" element={<AIAssistant />} /><Route path="/incidents" element={<IncidentCenter />} />
      <Route path="/sops" element={<SOPKB />} /><Route path="/scenario" element={<ScenarioSimulator store={store} />} />
      <Route path="/architecture" element={<Architecture />} /><Route path="/evidence" element={<EngineeringEvidence />} />
    </Routes></ErrorBoundary></main>
    <footer>Offline portfolio — fully functional without any backend, AWS, Pinecone or NVIDIA. Live AI is a separate gated mode.</footer>
  </div>;
}

export default function App() { return <HashRouter><Shell /></HashRouter>; }
