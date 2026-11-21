/* Offline AI Assistant Engine — Cold-Chain Logistics (Phase 8)
 * Deterministic intent routing + dynamic response generation from bundled
 * synthetic data. NO live LLM inference, NO external API calls.
 *
 * Answers are labeled: "Offline AI Demonstration: generated from curated
 * knowledge and bundled synthetic data. No live LLM inference."
 */
"use strict";

// ─────────────────────────── Intent Engine ───────────────────────────
const INTENTS = {
  // Shipment record
  SHOW_SHIPMENT:    /show\b|details?\b|tell me about|what is|look up|report/i,
  SHIPMENT_STATUS:  /status\b|verdict\b|accepted\b|rejected\b|outcome/i,
  SHIPMENT_CARGO:   /cargo\b|what.*carrying|contents\b|product/i,

  // Temperature
  MAX_TEMP:         /highest|max(imum)?\s+temp|max(imum)?\s+record/i,
  MIN_TEMP:         /lowest|min(imum)?\s+temp|min(imum)?\s+record/i,
  AVG_TEMP:         /average|mean\b|typical/i,
  TEMP_HISTORY:     /temperature\b.*history|temp(erature)?\s+(chart|graph|timeline|trend)/i,
  EXCEED_THRESHOLD: /exceed|above.*threshold|over.*limit|too (high|hot|warm)|out.*range|excursion/i,
  TEMP_RANGE:       /range|limits?\b|configured|threshold/i,

  // Humidity
  HUMIDITY:         /humidity|hum\b|moisture/i,
  MAX_HUM:          /highest|max(imum)?.*hum/i,
  AVG_HUM:          /average.*hum|mean.*hum/i,

  // Incidents
  INCIDENTS:        /incident|excursion event|breach|violation|alert/i,
  INCIDENT_COUNT:   /how many.*(incident|excursion|alert|breach)/i,

  // Custody
  CUSTODY:          /custody|hand(over|off)|seal|transfer|received by|chain of custody/i,

  // Dataset-wide analytics
  FLEET_STATS:      /how many shipment|total shipment|fleet|all shipment|overview|summary/i,
  EXCEEDING_SHIPMENTS: /which shipment.*(exceed|above|over|excursion)|shipments with.*incident/i,
  CARGO_TYPES:      /cargo type|what.*cargo|type of.*goods|product type/i,
  VERDICT_STATS:    /how many.*(accepted|rejected|pending)|verdict.*(count|stats|summary)/i,

  // SOP
  SOP_SEARCH:       /sop|procedure|corrective|mitigation|what should|what action|recommend/i,
  SOP_SECTIONS:     /sop.*(section|list|category|category)|list.*sop/i,

  // Architecture
  ARCHITECTURE:     /architecture|how does.*work|system design|infrastructure|stack/i,
  SQL_GUARD:        /sql guard|read.only|security|permission|access control/i,
  AI_MODEL:         /model|llm|glm|nemotron|nvidia|pinecone|embedding/i,
  AUDIT:            /audit|logging|correlation|monitoring/i,

  // Meta
  HELP:             /help|what can you|guide|instruction|how to use/i,
  RECORDED_AI:      /recorded|real ai|phase 6|phase 8|e2e|live demo|actual.*answer/i,
  PERFORMANCE:      /performance|latency|speed|how fast|response time|test/i,
  COST:             /cost|price|billing|spend|charge/i,
};

// Suggested questions shown as chips
const SUGGESTIONS = [
  "Show shipment UC-003241",
  "What was the highest temperature for UC-003241?",
  "Were there any incidents for UC-003241?",
  "How many shipments are in the dataset?",
  "Which shipments have incidents?",
  "What cargo types are represented?",
  "Find SOP for temperature excursion",
  "How does the SQL guard work?",
  "What was the Phase 6 AI answer?",
  "What is the average temperature for UC-003241?",
  "Show custody handovers for UC-003241",
  "What is the system architecture?",
];

// ─────────────────────────── Utility ───────────────────────────
function extractSID(text) {
  const m = text.toUpperCase().match(/\bUC-(\d{6})\b/);
  return m ? "UC-" + m[1] : null;
}

