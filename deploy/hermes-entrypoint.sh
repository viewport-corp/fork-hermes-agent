#!/bin/sh
set -eu

secret_file="${HERMES_PROJECTED_ENV_FILE:-/run/hermes-secrets/runtime.env}"
hermes_home="${HERMES_HOME:-/opt/data}"

for legacy_env_file in "$hermes_home/.env" "$hermes_home/.op.env"; do
  if [ -e "$legacy_env_file" ] || [ -L "$legacy_env_file" ]; then
    echo "Hermes legacy state env file blocks canonical secret projection: $legacy_env_file" >&2
    echo "Move the legacy file to a protected rollback backup after the full state backup and before deploy; do not replace it with an empty file or /dev/null." >&2
    exit 78
  fi
done

if [ ! -r "$secret_file" ]; then
  echo "Hermes projected secret file is unavailable: $secret_file" >&2
  exit 78
fi

set -a
# Generated from a fixed key allowlist with POSIX-safe quoting.
. "$secret_file"
set +a

exec /opt/hermes/docker/entrypoint-dispatch.sh "$@"
