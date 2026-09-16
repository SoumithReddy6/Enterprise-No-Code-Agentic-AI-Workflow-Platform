# Data Retention Schedule

This schedule defines how long Meridian Freight Cooperative keeps each class of data and where it is stored after its active life.

| Data class | Active retention | Archive | Deletion |
| --- | --- | --- | --- |
| Shipment and billing records | 7 years | Coldstore after 18 months | Deleted after 7 years |
| Vehicle telemetry (GPS, engine sensors) | 90 days | Not archived | Purged automatically at 90 days |
| Driver hours-of-service logs | 6 months online | Coldstore | Deleted after 3 years |
| Customer support transcripts | 2 years | Coldstore | Deleted after 2 years |
| Security camera footage | 45 days | Not archived | Overwritten at 45 days |
| Employee records | Employment plus 5 years | Coldstore | Deleted after 5 years |

## Customer deletion requests

When a customer asks for their personal information to be deleted, the request is logged in ClaimDesk and fulfilled within 30 days. Records that must be kept for legal, tax or safety reasons are pseudonymised instead of deleted, and the customer is told which records were retained and why.

## Legal hold

The Legal team may place any data under legal hold. Data under hold is excluded from automatic deletion until the hold is lifted in writing.

## Backups

Backups follow the same retention as the source system. Coldstore archives are encrypted and tested for restore twice a year.
