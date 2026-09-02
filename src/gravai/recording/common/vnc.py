"""Puts a person in front of the recorder's browser when Google asks for one.

Every screen the Google sign-in can end on is one this container cannot answer:
a 2-Step Verification code it has no phone for, an automation judgement there is
nothing to retry. The CAPTCHA is the exception. It is not asking for a secret or
a device - it is asking for a pair of eyes, and a pair of eyes is a thing that
can be lent over a network. This module is that loan: when
common/google_sign_in.py finds the challenge on the page, the browser's screen
goes onto a VNC port and the sign-in blocks until somebody has typed the answer.

It is three pieces, in the order they come into existence:

    VirtualDisplay   an Xvfb screen for the browser to draw on, started with the
                     recording. Headless Chrome has no window at all, so there
                     would be nothing to serve and nothing to click - the display
                     is the price of being able to hand the session over later.
    VncServer        x11vnc on that display, started *only* when the challenge
                     appears and stopped when the sign-in is over. A port that
                     accepts keystrokes into a logged-in browser is not something
                     to leave open across a two-hour meeting.
    CaptchaHandoff   the wait itself: the screenshot, the connection details, the
                     record left in the session directory, and the poll that
                     notices the challenge is gone.

Nothing here runs for a meeting that admits a guest. The sign-in is only reached
when Meet turns the anonymous guest away, and the VNC server is only started when
that sign-in hits a CAPTCHA - see MeetMeetingRecorder._sign_in_for_meeting, which
is the only caller.

The display is the one part that is always on, because it cannot be added to a
browser that is already running: a headless Chrome cannot be moved onto a screen
half way through a join. `VNC_ENABLED=false` turns the whole mechanism off and
the browser goes back to headless, where a CAPTCHA ends the attempt as it did
before this existed.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from secrets import token_urlsafe

# The record a waiting challenge leaves in the session directory, so that
# something other than a log tail can find it - the API reads these to answer
# 'is anything waiting for me right now?'. See pending_challenges().
CHALLENGE_FILENAME = "captcha_challenge.json"

_XVFB_BINARY = "Xvfb"
_X11VNC_BINARY = "x11vnc"

# Xvfb writes the display number it settled on to this pipe once it is listening,
# which is what makes two recordings starting at the same moment safe: each is
# handed a number the other cannot also be handed. Picking one by scanning
# /tmp/.X11-unix is the same check with a race in the middle of it.
_DISPLAY_TIMEOUT_S = 20.0

# From launching x11vnc to a client being able to complete a handshake with it.
_VNC_LISTEN_TIMEOUT_S = 15.0

# Ports tried, from VNC_PORT upwards, before giving up. Only more than one when
# two recordings hit a CAPTCHA at the same time, which is rare enough that a
# short walk is plenty and a long one only delays the report.
_PORT_ATTEMPTS = 8

# How often the handoff looks to see whether the challenge is still on screen.
# A person is typing at the other end of this; there is nothing to be gained by
# asking more often than they can finish.
_SOLVED_POLL_INTERVAL_S = 1.0

_TERMINATE_GRACE_S = 5.0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@lru_cache(maxsize=1)
def _die_with_parent() -> list[str]:
    """A command prefix that has the kernel stop this child with its parent.

    Both children outlive a parent that is killed outright rather than asked to
    stop: an X server nobody draws to, and - worse - a VNC port that accepts
    keystrokes into a browser holding a Google session, held open for as long as
    the machine is up. PR_SET_PDEATHSIG closes that window, and the ordinary
    stop() paths cover every other way a recording ends.

    Through setpriv rather than a preexec_fn, which is the other way to set it:
    a preexec_fn runs in the child between fork and exec, where allocating -
    which loading libc through ctypes does - is only safe while the parent
    happens to be single-threaded. setpriv execs into the real command, so the
    process this returns a prefix for is still the one being started, and there
    is no window to be careful about.

    Empty where setpriv is not installed. The child is then only cleaned up by
    stop(), which covers a recording that ends or throws but not one that is
    killed outright.
    """
    if shutil.which("setpriv") is None:
        return []
    return ["setpriv", "--pdeathsig", "TERM", "--"]


def _stop(process: subprocess.Popen | None, what: str, logger=None) -> None:
    """Ends a child, and does not return while it is still running."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=_TERMINATE_GRACE_S)
    except subprocess.TimeoutExpired:
        if logger:
            logger.warning(f"{what} did not stop when asked, killing it")
        process.kill()
        try:
            process.wait(timeout=_TERMINATE_GRACE_S)
        except subprocess.TimeoutExpired:
            pass


