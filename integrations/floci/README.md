# Floci local AWS development environment

**Scope:** optional S3 + SQS + SNS integration for simulated telemetry. Does not replace SQL Server, Grafana, LibreChat, Pinecone or real AWS. Lambda and EventBridge are not required. SQL incident persistence is available behind an explicit opt-in; this is not a production-grade event system.

## Quick start (from repository root)

1. Install Docker Compose and Python 3.11+. `pip install -r requirements-floci.txt`.
2. `docker compose -f docker-compose.floci.yml up -d floci`.
3. In a shell on your host set `AWS_ENDPOINT_URL=http://localhost:4566`, `AWS_REGION=us-east-1`, `AWS_ACCESS_KEY_ID=test`, `AWS_SECRET_ACCESS_KEY=test`. These are dummy **local emulator** credentials only.
4. `python -m scripts.bootstrap_floci`.
5. `python -m scripts.publish_sample_telemetry --temperature 6.2`.
6. `python -m src.telemetry.worker` from another terminal. Stop with Ctrl+C. With SQL persistence **off**, the worker logs incidents in `data/cache/floci_incidents.jsonl` and publishes to an emulator SNS topic. SNS has no human notification recipient unless a subscription is configured. A reading at 4.0°C or below creates no alert.
7. You can run the worker in Docker with `docker compose -f docker-compose.floci.yml --profile worker up -d --build telemetry-worker`. The Docker image includes ODBC Driver 18; the local journal is ephemeral, so use SQL persistence for durable demonstration records.

## Design notes

- Event fields: `event_id`, `shipment_id`, ISO8601 UTC `timestamp_utc`, `temperature_c`, `source` (`synthetic` or `real`). The sample publisher always uses `synthetic`.
- The demo worker uses the **4°C fresh-perishables SOP threshold** as an example, not a universal cargo-specific policy. Do not make real dispatch decisions from this pipeline.
- SQS uses at-least-once delivery. When SQL persistence is enabled, `EventId` is deduplicated transactionally and incidents are stored with stable IDs. Local JSONL fallback can contain duplicates. **Phase 4 (2026-11-17) closes the previously documented gaps for the AWS deployment**: a Terraform-managed SQS DLQ + redrive policy, worker-side DLQ routing for invalid payloads and bad shipment references (C4 linkage validation), and an append-only `dbo.IncidentNotificationAttempts` ledger with a scheduled reconcile step (`src/telemetry/reconcile.py`) so a post-commit SNS publish failure is no longer lost silently (blueprint 03 §B3/B5). Residual limitation stated honestly: SNS Standard is at-least-once and the ack-to-insert crash window can still duplicate a notification — database-only reconciliation cannot close that (R5-2). Cargo-specific policies and event schema versioning remain out of scope for the demo threshold classifier.
- The worker writes to separate `dbo.TelemetryEvents` and `dbo.TelemetryIncidents` tables only when explicitly enabled. Historical fleet records remain unchanged. Grafana queries their views separately; timestamps and provenance labels distinguish synthetic data from historical measurements.
- Floci is bound to `127.0.0.1` to avoid unnecessary network exposure. Avoid giving Floci Docker socket access unless you explicitly enable container-backed emulated services such as Lambda.
- Do not assume Floci emulation matches AWS production behavior; run AWS integration tests separately.

Official references: https://floci.io/floci/configuration/environment-variables/ and https://github.com/floci-io/floci

## SQL Server and Grafana integration (opt-in)

1. Provision a private SQL Server database. Execute `scripts/setup_telemetry.sql` using `sqlcmd` (with `GO` support) against the database selected by `SQL_DATABASE` (default `ColdChainLogistics` for this project). Do not run against a production database without a review and backup.
2. Create separate SQL login/database user `USR_INCIDENT_WRITER` with a strong random password. Grant **SELECT and INSERT only** on `dbo.TelemetryEvents` and `dbo.TelemetryIncidents` to that user. Do not reuse the administrator or read-only agent login. Ensure the read-only user `USR_FDE_RO` has SELECT on `FDE_VIEWS.VW_TELEMETRY_EVENTS` and `FDE_VIEWS.VW_TELEMETRY_INCIDENTS`. Do not expose port 1433 to the public internet.
3. Set `SQL_SERVER_HOST`, `SQL_SERVER_PORT`, `SQL_DATABASE`, `SQL_INCIDENT_WRITER_USER`, `SQL_INCIDENT_WRITER_PASSWORD`, `SQL_ENCRYPT=yes`, and `SQL_TRUST_SERVER_CERTIFICATE=no` with a valid SQL TLS certificate. Set `TELEMETRY_PERSIST_SQL=true`. For Docker worker access to a SQL instance on the host, use `SQL_SERVER_HOST=host.docker.internal` and ensure the server is reachable; container-to-container deployments should use the database service hostname.
4. Run the worker and send an example reading using `python -m scripts.publish_sample_telemetry --temperature 6.2` (with a host-local Floci endpoint set). Inspect `dbo.TelemetryEvents` and `dbo.TelemetryIncidents`. Re-send the *same event ID* through SQS to test idempotency.
5. Configure Grafana read-only SQL datasource using `docker-compose.observability.yml` and open dashboard **Telemetry incidents (synthetic vs real)** (`uid: cold-chain-incidents`). The existing historical fleet dashboard remains unchanged. Synthetic time-series values are *not* genuine sensor observations.

The worker does not automatically approve corrective actions. SNS is only an emulated topic, not an SMS/email delivery integration. Live AWS, actual SQL Server connections, iframe authentication, real alert delivery and cross-service integration still require verification.
