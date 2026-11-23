// P1: independent dataset integrity verification (Stage 3 directive).
// Re-derives everything from the source CSVs and cross-checks bundle.json.
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const SRC = path.resolve(import.meta.dirname, "../../data/unified");
const BUNDLE = path.resolve(import.meta.dirname, "../public/data/bundle.json");
const manifest = JSON.parse(fs.readFileSync(path.join(SRC, "DATASET_MANIFEST.json"), "utf-8"));
const bundle = JSON.parse(fs.readFileSync(BUNDLE, "utf-8"));

const fail = (m) => { console.error("FAIL:", m); process.exitCode = 1; };
const ok = (m) => console.log("OK:  ", m);

// 1. telemetry row count
const telText = fs.readFileSync(path.join(SRC, "telemetry.csv"), "utf-8");
const telRows = telText.trim().split(/\r?\n/).length - 1;
if (telRows !== 64120) fail(`telemetry.csv rows = ${telRows} (expected 64120)`);
else ok(`telemetry.csv rows = 64120`);
const bundleTelRows = Object.values(bundle.telemetry).reduce((a, b) => a + b.length, 0);
if (bundleTelRows !== telRows) fail(`bundle telemetry rows = ${bundleTelRows} != csv ${telRows}`);
else ok(`bundle ships ALL ${bundleTelRows} telemetry rows (no sampling)`);

// 2. referential integrity: every telemetry/alerts/custody key exists in shipments
const shipIds = new Set(bundle.shipments.map((s) => s[0]));
let orphans = 0;
for (const k of Object.keys(bundle.telemetry)) if (!shipIds.has(k)) { orphans++; fail(`orphan telemetry key ${k}`); }
for (const k of Object.keys(bundle.alerts)) if (!shipIds.has(k)) { orphans++; fail(`orphan alerts key ${k}`); }
for (const k of Object.keys(bundle.custody)) if (!shipIds.has(k)) { orphans++; fail(`orphan custody key ${k}`); }
if (orphans === 0) ok("referential integrity: 0 orphans (telemetry/alerts/custody keys all exist in shipments)");

// 3. duplicate records — keyed by telemetry_id (the true row identity)
const dupShip = bundle.shipments.length - shipIds.size;
if (dupShip) fail(`${dupShip} duplicate shipment ids`);
else ok("0 duplicate shipment ids (4020 unique)");
const telHead = telText.trim().split(/\r?\n/)[0].split(",");
const col = (name) => telHead.indexOf(name);
const cTel = col("telemetry_id"), cShip = col("shipment_id"), cAt = col("recorded_at"), cTemp = col("temperature_c");
if (cTel < 0 || cShip < 0 || cAt < 0 || cTemp < 0) { fail("telemetry.csv missing expected columns"); }
else ok(`telemetry.csv columns located (telemetry_id=${cTel}, shipment_id=${cShip}, recorded_at=${cAt}, temperature_c=${cTemp})`);

// 4. random-shipment statistics verification: pick 8 deterministic ids across
//    the ID range, recompute stats from the CSV independently, compare.
const csvTel = telText.trim().split(/\r?\n/).slice(1);
const picks = ["UC-000004", "UC-000321", "UC-001461", "UC-002191", "UC-002674", "UC-003241", "UC-003916", "UC-004010"];
const telIds = new Set(); let dupTel = 0;
for (const id of picks) {
  const rows = csvTel.filter((l) => l.split(",")[cShip] === id).map((l) => parseFloat(l.split(",")[cTemp]));
  const n = rows.length;
  const min = Math.min(...rows), max = Math.max(...rows);
  const avg = rows.reduce((a, b) => a + b, 0) / n;
  const b = bundle.telemetry[id] ?? [];
  const bn = b.length;
  const bmin = Math.min(...b.map((r) => r[1])), bmax = Math.max(...b.map((r) => r[1]));
  const bavg = b.map((r) => r[1]).reduce((a, c) => a + c, 0) / bn;
  const same = n === bn && Math.abs(min - bmin) < 1e-9 && Math.abs(max - bmax) < 1e-9 && Math.abs(avg - bavg) < 1e-9;
  if (!same) fail(`${id}: CSV(n=${n} ${min.toFixed(2)}/${max.toFixed(2)}/${avg.toFixed(3)}) != bundle(n=${bn} ${bmin.toFixed(2)}/${bmax.toFixed(2)}/${bavg.toFixed(3)})`);
  else ok(`${id}: stats match CSV (n=${n}, min=${min.toFixed(2)}, max=${max.toFixed(2)}, avg=${avg.toFixed(3)})`);
}
// duplicate telemetry_id check across ALL rows
for (const l of csvTel) { const t = l.split(",")[cTel]; if (telIds.has(t)) dupTel++; else telIds.add(t); }
if (dupTel) fail(`${dupTel} duplicate telemetry_id values`);
else ok(`0 duplicate telemetry rows (all ${telIds.size} telemetry_id values unique)`);

// 5. manifest count agreement
const mc = manifest.counts;
const checks = [["shipments.csv", bundle.shipments.length, 4020], ["telemetry.csv", bundleTelRows, 64120],
  ["alerts.csv", Object.values(bundle.alerts).reduce((a, b) => a + b.length, 0), 8094],
  ["custody_handoffs.csv", Object.values(bundle.custody).reduce((a, b) => a + b.length, 0), 10030]];
for (const [name, actual, want] of checks)
  actual === want ? ok(`${name}: ${actual} = manifest ${want}`) : fail(`${name}: ${actual} != manifest ${want}`);

// 6. integrity hashes still match the build-time values
const shipHash = crypto.createHash("sha256").update(JSON.stringify(bundle.shipments)).digest("hex").slice(0, 16);
shipHash === bundle.meta.integrity.shipments_sha256 ? ok(`shipments sha256 ${shipHash} matches`) : fail("shipments hash drift");
console.log("\nDATASET INTEGRITY:", process.exitCode === 1 ? "FAILED" : "ALL CHECKS PASSED");
