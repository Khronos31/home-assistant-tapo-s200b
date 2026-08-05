# Changelog

## Unreleased

- Deliver each child's persisted events without waiting for later child-log
  reads, and avoid polling disabled Event entities.
- Use a 10-record fast first page while preserving configured catch-up depth,
  and expose fetch, cursor-save, and delivery-lead timing in diagnostics.
- Match the icon and logo used by Tapo: Cameras Control, including 2x assets.
- Add official-style cloud connection, signal level, battery, RSSI, reboot,
  and unpair entities to supported child devices. All are diagnostic entities
  and disabled by default.
- Start the low-frequency diagnostic poller only after an operator enables at
  least one diagnostic or maintenance entity.

## 0.1.0

- Add UI configuration, reauthentication, and polling options for H110/H110C.
- Add S200B/S200D Event entities for single click, double click, and signed
  30-degree rotation steps.
- Add durable at-most-once cursors, event-ID deduplication, pagination, and
  event-storm safety bounds.
- Share the official TP-Link integration's python-kasa KLAP session when it
  manages the same hub; otherwise use a standalone plugp100 connection.
- Add redacted diagnostics, a read-only hardware probe, tests, HACS metadata,
  and validation workflows.
