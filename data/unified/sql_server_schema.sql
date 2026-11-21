IF SCHEMA_ID(N'unified') IS NULL EXEC(N'CREATE SCHEMA unified');

CREATE TABLE [unified].[vehicles] (
  [vehicle_id] NVARCHAR(100) PRIMARY KEY,
  [vehicle_type] NVARCHAR(100),
  [capacity_pallets] NVARCHAR(100),
  [fuel_type] NVARCHAR(100),
  [fleet_key] NVARCHAR(100)
);

CREATE TABLE [unified].[shipments] (
  [shipment_id] NVARCHAR(100) PRIMARY KEY,
  [original_reference] NVARCHAR(100),
  [cargo_type] NVARCHAR(100),
  [pallets] INT,
  [vehicle_id] NVARCHAR(100),
  [departed_at] NVARCHAR(100),
  [expected_arrival_at] NVARCHAR(100),
  [arrived_at] NVARCHAR(100),
  [receiver_verdict] NVARCHAR(100),
  [source_category] NVARCHAR(100),
  [synthetic_test_data] INT,
  [origin_lat] FLOAT,
  [origin_lon] FLOAT,
  [destination_lat] FLOAT,
  [destination_lon] FLOAT,
  [temperature_min_c] FLOAT,
  [temperature_max_c] FLOAT,
  FOREIGN KEY ([vehicle_id]) REFERENCES [unified].[vehicles]([vehicle_id])
);

CREATE TABLE [unified].[sensors] (
  [sensor_id] NVARCHAR(100) PRIMARY KEY,
  [shipment_id] NVARCHAR(100),
  [serial_no] NVARCHAR(100),
  [position] NVARCHAR(100),
  [installed_at] NVARCHAR(100),
  [device_type] NVARCHAR(100),
  [source_category] NVARCHAR(100),
  FOREIGN KEY ([shipment_id]) REFERENCES [unified].[shipments]([shipment_id])
);

CREATE TABLE [unified].[telemetry] (
  [telemetry_id] INT PRIMARY KEY,
  [shipment_id] NVARCHAR(100),
  [sensor_id] NVARCHAR(100),
  [vehicle_id] NVARCHAR(100),
  [recorded_at] NVARCHAR(100),
  [latitude] FLOAT,
  [longitude] FLOAT,
  [speed_kmh] FLOAT,
  [temperature_c] FLOAT,
  [humidity_pct] FLOAT,
  [traffic_congestion] FLOAT,
  [fuel_consumption_l_per_100km] FLOAT,
  [estimated_fuel_used_l] FLOAT,
  [fuel_level_pct] FLOAT,
  [risk_level] NVARCHAR(100),
  [source_category] NVARCHAR(100),
  [synthetic_test_data] INT,
  FOREIGN KEY ([shipment_id]) REFERENCES [unified].[shipments]([shipment_id]),
  FOREIGN KEY ([sensor_id]) REFERENCES [unified].[sensors]([sensor_id]),
  FOREIGN KEY ([vehicle_id]) REFERENCES [unified].[vehicles]([vehicle_id])
);

CREATE TABLE [unified].[custody_handoffs] (
  [handoff_id] INT PRIMARY KEY,
  [shipment_id] NVARCHAR(100),
  [handoff_no] INT,
  [received_by] NVARCHAR(100),
  [handed_at] NVARCHAR(100),
  [seal_intact] INT,
  [source_category] NVARCHAR(100),
  FOREIGN KEY ([shipment_id]) REFERENCES [unified].[shipments]([shipment_id])
);

CREATE TABLE [unified].[alerts] (
  [alert_id] INT PRIMARY KEY,
  [shipment_id] NVARCHAR(100),
  [telemetry_id] INT,
  [alert_at] NVARCHAR(100),
  [alert_type] NVARCHAR(100),
  [severity] NVARCHAR(100),
  [message] NVARCHAR(512),
  [source_category] NVARCHAR(100),
  FOREIGN KEY ([shipment_id]) REFERENCES [unified].[shipments]([shipment_id])
);

CREATE TABLE [unified].[route_events] (
  [route_event_id] INT PRIMARY KEY,
  [shipment_id] NVARCHAR(100),
  [telemetry_id] INT,
  [route_sequence] INT,
  [recorded_at] NVARCHAR(100),
  [latitude] FLOAT,
  [longitude] FLOAT,
  [traffic_congestion] FLOAT,
  [weather_severity] FLOAT,
  FOREIGN KEY ([shipment_id]) REFERENCES [unified].[shipments]([shipment_id]),
  FOREIGN KEY ([telemetry_id]) REFERENCES [unified].[telemetry]([telemetry_id])
);

CREATE INDEX IX_sensors_shipment ON [unified].[sensors]([shipment_id]);

CREATE INDEX IX_telemetry_shipment ON [unified].[telemetry]([shipment_id]);

CREATE INDEX IX_custody_handoffs_shipment ON [unified].[custody_handoffs]([shipment_id]);

CREATE INDEX IX_alerts_shipment ON [unified].[alerts]([shipment_id]);

CREATE INDEX IX_route_events_shipment ON [unified].[route_events]([shipment_id]);

CREATE VIEW [unified].[v_shipment_summary] AS SELECT s.shipment_id,s.original_reference,s.cargo_type,s.vehicle_id,s.departed_at,s.arrived_at,s.receiver_verdict,s.source_category,COUNT(DISTINCT sn.sensor_id) AS logger_count,COUNT(DISTINCT t.telemetry_id) AS reading_count FROM [unified].[shipments] s LEFT JOIN [unified].[sensors] sn ON sn.shipment_id=s.shipment_id LEFT JOIN [unified].[telemetry] t ON t.shipment_id=s.shipment_id GROUP BY s.shipment_id,s.original_reference,s.cargo_type,s.vehicle_id,s.departed_at,s.arrived_at,s.receiver_verdict,s.source_category;

CREATE VIEW [unified].[v_sensor_metrics] AS SELECT t.shipment_id,t.sensor_id,t.recorded_at,t.temperature_c,t.humidity_pct,t.risk_level,t.source_category,s.cargo_type,s.temperature_min_c,s.temperature_max_c FROM [unified].[telemetry] t JOIN [unified].[shipments] s ON s.shipment_id=t.shipment_id;
