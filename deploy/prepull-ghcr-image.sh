#!/usr/bin/env bash
set -euo pipefail
# Pre-pull the reviewed private GHCR digest into the NEW Dokploy docker daemon.
# Contract: read only the canonical PlatformX secret file, use temporary Docker
# auth under /run, pull the digest declared in deploy/dokploy.desired-state.json,
# and never persist registry credentials in Dokploy or the host Docker config.
readonly HERMES_PREPULL_SECRETS_FILE="/srv/viewport/secrets/platformx.env"
readonly HERMES_PREPULL_DESIRED_STATE="${HERMES_PREPULL_DESIRED_STATE:-deploy/dokploy.desired-state.json}"
readonly HERMES_PREPULL_DOCKER_HOST="unix:///var/run/docker-viewport.sock"
readonly HERMES_PREPULL_GHCR_USER="theplatformx"
HERMES_PREPULL_DOCKER_CONFIG="$(mktemp -d /run/hermes-ghcr-prepull.XXXXXX)"
cleanup() {
  case "$HERMES_PREPULL_DOCKER_CONFIG" in
    /run/hermes-ghcr-prepull.*) rm -rf "$HERMES_PREPULL_DOCKER_CONFIG" ;;
    *) echo "Refusing unsafe Docker config cleanup path: $HERMES_PREPULL_DOCKER_CONFIG" >&2 ;;
  esac
}
trap cleanup EXIT
if [[ ! -r "$HERMES_PREPULL_SECRETS_FILE" ]]; then echo "Cannot read $HERMES_PREPULL_SECRETS_FILE" >&2; exit 66; fi
if [[ ! -r "$HERMES_PREPULL_DESIRED_STATE" ]]; then echo "Cannot read desired state $HERMES_PREPULL_DESIRED_STATE" >&2; exit 66; fi
HERMES_PREPULL_IMAGE="$(python3 - "$HERMES_PREPULL_DESIRED_STATE" <<"PY"
import json, sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text())["image"])
PY
)"
if [[ ! "$HERMES_PREPULL_IMAGE" =~ ^ghcr\.io/viewport-corp/fork-hermes-agent@sha256:[a-f0-9]{64}$ ]]; then
  echo "deploy/dokploy.desired-state.json image must be the exact reviewed GHCR digest" >&2
  exit 64
fi
HERMES_PREPULL_TOKEN=""
while IFS= read -r HERMES_PREPULL_LINE; do
  case "$HERMES_PREPULL_LINE" in
    export\ GITHUB_TOKEN_VIEWPORT_CORP=*) HERMES_PREPULL_TOKEN="${HERMES_PREPULL_LINE#export GITHUB_TOKEN_VIEWPORT_CORP=}"; break ;;
    GITHUB_TOKEN_VIEWPORT_CORP=*) HERMES_PREPULL_TOKEN="${HERMES_PREPULL_LINE#GITHUB_TOKEN_VIEWPORT_CORP=}"; break ;;
  esac
done < "$HERMES_PREPULL_SECRETS_FILE"
HERMES_PREPULL_TOKEN="${HERMES_PREPULL_TOKEN%\"}"
HERMES_PREPULL_TOKEN="${HERMES_PREPULL_TOKEN#\"}"
if [[ -z "$HERMES_PREPULL_TOKEN" ]]; then echo "GITHUB_TOKEN_VIEWPORT_CORP is missing or empty in $HERMES_PREPULL_SECRETS_FILE" >&2; exit 78; fi
echo "Logging into GHCR with temporary Docker auth under /run via $HERMES_PREPULL_DOCKER_HOST"
printf "%s\n" "$HERMES_PREPULL_TOKEN" | DOCKER_CONFIG="$HERMES_PREPULL_DOCKER_CONFIG" DOCKER_HOST="$HERMES_PREPULL_DOCKER_HOST" docker login ghcr.io --username "$HERMES_PREPULL_GHCR_USER" --password-stdin >/dev/null
echo "Pulling reviewed digest $HERMES_PREPULL_IMAGE into $HERMES_PREPULL_DOCKER_HOST"
DOCKER_CONFIG="$HERMES_PREPULL_DOCKER_CONFIG" DOCKER_HOST="$HERMES_PREPULL_DOCKER_HOST" docker pull "$HERMES_PREPULL_IMAGE"
DOCKER_CONFIG="$HERMES_PREPULL_DOCKER_CONFIG" DOCKER_HOST="$HERMES_PREPULL_DOCKER_HOST" docker logout ghcr.io >/dev/null 2>&1 || true
echo "Pre-pull complete; temporary Docker auth removed on exit"
