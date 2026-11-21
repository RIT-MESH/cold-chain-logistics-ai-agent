"""Validate telemetry events and classify demo threshold breaches."""
from datetime import datetime, timezone
import math


def validate(event):
    if not isinstance(event, dict):
        raise ValueError("Event must be a JSON object")
    required = {"event_id", "shipment_id", "timestamp_utc", "temperature_c", "source"}
    if not required.issubset(event):
        raise ValueError(f"Missing fields: {sorted(required - event.keys())}")
    if not all(isinstance(event[k], str) and 0 < len(event[k]) <= 128 for k in ("event_id", "shipment_id", "source")):
        raise ValueError("Invalid event identifiers")
    ts = event["timestamp_utc"]
    if not isinstance(ts, str):
        raise ValueError("timestamp_utc must be ISO8601 string")
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid ISO8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp_utc must include timezone")
    if not isinstance(event["temperature_c"], (int, float)) or isinstance(event["temperature_c"], bool) or not math.isfinite(event["temperature_c"]):
        raise ValueError("temperature_c must be a finite number")
    if not -80 <= event["temperature_c"] <= 80:
        raise ValueError("temperature_c outside allowed demo range")
    if event["source"] not in ("synthetic", "real"):
        raise ValueError("source must be synthetic or real")
    return event


def severity(exceedance):
    """Derived severity bucket from excursion magnitude (°C above threshold).

    Honest derived value — the schema stores ThresholdC, not a severity label;
    buckets are a presentation convention, not cargo-specific policy.
    """
    if exceedance <= 0:
        return None
    if exceedance <= 2:
        return "low"
    if exceedance <= 4:
        return "medium"
    return "high"


def classify(event, maximum=4.0):
    validate(event)
    if event["temperature_c"] <= maximum:
        return None
    return {"incident_id": f"temp:{event['event_id']}", "shipment_id": event["shipment_id"],
            "timestamp_utc": event["timestamp_utc"], "temperature_c": event["temperature_c"],
            "threshold_c": maximum, "source": event["source"], "status": "OPEN",
            "severity": severity(event["temperature_c"] - maximum),
            "recommendation": "Human review required; verify sensor and consult cargo-specific SOP."}
