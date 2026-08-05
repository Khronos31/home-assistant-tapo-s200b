from __future__ import annotations

import pytest

from custom_components.tapo_s200b.const import (
    EVENT_DOUBLE_CLICK,
    EVENT_ROTATE_LEFT,
    EVENT_ROTATE_RIGHT,
    EVENT_SINGLE_CLICK,
)
from custom_components.tapo_s200b.models import ChildCursor
from custom_components.tapo_s200b.processor import (
    InvalidEventRecord,
    parse_record,
    plan_batch,
)


def raw_event(
    record_id: int,
    event_id: str,
    event: str,
    degrees: int | None = None,
) -> dict:
    value = {
        "id": record_id,
        "eventId": event_id,
        "timestamp": 1_700_000_000 + record_id,
        "event": event,
    }
    if degrees is not None:
        value["params"] = {"rotate_deg": degrees}
    return value


@pytest.mark.parametrize(
    ("raw_type", "expected"),
    [("singleClick", EVENT_SINGLE_CLICK), ("doubleClick", EVENT_DOUBLE_CLICK)],
)
def test_click_event_types(raw_type: str, expected: str) -> None:
    parsed = parse_record(raw_event(1, "uuid-1", raw_type))
    assert [event.event_type for event in parsed.emissions] == [expected]


@pytest.mark.parametrize(
    ("degrees", "event_type", "count"),
    [
        (30, EVENT_ROTATE_RIGHT, 1),
        (120, EVENT_ROTATE_RIGHT, 4),
        (-60, EVENT_ROTATE_LEFT, 2),
    ],
)
def test_rotation_expands_to_30_degree_steps(
    degrees: int, event_type: str, count: int
) -> None:
    parsed = parse_record(raw_event(1, "uuid-1", "rotation", degrees))
    assert len(parsed.emissions) == count
    assert {event.event_type for event in parsed.emissions} == {event_type}
    assert parsed.emissions[-1].data["step_count"] == count
    assert parsed.emissions[-1].data["source_degrees"] == degrees


@pytest.mark.parametrize("degrees", [0, 31, -31, 750, 3600])
def test_rotation_rejects_unsafe_magnitudes(degrees: int) -> None:
    with pytest.raises(InvalidEventRecord):
        parse_record(raw_event(1, "uuid-1", "rotation", degrees))


def test_unknown_event_is_ignored_without_exception() -> None:
    parsed = parse_record(raw_event(1, "uuid-1", "firmwareAddedSomething"))
    assert parsed.emissions == ()
    assert parsed.ignored_reason == "unknown_event_type"


def test_first_page_bootstraps_without_replaying_history() -> None:
    plan = plan_batch(
        [
            raw_event(11, "uuid-11", "singleClick"),
            raw_event(10, "uuid-10", "doubleClick"),
        ],
        ChildCursor(),
    )
    assert plan.bootstrapped is True
    assert plan.emissions == ()
    assert plan.cursor.latest_record_id == 11
    assert plan.cursor.recent_event_ids == ("uuid-10", "uuid-11")


def test_new_records_are_emitted_oldest_first() -> None:
    plan = plan_batch(
        [
            raw_event(12, "uuid-12", "doubleClick"),
            raw_event(11, "uuid-11", "singleClick"),
        ],
        ChildCursor(latest_record_id=10, recent_event_ids=("uuid-10",)),
    )
    assert [event.event_type for event in plan.emissions] == [
        EVENT_SINGLE_CLICK,
        EVENT_DOUBLE_CLICK,
    ]
    assert plan.cursor.latest_record_id == 12


def test_event_id_and_record_cursor_both_deduplicate() -> None:
    plan = plan_batch(
        [
            raw_event(12, "uuid-seen", "singleClick"),
            raw_event(10, "uuid-new-but-old-record", "doubleClick"),
        ],
        ChildCursor(latest_record_id=11, recent_event_ids=("uuid-seen",)),
    )
    assert plan.emissions == ()
    assert plan.duplicate_records == 2


def test_malformed_event_is_advanced_but_not_emitted() -> None:
    malformed = raw_event(12, "uuid-12", "rotation")
    plan = plan_batch(
        [malformed], ChildCursor(latest_record_id=11, recent_event_ids=("uuid-11",))
    )
    assert plan.cursor.latest_record_id == 12
    assert plan.emissions == ()
    assert plan.ignored_records == 1


def test_batch_amplification_cap_suppresses_entire_batch() -> None:
    plan = plan_batch(
        [
            raw_event(12, "uuid-12", "rotation", 720),
            raw_event(11, "uuid-11", "rotation", 720),
            raw_event(10, "uuid-10", "rotation", 720),
        ],
        ChildCursor(latest_record_id=9),
        emission_limit=64,
    )
    assert plan.emissions == ()
    assert plan.suppressed_emissions == 72
    assert plan.cursor.latest_record_id == 12
