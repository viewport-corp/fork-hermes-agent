"""Anthropic CLI runtime — drives one turn through ``claude -p``.

Sibling of ``agent/codex_runtime.run_codex_app_server_turn``: a subprocess
runtime that hands the whole turn to a local CLI instead of Hermes' own
HTTP tool-dispatch loop.  Dispatched from ``agent.conversation_loop`` when
``agent.api_mode == "anthropic_cli"``.

``claude -p`` runs its own internal agentic loop and returns a single
completed result, so there is no per-iteration tool dispatch here — one
``claude -p`` invocation maps to one logical turn / API call.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _normalized_model(agent) -> str:
    """Resolve the model id to pass to ``claude --model``.

    Prefer the agent's already-normalized model; fall back to normalizing
    whatever is configured for the ``anthropic-cli`` provider.
    """
    model = getattr(agent, "model", "") or ""
    if model:
        return model
    try:
        from hermes_cli.model_normalize import normalize_model_for_provider

        return normalize_model_for_provider(model, "anthropic-cli")
    except Exception:
        return model


def run_anthropic_cli_turn(
    agent,
    *,
    user_message: str,
    original_user_message: Any,
    messages: List[Dict[str, Any]],
    effective_task_id: str,
    should_review_memory: bool = False,
) -> Dict[str, Any]:
    """Run one ``claude -p`` turn and project its result into ``messages``.

    Called from run_conversation() when agent.api_mode == "anthropic_cli".
    Returns the same dict shape as the chat_completions / codex_app_server
    paths.

    On any CLI failure an ``AnthropicCliError`` is raised inside
    ``run_claude_cli_turn`` and caught here; we return a partial/failed
    result dict (``completed=False``) so the surrounding fallback machinery
    can switch to another provider, matching the codex app-server contract.
    """
    from agent.transports.anthropic_cli_session import (
        AnthropicCliError,
        run_claude_cli_turn,
    )

    # NOTE: the user message is ALREADY appended to messages by the standard
    # run_conversation() flow before this early-return path is reached. Do
    # NOT append again — that would duplicate (mirrors the codex path).

    config = None
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly()
    except Exception:
        logger.debug("anthropic-cli: config load skipped", exc_info=True)

    cwd = getattr(agent, "session_cwd", None) or os.getcwd()
    model = _normalized_model(agent)

    try:
        turn = run_claude_cli_turn(
            user_input=user_message,
            model=model,
            config=config,
            cwd=cwd,
        )
    except AnthropicCliError as exc:
        logger.warning(
            "claude CLI turn failed: %s (stderr tail: %s)",
            exc,
            getattr(exc, "stderr_tail", ""),
        )
        return {
            "final_response": (
                f"claude CLI turn failed: {exc}. "
                f"Fall back to another provider (e.g. `/model`)."
            ),
            "messages": messages,
            "api_calls": 0,
            "completed": False,
            "partial": True,
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("claude CLI turn raised unexpectedly")
        return {
            "final_response": f"claude CLI turn error: {exc}.",
            "messages": messages,
            "api_calls": 0,
            "completed": False,
            "partial": True,
            "error": str(exc),
        }

    # Splice the assistant message into the conversation. The session emits a
    # standard {role, content} entry, which is what curator.py / the sessions
    # DB expect.
    if turn.projected_messages:
        messages.extend(turn.projected_messages)

    # Skill-nudge counter — same accounting the codex app-server path does.
    agent._iters_since_skill = (
        getattr(agent, "_iters_since_skill", 0) + max(turn.tool_iterations, 1)
    )
    should_review_skills = False
    if (
        getattr(agent, "_skill_nudge_interval", 0) > 0
        and agent._iters_since_skill >= agent._skill_nudge_interval
        and "skill_manage" in getattr(agent, "valid_tool_names", set())
    ):
        should_review_skills = True
        agent._iters_since_skill = 0

    # External memory sync — mirrors the codex/chat paths; skipped on error.
    if turn.error is None:
        try:
            agent._sync_external_memory_for_turn(
                original_user_message=original_user_message,
                final_response=turn.final_text,
                interrupted=False,
            )
        except Exception:
            logger.debug("external memory sync raised", exc_info=True)

    # Background review fork — same cadence + signature as the default path.
    if turn.final_text and (should_review_memory or should_review_skills):
        try:
            agent._spawn_background_review(
                messages_snapshot=list(messages),
                review_memory=should_review_memory,
                review_skills=should_review_skills,
            )
        except Exception:
            logger.debug("background review spawn raised", exc_info=True)

    return {
        "final_response": turn.final_text,
        "messages": messages,
        "api_calls": 1,  # one ``claude -p`` invocation == one logical API call
        "completed": True,
        "partial": False,
        "error": None,
        "anthropic_cli_session_id": turn.session_id,
        "anthropic_cli_total_cost_usd": turn.total_cost_usd,
    }


__all__ = ["run_anthropic_cli_turn"]
