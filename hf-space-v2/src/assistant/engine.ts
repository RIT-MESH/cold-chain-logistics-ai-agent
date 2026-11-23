// Deterministic offline assistant (directive Stage 4). Every numeric answer
// is computed from the loaded Store. No network, no LLM, no code execution.
// Input limits + strict ID validation; the UI renders replies as plain text
// (React escapes by construction — no innerHTML anywhere).
import { Store, validateShipmentId } from "../data/dataset";

export const MAX_INPUT_CHARS = 400;

export type Reply = { kind: "answer" | "unsupported" | "unknown-id" | "error"; text: string };

const f = (n: number) => (Number.isNaN(n) ? "n/a" : n.toFixed(2).replace(/\.?0+$/, ""));

function shipmentReport(st: Store, id: string): Reply {
  const s = st.byId.get(id);
  if (!s) return { kind: "unknown-id", text: `No shipment ${id} exists in the approved synthetic dataset.` };
  const t = st.stats(id);
  const alerts = st.alertsOf(id);
  const cust = st.custodyOf(id);
  const lines = [
    `Shipment ${s.id} (SYNTHETIC TEST DATA)`,
    `Cargo: ${s.cargo}; vehicle ${s.vehicle}; ${s.pallets} pallets.`,
    `Departed ${s.departed}; arrived ${s.arrived}; receiver verdict: ${s.verdict}.`,
    `Configured temperature envelope: ${f(s.minC)} to ${f(s.maxC)} °C.`,
    `Telemetry: ${t.n} readings; observed min ${f(t.min)} °C, max ${f(t.max)} °C, average ${f(t.avg)} °C.`,
    t.n && t.min >= s.minC && t.max <= s.maxC
      ? `All observed readings stayed within the stated envelope (separate from recorded risk classifications).`
      : `At least one reading falls outside the stated envelope — see the incident/alert records.`,
    `Alerts: ${alerts.length ? alerts.length + " recorded" : "none recorded"}. Custody handoffs: ${cust.length}${cust.length ? ", seals: " + (cust.every((c) => c.sealIntact === "True" || c.sealIntact === "true" || c.sealIntact === "1") ? "all intact" : "AT LEAST ONE NOT INTACT") : ""}.`,
  ];
  return { kind: "answer", text: lines.join("\n") };
}

function tempHistory(st: Store, id: string, metric: "temp" | "hum"): Reply {
  const s = st.byId.get(id);
  if (!s) return { kind: "unknown-id", text: `No shipment ${id} exists in the approved synthetic dataset.` };
  const rs = st.readings(id);
  if (!rs.length) return { kind: "error", text: `No telemetry readings are available for ${id}.` };
  const head = metric === "temp"
    ? `Temperature history for ${id} (envelope ${f(s.minC)}–${f(s.maxC)} °C), ${rs.length} readings:`
    : `Humidity history for ${id}, ${rs.length} readings:`;
  const rows = rs.slice(-20).map((r) => {
    const v = metric === "temp" ? `${f(r.tempC)} °C` : r.hum === null ? "no reading" : `${f(r.hum)} %`;
    const out = metric === "temp" && (r.tempC < s.minC || r.tempC > s.maxC) ? "  [outside envelope]" : "";
    return `${r.at}  ${v}${out}`;
  });
  return { kind: "answer", text: [head, ...rows].join("\n") };
}

function incidents(st: Store, id: string): Reply {
  const s = st.byId.get(id);
  if (!s) return { kind: "unknown-id", text: `No shipment ${id} exists in the approved synthetic dataset.` };
  const a = st.alertsOf(id);
  if (!a.length) return { kind: "answer", text: `No alert records exist for ${id} in the approved synthetic dataset. (Absence of records is not a real-world bill of health — this is synthetic data.)` };
  return {
    kind: "answer",
    text: [`Alert records for ${id} (synthetic):`, ...a.map((x) => `${x.at}  ${x.type} (${x.severity}): ${x.message}`)].join("\n"),
  };
}

