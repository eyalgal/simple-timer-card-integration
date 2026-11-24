"""Actions a timer runs when it finishes."""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, intent
from homeassistant.helpers.script import Script, async_validate_actions_config

from .const import DOMAIN
from .timers import TimerItem

# A non-admin may only schedule plain action calls, the same things they can
# call directly. Script steps such as event, wait_for_trigger or choose would
# let them do what Home Assistant keeps for admins (execute_script).
_PLAIN_STEP_KEYS = {"action", "service", "target", "data", "entity_id", "alias", "continue_on_error", "enabled", "metadata"}


def _plain_action_calls(actions: list[Any]) -> bool:
    return all(
        isinstance(step, dict) and ("action" in step or "service" in step) and not set(step) - _PLAIN_STEP_KEYS
        for step in actions
    )


async def async_action_denied(hass: HomeAssistant, user_id: str | None, actions: list[Any] | None) -> str | None:
    """Why this user may not run these actions, or None when allowed.

    Calls without a user (automations, voice satellites) are trusted, as in
    Home Assistant itself.
    """
    if user_id is None:
        return None
    user = await hass.auth.async_get_user(user_id)
    if user is None or not user.is_active:
        return "the user who started it is no longer active"
    if user.is_admin or not actions or _plain_action_calls(actions):
        return None
    return "only administrators can schedule script steps other than action calls"

_LOGGER = logging.getLogger(__name__)


async def async_command_is_understood(
    hass: HomeAssistant,
    command: str,
    *,
    language: str | None,
    context: Context | None,
    agent_id: str | None = None,
    device_id: str | None = None,
) -> bool:
    """Return whether the built-in assistant recognizes a command.

    Mirrors how Home Assistant validates delayed voice commands. Other
    conversation agents (for example LLM agents) are not checked.
    """
    from homeassistant.components.conversation import (  # noqa: PLC0415
        ConversationInput,
        async_get_agent,
        default_agent,
    )

    agent = async_get_agent(hass, agent_id)
    if agent is None or not isinstance(agent, default_agent.DefaultAgent):
        return True
    fields = {
        "text": command,
        "context": context or Context(),
        "conversation_id": None,
        "device_id": device_id,
        "language": language or hass.config.language,
        "agent_id": agent.entity_id,
    }
    # satellite_id is newer than some supported Home Assistant versions.
    if "satellite_id" in {f.name for f in dataclasses.fields(ConversationInput)}:
        fields["satellite_id"] = None
    test_input = ConversationInput(**fields)
    if await agent.async_recognize_sentence_trigger(test_input) is not None:
        return True
    result = await agent.async_recognize_intent(test_input)
    # Older versions return partial matches ("no area named ...") instead of
    # None; a command is only understood when everything in it matched.
    return result is not None and not getattr(result, "unmatched_entities", None)


async def async_build_action(
    hass: HomeAssistant,
    *,
    command: str | None,
    actions: list[dict[str, Any]] | None,
    language: str | None,
    context: Context | None,
    agent_id: str | None = None,
    device_id: str | None = None,
    validate_command: bool = True,
) -> dict[str, Any] | None:
    """Validate and normalize the action stored on a timer."""
    command = (command or "").strip()
    if command and actions:
        raise ServiceValidationError(
            "Use either a command or actions, not both",
            translation_domain=DOMAIN,
            translation_key="command_and_actions",
        )
    if command:
        if validate_command and not await async_command_is_understood(
            hass, command, language=language, context=context, agent_id=agent_id, device_id=device_id
        ):
            raise ServiceValidationError(
                f"Home Assistant did not understand: {command}",
                translation_domain=DOMAIN,
                translation_key="command_not_understood",
                translation_placeholders={"command": command},
            )
        action: dict[str, Any] = {"type": "command", "command": command, "language": language or hass.config.language}
        if agent_id:
            action["agent_id"] = agent_id
        if device_id:
            action["device_id"] = device_id
        return action
    if actions:
        validated = await async_validate_actions_config(hass, cv.SCRIPT_SCHEMA(actions))
        if await async_action_denied(hass, context.user_id if context else None, validated):
            raise ServiceValidationError(
                "Only administrators can schedule script steps other than action calls",
                translation_domain=DOMAIN,
                translation_key="actions_not_allowed",
            )
        return {"type": "actions", "actions": validated}
    return None


async def async_run_action(hass: HomeAssistant, timer: TimerItem) -> str:
    """Run a finished timer's action as the user who created it."""
    action = timer.action or {}
    # Checked again when it runs: the owner may have been removed or demoted.
    denied = await async_action_denied(hass, timer.user_id, action.get("actions") if action.get("type") == "actions" else None)
    if denied:
        _LOGGER.warning("Timer %s action skipped: %s", timer.name or timer.timer_id, denied)
        return f"skipped: {denied}"
    context = Context(user_id=timer.user_id)
    if action.get("type") == "command":
        from homeassistant.components.conversation import async_converse  # noqa: PLC0415

        result = await async_converse(
            hass,
            action["command"],
            conversation_id=None,
            context=context,
            language=action.get("language"),
            agent_id=action.get("agent_id"),
            device_id=action.get("device_id"),
        )
        response = result.response
        if response.response_type is intent.IntentResponseType.ERROR:
            speech = response.speech.get("plain", {}).get("speech", "")
            _LOGGER.warning("Timer command failed: %s (%s)", action["command"], speech)
            return f"error: {speech or response.error_code}"
        return "ok"
    if action.get("type") == "actions":
        script = Script(hass, action["actions"], f"Timer {timer.name or timer.timer_id}", DOMAIN)
        await script.async_run(context=context)
        return "ok"
    return "error: unknown action"
