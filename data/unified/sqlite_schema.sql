CREATE TABLE [vehicles] (
  [vehicle_id] TEXT PRIMARY KEY,
  [vehicle_type] TEXT,
  [capacity_pallets] TEXT,
  [fuel_type] TEXT,
  [fleet_key] TEXT
);

CREATE TABLE [shipments] (
  [shipment_id] TEXT PRIMARY KEY,
  [original_reference] TEXT,
  [cargo_type] TEXT,
  [pallets] INTEGER,
  [vehicle_id] TEXT,
  [departed_at] TEXT,
  [expected_arrival_at] TEXT,
  [arrived_at] TEXT,
  [receiver_verdict] TEXT,
  [source_category] TEXT,
  [synthetic_test_data] INTEGER,
  [origin_lat] REAL,
  [origin_lon] REAL,
  [destination_lat] REAL,
  [destination_lon] REAL,
  [temperature_min_c] REAL,
  [temperature_max_c] REAL,
  FOREIGN KEY ([vehicle_id]) REFERENCES [vehicles]([vehicle_id])
);

CREATE TABLE [sensors] (
  [sensor_id] TEXT PRIMARY KEY,
  [shipment_id] TEXT,
  [serial_no] TEXT,
  [position] TEXT,
  [installed_at] TEXT,
  [device_type] TEXT,
  [source_category] TEXT,
  FOREIGN KEY ([shipment_id]) REFERENCES [shipments]([shipment_id])
);

CREATE TABLE [telemetry] (
  [telemetry_id] INTEGER PRIMARY KEY,
  [shipment_id] TEXT,
  [sensor_id] TEXT,
  [vehicle_id] TEXT,
  [recorded_at] TEXT,
  [latitude] REAL,
  [longitude] REAL,
  [speed_kmh] REAL,
  [temperature_c] REAL,
  [humidity_pct] REAL,
  [traffic_congestion] REAL,
  [fuel_consumption_l_per_100km] REAL,
  [estimated_fuel_used_l] REAL,
  [fuel_level_pct] REAL,
  [risk_level] TEXT,
  [source_category] TEXT,
  [synthetic_test_data] INTEGER,
  FOREIGN KEY ([shipment_id]) REFERENCES [shipments]([shipment_id]),
  FOREIGN KEY ([sensor_id]) REFERENCES [sensors]([sensor_id]),
  FOREIGN KEY ([vehicle_id]) REFERENCES [vehicles]([vehicle_id])
);

CREATE TABLE [custody_handoffs] (
  [handoff_id] INTEGER PRIMARY KEY,
  [shipment_id] TEXT,
  [handoff_no] INTEGER,
  [received_by] TEXT,
  [handed_at] TEXT,
  [seal_intact] INTEGER,
  [source_category] TEXT,
  FOREIGN KEY ([shipment_id]) REFERENCES [shipments]([shipment_id])
);

CREATE TABLE [alerts] (
  [alert_id] INTEGER PRIMARY KEY,
  [shipment_id] TEXT,
  [telemetry_id] INTEGER,
  [alert_at] TEXT,
  [alert_type] TEXT,
  [severity] TEXT,
  [message] TEXT,
  [source_category] TEXT,
  FOREIGN KEY ([shipment_id]) REFERENCES [shipments]([shipment_id])
);

CREATE TABLE [route_events] (
  [route_event_id] INTEGER PRIMARY KEY,
  [shipment_id] TEXT,
  [telemetry_id] INTEGER,
  [route_sequence] INTEGER,
  [recorded_at] TEXT,
  [latitude] REAL,
  [longitude] REAL,
  [traffic_congestion] REAL,
  [weather_severity] REAL,
  FOREIGN KEY ([shipment_id]) REFERENCES [shipments]([shipment_id]),
  FOREIGN KEY ([telemetry_id]) REFERENCES [telemetry]([telemetry_id])
);

CREATE INDEX IX_sensors_shipment ON [sensors]([shipment_id]);

CREATE INDEX IX_telemetry_shipment ON [telemetry]([shipment_id]);

CREATE INDEX IX_custody_handoffs_shipment ON [custody_handoffs]([shipment_id]);

CREATE INDEX IX_alerts_shipment ON [alerts]([shipment_id]);

CREATE INDEX IX_route_events_shipment ON [route_events]([shipment_id]);
