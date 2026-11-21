-- verify_grants.sql — the ADR-4 permission-verification query, executed at
-- initial database deployment (Phase 2) and re-run at every later phase
-- that touches identities. Output is archived as deployment evidence and
-- human-reviewed (guidelines: human-reviewed operational decisions).
--
-- EXPECTED (05_SECURITY_MODEL §3, ADR-4, ADR-7):
--   USR_FDE_RO         -> SELECT on SCHEMA::FDE_VIEWS only
--   USR_COLDCHAIN_UI_RO-> SELECT on SCHEMA::FDE_VIEWS only
--   USR_GRAFANA_RO     -> SELECT on SCHEMA::FDE_VIEWS only
--   USR_INCIDENT_WRITER-> SELECT+INSERT on dbo.TelemetryEvents,
--                         dbo.TelemetryIncidents only (append-only)
--   USR_AUDIT_WRITER   -> no grants yet (Phase 5)
--   NO application identity has ANY grant on [unified].*

USE ColdChainLogistics;

PRINT N'=== Identity permission inventory ===';
SELECT dp.class_desc, dp.permission_name, dp.state_desc,
       COALESCE(s.name, o.name, N'') AS grant_target
FROM sys.database_principals p
JOIN sys.database_permissions dp ON dp.grantee_principal_id = p.principal_id
LEFT JOIN sys.schemas s ON dp.class = 3 AND s.schema_id = dp.major_id
LEFT JOIN sys.objects o ON dp.class = 1 AND o.object_id = dp.major_id
WHERE p.name LIKE N'USR[_]%'
ORDER BY p.name, dp.permission_name;

PRINT N'=== FDE_VIEWS consumers (expected: 3 view-only identities) ===';
SELECT p.name AS identity_name, dp.permission_name
FROM sys.database_principals p
JOIN sys.database_permissions dp ON dp.grantee_principal_id = p.principal_id
JOIN sys.schemas s ON dp.class = 3 AND s.schema_id = dp.major_id
WHERE s.name = N'FDE_VIEWS'
ORDER BY p.name;

PRINT N'=== BASE-TABLE grants to application identities (expected: NONE on [unified].*) ===';
SELECT p.name AS identity_name, dp.permission_name, s.name AS schema_name
FROM sys.database_principals p
JOIN sys.database_permissions dp ON dp.grantee_principal_id = p.principal_id
JOIN sys.schemas s ON dp.class = 3 AND s.schema_id = dp.major_id
WHERE p.name LIKE N'USR[_]%' AND s.name = N'unified';

PRINT N'=== Incident writer grants (expected: SELECT+INSERT on the 2 dbo tables only) ===';
SELECT p.name AS identity_name, dp.permission_name, o.name AS object_name
FROM sys.database_principals p
JOIN sys.database_permissions dp ON dp.grantee_principal_id = p.principal_id
JOIN sys.objects o ON dp.class = 1 AND o.object_id = dp.major_id
WHERE p.name = N'USR_INCIDENT_WRITER';

PRINT N'=== Schema/object inventory ===';
SELECT 'FDE_VIEWS views' AS kind, COUNT(*) AS n FROM sys.views v JOIN sys.schemas s ON v.schema_id = s.schema_id WHERE s.name = N'FDE_VIEWS'
UNION ALL
SELECT 'unified tables', COUNT(*) FROM sys.tables t JOIN sys.schemas s ON t.schema_id = s.schema_id WHERE s.name = N'unified';
