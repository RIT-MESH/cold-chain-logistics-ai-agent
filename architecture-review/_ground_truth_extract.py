"""Ground-truth extraction for the UC-003241 accuracy audit (Task 3):
what does the REAL synthetic dataset record? Free/offline."""
import json
import sqlite3

con = sqlite3.connect(r"data\unified\unified_cold_chain.sqlite")
con.row_factory = sqlite3.Row
cur = con.cursor()
sid = "UC-003241"

print("=== ALL 16 telemetry rows for UC-003241 (temp, time) ===")
cur.execute("SELECT recorded_at, temperature_c, humidity_pct, risk_level FROM telemetry "
            "WHERE shipment_id=? ORDER BY recorded_at", (sid,))
rows = [dict(r) for r in cur.fetchall()]
for r in rows:
    print(f"  {r['recorded_at']}  {r['temperature_c']} C  humidity {r['humidity_pct']}  risk {r['risk_level']}")

print("\n=== readings OUTSIDE 2.0-8.0 C (the shipment's configured band) ===")
cur.execute("SELECT recorded_at, temperature_c FROM telemetry WHERE shipment_id=? "
            "AND (temperature_c > 8.0 OR temperature_c < 2.0) ORDER BY recorded_at", (sid,))
out = [dict(r) for r in cur.fetchall()]
print(json.dumps(out, indent=1) if out else "  NONE")

print("\n=== ALL alerts for UC-003241 ===")
cur.execute("SELECT alert_at, alert_type, severity, message FROM alerts WHERE shipment_id=?", (sid,))
for r in cur.fetchall():
    print(" ", dict(r))

print("\n=== shipments row (thresholds, verdict, timing) ===")
cur.execute("SELECT cargo_type, temperature_min_c, temperature_max_c, receiver_verdict, "
            "departed_at, expected_arrival_at, arrived_at FROM shipments WHERE shipment_id=?", (sid,))
print(" ", dict(cur.fetchone()))
