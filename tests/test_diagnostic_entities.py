"""Home Assistant behavior for opt-in child diagnostic entities."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tapo_s200b.api import FetchResult
from custom_components.tapo_s200b.connection import ButtonDiagnostics
from custom_components.tapo_s200b.const import (
    CONF_EMAIL,
    DIAGNOSTIC_BATTERY_LOW,
    DIAGNOSTIC_CLOUD_CONNECTION,
    DIAGNOSTIC_FEATURES,
    DIAGNOSTIC_REBOOT,
    DIAGNOSTIC_RSSI,
    DIAGNOSTIC_SIGNAL_LEVEL,
    DIAGNOSTIC_UNPAIR,
    DOMAIN,
)


async def test_enabled_diagnostics_publish_values_and_actions_only_run_on_press(
    hass, enable_custom_integrations
) -> None:
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

    registry = er.async_get(hass)
    requested = {
        DIAGNOSTIC_CLOUD_CONNECTION: "binary_sensor",
        DIAGNOSTIC_SIGNAL_LEVEL: "sensor",
        DIAGNOSTIC_BATTERY_LOW: "binary_sensor",
        DIAGNOSTIC_RSSI: "sensor",
        DIAGNOSTIC_REBOOT: "button",
        DIAGNOSTIC_UNPAIR: "button",
    }
    entity_ids = {
        key: registry.async_get_or_create(
            domain=domain,
            platform=DOMAIN,
            unique_id=f"child-id_{key}",
            config_entry=entry,
            suggested_object_id=f"Desk dial {key}",
            disabled_by=None,
        ).entity_id
        for key, domain in requested.items()
    }

    empty_fetch = FetchResult((), 1, True, False, False)
    with (
        patch(
            "custom_components.tapo_s200b.async_get_connection",
            AsyncMock(return_value=connection),
        ),
        patch(
            "custom_components.tapo_s200b.coordinator.async_fetch_since",
            AsyncMock(return_value=empty_fetch),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    diagnostic_fetch.assert_awaited()
    assert hass.states.get(entity_ids[DIAGNOSTIC_CLOUD_CONNECTION]).state == "on"
    assert hass.states.get(entity_ids[DIAGNOSTIC_SIGNAL_LEVEL]).state == "3"
    assert hass.states.get(entity_ids[DIAGNOSTIC_BATTERY_LOW]).state == "off"
    assert hass.states.get(entity_ids[DIAGNOSTIC_RSSI]).state == "-42"
    reboot.assert_not_awaited()
    unpair.assert_not_awaited()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_ids[DIAGNOSTIC_REBOOT]},
        blocking=True,
    )
    reboot.assert_awaited_once()
    unpair.assert_not_awaited()

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_ids[DIAGNOSTIC_UNPAIR]},
        blocking=True,
    )
    unpair.assert_awaited_once()
