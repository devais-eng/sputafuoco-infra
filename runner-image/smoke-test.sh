#!/usr/bin/env bash
# Checks a built runner image: docker run --rm -i <image> bash -s < runner-image/smoke-test.sh
# Expected versions come from the environment (defaults match the Dockerfile ARGs).
set -euo pipefail

TASK_VERSION=${TASK_VERSION:-3.48.0}
UV_VERSION=${UV_VERSION:-0.11.2}
PYTHON_VERSION=${PYTHON_VERSION:-3.13.12}
COMPOSE_VERSION=${COMPOSE_VERSION:-v2.32.4}
FFMPEG_VERSION=${FFMPEG_VERSION:-8.1}

fail=0
check() { # check <description> <command...>
  local desc=$1; shift
  if out=$("$@" 2>&1); then printf 'ok    %s: %s\n' "$desc" "$(head -1 <<<"$out")"
  else printf 'FAIL  %s: %s\n' "$desc" "$(head -1 <<<"$out")"; fail=1; fi
}

TC=${RUNNER_TOOL_CACHE:-}
check "user is runner (uid 1001)"      bash -c '[ "$(id -u)" = 1001 ] && id -un'
check "RUNNER_TOOL_CACHE set"          bash -c "[ '$TC' = /opt/hostedtoolcache ] && echo $TC"
check "tool cache writable by runner"  bash -c "touch $TC/.w && rm $TC/.w && echo writable"

# Layouts the setup actions look up (@actions/tool-cache find: <tool>/<version>/<arch> + <arch>.complete)
check "task in tool cache"             bash -c "test -f $TC/task/$TASK_VERSION/x64.complete && $TC/task/$TASK_VERSION/x64/bin/task --version"
check "uv in tool cache"               bash -c "test -f $TC/uv/$UV_VERSION/x86_64.complete && $TC/uv/$UV_VERSION/x86_64/uv --version | grep -F $UV_VERSION"
check "uvx in tool cache"              bash -c "$TC/uv/$UV_VERSION/x86_64/uvx --version"
check "python $PYTHON_VERSION for uv"  bash -c "$TC/uv/$UV_VERSION/x86_64/uv python find --no-python-downloads $PYTHON_VERSION"

check "docker compose plugin"          bash -c "docker compose version | grep -F $COMPOSE_VERSION"
check "docker buildx plugin"           docker buildx version
check "ffmpeg $FFMPEG_VERSION"         bash -c "ffmpeg -version | grep -E '^ffmpeg version n?$FFMPEG_VERSION'"
check "ffprobe"                        ffprobe -version
check "tesseract (eng)"                bash -c "tesseract --list-langs 2>&1 | grep -x eng"
check "gcc"                            gcc --version
check "make"                           make --version
check "gh"                             gh --version
check "wget"                           wget --version
check "xz"                             xz --version
check "git"                            git --version
check "passwordless sudo (as in base)" sudo -n true

exit $fail
