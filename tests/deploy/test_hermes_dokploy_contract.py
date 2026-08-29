"""Tests for the Viewport Hermes Dokploy deployment contract."""

from __future__ import annotations

import json
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
    "API_SERVER_KEY",
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
        "API_SERVER_KEY": "api-key",
        "GITHUB_TOKEN": "github-token",
        "OPENAI_API_KEY": "token with spaces",
        "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD": "pass with quote ' ok",
    }


def test_projector_stage_aliases_api_key(tmp_path: Path) -> None:
    source = tmp_path / "platformx.env"
    dest = tmp_path / "runtime.env"
    source.write_text("API_SERVER_KEY=prod\nHERMES_STAGE_API_SERVER_KEY=stage\n", encoding="utf-8")
    _run_node("deploy/project-platformx-env.mjs", str(source), str(dest), "stage")
    rendered = dest.read_text(encoding="utf-8")
    assert "export API_SERVER_KEY='stage'" in rendered
    assert "HERMES_STAGE_API_SERVER_KEY" not in rendered


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


def test_prepull_contract_is_digest_only_and_docker_viewport_socket() -> None:
    script = (ROOT / "deploy/prepull-ghcr-image.sh").read_text(encoding="utf-8")
    assert "readonly HERMES_PREPULL_SECRETS_FILE=\"/srv/viewport/secrets/platformx.env\"" in script
    assert "readonly HERMES_PREPULL_DOCKER_HOST=\"unix:///var/run/docker-viewport.sock\"" in script
    assert "readonly HERMES_PREPULL_GHCR_USER=\"theplatformx\"" in script
    assert "docker-viewport" not in script.replace("docker-viewport.sock", "")
    assert "HERMES_PREPULL_IMAGE=\"$(python3" in script
    assert "@sha256:[a-f0-9]{64}" in script
    assert "--password-stdin" in script
