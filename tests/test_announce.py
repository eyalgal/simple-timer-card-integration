"""Announce routing."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.simple_timer_card.const import DOMAIN

PIPELINE = "custom_components.simple_timer_card.announce._preferred_pipeline"


async def announce(hass: HomeAssistant, entity_id: str, message: str = "Pasta is done") -> None:
    await hass.services.async_call(DOMAIN, "announce", {"entity_id": entity_id, "message": message}, blocking=True)


async def test_satellites_announce_natively(hass: HomeAssistant, entry) -> None:
    calls = async_mock_service(hass, "assist_satellite", "announce")
    await announce(hass, "assist_satellite.kitchen")
    assert calls[0].data["entity_id"] in ("assist_satellite.kitchen", ["assist_satellite.kitchen"])
    assert calls[0].data["message"] == "Pasta is done"


async def test_alexa_speakers_use_alexa_announce(hass: HomeAssistant, entry, entity_registry: er.EntityRegistry) -> None:
    calls = async_mock_service(hass, "notify", "alexa_media")
    echo = entity_registry.async_get_or_create("media_player", "alexa_media", "echo1", suggested_object_id="kitchen_echo")
    await announce(hass, echo.entity_id)
    assert calls[0].data == {"message": "Pasta is done", "target": [echo.entity_id], "data": {"type": "announce"}}


async def test_other_speakers_use_the_pipeline_voice_and_skip_cloud_without_a_subscription(hass: HomeAssistant, entry) -> None:
    calls = async_mock_service(hass, "tts", "speak")
    hass.states.async_set("tts.home_assistant_cloud", "unknown")
    hass.states.async_set("tts.piper", "unknown")
    cloud_pipeline = SimpleNamespace(tts_engine="tts.home_assistant_cloud", tts_language="en-US", tts_voice="JennyNeural")
    piper_pipeline = SimpleNamespace(tts_engine="tts.piper", tts_language="en_US", tts_voice="en_US-hfc_female-medium")

    with patch(PIPELINE, return_value=piper_pipeline):
        await announce(hass, "media_player.garage")
    assert calls[-1].data["entity_id"] in ("tts.piper", ["tts.piper"])
    assert calls[-1].data["media_player_entity_id"] == "media_player.garage"
    assert calls[-1].data["options"] == {"voice": "en_US-hfc_female-medium"}

    with (
        patch(PIPELINE, return_value=cloud_pipeline),
        patch("custom_components.simple_timer_card.announce._cloud_active", return_value=False),
    ):
        await announce(hass, "media_player.garage")
    assert calls[-1].data["entity_id"] in ("tts.piper", ["tts.piper"]), "falls back from Cloud without a subscription"
    assert "options" not in calls[-1].data


async def test_no_voice_available_raises(hass: HomeAssistant, entry) -> None:
    with patch(PIPELINE, return_value=None):
        with pytest.raises(HomeAssistantError):
            await announce(hass, "media_player.garage")
