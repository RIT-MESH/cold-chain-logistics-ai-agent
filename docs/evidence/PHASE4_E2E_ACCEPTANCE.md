# Phase 4 — End-to-End Acceptance Evidence (2026-11-17, deployed)

**Approval:** operator Phase 4 deployment authorization, 2026-11-17 (14 add /
1 change / 0 destroy, including the deferred Dashboard-D CloudWatch IAM).
Plan verified non-stale before apply (fresh refresh-plan change-set identical
to the saved plan). No Terraform destroy; no SNS subscriptions created; no
destructive database operations (the DLQ purge removed only the three
intentional test rejects after their evidence was captured below).

## 1. Deployment actions executed

| Step | Result |
|---|---|
| Plan freshness check | saved vs. fresh change sets **identical** (15 entries) |
| `terraform apply "tfplan-phase4"` | **14 added, 1 changed, 0 destroyed** |
| SQL migration (`scripts/phase4_event_processing.sql`) | exit 0 via sqlcmd with **full TLS verification** (RDS global CA installed into the host trust store — an improvement over Phase 2's `-C` trust-server-cert procedure) |
| Host rollout | files pushed LF-clean (tarball+base64), `fetch-secrets` roll (3 `TELEMETRY_*` values resolved), 5 app containers recreated, worker + reconcile built and started, nginx restarted |
| Post-apply drift | `terraform plan` → **"no differences"** |

## 2. AWS resources created (all `module.events`, account 123456789012, us-east-1)

- S3 bucket `coldchain-dev-telemetry` (versioned, SSE-S3, public-access-blocked,
  ObjectCreated:Put notification on prefix `synthetic/`)
- SQS `coldchain-dev-telemetry` (visibility 120 s, redrive maxReceiveCount=5)
- SQS DLQ `coldchain-dev-telemetry-dlq` (14-day retention)
- SQS queue policy (S3 service principal, SourceArn-conditioned)
- SNS topic `coldchain-dev-alerts` (**no subscriptions** — recipient unconfirmed)
- Alarms: `coldchain-dev-event-dlq-depth`, `-queue-oldest-age`,
  `-sns-publish-failures`, `-reconcile-pending-age`, `-reconcile-exhausted`
- IAM policy update (1 change): +`sqs:GetQueueUrl`, +`s3:GetObject`,
  +`sqs:SendMessage` (exact DLQ ARN only), +`cloudwatch:PutMetricData`
  (namespace-conditioned), and the **Dashboard-D CloudWatch read-only
  statement** (metrics actions only — no logs read, by design).

## 3. Live-found defects fixed during deployment (honest record)

1. **Worker/reconcile crash-loop** (`ModuleNotFoundError: 'src'`) —
   `Dockerfile.worker` installs only dependencies; the application source was
   never in the image and the AWS compose services lacked the mount the local
   Floci compose had. Fixed: `./src:/workspace/src:ro` mounts (compose).
2. **Worker SQL TLS failure** (`certificate verify failed: unable to get
   local issuer certificate`) — the worker image lacked the RDS CA. Fixed
   WITHOUT disabling verification: RDS global bundle + `update-ca-certificates`
   in `Dockerfile.worker` (same pattern as the streamlit image).
3. **Missing build input** — `requirements-floci.txt` had never been synced
   to the host; worker image build failed until pushed.
4. **Grafana admin 401 storm → brute-force lockout** — the known
   fetch-secrets password drift (password regenerated per roll; Grafana
   applies it only at first DB init) struck again, and repeated 401 probes
   tripped Grafana 11.6's "too many consecutive incorrect login attempts …
   temporarily blocked", which masks whether a reset worked. Resolution:
   `grafana-cli` reset to the current app.env value + wait out the decay
   window. **Recorded procedure: after any fetch-secrets roll, reset the
   Grafana admin password ONCE and do not probe repeatedly.**
   (Improvement candidate: fetch-secrets should reuse a stable password.)
5. **S3 `TestEvent`** — S3 sends a test notification on first notification
   configuration; the worker correctly rejected it to the DLQ
   (`invalid_payload`).

## 4. End-to-end test evidence (synthetic data only)

Test uploads via the S3 → SQS notification path (the instance role
deliberately cannot SendMessage to the main queue — anti-fabrication
property; the publisher's direct-SQS path stays Floci-local).

| Test | Evidence |
|---|---|
| E1 excursion 6.2 °C (UC-003241) | worker log `event_processed new_event=true` + `notification_acked`; SQL: event+incident rows (`6.2C thr 4C, synthetic, OPEN`); attempt row `published_acked sns_msg=7854754f-cc59-5691-b184-163a77397d56`; derived state `sns_acked attempts=1 acked=1`; CloudWatch SNS `NumberOfMessagesPublished Sum=1` |
| E2 in-range 3.5 °C | `event_processed incident=null` — stored, no incident, no publish (SQL events=2 confirms) |
| E3 unknown shipment UC-999999 | `event_bad_shipment_ref` → DLQ `bad_shipment_ref`; **not persisted** (link-failures view = 0) |
| E4 invalid payload | DLQ `invalid_payload` |
| E5 duplicate event id (E1 re-upload) | `event_processed new_event=false`; **no second incident, no second publish, no second attempt row** |
| DLQ behavior | depth reached 3 (TestEvent + E3 + E4) → **dlq-depth alarm entered ALARM correctly** → purged after evidence (now 0); redrive policy `maxReceiveCount=5` confirmed live |
| Idempotency totals | SQL: `events=2, incidents=1, attempts=1, link_failures=0` |
| Reconciliation | live passes: `reconcile_pass {republished:0, pending_alarm:0, exhausted:0}` — advisory lock (sp_getapplock) acquired, pending/exhausted queries run, custom CW metrics `IncidentsPendingNotification`/`IncidentsNotifyExhausted` **live in CloudWatch**; the republish path is offline-tested (suite) — no live Class-B failure was induced (would require an SNS outage or IAM change, both out of scope) |
| Alarms | 5/5 present; dlq-depth ALARM→(purge)→OK expected; queue-oldest-age OK (0 s); sns-publish-failures OK; reconcile alarms OK |
| Streamlit visibility | UI identity queries return exactly the page content: incidents=1, notified=1, sns_acked=1, events=2, link_fails=0, severity bucket=medium |
| Grafana | admin API 200; incident dashboard **8 panels** (3 new Phase 4); VW_INCIDENT_NOTIFICATIONS panel query 200; SQL DS health OK; **CW datasource: "Successfully queried the CloudWatch metrics API"**; real SQS DLQ metric frame returned (status 200) |
| Dashboard D | CloudWatch metrics access live (the deferred IAM applied); panels are metrics/billing only — the DS health's logs-probe AccessDenied is expected (no logs read granted, by design) |
| Phase 3 regression | CF: `/` 403 fail-closed, `/chat/` 200, `/grafana/` 302→login, `sign_out` 302→Cognito; all containers healthy; ColdChat/LibreChat/Grafana/streamlit serving; zero TF drift |

## 5. Automated tests

**195/195 passed** post-deployment (local), including the 19 Phase 4 tests.
Terraform: fmt clean, validate Success, apply exactly as planned, zero drift.

## 6. Cost impact

~$1–2/month standing (5 alarms ≈ $0.50; 2 custom metrics ≈ $0.60; queue/
topic/bucket pennies at demo volume). $10 window alarm intact; $50 approval
condition respected.

## 7. Operator visual acceptance (remaining)

Everything server-side is verified; the final Phase 4 visual pass is
operator-side, exactly as Phase 3 did it:
1. Login to the dashboard → **Floci Incidents** page: expect 1 incident
   (UC-003241, 6.2 °C, synthetic, severity medium, SNS-acked), queue-health
   tiles, event history, link-validation 0.
2. Grafana → **Telemetry incidents** dashboard: notification-state panels
   (8 panels total).
3. Grafana → **Infrastructure and AI Monitoring** (Dashboard D): live
   EC2/RDS/ALB/billing charts — now real with the applied IAM.
4. LibreChat login (secrets rotated during the rollout — fresh login
   required) if you want a Phase-4-adjacent sanity conversation.

## 8. Known limitations (stated honestly)

- At-least-once end-to-end (R5-2): the ack-to-insert crash window can still
  duplicate a SNS notification; database-only reconciliation cannot close it.
- SNS Standard has no email subscription yet (recipient unconfirmed —
  operator gate; `sns_acked` means topic acceptance, never delivery).
- Worker/reconcile container logs go to Docker's json-file driver, not the
  CW log group (awslogs driver = future enhancement; alarms/metrics cover
  queue/processing state meanwhile).
- The 4.0 °C threshold is the fresh-perishables SOP example, not a
  cargo-specific policy.
