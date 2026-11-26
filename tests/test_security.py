"""Permission and limit checks."""

from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockUser, async_fire_time_changed, async_mock_service

from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util import dt as dt_util

from custom_components.simple_timer_card.const import DOMAIN, MAX_TIMERS

from .conftest import LIST, create, timers

CALL = [{"action": "test.record", "target": {"entity_id": "light.kitchen"}, "data": {"secret": "s3cr3t"}}]
EVENT = [{"event": "tag_scanned", "event_data": {"tag_id": "front-door"}}]


async def user(hass: HomeAssistant, admin: bool) -> MockUser:
    groups = [await hass.auth.async_get_group(GROUP_ID_ADMIN if admin else GROUP_ID_USER)]
    return MockUser(groups=groups).add_to_hass(hass)


async def test_non_admins_may_only_schedule_action_calls(hass: HomeAssistant, entry) -> None:
    member = await user(hass, admin=False)
    owner = await user(hass, admin=True)
    async_mock_service(hass, "test", "record")
    await create(hass, Context(user_id=member.id), duration={"minutes": 1}, actions=CALL)
    with pytest.raises(ServiceValidationError):
        await create(hass, Context(user_id=member.id), duration={"minutes": 1}, actions=EVENT)
    await create(hass, Context(user_id=owner.id), duration={"minutes": 1}, actions=EVENT)
    await create(hass, Context(), duration={"minutes": 1}, actions=EVENT)


async def test_actions_are_skipped_when_the_owner_is_gone_or_demoted(hass: HomeAssistant, entry) -> None:
    calls = async_mock_service(hass, "test", "record")
    fired = []
    hass.bus.async_listen("tag_scanned", fired.append)
    leaver = await user(hass, admin=False)
    admin = await user(hass, admin=True)
    gone = await create(hass, Context(user_id=leaver.id), duration={"seconds": 5}, actions=CALL)
    demoted = await create(hass, Context(user_id=admin.id), duration={"seconds": 5}, actions=EVENT)
    leaver.is_active = False
    admin.groups = []
    assert not admin.is_admin

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert calls == [] and fired == []
    assert timers(hass, entry)[gone].action_result.startswith("skipped")
    assert timers(hass, entry)[demoted].action_result.startswith("skipped")


async def test_an_admin_finishing_a_timer_does_not_lend_their_rights(hass: HomeAssistant, entry) -> None:
    calls = []

    async def stop(call):
        calls.append(call)

    async_register_admin_service(hass, "test", "stop", stop)
    admin = await user(hass, admin=True)
    member = await user(hass, admin=False)
    timer_id = await create(hass, Context(user_id=member.id), duration={"minutes": 5}, actions=[{"action": "test.stop"}])

    for service in ("restart_timer", "finish_timer"):
        await hass.services.async_call(
            DOMAIN, service, {"entity_id": LIST, "timer_id": timer_id}, blocking=True, context=Context(user_id=admin.id)
        )
        await hass.async_block_till_done()
        assert timers(hass, entry)[timer_id].user_id == member.id, f"{service} keeps the creator as the owner"
    assert calls == []
    assert timers(hass, entry)[timer_id].action_result.startswith("error")


async def test_finishing_someone_elses_timer_needs_both_to_be_allowed(hass: HomeAssistant, entry) -> None:
    calls = async_mock_service(hass, "test", "record")
    fired = []
    hass.bus.async_listen("tag_scanned", fired.append)
    admin = await user(hass, admin=True)
    member = await user(hass, admin=False)
    script = await create(hass, Context(user_id=admin.id), duration={"minutes": 5}, actions=EVENT)
    call = await create(hass, Context(user_id=admin.id), duration={"minutes": 5}, actions=CALL)

    subtracted = await create(hass, Context(user_id=admin.id), duration={"minutes": 5}, actions=EVENT)
    for timer_id in (script, call):
        await hass.services.async_call(
            DOMAIN, "finish_timer", {"entity_id": LIST, "timer_id": timer_id}, blocking=True, context=Context(user_id=member.id)
        )
    await hass.services.async_call(
        DOMAIN, "subtract_time", {"entity_id": LIST, "timer_id": subtracted, "duration": {"minutes": 10}},
        blocking=True, context=Context(user_id=member.id),
    )
    await hass.async_block_till_done()
    assert fired == [], "a non-admin finishing an admin's script timer does not borrow the admin's rights"
    assert timers(hass, entry)[script].action_result.startswith("skipped: the user who finished it")
    assert timers(hass, entry)[subtracted].action_result.startswith("skipped: the user who finished it"), "so does subtract_time"
    assert [c.context.user_id for c in calls] == [member.id], "the call runs with the less privileged user's rights"
    assert timers(hass, entry)[script].user_id == admin.id

    own = await create(hass, Context(user_id=admin.id), duration={"minutes": 5}, actions=EVENT)
    await hass.services.async_call(
        DOMAIN, "finish_timer", {"entity_id": LIST, "timer_id": own}, blocking=True, context=Context(user_id=admin.id)
    )
    await hass.async_block_till_done()
    assert len(fired) == 1, "the owner can still finish their own timer early"


async def test_restart_does_not_revive_a_cancelled_timer(hass: HomeAssistant, entry) -> None:
    cancelled = await create(hass, duration={"minutes": 5})
    await hass.services.async_call(DOMAIN, "cancel_timer", {"entity_id": LIST, "timer_id": cancelled}, blocking=True)
    await hass.services.async_call(DOMAIN, "restart_timer", {"entity_id": LIST, "timer_id": cancelled}, blocking=True)
    assert timers(hass, entry)[cancelled].status == "cancelled"


async def test_service_data_stays_on_the_server(hass: HomeAssistant, entry) -> None:
    async_mock_service(hass, "test", "record")
    timer_id = await create(hass, duration={"minutes": 1}, actions=CALL)
    public = timers(hass, entry)[timer_id].as_dict(dt_util.utcnow())["action"]["actions"][0]
    assert public["action"] == "test.record"
    assert public["target"] == {"entity_id": ["light.kitchen"]}
    assert "data" not in public
    announce = [{"action": "simple_timer_card.announce", "data": {"entity_id": "media_player.garage", "message": "hi"}}]
    timer_id = await create(hass, duration={"minutes": 1}, actions=announce)
    public = timers(hass, entry)[timer_id].as_dict(dt_util.utcnow())["action"]["actions"][0]
    assert public["data"] == {"entity_id": "media_player.garage"}


async def test_limits(hass: HomeAssistant, entry) -> None:
    with pytest.raises(ServiceValidationError):
        await create(hass, duration={"days": 400})
    with pytest.raises(ServiceValidationError):
        await create(hass, duration={"minutes": 1}, metadata={"blob": "x" * 5000})
    for _ in range(MAX_TIMERS):
        await create(hass, duration={"minutes": 1})
    with pytest.raises(ServiceValidationError):
        await create(hass, duration={"minutes": 1})
