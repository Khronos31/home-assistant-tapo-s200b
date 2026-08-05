"""Tests for Event Entity delivery behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.tapo_s200b.const import (
    EVENT_ROTATE_RIGHT,
    EVENT_SINGLE_CLICK,
)
from custom_components.tapo_s200b.event import TapoButtonEvent
from custom_components.tapo_s200b.models import EventEmission


def test_entity_publishes_every_rotation_step_from_child_delivery() -> None:
    child = SimpleNamespace(
        device_id="child-1",
        model="S200D",
        nickname="Dial",
        firmware_version="1.0",
        hardware_version="1.0",
    )
    emissions = tuple(
        EventEmission(EVENT_ROTATE_RIGHT, {"step_index": index, "step_count": 4})
        for index in range(1, 5)
    )
    coordinator = SimpleNamespace(
        data={"child-1": emissions},
        connection=SimpleNamespace(hub=SimpleNamespace(device_id="hub-1")),
    )
    entity = TapoButtonEvent(coordinator, child)
    entity._trigger_event = Mock()
    entity.async_write_ha_state = Mock()

    entity._handle_child_emissions(emissions)

    assert entity._trigger_event.call_count == 4
    assert [call.args[0] for call in entity._trigger_event.call_args_list] == [
        EVENT_ROTATE_RIGHT,
    ] * 4
    assert entity.async_write_ha_state.call_count == 4


def test_regular_coordinator_update_does_not_replay_event() -> None:
    child = SimpleNamespace(
        device_id="child-1",
        model="S200D",
        nickname="Dial",
        firmware_version="1.0",
        hardware_version="1.0",
    )
    coordinator = SimpleNamespace(
        data={"child-1": (EventEmission(EVENT_SINGLE_CLICK),)},
        connection=SimpleNamespace(hub=SimpleNamespace(device_id="hub-1")),
    )
    entity = TapoButtonEvent(coordinator, child)
    entity._trigger_event = Mock()
    entity.async_write_ha_state = Mock()

    entity._handle_coordinator_update()

    entity._trigger_event.assert_not_called()
    entity.async_write_ha_state.assert_called_once()


async def test_child_listener_follows_entity_lifecycle(hass) -> None:
    remove_coordinator_listener = Mock()
    remove_child_listener = Mock()
    child = SimpleNamespace(
        device_id="child-1",
        model="S200B",
        nickname="Button",
        firmware_version="1.0",
        hardware_version="1.0",
    )
    coordinator = SimpleNamespace(
        data={},
        connection=SimpleNamespace(hub=SimpleNamespace(device_id="hub-1")),
        async_add_listener=Mock(return_value=remove_coordinator_listener),
        async_add_child_listener=Mock(return_value=remove_child_listener),
    )
    entity = TapoButtonEvent(coordinator, child)
    entity.hass = hass

    await entity.async_added_to_hass()
    entity._call_on_remove_callbacks()

    coordinator.async_add_child_listener.assert_called_once_with(
        "child-1", entity._handle_child_emissions
    )
    remove_coordinator_listener.assert_called_once()
    remove_child_listener.assert_called_once()


def test_delivery_is_available_after_previous_coordinator_failure() -> None:
    child = SimpleNamespace(
        device_id="child-1",
        model="S200B",
        nickname="Button",
        firmware_version="1.0",
        hardware_version="1.0",
    )
    coordinator = SimpleNamespace(
        data={},
        last_update_success=False,
        connection=SimpleNamespace(hub=SimpleNamespace(device_id="hub-1")),
    )
    entity = TapoButtonEvent(coordinator, child)
    availability_during_write: list[bool] = []
    entity._trigger_event = Mock()
    entity.async_write_ha_state = Mock(
        side_effect=lambda: availability_during_write.append(entity.available)
    )

    entity._handle_child_emissions((EventEmission(EVENT_SINGLE_CLICK),))

    assert availability_during_write == [True]
    assert entity.available is False


def test_entity_supports_click_and_rotation_types() -> None:
    child = SimpleNamespace(
        device_id="child-1",
        model="S200B",
        nickname="Button",
        firmware_version="1.0",
        hardware_version="1.0",
    )
    coordinator = SimpleNamespace(
        data={"child-1": (EventEmission(EVENT_SINGLE_CLICK),)},
        connection=SimpleNamespace(hub=SimpleNamespace(device_id="hub-1")),
    )
    entity = TapoButtonEvent(coordinator, child)

    assert set(entity.event_types) == {
        "single_click",
        "double_click",
        "rotate_left",
        "rotate_right",
    }
