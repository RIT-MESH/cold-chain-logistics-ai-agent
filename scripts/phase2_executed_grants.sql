-- Phase 2 EXECUTED grants (ADR-4: grants are EXECUTED at initial database
-- deployment — the equivalents inside setup_unified_views.sql /
-- setup_telemetry.sql are DOCUMENTATION and stay commented there; this file
-- is the executed statement of record).
--
-- Posture: FIVE application identities + temporary import admin (ADR-4).
-- View-only consumers get SELECT on the FDE_VIEWS schema ONLY — no
-- application identity EVER holds base-table grants on [unified].*
-- (guidelines §4, 05_SECURITY_MODEL §3). The incident writer is the one
-- documented exception: SELECT+INSERT on the two dbo incident tables
-- (ADR-7 — append-only attempt model, no UPDATE permission exists).
-- The audit writer (ADR-10) is created now; its INSERT grant on the
-- Phase 5 AgentAuditLog table is issued when that table is deployed.
--
-- Run inside the ColdChainLogistics database AFTER identities exist.

-- Agent (and every view consumer): views only.
GRANT SELECT ON SCHEMA::FDE_VIEWS TO USR_FDE_RO;
GRANT SELECT ON SCHEMA::FDE_VIEWS TO USR_COLDCHAIN_UI_RO;
GRANT SELECT ON SCHEMA::FDE_VIEWS TO USR_GRAFANA_RO;

-- Incident writer: append-only on the two incident tables (no UPDATE —
-- the append-only model is enforced by permission, ADR-7).
GRANT SELECT, INSERT ON dbo.TelemetryEvents TO USR_INCIDENT_WRITER;
GRANT SELECT, INSERT ON dbo.TelemetryIncidents TO USR_INCIDENT_WRITER;

-- USR_AUDIT_WRITER: intentionally NO grants yet (AgentAuditLog lands in
-- Phase 5; ADR-10). The identity exists so its grants can be issued at
-- that deployment without re-creating logins.

-- USR_IMPORT_ADMIN: temporary; granted db_owner for the import, then
-- DROPPED by the seeding step after import verification (ADR-4).
-- (Creation and drop are handled by the seeding script, not this file.)
