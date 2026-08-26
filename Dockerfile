FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.12-slim-trixie

WORKDIR /app

# ENV DEBIAN_FRONTEND=noninteractive

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

# RUN pulseaudio --start

RUN mkdir -p /run/dbus

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md /app/

COPY src /app/src

RUN uv sync

RUN uv run playwright install-deps
RUN uv run playwright install
# Meet refuses bundled Chromium at ResolveMeetingSpace even behind a complete
# Windows persona, and admits the branded Google Chrome build behind the same
# one. The Meet recorder launches channel="chrome", so this is required, not a
# preference - see src/gravai/recording/common/browser_persona.py.
RUN uv run playwright install chrome
