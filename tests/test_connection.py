"""Tests for LAN targets, transport sharing, and resource ownership."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from plugp100.common.functional.tri import Success
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tapo_s200b.connection import (
    HubConnection,
    HubDescription,
    InvalidHostError,
    SharedIntegrationNotReady,
    _plugp100_button,
    async_get_connection,
    normalize_host,
)
from custom_components.tapo_s200b.const import (
    DIAGNOSTIC_BATTERY_LOW,
    DIAGNOSTIC_CLOUD_CONNECTION,
    DIAGNOSTIC_FEATURES,
    DIAGNOSTIC_REBOOT,
    DIAGNOSTIC_RSSI,
    DIAGNOSTIC_SIGNAL_LEVEL,
    DIAGNOSTIC_UNPAIR,
)
from custom_components.tapo_s200b.discovery import (
    AmbiguousHubError,
    DiscoveredHub,
    HubNotFoundError,
)


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "169.254.1.1", "8.8.8.8", "::1", "example.com"]
)
def test_host_rejects_non_rfc1918_targets(host: str) -> None:
    with pytest.raises(InvalidHostError):
        normalize_host(host)


@pytest.mark.parametrize("host", ["10.1.2.3", "172.16.0.1", "192.168.50.10"])
def test_host_accepts_rfc1918_targets(host: str) -> None:
    assert normalize_host(f" {host} ") == host


async def test_connection_runs_only_its_own_close_callback() -> None:
    close = AsyncMock()
    connection = HubConnection(
        HubDescription("hub-id", "AA:BB:CC:DD:EE:FF", "H110", "Hub", "1", "1", "test"),
        (),
        "test",
        close,
    )

    await connection.async_close()

    close.assert_awaited_once()


async def test_shared_connection_reuses_official_tplink_protocol(hass) -> None:
    query = AsyncMock(
        return_value={
            "get_trigger_logs": {
                "start_id": 10,
                "sum": 1,
                "logs": [{"id": 10}],
            }
        }
    )
    child = SimpleNamespace(
        device_id="child-id",
        model="S200D",
        alias="Dial",
        hw_info={"sw_ver": "1.0", "hw_ver": "2.0"},
        protocol=SimpleNamespace(query=query),
    )
    device = SimpleNamespace(
        device_id="hub-id",
        mac="AA:BB:CC:DD:EE:FF",
        model="H110",
        alias="Hub",
        hw_info={"sw_ver": "1.2", "hw_ver": "3.0"},
        children=(child,),
        get_child_device=lambda child_id: child if child_id == "child-id" else None,
    )
    entry = MockConfigEntry(
        domain="tplink",
        data={CONF_HOST: "192.168.50.10"},
        state=ConfigEntryState.LOADED,
    )
    entry.runtime_data = SimpleNamespace(
        parent_coordinator=SimpleNamespace(device=device)
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.tapo_s200b.connection.async_resolve_hub",
        AsyncMock(
            return_value=DiscoveredHub(
                "192.168.50.10",
                "H110",
                "hub-id",
                "AA:BB:CC:DD:EE:FF",
            )
        ),
    ):
        connection = await async_get_connection(
            hass,
            "192.168.50.5",
            "unused@example.com",
            "unused",
            expected_device_id="hub-id",
            expected_mac="AA:BB:CC:DD:EE:FF",
        )
    response = await connection.buttons[0].async_get_trigger_logs(50, 0)

    assert connection.transport == "tplink_shared"
    assert response["logs"] == [{"id": 10}]
    query.assert_awaited_once_with({
        "get_trigger_logs": {"page_size": 50, "start_id": 0}
    })
    await connection.async_close()


async def test_discovery_connects_standalone_without_official_integration(hass) -> None:
    close = AsyncMock()
    connection = HubConnection(
        HubDescription(
            "hub-id",
            "AA:BB:CC:DD:EE:FF",
            "H110C",
            "Hub",
            "1",
            "1",
            "Klap V2",
            "192.168.50.20",
        ),
        (),
        "standalone_plugp100",
        close,
    )
    with (
        patch(
            "custom_components.tapo_s200b.connection.async_resolve_hub",
            AsyncMock(
                return_value=DiscoveredHub(
                    "192.168.50.20",
                    "H110C",
                    "hub-id",
                    "AA:BB:CC:DD:EE:FF",
                )
            ),
        ),
        patch(
            "custom_components.tapo_s200b.connection.async_connect_hub",
            AsyncMock(return_value=connection),
        ) as connect_hub,
    ):
        result = await async_get_connection(
            hass,
            "192.168.50.10",
            "user@example.com",
            "secret",
            expected_device_id="hub-id",
            expected_mac="AA:BB:CC:DD:EE:FF",
        )

    assert result is connection
    connect_hub.assert_awaited_once_with(
        hass, "192.168.50.20", "user@example.com", "secret"
    )


async def test_missing_discovery_result_uses_verified_configured_host(hass) -> None:
    close = AsyncMock()
    connection = HubConnection(
        HubDescription(
            "hub-id",
            "AA:BB:CC:DD:EE:FF",
            "H110",
            "Hub",
            "1",
            "1",
            "Klap V2",
            "192.168.50.10",
        ),
        (),
        "standalone_plugp100",
        close,
    )
    with (
        patch(
            "custom_components.tapo_s200b.connection.async_resolve_hub",
            AsyncMock(side_effect=HubNotFoundError),
        ),
        patch(
            "custom_components.tapo_s200b.connection.async_connect_hub",
            AsyncMock(return_value=connection),
        ) as connect_hub,
    ):
        result = await async_get_connection(
            hass,
            "192.168.50.10",
            "user@example.com",
            "secret",
            expected_device_id="hub-id",
            expected_mac="AA:BB:CC:DD:EE:FF",
        )

    assert result is connection
    connect_hub.assert_awaited_once_with(
        hass, "192.168.50.10", "user@example.com", "secret"
    )


async def test_connected_identity_mismatch_is_closed(hass) -> None:
    close = AsyncMock()
    connection = HubConnection(
        HubDescription(
            "other-hub",
            "11:22:33:44:55:66",
            "H110",
            "Other",
            "1",
            "1",
            "Klap V2",
            "192.168.50.20",
        ),
        (),
        "standalone_plugp100",
        close,
    )
    with (
        patch(
            "custom_components.tapo_s200b.connection.async_resolve_hub",
            AsyncMock(
                return_value=DiscoveredHub(
                    "192.168.50.20",
                    "H110",
                    "hub-id",
                    "AA:BB:CC:DD:EE:FF",
                )
            ),
        ),
        patch(
            "custom_components.tapo_s200b.connection.async_connect_hub",
            AsyncMock(return_value=connection),
        ),
    ):
        with pytest.raises(AmbiguousHubError):
            await async_get_connection(
                hass,
                "192.168.50.10",
                "user@example.com",
                "secret",
                expected_device_id="hub-id",
                expected_mac="AA:BB:CC:DD:EE:FF",
            )

    close.assert_awaited_once()


async def test_shared_button_resolves_reloaded_official_device(hass) -> None:
    old_child = SimpleNamespace(
        device_id="child-id",
        model="S200D",
        alias="Dial",
        hw_info={},
        protocol=SimpleNamespace(query=AsyncMock()),
    )
    old_device = SimpleNamespace(
        device_id="hub-id",
        mac="AA:BB:CC:DD:EE:FF",
        model="H110",
        alias="Hub",
        hw_info={},
        children=(old_child,),
        get_child_device=lambda _child_id: old_child,
    )
    entry = MockConfigEntry(
        domain="tplink",
        data={CONF_HOST: "192.168.50.10"},
        state=ConfigEntryState.LOADED,
    )
    entry.runtime_data = SimpleNamespace(
        parent_coordinator=SimpleNamespace(device=old_device)
    )
    entry.add_to_hass(hass)
    connection = await async_get_connection(
        hass, "192.168.50.10", "unused@example.com", "unused"
    )

    new_query = AsyncMock(
        return_value={"get_trigger_logs": {"start_id": 20, "sum": 0, "logs": []}}
    )
    new_child = SimpleNamespace(
        device_id="child-id", protocol=SimpleNamespace(query=new_query)
    )
    new_device = SimpleNamespace(
        get_child_device=lambda child_id: new_child if child_id == "child-id" else None
    )
    entry.runtime_data = SimpleNamespace(
        parent_coordinator=SimpleNamespace(device=new_device)
    )

    response = await connection.buttons[0].async_get_trigger_logs(25, 19)

    assert response["start_id"] == 20
    new_query.assert_awaited_once()
    old_child.protocol.query.assert_not_awaited()


async def test_shared_diagnostics_and_actions_resolve_current_child(hass) -> None:
    old_features = {
        key: SimpleNamespace(value=None, set_value=AsyncMock())
        for key in DIAGNOSTIC_FEATURES
    }
    old_child = SimpleNamespace(
        device_id="child-id",
        model="S200D",
        alias="Dial",
        hw_info={},
        features=old_features,
        protocol=SimpleNamespace(query=AsyncMock()),
    )
    old_device = SimpleNamespace(
        device_id="hub-id",
        mac="AA:BB:CC:DD:EE:FF",
        model="H110",
        alias="Hub",
        hw_info={},
        children=(old_child,),
        get_child_device=lambda _child_id: old_child,
    )
    entry = MockConfigEntry(
        domain="tplink",
        data={CONF_HOST: "192.168.50.10"},
        state=ConfigEntryState.LOADED,
    )
    entry.runtime_data = SimpleNamespace(
        parent_coordinator=SimpleNamespace(device=old_device)
    )
    entry.add_to_hass(hass)
    connection = await async_get_connection(
        hass, "192.168.50.10", "unused@example.com", "unused"
    )

    reboot = AsyncMock()
    unpair = AsyncMock()
    new_features = {
        DIAGNOSTIC_CLOUD_CONNECTION: SimpleNamespace(value=True, set_value=AsyncMock()),
        DIAGNOSTIC_SIGNAL_LEVEL: SimpleNamespace(value=3, set_value=AsyncMock()),
        DIAGNOSTIC_BATTERY_LOW: SimpleNamespace(value=False, set_value=AsyncMock()),
        DIAGNOSTIC_RSSI: SimpleNamespace(value=-42, set_value=AsyncMock()),
        DIAGNOSTIC_REBOOT: SimpleNamespace(value=None, set_value=reboot),
        DIAGNOSTIC_UNPAIR: SimpleNamespace(value=None, set_value=unpair),
    }
    new_child = SimpleNamespace(device_id="child-id", features=new_features)
    new_device = SimpleNamespace(get_child_device=lambda _child_id: new_child)
    entry.runtime_data = SimpleNamespace(
        parent_coordinator=SimpleNamespace(device=new_device)
    )

    button = connection.buttons[0]
    diagnostics = await button.async_get_diagnostics()
    await button.async_reboot()
    await button.async_unpair()

    assert button.diagnostic_features == DIAGNOSTIC_FEATURES
    assert diagnostics.cloud_connected is True
    assert diagnostics.signal_level == 3
    assert diagnostics.battery_low is False
    assert diagnostics.rssi == -42
    reboot.assert_awaited_once_with(True)
    unpair.assert_awaited_once_with(True)
    for feature in old_features.values():
        feature.set_value.assert_not_awaited()


async def test_standalone_diagnostic_and_action_wire_contracts() -> None:
    client = SimpleNamespace(
        control_child=AsyncMock(return_value=Success({"status": 0})),
        execute_raw_request=AsyncMock(return_value=Success({})),
    )
    child = SimpleNamespace(
        device_id="child-id",
        model="S200D",
        nickname="Dial",
        firmware_version="1.0",
        hardware_version="1.0",
        raw_state={
            "signal_level": 3,
            "at_low_battery": False,
            "rssi": -42,
        },
        components=SimpleNamespace(has=lambda key: key == "cloud_connect"),
        client=client,
        update=AsyncMock(),
    )
    button = _plugp100_button(child, asyncio.Lock())

    diagnostics = await button.async_get_diagnostics()
    await button.async_reboot()
    await button.async_unpair()

    assert button.diagnostic_features == DIAGNOSTIC_FEATURES
    assert diagnostics.cloud_connected is True
    assert diagnostics.signal_level == 3
    assert diagnostics.battery_low is False
    assert diagnostics.rssi == -42
    cloud_request = client.control_child.await_args_list[0].args[1]
    reboot_request = client.control_child.await_args_list[1].args[1]
    unpair_request = client.execute_raw_request.await_args.args[0]
    assert cloud_request.method == "get_connect_cloud_state"
    assert cloud_request.params is None
    assert reboot_request.method == "device_reboot"
    assert reboot_request.params == {"delay": 1}
    assert unpair_request.method == "remove_child_device_list"
    assert unpair_request.params == {"child_device_list": [{"device_id": "child-id"}]}


async def test_enabled_official_entry_must_finish_loading_before_fallback(hass) -> None:
    entry = MockConfigEntry(
        domain="tplink",
        data={CONF_HOST: "192.168.50.10"},
        state=ConfigEntryState.SETUP_RETRY,
    )
    entry.add_to_hass(hass)

    with pytest.raises(SharedIntegrationNotReady):
        await async_get_connection(
            hass, "192.168.50.10", "unused@example.com", "unused"
        )
