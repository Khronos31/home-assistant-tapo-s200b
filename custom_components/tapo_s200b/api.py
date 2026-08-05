"""Read-only access to Tapo trigger-log pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class TriggerLogClient(Protocol):
    """Subset of the plugp100 child interface used by this integration."""

    device_id: str

    async def async_get_trigger_logs(
        self, page_size: int, start_id: int
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Raw records collected by walking backward from the newest page."""

    records: tuple[Mapping[str, Any], ...]
    pages: int
    reached_cursor: bool
    history_gap: bool
    truncated: bool


async def async_fetch_page(
    child: TriggerLogClient, page_size: int, start_id: int
) -> Mapping[str, Any]:
    """Fetch one raw page while preserving eventId and unknown event data."""
    response = await child.async_get_trigger_logs(page_size, start_id)
    if not isinstance(response, Mapping):
        raise TypeError("trigger-log response is not an object")
    return response


async def async_fetch_since(
    child: TriggerLogClient,
    latest_record_id: int | None,
    *,
    page_size: int,
    first_page_size: int | None = None,
    max_pages: int,
) -> FetchResult:
    """Fetch newest records and page backward until the stored cursor is reached.

    The H110 uses descending, inclusive pagination. ``start_id=0`` selects the
    newest page; subsequent pages use ``oldest_id - 1`` to avoid overlap.
    """
    records: list[Mapping[str, Any]] = []
    start_id = 0
    reached_cursor = False
    exhausted = False
    page_number = 0
    first_page_size = min(first_page_size or page_size, page_size)
    record_budget = page_size * max_pages

    while len(records) < record_budget:
        page_number += 1
        current_page_size = first_page_size if page_number == 1 else page_size
        response = await async_fetch_page(child, current_page_size, start_id)
        raw_logs = response.get("logs")
        if not isinstance(raw_logs, list):
            raise TypeError("trigger-log response has no logs list")
        page_records = [item for item in raw_logs if isinstance(item, Mapping)]
        records.extend(page_records)

        record_ids = [
            record_id
            for item in page_records
            if isinstance((record_id := item.get("id")), int)
            and not isinstance(record_id, bool)
        ]

        if latest_record_id is None:
            return FetchResult(tuple(records), page_number, False, False, False)
        if any(record_id <= latest_record_id for record_id in record_ids):
            reached_cursor = True
            return FetchResult(tuple(records), page_number, True, False, False)
        if len(page_records) < current_page_size or not record_ids:
            exhausted = True
            return FetchResult(tuple(records), page_number, False, True, False)

        next_start_id = min(record_ids) - 1
        if next_start_id < 1 or next_start_id >= start_id > 0:
            exhausted = True
            return FetchResult(tuple(records), page_number, False, True, False)
        start_id = next_start_id

    return FetchResult(
        tuple(records),
        page_number,
        reached_cursor,
        history_gap=not reached_cursor and exhausted,
        truncated=not reached_cursor and not exhausted,
    )
