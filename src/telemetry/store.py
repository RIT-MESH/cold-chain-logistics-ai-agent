"""Durable idempotent incident projection into SQL Server. Explicit opt-in only."""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL


def make_engine():
    password = os.environ.get("SQL_INCIDENT_WRITER_PASSWORD")
    if not password:
        raise RuntimeError("SQL_INCIDENT_WRITER_PASSWORD must be configured")
    url = URL.create("mssql+pyodbc", username=os.getenv("SQL_INCIDENT_WRITER_USER", "USR_INCIDENT_WRITER"), password=password,
        host=os.getenv("SQL_SERVER_HOST", "localhost"), port=int(os.getenv("SQL_SERVER_PORT", "1433")),
        database=os.getenv("SQL_DATABASE", "ColdChainLogistics"), query={"driver": "ODBC Driver 18 for SQL Server",
        "Encrypt": os.getenv("SQL_ENCRYPT", "yes"), "TrustServerCertificate": os.getenv("SQL_TRUST_SERVER_CERTIFICATE", "no")})
    return create_engine(url, pool_pre_ping=True, connect_args={"timeout": 10})


def save_event(engine, event, incident):
    """Single transaction: insert event once, and incident when breached. Returns True if event was new."""
    from src.telemetry.events import validate
    validate(event)
    with engine.begin() as conn:
        result = conn.execute(text("""
            INSERT INTO dbo.TelemetryEvents (EventId, ShipmentId, ObservedAtUtc, TemperatureC, Provenance)
            OUTPUT INSERTED.EventId
            SELECT :eid, :sid, :observed, :temp, :source
            WHERE NOT EXISTS (SELECT 1 FROM dbo.TelemetryEvents WITH (UPDLOCK, HOLDLOCK) WHERE EventId=:eid)
        """), {"eid": event["event_id"], "sid": event["shipment_id"], "observed": event["timestamp_utc"],
               "temp": event["temperature_c"], "source": event["source"]}).first()
        if result is None:
            return False
        if incident:
            conn.execute(text("""
                INSERT INTO dbo.TelemetryIncidents (IncidentId, EventId, ShipmentId, ObservedAtUtc, TemperatureC, ThresholdC, Provenance, Status)
                VALUES (:iid,:eid,:sid,:observed,:temp,:threshold,:source,'OPEN')
            """), {"iid": incident["incident_id"], "eid": event["event_id"], "sid": event["shipment_id"],
                   "observed": event["timestamp_utc"], "temp": event["temperature_c"],
                   "threshold": incident["threshold_c"], "source": event["source"]})
        return True


def check_shipment_exists(engine, shipment_id):
    """C4 linkage check (blueprint 03 §B1): the worker confirms the unified
    shipment exists BEFORE persisting. Uses the column-level grant on
    unified.shipments (shipment_id only). Returns True when the shipment exists."""
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1 FROM unified.shipments WHERE shipment_id = :sid"),
                            {"sid": shipment_id}).first() is not None


