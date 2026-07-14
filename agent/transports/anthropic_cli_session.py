"""Anthropic CLI transport — spawns the real ``claude -p`` binary.

This is the subprocess sibling of the Anthropic Messages HTTP transport
(``agent/transports/anthropic.py``).  Instead of talking to
``api.anthropic.com`` over HTTPS with an API key, it shells out to a local
Claude Code CLI install and drives it in non-interactive "print" mode::

    claude -p "<prompt>" --output-format json --model <model>

The CLI authenticates off the ``CLAUDE_CODE_OAUTH_TOKEN`` environment
variable (a long-lived Max-subscription setup token), which is injected into
the child process environment.  No API key is involved, so this path lets a
Hermes deployment ride a Claude *subscription* rather than metered API
billing.

``claude -p ... --output-format json`` emits a single JSON object on stdout::

    {
      "type": "result",
      "subtype": "success",
      "is_error": false,
      "result": "<assistant text>",
      "usage": {"input_tokens": .., "output_tokens": .., ...},
      "modelUsage": {"<model>": {"inputTokens": .., "outputTokens": .., ...}},
      "stop_reason": "end_turn",
      "total_cost_usd": 0.04,
      ...
    }

The assistant's user-facing text is ``result``.  ``is_error: true`` (or a
non-zero exit, or unparseable stdout) is surfaced as an
:class:`AnthropicCliError` so the caller can fall back to another provider.

Structurally this mirrors ``agent/transports/codex_app_server_session.py``
but is far simpler: ``claude -p`` runs its own agentic loop internally and
returns a single completed result, so there is no persistent JSON-RPC server,
no handshake, and no per-turn tool projection — one ``subprocess.run`` per
turn.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default location of the bundled Claude Code CLI binary on the Viewport VPS.
# Overridable via the ``HERMES_CLAUDE_CLI_BIN`` env var or the
# ``model.anthropic_cli_bin`` config key (see _resolve_claude_bin).
_DEFAULT_CLAUDE_BIN = (
    "/srv/viewport/runtime/openclaw-fresh/claude-cli/claude-code/bin/claude.exe"
)

# Env var the ``claude`` CLI reads for its long-lived OAuth setup token.
_OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"

# How long (seconds) to let a single ``claude -p`` invocation run before
# giving up.  ``claude -p`` can run a multi-step internal loop, so this is
# generous; overridable via HERMES_CLAUDE_CLI_TIMEOUT.
_DEFAULT_TIMEOUT_SECONDS = 600.0

# How many tailing stderr lines to attach to an error for debugging.
_STDERR_TAIL_LINES = 20


class AnthropicCliError(RuntimeError):
    """Raised when the ``claude -p`` invocation fails.

    Carries the exit code (when the failure was a non-zero exit) and a tail
    of stderr so the caller's error classifier / logs see the real reason
    (auth expired, model unavailable, quota, etc.).
    """

    def __init__(
        self,
        message: str,
        *,
        returncode: Optional[int] = None,
        stderr_tail: str = "",
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


@dataclass
class AnthropicCliTurnResult:
    """Result of one ``claude -p`` invocation.

    Shaped to mirror the fields the conversation-loop dispatch reads from
    the codex app-server ``TurnResult`` so the runtime glue stays uniform.
    """

    final_text: str = ""
    projected_messages: List[Dict[str, Any]] = field(default_factory=list)
    tool_iterations: int = 0
    interrupted: bool = False
    error: Optional[str] = None
    # Raw provider usage blocks, surfaced for token accounting.
    usage: Optional[Dict[str, Any]] = None
    model_usage: Optional[Dict[str, Any]] = None
    stop_reason: Optional[str] = None
    total_cost_usd: Optional[float] = None
    session_id: Optional[str] = None


def _resolve_claude_bin(config: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the path to the ``claude`` binary.

    Precedence:
      1. ``HERMES_CLAUDE_CLI_BIN`` env var.
      2. ``model.anthropic_cli_bin`` in config.yaml.
      3. The bundled default path on the VPS (if it exists).
      4. ``claude`` resolved on PATH.
    """
    env_bin = os.environ.get("HERMES_CLAUDE_CLI_BIN", "").strip()
    if env_bin:
        return env_bin

    if config:
        model_cfg = config.get("model")
        if isinstance(model_cfg, dict):
            cfg_bin = str(model_cfg.get("anthropic_cli_bin") or "").strip()
            if cfg_bin:
                return cfg_bin

    if os.path.exists(_DEFAULT_CLAUDE_BIN):
        return _DEFAULT_CLAUDE_BIN

    found = shutil.which("claude")
    if found:
        return found

    # Last resort: return the bare name so the spawn failure names it clearly.
    return "claude"


def _resolve_timeout() -> float:
    raw = os.environ.get("HERMES_CLAUDE_CLI_TIMEOUT", "").strip()
    if raw:
        try:
            value = float(raw)
            if value > 0:
                return value
        except ValueError:
            logger.debug("invalid HERMES_CLAUDE_CLI_TIMEOUT=%r; using default", raw)
    return _DEFAULT_TIMEOUT_SECONDS


