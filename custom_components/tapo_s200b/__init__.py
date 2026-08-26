"""Set up the Tapo S200B/S200D integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from plugp100.errors import InvalidAuthentication

from .connection import HubConnection, async_get_connection
from .const import CONF_EMAIL, CONF_MAC, DOMAIN, PLATFORMS
from .coordinator import TapoS200BCoordinator
from .diagnostic_coordinator import TapoS200BDiagnosticCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class TapoS200BRuntimeData:
    """Runtime objects owned by one hub config entry."""

    connection: HubConnection
    coordinator: TapoS200BCoordinator
    diagnostic_coordinator: TapoS200BDiagnosticCoordinator


type TapoS200BConfigEntry = ConfigEntry[TapoS200BRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: TapoS200BConfigEntry) -> bool:
    """Connect, bootstrap cursors, and set up event entities."""
    registry = dr.async_get(hass)
    expected_mac = _entry_mac(registry, entry)
    try:
        connection = await async_get_connection(
            hass,
            entry.data[CONF_HOST],
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            expected_device_id=entry.unique_id,
            expected_mac=expected_mac,
        )
    except InvalidAuthentication as err:
        raise ConfigEntryAuthFailed from err
    except Exception as err:
        raise ConfigEntryNotReady from err

    try:
        updated_data = dict(entry.data)
        previous_host = entry.data[CONF_HOST]
        resolved_host = getattr(connection.hub, "host", None)
        if resolved_host and resolved_host != previous_host:
            updated_data[CONF_HOST] = resolved_host
        formatted_mac = dr.format_mac(connection.hub.mac)
        if formatted_mac != entry.data.get(CONF_MAC):
            updated_data[CONF_MAC] = formatted_mac

        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            connections={
                (dr.CONNECTION_NETWORK_MAC, dr.format_mac(connection.hub.mac))
            },
            identifiers={(DOMAIN, connection.hub.device_id)},
            manufacturer="TP-Link",
            model=connection.hub.model,
            name=connection.hub.nickname,
            sw_version=connection.hub.firmware_version,
            hw_version=connection.hub.hardware_version,
        )

        coordinator = TapoS200BCoordinator(hass, entry, connection)
        await coordinator.async_load_cursors()
        await coordinator.async_config_entry_first_refresh()
        diagnostic_coordinator = TapoS200BDiagnosticCoordinator(hass, entry, connection)
        entry.runtime_data = TapoS200BRuntimeData(
            connection, coordinator, diagnostic_coordinator
        )

        # Save a repaired address only after the authenticated hub has returned
        # its first event-log update. Add the listener afterwards so our own
        # update cannot cause a reload loop.
        if updated_data != entry.data:
            if resolved_host and resolved_host != previous_host:
                _LOGGER.info(
                    "Discovered Tapo hub %s at new address %s (was %s)",
                    entry.title,
                    resolved_host,
                    previous_host,
                )
            hass.config_entries.async_update_entry(entry, data=updated_data)

        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await connection.async_close()
        raise
    return True


def _entry_mac(registry: dr.DeviceRegistry, entry: TapoS200BConfigEntry) -> str | None:
    """Return a stored MAC, backfilling legacy entries from the registry."""
    if stored_mac := entry.data.get(CONF_MAC):
        return stored_mac
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if (DOMAIN, entry.unique_id) not in device.identifiers:
            continue
        for connection_type, value in device.connections:
            if connection_type == dr.CONNECTION_NETWORK_MAC:
                return value
    return None


async def async_unload_entry(hass: HomeAssistant, entry: TapoS200BConfigEntry) -> bool:
    """Unload entities and close protocol state."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.connection.async_close()
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: TapoS200BConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
