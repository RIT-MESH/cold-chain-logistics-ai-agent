// Builds hf-space-v2/public/data/bundle.json from the APPROVED synthetic CSVs.
// No fabrication: every record comes from data/unified/*.csv; counts are
// verified against DATASET_MANIFEST.json before the bundle is written.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const SRC = path.resolve(import.meta.dirname, "../../data/unified");
const OUT_DIR = path.resolve(import.meta.dirname, "../public/data");
const OUT = path.join(OUT_DIR, "bundle.json");

function parseCsv(file) {
  const text = fs.readFileSync(path.join(SRC, file), "utf-8");
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  const head = lines[0].split(",").map((h) => h.trim());
  return lines.slice(1).map((l) => {
    // minimal CSV handling: the unified CSVs are unquoted except the alert
    // message field (handled separately below)
    const cells = l.split(",");
    const row = {};
    head.forEach((h, i) => (row[h] = cells[i]));
    return row;
  });
}

const manifest = JSON.parse(fs.readFileSync(path.join(SRC, "DATASET_MANIFEST.json"), "utf-8"));
const shipments = parseCsv("shipments.csv");
const telemetry = parseCsv("telemetry.csv");
const alertsRaw = fs.readFileSync(path.join(SRC, "alerts.csv"), "utf-8");
const alertsLines = alertsRaw.split(/\r?\n/).filter((l) => l.trim());
const alerts = [];
{
  const head = alertsLines[0].split(",").map((h) => h.trim());
  for (const line of alertsLines.slice(1)) {
    // alert messages are CSV-quoted and contain commas: use a regex split
    // that keeps quoted segments intact
    const cells = line.match(/("([^"]|"")*"|[^,]*)(,|$)/g)?.map((c) => c.replace(/,$/, "").replace(/^"|"$/g, "").replace(/""/g, '"')) ?? [];
    const row = {};
    head.forEach((h, i) => (row[h] = cells[i]));
    alerts.push(row);
  }
}
const custody = parseCsv("custody_handoffs.csv");

// synthetic SOP corpus (approved demo documents only; the third-party
// baseline SOP is intentionally excluded from the public bundle)
const POLICY = path.resolve(import.meta.dirname, "../../data/policy");
const sops = fs.readdirSync(POLICY).filter((f) => f.startsWith("SYNTHETIC_SOP_") && f.endsWith(".md"))
  .map((f, i) => {
    const text = fs.readFileSync(path.join(POLICY, f), "utf-8");
    const title = (text.match(/^#\s+(.+)$/m)?.[1] ?? f).slice(0, 80);
    const sid = text.match(/Section ([A-F]\d)/)?.[1] ?? `A${i + 1}`;
    return { file: f, title, section: sid, excerpt: text.slice(0, 700) };
  });

// verify counts against the canonical manifest BEFORE writing anything
const expect = manifest.counts;
const assertEq = (name, actual, want) => {
  if (actual !== want) throw new Error(`${name}: got ${actual}, manifest says ${want} — ABORT`);
};
assertEq("shipments", shipments.length, expect["shipments.csv"]);
assertEq("telemetry", telemetry.length, expect["telemetry.csv"]);
assertEq("custody", custody.length, expect["custody_handoffs.csv"]);
assertEq("alerts", alerts.length, expect["alerts.csv"]);

// compact encodings (arrays, not objects — size matters for a static bundle)
const S = shipments.map((r) => [
  r.shipment_id, r.cargo_type, r.vehicle_id, r.departed_at, r.arrived_at,
  r.receiver_verdict, +r.temperature_min_c, +r.temperature_max_c, +r.pallets,
]);
// group telemetry by shipment once
const telByShip = new Map();
for (const r of telemetry) {
  const arr = telByShip.get(r.shipment_id) ?? telByShip.set(r.shipment_id, []).get(r.shipment_id);
  arr.push([r.recorded_at, +r.temperature_c, r.humidity_pct === "" ? null : +r.humidity_pct,
            r.latitude === "" ? null : +r.latitude, r.longitude === "" ? null : +r.longitude,
            r.risk_level]);
}
const T = Object.fromEntries(telByShip);
const A = {};
for (const r of alerts) (A[r.shipment_id] ??= []).push([r.alert_at, r.alert_type, r.severity, r.message]);
const C = {};
for (const r of custody) (C[r.shipment_id] ??= []).push([+r.handoff_no, r.received_by, r.handed_at, r.seal_intact]);

const bundle = {
  meta: {
    generated_at_utc: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    source: "data/unified/*.csv (approved synthetic dataset)",
    counts: { shipments: S.length, telemetry: telemetry.length, alerts: alerts.length,
              custody: custody.length, sops: sops.length },
    shipments_with_alerts: Object.keys(A).length,
    integrity: {
      shipments_sha256: crypto.createHash("sha256").update(JSON.stringify(S)).digest("hex").slice(0, 16),
      telemetry_rows_sha256: crypto.createHash("sha256").update(telemetry.map((r) => r.recorded_at + r.temperature_c).join("|")).digest("hex").slice(0, 16),
    },
  },
  shipments: S, telemetry: T, alerts: A, custody: C, sops,
};

fs.mkdirSync(OUT_DIR, { recursive: true });
const json = JSON.stringify(bundle);
fs.writeFileSync(OUT, json);
const kb = (fs.statSync(OUT).size / 1024).toFixed(0);
console.log(`bundle.json written: ${kb} KB`);
console.log("counts:", JSON.stringify(bundle.meta.counts));
console.log("integrity:", JSON.stringify(bundle.meta.integrity));
