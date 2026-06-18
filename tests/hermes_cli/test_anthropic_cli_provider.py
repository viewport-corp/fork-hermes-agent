"""Tests for the ``anthropic-cli`` provider — Claude via the real ``claude -p`` CLI.

Covers the end-to-end wiring (provider overlay → transport → api_mode →
model normalization) and the subprocess session adapter
(``agent.transports.anthropic_cli_session``) with the ``claude`` binary
mocked, so these run without a real CLI install or network.
"""
import json
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest


# ── Wiring ──────────────────────────────────────────────────────────────


class TestAnthropicCliWiring:
    def test_overlay_registered(self):
        from hermes_cli.providers import get_provider

        ov = get_provider("anthropic-cli")
        assert ov is not None
        assert ov.transport == "anthropic_cli"
        assert ov.auth_type == "external_process"
        assert "CLAUDE_CODE_OAUTH_TOKEN" in ov.api_key_env_vars

    def test_transport_maps_to_api_mode(self):
        from hermes_cli.providers import TRANSPORT_TO_API_MODE

        assert TRANSPORT_TO_API_MODE["anthropic_cli"] == "anthropic_cli"

    def test_determine_api_mode(self):
        from hermes_cli.providers import determine_api_mode

        assert determine_api_mode("anthropic-cli") == "anthropic_cli"

    def test_api_mode_is_valid(self):
        from hermes_cli.runtime_provider import _VALID_API_MODES, _parse_api_mode

        assert "anthropic_cli" in _VALID_API_MODES
        assert _parse_api_mode("anthropic_cli") == "anthropic_cli"

    @pytest.mark.parametrize("model,expected", [
        ("claude-sonnet-4.6", "claude-sonnet-4-6"),
        ("claude-sonnet-4-6", "claude-sonnet-4-6"),
        ("claude-opus-4.8", "claude-opus-4-8"),
    ])
    def test_model_normalization_matches_anthropic(self, model, expected):
        from hermes_cli.model_normalize import normalize_model_for_provider

        assert normalize_model_for_provider(model, "anthropic-cli") == expected


# ── Session subprocess adapter ──────────────────────────────────────────


_SUCCESS_PAYLOAD = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "BRIDGE_OK",
    "stop_reason": "end_turn",
    "num_turns": 1,
    "session_id": "abc-123",
    "total_cost_usd": 0.04,
    "usage": {"input_tokens": 3, "output_tokens": 7},
    "modelUsage": {"claude-sonnet-4-6": {"inputTokens": 3, "outputTokens": 7}},
}


def _completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestAnthropicCliSession:
    def _run(self, monkeypatch, completed, *, token="tok"):
        from agent.transports import anthropic_cli_session as s

        if token is None:
            monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        else:
            monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", token)
        monkeypatch.setattr(s, "_resolve_claude_bin", lambda config=None: "/fake/claude")
        with mock.patch.object(s.subprocess, "run", return_value=completed) as m:
            return s.run_claude_cli_turn(
                user_input="hi", model="claude-sonnet-4-6"
            ), m

    def test_success_parses_result_and_usage(self, monkeypatch):
        completed = _completed(stdout=json.dumps(_SUCCESS_PAYLOAD))
        turn, run_mock = self._run(monkeypatch, completed)
        assert turn.final_text == "BRIDGE_OK"
        assert turn.error is None
        assert turn.stop_reason == "end_turn"
        assert turn.session_id == "abc-123"
        assert turn.usage == {"input_tokens": 3, "output_tokens": 7}
        assert "claude-sonnet-4-6" in turn.model_usage
        assert turn.projected_messages == [
            {"role": "assistant", "content": "BRIDGE_OK"}
        ]
        # argv shape: -p <prompt> --output-format json --model <model>
        argv = run_mock.call_args[0][0]
        assert argv[:3] == ["/fake/claude", "-p", "hi"]
        assert "--output-format" in argv and "json" in argv
        assert "--model" in argv and "claude-sonnet-4-6" in argv
        # token injected into child env
        assert run_mock.call_args.kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"

    def test_is_error_raises(self, monkeypatch):
        from agent.transports.anthropic_cli_session import AnthropicCliError

        payload = dict(_SUCCESS_PAYLOAD, is_error=True, result="quota exceeded")
        completed = _completed(stdout=json.dumps(payload))
        with pytest.raises(AnthropicCliError) as exc:
            self._run(monkeypatch, completed)
        assert "quota exceeded" in str(exc.value)

    def test_nonzero_exit_raises(self, monkeypatch):
        from agent.transports.anthropic_cli_session import AnthropicCliError

        completed = _completed(returncode=1, stderr="boom")
        with pytest.raises(AnthropicCliError) as exc:
            self._run(monkeypatch, completed)
        assert exc.value.returncode == 1
        assert "boom" in exc.value.stderr_tail

    def test_bad_json_raises(self, monkeypatch):
        from agent.transports.anthropic_cli_session import AnthropicCliError

        completed = _completed(stdout="not json at all")
        with pytest.raises(AnthropicCliError):
            self._run(monkeypatch, completed)

    def test_banner_then_json_recovers(self, monkeypatch):
        # A wrapper that prints a banner line before the JSON should still parse
        # (we fall back to the last line).
        completed = _completed(
            stdout="[banner] starting\n" + json.dumps(_SUCCESS_PAYLOAD)
        )
        turn, _ = self._run(monkeypatch, completed)
        assert turn.final_text == "BRIDGE_OK"

    def test_missing_token_raises(self, monkeypatch):
        from agent.transports.anthropic_cli_session import AnthropicCliError

        completed = _completed(stdout=json.dumps(_SUCCESS_PAYLOAD))
        with pytest.raises(AnthropicCliError) as exc:
            self._run(monkeypatch, completed, token=None)
        assert "CLAUDE_CODE_OAUTH_TOKEN" in str(exc.value)


