#!/usr/bin/env bash
set -euo pipefail
# Pre-pull the reviewed private GHCR digest into the docker-viewport daemon before Dokploy starts it.
# Reads only GITHUB_TOKEN_VIEWPORT_CORP from /srv/viewport/secrets/platformx.env and uses temporary Docker auth under /run.
HERMES_PREPULL_SECRETS_FILE="${HERMES_PREPULL_SECRETS_FILE:-/srv/viewport/secrets/platformx.env}"
HERMES_PREPULL_IMAGE="${HERMES_PREPULL_IMAGE:?set ghcr.io/viewport-corp/fork-hermes-agent@sha256:<digest>}"
HERMES_PREPULL_DOCKER_CONTEXT="${HERMES_PREPULL_DOCKER_CONTEXT:-docker-viewport}"
HERMES_PREPULL_DOCKER_CONFIG="$(mktemp -d /run/hermes-ghcr-prepull.XXXXXX)"
cleanup() { rm -rf "$HERMES_PREPULL_DOCKER_CONFIG"; }
trap cleanup EXIT
if [[ "$HERMES_PREPULL_SECRETS_FILE" != "/srv/viewport/secrets/platformx.env" ]]; then echo "Refusing to read GitHub token outside /srv/viewport/secrets/platformx.env" >&2; exit 64; fi
if [[ ! -r "$HERMES_PREPULL_SECRETS_FILE" ]]; then echo "Cannot read $HERMES_PREPULL_SECRETS_FILE" >&2; exit 66; fi
if [[ ! "$HERMES_PREPULL_IMAGE" =~ ^ghcr\.io/viewport-corp/fork-hermes-agent@sha256:[a-f0-9]{64}$ ]]; then echo "HERMES_PREPULL_IMAGE must be the exact reviewed GHCR digest" >&2; exit 64; fi
HERMES_PREPULL_TOKEN=""
while IFS= read -r HERMES_PREPULL_LINE; do case "$HERMES_PREPULL_LINE" in GITHUB_TOKEN_VIEWPORT_CORP=*) HERMES_PREPULL_TOKEN="${HERMES_PREPULL_LINE#GITHUB_TOKEN_VIEWPORT_CORP=}"; break;; esac; done < "$HERMES_PREPULL_SECRETS_FILE"
HERMES_PREPULL_TOKEN="${HERMES_PREPULL_TOKEN%\"}"
HERMES_PREPULL_TOKEN="${HERMES_PREPULL_TOKEN#\"}"
if [[ -z "$HERMES_PREPULL_TOKEN" ]]; then echo "GITHUB_TOKEN_VIEWPORT_CORP is missing or empty in $HERMES_PREPULL_SECRETS_FILE" >&2; exit 78; fi
echo "Logging into GHCR with temporary Docker auth under /run for $HERMES_PREPULL_DOCKER_CONTEXT"
printf "%s\n" "$HERMES_PREPULL_TOKEN" | DOCKER_CONFIG="$HERMES_PREPULL_DOCKER_CONFIG" docker --context "$HERMES_PREPULL_DOCKER_CONTEXT" login ghcr.io --username viewport-corp --password-stdin >/dev/null
echo "Pulling $HERMES_PREPULL_IMAGE into Docker context $HERMES_PREPULL_DOCKER_CONTEXT"
DOCKER_CONFIG="$HERMES_PREPULL_DOCKER_CONFIG" docker --context "$HERMES_PREPULL_DOCKER_CONTEXT" pull "$HERMES_PREPULL_IMAGE"
DOCKER_CONFIG="$HERMES_PREPULL_DOCKER_CONFIG" docker --context "$HERMES_PREPULL_DOCKER_CONTEXT" logout ghcr.io >/dev/null 2>&1 || true
echo "Pre-pull complete; temporary Docker auth removed on exit"
