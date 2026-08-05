"""Shared entity support for child diagnostics and maintenance controls."""

from __future__ import annotations

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .connection import ButtonConnection, ButtonDiagnostics
from .const import DOMAIN
from .diagnostic_coordinator import TapoS200BDiagnosticCoordinator


class TapoChildDiagnosticEntity(CoordinatorEntity[TapoS200BDiagnosticCoordinator]):
    """Base for an opt-in entity attached to one S200B/S200D child."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: TapoS200BDiagnosticCoordinator,
        child: ButtonConnection,
        key: str,
    ) -> None:
        super().__init__(coordinator, context=child.device_id)
        self._child = child
        self._attr_unique_id = f"{child.device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, child.device_id)},
            manufacturer="TP-Link",
            model=child.model,
            name=child.nickname,
            sw_version=child.firmware_version,
            hw_version=child.hardware_version,
            via_device=(DOMAIN, coordinator.connection.hub.device_id),
        )

    @property
    def snapshot(self) -> ButtonDiagnostics | None:
        """Return the most recent values for this child."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._child.device_id)

    async def async_added_to_hass(self) -> None:
        """Fetch once when an operator explicitly enables an entity."""
        await super().async_added_to_hass()
        if self.coordinator.data is None:
            await self.coordinator.async_request_refresh()
