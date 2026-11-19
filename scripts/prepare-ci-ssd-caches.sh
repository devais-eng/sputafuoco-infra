#!/usr/bin/env bash
# Prepares persistent caches alongside, never inside, ephemeral runner PVCs.
set -euo pipefail

measure_uv_cache() {
  local errors output rc bytes
  errors=$(mktemp)
  if output=$(LC_ALL=C du -sx -B1 --exclude='.tmp*' "$1" 2>"$errors"); then
    :
  else
    rc=$?
    # Tolerate only a disappearing uv temporary path, never permission/I/O errors.
    if [[ $rc == 1 && -s "$errors" ]] && ! grep -qvE "^du: cannot access '.*/\.tmp[^']*': No such file or directory$" "$errors"; then
      echo 'Temporary uv entries vanished during measurement; continuing with disposable entries excluded.' >&2
    else
      cat "$errors" >&2
      rm -f "$errors"
      return "$rc"
    fi
  fi
  rm -f "$errors"
  bytes=${output%%$'\t'*}
  [[ "$bytes" =~ ^[0-9]+$ ]] || { echo 'Invalid du output; refusing migration' >&2; return 1; }
  printf '%s\n' "$bytes"
}
mode=${1:---check}
[[ "$mode" == --check || "$mode" == --apply ]] || { echo 'Usage: prepare-ci-ssd-caches.sh [--check|--apply]' >&2; exit 2; }
[[ $(hostname) == sputafuoco && $EUID == 0 ]] || { echo 'Run with sudo on sputafuoco' >&2; exit 1; }
for tool in rsync findmnt du df install; do command -v "$tool" >/dev/null; done
root=/srv/runner-ssd
source_uv=/srv/data/ci-cache/uv
[[ $(findmnt -nro SOURCE --mountpoint "$root") == /dev/sda3 ]] || { echo 'Expected SSD partition is not mounted' >&2; exit 1; }
[[ $(findmnt -nro FSTYPE --mountpoint "$root") == ext4 && -s "$root/.runner-ssd-volume" ]] || { echo 'SSD filesystem or marker invalid' >&2; exit 1; }
findmnt --mountpoint /srv/data >/dev/null
for path in "$root" "$root/ci-cache" "$root/ci-cache/uv" "$root/ci-cache/registry" /srv/data/ci-cache "$source_uv"; do
 [[ ! -L "$path" ]] || { echo "Refusing symlink: $path" >&2; exit 1; }
 [[ ! -e "$path" || -d "$path" ]] || { echo "Not a directory: $path" >&2; exit 1; }
done
bytes=0
if [[ -d "$source_uv" ]]; then
  echo "Measuring reusable uv cache on HDD; this can take several minutes..."
  bytes=$(measure_uv_cache "$source_uv")
fi
free=$(df -B1 --output=avail "$root" | tail -1 | tr -d ' ')
reserve=$((20 * 1024 * 1024 * 1024))
(( free > bytes + reserve )) || { echo 'Not enough SSD space to copy UV while retaining 20 GiB free' >&2; exit 1; }
echo "UV allocated bytes: $bytes; SSD available bytes: $free"
[[ "$mode" == --apply ]] || { echo 'CHECK OK; no changes'; exit 0; }
install -d -o root -g root -m 0755 "$root/ci-cache"
install -d -o 1001 -g 1001 -m 0750 "$root/ci-cache/uv"
install -d -o root -g root -m 0755 "$root/ci-cache/registry"
# UV entries are disposable. Do not delete source data or overwrite cache entries
# in a destination already used by a new runner. Vanished source entries are OK.
rc=0
if [[ -d "$source_uv" ]]; then
  echo "Copying reusable uv cache to SSD (temporary build directories excluded)..."
  rsync -a --numeric-ids --ignore-existing --exclude='.tmp*' --info=progress2,name0 --human-readable "$source_uv/" "$root/ci-cache/uv/" || rc=$?
else
  echo 'No previous uv cache; starting empty'
fi
[[ $rc == 0 || $rc == 24 ]] || exit "$rc"
chown 1001:1001 "$root/ci-cache/uv"
chmod 0750 "$root/ci-cache/uv"
echo 'SSD caches ready. HDD originals retained. New registry cache starts cold.'
df -h "$root"
