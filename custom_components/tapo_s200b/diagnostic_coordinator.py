"""Listener-driven, low-frequency diagnostics for supported child controls."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .connection import ButtonDiagnostics, HubConnection
from .const import DIAGNOSTIC_POLL_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class TapoS200BDiagnosticCoordinator(
    DataUpdateCoordinator[dict[str, ButtonDiagnostics]]
):
    """Refresh diagnostics only while at least one disabled-by-default entity is on."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        connection: HubConnection,
    ) -> None:
        self.connection = connection
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_diagnostics",
            update_interval=timedelta(seconds=DIAGNOSTIC_POLL_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, ButtonDiagnostics]:
        diagnostics: dict[str, ButtonDiagnostics] = {}
        for child in self.connection.buttons:
            if not child.diagnostic_features:
                continue
            try:
                diagnostics[child.device_id] = await child.async_get_diagnostics()
            except Exception:
                _LOGGER.warning(
                    "Failed to read diagnostics from child model %s", child.model
                )

        if not diagnostics:
            raise UpdateFailed("All child diagnostic reads failed")
        return diagnostics
