"""Tests for the Viewport Hermes Dokploy deployment contract."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _run_node(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", str(ROOT / script), *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_dokploy_desired_state_contract() -> None:
    result = _run_node("deploy/check-dokploy-desired-state.mjs")
    assert json.loads(result.stdout) == {
        "dokployDesiredStateSafe": True,
        "composeId": "kl7tmNAE6_kbE_c7q6l2I",
    }


def test_projector_allows_only_runtime_keys_and_quotes(tmp_path: Path) -> None:
    source = tmp_path / "platformx.env"
    dest = tmp_path / "runtime.env"
    source.write_text(
        "\n".join(
            [
                "API_SERVER_KEY=api-key",
                "TELEGRAM_BOT_TOKEN=telegram-token",
                "GITHUB_TOKEN_VIEWPORT_CORP=github-token",
                "OPENAI_API_KEY=token with spaces",
                "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin",
                "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=pass with quote ' ok",
                "UNRELATED_SECRET=must-not-appear",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run_node("deploy/project-platformx-env.mjs", str(source), str(dest), "production")
    payload = json.loads(result.stdout)
    assert payload["profile"] == "production"
    assert "GITHUB_TOKEN" in payload["projectedKeys"]
    rendered = dest.read_text(encoding="utf-8")
    assert "UNRELATED_SECRET" not in rendered
    assert "export API_SERVER_KEY=" not in rendered
    assert "export GITHUB_TOKEN=" in rendered
    assert "export HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=" in rendered
    assert stat.S_IMODE(dest.stat().st_mode) == 0o400
    readback = f"""
