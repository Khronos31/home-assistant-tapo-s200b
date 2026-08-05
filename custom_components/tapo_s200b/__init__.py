"""Set up the Tapo S200B/S200D integration."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from plugp100.errors import InvalidAuthentication

from .connection import HubConnection, async_get_connection
from .const import CONF_EMAIL, DOMAIN, PLATFORMS
from .coordinator import TapoS200BCoordinator
from .diagnostic_coordinator import TapoS200BDiagnosticCoordinator


@dataclass(slots=True)
class TapoS200BRuntimeData:
    """Runtime objects owned by one hub config entry."""

    connection: HubConnection
    coordinator: TapoS200BCoordinator
    diagnostic_coordinator: TapoS200BDiagnosticCoordinator


type TapoS200BConfigEntry = ConfigEntry[TapoS200BRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: TapoS200BConfigEntry) -> bool:
    """Connect, bootstrap cursors, and set up event entities."""
    try:
        connection = await async_get_connection(
            hass,
            entry.data[CONF_HOST],
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
        )
    except InvalidAuthentication as err:
        raise ConfigEntryAuthFailed from err
    except Exception as err:
        raise ConfigEntryNotReady from err

    try:
        registry = dr.async_get(hass)
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
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await connection.async_close()
        raise
    return True


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