function compare(st: Store, a: string, b: string): Reply {
  const sa = st.byId.get(a), sb = st.byId.get(b);
  if (!sa || !sb) return { kind: "unknown-id", text: `Comparison needs two existing shipment IDs (got ${a}, ${b}).` };
  const ta = st.stats(a), tb = st.stats(b);
  const outA = ta.min < sa.minC || ta.max > sa.maxC;
  const outB = tb.min < sb.minC || tb.max > sb.maxC;
  const lines = [
    `Comparison of ${a} and ${b} (synthetic test data):`,
    `${a}: ${sa.cargo}, envelope ${f(sa.minC)}–${f(sa.maxC)} °C, observed ${f(ta.min)}–${f(ta.max)} °C (avg ${f(ta.avg)}), ${st.alertsOf(a).length} alerts, verdict ${sa.verdict}.`,
    `${b}: ${sb.cargo}, envelope ${f(sb.minC)}–${f(sb.maxC)} °C, observed ${f(tb.min)}–${f(tb.max)} °C (avg ${f(tb.avg)}), ${st.alertsOf(b).length} alerts, verdict ${sb.verdict}.`,
    outA === outB
      ? `On the stated criteria (envelope compliance and recorded alerts) neither clearly shows more confirmed issues.`
      : outA
        ? `On the stated criteria (envelope compliance), ${a} shows readings outside its envelope while ${b} does not.`
        : `On the stated criteria (envelope compliance), ${b} shows readings outside its envelope while ${a} does not.`,
  ];
  return { kind: "answer", text: lines.join("\n") };
}

function sopSearch(sops: { title: string; section: string; excerpt: string }[], q: string): Reply {
  const hits = sops.filter(
    (s) => s.title.toLowerCase().includes(q) || s.excerpt.toLowerCase().includes(q),
  );
  if (!hits.length) return { kind: "answer", text: `No SOP section matched "${q}" in the approved synthetic SOP corpus.` };
  return {
    kind: "answer",
    text: [`SOP matches (SYNTHETIC TEST DOCUMENTS — procedural reference only, human review required):`,
      ...hits.slice(0, 3).map((h) => `[${h.title} | section ${h.section}]\n${h.excerpt.slice(0, 300)}…`)].join("\n"),
  };
}

export function askOffline(st: Store & { sops?: { title: string; section: string; excerpt: string }[] }, raw: string): Reply {
  const q = (raw ?? "").trim();
  if (!q) return { kind: "error", text: "Please enter a question." };
  if (q.length > MAX_INPUT_CHARS) return { kind: "error", text: `Question too long (limit ${MAX_INPUT_CHARS} characters).` };
  const ids = [...q.matchAll(/\bUC-\d{6}\b/gi)].map((m) => m[0].toUpperCase());
  const low = q.toLowerCase();
  if (ids.length >= 2) {
    if (/compar|vs\.?|versus|better|riskier|differ/.test(low)) return compare(st, ids[0], ids[1]);
    return shipmentReport(st, ids[0]);
  }
  if (ids.length === 1) {
    if (/humid/.test(low)) return tempHistory(st, ids[0], "hum");
    if (/temp|readings|history|high|low|max|min|average/.test(low)) return tempHistory(st, ids[0], "temp");
    if (/incident|alert|problem|issue|happen|wrong/.test(low)) return incidents(st, ids[0]);
    return shipmentReport(st, ids[0]);
  }
  if (/sop|procedure/.test(low)) return sopSearch(st.sops ?? [], (low.replace(/.*sop\s*(for|about|on)?\s*/, "").slice(0, 40)) || "temperature");
  const id = validateShipmentId(q);
  if (id) return shipmentReport(st, id);
  return {
    kind: "unsupported",
    text: "This offline assistant answers questions about approved synthetic shipments (e.g. \"Show UC-003241\", \"temperature history for UC-003916\", \"compare UC-003241 and UC-003916\", \"SOP for temperature excursions\"). Live NVIDIA-powered inference is a separate, gated mode.",
  };
}