def _build_child_env() -> Dict[str, str]:
    """Build the child environment, ensuring the OAuth token is present.

    The token is expected to already be in the Hermes process environment
    (the ``anthropic-cli`` provider overlay lists it in ``extra_env_vars``).
    We pass the whole environment through and only validate the token is set.
    """
    env = dict(os.environ)
    if not env.get(_OAUTH_TOKEN_ENV):
        raise AnthropicCliError(
            f"{_OAUTH_TOKEN_ENV} is not set in the environment; the claude CLI "
            "cannot authenticate. Set it on the Hermes container (Max-"
            "subscription setup token)."
        )
    return env


def _stderr_tail(stderr: str) -> str:
    if not stderr:
        return ""
    lines = stderr.strip().splitlines()
    return "\n".join(lines[-_STDERR_TAIL_LINES:])


def run_claude_cli_turn(
    *,
    user_input: str,
    model: str,
    config: Optional[Dict[str, Any]] = None,
    cwd: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> AnthropicCliTurnResult:
    """Run one non-interactive ``claude -p`` turn and return its result.

    Args:
        user_input: The prompt text to send (passed as the ``-p`` value).
        model: The model id (e.g. ``claude-sonnet-4-6``). Forwarded via
            ``--model``. Caller is responsible for normalization.
        config: Optional loaded Hermes config (for the binary-path key).
        cwd: Working directory for the child process.
        extra_args: Extra argv appended after the standard flags (e.g.
            ``["--append-system-prompt", "..."]``). Optional.

    Returns:
        AnthropicCliTurnResult with ``final_text`` set to the CLI's
        ``result`` field and usage blocks populated.

    Raises:
        AnthropicCliError: on non-zero exit, unparseable stdout,
            ``is_error: true``, or a missing OAuth token. The conversation
            runtime catches this to fall back to another provider.
    """
    claude_bin = _resolve_claude_bin(config)
    env = _build_child_env()
    timeout = _resolve_timeout()

    argv: List[str] = [
        claude_bin,
        "-p",
        user_input,
        "--output-format",
        "json",
    ]
    if model:
        argv += ["--model", model]
    if extra_args:
        argv += list(extra_args)

    logger.info(
        "spawning claude CLI: bin=%s model=%s cwd=%s prompt_chars=%d",
        claude_bin,
        model,
        cwd or os.getcwd(),
        len(user_input or ""),
    )

    try:
        completed = subprocess.run(
            argv,
            env=env,
            cwd=cwd or os.getcwd(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise AnthropicCliError(
            f"claude CLI binary not found at {claude_bin!r}: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AnthropicCliError(
            f"claude CLI timed out after {timeout:.0f}s",
            stderr_tail=_stderr_tail(exc.stderr or "" if isinstance(exc.stderr, str) else ""),
        ) from exc

    if completed.returncode != 0:
        raise AnthropicCliError(
            f"claude CLI exited with code {completed.returncode}",
            returncode=completed.returncode,
            stderr_tail=_stderr_tail(completed.stderr),
        )

    stdout = (completed.stdout or "").strip()
    if not stdout:
        raise AnthropicCliError(
            "claude CLI produced no stdout",
            stderr_tail=_stderr_tail(completed.stderr),
        )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        # ``claude -p --output-format json`` should emit exactly one JSON
        # object. If a wrapper printed banner lines first, try the last line.
        last_line = stdout.splitlines()[-1].strip()
        try:
            payload = json.loads(last_line)
        except json.JSONDecodeError:
            raise AnthropicCliError(
                f"claude CLI stdout was not valid JSON: {exc}",
                stderr_tail=_stderr_tail(completed.stderr),
            ) from exc

    if not isinstance(payload, dict):
        raise AnthropicCliError("claude CLI JSON was not an object")

    if payload.get("is_error"):
        # api_error_status / result carry the provider's reason on failure.
        reason = (
            payload.get("result")
            or payload.get("api_error_status")
            or payload.get("subtype")
            or "unknown error"
        )
        raise AnthropicCliError(
            f"claude CLI returned is_error=true: {reason}",
            stderr_tail=_stderr_tail(completed.stderr),
        )

    result_text = payload.get("result")
    if not isinstance(result_text, str):
        result_text = "" if result_text is None else str(result_text)

    return AnthropicCliTurnResult(
        final_text=result_text,
        projected_messages=[{"role": "assistant", "content": result_text}],
        tool_iterations=int(payload.get("num_turns") or 1),
        interrupted=False,
        error=None,
        usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
        model_usage=(
            payload.get("modelUsage")
            if isinstance(payload.get("modelUsage"), dict)
            else None
        ),
        stop_reason=payload.get("stop_reason"),
        total_cost_usd=payload.get("total_cost_usd"),
        session_id=payload.get("session_id"),
    )


__all__ = [
    "AnthropicCliError",
    "AnthropicCliTurnResult",
    "run_claude_cli_turn",
]
