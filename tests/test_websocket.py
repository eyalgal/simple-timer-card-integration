"""Websocket API and permissions."""

from homeassistant.core import HomeAssistant

from .conftest import LIST, create


async def test_subscribe_sends_snapshot_then_changes(hass: HomeAssistant, entry, hass_ws_client) -> None:
    existing = await create(hass, duration={"minutes": 5}, name="Existing")
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "simple_timer_card/item/subscribe", "entity_id": LIST})
    assert (await client.receive_json())["success"]
    snapshot = (await client.receive_json())["event"]
    assert snapshot["type"] == "timers"
    assert [t["timer_id"] for t in snapshot["timers"]] == [existing]

    new = await create(hass, duration={"minutes": 1}, name="New")
    change = (await client.receive_json())["event"]
    assert change["type"] == "change"
    assert change["event_type"] == "created"
    assert change["timer"]["timer_id"] == new


async def test_list_and_subscribe_need_read_permission(hass: HomeAssistant, entry, hass_ws_client, hass_admin_user) -> None:
    hass_admin_user.groups = []
    hass_admin_user.mock_policy({"entities": {"entity_ids": {LIST: False}}})
    client = await hass_ws_client(hass)
    for command in ("simple_timer_card/item/subscribe", "simple_timer_card/item/list"):
        await client.send_json_auto_id({"type": command, "entity_id": LIST})
        response = await client.receive_json()
        assert not response["success"]
        assert response["error"]["code"] == "unauthorized"


async def test_unknown_list_is_not_found(hass: HomeAssistant, entry, hass_ws_client) -> None:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "simple_timer_card/item/list", "entity_id": "sensor.other"})
    response = await client.receive_json()
    assert response["error"]["code"] == "not_found"
