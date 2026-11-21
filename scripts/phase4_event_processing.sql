-- Phase 4 event-processing migration — APPEND-ONLY (blueprint 03 §B3, R3-2).
-- Apply as DBA to SQL_DATABASE (ColdChainLogistics). Additive only: no
-- UPDATE/DELETE grants anywhere; existing Phase 0-3 objects untouched.
--
-- Statement of record pattern: after execution, copy the executed statements
-- into scripts/phase4_executed_grants.sql (see phase2_executed_grants.sql).
--
-- Contents:
--   1. dbo.IncidentNotificationAttempts — append-only SNS attempt ledger.
--      Fixes the Class B failure (post-commit publish loss): the worker
--      INSERTs one row per publish outcome and never mutates anything.
--      "Notified" is DERIVED (>=1 published_acked row), never a status
--      column to update (blueprint 03 §B3/B5).
--   2. FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS — derived per-incident
--      notification state (Streamlit Floci page + Grafana).
--   3. FDE_VIEWS.VW_EVENT_LINK_VALIDATION — C4 linkage reporting: events
--      whose ShipmentId has no matching unified shipment.
--   4. Grants: INSERT-only attempt ledger to USR_INCIDENT_WRITER;
--      column-level SELECT (shipment_id only) on unified.shipments to
--      USR_INCIDENT_WRITER for the ingest-time linkage existence check —
--      the narrowest possible read on THE related base table; RO identities
--      need no new grants (schema-level FDE_VIEWS grants from Phase 2 cover
--      every view created in that schema).

IF OBJECT_ID('dbo.IncidentNotificationAttempts','U') IS NULL
BEGIN
 CREATE TABLE dbo.IncidentNotificationAttempts (
   AttemptId int IDENTITY(1,1) NOT NULL PRIMARY KEY,
   IncidentId nvarchar(133) NOT NULL REFERENCES dbo.TelemetryIncidents(IncidentId),
   AttemptedAtUtc datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
   AttemptResult varchar(24) NOT NULL CHECK (AttemptResult IN ('published_acked','publish_failed')),
   ErrorCode varchar(64) NULL,      -- short class only (sns_throttled, ...); never raw payloads
   SnsMessageId varchar(128) NULL    -- topic-acceptance proof (NOT recipient delivery; B4)
 );
END;
GO
CREATE INDEX IX_IncidentNotificationAttempts_IncidentId
  ON dbo.IncidentNotificationAttempts (IncidentId);
GO

-- Derived notification state per incident (read side; no mutable status).
CREATE OR ALTER VIEW FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS AS
SELECT i.IncidentId,
       i.EventId,
       i.ShipmentId,
       i.ObservedAtUtc,
       i.TemperatureC,
       i.ThresholdC,
       i.Provenance,
       i.Status,
       i.CreatedAtUtc,
       COUNT(a.AttemptId)                                                  AS AttemptCount,
       COALESCE(SUM(CASE WHEN a.AttemptResult = 'published_acked' THEN 1 ELSE 0 END), 0) AS AckedCount,
       MIN(a.AttemptedAtUtc)                                               AS FirstAttemptAtUtc,
       MAX(a.AttemptedAtUtc)                                               AS LastAttemptAtUtc,
       CASE WHEN EXISTS (SELECT 1 FROM dbo.IncidentNotificationAttempts a2
                         WHERE a2.IncidentId = i.IncidentId
                           AND a2.AttemptResult = 'published_acked')
            THEN 'sns_acked' ELSE 'pending' END                            AS NotificationState
FROM dbo.TelemetryIncidents i
LEFT JOIN dbo.IncidentNotificationAttempts a ON a.IncidentId = i.IncidentId
GROUP BY i.IncidentId, i.EventId, i.ShipmentId, i.ObservedAtUtc, i.TemperatureC,
         i.ThresholdC, i.Provenance, i.Status, i.CreatedAtUtc;
GO

-- C4 linkage validation reporting: any persisted event whose ShipmentId has
-- no unified shipment row (should stay empty — the worker rejects bad refs
-- into the DLQ at ingest time; this view is the audit backstop).
CREATE OR ALTER VIEW FDE_VIEWS.VW_EVENT_LINK_VALIDATION AS
SELECT e.EventId,
       e.ShipmentId,
       e.ObservedAtUtc,
       e.Provenance,
       e.IngestedAtUtc
FROM dbo.TelemetryEvents e
LEFT JOIN unified.shipments s ON s.shipment_id = e.ShipmentId
WHERE s.shipment_id IS NULL;
GO

-- Writer grants (least privilege, append-only posture preserved).
-- Column-level shipment grant: the worker may only read shipment_id for its
-- ingest-time existence check — no access to any other shipments column.
GRANT SELECT, INSERT ON dbo.IncidentNotificationAttempts TO USR_INCIDENT_WRITER;
GRANT SELECT ON unified.shipments (shipment_id) TO USR_INCIDENT_WRITER;
GO

-- Read-only identities: NO new grants required — the Phase 2 schema-level
-- grants (GRANT SELECT ON SCHEMA::FDE_VIEWS TO USR_FDE_RO / USR_COLDCHAIN_UI_RO
-- / USR_GRAFANA_RO, scripts/phase2_executed_grants.sql:18-20) automatically
-- cover VW_INCIDENT_NOTIFICATIONS and VW_EVENT_LINK_VALIDATION.
--
-- Deliberately NOT granted anywhere: UPDATE, DELETE, ALTER, CONTROL.
-- The worker remains INSERT-only (guidelines / ADR-7 / R3-2).
