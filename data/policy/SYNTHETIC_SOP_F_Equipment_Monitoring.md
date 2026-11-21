# Southern California Logistics Operations — SYNTHETIC DEMONSTRATION SOP
## Standard Operating Procedure (SYNTHETIC): Equipment and Monitoring
**Version:** 1.0-demo | **Effective Date:** Jan 2021 (demo context) | **Status:** SYNTHETIC TEST DOCUMENT — NOT AN APPROVED OPERATIONAL POLICY | **Category:** F. Equipment and Monitoring | **Source:** synthetic-demo | **Access:** internal-demo

### F1. Sensor faults and logger disconnection
* A temperature logger that stops reporting is treated as a monitoring fault: contact the driver to inspect the device and record the outcome.
* Until the sensor is restored or replaced, adjacent-sensor coverage (if present) is noted on the shipment; the coverage gap is recorded with its start time.

### F2. Logger disconnection handling
1. Confirm the last received reading time from the telemetry record.
2. Request a physical device check through the driver.
3. If the logger cannot be restored within 2 hours, dispatch a replacement sensor and document the exchange.
4. Any temperature event occurring during a coverage gap is evaluated conservatively and flagged as unverified.

### F3. Missing telemetry
* A gap in the telemetry record is documented with its exact start and end times.
* Never interpolate or estimate missing readings — record the gap itself as the evidence.
* Coverage gaps longer than one scheduled sampling interval are reported on the shipment review.

### F4. Equipment failure
* Cooling-unit failure follows the corrective-action procedure in SOP A3 and is documented as an equipment failure record.
* Repeated failures of the same unit within 30 days trigger a maintenance review escalation.

### F5. Data-quality exceptions
* Readings outside the physically plausible range for the device are treated as data-quality exceptions: exclude them from excursion assessment, document the exclusion, and verify the sensor.
* Data-quality exclusions are recorded with the raw observed value and the exclusion reason.
