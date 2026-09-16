# Customer API Integration Guide

The Meridian Freight Cooperative API lets customers create shipments, print labels and receive tracking events from their own systems.

## Base URL and versions

All requests use the base URL `https://api.meridianfreight.example/v2`. Version 1 was retired in March and now returns HTTP 410. Breaking changes are announced at least 6 months before a version is retired.

## Authentication

The API uses OAuth 2.0 client credentials. Customers exchange their client ID and secret for an access token that is valid for 60 minutes. Tokens are sent in the `Authorization: Bearer` header. Secrets are rotated from the developer portal and the previous secret keeps working for 24 hours after rotation.

## Rate limits

Each customer account is limited to 600 requests per minute. Requests above the limit receive HTTP 429 with a `Retry-After` header. Bulk shipment creation should use the batch endpoint, which accepts up to 500 shipments per call.

## Webhooks

Tracking events are pushed to a customer-registered HTTPS endpoint. Each delivery is signed with an HMAC-SHA256 signature in the `X-Meridian-Signature` header. If the endpoint does not return HTTP 2xx, delivery is retried 5 times with exponential backoff starting at 1 minute. After the fifth failure the event is parked and visible in the developer portal for 7 days.

## Sandbox

A sandbox environment at `https://sandbox.api.meridianfreight.example/v2` mirrors production behaviour with fake carriers and no charges.
