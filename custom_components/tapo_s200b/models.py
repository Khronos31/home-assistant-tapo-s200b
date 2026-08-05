"""Pure data models for Tapo trigger-log processing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class EventEmission:
    """One Home Assistant event-entity emission."""

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    """A validated Tapo trigger-log record and its HA emissions."""

    record_id: int
    event_id: str
    timestamp: int
    raw_event_type: str
    emissions: tuple[EventEmission, ...]
    ignored_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ChildCursor:
    """Persisted at-most-once cursor for one child device."""

    latest_record_id: int | None = None
    recent_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BatchPlan:
    """A cursor update that must be saved before emissions are published."""

    cursor: ChildCursor
    emissions: tuple[EventEmission, ...]
    processed_records: int
    duplicate_records: int
    ignored_records: int
    suppressed_emissions: int
    bootstrapped: bool = False
