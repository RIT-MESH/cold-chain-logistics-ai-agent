-- Phase 5 OPTIONAL durable audit store (ADR-10, USR_AUDIT_WRITER) —
-- PREPARED, NOT EXECUTED. Deployment is an operator approval item
-- (operator Phase 5 directive §11: database migrations require explicit
-- scope/impact confirmation first).
--
-- Impact if executed: 1 new append-only table + 1 view; INSERT-only grant
-- to the EXISTING USR_AUDIT_WRITER identity (login already exists from
-- Phase 2; sql-audit-writer secret container already exists and is
-- already fetched by fetch-secrets.sh); schema-level FDE_VIEWS grants
-- from Phase 2 automatically cover the view for RO identities. No
-- existing table, view, grant, or data is modified. Zero downtime.
--
-- Why optional: the PRIMARY audit path (Phase 5 as deployed) is the
-- structured JSON lines in the /coldchain/dev/gateway CloudWatch Logs
-- group (90-day retention, restricted write via the instance role).
-- This table adds a queryable, correlation-friendly audit trail INSIDE
-- SQL Server with the same append-only posture as the notification
-- ledger. CloudWatch alone remains the operational source of truth.

IF OBJECT_ID('dbo.AgentAuditLog','U') IS NULL
BEGIN
 CREATE TABLE dbo.AgentAuditLog (
   AuditId int IDENTITY(1,1) NOT NULL PRIMARY KEY,
   RecordedAtUtc datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME(),
   RequestId varchar(64) NOT NULL,          -- gateway correlation id
   Actor varchar(32) NOT NULL,               -- pseudonymous key hash, never raw keys
   Event varchar(32) NOT NULL,               -- agent_request_end | agent_tool_event | auth_failure
   Tool varchar(64) NULL,
   GuardDecision varchar(160) NULL,          -- short guard outcome, never raw SQL
   Views varchar(256) NULL,                  -- approved view names only
   Outcome varchar(32) NULL,                 -- executed | guard_rejected | error | success
   DurationMs int NULL,
   RowsReturned int NULL,
   ErrorClass varchar(64) NULL
 );
END;
GO
CREATE INDEX IX_AgentAuditLog_RequestId ON dbo.AgentAuditLog (RequestId);
CREATE INDEX IX_AgentAuditLog_RecordedAt ON dbo.AgentAuditLog (RecordedAtUtc);
GO
-- Read-side projection for authorized review (RO identities need no new
-- grants — the Phase 2 schema-level FDE_VIEWS grants cover this view).
CREATE OR ALTER VIEW FDE_VIEWS.VW_AGENT_AUDIT AS
SELECT AuditId, RecordedAtUtc, RequestId, Actor, Event, Tool, GuardDecision,
       Views, Outcome, DurationMs, RowsReturned, ErrorClass
FROM dbo.AgentAuditLog;
GO
-- Writer grant: INSERT-only. NO UPDATE/DELETE anywhere (append-only, ADR-10).
GRANT SELECT, INSERT ON dbo.AgentAuditLog TO USR_AUDIT_WRITER;
GO
-- Deliberately NOT granted: UPDATE, DELETE, ALTER, CONTROL. Retention
-- purges (ADR-10 90-day philosophy) would be a DBA-executed maintenance
-- job, not an application permission.
