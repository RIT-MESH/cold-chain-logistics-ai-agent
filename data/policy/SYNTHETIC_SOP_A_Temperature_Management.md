# Southern California Logistics Operations — SYNTHETIC DEMONSTRATION SOP
## Standard Operating Procedure (SYNTHETIC): Temperature Management
**Version:** 1.0-demo | **Effective Date:** Jan 2021 (demo context) | **Status:** SYNTHETIC TEST DOCUMENT — NOT AN APPROVED OPERATIONAL POLICY | **Category:** A. Temperature Management | **Source:** synthetic-demo | **Access:** internal-demo

### A1. Approved temperature ranges
Cargo-specific configured ranges are authoritative. For the demonstration dataset's fresh-perishables profile the range is 0.0–4.0 °C (see Cold_Chain_Incident_SOP_v2 §1). Other cargo types follow the per-shipment configured range recorded with the shipment.
* Never treat any single range as universal — always read the shipment's configured limits first.

### A2. Temperature excursion identification
* An excursion is any reading outside the shipment's configured range.
* Classify severity by excursion magnitude (deviation beyond the limit), reported as a DERIVED value — the incident store does not record a severity label.
* Corroborate any alert with the underlying telemetry readings around the alert time before drawing conclusions.

### A3. Corrective-action procedure (demo guidance)
1. Verify the reading is genuine: confirm the sensor is operational and check adjacent readings (see F1).
2. Contact the driver to restart/verify the auxiliary cooling unit.
3. If the estimated delay to correction exceeds 1 hour, divert to the nearest cold-storage facility.
4. Record every action taken, with timestamps, in the shipment file.

### A4. Escalation requirements
* Any confirmed excursion on a High-Risk shipment: escalate to the Tier 2 Logistics Manager.
* Any excursion unresolved after 1 hour: escalate to the duty operations supervisor.

### A5. Sensor verification
* Before acting on a single out-of-range reading, confirm the sensor's calibration status and recent reporting continuity.
* A single anomalous reading from an otherwise-quiet sensor is treated as suspect until verified (see F1).

### A6. Documentation requirements
* Every excursion event, action taken, and escalation must be recorded with UTC timestamps and the responsible role.
* The incident record must reference the shipment, the measured value, and the configured limits.
