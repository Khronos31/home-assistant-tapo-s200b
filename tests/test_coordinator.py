"""Tests for durable per-child at-most-once delivery."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tapo_s200b.api import FetchResult
from custom_components.tapo_s200b.const import DOMAIN, EVENT_SINGLE_CLICK
from custom_components.tapo_s200b.coordinator import TapoS200BCoordinator


@dataclass
class FakeButton:
    device_id: str


class FakeStore:
    def __init__(self, value=None) -> None:
        self.value = value
        self.saved: list[dict] = []

    async def async_load(self):
        return self.value

    async def async_save(self, value) -> None:
        self.saved.append(value)
        self.value = value


def raw_event(record_id: int) -> dict:
    return {
        "id": record_id,
        "eventId": f"uuid-{record_id}",
        "timestamp": 1_700_000_000 + record_id,
        "event": "singleClick",
    }


def fetch(*record_ids: int) -> FetchResult:
    return FetchResult(
        records=tuple(raw_event(record_id) for record_id in record_ids),
        pages=1,
        reached_cursor=bool(record_ids),
        history_gap=False,
        truncated=False,
    )


def coordinator(hass, store: FakeStore, *child_ids: str) -> TapoS200BCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    connection = SimpleNamespace(
        hub=SimpleNamespace(model="H110"),
        buttons=tuple(FakeButton(child_id) for child_id in child_ids),
    )
    value = TapoS200BCoordinator(hass, entry, connection)
    value._store = store
    return value


async def test_restart_never_replays_saved_record(hass) -> None:
    store = FakeStore()
    first = coordinator(hass, store, "child-1")
    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        AsyncMock(return_value=fetch(10)),
    ):
        assert await first._async_update_data() == {"child-1": ()}
    assert store.saved

    restarted = coordinator(hass, store, "child-1")
    await restarted.async_load_cursors()
    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        AsyncMock(return_value=fetch(10)),
    ):
        assert await restarted._async_update_data() == {"child-1": ()}

    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        AsyncMock(return_value=fetch(11, 10)),
    ):
        result = await restarted._async_update_data()
    assert [item.event_type for item in result["child-1"]] == [EVENT_SINGLE_CLICK]

    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        AsyncMock(return_value=fetch(11, 10)),
    ):
        assert await restarted._async_update_data() == {"child-1": ()}


async def test_cursor_save_failure_prevents_emission(hass) -> None:
    store = FakeStore({
        "children": {"child-1": {"latest_record_id": 10, "recent_event_ids": []}}
    })
    value = coordinator(hass, store, "child-1")
    await value.async_load_cursors()
    store.async_save = AsyncMock(side_effect=OSError("disk full"))
    listener = Mock()
    value.async_add_child_listener("child-1", listener)

    with (
        patch(
            "custom_components.tapo_s200b.coordinator.async_fetch_since",
            AsyncMock(return_value=fetch(11, 10)),
        ),
        pytest.raises(OSError, match="disk full"),
    ):
        await value._async_update_data()
    listener.assert_not_called()


async def test_one_child_failure_does_not_block_other_child(hass) -> None:
    store = FakeStore()
    value = coordinator(hass, store, "bad-child", "good-child")

    async def fetch_child(child, *_args, **_kwargs):
        if child.device_id == "bad-child":
            raise TimeoutError
        return fetch(10)

    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        side_effect=fetch_child,
    ):
        result = await value._async_update_data()

    assert result == {"good-child": ()}
    assert value.diagnostics.failed_child_fetches == 1
    assert value.diagnostics.successful_child_fetches == 1


async def test_all_child_failures_mark_update_failed(hass) -> None:
    value = coordinator(hass, FakeStore(), "child-1", "child-2")
    with (
        patch(
            "custom_components.tapo_s200b.coordinator.async_fetch_since",
            AsyncMock(side_effect=TimeoutError),
        ),
        pytest.raises(UpdateFailed, match="All trigger-log reads failed"),
    ):
        await value._async_update_data()


async def test_child_is_delivered_after_save_before_later_fetch(hass) -> None:
    store = FakeStore({
        "children": {
            "child-1": {"latest_record_id": 10, "recent_event_ids": []},
            "child-2": {"latest_record_id": 10, "recent_event_ids": []},
        }
    })
    value = coordinator(hass, store, "child-1", "child-2")
    await value.async_load_cursors()
    delivered: list[tuple] = []

    def deliver(emissions) -> None:
        assert store.saved
        delivered.append(emissions)

    value.async_add_child_listener("child-1", deliver)

    async def fetch_child(child, *_args, **_kwargs):
        if child.device_id == "child-1":
            return fetch(11, 10)
        assert delivered, "child-1 waited for a later child fetch"
        return fetch(10)

    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        side_effect=fetch_child,
    ):
        result = await value._async_update_data()

    assert [item.event_type for item in delivered[0]] == [EVENT_SINGLE_CLICK]
    assert result["child-1"] == delivered[0]
    assert value.diagnostics.last_event_delivery_offset_ms is not None
    assert value.diagnostics.last_event_poll_latency_ms is not None
    assert value.diagnostics.last_event_delivery_lead_ms is not None
    assert value.diagnostics.last_event_delivery_lead_ms >= 0


async def test_listener_exception_does_not_block_later_child(hass) -> None:
    store = FakeStore({
        "children": {
            "child-1": {"latest_record_id": 10, "recent_event_ids": []},
            "child-2": {"latest_record_id": 10, "recent_event_ids": []},
        }
    })
    value = coordinator(hass, store, "child-1", "child-2")
    await value.async_load_cursors()
    value.async_add_child_listener(
        "child-1", Mock(side_effect=RuntimeError("entity removed"))
    )
    second_listener = Mock()
    value.async_add_child_listener("child-2", second_listener)

    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        AsyncMock(return_value=fetch(11, 10)),
    ):
        await value._async_update_data()

    second_listener.assert_called_once()


async def test_context_filtering_polls_only_enabled_event_children(hass) -> None:
    value = coordinator(hass, FakeStore(), "child-1", "child-2")
    value.async_enable_context_filtering()
    remove_listener = value.async_add_listener(Mock(), context="child-2")
    fetched: list[str] = []

    async def fetch_child(child, *_args, **_kwargs):
        fetched.append(child.device_id)
        return fetch(10)

    try:
        with patch(
            "custom_components.tapo_s200b.coordinator.async_fetch_since",
            side_effect=fetch_child,
        ):
            await value._async_update_data()
    finally:
        remove_listener()

    assert fetched == ["child-2"]


async def test_bootstrap_polls_all_children_before_context_filtering(hass) -> None:
    value = coordinator(hass, FakeStore(), "child-1", "child-2")
    fetched: list[str] = []

    async def fetch_child(child, *_args, **_kwargs):
        fetched.append(child.device_id)
        return fetch(10)

    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        side_effect=fetch_child,
    ):
        await value._async_update_data()

    assert fetched == ["child-1", "child-2"]