set -a
. {dest}
python3 - <<PY
import json
import os
keys = [
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD",
]
print(json.dumps({{key: os.environ[key] for key in keys}}))
PY
"""
    loaded = json.loads(
        subprocess.run(
            ["bash", "-c", readback],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    )
    assert loaded == {
        "GITHUB_TOKEN": "github-token",
        "OPENAI_API_KEY": "token with spaces",
        "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD": "pass with quote ' ok",
    }


def test_projector_stage_allows_empty_projection(tmp_path: Path) -> None:
    source = tmp_path / "platformx.env"
    dest = tmp_path / "runtime.env"
    source.write_text(
        "API_SERVER_KEY=ignored\n"
        "HERMES_STAGE_API_SERVER_KEY=ignored\n"
        "TELEGRAM_BOT_TOKEN=must-not-stage\n",
        encoding="utf-8",
    )
    result = _run_node("deploy/project-platformx-env.mjs", str(source), str(dest), "stage")
    payload = json.loads(result.stdout)
    assert payload["profile"] == "stage"
    assert payload["projectedKeys"] == []
    assert dest.read_text(encoding="utf-8") == "\n"


def test_projector_gates_production_without_dashboard_auth(tmp_path: Path) -> None:
    source = tmp_path / "platformx.env"
    dest = tmp_path / "runtime.env"
    source.write_text("API_SERVER_KEY=api\nTELEGRAM_BOT_TOKEN=telegram\n", encoding="utf-8")
    result = subprocess.run(
        ["node", str(ROOT / "deploy/project-platformx-env.mjs"), str(source), str(dest), "production"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode != 0
    assert "dashboard auth migration keys" in result.stderr


def test_production_uses_official_image_live_state_and_own_bot_token() -> None:
    prod = (ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    desired = json.loads((ROOT / "deploy/dokploy.desired-state.json").read_text(encoding="utf-8"))
    assert desired["image"].startswith("docker.io/nousresearch/hermes-agent:v2026.9.24@sha256:")
    assert f"x-hermes-image: &hermes-image {desired['image']}" in prod
    assert "source: /srv/viewport/runtime/hermes-viewport-v2026-8-27\n        target: /opt/data" in prod
    assert "hermes-viewport-new" not in prod
    assert "TELEGRAM_BOT_TOKEN: ${HERMES_TELEGRAM_BOT_TOKEN:?" in prod
    assert "${TELEGRAM_BOT_TOKEN" not in prod
    assert "secret-init" not in prod
    assert "\n    entrypoint:" not in prod
    assert "\n    user:" not in prod
    assert '"127.0.0.1:9119:9119"' in prod


def test_checker_rejects_shared_openclaw_bot_token(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in (
        "dokploy.desired-state.json",
        "dokploy.stage.desired-state.json",
        "dokploy.stage.yml",
        "docker-compose.yml",
    ):
        (deploy / name).write_text((ROOT / "deploy" / name).read_text(encoding="utf-8"), encoding="utf-8")
    compose = deploy / "docker-compose.yml"
    compose.write_text(
        compose.read_text(encoding="utf-8").replace(
            "${HERMES_TELEGRAM_BOT_TOKEN:?", "${TELEGRAM_BOT_TOKEN:?"
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(ROOT / "deploy/check-dokploy-desired-state.mjs")],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode != 0
    assert "dokployDesiredStateSafe" not in result.stdout


def _run_stage_guard(state_dir: Path) -> subprocess.CompletedProcess[str]:
    """Run the stage entrypoint guard against state_dir; the upstream dispatcher is stubbed by echo."""
    import yaml

    stage = yaml.safe_load((ROOT / "deploy/dokploy.stage.yml").read_text(encoding="utf-8"))
    shell, flags, script, argv0 = stage["services"]["hermes-stage"]["entrypoint"]
    assert (shell, flags, argv0) == ("/bin/sh", "-ec", "hermes-stage-guard")
    script = (
        script.replace("$$", "$")
        .replace("/opt/hermes/docker/entrypoint-dispatch.sh", "echo dispatched")
        .replace("/opt/data", str(state_dir))
    )
    return subprocess.run(
        ["sh", "-ec", script, "hermes-stage-guard", "gateway", "run"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stage_state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(parents=True)
    return state


def test_stage_guard_allows_restart_after_upstream_seeds_env(tmp_path: Path) -> None:
    state = _stage_state(tmp_path)
    first = _run_stage_guard(state)
    assert first.returncode == 0, first.stderr
    assert first.stdout == "dispatched gateway run\n"
    # What upstream stage2-hook.sh does on first boot: seed .env from .env.example,
    # then persist a generated API_SERVER_KEY.
    (state / ".env").write_text(
        (ROOT / ".env.example").read_text(encoding="utf-8") + "API_SERVER_KEY=" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    second = _run_stage_guard(state)
    assert second.returncode == 0, second.stderr
    assert second.stdout == "dispatched gateway run\n"


def test_stage_guard_allows_empty_or_commented_secret_keys(tmp_path: Path) -> None:
    state = _stage_state(tmp_path)
    (state / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=\n"
        'OPENROUTER_API_KEY=""\n'
        "GITHUB_TOKEN=  # set later\n"
        "# TELEGRAM_BOT_TOKEN=123:commented\n"
        "HERMES_TELEGRAM_BOT_TOKEN_NOTE=not-a-forbidden-class\n",
        encoding="utf-8",
    )
    result = _run_stage_guard(state)
    assert result.returncode == 0, result.stderr


def test_stage_guard_refuses_copied_production_secrets(tmp_path: Path) -> None:
    for index, line in enumerate(
        (
            "TELEGRAM_BOT_TOKEN=123456:live-bot",
            "export TELEGRAM_BOT_TOKEN='123456:live-bot'",
            '  HERMES_DASHBOARD_BASIC_AUTH_PASSWORD = "pw"',
            "OPENAI_API_KEY=sk-test",
            "GITHUB_TOKEN_VIEWPORT_CORP=ghp-test",
        )
    ):
        state = _stage_state(tmp_path / str(index))
        (state / ".env").write_text("API_SERVER_KEY=" + "a" * 64 + "\n" + line + "\n", encoding="utf-8")
        result = _run_stage_guard(state)
        assert result.returncode == 78, line
        assert "production secret key" in result.stderr
        assert "dispatched" not in result.stdout


def test_stage_guard_refuses_legacy_op_env_and_symlinked_env(tmp_path: Path) -> None:
    state = _stage_state(tmp_path / "op")
    (state / ".op.env").write_text("X=1\n", encoding="utf-8")
    result = _run_stage_guard(state)
    assert result.returncode == 78
    assert ".op.env" in result.stderr

    state = _stage_state(tmp_path / "link")
    target = tmp_path / "production.env"
    target.write_text("API_SERVER_KEY=x\n", encoding="utf-8")
    (state / ".env").symlink_to(target)
    result = _run_stage_guard(state)
    assert result.returncode == 78
    assert "symlink" in result.stderr


def test_entrypoint_refuses_legacy_state_env_files(tmp_path: Path) -> None:
    projected = tmp_path / "runtime.env"
    projected.write_text("export HERMES_ENTRYPOINT_TEST_VALUE=safe-test-value\n", encoding="utf-8")
    for legacy_name in (".env", ".op.env"):
        hermes_home = tmp_path / legacy_name.replace(".", "legacy-")
        hermes_home.mkdir()
        (hermes_home / legacy_name).write_text("HERMES_LEGACY_TEST_VALUE=legacy\n", encoding="utf-8")
        result = subprocess.run(
            ["sh", str(ROOT / "deploy/hermes-entrypoint.sh"), "true"],
            cwd=ROOT,
            env={
                **os.environ,
                "HERMES_HOME": str(hermes_home),
                "HERMES_PROJECTED_ENV_FILE": str(projected),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert result.returncode == 78
        assert legacy_name in result.stderr
        assert "protected rollback backup" in result.stderr
        assert "empty file or /dev/null" in result.stderr


def test_prepull_contract_is_digest_only_and_docker_viewport_socket() -> None:
    script = (ROOT / "deploy/prepull-ghcr-image.sh").read_text(encoding="utf-8")
    assert "readonly HERMES_PREPULL_SECRETS_FILE=\"/srv/viewport/secrets/platformx.env\"" in script
    assert "readonly HERMES_PREPULL_DOCKER_HOST=\"unix:///var/run/docker-viewport.sock\"" in script
    assert "readonly HERMES_PREPULL_GHCR_USER=\"theplatformx\"" in script
    assert "docker-viewport" not in script.replace("docker-viewport.sock", "")
    assert "HERMES_PREPULL_IMAGE=\"$(python3" in script
    assert "@sha256:[a-f0-9]{64}" in script
    assert "--password-stdin" in script
    assert "^docker\\.io/nousresearch/hermes-agent:v[0-9.]+@sha256:[a-f0-9]{64}$" in script


def test_fork_publish_gate_skips_digest_pin_only_changes() -> None:
    import importlib.util

    script = ROOT / "scripts/ci/viewport_ghcr_publish_gate.py"
    spec = importlib.util.spec_from_file_location("viewport_ghcr_publish_gate", script)
    assert spec and spec.loader
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    assert gate.should_publish({
        "deploy/docker-compose.yml",
        "deploy/dokploy.desired-state.json",
    }, "1" * 40) is False
    assert gate.should_publish({
        "deploy/docker-compose.yml",
        "hermes_cli/gateway.py",
    }, "1" * 40) is True
    assert gate.should_publish({
        "deploy/docker-compose.yml",
    }, "0" * 40) is True


def test_stage_compose_is_isolated_from_production() -> None:
    stage = (ROOT / "deploy/dokploy.stage.yml").read_text(encoding="utf-8")
    desired = json.loads(
        (ROOT / "deploy/dokploy.stage.desired-state.json").read_text(encoding="utf-8")
    )
    assert desired["stageOnly"] is True
    assert desired["image"].startswith("docker.io/nousresearch/hermes-agent:")
    assert desired["secrets"]["source"] == "none"
    assert desired["secrets"]["allowedProjectedKeys"] == []
    assert desired["runtimeIsolation"]["forbiddenProductionStateDir"] == "/srv/viewport/runtime/hermes-viewport-v2026-8-27"
    assert desired["runtimeIsolation"]["legacyEnvExclusion"]["required"] is True
    assert desired["runtimeIsolation"]["legacyEnvExclusion"]["forbiddenFiles"] == [".op.env"]
    for key in desired["requiredVariables"]:
        assert f"${{{key}:?" in stage
    assert "platformx.env" not in stage
    assert "secret-init" not in stage
    assert "exec /opt/hermes/docker/entrypoint-dispatch.sh" in stage
    assert "ports:" not in stage
    assert "hermes-viewport-v2026-8-27" not in stage
    assert "/srv/viewport/runtime/hermes-viewport-new" not in stage
    assert "HERMES_DASHBOARD_HOST=127.0.0.1" in stage
    # Secret names may appear only in the entrypoint guard, which refuses them.
    import textwrap

    import yaml

    service = yaml.safe_load(stage)["services"]["hermes-stage"]
    guard = textwrap.indent(service["entrypoint"][2], " " * 8)
    assert guard in stage
    outside_guard = stage.replace(guard, "")
    assert "TELEGRAM_" not in outside_guard
    assert "OPENAI_API_KEY" not in outside_guard
    assert "GITHUB_TOKEN" not in outside_guard
    for name in service["environment"]:
        assert not name.startswith(("TELEGRAM_", "OPENAI_API_KEY", "GITHUB_TOKEN")), name
