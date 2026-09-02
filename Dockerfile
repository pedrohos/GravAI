# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.12-slim-trixie

WORKDIR /app

# ENV DEBIAN_FRONTEND=noninteractive

# pulseaudio and ffmpeg are how the meeting is recorded: each recording plays
# into a null sink of its own and ffmpeg records that sink's monitor - see
# recording/session_audio_capture.py. Nothing here starts the daemon: it is
# started per recording, so that one that has died is brought back rather than
# leaving the browser playing into a dummy device.
# x11vnc serves the browser's screen when the Google sign-in hits a CAPTCHA, so
# that a person can read it and type what it says - see recording/common/vnc.py.
# Xvfb, the screen it serves, arrives with 'playwright install-deps' further down.
RUN apt-get update &&\
    apt-get install -y --no-install-recommends curl\
        ca-certificates gcc libasound-dev portaudio19-dev\
        libportaudio2 libportaudiocpp0 build-essential\
        libcap2-bin pulseaudio ffmpeg x11vnc &&\
    rm -rf /var/lib/apt/lists/*

# Fix dumpcap capabilities to run in unprivileged docker environments - required by tshark
# RUN setcap -r /usr/bin/dumpcap && chmod 755 /usr/bin/dumpcap

RUN mkdir -p /run/dbus

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PATH="/app/.venv/bin:${PATH}"

# The cache mount below lives on a different filesystem from /app, where uv would
# rather hardlink than copy; saying so here is what keeps it from warning about
# it on every build.
ENV UV_LINK_MODE=copy

# The lock file and nothing else, so that the dependency layer is invalidated by
# a dependency change and not by editing a source file. --no-install-project
# leaves gravai itself out of it for the same reason: it is the half that
# changes every build, and installing it here would drag the browser downloads
# below back through the cache with it.
# The cache mount carries uv's own download cache between builds, so even a
# genuine dependency change re-downloads only what actually changed.
COPY pyproject.toml uv.lock README.md /app/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Called through the venv on PATH rather than `uv run`, which would sync first
# and fail on a project whose source is not copied in yet.
RUN playwright install-deps
RUN playwright install
# Meet refuses bundled Chromium at ResolveMeetingSpace even behind a complete
# Windows persona, and admits the branded Google Chrome build behind the same
# one. The Meet recorder launches channel="chrome", so this is required, not a
# preference - see src/gravai/recording/common/browser_persona.py.
RUN playwright install chrome

# Last, because it changes on every build. Everything above this line survives a
# source edit; this layer is the only one that has to be rebuilt for one, and it
# only installs gravai itself into the environment the layers above prepared.
COPY src /app/src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen
