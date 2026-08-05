"""Event entities for Tapo S200B/S200D controls."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import TapoS200BConfigEntry
from .connection import ButtonConnection
from .const import DOMAIN, EVENT_TYPES
from .coordinator import TapoS200BCoordinator
from .models import EventEmission


async def async_setup_entry(
    _hass,
    entry: TapoS200BConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one event entity per supported physical control."""
    runtime = entry.runtime_data
    async_add_entities(
        TapoButtonEvent(runtime.coordinator, child)
        for child in runtime.connection.buttons
    )
    runtime.coordinator.async_enable_context_filtering()


class TapoButtonEvent(CoordinatorEntity[TapoS200BCoordinator], EventEntity):
    """Click and 30-degree rotation events from one S200B/S200D."""

    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types: ClassVar[list[str]] = list(EVENT_TYPES)
    _attr_has_entity_name = True
    _attr_name = "Event"

    def __init__(
        self, coordinator: TapoS200BCoordinator, child: ButtonConnection
    ) -> None:
        super().__init__(coordinator, context=child.device_id)
        self._child = child
        self._publishing = False
        self._attr_unique_id = f"{child.device_id}_event"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, child.device_id)},
            manufacturer="TP-Link",
            model=child.model,
            name=child.nickname,
            sw_version=child.firmware_version,
            hw_version=child.hardware_version,
            via_device=(DOMAIN, coordinator.connection.hub.device_id),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to persisted events for this child."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_child_listener(
                self._child.device_id, self._handle_child_emissions
            )
        )

    def _handle_child_emissions(self, emissions: tuple[EventEmission, ...]) -> None:
        """Publish every persisted event step immediately."""
        self._publishing = True
        try:
            for emission in emissions:
                self._trigger_event(emission.event_type, emission.data or None)
                self.async_write_ha_state()
        finally:
            self._publishing = False

    @property
    def available(self) -> bool:
        """Expose a successfully fetched event after a previous poll failure."""
        return self._publishing or super().available

    def _handle_coordinator_update(self) -> None:
        """Update availability without replaying already-published events."""
        super()._handle_coordinator_update()
