"""Shared fixtures."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.simple_timer_card.const import CONF_CAPTURE_VOICE, CONF_LATE_MINUTES, DOMAIN

LIST = "sensor.simple_timer_card_timers"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components."""
    yield


@pytest.fixture
def options() -> dict:
    """Entry options; tests override to change behaviour."""
    return {CONF_CAPTURE_VOICE: False, CONF_LATE_MINUTES: 10}


@pytest.fixture
async def entry(hass: HomeAssistant, options: dict) -> MockConfigEntry:
    """Set up the integration and return its entry."""
    assert await async_setup_component(hass, "homeassistant", {})
    config_entry = MockConfigEntry(domain=DOMAIN, title="Simple Timer Card", data={}, options=options)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def create(hass: HomeAssistant, context=None, **data) -> str:
    """Create a timer and return its id."""
    response = await hass.services.async_call(
        DOMAIN, "create_timer", {"entity_id": LIST, **data}, blocking=True, return_response=True, context=context
    )
    return response[LIST]["timer_id"]


def timers(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    """The integration's timers by id."""
    return hass.data[DOMAIN][entry.entry_id].timers
