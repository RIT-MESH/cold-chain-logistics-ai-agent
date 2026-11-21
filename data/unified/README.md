# Unified Cold-Chain Logistics Dataset (2021–October 2026)

**SYNTHETIC TEST DATA. NOT REAL SHIPMENT OPERATIONS.** This unified database replaces the need to load legacy datasets separately for development. All entities use consistent `shipment_id` references. Source data from separate businesses/regions is NOT asserted to be originally part of one fleet.

## Contents

- `unified_cold_chain.sqlite`: ready-to-query SQLite database with foreign keys.
- Seven CSV tables (`shipments`, `vehicles`, `sensors`, `telemetry`, `route_events`, `custody_handoffs`, `alerts`).
- `sql_server_schema.sql`: SQL Server schema under `unified`, with views for shipment summary and sensor metrics.
- `import_sql_server.py`: imports the CSV tables into an EMPTY SQL Server database (`pip install pyodbc`; set `SQLSERVER_CONNECTION_STRING`).
- `DATASET_MANIFEST.json`: counts, date coverage, input records.

## Design notes and limitations

- Preserved from uploaded 2026 source: original shipment references, cargo, pallet counts, departure times, delivery verdicts, logger serials/positions, temperature and humidity measurements, custody handoffs, timestamps and seal states. GPS, traffic, vehicle assignment and fuel are modeled for these shipments.
- Preserved or adapted from 2025 exports: order references, order timestamps, basic delivery status, pickup locations and GPS monitoring points where linkable through original order IDs. Cold-chain cargo/sensors/fuel values are simulated. No personal contact details were copied into unified outputs.
- 2021–2024 fleet source: its timestamped fuel, traffic and GPS observations are ingested and grouped into 12-observation **synthetic shipments**. These GPS observations are not guaranteed to form a physically possible continuous route and should not be treated as confirmed trajectories. Source `iot_temperature` is transformed into cargo-relative synthetic Celsius using `(min+max)/2 + clamp((raw-15)/8, -3, 3)`, not copied as an actual sensor temperature; humidity and custody are simulated.
- 2024 IoT sample contains only 10 uploaded records (not a 120-row full dataset); these are enriched separately. Uploaded order and monitor CSVs are from 2025.
- Synthetic gap-fill 2025–2026 creates additional shipments and consistent telematics with fixed seed 53026. Country/region mixing is intentional in the *test* environment. Simulated GPS interpolation is not road-network routing, and simulated fuel estimates are not measured consumption.
- `temperature_min_c`/`temperature_max_c` are demonstration thresholds by cargo category, not regulatory or product-specific standards.
- `source_category` identifies which data was adapted, and `synthetic_test_data=1` labels the generated unified records. Reproduction requires the user-uploaded source files and the supplied generation script.

## SQL Server usage

1. Create a **new empty database** using SSMS or sqlcmd. Never run the schema against your production database.
2. Set `SQLSERVER_CONNECTION_STRING` to an ODBC Driver 18 SQL Server connection string for the empty database.
3. Run `python import_sql_server.py` from this directory. The script does not drop tables or overwrite an existing database.
4. Query `SELECT TOP 20 * FROM unified.v_shipment_summary ORDER BY departed_at DESC;`.
5. For Grafana use `unified.v_sensor_metrics` and its original `recorded_at` dates. For other tools join on `shipment_id`; do not mix with a legacy database.

## QA

SQLite foreign-key validation passed, 2021–2026 yearly coverage checked, non-null sensor temperature and humidity/GPS checked, and all row counts recorded in the manifest. SQL Server was not available here so its import has not been integration-tested against your instance.
