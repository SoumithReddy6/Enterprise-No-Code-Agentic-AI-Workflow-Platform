# Incident Severity Definitions and Postmortems

Consistent severity levels let dispatch, customer care and engineering react proportionally.

## Definitions

- **SEV1**: a customer-facing outage affecting more than 5 percent of active shipments, any data loss, or a security breach. Requires a Bridge Alpha call immediately and executive notification within 1 hour.
- **SEV2**: degraded service with a workaround, or an outage affecting a single hub. Handled by the on-call primary with hourly updates.
- **SEV3**: minor defects, cosmetic problems or issues affecting internal tools only. Tracked in the backlog.

## Declaring severity

Anyone may declare an incident. The on-call primary confirms or adjusts the severity within 15 minutes of declaration. Severity may be lowered only by the incident commander.

## Postmortems

Every SEV1 and SEV2 incident requires a written postmortem published within 5 business days of resolution. Postmortems are blameless: they describe the timeline, contributing factors, what went well and follow-up actions with owners. Follow-up actions are tracked in the engineering backlog with the label `postmortem-action` and reviewed at the fortnightly operations review.

## Customer communication

Customer care sends the first customer notice within 30 minutes of a confirmed SEV1 using the approved template in the Care Handbook.
