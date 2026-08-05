"""Disabled-by-default maintenance buttons for Tapo child controls."""

from __future__ import annotations

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TapoS200BConfigEntry
from .connection import ButtonConnection
from .const import DIAGNOSTIC_REBOOT, DIAGNOSTIC_UNPAIR
from .diagnostic_coordinator import TapoS200BDiagnosticCoordinator
from .entity import TapoChildDiagnosticEntity

BUTTON_DESCRIPTIONS = (
    ButtonEntityDescription(
        key=DIAGNOSTIC_REBOOT,
        translation_key=DIAGNOSTIC_REBOOT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        device_class=ButtonDeviceClass.RESTART,
    ),
    ButtonEntityDescription(
        key=DIAGNOSTIC_UNPAIR,
        translation_key=DIAGNOSTIC_UNPAIR,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    _hass,
    entry: TapoS200BConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Register supported opt-in maintenance buttons."""
    runtime = entry.runtime_data
    async_add_entities(
        TapoMaintenanceButton(runtime.diagnostic_coordinator, child, description)
        for child in runtime.connection.buttons
        for description in BUTTON_DESCRIPTIONS
        if description.key in child.diagnostic_features
    )


class TapoMaintenanceButton(TapoChildDiagnosticEntity, ButtonEntity):
    """Invoke one explicit maintenance action on a child."""

    entity_description: ButtonEntityDescription

    def __init__(
        self,
        coordinator: TapoS200BDiagnosticCoordinator,
        child: ButtonConnection,
        description: ButtonEntityDescription,
    ) -> None:
        self.entity_description = description
        super().__init__(coordinator, child, description.key)

    async def async_press(self) -> None:
        """Run only the action selected by this entity."""
        if self.entity_description.key == DIAGNOSTIC_REBOOT:
            await self._child.async_reboot()
            return
        await self._child.async_unpair()