function normalize(text) {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function findIntent(text) {
  const q = normalize(text);
  // Priority-ordered matching
  if (INTENTS.RECORDED_AI.test(q)) return "recorded_ai";
  if (INTENTS.HELP.test(q)) return "help";
  if (INTENTS.INCIDENT_COUNT.test(q)) return "incident_count";
  if (INTENTS.MAX_TEMP.test(q)) return "max_temp";
  if (INTENTS.MIN_TEMP.test(q)) return "min_temp";
  if (INTENTS.AVG_TEMP.test(q) && !INTENTS.HUMIDITY.test(q)) return "avg_temp";
  if (INTENTS.MAX_HUM.test(q)) return "max_hum";
  if (INTENTS.AVG_HUM.test(q)) return "avg_hum";
  if (INTENTS.HUMIDITY.test(q)) return "humidity";
  if (INTENTS.EXCEED_THRESHOLD.test(q)) return "exceed";
  if (INTENTS.TEMP_HISTORY.test(q)) return "temp_history";
  if (INTENTS.TEMP_RANGE.test(q)) return "temp_range";
  if (INTENTS.INCIDENTS.test(q)) return "incidents";
  if (INTENTS.CUSTODY.test(q)) return "custody";
  if (INTENTS.EXCEEDING_SHIPMENTS.test(q)) return "exceeding_shipments";
  if (INTENTS.FLEET_STATS.test(q)) return "fleet_stats";
  if (INTENTS.CARGO_TYPES.test(q)) return "cargo_types";
  if (INTENTS.VERDICT_STATS.test(q)) return "verdict_stats";
  if (INTENTS.SOP_SEARCH.test(q)) return "sop_search";
  if (INTENTS.SOP_SECTIONS.test(q)) return "sop_sections";
  if (INTENTS.SQL_GUARD.test(q)) return "sql_guard";
  if (INTENTS.AI_MODEL.test(q)) return "ai_model";
  if (INTENTS.ARCHITECTURE.test(q)) return "architecture";
  if (INTENTS.AUDIT.test(q)) return "audit";
  if (INTENTS.PERFORMANCE.test(q)) return "performance";
  if (INTENTS.COST.test(q)) return "cost";
  if (INTENTS.SHIPMENT_STATUS.test(q)) return "shipment_status";
  if (INTENTS.SHIPMENT_CARGO.test(q)) return "shipment_cargo";
  if (INTENTS.SHOW_SHIPMENT.test(q)) return "show_shipment";
  return null;
}

// ─────────────────────────── Data helpers ───────────────────────────
const _shipIdx = {};
if (typeof CC_DATA !== "undefined") {
  CC_DATA.shipments.forEach((s, i) => { _shipIdx[s[0]] = i; });
}

function getShip(sid) {
  const i = _shipIdx[sid];
  return i !== undefined ? CC_DATA.shipments[i] : null;
}

function getTelSummary(sid) {
  return CC_DATA.telSummary[sid] || null;
}

function getAlerts(sid) {
  return CC_DATA.alerts[sid] || [];
}

function getCustody(sid) {
  return CC_DATA.custody[sid] || [];
}

function fmtTemp(v) { return v !== null && v !== undefined ? v.toFixed(1) + "°C" : "not available"; }

function escHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// ─────────────────────────── Response Generators ───────────────────────────
const OFFLINE_LABEL = "📋 <b>Offline AI Demonstration</b> — generated from bundled synthetic data. No live LLM inference.";

function genShipmentReport(sid) {
  const s = getShip(sid);
  if (!s) return unknownShipment(sid);
  const ts = getTelSummary(sid);
  const alerts = getAlerts(sid);
  const cust = getCustody(sid);
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n`;
  r += `<h4>📦 Shipment ${escHtml(sid)}</h4>\n`;
  r += `<table class="ev-table"><tr><th>Field</th><th>Value</th></tr>`;
  r += `<tr><td>Cargo</td><td>${escHtml(s[1])}</td></tr>`;
  r += `<tr><td>Vehicle</td><td>${escHtml(s[2])}</td></tr>`;
  r += `<tr><td>Departed</td><td>${escHtml(s[3])}</td></tr>`;
  r += `<tr><td>Arrived</td><td>${escHtml(s[4])}</td></tr>`;
  r += `<tr><td>Verdict</td><td><b>${escHtml(s[5])}</b></td></tr>`;
  r += `<tr><td>Temp range</td><td>${s[6]}°C – ${s[7]}°C</td></tr>`;
  r += `<tr><td>Pallets</td><td>${s[8]}</td></tr>`;
  if (ts) {
    r += `<tr><td>Readings</td><td>${ts[6]}</td></tr>`;
    r += `<tr><td>Min temp</td><td>${fmtTemp(ts[0])}</td></tr>`;
    r += `<tr><td>Max temp</td><td>${fmtTemp(ts[1])}</td></tr>`;
    r += `<tr><td>Avg temp</td><td>${fmtTemp(ts[2])}</td></tr>`;
    r += `<tr><td>Excursions</td><td>${ts[7]}</td></tr>`;
  }
  r += `<tr><td>Alerts</td><td>${alerts.length}</td></tr>`;
  r += `<tr><td>Custody handoffs</td><td>${cust.length}</td></tr>`;
  r += `<tr><td>Data class</td><td><span class="tag-sim">SYNTHETIC TEST DATA</span></td></tr>`;
  r += `</table>`;
  if (ts && ts[7] > 0) {
    r += `<p class="note">⚠️ ${ts[7]} temperature excursion(s) recorded. `;
    r += `Source: bundled dataset, telemetry summary for ${escHtml(sid)}.</p>`;
  }
  return r;
}

function genMaxTemp(sid) {
  const ts = getTelSummary(sid);
  const s = getShip(sid);
  if (!ts) return noTelemetry(sid);
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>The <b>highest recorded temperature</b> for shipment ${escHtml(sid)} was ` +
    `<b>${fmtTemp(ts[1])}</b>.</p>` +
    `<p class="note">Configured range: ${s ? s[6] + "–" + s[7] + "°C" : "not available"}. ` +
    `${ts[1] > (s ? s[7] : 999) ? "⚠️ This exceeds the configured maximum." : "Within range."} ` +
    `Source: ${ts[6]} telemetry readings, bundled synthetic dataset.</p>`;
}

function genMinTemp(sid) {
  const ts = getTelSummary(sid);
  const s = getShip(sid);
  if (!ts) return noTelemetry(sid);
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>The <b>lowest recorded temperature</b> for shipment ${escHtml(sid)} was ` +
    `<b>${fmtTemp(ts[0])}</b>.</p>` +
    `<p class="note">Source: ${ts[6]} readings, bundled synthetic dataset.</p>`;
}

function genAvgTemp(sid) {
  const ts = getTelSummary(sid);
  if (!ts) return noTelemetry(sid);
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>The <b>average temperature</b> for shipment ${escHtml(sid)} was <b>${fmtTemp(ts[2])}</b> ` +
    `(across ${ts[6]} readings).</p>` +
    `<p class="note">Range: ${fmtTemp(ts[0])} – ${fmtTemp(ts[1])}. Source: bundled synthetic dataset.</p>`;
}

function genExceed(sid) {
  const s = getShip(sid);
  const ts = getTelSummary(sid);
  if (!s || !ts) return noTelemetry(sid);
  const exceeded = ts[1] > s[7] || ts[0] < s[6];
  const excursions = ts[7];
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>${exceeded ? "⚠️ <b>Yes</b> — shipment " + escHtml(sid) + " recorded temperature excursions." :
      "<b>No</b> — shipment " + escHtml(sid) + " stayed within its configured range."}</p>` +
    `<table class="ev-table"><tr><th>Metric</th><th>Value</th></tr>` +
    `<tr><td>Configured range</td><td>${s[6]}°C – ${s[7]}°C</td></tr>` +
    `<tr><td>Min recorded</td><td>${fmtTemp(ts[0])}</td></tr>` +
    `<tr><td>Max recorded</td><td>${fmtTemp(ts[1])}</td></tr>` +
    `<tr><td>Excursion count</td><td>${excursions}</td></tr></table>` +
    `<p class="note">Source: ${ts[6]} telemetry readings, bundled synthetic dataset.</p>`;
}

function genIncidents(sid) {
  const alerts = getAlerts(sid);
  const ts = getTelSummary(sid);
  if (alerts.length === 0 && (!ts || ts[7] === 0)) {
    return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
      `<p><b>No incidents or alerts</b> were recorded for shipment ${escHtml(sid)}.</p>` +
      `<p class="note">This means zero incidents in the bundled synthetic dataset — not missing data.</p>`;
  }
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n`;
  r += `<p>Shipment ${escHtml(sid)} has <b>${alerts.length} alert(s)</b>`;
  if (ts && ts[7] > 0) r += ` and <b>${ts[7]} temperature excursion(s)</b>`;
  r += `.</p><table class="ev-table"><tr><th>Time</th><th>Type</th><th>Severity</th><th>Message</th></tr>`;
  alerts.slice(0, 10).forEach(a => {
    r += `<tr><td>${escHtml(a[0])}</td><td>${escHtml(a[1])}</td><td>${escHtml(a[2])}</td><td>${escHtml(a[3])}</td></tr>`;
  });
  r += `</table>`;
  if (alerts.length > 10) r += `<p class="note">Showing first 10 of ${alerts.length} alerts.</p>`;
  r += `<p class="note">Source: bundled synthetic dataset alerts table.</p>`;
  return r;
}

function genCustody(sid) {
  const cust = getCustody(sid);
  if (!cust.length) return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p><b>No custody handoff records</b> available for shipment ${escHtml(sid)} in the bundled dataset.</p>`;
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n`;
  r += `<p>Shipment ${escHtml(sid)} has <b>${cust.length} custody handoff(s)</b>:</p>`;
  r += `<table class="ev-table"><tr><th>#</th><th>Received by</th><th>Handed at</th><th>Seal</th></tr>`;
  cust.slice(0, 10).forEach(c => {
    r += `<tr><td>${c[0]}</td><td>${escHtml(c[1])}</td><td>${escHtml(c[2])}</td><td>${escHtml(c[3])}</td></tr>`;
  });
  r += `</table><p class="note">Source: bundled synthetic dataset custody table.</p>`;
  return r;
}

function genHumidity(sid) {
  const ts = getTelSummary(sid);
  if (!ts || ts[3] === null) return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p><b>No humidity readings</b> available for shipment ${escHtml(sid)}. ` +
    `This may mean the shipment's sensors did not record humidity — not a data error.</p>`;
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>Humidity for shipment ${escHtml(sid)}:</p>` +
    `<table class="ev-table"><tr><th>Metric</th><th>Value</th></tr>` +
    `<tr><td>Minimum</td><td>${ts[3]}%</td></tr>` +
    `<tr><td>Maximum</td><td>${ts[4]}%</td></tr>` +
    `<tr><td>Average</td><td>${ts[5]}%</td></tr></table>` +
    `<p class="note">Source: ${ts[6]} readings, bundled synthetic dataset.</p>`;
}

function genFleetStats() {
  const ships = CC_DATA.shipments;
  const verdicts = {};
  const cargos = {};
  let excTotal = 0, alertShips = 0;
  ships.forEach(s => {
    verdicts[s[5]] = (verdicts[s[5]] || 0) + 1;
    cargos[s[1]] = (cargos[s[1]] || 0) + 1;
    const ts = CC_DATA.telSummary[s[0]];
    if (ts && ts[7] > 0) excTotal += ts[7];
  });
  alertShips = Object.keys(CC_DATA.alerts).length;
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n`;
  r += `<h4>📊 Dataset Overview</h4><table class="ev-table">`;
  r += `<tr><th>Metric</th><th>Value</th></tr>`;
  r += `<tr><td>Total shipments</td><td>${ships.length.toLocaleString()}</td></tr>`;
  r += `<tr><td>Telemetry readings</td><td>${CC_DATA.meta.telemetry.toLocaleString()}</td></tr>`;
  r += `<tr><td>Alerts</td><td>${CC_DATA.meta.alerts.toLocaleString()}</td></tr>`;
  r += `<tr><td>Custody handoffs</td><td>${CC_DATA.meta.custody.toLocaleString()}</td></tr>`;
  r += `<tr><td>Shipments with alerts</td><td>${alertShips.toLocaleString()}</td></tr>`;
  r += `<tr><td>Total temperature excursions</td><td>${excTotal.toLocaleString()}</td></tr>`;
  r += `<tr><td>Cargo types</td><td>${Object.keys(cargos).length}</td></tr>`;
  r += `<tr><td>Data class</td><td><span class="tag-sim">SYNTHETIC TEST DATA (2021–Oct 2026)</span></td></tr>`;
  r += `</table>`;
  r += `<p class="note">Source: bundled synthetic dataset (${ships.length} shipment records).</p>`;
  return r;
}

function genExceeding() {
  const results = [];
  CC_DATA.shipments.forEach(s => {
    const ts = CC_DATA.telSummary[s[0]];
    if (ts && ts[7] > 0) {
      results.push({ sid: s[0], cargo: s[1], exc: ts[7], max: ts[1], tmax: s[7] });
    }
  });
  results.sort((a, b) => b.exc - a.exc);
  if (!results.length) return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n<p>No shipments with excursions found.</p>`;
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n`;
  r += `<p><b>${results.length} shipments</b> recorded temperature excursions. Top 10 by excursion count:</p>`;
  r += `<table class="ev-table"><tr><th>Shipment</th><th>Cargo</th><th>Excursions</th><th>Max temp</th><th>Limit</th></tr>`;
  results.slice(0, 10).forEach(x => {
    r += `<tr><td>${x.sid}</td><td>${escHtml(x.cargo)}</td><td>${x.exc}</td><td>${fmtTemp(x.max)}</td><td>${x.tmax}°C</td></tr>`;
  });
  r += `</table><p class="note">Source: bundled synthetic dataset, telemetry summaries.</p>`;
  return r;
}

function genCargoTypes() {
  const cargos = {};
  CC_DATA.shipments.forEach(s => { cargos[s[1]] = (cargos[s[1]] || 0) + 1; });
  const entries = Object.entries(cargos).sort((a, b) => b[1] - a[1]);
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n<p>The dataset contains <b>${entries.length} cargo types</b>:</p>`;
  r += `<table class="ev-table"><tr><th>Cargo type</th><th>Shipments</th></tr>`;
  entries.forEach(([k, v]) => { r += `<tr><td>${escHtml(k)}</td><td>${v.toLocaleString()}</td></tr>`; });
  r += `</table><p class="note">Source: bundled synthetic dataset.</p>`;
  return r;
}

function genVerdictStats() {
  const verdicts = {};
  CC_DATA.shipments.forEach(s => { verdicts[s[5]] = (verdicts[s[5]] || 0) + 1; });
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n<p><b>Shipment verdict summary</b> (${CC_DATA.shipments.length.toLocaleString()} total):</p>`;
  r += `<table class="ev-table"><tr><th>Verdict</th><th>Count</th><th>%</th></tr>`;
  Object.entries(verdicts).sort((a, b) => b[1] - a[1]).forEach(([k, v]) => {
    r += `<tr><td>${escHtml(k)}</td><td>${v.toLocaleString()}</td><td>${(100*v/CC_DATA.shipments.length).toFixed(1)}%</td></tr>`;
  });
  r += `</table><p class="note">Source: bundled synthetic dataset.</p>`;
  return r;
}

function genSOPSearch(query) {
  const q = normalize(query);
  const results = [];
  CC_DATA.sops.forEach(doc => {
    doc.sections.forEach(sec => {
      const text = normalize(sec.title + " " + sec.body);
      const words = q.split(" ").filter(w => w.length > 3);
      const matches = words.filter(w => text.includes(w)).length;
      if (matches > 0 || text.includes("corrective")) {
        results.push({ doc: doc, sec: sec, score: matches });
      }
    });
  });
  results.sort((a, b) => b.score - a.score);
  if (!results.length) {
    return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
      `<p><b>No matching SOP sections found</b> for that query. Try: "temperature excursion", ` +
      `"corrective action", "custody transfer", or "sensor fault".</p>` +
      `<p class="note">This is a local keyword search over bundled SOP documents — not the original ` +
      `Pinecone vector search. The deployed system uses 2048-dimension semantic embeddings.</p>`;
  }
  let r = `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n`;
  r += `<p>Found <b>${results.length} matching SOP section(s)</b>:</p>`;
  results.slice(0, 3).forEach(x => {
    r += `<div class="sop-card"><h4>${escHtml(x.sec.title)}</h4>`;
    r += `<p class="sop-cite">[${escHtml(x.doc.title)} | v${escHtml(x.doc.version)} | ${escHtml(x.doc.category)}]</p>`;
    r += `<p>${escHtml(x.sec.body.substring(0, 300))}${x.sec.body.length > 300 ? "…" : ""}</p></div>`;
  });
  r += `<p class="note">⚠️ These are SYNTHETIC TEST DOCUMENTS — not approved operational policies. ` +
    `This is a local keyword search, not the original Pinecone semantic retrieval.</p>`;
  return r;
}

function genArchitecture() {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<h4>🏗️ System Architecture</h4>` +
    `<p>The deployed system runs on AWS with these layers:</p>` +
    `<ul>` +
    `<li><b>Edge:</b> CloudFront → ALB → OAuth2 Proxy + Cognito (fail-closed)</li>` +
    `<li><b>Agent:</b> LangGraph orchestrator with guarded SQL, Pinecone SOP, weather tools</li>` +
    `<li><b>Data:</b> RDS SQL Server with read-only FDE_VIEWS, 4,020 synthetic shipments</li>` +
    `<li><b>Events:</b> S3 → SQS → worker → RDS incidents → SNS (with DLQ, reconcile)</li>` +
    `<li><b>Models:</b> GLM-5.3-Flash (ReAct) + Nemotron Lightning (synthesis) + Nemotron Embed</li>` +
    `<li><b>Monitoring:</b> Grafana (6 dashboards) + CloudWatch audit (structured JSON, 90-day retention)</li>` +
    `</ul>` +
    `<p class="note">This is a factual description of the deployed architecture. See the Architecture tab for interactive details.</p>`;
}

function genSQLGuard() {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<h4>🛡️ SQL Guard</h4>` +
    `<p>The AI agent queries SQL Server through an <b>enforceable read-only guard</b>:</p>` +
    `<ul>` +
    `<li><b>Allowlist:</b> only approved FDE_VIEWS.VW_* schemas (base tables blocked)</li>` +
    `<li><b>SELECT-only:</b> INSERT/UPDATE/DELETE/DROP/ALTER/EXEC rejected</li>` +
    `<li><b>TOP 1-100:</b> mandatory row cap; aggregate-only queries exempt</li>` +
    `<li><b>Single statement:</b> multi-statement injection rejected</li>` +
    `<li><b>10s timeout:</b> bounded execution</li>` +
    `<li><b>Read-only identity:</b> agent uses USR_FDE_RO (SELECT on views only)</li>` +
    `</ul>` +
    `<p class="note">All queries are audited with CloudWatch correlation IDs. ` +
    `Base-table access is functionally denied — verified live.</p>`;
}

function genAIModel() {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<h4>🤖 AI Model Architecture</h4>` +
    `<table class="ev-table"><tr><th>Model</th><th>Role</th><th>Why</th></tr>` +
    `<tr><td>z-ai/glm-5.3-flash</td><td>Primary ReAct reasoning</td><td>Qualified 14/15, best accuracy</td></tr>` +
    `<tr><td>nemotron-3.5-lightning</td><td>Evidence synthesis (prefetch)</td><td>~1s/turn; tool-selection errors eliminated</td></tr>` +
    `<tr><td>nemotron-3.5-embed-1b</td><td>SOP embeddings (2048-d)</td><td>Same NIM endpoint/key</td></tr></table>` +
    `<p class="note">The deployed system uses these NVIDIA NIM models through integrate.api.nvidia.com. ` +
    `This offline assistant uses NO live models — all answers are deterministic.</p>`;
}

function genPerformance() {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<h4>📊 Verified Performance</h4>` +
    `<table class="ev-table"><tr><th>Metric</th><th>Measured</th></tr>` +
    `<tr><td>Shipment report E2E</td><td><b>28.5s</b> (UC-003916, 6/6 checks)</td></tr>` +
    `<tr><td>Incident + SOP E2E</td><td><b>69.9s</b> (UC-003241, 16/16 checks)</td></tr>` +
    `<tr><td>Avg SQL query</td><td>137ms</td></tr>` +
    `<tr><td>Prefetch total (5 queries)</td><td>1.2s</td></tr>` +
    `<tr><td>Automated tests</td><td>323/323 passing</td></tr>` +
    `<tr><td>Month-to-date AWS cost</td><td>$6.22</td></tr></table>` +
    `<p class="note">These are verified one-time measurements from the deployed system — not SLAs.</p>`;
}

function genRecordedAI() {
  return `<div class="ai-hdr">✅ <b>Recorded AI Execution</b> — captured from the original deployed system. NOT a live response.</div>\n` +
    `<p>See the <b>AI Assistant</b> tab for the full recorded Phase 6 answer (UC-003241 incident + SOP citation, ` +
    `69.9s, 16/16 validator checks, request_id 4d1f8470…).</p>` +
    `<p>The deployed system used: GLM-5.3-Flash (ReAct) → deterministic prefetch (5 SQL queries) → ` +
    `Nemotron-3.5-Lightning synthesis → 16-check answer validator → CloudWatch audit correlation.</p>` +
    `<p class="note">This offline assistant does NOT reproduce that inference — it uses deterministic ` +
    `response generation from the same bundled synthetic data.</p>`;
}

function genHelp() {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<h4>💡 What you can ask</h4>` +
    `<p>I can answer questions about <b>any shipment</b> in the bundled synthetic dataset (4,020 shipments, 64,120 telemetry readings). Try:</p>` +
    `<ul>` +
    `<li><b>Shipment:</b> "Show UC-003241" · "What is the status of UC-003916?" · "Tell me about UC-002170"</li>` +
    `<li><b>Temperature:</b> "What was the highest temperature for UC-003241?" · "Did UC-003916 exceed its threshold?"</li>` +
    `<li><b>Incidents:</b> "Were there any incidents for UC-003241?" · "How many excursions for UC-003916?"</li>` +
    `<li><b>Humidity:</b> "What is the humidity for UC-003241?"</li>` +
    `<li><b>Custody:</b> "Show custody handovers for UC-003241"</li>` +
    `<li><b>Dataset:</b> "How many shipments are in the dataset?" · "Which shipments have incidents?" · "What cargo types?"</li>` +
    `<li><b>SOP:</b> "Find SOP for temperature excursion" · "What is the corrective action?"</li>` +
    `<li><b>Architecture:</b> "How does the SQL guard work?" · "What is the system architecture?"</li>` +
    `<li><b>Performance:</b> "What was the E2E latency?" · "How many tests passed?"</li>` +
    `</ul>` +
    `<p class="note">💡 Include a shipment ID (UC-xxxxxx) for shipment-specific questions. Without one, I'll show dataset-wide answers or ask for clarification.</p>`;
}

// ─────────────────────────── Error handlers ───────────────────────────
function unknownShipment(sid) {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>❓ Shipment <b>${escHtml(sid)}</b> was not found in the bundled synthetic dataset.</p>` +
    `<p class="note">The dataset contains ${CC_DATA.shipments.length.toLocaleString()} shipments. ` +
    `Try a valid ID like UC-003241, UC-003916, or UC-002170. Use "help" for more options.</p>`;
}

function noTelemetry(sid) {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>No telemetry summary available for <b>${escHtml(sid)}</b>. ` +
    `This shipment may not have recorded temperature readings in the bundled dataset.</p>` +
    `<p class="note">This means no data exists — not a query error. Try "show ${escHtml(sid)}" for the shipment record.</p>`;
}

function noIntent(text, activeSid) {
  const sid = extractSID(text);
  if (sid) {
    return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
      `<p>I found shipment <b>${escHtml(sid)}</b> but I'm not sure what you want to know about it.</p>` +
      `<p>Try: "show ${escHtml(sid)}" · "max temp ${escHtml(sid)}" · "incidents ${escHtml(sid)}" · "custody ${escHtml(sid)}"</p>`;
  }
  if (activeSid) {
    return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
      `<p>I don't understand that question. You currently have shipment <b>${activeSid}</b> selected.</p>` +
      `<p>Try: "show ${activeSid}" · "max temp" (uses active shipment) · "help" for all options.</p>`;
  }
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>I don't understand that question. Type <b>"help"</b> to see what I can answer, ` +
    `or include a shipment ID like <b>UC-003241</b> for shipment-specific questions.</p>` +
    `<p class="note">This is a deterministic offline assistant — it cannot answer questions ` +
    `outside the bundled data and knowledge. No live LLM was invoked.</p>`;
}

// ─────────────────────────── Main router ───────────────────────────
let activeShipment = null;
let chatHistory = [];

function processQuestion(text) {
  const sid = extractSID(text);
  // Conversational context: if no SID but we have an active one, use it
  const targetSid = sid || activeShipment;
  const intent = findIntent(text);

  // Update active shipment
  if (sid) activeShipment = sid;

  let response;
  switch (intent) {
    case "show_shipment": case "shipment_cargo":
      response = targetSid ? genShipmentReport(targetSid) : genFleetStats();
      break;
    case "shipment_status":
      response = targetSid ? genShipmentReport(targetSid) : genVerdictStats();
      break;
    case "max_temp":
      response = targetSid ? genMaxTemp(targetSid) : noTarget("highest temperature");
      break;
    case "min_temp":
      response = targetSid ? genMinTemp(targetSid) : noTarget("lowest temperature");
      break;
    case "avg_temp":
      response = targetSid ? genAvgTemp(targetSid) : noTarget("average temperature");
      break;
    case "exceed":
      response = targetSid ? genExceed(targetSid) : genExceeding();
      break;
    case "temp_history":
      response = targetSid ? genShipmentReport(targetSid) : noTarget("temperature history");
      break;
    case "temp_range":
      response = targetSid ? genExceed(targetSid) : noTarget("temperature range");
      break;
    case "humidity": case "max_hum": case "avg_hum":
      response = targetSid ? genHumidity(targetSid) : noTarget("humidity");
      break;
    case "incident_count":
      response = targetSid ? genIncidents(targetSid) : genExceeding();
      break;
    case "incidents":
      response = targetSid ? genIncidents(targetSid) : genExceeding();
      break;
    case "custody":
      response = targetSid ? genCustody(targetSid) : noTarget("custody handovers");
      break;
    case "fleet_stats":
      response = genFleetStats();
      break;
    case "exceeding_shipments":
      response = genExceeding();
      break;
    case "cargo_types":
      response = genCargoTypes();
      break;
    case "verdict_stats":
      response = genVerdictStats();
      break;
    case "sop_search": case "sop_sections":
      response = genSOPSearch(text);
      break;
    case "architecture":
      response = genArchitecture();
      break;
    case "sql_guard":
      response = genSQLGuard();
      break;
    case "ai_model":
      response = genAIModel();
      break;
    case "audit":
      response = genSQLGuard();
      break;
    case "performance":
      response = genPerformance();
      break;
    case "cost":
      response = genPerformance();
      break;
    case "recorded_ai":
      response = genRecordedAI();
      break;
    case "help":
      response = genHelp();
      break;
    default:
      response = noIntent(text, activeShipment);
  }

  // If we had a SID and it exists, try to update the demo chart
  if (targetSid && _shipIdx[targetSid] !== undefined) {
    try { if (typeof pick === "function") pick(targetSid); if (typeof showTab === "function") showTab("demo"); } catch(e) {}
  }

  return response;
}

function noTarget(what) {
  return `<div class="ai-hdr">${OFFLINE_LABEL}</div>\n` +
    `<p>Which shipment would you like to know the <b>${what}</b> for?</p>` +
    `<p class="note">Include a shipment ID like UC-003241, or type "help" for more options.</p>`;
}

// ─────────────────────────── Chat UI ───────────────────────────
function initChat() {
  const input = document.getElementById("chat-input");
  const send = document.getElementById("chat-send");
  const chat = document.getElementById("chat-messages");
  const chips = document.getElementById("chat-chips");
  const clear = document.getElementById("chat-clear");

  if (!input || !chat) return;

  // Render suggestion chips
  if (chips) {
    chips.innerHTML = SUGGESTIONS.slice(0, 8).map(s =>
      `<span class="pill" onclick="askAI('${escHtml(s)}')">${escHtml(s)}</span>`
    ).join(" ");
  }

  function sendMsg() {
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    addMsg("user", escHtml(text));
    const response = processQuestion(text);
    setTimeout(() => addMsg("ai", response), 200);
    input.focus();
  }

  if (send) send.onclick = sendMsg;
  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); }
  });
  if (clear) clear.onclick = () => {
    chat.innerHTML = "";
    activeShipment = null;
    addMsg("ai", genHelp());
  };

  // Welcome message
  addMsg("ai", genHelp());
}

function addMsg(role, html) {
  const chat = document.getElementById("chat-messages");
  if (!chat) return;
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.innerHTML = html;
  chat.appendChild(div);
  div.scrollIntoView({ behavior: "smooth", block: "end" });
}

function askAI(q) {
  const input = document.getElementById("chat-input");
  if (input) { input.value = q; }
  addMsg("user", escHtml(q));
  const response = processQuestion(q);
  setTimeout(() => addMsg("ai", response), 200);
}

// Initialize on DOM ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initChat);
} else {
  initChat();
}
