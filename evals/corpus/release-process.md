# Software Release Process

## Deployment windows

Production deployments happen on Tuesdays and Thursdays between 14:00 and 16:00 Eastern. Deployments outside the window need approval from the platform lead and are reserved for urgent fixes.

## Change freeze

A change freeze applies during the last week of each quarter and from 15 December to 2 January, when shipment volumes peak. Only SEV1 fixes are deployed during a freeze.

## Canary releases

Every deployment starts as a canary that receives 10 percent of traffic for 30 minutes. The release proceeds to 100 percent only if error rate, latency and the shipment-scan success metric stay within their thresholds on the release dashboard.

## Rollback

Rollbacks use the Rewind tool, which redeploys the previous build in under 5 minutes. Any engineer on the release may trigger Rewind without further approval; the reason is recorded in the release ticket.

## Checklist

Before deployment the release owner confirms:

- Automated tests passed on the release branch.
- Database migrations are backward compatible with the previous build.
- The release notes are posted in #releases.
- The on-call primary is aware of the deployment.

## Feature flags

New customer-facing features ship behind feature flags and are enabled per customer account by product management, not by deployment.
