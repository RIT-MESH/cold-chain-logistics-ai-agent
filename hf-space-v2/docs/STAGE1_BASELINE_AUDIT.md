# HF Space v2 — Stage 1 Baseline Audit (2026-11-22)

Published Space baseline (rollback source): commit `22dcf5a039bd6245a3d43cf9aee2297ff43a5c62`
("Upload assistant.js with huggingface_hub", 2026-11-17 19:36 UTC), sdk: static.
Local `hf-space/` content is line-identical to the published Space (hash differences
are line endings only). Preserved verbatim at `hf-space-v2/docs/baseline/`.

## Inventory (6 files, ~2.0 MB)

| File | Size | Role |
|---|---|---|
| index.html | 41.0 KB | Single-file app: 7 tabs, CSS, markup |
| dataset.js | 1.94 MB | Compact bundle: `CC_DATA` = shipments, telSummary, detailed, alerts, custody, sops |
| assistant.js | 30.9 KB | Deterministic 29-intent offline assistant |
| demo-data.js | 22.3 KB | LEGACY demo dataset (12 shipments / 174 telemetry / 22 alerts) |
| README.md | 2.5 KB | HF Space metadata (sdk: static) |
| .gitattributes | 1.5 KB | HF LFS attrs |

## Dataset verification (programmatic, Node)

| Claim (meta) | Actual in bundle | Verdict |
|---|---|---|
| shipments: 4,020 | 4,020 rows ✓ | matches |
| telemetry: 64,120 | **600 detailed readings (50 shipments × 12)** + per-shipment summary stats for 4,020 | **MISMATCH — see D1** |
| alerts: 8,094 | 8,094 rows across 1,651 shipments ✓ | matches |
| custody: 10,030 | 10,030 rows across 4,020 ✓ | matches |
| sops: 6 | 6 ✓ | matches |
| Referential integrity | 0 orphans (every telemetry/alert/custody key exists in shipments) ✓ | clean |

## Severity-ranked defect report

### S1 — Critical (honesty / core objective violations)
- **D1. Dataset coverage mismatch.** The UI and meta advertise "64,120 telemetry
  rows" (import-vs-manifest, architecture tab), but only 600 full readings for 50
  shipments are genuinely accessible; the other 4,020 shipments expose only summary
  statistics. Any search/answer that implies row-level access to 64,120 readings is
  misleading. v2 fix: ship the COMPLETE 64,120-reading bundle (feasible: compact
  encoding ≈ 2–3 MB) or label coverage exactly ("detailed readings: 50 shipments;
  summary statistics: all 4,020").
- **D2. AWS dependency.** The Live AI tab's only external link is the CloudFront
  `/oauth2/start` demo (AWS-gated, will die at teardown). The portfolio is not
  AWS-independent as required. v2 fix: three clearly separated AI modes — offline
  deterministic (always works), recorded model evidence (static), and live NVIDIA
  inference via the NEW Supabase/NeMo pipeline (disabled until Stage 6 gates pass);
  no CloudFront/AWS URLs anywhere.

### S2 — High
- **D3. Dual dataset sources.** `demo-data.js` (12-shipment legacy demo) and
  `dataset.js` (production bundle) coexist; provenance is confusing and risks
  answers sourced from the stale demo set. v2: single validated data-access layer,
  one source of truth.
- **D4. XSS surface.** 11 `innerHTML` assignments; a correct `escHtml`
  (textContent-based, 46 call sites) exists, but no dataflow proof that every
  user-controlled fragment is escaped. v2: framework auto-escaping + explicit
  tests; never build HTML from raw user input.
- **D5. Outdated engineering evidence.** The Engineering/Recorded-AI tabs cite
  test counts and latency figures from the 11-17/11-18 era (suite has since grown
  to 382; latency/fallback behavior changed) and contain mojibake ("Â·", "â€").
  v2: evidence pulled from the current verified record (382-test suite,
  post-repair behavior), rendered clean.

### S3 — Medium
- **D6. Responsive gaps.** Charts force `min-width:640px` (horizontal scroll on
  phones); media queries exist but are partial; no loading/empty/error states by
  construction (single-page app, no boundaries).
- **D7. AI capability descriptions blur modes.** "AI Chat" (offline deterministic)
  vs "Recorded AI" vs "Live AI" are not crisply distinguished; the login-wall Live
  AI path confuses visitors.
- **D8. Accessibility unaudited** (keyboard navigation, ARIA, focus states).

### S4 — Low
- **D9.** README/HF metadata minimal; **D10.** Recorded-AI gallery not linked to
  its underlying shipment records (v2 Stage 5 requires linkage); **D11.** evidence
  claims not programmatically tied to the published dataset.

### Positives to preserve
Honest synthetic-data labeling throughout; the 29-intent deterministic assistant
(covers show_shipment, temp/humidity history, incidents, custody, comparisons,
aggregates, SOP search with citations, unknown-ID handling); complete
shipments/alerts/custody/SOP coverage; zero credentials or unintended endpoints in
any public asset (verified by pattern scan); keyless assets.

## Verification notes
- Recorded Phase 6/8 answers in the gallery match the evidence docs they cite
  (UC-003241 incident, UC-003916 report) — spot-verified against
  `docs/evidence/PHASE6_E2E5_ANSWER.md` and the dataset (single 1.2 °C excursion
  for UC-003241, no incidents for UC-003916).
- Secrets scan of all 5 public files: 0 credential hits (innerHTML usage noted in D4).
- The `hf-space/` original is untouched; the published Space is untouched (no push
  without approval; commit 22dcf5a recorded as rollback point).
