# syntax=docker/dockerfile:1
#
# ARC runner image for the devais-eng self-hosted runners.
#
# The official ARC runner image plus what our CI otherwise installs on every job:
#   - apt packages: build-essential, gh, wget, xz-utils, tesseract-ocr, tesseract-ocr-eng
#   - the Docker Compose CLI plugin
#   - FFmpeg and FFprobe, the BtbN static build AnimMouse/setup-ffmpeg downloads
#   - Task and uv, pre-seeded in the tool cache where go-task/setup-task and
#     astral-sh/setup-uv look before downloading
#   - CPython for uv, where setup-uv points UV_PYTHON_INSTALL_DIR
#
# Keep the versions below in step with the ones the workflows pin. A mismatch is not an
# error: the setup actions just fall back to downloading their own copy.

ARG RUNNER_VERSION=2.338.2

FROM ubuntu:24.04 AS downloads

ARG COMPOSE_VERSION=v2.32.4
ARG TASK_VERSION=3.48.0
ARG UV_VERSION=0.11.2
ARG FFMPEG_VERSION=8.1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /dl
ENV CURL="curl -fsSL --retry 5 --retry-all-errors --connect-timeout 30"

# Docker Compose CLI plugin
RUN $CURL -o docker-compose "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64" \
    && $CURL -o docker-compose.sha256 "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64.sha256" \
    && echo "$(cut -d' ' -f1 docker-compose.sha256)  docker-compose" | sha256sum -c - \
    && install -D -m 0755 docker-compose /out/usr/local/lib/docker/cli-plugins/docker-compose

# Task, laid out as go-task/setup-task caches it: <cache>/task/<version>/x64/bin/task
RUN $CURL -o task.tar.gz "https://github.com/go-task/task/releases/download/v${TASK_VERSION}/task_linux_amd64.tar.gz" \
    && $CURL -o task_checksums.txt "https://github.com/go-task/task/releases/download/v${TASK_VERSION}/task_checksums.txt" \
    && grep " task_linux_amd64.tar.gz$" task_checksums.txt | sed 's/task_linux_amd64.tar.gz$/task.tar.gz/' | sha256sum -c - \
    && d=/out/opt/hostedtoolcache/task/${TASK_VERSION}/x64 \
    && mkdir -p $d/bin && tar -xzf task.tar.gz -C $d/bin task \
    && touch $d.complete

# uv, laid out as astral-sh/setup-uv caches it: <cache>/uv/<version>/x86_64/{uv,uvx}
RUN f=uv-x86_64-unknown-linux-gnu.tar.gz \
    && $CURL -o $f "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/$f" \
    && $CURL -o $f.sha256 "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/$f.sha256" \
    && sha256sum -c $f.sha256 \
    && d=/out/opt/hostedtoolcache/uv/${UV_VERSION}/x86_64 \
    && mkdir -p $d && tar -xzf $f -C $d --strip-components 1 \
    && touch $d.complete

# FFmpeg: the same BtbN static build AnimMouse/setup-ffmpeg downloads. BtbN's "latest"
# release is rolling, so a rebuild picks up the newest patch build of the branch.
RUN f=ffmpeg-n${FFMPEG_VERSION}-latest-linux64-gpl-${FFMPEG_VERSION}.tar.xz \
    && $CURL -o $f "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$f" \
    && $CURL -o checksums.sha256 "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256" \
    && grep " $f$" checksums.sha256 | sha256sum -c - \
    && mkdir -p /out/usr/local/bin \
    && tar -xJf $f -C /out/usr/local/bin --strip-components 2 --wildcards '*/bin/ffmpeg' '*/bin/ffprobe'


FROM ghcr.io/actions/actions-runner:${RUNNER_VERSION}

ARG PYTHON_VERSION=3.13.12
ARG UV_VERSION=0.11.2

LABEL org.opencontainers.image.title="arc-runner-sallo" \
      org.opencontainers.image.description="ARC runner with CI dependencies preinstalled" \
      org.opencontainers.image.source="https://github.com/devais-eng/sputafuoco-infra"

USER root

# Packages the CI jobs otherwise install with apt.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        gh \
        wget \
        xz-utils \
        tesseract-ocr \
        tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY --from=downloads /out/usr/ /usr/

# The runner exports RUNNER_TOOL_CACHE to the jobs; the default (_work/_tool) is on the
# pod's emptyDir and starts empty. Pointing it into the image makes the pre-seeded Task
# and uv visible to the setup actions. setup-ffmpeg also caches there, so the runner
# user owns it (COPY --chown, not a chown -R layer that would duplicate the files).
COPY --from=downloads --chown=runner:docker /out/opt/hostedtoolcache/ /opt/hostedtoolcache/
ENV RUNNER_TOOL_CACHE=/opt/hostedtoolcache \
    AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache

USER runner

# Python for uv, where setup-uv sets UV_PYTHON_INSTALL_DIR.
ENV UV_PYTHON_INSTALL_DIR=/home/runner/.local/share/uv/python
RUN /opt/hostedtoolcache/uv/${UV_VERSION}/x86_64/uv python install ${PYTHON_VERSION} \
    && /opt/hostedtoolcache/uv/${UV_VERSION}/x86_64/uv cache clean
