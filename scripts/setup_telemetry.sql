-- Apply once to the selected SQL_DATABASE (defaults to ColdChainLogistics for this project).
-- Execute as DBA, then create separate SQL logins/users with strong generated passwords.
-- Strictly segregate read-only Grafana/agent and writer permissions.
IF OBJECT_ID('dbo.TelemetryEvents','U') IS NULL
BEGIN
 CREATE TABLE dbo.TelemetryEvents (
   EventId nvarchar(128) NOT NULL PRIMARY KEY,
   ShipmentId nvarchar(128) NOT NULL,
   ObservedAtUtc datetimeoffset(0) NOT NULL,
   TemperatureC float NOT NULL,
   Provenance varchar(16) NOT NULL CHECK (Provenance IN ('synthetic','real')),
   IngestedAtUtc datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME()
 );
END;
GO
IF OBJECT_ID('dbo.TelemetryIncidents','U') IS NULL
BEGIN
 CREATE TABLE dbo.TelemetryIncidents (
   IncidentId nvarchar(133) NOT NULL PRIMARY KEY,
   EventId nvarchar(128) NOT NULL UNIQUE REFERENCES dbo.TelemetryEvents(EventId),
   ShipmentId nvarchar(128) NOT NULL,
   ObservedAtUtc datetimeoffset(0) NOT NULL,
   TemperatureC float NOT NULL,
   ThresholdC float NOT NULL,
   Provenance varchar(16) NOT NULL CHECK (Provenance IN ('synthetic','real')),
   Status varchar(24) NOT NULL DEFAULT 'OPEN',
   CreatedAtUtc datetime2(0) NOT NULL DEFAULT SYSUTCDATETIME()
 );
END;
GO
IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name='FDE_VIEWS') EXEC('CREATE SCHEMA FDE_VIEWS');
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_TELEMETRY_EVENTS AS
SELECT EventId,ShipmentId,CAST(SWITCHOFFSET(ObservedAtUtc,'+00:00') AS datetime2) AS ObservedAtUtc,
       TemperatureC,Provenance,IngestedAtUtc FROM dbo.TelemetryEvents;
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_TELEMETRY_INCIDENTS AS
SELECT IncidentId,EventId,ShipmentId,CAST(SWITCHOFFSET(ObservedAtUtc,'+00:00') AS datetime2) AS ObservedAtUtc,
       TemperatureC,ThresholdC,Provenance,Status,CreatedAtUtc FROM dbo.TelemetryIncidents;
GO
-- After securely provisioning these separate database users, grant the minimum needed:
-- GRANT SELECT ON FDE_VIEWS.VW_TELEMETRY_EVENTS TO USR_FDE_RO;
-- GRANT SELECT ON FDE_VIEWS.VW_TELEMETRY_INCIDENTS TO USR_FDE_RO;
-- GRANT SELECT, INSERT ON dbo.TelemetryEvents TO USR_INCIDENT_WRITER;
-- GRANT SELECT, INSERT ON dbo.TelemetryIncidents TO USR_INCIDENT_WRITER;
-- Do not grant UPDATE or DELETE to the automated worker.
