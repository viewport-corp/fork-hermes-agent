#!/bin/sh
set -eu

secret_file="${HERMES_PROJECTED_ENV_FILE:-/run/hermes-secrets/runtime.env}"
if [ ! -r "$secret_file" ]; then
  echo "Hermes projected secret file is unavailable: $secret_file" >&2
  exit 78
fi

set -a
# Generated from a fixed key allowlist with POSIX-safe quoting.
. "$secret_file"
set +a

exec /opt/hermes/docker/entrypoint-dispatch.sh "$@"
