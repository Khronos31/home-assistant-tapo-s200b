"""Validate, deduplicate, and expand raw S200B/S200D trigger logs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .const import (
    EVENT_DOUBLE_CLICK,
    EVENT_ROTATE_LEFT,
    EVENT_ROTATE_RIGHT,
    EVENT_SINGLE_CLICK,
    MAX_EMISSIONS_PER_POLL,
    MAX_ROTATION_DEGREES,
    RECENT_EVENT_IDS_LIMIT,
    ROTATION_STEP_DEGREES,
)
from .models import BatchPlan, ChildCursor, EventEmission, ParsedRecord


class InvalidEventRecord(ValueError):
    """Raised when a trigger-log record cannot be handled safely."""


def _required_int(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidEventRecord(f"{key} must be an integer")
    return value


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidEventRecord(f"{key} must be a non-empty string")
    return value


def parse_record(record: Mapping[str, Any]) -> ParsedRecord:
    """Parse one raw trigger-log record without guessing unknown event types."""
    record_id = _required_int(record, "id")
    timestamp = _required_int(record, "timestamp")
    event_id = _required_text(record, "eventId")
    raw_event_type = _required_text(record, "event")

    if record_id < 0 or timestamp < 0:
        raise InvalidEventRecord("id and timestamp must not be negative")

    if raw_event_type == "singleClick":
        emissions = (EventEmission(EVENT_SINGLE_CLICK),)
    elif raw_event_type == "doubleClick":
        emissions = (EventEmission(EVENT_DOUBLE_CLICK),)
    elif raw_event_type == "rotation":
        emissions = _rotation_emissions(record)
    else:
        return ParsedRecord(
            record_id=record_id,
            event_id=event_id,
            timestamp=timestamp,
            raw_event_type=raw_event_type,
            emissions=(),
            ignored_reason="unknown_event_type",
        )

    return ParsedRecord(
        record_id=record_id,
        event_id=event_id,
        timestamp=timestamp,
        raw_event_type=raw_event_type,
        emissions=emissions,
    )


def _rotation_emissions(record: Mapping[str, Any]) -> tuple[EventEmission, ...]:
    params = record.get("params")
    if not isinstance(params, Mapping):
        raise InvalidEventRecord("rotation params must be an object")
    degrees = params.get("rotate_deg")
    if isinstance(degrees, bool) or not isinstance(degrees, int):
        raise InvalidEventRecord("rotate_deg must be an integer")
    if degrees == 0 or degrees % ROTATION_STEP_DEGREES:
        raise InvalidEventRecord("rotate_deg must be a non-zero multiple of 30")
    if abs(degrees) > MAX_ROTATION_DEGREES:
        raise InvalidEventRecord("rotate_deg exceeds the safety bound")

    steps = abs(degrees) // ROTATION_STEP_DEGREES
    event_type = EVENT_ROTATE_RIGHT if degrees > 0 else EVENT_ROTATE_LEFT
    step_degrees = ROTATION_STEP_DEGREES if degrees > 0 else -ROTATION_STEP_DEGREES
    return tuple(
        EventEmission(
            event_type,
            {
                "degrees": step_degrees,
                "source_degrees": degrees,
                "step_index": index,
                "step_count": steps,
            },
        )
        for index in range(1, steps + 1)
    )


def _record_identity(record: Mapping[str, Any]) -> tuple[int, str | None]:
    record_id = _required_int(record, "id")
    event_id = record.get("eventId")
    return record_id, event_id if isinstance(event_id, str) and event_id else None


def plan_batch(
    raw_records: Iterable[Mapping[str, Any]],
    cursor: ChildCursor,
    *,
    emission_limit: int = MAX_EMISSIONS_PER_POLL,
) -> BatchPlan:
    """Plan an at-most-once batch.

    The returned cursor must be durably saved before publishing ``emissions``.
    If the process stops between those operations, events may be lost but are
    never intentionally replayed after restart.
    """
    records = list(raw_records)
    if not records:
        return BatchPlan(cursor, (), 0, 0, 0, 0)

    identities: list[tuple[int, str | None, Mapping[str, Any]]] = []
    invalid_identity_records = 0
    for record in records:
        try:
            record_id, event_id = _record_identity(record)
        except InvalidEventRecord:
            invalid_identity_records += 1
            continue
        identities.append((record_id, event_id, record))

    if not identities:
        return BatchPlan(
            cursor,
            (),
            0,
            0,
            invalid_identity_records,
            0,
        )

    newest_record_id = max(record_id for record_id, _, _ in identities)
    if cursor.latest_record_id is None:
        identities_for_recent_ids = identities
    else:
        identities_for_recent_ids = [
            identity for identity in identities if identity[0] > cursor.latest_record_id
        ]
    observed_event_ids = [
        event_id
        for _, event_id, _ in sorted(identities_for_recent_ids)
        if event_id is not None
    ]
    next_recent_ids = _merge_recent_ids(
        cursor.recent_event_ids, observed_event_ids, RECENT_EVENT_IDS_LIMIT
    )
    next_cursor = ChildCursor(
        latest_record_id=max(cursor.latest_record_id or 0, newest_record_id),
        recent_event_ids=next_recent_ids,
    )

    if cursor.latest_record_id is None:
        return BatchPlan(
            cursor=next_cursor,
            emissions=(),
            processed_records=len(identities),
            duplicate_records=0,
            ignored_records=invalid_identity_records,
            suppressed_emissions=0,
            bootstrapped=True,
        )

    seen_ids = set(cursor.recent_event_ids)
    candidates: list[Mapping[str, Any]] = []
    duplicates = 0
    for record_id, event_id, record in identities:
        if record_id <= cursor.latest_record_id or (
            event_id is not None and event_id in seen_ids
        ):
            duplicates += 1
            continue
        candidates.append(record)

    candidates.sort(key=lambda item: _required_int(item, "id"))
    emissions: list[EventEmission] = []
    ignored = invalid_identity_records
    for record in candidates:
        try:
            parsed = parse_record(record)
        except InvalidEventRecord:
            ignored += 1
            continue
        if parsed.ignored_reason is not None:
            ignored += 1
        emissions.extend(parsed.emissions)

    if len(emissions) > emission_limit:
        suppressed = len(emissions)
        emissions = []
    else:
        suppressed = 0

    return BatchPlan(
        cursor=next_cursor,
        emissions=tuple(emissions),
        processed_records=len(candidates),
        duplicate_records=duplicates,
        ignored_records=ignored,
        suppressed_emissions=suppressed,
    )


def _merge_recent_ids(
    existing: Iterable[str], observed: Iterable[str], limit: int
) -> tuple[str, ...]:
    merged: dict[str, None] = {}
    for event_id in (*existing, *observed):
        merged.pop(event_id, None)
        merged[event_id] = None
    return tuple(merged)[-limit:]
