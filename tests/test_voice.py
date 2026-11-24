"""Capturing delayed voice commands."""

from unittest.mock import AsyncMock, patch

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component

from custom_components.simple_timer_card import voice
from custom_components.simple_timer_card.const import DOMAIN


def handler(hass: HomeAssistant):
    return voice._find_handler(hass, intent.INTENT_START_TIMER)


async def test_delayed_command_becomes_a_timer_and_the_handler_is_restored(hass: HomeAssistant, entry) -> None:
    assert await async_setup_component(hass, "intent", {})
    original = handler(hass)
    timer_list = hass.data[DOMAIN][entry.entry_id]
    assert voice.async_install(hass, timer_list)
    wrapper = handler(hass)
    assert isinstance(wrapper, voice.CapturingStartTimerIntentHandler)
    assert wrapper.original is original

    intent_obj = intent.Intent(
        hass, "test", intent.INTENT_START_TIMER,
        {"minutes": {"value": 10}, "conversation_command": {"value": "turn off the lights"}},
        "turn off the lights in 10 minutes", Context(user_id="abc"), "en",
    )
    with patch.object(type(wrapper), "_validate_conversation_command", new_callable=AsyncMock, return_value=True, create=True):
        await wrapper.async_handle(intent_obj)

    [timer] = timer_list.timers.values()
    assert timer.source == "voice"
    assert timer.action["command"] == "turn off the lights"
    assert timer.created_duration.total_seconds() == 600
    assert timer.user_id == "abc"

    voice.async_uninstall(hass)
    assert handler(hass) is original