class TestBinResolution:
    def test_env_var_wins(self, monkeypatch):
        from agent.transports.anthropic_cli_session import _resolve_claude_bin

        monkeypatch.setenv("HERMES_CLAUDE_CLI_BIN", "/x/claude")
        assert _resolve_claude_bin() == "/x/claude"

    def test_config_key_used(self, monkeypatch):
        from agent.transports.anthropic_cli_session import _resolve_claude_bin

        monkeypatch.delenv("HERMES_CLAUDE_CLI_BIN", raising=False)
        cfg = {"model": {"anthropic_cli_bin": "/cfg/claude"}}
        assert _resolve_claude_bin(cfg) == "/cfg/claude"


# ── Runtime turn handler (failure → fallback contract) ──────────────────


class TestAnthropicCliRuntimeFallback:
    def test_cli_error_returns_partial_not_raises(self, monkeypatch):
        """A CLI failure must return a completed=False dict so Hermes can fall
        back, not propagate the exception."""
        from agent import anthropic_cli_runtime as rt
        from agent.transports import anthropic_cli_session as s
        from agent.transports.anthropic_cli_session import AnthropicCliError

        def boom(**kwargs):
            raise AnthropicCliError("auth expired", stderr_tail="401")

        # The runtime imports run_claude_cli_turn from the session module at
        # call time, so patch the symbol on that module.
        monkeypatch.setattr(s, "run_claude_cli_turn", boom)

        agent = SimpleNamespace(
            model="claude-sonnet-4-6",
            session_cwd=None,
            _iters_since_skill=0,
            _skill_nudge_interval=0,
            valid_tool_names=set(),
        )
        messages = []
        result = rt.run_anthropic_cli_turn(
            agent,
            user_message="hi",
            original_user_message="hi",
            messages=messages,
            effective_task_id="t1",
        )
        assert result["completed"] is False
        assert result["partial"] is True
        assert "auth expired" in result["error"]
        assert result["api_calls"] == 0


# ── Regression: anthropic-cli must NOT hit the Copilot credential path ───
# See fork issue #13. Prior to the fix, anthropic-cli (an external_process
# provider) fell through to resolve_external_process_provider_credentials(),
# which is hardcoded for Copilot and raised
#   AuthError("Could not find the Copilot CLI command copilot.")
# at agent-init on every turn.


class TestAnthropicCliNotCopilotCredentialPath:
    def test_resolve_external_process_creds_does_not_raise_copilot_error(
        self, monkeypatch
    ):
        """anthropic-cli must resolve to process creds without requiring the
        copilot binary, even when no `copilot` is on PATH."""
        from hermes_cli import auth

        # Ensure neither a copilot binary nor copilot env vars are present.
        monkeypatch.delenv("HERMES_COPILOT_ACP_COMMAND", raising=False)
        monkeypatch.delenv("COPILOT_CLI_PATH", raising=False)
        monkeypatch.setattr(auth.shutil, "which", lambda *_a, **_k: None)

        creds = auth.resolve_external_process_provider_credentials("anthropic-cli")
        assert creds["provider"] == "anthropic-cli"
        assert creds["source"] == "process"
        # Must NOT carry copilot-acp markers.
        assert creds["api_key"] != "copilot-acp"
        assert creds.get("base_url", "") == ""

    def test_resolve_external_process_creds_honours_claude_bin_env(
        self, monkeypatch
    ):
        from hermes_cli import auth

        monkeypatch.setenv("HERMES_CLAUDE_CLI_BIN", "/host/srv/claude.exe")
        monkeypatch.setattr(auth.shutil, "which", lambda *_a, **_k: None)
        creds = auth.resolve_external_process_provider_credentials("anthropic-cli")
        assert creds["command"] == "/host/srv/claude.exe"

    def test_copilot_still_raises_when_binary_missing(self, monkeypatch):
        """The fix must NOT regress copilot-acp: it should still raise the
        missing-copilot error when no binary is found."""
        from hermes_cli import auth
        from hermes_cli.auth import AuthError

        monkeypatch.delenv("HERMES_COPILOT_ACP_COMMAND", raising=False)
        monkeypatch.delenv("COPILOT_CLI_PATH", raising=False)
        monkeypatch.setattr(auth.shutil, "which", lambda *_a, **_k: None)

        with pytest.raises(AuthError) as exc:
            auth.resolve_external_process_provider_credentials("copilot-acp")
        assert "copilot" in str(exc.value).lower()

    def test_agent_init_anthropic_cli_skips_provider_client(self, monkeypatch):
        """With api_mode == anthropic_cli, init must set agent.client = None
        and never call resolve_provider_client (the copilot trap)."""
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "dummy-token")

        import agent.auxiliary_client as aux

        def fail(*_a, **_k):
            raise AssertionError(
                "resolve_provider_client must not be called for anthropic_cli"
            )

        monkeypatch.setattr(aux, "resolve_provider_client", fail)

        from run_agent import AIAgent

        # Explicit provider + api_mode, no api_key/base_url so the catch-all
        # (copilot trap) would fire if the anthropic_cli branch were missing.
        agent = AIAgent(
            provider="anthropic-cli",
            api_mode="anthropic_cli",
            model="claude-sonnet-4-6",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        assert agent.api_mode == "anthropic_cli"
        assert agent.client is None
