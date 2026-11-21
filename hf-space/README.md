---
title: Cold-Chain Logistics AI Agent
emoji: 🧊
colorFrom: blue
colorTo: indigo
sdk: static
app_file: index.html
pinned: false
license: mit
---

# Cold-Chain Logistics AI Agent — Interactive Portfolio Space

A **fully self-contained** portfolio showcase + offline interactive demo for a
cold-chain logistics AI assistant built on AWS (LangGraph agent, NVIDIA NIM
`z-ai/glm-5.3-flash`, SQL Server, enforceable read-only SQL guard,
Streamlit/LibreChat/Grafana front-ends).

**Teardown-proof by design:** this Space has no backend dependencies — no
AWS, no NVIDIA API keys, no RDS, no paid services. `demo-data.js` embeds a
sanitized sample of the *synthetic* dataset; all demo answers, charts, and
searches run entirely in the visitor's browser. It keeps working
independently of any AWS teardown, and it must NOT be deleted during one.

## Contents

- `index.html` — three tabs: **Offline Demo** (interactive), **Architecture**
  (diagram, features, verified results, security posture), **Live AI**
  (link + real recorded AI responses)
- `demo-data.js` — embedded synthetic sample: 12 shipments (incl. the
  qualification shipment UC-003241), 174 telemetry points, 22 alerts
- `README.md` — this file

## Honest labeling (do not remove)

- The Offline Demo's answers are **SIMULATED** — generated in-browser from
  the embedded sample, not by a language model. The page labels them.
- The Live AI tab shows **REAL recorded responses** from the AWS system and
  links to the authenticated CloudFront entry — clearly marked as available
  *only while the AWS environment exists*.

## Before publishing (operator checklist)

1. Create a free Static Space at huggingface.co/new-space (SDK: **Static**),
   upload both files.
2. Replace the two marked placeholders in `index.html`:
   - `<!-- PLACEHOLDER: LIVE_DEMO_URL -->` → the CloudFront HTTPS URL
     (keep the "available only while AWS exists" wording); remove the
     `disabled` attribute on the button.
   - `<!-- PLACEHOLDER: GITHUB_URL -->` → the public GitHub repository URL.
3. Optionally add real dashboard screenshots (captured from an
   *authenticated* session) to the Architecture tab — never include
   screenshots showing credentials, endpoints, or account identifiers.

## Security rules baked in

- No NVIDIA keys, database credentials, or private endpoints appear
  anywhere in the Space.
- The embedded data is synthetic (2021–Oct 2026) — no real shipments.
- The live demo link points only at the Cognito-authenticated public entry;
  the agent gateway is never publicly routed.
