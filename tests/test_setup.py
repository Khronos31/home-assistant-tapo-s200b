"""End-to-end setup test through Home Assistant's config-entry machinery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    EVENT_STATE_CHANGED,
    EntityCategory,
)
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tapo_s200b import async_setup_entry
from custom_components.tapo_s200b.api import FetchResult
from custom_components.tapo_s200b.connection import ButtonDiagnostics
from custom_components.tapo_s200b.const import (
    CONF_EMAIL,
    CONF_MAC,
    DIAGNOSTIC_FEATURES,
    DOMAIN,
)


def fetch(records: list[dict]) -> FetchResult:
    return FetchResult(
        records=tuple(records),
        pages=1,
        reached_cursor=True,
        history_gap=False,
        truncated=False,
    )


def event(record_id: int, event_type: str, degrees: int | None = None) -> dict:
    record = {
        "id": record_id,
        "eventId": f"uuid-{record_id}",
        "timestamp": 1_700_000_000 + record_id,
        "event": event_type,
    }
    if degrees is not None:
        record["params"] = {"rotate_deg": degrees}
    return record


async def test_setup_and_four_step_rotation(hass, enable_custom_integrations) -> None:
    diagnostic_fetch = AsyncMock(return_value=ButtonDiagnostics(True, 3, False, -42))
    reboot = AsyncMock()
    unpair = AsyncMock()
    child = SimpleNamespace(
        device_id="child-id",
        model="S200D",
        nickname="Desk dial",
        firmware_version="1.0.0",
        hardware_version="1.0",
        diagnostic_features=DIAGNOSTIC_FEATURES,
        async_get_diagnostics=diagnostic_fetch,
        async_reboot=reboot,
        async_unpair=unpair,
    )
    connection = SimpleNamespace(
        hub=SimpleNamespace(
            device_id="hub-id",
            mac="AA:BB:CC:DD:EE:FF",
            model="H110",
            nickname="Living room hub",
            firmware_version="1.2.3",
            hardware_version="1.0",
            host="192.168.50.20",
        ),
        buttons=(child,),
        async_close=AsyncMock(),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="hub-id",
        data={
            CONF_HOST: "192.168.50.10",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "secret",
        },
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.tapo_s200b.async_get_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "custom_components.tapo_s200b.coordinator.async_fetch_since",
            AsyncMock(return_value=fetch([event(10, "singleClick")])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.data[CONF_HOST] == "192.168.50.20"
    assert entry.data[CONF_MAC] == "aa:bb:cc:dd:ee:ff"

    states = hass.states.async_all("event")
    assert len(states) == 1
    entity_id = states[0].entity_id
    assert states[0].state == "unknown"

    registry = dr.async_get(hass)
    entry_devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert {device.model for device in entry_devices} == {"H110", "S200D"}

    entity_registry = er.async_get(hass)
    registry_entries = er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    )
    diagnostic_entries = [item for item in registry_entries if item.domain != "event"]
    assert len(diagnostic_entries) == 6
    assert {item.domain for item in diagnostic_entries} == {
        "binary_sensor",
        "button",
        "sensor",
    }
    assert all(
        item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        for item in diagnostic_entries
    )
    assert all(
        item.entity_category is EntityCategory.DIAGNOSTIC for item in diagnostic_entries
    )
    assert all(hass.states.get(item.entity_id) is None for item in diagnostic_entries)
    diagnostic_fetch.assert_not_awaited()
    reboot.assert_not_awaited()
    unpair.assert_not_awaited()

    changed = []

    def capture(event_data) -> None:
        if event_data.data["entity_id"] == entity_id:
            changed.append(event_data)

    remove_listener = hass.bus.async_listen(EVENT_STATE_CHANGED, capture)
    with patch(
        "custom_components.tapo_s200b.coordinator.async_fetch_since",
        AsyncMock(
            return_value=fetch([event(11, "rotation", 120), event(10, "singleClick")])
        ),
    ):
        await entry.runtime_data.coordinator.async_request_refresh()
        await hass.async_block_till_done()
    remove_listener()

    assert len(changed) == 4
    assert [item.data["new_state"].attributes["step_index"] for item in changed] == [
        1,
        2,
        3,
        4,
    ]
    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == "rotate_right"
    assert state.attributes["source_degrees"] == 120
    assert state.attributes["step_count"] == 4

    assert await hass.config_entries.async_unload(entry.entry_id)
    connection.async_close.assert_awaited_once()


async def test_failed_host_repair_does_not_persist_discovered_host(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="hub-id",
        data={
            CONF_HOST: "192.168.50.10",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_MAC: "aa:bb:cc:dd:ee:ff",
        },
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)

    with patch(
        "custom_components.tapo_s200b.async_get_connection",
        AsyncMock(side_effect=ConnectionError),
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)

    assert entry.data == original_data


async def test_failed_initial_refresh_does_not_persist_discovered_host(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="hub-id",
        data={
            CONF_HOST: "192.168.50.10",
            CONF_EMAIL: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_MAC: "aa:bb:cc:dd:ee:ff",
        },
    )
    entry.add_to_hass(hass)
    original_data = dict(entry.data)
    connection = SimpleNamespace(
        hub=SimpleNamespace(
            device_id="hub-id",
            mac="AA-BB-CC-DD-EE-FF",
            host="192.168.50.20",
            model="H110",
            nickname="Study hub",
            firmware_version="1.2.3",
            hardware_version="1.0",
        ),
        buttons=(),
        async_close=AsyncMock(),
    )

    with (
        patch(
            "custom_components.tapo_s200b.async_get_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "custom_components.tapo_s200b.TapoS200BCoordinator.async_config_entry_first_refresh",
            AsyncMock(side_effect=ConnectionError),
        ),
        pytest.raises(ConnectionError),
    ):
        await async_setup_entry(hass, entry)

    assert entry.data == original_data
    connection.async_close.assert_awaited_once()
