**Synthetic Test Data Notice**: All evidence below is from the 2021–Oct 2026 unified test environment. No real-time vehicle status, traffic, or fuel metrics are claimed.

---
### Incident Evidence (SQL: `FDE_VIEWS.VW_TELEMETRY_INCIDENTS`)
| Field | Value |
|------|-------|
| Shipment ID | UC-003241 |
| Incident ID | `temp:8df143db-78a9-41f9-aafe-c2ad0046906e` |
| Event ID | `temp:8df143db-78a9-41f9-aafe-c2ad0046906e` |
| Observed At UTC | 2026-11-17 02:17:01 |
| Measured Temperature | **6.2 °C** |
| Threshold Temperature | **4.0 °C** |
| Derived Excursion Magnitude | 2.2 °C above threshold (derived; not stored as a separate column) |
| Provenance | **synthetic** (replay/demo event) |
| Status | OPEN |
| Created At UTC | 2026-11-17 02:17:04 |
| Notification State | **sns_acked** (SNS topic accepted the publish attempt; does **not** confirm email delivery) |
| Attempt Count | 1 |
| Acknowledged Count | 1 |
| Last Attempt At UTC | 2026-11-17 02:17:05 |

*No matching record in `FDE_VIEWS.VW_INCIDENT_NOTIFICATIONS` beyond the above; no additional notification outcomes are available.*

---
### Applicable SOP (Corrective‑Action Procedure)
- **Document citation (verbatim)**:
  `[Standard Operating Procedure (SYNTHETIC): Temperature Management | version 1.0-demo | section A3 | source synthetic-demo | status synthetic-demo]`
  *This is a synthetic test document – not an approved operational policy.*

- **SOP section A3 – Corrective‑action procedure (demo guidance)**
  1. Verify the reading is genuine: confirm the sensor is operational and check adjacent readings.
  2. Contact the driver to restart/verify the auxiliary cooling unit.
  3. If the estimated delay to correction exceeds 1 hour, divert to the nearest cold‑storage facility.
  4. Record every action taken, with timestamps, in the shipment file.

---
### Recommended Corrective Action (derived from cited SOP A3)
1. **Verify sensor integrity** – confirm the temperature sensor for shipment UC-003241 is operational and review any adjacent telemetry readings for corroboration.
2. **Driver outreach** – contact the assigned driver to restart or verify the auxiliary cooling unit operation.
3. **Delay assessment** – if the estimated time to restore the temperature within the 4.0 °C threshold exceeds 1 hour, divert the shipment to the nearest cold‑storage facility.
4. **Documentation** – log every action (sensor check, driver contact, diversion decision, temperature re‑readings) with UTC timestamps in the shipment file.

*All steps are contingent on human review and real‑world verification. The SOP cited is synthetic demo material; operational procedures must align with your organization's approved cold‑chain policies.*

---
### Limitations / Labels
- **Synthetic content**: All incident data, SOP text, and timestamps are test‑environment replay/demo events (provenance `synthetic`).
- **Notification state**: `sns_acked` indicates SNS topic acceptance only; email delivery or recipient acknowledgment is not verified.
- **Excursion magnitude**: Derived as `TemperatureC - ThresholdC = 2.2 °C`; the database does not store a severity label.
- **Missing evidence**: No humidity, GPS, traffic, fuel, or handoff/seal status records are provided in the pre‑fetched evidence.
- **SOP applicability**: Retrieved SOP is a synthetic demo document; it is not an approved operational policy and should not be followed as real‑world guidance without independent validation.
- **No real‑time claims**: This response does not assert current vehicle location, traffic conditions, or measured fuel consumption.
