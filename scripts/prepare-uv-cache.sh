#!/usr/bin/env bash
set -euo pipefail
[[ $(hostname) == sputafuoco ]] || { echo "Expected sputafuoco" >&2; exit 1; }
[[ $EUID == 0 ]] || { echo "Run with sudo" >&2; exit 1; }
: "${EXPECTED_DATA_UUID:?Set EXPECTED_DATA_UUID to the independently verified data filesystem UUID}"
[[ $(findmnt -n -o UUID --mountpoint /srv/data) == "$EXPECTED_DATA_UUID" ]] || { echo "Expected data filesystem is not mounted" >&2; exit 1; }
[[ -d /srv/data/ci-cache && ! -L /srv/data/ci-cache ]] || { echo "Invalid cache parent" >&2; exit 1; }
[[ ! -L /srv/data/ci-cache/uv ]] || { echo "Cache path must not be a symlink" >&2; exit 1; }
install -d -o 1001 -g 1001 -m 0750 /srv/data/ci-cache/uv
stat -c '%n uid=%u gid=%g mode=%a' /srv/data/ci-cache/uv
findmnt --target /srv/data/ci-cache/uv
