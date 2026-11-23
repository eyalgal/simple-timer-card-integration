"""Timer lifecycle, actions and persistence."""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.simple_timer_card.const import CONF_CAPTURE_VOICE, CONF_LATE_MINUTES, DOMAIN, EVENT_TIMER

from .conftest import LIST, create, timers

RECORD = [{"action": "test.record", "data": {"who": "timer"}}]


async def test_entity_exposes_the_card_api(hass: HomeAssistant, entry) -> None:
    state = hass.states.get(LIST)
    assert state.state == "0"
    assert state.attributes["simple_timer_card_api"] == 1
    assert state.attributes["supported_features"] == 255


async def test_finished_timer_runs_its_actions_as_the_creator(hass: HomeAssistant, entry, hass_admin_user) -> None:
    calls = async_mock_service(hass, "test", "record")
    events = async_capture_events(hass, EVENT_TIMER)
    timer_id = await create(hass, Context(user_id=hass_admin_user.id), duration={"seconds": 5}, name="Tea", actions=RECORD)
    assert hass.states.get(LIST).state == "1"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].context.user_id == hass_admin_user.id
    timer = timers(hass, entry)[timer_id]
    assert timer.status == "finished"
    assert timer.action_result == "ok"
    assert [e.data["event"] for e in events] == ["created", "finished", "action_ran"]
    assert hass.states.get(LIST).state == "0"


async def test_pause_add_time_and_cancel_do_not_run_the_action(hass: HomeAssistant, entry) -> None:
    calls = async_mock_service(hass, "test", "record")
    timer_id = await create(hass, duration={"minutes": 10}, actions=RECORD)
    call = lambda service, **data: hass.services.async_call(  # noqa: E731
        DOMAIN, service, {"entity_id": LIST, "timer_id": timer_id, **data}, blocking=True
    )

    await call("pause_timer")
    timer = timers(hass, entry)[timer_id]
    assert timer.status == "paused"
    await call("add_time", duration={"minutes": 5})
    assert timer.paused_remaining.total_seconds() == pytest.approx(15 * 60, abs=2)
    await call("unpause_timer")
    assert timer.status == "active"
    await call("cancel_timer")
    assert timer.status == "cancelled"

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=20))
    await hass.async_block_till_done()
    assert calls == []


async def test_finish_now_and_restart(hass: HomeAssistant, entry) -> None:
    calls = async_mock_service(hass, "test", "record")
    timer_id = await create(hass, duration={"minutes": 10}, actions=RECORD)
    await hass.services.async_call(DOMAIN, "finish_timer", {"entity_id": LIST, "timer_id": timer_id}, blocking=True)
    await hass.async_block_till_done()
    assert len(calls) == 1

    await hass.services.async_call(
        DOMAIN, "restart_timer", {"entity_id": LIST, "timer_id": timer_id, "duration": {"minutes": 1}}, blocking=True
    )
    timer = timers(hass, entry)[timer_id]
    assert timer.status == "active"
    assert timer.action_result is None


async def test_invalid_requests_are_rejected(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError):
        await create(hass, duration={"seconds": 0})
    with pytest.raises(ServiceValidationError):
        await create(hass, duration={"minutes": 1}, command="turn off the lights", actions=RECORD)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "pause_timer", {"entity_id": LIST, "timer_id": "nope"}, blocking=True)


async def test_unknown_command_is_rejected(hass: HomeAssistant, entry) -> None:
    assert await __import__("homeassistant.setup", fromlist=["x"]).async_setup_component(hass, "conversation", {})
    with pytest.raises(ServiceValidationError):
        await create(hass, duration={"minutes": 1}, command="flibber the wobbleglorp")


async def test_get_timers_filters_by_status(hass: HomeAssistant, entry) -> None:
    keep = await create(hass, duration={"minutes": 5}, name="Keep")
    gone = await create(hass, duration={"minutes": 5}, name="Gone")
    await hass.services.async_call(DOMAIN, "cancel_timer", {"entity_id": LIST, "timer_id": gone}, blocking=True)
    response = await hass.services.async_call(
        DOMAIN, "get_timers", {"entity_id": LIST, "status": ["active"]}, blocking=True, return_response=True
    )
    assert [t["timer_id"] for t in response[LIST]["timers"]] == [keep]


async def test_timers_survive_a_restart_and_late_actions_run_within_the_window(
    hass: HomeAssistant, hass_storage
) -> None:
    from homeassistant.setup import async_setup_component

    calls = async_mock_service(hass, "test", "record")
    now = dt_util.utcnow()
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={CONF_CAPTURE_VOICE: False, CONF_LATE_MINUTES: 10})
    def stored(timer_id, finishes_at, status="active"):
        return {
            "timer_id": timer_id, "name": timer_id, "status": status, "created_duration": 600, "total_duration": 600,
            "created_at": (now - timedelta(hours=2)).isoformat(), "finishes_at": finishes_at.isoformat(),
            "action": {"type": "actions", "actions": RECORD}, "metadata": {}, "source": "service",
        }
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1, "minor_version": 1, "key": f"{DOMAIN}.{entry.entry_id}",
        "data": {"timers": [
            stored("running", now + timedelta(minutes=5)),
            stored("late", now - timedelta(minutes=3)),
            stored("too_late", now - timedelta(hours=1)),
        ]},
    }
    assert await async_setup_component(hass, "homeassistant", {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_start()
    await hass.async_block_till_done()

    restored = timers(hass, entry)
    assert restored["running"].status == "active"
    assert restored["late"].status == "finished"
    assert restored["too_late"].action_result.startswith("skipped")
    assert len(calls) == 1, "only the timer that ended within the late window runs its action"
