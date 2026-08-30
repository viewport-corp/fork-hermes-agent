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


def test_secret_init_healthchecks_use_metadata_not_read_permission() -> None:
    prod = (ROOT / "deploy/docker-compose.yml").read_text(encoding="utf-8")
    stage = (ROOT / "deploy/dokploy.stage.yml").read_text(encoding="utf-8")
    assert "test -f /run/hermes-secrets/runtime.env" in prod
    assert "test -r /run/hermes-secrets/runtime.env" not in prod
    assert "stat -c %u:%g:%a /run/hermes-secrets/runtime.env" in prod
    assert "test -f /run/hermes-stage-secrets/runtime.env" in stage
    assert "test -r /run/hermes-stage-secrets/runtime.env" not in stage
    assert "stat -c %u:%g:%a /run/hermes-stage-secrets/runtime.env" in stage


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
    assert desired["secrets"]["projectorProfile"] == "stage"
    assert desired["secrets"]["allowedProjectedKeys"] == []
    assert desired["secrets"]["emptyProjectionAllowed"] is True
    assert desired["runtimeIsolation"]["legacyEnvExclusion"]["required"] is True
    assert desired["runtimeIsolation"]["legacyEnvExclusion"]["forbiddenFiles"] == [".env", ".op.env"]
    for key in desired["requiredVariables"]:
        assert f"${{{key}:" in stage or key == "HERMES_STAGE_IMAGE"
    assert "project-platformx-env.mjs /run/platformx.env" in stage
    assert "/run/hermes-stage-secrets/runtime.env stage" in stage
    assert "test -f /run/hermes-stage-secrets/runtime.env" in stage
    assert "test -r /run/hermes-stage-secrets/runtime.env" not in stage
    assert "stat -c %u:%g:%a /run/hermes-stage-secrets/runtime.env" in stage
    assert "ports:" not in stage
    assert "172.31.15.2" not in stage
    assert "/srv/viewport/runtime/hermes-viewport-new" not in stage
    assert "HERMES_DASHBOARD_HOST=127.0.0.1" in stage
    assert "TELEGRAM_" not in stage
    assert "OPENAI_API_KEY" not in stage
    assert "GITHUB_TOKEN" not in stage
