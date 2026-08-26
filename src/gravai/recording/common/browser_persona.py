"""Presents the recorder's browser as Chrome on a Windows desktop.

Google refuses this container's browser at Meet's front door - the
`ResolveMeetingSpace` RPC answers 403 - while an ordinary Chrome on a Windows
machine on the same connection is offered the green room. This module is the
attempt to close the gap that a browser can close: every difference between the
two fingerprints that is a *string the page reports* rather than a fact about
the machine.

It works on three levels, because a persona that only changes one contradicts
itself on the others:

    headers      user agent and the full client-hint set, rewritten together
                 over CDP - Playwright's user_agent option leaves sec-ch-ua
                 alone, so a UA-only spoof says Windows and Linux at once
    scripts      browser_persona.js, injected at document start
    the process  a real Chrome binary rather than bundled Chromium, no
                 automation flag, and a window the size of the persona's screen

What it cannot change (even with JS):

    the renderer     this LXC has no /dev/dri, so drawing happens on the CPU in
                     SwiftShader. The reported strings are the workstation's
                     card; anything actually rasterised is not.
    font metrics     text measured on a canvas uses the 58 fonts installed here,
                     not Windows'. `document.fonts.check` is answered from the
                     persona, but `measureText` is answered by fontconfig.
    the TLS stack    Chrome's own, so this one is honest by construction.

Use it through `apply_persona`, which needs the page before it navigates:

    page = context.new_page()
    apply_persona(page, WINDOWS_CHROME)
    page.goto(meeting_url)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache
from importlib import resources
from typing import Any

# Injected into every frame; read from disk once per process.
_SCRIPT_PATH = resources.files("gravai.recording") / "common" / "browser_persona.js"


@dataclass(frozen=True)
class BrowserPersona:
    """One coherent identity: what the browser says over the wire and in-page."""

    name: str
    user_agent: str
    ua_metadata: dict[str, Any]
    accept_language: str
    locale: str
    timezone: str
    # navigator.platform, which client hints do not cover.
    js_platform: str
    viewport: dict[str, int]
    launch_args: list[str]
    # Playwright adds these; a real Chrome has none of them.
    ignore_default_args: list[str]
    # Everything browser_persona.js reads, serialised into it as {{PERSONA}}.
    js_config: dict[str, Any] = field(default_factory=dict)


_PDF_PLUGINS = [
    # Chrome ships one PDF viewer under five names. The list is fixed: a count
    # that is not five, from a browser claiming to be Chrome, is itself a tell.
    {"name": "PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
    {"name": "Chrome PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
    {"name": "Chromium PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
    {"name": "Microsoft Edge PDF Viewer", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
    {"name": "WebKit built-in PDF", "filename": "internal-pdf-viewer", "description": "Portable Document Format"},
]

_PDF_MIME_TYPES = [
    {"type": "application/pdf", "suffixes": "pdf", "description": "Portable Document Format"},
    {"type": "text/pdf", "suffixes": "pdf", "description": "Portable Document Format"},
]

# The speech engines a Windows install has and a Debian container does not.
_WINDOWS_VOICES = [
    {"name": "Microsoft David - English (United States)", "lang": "en-US", "localService": True, "default": True},
    {"name": "Microsoft Mark - English (United States)", "lang": "en-US", "localService": True},
    {"name": "Microsoft Zira - English (United States)", "lang": "en-US", "localService": True},
    {"name": "Microsoft Maria - Portuguese (Brazil)", "lang": "pt-BR", "localService": True},
    {"name": "Microsoft Daniel - Portuguese (Brazil)", "lang": "pt-BR", "localService": True},
    {"name": "Google US English", "lang": "en-US", "localService": False},
    {"name": "Google português do Brasil", "lang": "pt-BR", "localService": False},
]

# Answers 'is this installed' for the fonts that ship with Windows and are
# absent here. Enumeration by name is what this covers; measured width is not.
_WINDOWS_FONTS = [
    "Segoe UI", "Segoe UI Emoji", "Segoe UI Symbol", "Segoe UI Historic",
    "Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Cambria Math",
    "Candara", "Comic Sans MS", "Consolas", "Constantia", "Corbel",
    "Courier New", "Ebrima", "Franklin Gothic Medium", "Gabriola", "Gadugi",
    "Georgia", "HoloLens MDL2 Assets", "Impact", "Ink Free",
    "Javanese Text", "Leelawadee UI", "Lucida Console", "Lucida Sans Unicode",
    "Malgun Gothic", "Marlett", "Microsoft Himalaya", "Microsoft JhengHei",
    "Microsoft New Tai Lue", "Microsoft PhagsPa", "Microsoft Sans Serif",
    "Microsoft Tai Le", "Microsoft YaHei", "Microsoft Yi Baiti",
    "MingLiU-ExtB", "Mongolian Baiti", "MS Gothic", "MV Boli",
    "Myanmar Text", "Nirmala UI", "Palatino Linotype", "Segoe MDL2 Assets",
    "Segoe Print", "Segoe Script", "SimSun", "Sitka", "Sylfaen", "Symbol",
    "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana", "Webdings",
    "Wingdings", "Yu Gothic",
]

# Chrome 151 as it ships on Windows 11. platformVersion is the client-hint
# encoding of the OS: Windows 11 reports 15.0.0, not 10.
_CHROME_VERSION = "151.0.7922.137"
_CHROME_MAJOR = "151"

WINDOWS_CHROME = BrowserPersona(
    name="windows-chrome",
    user_agent=(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{_CHROME_MAJOR}.0.0.0 Safari/537.36"
    ),
    ua_metadata={
        # The GREASE brand is replaced at apply time with whatever this Chrome
        # really sends; hard-coding it guesses at a value that rotates by
        # version, and a wrong guess is worse than the Linux string it replaces.
        "brands": [
            {"brand": "Not;A=Brand", "version": "99"},
            {"brand": "Google Chrome", "version": _CHROME_MAJOR},
            {"brand": "Chromium", "version": _CHROME_MAJOR},
        ],
        "fullVersionList": [
            {"brand": "Not;A=Brand", "version": "99.0.0.0"},
            {"brand": "Google Chrome", "version": _CHROME_VERSION},
            {"brand": "Chromium", "version": _CHROME_VERSION},
        ],
        "fullVersion": _CHROME_VERSION,
        "platform": "Windows",
        "platformVersion": "15.0.0",
        "architecture": "x86",
        "bitness": "64",
        "model": "",
        "mobile": False,
        "wow64": False,
    },
    accept_language="en-US,en;q=0.9",
    locale="en-US",
    # The workstation's zone. A browser claiming Windows from a machine sitting
    # in UTC is a mismatch anyone can check with one call to Intl.
    timezone="America/Sao_Paulo",
    js_platform="Win32",
    viewport={"width": 1920, "height": 1080},
    launch_args=[
        "--disable-blink-features=AutomationControlled",
        "--window-size=1920,1080",
        "--window-position=0,0",
        "--lang=en-US",
        "--force-device-scale-factor=1",
        "--force-color-profile=srgb",
    ],
    ignore_default_args=[
        "--enable-automation",
        "--disable-background-networking",
        "--disable-component-update",
        "--disable-default-apps",
        "--disable-extensions",
        "--disable-sync",
        "--mute-audio",
    ],
    js_config={
        "platform": "Win32",
        "vendor": "Google Inc.",
        "languages": ["en-US", "en"],
        "hardwareConcurrency": 8,
        "deviceMemory": 8,
        "maxTouchPoints": 0,
        "devicePixelRatio": 1,
        "connection": {"downlink": 10, "effectiveType": "4g", "rtt": 50},
        "plugins": _PDF_PLUGINS,
        "mimeTypes": _PDF_MIME_TYPES,
        "screen": {
            "width": 1920,
            "height": 1080,
            "availWidth": 1920,
            # 1080 less the Windows 11 taskbar.
            "availHeight": 1032,
            "colorDepth": 24,
        },
        "webgl": {
            "unmaskedVendor": "Google Inc. (AMD)",
            "unmaskedRenderer": (
                "ANGLE (AMD, AMD Radeon RX 9070 XT (0x00007550) Direct3D11 vs_5_0 ps_5_0, "
                "D3D11-32.0.21023.1017)"
            ),
            # SwiftShader's limits differ from a D3D11 driver's, and they are one
            # getParameter call away from the renderer string that was spoofed.
            "maxTextureSize": 16384,
            "maxViewportDims": [32767, 32767],
        },
        "webgpu": {
            "vendor": "amd",
            "architecture": "rdna-4",
            "description": "AMD Radeon RX 9070 XT",
        },
        "voices": _WINDOWS_VOICES,
        "fonts": _WINDOWS_FONTS,
    },
)

# The same persona, minus the graphics card it does not have. Worth having as a
# named variant rather than a flag: a claimed renderer can be checked against
# what actually gets drawn, so which of the two is more convincing is an open
# question that gets re-tested, not a setting anyone should have to guess at.
WINDOWS_CHROME_HONEST_GPU = replace(
    WINDOWS_CHROME,
    name="windows-chrome-honest-gpu",
    js_config={
        key: value
        for key, value in WINDOWS_CHROME.js_config.items()
        if key not in ("webgl", "webgpu")
    },
)

PERSONAS = {
    persona.name: persona for persona in (WINDOWS_CHROME, WINDOWS_CHROME_HONEST_GPU)
}


@lru_cache(maxsize=4)
def _script_template() -> str:
    return _SCRIPT_PATH.read_text(encoding="utf-8")


def init_script(persona: BrowserPersona) -> str:
    """The document-start script for this persona."""
    return _script_template().replace("{{PERSONA}}", json.dumps(persona.js_config))


def launch_kwargs(persona: BrowserPersona, headless: bool = True, channel: str | None = "chrome") -> dict:
    """Launch options for `chromium.launch`.

    `channel="chrome"` runs the real Google Chrome rather than the bundled
    Chromium: a persona claiming to be Chrome from a Chromium build disagrees
    with itself in more places than can be patched by hand.
    """
    kwargs: dict[str, Any] = {
        "headless": headless,
        "args": list(persona.launch_args),
        "ignore_default_args": list(persona.ignore_default_args),
    }
    if channel:
        kwargs["channel"] = channel
    return kwargs


def context_kwargs(persona: BrowserPersona) -> dict:
    """Context options that belong to the persona rather than to the caller."""
    return {
        "locale": persona.locale,
        "timezone_id": persona.timezone,
        "viewport": dict(persona.viewport),
        "user_agent": persona.user_agent,
    }


def apply_persona(page, persona: BrowserPersona = WINDOWS_CHROME) -> None:
    """Applies the header-level half of the persona to one page.

    Must run before the page navigates: the overrides are read when a request is
    built, and a document already fetched keeps the headers it was fetched with.

    The script half is added to the *context* by the caller, so that it also
    covers frames and pages opened later:

        context.add_init_script(init_script(persona))
    """
    session = page.context.new_cdp_session(page)

    metadata = json.loads(json.dumps(persona.ua_metadata))
    _use_real_brands(page, metadata)

    session.send(
        "Emulation.setUserAgentOverride",
        {
            "userAgent": persona.user_agent,
            "acceptLanguage": persona.accept_language,
            "platform": persona.js_platform,
            "userAgentMetadata": metadata,
        },
    )
    # Set here as well as on the context: a context created without them, or one
    # this function is handed after the fact, still ends up consistent.
    for command, params in (
        ("Emulation.setTimezoneOverride", {"timezoneId": persona.timezone}),
        ("Emulation.setLocaleOverride", {"locale": persona.locale}),
    ):
        try:
            session.send(command, params)
        except Exception:
            # Already overridden to the same value by the context; harmless.
            pass


def _use_real_brands(page, metadata: dict) -> None:
    """Keeps this Chrome's own brand list, relabelled for the persona.

    Chrome pads `sec-ch-ua` with a deliberately varying junk brand ('Not;A=Brand'
    and friends), whose spelling and position change between versions. Reading
    the browser's real list and swapping only the version numbers is exact,
    where writing the list out by hand is a guess that dates.
    """
    try:
        brands = page.evaluate(
            "() => navigator.userAgentData && navigator.userAgentData.brands"
        )
    except Exception:
        return
    if not brands:
        return

    full_versions = {entry["brand"]: entry["version"] for entry in metadata["fullVersionList"]}
    metadata["brands"] = [dict(entry) for entry in brands]
    metadata["fullVersionList"] = [
        {
            "brand": entry["brand"],
            # A brand this persona has no full version for is the junk one,
            # whose full form is its major with three zeroes.
            "version": full_versions.get(entry["brand"], f"{entry['version']}.0.0.0"),
        }
        for entry in brands
    ]
