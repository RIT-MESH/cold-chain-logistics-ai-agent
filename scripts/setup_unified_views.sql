-- Run after importing the unified synthetic dataset in the selected SQL_DATABASE.
IF NOT EXISTS(SELECT 1 FROM sys.schemas WHERE name='FDE_VIEWS') EXEC('CREATE SCHEMA FDE_VIEWS');
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_SHIPMENTS AS
SELECT s.shipment_id,s.original_reference,s.cargo_type,s.pallets,s.vehicle_id,TRY_CONVERT(datetime2,s.departed_at) AS departed_at,TRY_CONVERT(datetime2,s.expected_arrival_at) AS expected_arrival_at,TRY_CONVERT(datetime2,s.arrived_at) AS arrived_at,s.receiver_verdict,s.origin_lat,s.origin_lon,s.destination_lat,s.destination_lon,s.temperature_min_c,s.temperature_max_c,s.source_category,s.synthetic_test_data
FROM unified.shipments s;
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_TELEMETRY AS
SELECT t.telemetry_id,t.shipment_id,t.sensor_id,t.vehicle_id,TRY_CONVERT(datetime2,t.recorded_at) AS recorded_at,t.latitude,t.longitude,t.speed_kmh,t.temperature_c,t.humidity_pct,t.traffic_congestion,t.fuel_consumption_l_per_100km,t.estimated_fuel_used_l,t.fuel_level_pct,t.risk_level,t.source_category,t.synthetic_test_data
FROM unified.telemetry t;
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_CUSTODY AS SELECT handoff_id,shipment_id,handoff_no,received_by,TRY_CONVERT(datetime2,handed_at) AS handed_at,seal_intact,source_category FROM unified.custody_handoffs;
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_ALERTS AS SELECT alert_id,shipment_id,telemetry_id,TRY_CONVERT(datetime2,alert_at) AS alert_at,alert_type,severity,message,source_category FROM unified.alerts;
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_SENSORS AS SELECT sensor_id,shipment_id,serial_no,position,TRY_CONVERT(datetime2,installed_at) AS installed_at,device_type,source_category FROM unified.sensors;
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_VEHICLES AS SELECT vehicle_id,vehicle_type,capacity_pallets,fuel_type,fleet_key FROM unified.vehicles;
GO
CREATE OR ALTER VIEW FDE_VIEWS.VW_ROUTES AS SELECT route_event_id,shipment_id,telemetry_id,route_sequence,TRY_CONVERT(datetime2,recorded_at) AS recorded_at,latitude,longitude,traffic_congestion,weather_severity FROM unified.route_events;
GO
-- As database administrator, after explicitly creating USR_FDE_RO and a dedicated UI read-only user:
-- GRANT SELECT ON SCHEMA::FDE_VIEWS TO USR_FDE_RO;
-- GRANT SELECT ON SCHEMA::unified TO USR_COLDCHAIN_UI_RO;
-- Configure the Streamlit SQL read role separately from the agent identity.

