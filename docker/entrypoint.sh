#!/bin/sh
# Drop privileges to an unprivileged user while still supporting a root-owned
# volume.
#
# Platform volumes (Railway, Fly, plain `docker run -v`) arrive owned by root, so
# a fixed `USER` directive in the image would make the app unable to write its
# SQLite database. This entrypoint fixes ownership on the mounted volume once,
# then hands the process to uid 10001 with no capabilities.
set -eu

APP_UID="${GFI_APP_UID:-10001}"
APP_GID="${GFI_APP_GID:-10001}"
DATA_DIR="${GFI_DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "${DATA_DIR}/cache/chips" 2>/dev/null || true
  if ! chown -R "${APP_UID}:${APP_GID}" "${DATA_DIR}" 2>/dev/null; then
    echo "entrypoint: could not set ownership of ${DATA_DIR}." >&2
    echo "entrypoint: mount the volume writable by uid ${APP_UID}, or set GFI_APP_UID/GFI_APP_GID." >&2
  fi
  if command -v gosu >/dev/null 2>&1; then
    exec gosu "${APP_UID}:${APP_GID}" "$@"
  fi
  echo "entrypoint: gosu is missing; refusing to run the app as root." >&2
  exit 1
fi

exec "$@"
