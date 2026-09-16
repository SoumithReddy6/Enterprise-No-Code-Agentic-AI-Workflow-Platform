# Platform On-Call Runbook

The platform engineering team keeps the shipment tracking platform available around the clock. This runbook describes the on-call rotation and response expectations.

## Rotation

The rotation is weekly and hands over every Monday at 09:00 Eastern. Each week has a primary and a secondary engineer. Swaps are arranged in the Klaxon paging tool and must be confirmed by both engineers.

## Response times

The primary engineer acknowledges a page in Klaxon within 15 minutes. If the primary does not acknowledge within 15 minutes, Klaxon automatically pages the secondary. If an incident is not mitigated within 30 minutes, the primary escalates to the Tier 2 group, which includes the platform lead and the database administrator on duty.

## Severity

Pages carry a severity from SEV1 (most severe) to SEV3. SEV1 and SEV2 pages wake the primary at any hour. SEV3 pages are delivered during business hours only and are handled the next working morning if they arrive overnight.

## During an incident

- Acknowledge the page and post in the #incident channel.
- Open a Bridge Alpha call for SEV1 incidents.
- Keep the status page updated at least every 30 minutes.
- Record actions with timestamps for the postmortem.

## Compensation

On-call weeks are compensated with a flat stipend of $350 plus time off in lieu for any SEV1 handled outside business hours.
