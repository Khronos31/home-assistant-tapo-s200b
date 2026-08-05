from __future__ import annotations

from dataclasses import dataclass

import pytest

from custom_components.tapo_s200b.api import async_fetch_since


class FakeClient:
    def __init__(self, pages: dict[int, list[dict]]) -> None:
        self.pages = pages
        self.requested_start_ids: list[int] = []
        self.requested_page_sizes: list[int] = []

    async def get_trigger_logs(self, page_size: int, start_id: int) -> dict:
        self.requested_start_ids.append(start_id)
        self.requested_page_sizes.append(page_size)
        logs = self.pages.get(start_id, [])
        assert page_size > 0
        return {"start_id": start_id, "sum": 100, "logs": logs}


@dataclass
class FakeChild:
    client: FakeClient
    device_id: str = "child-1"

    async def async_get_trigger_logs(self, page_size: int, start_id: int) -> dict:
        return await self.client.get_trigger_logs(page_size, start_id)


def records(*record_ids: int) -> list[dict]:
    return [
        {
            "id": record_id,
            "eventId": f"uuid-{record_id}",
            "timestamp": 1_700_000_000 + record_id,
            "event": "singleClick",
        }
        for record_id in record_ids
    ]


@pytest.mark.asyncio
async def test_bootstrap_fetches_only_latest_page() -> None:
    client = FakeClient({0: records(105, 104, 103)})
    result = await async_fetch_since(
        FakeChild(client),
        None,
        page_size=3,
        first_page_size=1,
        max_pages=10,
    )
    assert [item["id"] for item in result.records] == [105, 104, 103]
    assert client.requested_start_ids == [0]
    assert client.requested_page_sizes == [1]
    assert result.history_gap is False


@pytest.mark.asyncio
async def test_pagination_walks_backward_until_inclusive_cursor() -> None:
    client = FakeClient({
        0: records(105),
        104: records(104, 103, 102),
        101: records(101, 100, 99),
    })
    result = await async_fetch_since(
        FakeChild(client),
        100,
        page_size=3,
        first_page_size=1,
        max_pages=10,
    )
    assert [item["id"] for item in result.records] == [105, 104, 103, 102, 101, 100, 99]
    assert client.requested_start_ids == [0, 104, 101]
    assert client.requested_page_sizes == [1, 3, 3]
    assert result.reached_cursor is True
    assert result.history_gap is False
    assert result.truncated is False


@pytest.mark.asyncio
async def test_exhausted_history_reports_gap() -> None:
    client = FakeClient({0: records(105, 104)})
    result = await async_fetch_since(FakeChild(client), 50, page_size=3, max_pages=10)
    assert result.reached_cursor is False
    assert result.history_gap is True
    assert result.truncated is False


@pytest.mark.asyncio
async def test_page_limit_reports_truncation() -> None:
    client = FakeClient({
        0: records(109, 108, 107),
        106: records(106, 105, 104),
    })
    result = await async_fetch_since(FakeChild(client), 1, page_size=3, max_pages=2)
    assert result.reached_cursor is False
    assert result.history_gap is False
    assert result.truncated is True


@pytest.mark.asyncio
async def test_smaller_first_page_preserves_configured_recovery_budget() -> None:
    """A fast first page must not reduce the former page_size * max_pages cap."""
    client = FakeClient({
        0: records(106),
        105: records(105, 104, 103),
        102: records(102, 101, 100),
    })

    result = await async_fetch_since(
        FakeChild(client),
        100,
        page_size=3,
        first_page_size=1,
        max_pages=2,
    )

    assert result.reached_cursor is True
    assert result.truncated is False
    assert result.pages == 3
    assert client.requested_page_sizes == [1, 3, 3]