def xvfb_available() -> bool:
    return shutil.which(_XVFB_BINARY) is not None


def vnc_available() -> bool:
    return shutil.which(_X11VNC_BINARY) is not None


class VirtualDisplay:
    """An X screen for the browser, so that there is something to show a person.

    Started before the browser and stopped after it. Costs a process and a few
    megabytes for a recording nobody ever watches, which is the whole reason it
    is a setting: what it buys is that the browser *can* be watched, and that
    cannot be arranged after the fact.

    `start()` reports whether it worked rather than raising. A missing Xvfb, or
    one that will not come up, is a reason to record headless - it is not a
    reason to fail a meeting that was very likely never going to see a CAPTCHA.
    """

    def __init__(self, width: int, height: int, logger=None):
        self._width = width
        self._height = height
        self._logger = logger
        self._process: subprocess.Popen | None = None
        self._display: str | None = None

    @property
    def display(self) -> str | None:
        """The DISPLAY value, e.g. ':2', or None while nothing is running."""
        return self._display

    @property
    def active(self) -> bool:
        return self._display is not None and self._process is not None and self._process.poll() is None

    def env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        """`base` with DISPLAY pointed at this screen.

        Chrome is told where to draw through the environment rather than a flag,
        so this is what a launch has to be handed.
        """
        environment = dict(base if base is not None else os.environ)
        if self._display:
            environment["DISPLAY"] = self._display
        return environment

    def start(self) -> bool:
        if self.active:
            return True
        if not xvfb_available():
            self._log(
                "warning",
                f"{_XVFB_BINARY} is not installed, so the browser runs headless and a "
                f"CAPTCHA on the Google sign-in cannot be handed to anybody. Install it "
                f"(it ships with 'playwright install-deps') or set VNC_ENABLED=false to "
                f"stop this being reported.",
            )
            return False

        read_fd, write_fd = os.pipe()
        try:
            self._process = subprocess.Popen(
                _die_with_parent()
                + [
                    _XVFB_BINARY,
                    # The number is Xvfb's to choose and ours to read back.
                    "-displayfd", str(write_fd),
                    "-screen", "0", f"{self._width}x{self._height}x24",
                    # Nothing outside this container has any business connecting
                    # to the X server; the VNC server is the only way in, and it
                    # talks to it over the unix socket.
                    "-nolisten", "tcp",
                ],
                pass_fds=(write_fd,),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            os.close(read_fd)
            os.close(write_fd)
            self._log("warning", f"Could not start {_XVFB_BINARY}: {exc}. Recording headless instead.")
            self._process = None
            return False

        # The write end has to be closed here as well, or the read below never
        # sees end-of-file when Xvfb dies and waits out the whole timeout.
        os.close(write_fd)
        number = self._read_display_number(read_fd)
        if number is None:
            self.stop()
            self._log(
                "warning",
                f"{_XVFB_BINARY} did not report a display within {_DISPLAY_TIMEOUT_S:.0f}s. "
                f"Recording headless instead.",
            )
            return False

        self._display = f":{number}"
        self._log("info", f"Virtual display {self._display} up at {self._width}x{self._height}")
        return True

    def _read_display_number(self, read_fd: int) -> str | None:
        deadline = time.time() + _DISPLAY_TIMEOUT_S
        buffer = b""
        try:
            os.set_blocking(read_fd, False)
            while time.time() < deadline:
                if self._process is not None and self._process.poll() is not None:
                    return None
                try:
                    chunk = os.read(read_fd, 32)
                except BlockingIOError:
                    time.sleep(0.1)
                    continue
                if not chunk:
                    return None
                buffer += chunk
                if b"\n" in buffer:
                    number = buffer.split(b"\n", 1)[0].decode(errors="replace").strip()
                    return number or None
            return None
        finally:
            os.close(read_fd)

    def stop(self) -> None:
        _stop(self._process, f"{_XVFB_BINARY} {self._display or ''}".strip(), self._logger)
        self._process = None
        self._display = None

    def __enter__(self) -> VirtualDisplay:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def _log(self, level: str, message: str) -> None:
        if self._logger:
            getattr(self._logger, level)(message)


class VncUnavailable(RuntimeError):
    """Raised when the browser's screen cannot be served to anybody."""


@dataclass(frozen=True)
class VncEndpoint:
    """Where to connect, and with what."""

    host: str
    port: int
    password: str
    # A password this module invented for one challenge, rather than the one an
    # operator configured. Only the invented one is ever written down or logged:
    # it is the only way anybody could learn it, and it dies with the challenge.
    password_is_generated: bool

    @property
    def url(self) -> str:
        return f"vnc://{self.host}:{self.port}"

    @property
    def where_to_connect(self) -> str:
        """The url, plus what to do about it when it is not one to type.

        0.0.0.0 is what the server bound to, not an address anybody can reach:
        somebody reading it in a hurry needs to be told it means this machine,
        on that port, from wherever they are.
        """
        if self.host in ("0.0.0.0", "::", ""):
            return (
                f"{self.url}  (0.0.0.0 is every interface - connect to this machine's "
                f"own address on port {self.port})"
            )
        return self.url


class VncServer:
    """x11vnc on a virtual display, for as long as a person is needed.

    Started at the CAPTCHA and stopped when the sign-in is over rather than the
    moment the challenge clears: Google often follows one screen with another,
    and dropping the viewer's connection between them means reconnecting to a
    new port with a new password to answer a second challenge.
    """

    def __init__(
        self,
        display: str,
        host: str,
        port: int,
        password: str,
        output_dir: str,
        logger=None,
    ):
        self._display = display
        self._host = host
        self._port = port
        self._password = password
        self._output_dir = output_dir
        self._logger = logger
        self._process: subprocess.Popen | None = None
        self._passwd_file: Path | None = None
        self._endpoint: VncEndpoint | None = None

    @property
    def endpoint(self) -> VncEndpoint | None:
        return self._endpoint

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> VncEndpoint:
        """Serves the display, and returns only once a client could connect.

        The port is proven rather than assumed: a wrong one in the message that
        asks somebody to come and help is worse than no message, because the
        person spends the timeout finding out the port was wrong.
        """
        if self.running and self._endpoint:
            return self._endpoint

        if not vnc_available():
            raise VncUnavailable(
                f"{_X11VNC_BINARY} is not installed, so the browser's screen cannot be "
                f"served to anybody."
            )

        password = self._password or token_urlsafe(9)
        generated = not self._password
        # VNC passwords are truncated to 8 characters by the protocol itself, so
        # a longer one silently becomes its first 8. Doing the truncation here
        # keeps what gets logged the same as what actually opens the session.
        password = password[:8]
        try:
            self._passwd_file = self._store_password(password)
        except Exception as exc:
            # Every way this fails is the same thing to the caller: there is no
            # screen to offer. Letting it out raw would end the recording with
            # something about a subprocess instead of about the CAPTCHA.
            raise VncUnavailable(f"the VNC password file could not be written: {exc}") from exc

        last_error: Exception | None = None
        for port in range(self._port, self._port + _PORT_ATTEMPTS):
            if not _port_is_free(self._host, port):
                continue
            try:
                self._process = self._launch(port)
            except Exception as exc:
                last_error = exc
                break
            if _wait_until_serving(self._host, port):
                self._endpoint = VncEndpoint(
                    host=self._host, port=port, password=password, password_is_generated=generated
                )
                return self._endpoint
            # The port was free a moment ago and the server is not on it now:
            # either somebody else took it in between or x11vnc refused it.
            _stop(self._process, _X11VNC_BINARY, self._logger)
            self._process = None

        self._cleanup_password_file()
        raise VncUnavailable(
            f"{_X11VNC_BINARY} would not serve {self._display} on any port from {self._port} to "
            f"{self._port + _PORT_ATTEMPTS - 1}"
            + (f": {last_error}" if last_error else f". Check {self._log_path()} for what it said.")
        )

    def _launch(self, port: int) -> subprocess.Popen:
        return subprocess.Popen(
            _die_with_parent()
            + [
                _X11VNC_BINARY,
                "-display", self._display,
                "-rfbport", str(port),
                "-rfbauth", str(self._passwd_file),
                # -listen is how the server is kept off every interface but the
                # configured one; the default is every one of them.
                "-listen", self._host,
                # The viewer may disconnect and come back - a person reloading a
                # client mid-challenge should not end the handoff.
                "-forever",
                "-shared",
                # Xvfb has no XDAMAGE worth using, and x11vnc's polling is what
                # actually keeps the screen current on it.
                "-noxdamage",
                # Answering a CAPTCHA is typing, so the keyboard has to survive
                # the trip: -xkb keeps modifiers straight and -add_keysyms binds
                # characters the bare X server has no key for.
                "-xkb",
                "-add_keysyms",
                # Its own log, in the session directory, rather than mixed into
                # the recorder's stdout.
                "-o", str(self._log_path()),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _log_path(self) -> Path:
        return Path(self._output_dir) / "x11vnc.log"

    def _store_password(self, password: str) -> Path:
        """Writes the password in the format x11vnc reads, and only for us.

        Through `-storepasswd` rather than by hand: the file is obfuscated with a
        fixed DES key that is x11vnc's business, not this module's. It is written
        0600 by x11vnc itself, and the passing of it on the command line is
        deliberate - the alternative, `-passwd`, leaves it in the long-running
        server's argv where every `ps` in the container can read it.
        """
        path = Path(self._output_dir) / "vnc_passwd"
        subprocess.run(
            [_X11VNC_BINARY, "-storepasswd", password, str(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def stop(self) -> None:
        _stop(self._process, _X11VNC_BINARY, self._logger)
        self._process = None
        self._endpoint = None
        self._cleanup_password_file()

    def _cleanup_password_file(self) -> None:
        # The password opened one challenge on one port and both are gone; what
        # is left is a credential sitting in a directory that outlives the run.
        if self._passwd_file:
            try:
                self._passwd_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._passwd_file = None


def _port_is_free(host: str, port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def _wait_until_serving(host: str, port: int, timeout_s: float = _VNC_LISTEN_TIMEOUT_S) -> bool:
    """Whether a VNC client could connect here, tested by being one.

    A TCP connect alone would answer 'yes' to anything listening. The RFB banner
    is the server saying what it is, which is the thing being promised to the
    person who is about to be asked to connect.
    """
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with closing(socket.create_connection((connect_host, port), timeout=2.0)) as client:
                client.settimeout(2.0)
                if client.recv(12).startswith(b"RFB"):
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


class CaptchaHandoff:
    """The wait for a person, and everything that makes the wait answerable.

    Handed to google_sign_in.sign_in as its `captcha_solver`: it is called with
    the page the moment the challenge is recognised, and blocks until the
    challenge is gone from it or the wait runs out. True means somebody answered
    it and the sign-in carries on from where it was; False means nobody came, and
    the sign-in ends the way it always has.

    Deliberately not an answer: nothing here reads the image or types into the
    field. The challenge is asking whether there is a person, and the honest way
    to pass it is for there to be one.
    """

    def __init__(
        self,
        display: VirtualDisplay,
        output_dir: str,
        host: str,
        port: int,
        password: str,
        timeout_s: float,
        logger,
        meeting_url: str | None = None,
        screenshot: Callable[[str], str | None] | None = None,
    ):
        self._display = display
        self._output_dir = output_dir
        self._timeout_s = timeout_s
        self._logger = logger
        self._meeting_url = meeting_url
        self._screenshot = screenshot
        self._server = VncServer(
            display=display.display or "",
            host=host,
            port=port,
            password=password,
            output_dir=output_dir,
            logger=logger,
        )

    def solve(self, page) -> bool:
        """Shows the screen to whoever is out there, and waits for the answer."""
        # Imported here rather than at the top because the API imports this
        # module for pending_challenges() alone, and answering 'is anything
        # waiting?' should not pull the browser join flow in behind it.
        from gravai.recording.common.google_sign_in import captcha_visible

        try:
            endpoint = self._server.start()
        except VncUnavailable as exc:
            self._logger.error(
                f"Google is showing a CAPTCHA and it cannot be handed to anybody: {exc}"
            )
            return False

        shot = self._screenshot("google_captcha") if self._screenshot else None
        deadline = time.time() + self._timeout_s
        self._write_challenge("waiting", endpoint, shot, deadline)
        self._announce(endpoint, shot)

        while time.time() < deadline:
            if not captcha_visible(page):
                waited = self._timeout_s - (deadline - time.time())
                self._logger.info(
                    f"CAPTCHA answered after {waited:.0f}s; carrying on with the sign-in"
                )
                self._write_challenge("solved", endpoint, shot, deadline)
                return True
            time.sleep(_SOLVED_POLL_INTERVAL_S)

        self._logger.error(
            f"Nobody answered the CAPTCHA within {self._timeout_s:.0f}s, so the sign-in is "
            f"over. VNC_CAPTCHA_TIMEOUT_S is what that wait is."
        )
        self._write_challenge("timed_out", endpoint, shot, deadline)
        return False

    def close(self) -> None:
        """Takes the screen back off the network.

        Called when the sign-in ends, however it ended. What is on that screen
        by then is a browser holding a Google session, which is precisely what
        should not be left on an open port for the hours a meeting runs.
        """
        if self._server.running:
            self._logger.info("Closing the VNC session opened for the CAPTCHA")
        self._server.stop()
        self._mark_closed()

    def _announce(self, endpoint: VncEndpoint, shot: str | None) -> None:
        """The message somebody has to see for any of this to be worth doing."""
        credential = (
            f"password: {endpoint.password}"
            if endpoint.password_is_generated
            else "password: the one configured in VNC_PASSWORD"
        )
        self._logger.warning(
            "Google is showing a CAPTCHA on the sign-in page. It asks for a person to "
            "read an image, so the recorder's screen is now on VNC and the sign-in is "
            f"waiting up to {self._timeout_s / 60:.0f} min for one:\n"
            f"    {endpoint.where_to_connect}\n"
            f"    {credential}\n"
            + (f"    what it looks like: {shot}\n" if shot else "")
            + f"    waiting challenge recorded in: {self._challenge_path()}\n"
            "    Type the characters and press Next, then leave the browser alone - the "
            "recorder types the password and joins the meeting itself."
        )

    def _challenge_path(self) -> Path:
        return Path(self._output_dir) / CHALLENGE_FILENAME

    def _write_challenge(
        self, state: str, endpoint: VncEndpoint, shot: str | None, deadline: float
    ) -> None:
        """Leaves the challenge somewhere a program can find it.

        The recording holds an HTTP request open while it runs, so the reply
        cannot carry this: the API answers 'what is waiting?' by reading these
        files instead. See pending_challenges().
        """
        payload = {
            "state": state,
            "session_dir": self._output_dir,
            "meeting_url": self._meeting_url,
            "vnc_host": endpoint.host,
            "vnc_port": endpoint.port,
            "vnc_url": endpoint.url,
            # Only ever the throwaway one. A configured VNC_PASSWORD is the
            # operator's own secret and does not get copied into a session
            # directory to make it easier to look up.
            "vnc_password": endpoint.password if endpoint.password_is_generated else None,
            "screenshot_path": shot,
            "expires_at": datetime.fromtimestamp(deadline, UTC).isoformat(timespec="seconds"),
            "updated_at": _now(),
        }
        try:
            path = self._challenge_path()
            # Written whole and moved into place: the API reads this file from
            # another process, and a half-written one there reads as no
            # challenge at all.
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        except OSError as exc:
            self._logger.warning(f"Could not record the waiting CAPTCHA: {exc}")

    def _mark_closed(self) -> None:
        """Stops a challenge nobody answered from looking like it is still open."""
        path = self._challenge_path()
        try:
            if not path.exists():
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("state") == "waiting":
            payload["state"] = "abandoned"
        # The port is closed and the password is gone, whatever the state.
        payload["vnc_password"] = None
        payload["updated_at"] = _now()
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass


def pending_challenges(save_dir: str) -> list[dict]:
    """Every CAPTCHA currently waiting for somebody, across all recordings.

    Read off the session directories rather than held in memory: the recording
    that is waiting runs in a process of its own, and the API that gets asked
    about it is not that process.
    """
    root = Path(save_dir)
    if not root.is_dir():
        return []

    waiting = []
    for path in sorted(root.glob(f"*/{CHALLENGE_FILENAME}")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("state") != "waiting":
            continue
        # A recording that was killed outright never got to mark its challenge
        # abandoned, so a stale 'waiting' outlives it. The expiry it wrote down
        # is what tells the two apart.
        if _has_expired(payload.get("expires_at")):
            continue
        waiting.append(payload)
    return waiting


def _has_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    try:
        return datetime.fromisoformat(expires_at) <= datetime.now(UTC)
    except ValueError:
        return False
