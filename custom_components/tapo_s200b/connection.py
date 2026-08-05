"""Create a read-only connection to a Tapo H110C hub."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from plugp100.api.requests.tapo_request import TapoRequest
from plugp100.api.requests.trigger_logs_params import GetTriggerLogsParams
from plugp100.common.credentials import AuthCredential
from plugp100.devices.children.trigger_button import TriggerButtonDevice
from plugp100.devices.factory import DeviceConnectConfiguration, connect
from plugp100.devices.hub import TapoHub

from .const import (
    DIAGNOSTIC_BATTERY_LOW,
    DIAGNOSTIC_CLOUD_CONNECTION,
    DIAGNOSTIC_FEATURES,
    DIAGNOSTIC_REBOOT,
    DIAGNOSTIC_RSSI,
    DIAGNOSTIC_SIGNAL_LEVEL,
    DIAGNOSTIC_UNPAIR,
    SUPPORTED_BUTTON_MODELS,
    SUPPORTED_HUB_MODELS,
)


class UnsupportedHubError(ValueError):
    """Raised when the supplied address is not a supported H110C family hub."""


class NoButtonsError(ValueError):
    """Raised when a hub has no supported S200B/S200D children."""


class InvalidHostError(ValueError):
    """Raised when the target is not an RFC 1918 IPv4 address."""


class SharedIntegrationNotReady(ConnectionError):
    """Raised while a matching enabled TP-Link config entry is still loading."""


class SharedIntegrationUnavailable(ConnectionError):
    """Raised when an active shared TP-Link device cannot be resolved."""


_PRIVATE_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def normalize_host(value: str) -> str:
    """Return a canonical RFC 1918 IPv4 address for local-only access."""
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as err:
        raise InvalidHostError from err
    if not isinstance(address, ipaddress.IPv4Address) or not any(
        address in network for network in _PRIVATE_IPV4_NETWORKS
    ):
        raise InvalidHostError
    return str(address)


FetchPage = Callable[[int, int], Awaitable[Mapping[str, Any]]]
FetchDiagnostics = Callable[[], Awaitable["ButtonDiagnostics"]]
RunAction = Callable[[], Awaitable[None]]
CloseConnection = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class HubDescription:
    """Stable metadata independent of the selected transport library."""

    device_id: str
    mac: str
    model: str
    nickname: str
    firmware_version: str | None
    hardware_version: str | None
    protocol_version: str


@dataclass(frozen=True, slots=True)
class ButtonDiagnostics:
    """Passive diagnostic values exposed by one child."""

    cloud_connected: bool | None
    signal_level: int | None
    battery_low: bool | None
    rssi: int | None


@dataclass(frozen=True, slots=True)
class ButtonConnection:
    """One supported child, passive diagnostics, and opt-in maintenance actions."""

    device_id: str
    model: str
    nickname: str
    firmware_version: str | None
    hardware_version: str | None
    diagnostic_features: frozenset[str]
    _fetch_page: FetchPage = field(repr=False, compare=False)
    _fetch_diagnostics: FetchDiagnostics = field(repr=False, compare=False)
    _reboot: RunAction = field(repr=False, compare=False)
    _unpair: RunAction = field(repr=False, compare=False)

    async def async_get_trigger_logs(
        self, page_size: int, start_id: int
    ) -> Mapping[str, Any]:
        """Read one raw trigger-log page."""
        return await self._fetch_page(page_size, start_id)

    async def async_get_diagnostics(self) -> ButtonDiagnostics:
        """Read passive diagnostic values for this child."""
        return await self._fetch_diagnostics()

    async def async_reboot(self) -> None:
        """Reboot this child after an explicit entity-button press."""
        await self._reboot()

    async def async_unpair(self) -> None:
        """Remove this child from the hub after an explicit entity-button press."""
        await self._unpair()


@dataclass(slots=True)
class HubConnection:
    """An initialized hub and its supported button children."""

    hub: HubDescription
    buttons: tuple[ButtonConnection, ...]
    transport: str
    _close: CloseConnection = field(repr=False)

    async def async_close(self) -> None:
        """Release only resources owned by this connection."""
        await self._close()


async def async_get_connection(
    hass: HomeAssistant, host: str, email: str, password: str
) -> HubConnection:
    """Prefer the official TP-Link transport, falling back to standalone KLAP."""
    host = normalize_host(host)
    if shared := _async_get_shared_connection(hass, host):
        return shared
    return await async_connect_hub(hass, host, email, password)


def _async_get_shared_connection(
    hass: HomeAssistant, host: str
) -> HubConnection | None:
    """Wrap an already-loaded official TP-Link entry without owning its session."""
    matching_entries = [
        entry
        for entry in hass.config_entries.async_entries("tplink")
        if entry.data.get(CONF_HOST) == host
    ]
    loaded_entry = next(
        (entry for entry in matching_entries if entry.state is ConfigEntryState.LOADED),
        None,
    )
    if loaded_entry is None:
        if any(entry.disabled_by is None for entry in matching_entries):
            raise SharedIntegrationNotReady
        return None

    try:
        device = loaded_entry.runtime_data.parent_coordinator.device
    except (AttributeError, RuntimeError) as err:
        raise SharedIntegrationUnavailable from err
    if device.model.upper() not in SUPPORTED_HUB_MODELS:
        raise UnsupportedHubError

    buttons = tuple(
        _shared_button(hass, loaded_entry.entry_id, child)
        for child in device.children
        if child.model.upper() in SUPPORTED_BUTTON_MODELS
    )
    if not buttons:
        raise NoButtonsError

    async def async_noop_close() -> None:
        return None

    return HubConnection(
        hub=HubDescription(
            device_id=device.device_id,
            mac=device.mac,
            model=device.model,
            nickname=device.alias or f"Tapo hub {host}",
            firmware_version=_hardware_value(device, "sw_ver"),
            hardware_version=_hardware_value(device, "hw_ver"),
            protocol_version="python-kasa shared KLAP",
        ),
        buttons=buttons,
        transport="tplink_shared",
        _close=async_noop_close,
    )


def _shared_button(hass: HomeAssistant, entry_id: str, child: Any) -> ButtonConnection:
    child_id = child.device_id

    def current_child() -> Any:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise SharedIntegrationUnavailable
        try:
            parent = entry.runtime_data.parent_coordinator.device
            current_child = parent.get_child_device(child_id)
        except (AttributeError, RuntimeError) as err:
            raise SharedIntegrationUnavailable from err
        if current_child is None:
            raise SharedIntegrationUnavailable
        return current_child

    async def async_fetch(page_size: int, start_id: int) -> Mapping[str, Any]:
        current = current_child()
        response = await current.protocol.query({
            "get_trigger_logs": {
                "page_size": page_size,
                "start_id": start_id,
            }
        })
        result = response.get("get_trigger_logs")
        if not isinstance(result, Mapping):
            raise TypeError("trigger-log response is not an object")
        return result

    async def async_fetch_diagnostics() -> ButtonDiagnostics:
        current = current_child()
        return ButtonDiagnostics(
            cloud_connected=_optional_bool(
                _feature_value(current, DIAGNOSTIC_CLOUD_CONNECTION)
            ),
            signal_level=_optional_int(
                _feature_value(current, DIAGNOSTIC_SIGNAL_LEVEL)
            ),
            battery_low=_optional_bool(_feature_value(current, DIAGNOSTIC_BATTERY_LOW)),
            rssi=_optional_int(_feature_value(current, DIAGNOSTIC_RSSI)),
        )

    async def async_reboot() -> None:
        await _set_feature(current_child(), DIAGNOSTIC_REBOOT)

    async def async_unpair() -> None:
        await _set_feature(current_child(), DIAGNOSTIC_UNPAIR)

    features = getattr(child, "features", {})
    diagnostic_features = frozenset(
        key for key in DIAGNOSTIC_FEATURES if key in features
    )

    return ButtonConnection(
        device_id=child_id,
        model=child.model,
        nickname=child.alias or child.model,
        firmware_version=_hardware_value(child, "sw_ver"),
        hardware_version=_hardware_value(child, "hw_ver"),
        diagnostic_features=diagnostic_features,
        _fetch_page=async_fetch,
        _fetch_diagnostics=async_fetch_diagnostics,
        _reboot=async_reboot,
        _unpair=async_unpair,
    )


def _feature_value(device: Any, feature_id: str) -> Any:
    feature = getattr(device, "features", {}).get(feature_id)
    return None if feature is None else feature.value


async def _set_feature(device: Any, feature_id: str) -> None:
    feature = getattr(device, "features", {}).get(feature_id)
    if feature is None:
        raise SharedIntegrationUnavailable
    await feature.set_value(True)


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _hardware_value(device: Any, key: str) -> str | None:
    value = device.hw_info.get(key)
    return value if isinstance(value, str) else None


async def async_connect_hub(
    hass: HomeAssistant, host: str, email: str, password: str
) -> HubConnection:
    """Connect a standalone plugp100 session using local KLAP requests only."""
    host = normalize_host(host)
    session = async_create_clientsession(
        hass,
        auto_cleanup=False,
        cookie_jar=aiohttp.CookieJar(unsafe=True, quote_cookie=False),
    )
    try:
        device = await connect(
            DeviceConnectConfiguration(
                host=host,
                credentials=AuthCredential(email, password),
            ),
            session=session,
        )
        await device.update()
        if (
            not isinstance(device, TapoHub)
            or device.model.upper() not in SUPPORTED_HUB_MODELS
        ):
            raise UnsupportedHubError
        raw_buttons = tuple(
            child
            for child in device.children
            if isinstance(child, TriggerButtonDevice)
            and child.model.upper() in SUPPORTED_BUTTON_MODELS
        )
        if not raw_buttons:
            raise NoButtonsError

        async def async_close_owned() -> None:
            await device.client.close()
            if not session.closed:
                # Home Assistant owns the connector; this connection owns only
                # the per-device session facade around it.
                session.detach()

        request_lock = asyncio.Lock()
        return HubConnection(
            hub=HubDescription(
                device_id=device.device_id,
                mac=device.mac,
                model=device.model,
                nickname=device.nickname,
                firmware_version=device.firmware_version,
                hardware_version=device.hardware_version,
                protocol_version=device.protocol_version,
            ),
            buttons=tuple(
                _plugp100_button(child, request_lock) for child in raw_buttons
            ),
            transport="standalone_plugp100",
            _close=async_close_owned,
        )
    except BaseException:
        if not session.closed:
            session.detach()
        raise


def _plugp100_button(
    child: TriggerButtonDevice, request_lock: asyncio.Lock
) -> ButtonConnection:
    async def async_fetch(page_size: int, start_id: int) -> Mapping[str, Any]:
        request = TapoRequest.get_child_event_logs(
            GetTriggerLogsParams(page_size=page_size, start_id=start_id)
        )
        async with request_lock:
            result = await child.client.control_child(child.device_id, request)
        response = result.get_or_raise()
        if not isinstance(response, Mapping):
            raise TypeError("trigger-log response is not an object")
        return response

    async def async_fetch_diagnostics() -> ButtonDiagnostics:
        async with request_lock:
            await child.update()
            raw_state = child.raw_state
            cloud_connected = None
            if DIAGNOSTIC_CLOUD_CONNECTION in diagnostic_features:
                cloud_response = (
                    await child.client.control_child(
                        child.device_id,
                        TapoRequest(method="get_connect_cloud_state", params=None),
                    )
                ).get_or_raise()
                status = cloud_response.get("status")
                cloud_connected = (
                    status == 0
                    if isinstance(status, int) and not isinstance(status, bool)
                    else None
                )
        return ButtonDiagnostics(
            cloud_connected=cloud_connected,
            signal_level=_optional_int(raw_state.get("signal_level")),
            battery_low=_optional_bool(
                raw_state.get("at_low_battery", raw_state.get("is_low"))
            ),
            rssi=_optional_int(raw_state.get("rssi")),
        )

    async def async_reboot() -> None:
        async with request_lock:
            (
                await child.client.control_child(
                    child.device_id,
                    TapoRequest(method="device_reboot", params={"delay": 1}),
                )
            ).get_or_raise()

    async def async_unpair() -> None:
        async with request_lock:
            (
                await child.client.execute_raw_request(
                    TapoRequest(
                        method="remove_child_device_list",
                        params={"child_device_list": [{"device_id": child.device_id}]},
                    )
                )
            ).get_or_raise()

    raw_state = child.raw_state
    diagnostic_features = {
        DIAGNOSTIC_REBOOT,
        DIAGNOSTIC_UNPAIR,
    }
    for feature_id, raw_key in (
        (DIAGNOSTIC_SIGNAL_LEVEL, "signal_level"),
        (DIAGNOSTIC_RSSI, "rssi"),
    ):
        if raw_key in raw_state:
            diagnostic_features.add(feature_id)
    if "at_low_battery" in raw_state or "is_low" in raw_state:
        diagnostic_features.add(DIAGNOSTIC_BATTERY_LOW)
    if child.components.has("cloud_connect"):
        diagnostic_features.add(DIAGNOSTIC_CLOUD_CONNECTION)

    return ButtonConnection(
        device_id=child.device_id,
        model=child.model,
        nickname=child.nickname,
        firmware_version=child.firmware_version,
        hardware_version=child.hardware_version,
        diagnostic_features=frozenset(diagnostic_features),
        _fetch_page=async_fetch,
        _fetch_diagnostics=async_fetch_diagnostics,
        _reboot=async_reboot,
        _unpair=async_unpair,
    )