def record_attempt(engine, incident_id, result, *, error_code=None, sns_message_id=None):
    """Append-only SNS attempt ledger row (R3-2). One INSERT per publish
    outcome; nothing is ever mutated. Derived state lives in
    FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS."""
    if result not in ("published_acked", "publish_failed"):
        raise ValueError("result must be published_acked or publish_failed")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO dbo.IncidentNotificationAttempts
                   (IncidentId, AttemptResult, ErrorCode, SnsMessageId)
            VALUES (:iid, :result, :err, :mid)
        """), {"iid": incident_id, "result": result, "err": error_code, "mid": sns_message_id})


def pending_notifications(engine, *, base_interval_minutes=15, max_attempts=5, window_hours=24):
    """Reconcile query (blueprint 03 §B5): incidents with NO published_acked
    attempt, attempts below NOTIFY_MAX_ATTEMPTS, inside the attempt window,
    and past the exponential-backoff eligibility point
    (last attempt + base_interval x 2^(attempts-1), capped effect by
    max_attempts). Read-only SELECT."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT i.IncidentId, i.EventId, i.ShipmentId, i.ObservedAtUtc, i.TemperatureC,
                   i.ThresholdC, i.Provenance,
                   COUNT(a.AttemptId) AS AttemptCount,
                   MIN(a.AttemptedAtUtc) AS FirstAttemptAtUtc,
                   MAX(a.AttemptedAtUtc) AS LastAttemptAtUtc
            FROM dbo.TelemetryIncidents i
            LEFT JOIN dbo.IncidentNotificationAttempts a ON a.IncidentId = i.IncidentId
            GROUP BY i.IncidentId, i.EventId, i.ShipmentId, i.ObservedAtUtc, i.TemperatureC,
                     i.ThresholdC, i.Provenance, i.CreatedAtUtc
            HAVING SUM(CASE WHEN a.AttemptResult = 'published_acked' THEN 1 ELSE 0 END) = 0
               AND COUNT(a.AttemptId) < :max_attempts
               AND DATEDIFF(MINUTE, COALESCE(MAX(a.AttemptedAtUtc), i.CreatedAtUtc), SYSUTCDATETIME())
                   >= :base_interval * POWER(2, CASE WHEN COUNT(a.AttemptId) = 0 THEN 0
                                                     ELSE COUNT(a.AttemptId) - 1 END)
               AND (MIN(a.AttemptedAtUtc) IS NULL
                    OR DATEDIFF(HOUR, MIN(a.AttemptedAtUtc), SYSUTCDATETIME()) <= :window_hours)
        """), {"base_interval": base_interval_minutes, "max_attempts": max_attempts,
               "window_hours": window_hours})
        return [dict(r._mapping) for r in rows]


def alarm_pending_count(engine, *, pending_minutes=30):
    """Reconcile-pending-age alarm input (blueprint 03 §B3): count of
    incidents with NO published_acked attempt that have been unnotified for
    longer than pending_minutes, regardless of backoff eligibility."""
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT COUNT(*) FROM (
              SELECT i.IncidentId
              FROM dbo.TelemetryIncidents i
              LEFT JOIN dbo.IncidentNotificationAttempts a ON a.IncidentId = i.IncidentId
              GROUP BY i.IncidentId, i.CreatedAtUtc
              HAVING SUM(CASE WHEN a.AttemptResult = 'published_acked' THEN 1 ELSE 0 END) = 0
                 AND DATEDIFF(MINUTE, COALESCE(MIN(a.AttemptedAtUtc), i.CreatedAtUtc), SYSUTCDATETIME()) >= :pending_minutes
            ) x
        """), {"pending_minutes": pending_minutes}).scalar()


def exhausted_notifications(engine, *, max_attempts=5, window_hours=24):
    """Count of incidents that exhausted NOTIFY_MAX_ATTEMPTS without an ack —
    the reconcile-exhausted alarm input (bounded, fail-closed silence)."""
    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT COUNT(*) FROM (
              SELECT i.IncidentId
              FROM dbo.TelemetryIncidents i
              JOIN dbo.IncidentNotificationAttempts a ON a.IncidentId = i.IncidentId
              GROUP BY i.IncidentId
              HAVING SUM(CASE WHEN a.AttemptResult = 'published_acked' THEN 1 ELSE 0 END) = 0
                 AND COUNT(a.AttemptId) >= :max_attempts
                 AND MIN(a.AttemptedAtUtc) IS NOT NULL
                 AND DATEDIFF(HOUR, MIN(a.AttemptedAtUtc), SYSUTCDATETIME()) <= :window_hours
            ) x
        """), {"max_attempts": max_attempts, "window_hours": window_hours}).scalar()


def acquire_reconcile_lock(conn):
    """SQL advisory lock (blueprint 03 §B5 §2): single-scheduler defense in
    depth. MUST be called INSIDE the caller's transaction (LockOwner
    'Transaction'); returns True when the lock was acquired. 0 s timeout — a
    second reconciler exits immediately instead of waiting."""
    result = conn.execute(text("""
        DECLARE @r int;
        EXEC @r = sp_getapplock @Resource = 'coldchain_notify_reconcile',
                                @LockMode = 'Exclusive',
                                @LockOwner = 'Transaction',
                                @LockTimeout = 0;
        SELECT @r;
    """)).scalar()
    return result is not None and int(result) >= 0
