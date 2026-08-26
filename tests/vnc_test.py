"""Guards the mechanism that puts a person in front of a CAPTCHA.

Two halves, and they fail differently. The processes - Xvfb and x11vnc - are
only worth testing against the real binaries: what is being claimed is that a
VNC client can reach the screen the browser draws on and that nothing else can,
and a mock of x11vnc would agree with whatever this module believed. They skip
where the binaries are absent rather than pretending.

The handoff around them is the opposite: what it does is wait, so the real
version of it is minutes long and the parts worth pinning down - what it decides,
and what it leaves behind for somebody to find - are decided in seconds against a
page that is a dictionary and a server that is a flag.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gravai.recording.common import vnc
from gravai.recording.common.vnc import (
    CHALLENGE_FILENAME,
    CaptchaHandoff,
    VirtualDisplay,
    VncServer,
    VncUnavailable,
    pending_challenges,
)

needs_xvfb = pytest.mark.skipif(not vnc.xvfb_available(), reason="Xvfb is not installed")
needs_x11vnc = pytest.mark.skipif(not vnc.vnc_available(), reason="x11vnc is not installed")


class FakeLogger:
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    def __getattr__(self, level):
        def record(message):
            self.messages.append((level, str(message)))

        return record

    def text(self) -> str:
        return "\n".join(message for _, message in self.messages)


@pytest.fixture
def logger():
    return FakeLogger()


@pytest.fixture
def display(logger):
    screen = VirtualDisplay(width=800, height=600, logger=logger)
    if not screen.start():
        pytest.skip("Xvfb would not start here")
    yield screen
    screen.stop()


# --------------------------------------------------------------------------
# The screen
# --------------------------------------------------------------------------


@needs_xvfb
def test_the_display_is_a_screen_an_x_client_can_actually_open(display):
    assert display.active
    number = display.display.lstrip(":")
    # The socket is the X server saying it is ready for connections, which is
    # what -displayfd promises and what the browser is about to rely on.
    assert Path(f"/tmp/.X11-unix/X{number}").exists()


@needs_xvfb
def test_the_browser_is_pointed_at_the_display_through_the_environment(display):
    environment = display.env({"PATH": "/usr/bin"})

    assert environment["DISPLAY"] == display.display
    # The rest of the environment comes along: Chrome is launched with this and
    # nothing else, so dropping it would launch the browser without a PATH.
    assert environment["PATH"] == "/usr/bin"


@needs_xvfb
def test_two_recordings_never_get_handed_the_same_screen(logger):
    # Xvfb picks the number and reports it back, so two recordings starting in
    # the same second cannot both be told to draw on :99. Picking one here by
    # looking for a free number is the same check with a race in the middle.
    first = VirtualDisplay(width=320, height=240, logger=logger)
    second = VirtualDisplay(width=320, height=240, logger=logger)
    try:
        assert first.start() and second.start()
        assert first.display != second.display
    finally:
        first.stop()
        second.stop()


@needs_xvfb
def test_stopping_the_display_leaves_no_x_server_behind(logger):
    screen = VirtualDisplay(width=320, height=240, logger=logger)
    assert screen.start()
    number = screen.display.lstrip(":")

    screen.stop()

    assert not screen.active
    assert screen.display is None
    deadline = time.time() + 5
    while time.time() < deadline and Path(f"/tmp/.X11-unix/X{number}").exists():
        time.sleep(0.1)
    assert not Path(f"/tmp/.X11-unix/X{number}").exists()


@needs_xvfb
@pytest.mark.skipif(shutil.which("setpriv") is None, reason="setpriv is not installed")
def test_a_recording_killed_outright_leaves_no_screen_behind():
    """The case stop() cannot cover: the recorder is killed, not asked to stop.

    An X server has no parent left to answer to and would sit there holding a
    display number for as long as the machine is up - and when it is x11vnc on
    the same display, what is held open is a port that types into a browser. The
    kernel is what closes that window, through setpriv --pdeathsig.
    """
    source = Path(vnc.__file__).parents[3]
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from gravai.recording.common.vnc import VirtualDisplay\n"
            "screen = VirtualDisplay(width=320, height=240)\n"
            "assert screen.start()\n"
            "print(screen.display.lstrip(':'), flush=True)\n"
            "time.sleep(120)\n",
        ],
        stdout=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(source)},
        text=True,
    )
    try:
        number = child.stdout.readline().strip()
        socket_path = Path(f"/tmp/.X11-unix/X{number}")
        assert socket_path.exists()

        # Not terminate(): the point is the death nothing gets to clean up after.
        child.kill()
        child.wait(timeout=10)

        deadline = time.time() + 15
        while time.time() < deadline and socket_path.exists():
            time.sleep(0.2)
        assert not socket_path.exists(), f"display :{number} outlived the recording that started it"
    finally:
        child.kill()


def test_a_missing_xvfb_is_a_headless_recording_and_not_a_failed_one(monkeypatch, logger):
    # A CAPTCHA is an unlikely end to a join. Refusing to record at all because
    # the thing that would have handled one is missing gets that backwards.
    monkeypatch.setattr(vnc.shutil, "which", lambda _binary: None)
    screen = VirtualDisplay(width=800, height=600, logger=logger)

    assert screen.start() is False
    assert screen.active is False
    assert "DISPLAY" not in screen.env({})
    assert "Xvfb is not installed" in logger.text()


# --------------------------------------------------------------------------
# The server
# --------------------------------------------------------------------------


def _rfb_greeting(host: str, port: int) -> tuple[bytes, list[int]]:
    """Connects as a VNC viewer would, and reports what the server offers."""
    host = "127.0.0.1" if host == "0.0.0.0" else host
    with closing(socket.create_connection((host, port), timeout=5)) as client:
        client.settimeout(5)
        banner = client.recv(12)
        client.sendall(b"RFB 003.008\n")
        count = client.recv(1)[0]
        return banner, list(client.recv(count))


@needs_x11vnc
def test_the_server_serves_the_display_and_asks_for_the_password(display, logger, tmp_path):
    server = VncServer(
        display=display.display,
        host="127.0.0.1",
        port=_free_port(),
        password="hunter2",
        output_dir=str(tmp_path),
        logger=logger,
    )
    try:
        endpoint = server.start()
        banner, security_types = _rfb_greeting(endpoint.host, endpoint.port)

        assert banner.startswith(b"RFB")
        # 2 is VNC authentication and 1 is none at all. A screen showing a
        # browser signed into a Google account is not something to serve to
        # whoever connects first.
        assert security_types == [2]
        assert endpoint.url == f"vnc://127.0.0.1:{endpoint.port}"
    finally:
        server.stop()


@needs_x11vnc
def test_a_password_longer_than_vnc_allows_is_reported_as_what_will_work(display, logger, tmp_path):
    # The protocol truncates to 8 characters. Logging the whole of a longer one
    # sends somebody to a prompt their password does not open.
    server = VncServer(
        display=display.display,
        host="127.0.0.1",
        port=_free_port(),
        password="a-very-long-passphrase",
        output_dir=str(tmp_path),
        logger=logger,
    )
    try:
        endpoint = server.start()
        assert endpoint.password == "a-very-l"
        assert endpoint.password_is_generated is False
    finally:
        server.stop()


@needs_x11vnc
def test_a_challenge_with_no_configured_password_gets_one_of_its_own(display, logger, tmp_path):
    server = VncServer(
        display=display.display,
        host="127.0.0.1",
        port=_free_port(),
        password="",
        output_dir=str(tmp_path),
        logger=logger,
    )
    try:
        endpoint = server.start()
        assert endpoint.password_is_generated is True
        assert len(endpoint.password) == 8
        _, security_types = _rfb_greeting(endpoint.host, endpoint.port)
        assert security_types == [2]
    finally:
        server.stop()


@needs_x11vnc
def test_a_taken_port_is_walked_past_rather_than_waited_on(display, logger, tmp_path):
    # Two recordings can be waiting on a person at once, and the second one
    # asking for a port the first is already using is not a reason to fail it.
    taken = _free_port()
    with closing(socket.socket()) as squatter:
        squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        squatter.bind(("127.0.0.1", taken))
        squatter.listen(1)

        server = VncServer(
            display=display.display,
            host="127.0.0.1",
            port=taken,
            password="hunter2",
            output_dir=str(tmp_path),
            logger=logger,
        )
        try:
            endpoint = server.start()
            assert endpoint.port > taken
            assert _rfb_greeting(endpoint.host, endpoint.port)[0].startswith(b"RFB")
        finally:
            server.stop()


@needs_x11vnc
def test_stopping_the_server_closes_the_port_and_takes_the_password_with_it(
    display, logger, tmp_path
):
    server = VncServer(
        display=display.display,
        host="127.0.0.1",
        port=_free_port(),
        password="hunter2",
        output_dir=str(tmp_path),
        logger=logger,
    )
    endpoint = server.start()
    assert (tmp_path / "vnc_passwd").exists()

    server.stop()

    assert server.running is False
    # The password file outlives the run otherwise, in a directory that is kept.
    assert not (tmp_path / "vnc_passwd").exists()
    with pytest.raises(OSError):
        with closing(socket.create_connection(("127.0.0.1", endpoint.port), timeout=2)):
            pass


def test_a_screen_that_cannot_be_prepared_is_reported_as_one_that_cannot_be_offered(
    monkeypatch, logger, tmp_path
):
    # Whatever goes wrong on the way to a server, what the sign-in has to be
    # told is that there is nothing to show anybody - not the subprocess error
    # underneath it, which would end the recording talking about the wrong thing.
    monkeypatch.setattr(
        VncServer, "_store_password", lambda *_args: (_ for _ in ()).throw(OSError("read-only"))
    )
    server = VncServer(
        display=":99",
        host="127.0.0.1",
        port=_free_port(),
        password="hunter2",
        output_dir=str(tmp_path),
        logger=logger,
    )

    with pytest.raises(VncUnavailable, match="password file could not be written"):
        server.start()


def test_without_x11vnc_the_screen_simply_cannot_be_offered(monkeypatch, logger, tmp_path):
    monkeypatch.setattr(vnc.shutil, "which", lambda _binary: None)
    server = VncServer(
        display=":99",
        host="127.0.0.1",
        port=5900,
        password="hunter2",
        output_dir=str(tmp_path),
        logger=logger,
    )

    with pytest.raises(VncUnavailable, match="x11vnc is not installed"):
        server.start()


def _free_port() -> int:
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --------------------------------------------------------------------------
# The wait
# --------------------------------------------------------------------------


class FakeServer:
    """An x11vnc that is a boolean."""

    def __init__(self, *_args, port: int = 5900, fails: bool = False, **_kwargs):
        self.running = False
        self.started = 0
        self.stopped = 0
        self._port = port
        self._fails = fails

    def start(self):
        if self._fails:
            raise VncUnavailable("x11vnc is not installed")
        self.started += 1
        self.running = True
        return vnc.VncEndpoint(
            host="0.0.0.0", port=self._port, password="abc12345", password_is_generated=True
        )

    def stop(self):
        self.stopped += 1
        self.running = False


class ChallengePage:
    """A page showing the challenge until a given number of looks have passed."""

    def __init__(self, clears_after: int | None = 2):
        self._clears_after = clears_after
        self.looks = 0

    def locator(self, selector):
        self.looks += 1
        visible = self._clears_after is None or self.looks <= self._clears_after
        return _Locator(visible and selector == "input#ca:visible")


class _Locator:
    def __init__(self, visible):
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible


@pytest.fixture
def handoff_factory(monkeypatch, logger, tmp_path):
    servers: list[FakeServer] = []

    def build(timeout_s: float = 5.0, fails: bool = False, **kwargs) -> CaptchaHandoff:
        def fake_server(*args, **server_kwargs):
            server = FakeServer(fails=fails)
            servers.append(server)
            return server

        monkeypatch.setattr(vnc, "VncServer", fake_server)
        screen = VirtualDisplay(width=10, height=10, logger=logger)
        screen._display = ":42"
        return CaptchaHandoff(
            display=screen,
            output_dir=str(tmp_path),
            host="0.0.0.0",
            port=5900,
            password="",
            timeout_s=timeout_s,
            logger=logger,
            meeting_url="https://meet.google.com/abc-defg-hij",
            **kwargs,
        )

    build.servers = servers  # type: ignore[attr-defined]
    return build


def _challenge(tmp_path) -> dict:
    return json.loads((Path(tmp_path) / CHALLENGE_FILENAME).read_text())


def test_the_challenge_is_answered_when_it_leaves_the_page(handoff_factory, tmp_path, logger):
    handoff = handoff_factory()

    assert handoff.solve(ChallengePage(clears_after=1)) is True

    # Everything a person needs to get to it was said out loud, not only filed.
    assert "vnc://0.0.0.0:5900" in logger.text()
    assert "abc12345" in logger.text()
    # And 0.0.0.0 is explained rather than left as an address to try to type.
    assert "every interface" in logger.text()
    assert _challenge(tmp_path)["state"] == "solved"


def test_an_address_somebody_can_actually_use_is_left_alone():
    endpoint = vnc.VncEndpoint(
        host="10.0.0.4", port=5901, password="abc12345", password_is_generated=True
    )

    assert endpoint.where_to_connect == "vnc://10.0.0.4:5901"


def test_nobody_coming_ends_the_attempt_rather_than_the_recording_waiting_out_the_call(
    handoff_factory, tmp_path
):
    handoff = handoff_factory(timeout_s=1.0)

    # The challenge never clears: nobody connected.
    assert handoff.solve(ChallengePage(clears_after=None)) is False
    assert _challenge(tmp_path)["state"] == "timed_out"


def test_a_screen_that_cannot_be_served_is_not_a_wait_at_all(handoff_factory, tmp_path, logger):
    # Blocking for ten minutes on a VNC server that never started asks somebody
    # to connect to nothing, and costs the join the whole timeout to say so.
    handoff = handoff_factory(timeout_s=600.0, fails=True)

    started = time.time()
    assert handoff.solve(ChallengePage(clears_after=None)) is False
    assert time.time() - started < 5
    assert not (Path(tmp_path) / CHALLENGE_FILENAME).exists()


def test_the_screenshot_of_the_challenge_is_recorded_with_it(handoff_factory, tmp_path):
    shots = []
    handoff = handoff_factory(screenshot=lambda name: shots.append(name) or f"/shots/{name}.png")

    handoff.solve(ChallengePage(clears_after=1))

    assert shots == ["google_captcha"]
    assert _challenge(tmp_path)["screenshot_path"] == "/shots/google_captcha.png"


def test_the_port_closes_with_the_sign_in_and_the_record_stops_saying_come_and_help(
    handoff_factory, tmp_path
):
    handoff = handoff_factory(timeout_s=0.5)
    handoff.solve(ChallengePage(clears_after=None))
    assert _challenge(tmp_path)["vnc_password"] == "abc12345"

    handoff.close()

    server = handoff_factory.servers[0]  # type: ignore[attr-defined]
    assert server.stopped == 1
    # The password opened a port that is now closed, so leaving it written down
    # in a directory that outlives the run buys nothing.
    assert _challenge(tmp_path)["vnc_password"] is None


def test_a_challenge_nobody_ever_answered_is_not_left_looking_open(handoff_factory, tmp_path):
    handoff = handoff_factory(timeout_s=30.0)
    # Interrupted mid-wait, which is what a failed sign-in does to it.
    handoff._write_challenge(
        "waiting",
        vnc.VncEndpoint(host="0.0.0.0", port=5900, password="abc12345", password_is_generated=True),
        None,
        time.time() + 30,
    )

    handoff.close()

    assert _challenge(tmp_path)["state"] == "abandoned"


# --------------------------------------------------------------------------
# Finding one from somewhere else
# --------------------------------------------------------------------------


def _write_challenge(directory: Path, **overrides) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "state": "waiting",
        "session_dir": str(directory),
        "meeting_url": "https://meet.google.com/abc-defg-hij",
        "vnc_host": "0.0.0.0",
        "vnc_port": 5900,
        "vnc_url": "vnc://0.0.0.0:5900",
        "vnc_password": "abc12345",
        "screenshot_path": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds"),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    payload.update(overrides)
    (directory / CHALLENGE_FILENAME).write_text(json.dumps(payload))


def test_a_waiting_challenge_is_findable_without_reading_a_log(tmp_path):
    _write_challenge(tmp_path / "2026_08_25_one_tracks")

    waiting = pending_challenges(str(tmp_path))

    assert len(waiting) == 1
    assert waiting[0]["vnc_url"] == "vnc://0.0.0.0:5900"


def test_a_challenge_already_dealt_with_is_not_still_asking_for_help(tmp_path):
    _write_challenge(tmp_path / "solved_tracks", state="solved")
    _write_challenge(tmp_path / "abandoned_tracks", state="abandoned")

    assert pending_challenges(str(tmp_path)) == []


def test_a_challenge_whose_recording_was_killed_stops_asking_when_it_expires(tmp_path):
    # A recording killed outright never gets to mark its own challenge closed,
    # so the file it left says 'waiting' for as long as the directory is kept.
    _write_challenge(
        tmp_path / "killed_tracks",
        expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds"),
    )

    assert pending_challenges(str(tmp_path)) == []


def test_nothing_waiting_is_the_ordinary_answer(tmp_path):
    assert pending_challenges(str(tmp_path)) == []
    assert pending_challenges(str(tmp_path / "not-a-directory")) == []


def test_a_half_written_record_is_not_read_as_a_challenge(tmp_path):
    # It is written by another process while this one reads the directory.
    directory = tmp_path / "racing_tracks"
    directory.mkdir()
    (directory / CHALLENGE_FILENAME).write_text('{"state": "wait')

    assert pending_challenges(str(tmp_path)) == []
