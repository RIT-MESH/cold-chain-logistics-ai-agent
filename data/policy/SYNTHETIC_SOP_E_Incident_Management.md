# Southern California Logistics Operations — SYNTHETIC DEMONSTRATION SOP
## Standard Operating Procedure (SYNTHETIC): Incident Management
**Version:** 1.0-demo | **Effective Date:** Jan 2021 (demo context) | **Status:** SYNTHETIC TEST DOCUMENT — NOT AN APPROVED OPERATIONAL POLICY | **Category:** E. Incident Management | **Source:** synthetic-demo | **Access:** internal-demo

### E1. Incident severity assessment
* Severity is assessed from the measured deviation against the shipment's configured limits; report it as a derived magnitude, not a stored label.
* Incidents affecting High-Risk shipments or remaining unresolved after 1 hour are high severity.

### E2. Notification procedures
* An incident notification is issued for every qualifying incident through the configured notification channel.
* A notification acknowledgement confirms that the notification CHANNEL accepted the message — it is NOT confirmation of delivery to any recipient. Recipient delivery is verified out-of-band.
* No incident is considered communicated until the acknowledgement evidence is recorded.

### E3. Escalation workflows
* Unacknowledged notifications are re-attempted on a bounded, observable schedule; repeated failure raises an operational alarm for manual intervention.
* Escalation contacts are role-based (duty supervisor, Tier 2 Logistics Manager) — never a personal contact channel.

### E4. Evidence retention
* Every incident retains its source evidence: the originating event record, the measured values, and the notification attempt records.
* Evidence records are append-only — corrections are recorded as new entries, never by altering history.

### E5. Corrective and preventive actions
* Each incident records the corrective action taken and the preventive follow-up, with the responsible role and timestamps.
* Periodic review of recurring incidents is a documented management task; the AI assistant may suggest, never approve or execute, actions.
