import os
import re
import time
from multiprocessing import Queue
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from pydantic import model_validator
import json

from gravai.config.logging_config import get_logger
from gravai.config.settings import get_settings
from gravai.recording.utils import _meeting_origin, _load_text, _write_vad_timeline

from gravai.recording.common.browser_persona import (
    WINDOWS_CHROME,
    apply_persona,
    context_kwargs,
    init_script,
    launch_kwargs,
)
from gravai.recording.common.google_sign_in import (
    GoogleSignInError,
    back_on_meet,
    configured_credentials,
    on_sign_in_page,
    sign_in,
)
from gravai.recording.common.human_input import (
    click_if_present,
    move_and_click,
    pause,
    type_like_a_person,
)
from gravai.recording.common.vnc import CaptchaHandoff, VirtualDisplay

from ..common.provider_base import MeetingRecorder

# Meet has no stable ids to hook on - its class names are obfuscated and rotate -
# so the join flow is driven by accessible names and visible copy, which do not.
_GUEST_NAME = "Bot de Gravação de Pedro Silva"

# Anyone in the call should be able to tell from the participant list that they
# are being recorded by a bot, so the name it appears under has to say so.
#
# Which name that is depends on the path. As a guest it is _GUEST_NAME, typed by
# this flow and checkable here. Signed in, it is the Google account's own display
# name, which the recorder cannot set and cannot read either - it is not in any
# aria-label, not on Google's password page, and in the green room it sits in a
# div whose only handle is an obfuscated class that rotates between releases. The
# roster is where it does become observable, under the hook vad_observer.js
# already maintains, so that is where a signed-in join is checked.
_BOT_NAME_PREFIX = "bot"


def _names_the_bot_could_be(roster) -> list[str]:
    return [
        entry.get("participantName")
        for entry in (roster or [])
        if isinstance(entry, dict) and entry.get("participantName")
    ]


# How long the roster is given to render a tile before the name check gives up.
# Only the check waits on this; the recording is already running.
_ROSTER_TIMEOUT_S = 30.0


def _wait_for_roster(page, logger, timeout_s: float = _ROSTER_TIMEOUT_S):
    """The roster, once it has a named participant in it.

    Returns whatever it has when the window closes, empty included - the caller
    treats that as nothing known rather than as an answer.
    """
    deadline = time.time() + timeout_s
    while True:
        try:
            roster = page.evaluate(
                "() => window.__vadSnapshotRoster ? window.__vadSnapshotRoster() : []"
            )
        except Exception:
            roster = []
        if _names_the_bot_could_be(roster):
            return roster
        if time.time() >= deadline:
            logger.info(f"[vad] roster still empty after {timeout_s:.0f}s")
            return roster
        page.wait_for_timeout(_POLL_INTERVAL_MS)


def _warn_unless_named_as_a_bot(roster, logger) -> None:
    """Warns when nobody in the call is named like a recording bot.

    Checked against the whole roster rather than against the bot's own tile,
    because which tile is the bot's is precisely what an unrecognisable name
    makes unanswerable. A name starting with 'Bot' anywhere in the call is
    therefore taken as the bot having introduced itself.
    """
    names = _names_the_bot_could_be(roster)
    if not names:
        # Nothing was read, so nothing is known - and a warning that fires on no
        # evidence is one people learn to skip past.
        return
    if any(name.strip().casefold().startswith(_BOT_NAME_PREFIX) for name in names):
        return
    logger.warning(
        f"No participant is named like a recording bot (roster: {names}). The recorder "
        f"joined under the Google account's own display name, which does not start with "
        f"'Bot', so nobody in the call can tell from the participant list that a bot is "
        f"recording. Rename the account used by GOOGLE_ACCOUNT_EMAIL to start with 'Bot'."
    )

# From landing on the url to the green room being interactive. Meet spends a
# while on "Getting ready..." before it renders anything to click.
_GREEN_ROOM_TIMEOUT_S = 90.0

# An anonymous guest is admitted by a human, so this is a wait on a person.
_ADMISSION_TIMEOUT_S = 300.0

_POLL_INTERVAL_MS = 1000

# Reading the page text is cheap but not free, and a call runs for hours - once
# every couple of seconds is plenty to notice it ended.
_CALL_POLL_INTERVAL_MS = 2000

# Every way Meet says no. The distinction matters: a denial is someone deciding,
# while "can't join" is the call refusing anonymous guests outright - retrying
# the second one with the same setup will always fail the same way.
_REFUSALS: tuple[tuple[str, str], ...] = (
    (
        # Meet serves its copy in the browser's language, so every screen this
        # flow reads has to be recognised in Portuguese too - a Brazilian host's
        # meeting renders in pt-BR, where the English matcher sees nothing and
        # the bot waits out its timeout instead of reporting what happened.
        "não é possível participar desta videochamada",
        "There is no meeting an anonymous guest can enter at this link: it has ended, "
        "has not started, or the host's organisation blocks anonymous participants. "
        "Meet shows this same screen for a meeting code that never existed, so it says "
        "nothing more specific than that. Check with a link that is live right now.",
    ),
    (
        "You can't join this video call",
        "There is no meeting an anonymous guest can enter at this link: it has ended, "
        "has not started, or the host's organisation blocks anonymous participants. "
        "Meet shows this same screen for a meeting code that never existed, so it says "
        "nothing more specific than that. Check with a link that is live right now.",
    ),
    (
        "denied your request to join",
        "Someone in the call denied the request to join.",
    ),
    (
        "No one responded to your request to join",
        "Nobody admitted the guest before Meet gave up on the request.",
    ),
    (
        "Check your meeting code",
        "Meet does not recognise this meeting code.",
    ),
)

# Meet refuses ResolveMeetingSpace with a 403 for a call that will not take an
# anonymous guest and sends the tab to accounts.google.com, which is where the
# sign-in in common/google_sign_in.py takes over. With no account configured
# that redirect is simply the end of the run, and this is what it means.
#
# The navigation trails the 403 by anything from a few seconds to tens of them,
# which is why the green room still needs its own timeout: the redirect is
# noticed whenever it lands, it is not promised to land first.
_NO_ACCOUNT_REASON = (
    "Meet sent the guest to the Google sign-in page, which means this call does not "
    "admit anonymous guests: either the host's organisation blocks them, or the "
    "meeting is not live - Meet asks an anonymous visitor to sign in for a code with "
    "no host in it. Set GOOGLE_ACCOUNT_EMAIL and GOOGLE_ACCOUNT_PASSWORD to have the "
    "recorder sign in for meetings like this one."
)

# The green room interrupts itself with these, and each one has to be answered
# rather than waited out: they are modal, so the rest of the document goes inert
# while one is up and the join button leaves the accessibility tree with it.
# get_by_role then matches nothing at all, which reads exactly like a green room
# that never finished loading.
#
# In both languages, and not because the browser might be Portuguese - the
# persona pins it to en-US - but because Meet serves a *signed-in* account its
# own language: a pt-BR account gets this dialog in pt-BR from an en-US browser,
# which is how it went unanswered for a full timeout once the sign-in started
# working.
#
# Matched on the distinctive fragment rather than the whole sentence, so an
# accent or a rewording does not take the match with it. Order matters: each is
# the option that declines, and the mic/camera dialog also offers to grant.
_GREEN_ROOM_DISMISSALS = (
    r"continue without microphone",
    r"negar o acesso",
    r"^got it$",
    r"^entendi$",
    r"^dismiss$",
    r"^dispensar$",
)

# Meet's end-of-call screens, the counterpart of the Teams 'Did you leave by
# mistake?' banner.
_MEETING_ENDED_TEXTS: tuple[str, ...] = (
    "You've left the meeting",
    "You have left the meeting",
    "You've been removed from the meeting",
    "Your host ended the meeting for everyone",
    "Return to home screen",
    "Você saiu da reunião",
    "Você foi removido da reunião",
    "Voltar à tela inicial",
)


class MeetJoinError(RuntimeError):
    """Raised when the bot could not get into the call."""


def _start_virtual_display(logger) -> VirtualDisplay | None:
    """The screen this recording's browser draws on, when it is to have one.

    A window is not something that can be given to a browser that is already
    running, so this is decided before the launch and not at the CAPTCHA that
    needs it: by the time Google asks for a person, a headless Chrome has no
    screen to show them and no way to grow one. The cost of being wrong the
    other way is an X server nobody looks at.

    The size is the persona's, so that what a person sees over VNC is the same
    window Meet is being told about - a screen that does not match the window it
    is showing is a discrepancy on the more heavily defended half of Google.

    None means headless, which is where every failure here lands: a CAPTCHA is
    an unlikely end to a join, and a missing Xvfb is no reason not to attempt
    one. See common/vnc.py.
    """
    settings = get_settings()
    if not settings.VNC_ENABLED:
        return None

    viewport = WINDOWS_CHROME.viewport
    display = VirtualDisplay(width=viewport["width"], height=viewport["height"], logger=logger)
    if not display.start():
        return None
    return display


def _screenshot(page: Page, output_dir: str, name: str) -> str | None:
    """Screenshots into the session directory.

    Never into the working directory: two recordings running at once would
    overwrite each other's evidence, and the file would not belong to either
    session afterwards.
    """
    path = os.path.join(output_dir, f"{name}.png")
    try:
        page.screenshot(path=path)
        return path
    except Exception:
        return None


def _normalize(text: str) -> str:
    """Folds the apostrophes Google's copy alternates between, so a needle
    written with a plain ' keeps matching if the page switches to a curly one."""
    return text.replace("’", "'").replace("ʼ", "'").casefold()


def _page_text(page: Page) -> str:
    try:
        return _normalize(page.inner_text("body"))
    except Exception:
        # Navigating, or gone. Either way there is nothing to read this round.
        return ""


def _contains_any(page_text: str, needles: tuple[str, ...]) -> bool:
    return any(_normalize(needle) in page_text for needle in needles)


def _refusal_reason(page: Page) -> str | None:
    """Reads the whole visible page rather than matching element by element.

    Locator matching missed these screens for a full minute while they sat in
    plain view - the text is announced across several nodes and rewritten as the
    'returning to home screen' countdown runs - so the reliable question is
    simply whether the words are on the page.

    The sign-in redirect is not among these: it is read from the URL, by
    on_sign_in_page, because it is a screen the caller can act on rather than
    only report.
    """
    body = _page_text(page)
    if not body:
        return None
    for text, reason in _REFUSALS:
        if _normalize(text) in body:
            return reason
    return None


class MeetMeetingRecorder(MeetingRecorder):
    rtc_intercept_js_path: Path | None = None
    vad_observer_js_path: Path | None = None
    audio_worklet_js_path: Path | None = None

    @model_validator(mode="after")
    def assemble_es_hosts(self) -> "MeetMeetingRecorder":
        """Constructs the ES_HOSTS URL after model validation."""
        settings = get_settings()
        self.audio_worklet_js_path = self.audio_worklet_js_path or settings.AUDIO_WORKLET_JS_PATH
        self.rtc_intercept_js_path = self.rtc_intercept_js_path or settings.RTC_INTERCEPT_JS_PATH
        self.vad_observer_js_path = self.vad_observer_js_path or settings.VAD_OBSERVER_MEET_JS_PATH
        return self

    def prepare_injection(self, meeting_url: str, ws_url: str, session_id: str) -> tuple[str, str, list[dict], dict]:
        """Builds the scripts injected into the page, pointed at this session's
        audio server, which the caller has already started at ws_url."""
        worklet_path = self.audio_worklet_js_path
        intercept_path = self.rtc_intercept_js_path
        vad_observer_path = self.vad_observer_js_path

        assert worklet_path and intercept_path and vad_observer_path, "Expected worklet, intercept and vad observer paths to be set"

        worklet_js = _load_text(worklet_path)
        intercept_js = _load_text(intercept_path)
        vad_observer_js = _load_text(vad_observer_path)
        intercept_js = (
            intercept_js
            .replace("{{WS_URL}}", ws_url)
            .replace("{{WORKLET_CODE}}", json.dumps(worklet_js))
            # Meet reuses a handful of audio receivers between speakers, so no
            # single receiver is a participant and slicing has nothing to cut.
            # The mix is the track it cuts, using the VAD timeline for who.
            .replace("{{MAIN_MIX}}", "true")
        )
        vad_events: list[dict] = []
        vad_meta = {
            "session_id": session_id,
            "meeting_url": meeting_url,
            "page_start_ms": None,
            "page_end_ms": None,
        }

        return intercept_js, vad_observer_js, vad_events, vad_meta

    def _sign_in_for_meeting(
        self,
        page: Page,
        meeting_url: str,
        output_dir: str,
        logger,
        display: VirtualDisplay | None = None,
    ) -> str:
        """Signs into the configured account after Meet demanded one, and comes
        back to the meeting with a session. Returns the account's email, which
        is how its name is found on the page afterwards.

        Raises MeetJoinError when there is no account to use, or when Google
        would not take it - both end this join either way, and the difference is
        the whole content of the message.

        This is the only place a VNC session is ever opened, and only if Google
        asks for a CAPTCHA once inside it: signing in is the one part of a join
        where the recorder can be stopped by something that a person, and only a
        person, can answer. The server is taken back down before this returns,
        whichever way it went - what is on that screen afterwards is a browser
        holding a Google session.
        """
        credentials = configured_credentials()
        if credentials is None:
            shot = _screenshot(page, output_dir, "meet_refused")
            logger.error(f"Refused at the green room: {_NO_ACCOUNT_REASON}")
            raise MeetJoinError(f"{_NO_ACCOUNT_REASON} Screenshot: {shot}")

        handoff = self._captcha_handoff(page, meeting_url, output_dir, logger, display)
        try:
            sign_in(
                page,
                credentials,
                logger,
                screenshot=lambda name: _screenshot(page, output_dir, name),
                captcha_solver=handoff.solve if handoff else None,
            )
        except GoogleSignInError as exc:
            raise MeetJoinError(f"Could not sign in to Google: {exc}") from exc
        finally:
            if handoff:
                handoff.close()

        if not back_on_meet(page):
            # Google parks the tab on whatever it wanted to ask about - a
            # recovery phone, most often - and the way past those is to leave,
            # not to find the button that declines each one. The session is
            # already set, so the meeting loads signed in.
            logger.info("Sign-in stage: leaving Google's interstitial for the meeting")
            page.goto(meeting_url, wait_until="domcontentloaded")

        return credentials.email

    @staticmethod
    def _captcha_handoff(
        page: Page,
        meeting_url: str,
        output_dir: str,
        logger,
        display: VirtualDisplay | None,
    ) -> CaptchaHandoff | None:
        """What a CAPTCHA gets handed to, or None when there is nobody to hand it to.

        None whenever the browser is not on a display: headless Chrome draws no
        window, so there is no screen to show and nothing to click, and the
        sign-in goes back to treating the challenge as the end of the attempt.
        """
        settings = get_settings()
        if not (settings.VNC_ENABLED and display and display.active):
            return None
        return CaptchaHandoff(
            display=display,
            output_dir=output_dir,
            host=settings.VNC_HOST,
            port=settings.VNC_PORT,
            password=settings.VNC_PASSWORD.get_secret_value(),
            timeout_s=settings.VNC_CAPTCHA_TIMEOUT_S,
            logger=logger,
            meeting_url=meeting_url,
            screenshot=lambda name: _screenshot(page, output_dir, name),
        )

    def _join(
        self,
        page: Page,
        meeting_url: str,
        output_dir: str,
        debug: bool,
        logger,
        display: VirtualDisplay | None = None,
    ) -> tuple[str | None, str | None]:
        """Walks the green room and waits to be let in.

        As an anonymous guest by default: Meet asks for a name and offers 'Ask to
        join' rather than 'Join now', and someone already in the call has to
        admit us. A meeting that will not take a guest at all sends the tab to
        the Google sign-in page instead, and that is the one screen here with a
        way forward rather than only a way out.

        Returns the name to recognise the bot's own tile by - None when there is
        no such name - together with the account it signed in as, or None if it
        joined as a guest.
        """
        name_input = page.get_by_role("textbox", name=re.compile(r"your name|seu nome", re.I))
        join_button = page.get_by_role(
            "button", name=re.compile(r"ask to join|join now|pedir para participar|participar agora", re.I)
        )

        self_name: str | None = _GUEST_NAME
        account_email: str | None = None
        deadline = time.time() + _GREEN_ROOM_TIMEOUT_S
        while True:
            if on_sign_in_page(page):
                if account_email:
                    # Signed in, handed back to Meet, and bounced to the account
                    # page again. Whatever this call wants, the account does not
                    # have it, and trying once more only spends the credentials
                    # against Google's rate limit.
                    shot = _screenshot(page, output_dir, "meet_refused")
                    raise MeetJoinError(
                        "Meet asked for an account again after signing in, so the account "
                        "that was used is not one this meeting admits. "
                        f"Screenshot: {shot}"
                    )
                account_email = self._sign_in_for_meeting(
                    page, meeting_url, output_dir, logger, display
                )
                # The green room starts over on the other side of the redirect,
                # so it gets the full window rather than what the sign-in left.
                deadline = time.time() + _GREEN_ROOM_TIMEOUT_S
                continue

            reason = _refusal_reason(page)
            if reason:
                shot = _screenshot(page, output_dir, "meet_refused")
                logger.error(f"Refused at the green room: {reason}")
                raise MeetJoinError(f"{reason} Screenshot: {shot}")

            for dismissal in _GREEN_ROOM_DISMISSALS:
                if click_if_present(page, dismissal, timeout=500):
                    logger.info(f"Green room stage: dismissed {dismissal!r}")
                    # The dialog animates out, and the green room behind it
                    # re-renders before it has a join button to offer.
                    time.sleep(1.2)

            if join_button.first.is_visible():
                break
            if time.time() >= deadline:
                shot = _screenshot(page, output_dir, "meet_green_room_timeout")
                raise MeetJoinError(
                    f"Meet never offered a join button within {_GREEN_ROOM_TIMEOUT_S:.0f}s. "
                    f"Screenshot: {shot}"
                )
            page.wait_for_timeout(_POLL_INTERVAL_MS)

        if debug:
            _screenshot(page, output_dir, "meet_green_room")

        try:
            if name_input.first.is_visible():
                # Clicked into and typed rather than filled: the pointer has to
                # travel for the join to be accepted at all, and a name that
                # appears in one assignment with no keystrokes behind it is the
                # next thing of that kind worth not doing.
                move_and_click(page, name_input.first)
                # The beat between clicking a field and starting to type it.
                pause(page, 0.2, 0.7)
                type_like_a_person(page, _GUEST_NAME)
                logger.info("Green room stage: guest name typed in")
                if not _GUEST_NAME.strip().casefold().startswith(_BOT_NAME_PREFIX):
                    logger.warning(
                        f"The guest name {_GUEST_NAME!r} does not start with 'Bot', so nobody "
                        f"in the call can tell from the participant list that a bot is "
                        f"recording."
                    )
            elif account_email:
                # Expected on this path: a signed-in participant is named by the
                # account, so Meet has nothing to ask for.
                logger.info("Green room stage: signed in, no name to give")
            else:
                # Meet considers us signed in without this flow having signed in,
                # which is worth knowing when the join misbehaves.
                logger.info("Green room stage: no name field, joining without one")
        except Exception as exc:
            logger.warning(f"Green room stage: could not fill the guest name: {exc}")

        # Nothing should be published into the meeting, and a live mic would
        # also feed the bot's own audio back into the recording.
        for control in (r"turn off microphone", r"turn off camera"):
            if click_if_present(page, control, timeout=1500):
                logger.info(f"Green room stage: {control}")

        label = join_button.first.inner_text().strip() or "join"
        # The gap between finishing the name and deciding to press the button.
        pause(page, 0.8, 2.5)
        # This is the click Meet judges: it triggers CreateMeetingDevice, which
        # answers 403 to a pointer that teleported and 200 to one that travelled.
        move_and_click(page, join_button.first)
        logger.info(f"Green room stage: clicked {label!r}, waiting to be admitted")

        self._wait_for_admission(page, output_dir, logger)

        if account_email:
            # Nothing is excluded on this path, deliberately. The guest name
            # below is one this flow typed itself and can recognise; a signed-in
            # bot is labelled by its account, and that name is not
            # distinguishable from a participant's - here the recording account
            # and the meeting's host are both 'Pedro Silva', so excluding the
            # bot by name would take every event the host generated with it and
            # leave a call that transcribes as silence.
            #
            # The bot's own tile therefore stays in the timeline, which costs at
            # most a spurious participant. Doing better means recognising the
            # local tile by its participant id rather than its name.
            self_name = None
            logger.info(
                f"Joined as the account {account_email}; the bot's own tile is not excluded "
                f"from the VAD timeline, since a signed-in bot's name can be a participant's."
            )
        return self_name, account_email

    def _wait_for_admission(self, page: Page, output_dir: str, logger) -> None:
        """Blocks until we are in the call, or until Meet says we are not getting in."""
        in_call = page.get_by_role("button", name=re.compile(r"leave call|sair da chamada", re.I))
        deadline = time.time() + _ADMISSION_TIMEOUT_S

        while True:
            if in_call.first.is_visible():
                logger.info("Admitted into the meeting")
                return

            if on_sign_in_page(page):
                # Not worth signing in from here: the account was either already
                # used or deliberately not configured, and the join has been
                # asked for. Landing here means Meet withdrew the request.
                shot = _screenshot(page, output_dir, "meet_refused")
                raise MeetJoinError(
                    "Meet sent the tab to the Google sign-in page after the join was "
                    f"requested, so the request was dropped. Screenshot: {shot}"
                )

            reason = _refusal_reason(page)
            if reason:
                shot = _screenshot(page, output_dir, "meet_refused")
                logger.error(f"Not admitted: {reason}")
                raise MeetJoinError(f"{reason} Screenshot: {shot}")

            if time.time() >= deadline:
                shot = _screenshot(page, output_dir, "meet_admission_timeout")
                raise MeetJoinError(
                    f"Nobody admitted the guest within {_ADMISSION_TIMEOUT_S:.0f}s. "
                    f"Screenshot: {shot}"
                )
            page.wait_for_timeout(_POLL_INTERVAL_MS)

    # Is run on a separate process
    def record_meeting(self, meeting_url: str, q: Queue, output_dir: str, debug: bool, intercept_js, vad_observer_js, vad_events: list[dict], vad_meta: dict):
        logger = get_logger("recording.meet", output_dir)
        display = _start_virtual_display(logger)
        try:
            with sync_playwright() as p:
                # Meet turns this container away at ResolveMeetingSpace unless the
                # browser presents as an ordinary Windows desktop - see
                # common/browser_persona.py. If the channel is the same as
                # on bundled Chromium then it is refused, so this needs the real
                # Google Chrome build that the image installs.
                # Headless only when there is no display to draw on. A window on
                # a virtual screen is both what a person can be shown when Google
                # asks for one, and the less suspicious of the two to Google in
                # the first place - headless Chrome is a thing a page can notice.
                on_display = display is not None and display.active
                logger.info(
                    f"Launching Google Chrome as {WINDOWS_CHROME.name}, "
                    + (f"on display {display.display}" if on_display else "headless")  # type: ignore[union-attr]
                )
                launch = launch_kwargs(WINDOWS_CHROME, headless=not on_display, channel="chrome")
                browser = p.chromium.launch(
                    env=display.env() if on_display else None,  # type: ignore[union-attr]
                    **{
                        **launch,
                        "args": launch["args"] + [
                            "--use-fake-ui-for-media-stream",
                            # Not just auto-accepting the permission prompt but
                            # having devices to accept it with: this container has
                            # none, and Meet's green room says so out loud ('Mic
                            # not found'). A Windows desktop with no microphone at
                            # all contradicts the persona, and the media session is
                            # created from these devices.
                            "--use-fake-device-for-media-stream",
                            "--autoplay-policy=no-user-gesture-required",
                            # Chrome 151 blocks a page on a public site from
                            # opening a connection to a local address, so the
                            # intercept's ws:// to this session's audio server
                            # dies with ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS
                            # and every hooked track is dropped on the floor. The
                            # bundled Chromium the recorder used before the
                            # persona work did not enforce it, which is why this
                            # only appeared once the join started working.
                            # Binding the server elsewhere does not help - the
                            # check covers private addresses too - so the choice
                            # is this flag or serving wss:// with a real
                            # certificate.
                            "--disable-features=LocalNetworkAccessChecks",
                            "--enable-logging",
                            "--v=1",
                            "--vmodule=*webrtc*=3,*libjingle*=3",
                        ],
                    }
                )
                # The persona pins the locale to en-US, so the flow reads one
                # language regardless of the container's; the Portuguese matchers
                # stay as a backstop for when Meet follows the host's language
                # instead.
                context = browser.new_context(
                    bypass_csp=True, ignore_https_errors=True, **context_kwargs(WINDOWS_CHROME)
                )
                # First, so the page is already dressed when the intercept and the
                # observer run - and so it covers the frames Meet opens later.
                context.add_init_script(init_script(WINDOWS_CHROME))
                context.add_init_script(intercept_js)
                context.add_init_script(vad_observer_js)
                context.grant_permissions([], origin=_meeting_origin(meeting_url))

                page = context.new_page()
                page.set_default_timeout(20_000)

                # Need to be applied before the first navigation to ensure that
                # the header overrides are read when a request is built, and a
                # document already fetched keeps the headers it was fetched with.
                apply_persona(page, WINDOWS_CHROME)

                if debug:
                    # Errors during the connection phase are manifested as a
                    # 'you can't join this video call' screen, not the actual
                    # call that refused. Only the RPC status can distinguishes in which
                    # step the connection failed.
                    rpc_session = context.new_cdp_session(page)
                    rpc_session.send("Network.enable")

                    def _log_rpc(event):
                        response = event.get("response", {})
                        seen = response.get("url", "")
                        if "/$rpc/" in seen:
                            logger.info(f"[rpc] {seen.rsplit('/', 1)[-1]} {response.get('status')}")

                    rpc_session.on("Network.responseReceived", _log_rpc)

                    # The intercept and the observer report what they hooked
                    # through the page console, and nothing else reports it: a
                    # call that produces no audio looks identical, from the
                    # outside, to a call where nobody spoke.
                    page.on("console", lambda m: (
                        logger.info(f"[console] {m.type}: {m.text[:300]}")
                        if ("[rtc-intercept]" in m.text or "[vad]" in m.text or m.type == "error")
                        else None
                    ))
                    page.on("pageerror", lambda e: logger.warning(
                        f"[pageerror] {str(e).splitlines()[0][:300]}"
                    ))

                logger.info(f"Navigating to meeting URL: {meeting_url}")
                page.goto(meeting_url, wait_until="domcontentloaded")
                logger.info("Prejoin page DOM content loaded")
                if debug:
                    _screenshot(page, output_dir, "meet_prejoin")

                self_name, account_email = self._join(
                    page, meeting_url, output_dir, debug, logger, display
                )

                try:
                    vad_meta["page_start_ms"] = page.evaluate("() => Date.now()")
                    page.evaluate("() => { if (window.__vadSnapshotRoster) { window.__vadSnapshotRoster(); } }")
                except Exception as exc:
                    logger.warning(f"[vad] failed to initialize timeline: {exc}")

                if account_email:
                    # Not from the snapshot above: an invited account is admitted
                    # the instant it asks, and the roster read a moment later is
                    # empty because no tile has rendered yet - which reads as 'no
                    # evidence' and skips the check entirely. So it waits for the
                    # roster to have someone in it.
                    _warn_unless_named_as_a_bot(_wait_for_roster(page, logger), logger)

                logger.info("In the meeting, listening for audio activity...")

                def _drain_vad_events() -> None:
                    try:
                        new_events = page.evaluate(
                            """
                            () => {
                                const events = Array.isArray(window.__vadEvents) ? window.__vadEvents : [];
                                window.__vadEvents = [];
                                return events;
                            }
                            """
                        )
                    except Exception:
                        return
                    if new_events:
                        # The bot's own tile animates when it renders, and the bot
                        # cannot have spoken - its microphone is turned off in the
                        # green room before the join. Without this it enters the
                        # timeline as a participant and gets a track sliced for it.
                        #
                        # Which name that is depends on how the join went: the
                        # guest name when it joined as one, the account's own name
                        # when Meet demanded an account. A None means signing in
                        # worked but the name could not be read, and nothing is
                        # excluded - the roster is the only handle there is.
                        vad_events.extend(
                            event for event in new_events
                            if self_name is None
                            or event.get("data", {}).get("participantName") != self_name
                        )

                deadline = time.time() + 7_200
                activity_timeout = 600  # 10 minutes

                last_vad_event_time_update = time.time()
                last_vad_event_count = 0
                while True:
                    _drain_vad_events()
                    # New events imply the meeting is still ongoing and the bot
                    # was not left by himself on the meeting
                    if len(vad_events) != last_vad_event_count:
                        last_vad_event_time_update = time.time()
                        last_vad_event_count = len(vad_events)
                    # Checks for inactivity in VAD events to determine if the meeting has likely ended,
                    # as a fallback in case the end-of-meeting indicators are not detected
                    elif time.time() - last_vad_event_time_update > activity_timeout:
                        logger.info("No audio activity detected for a prolonged period, assuming meeting ended")
                        break

                    if _contains_any(_page_text(page), _MEETING_ENDED_TEXTS):
                        logger.info("Meeting end detected (left the meeting or was removed)")
                        break

                    if time.time() >= deadline:
                        logger.warning("Meeting end not detected, closing after timeout.")
                        break

                    page.wait_for_timeout(_CALL_POLL_INTERVAL_MS)

                _drain_vad_events()
                try:
                    vad_meta["page_end_ms"] = page.evaluate("() => Date.now()")
                except Exception as exc:
                    logger.warning(f"[vad] failed to capture end time: {exc}")
                try:
                    vad_path = os.path.join(output_dir, "vad_timeline.json")
                    _write_vad_timeline(vad_path, vad_meta, vad_events)
                    logger.info(f"[vad] timeline written to {vad_path}")
                except Exception as exc:
                    logger.warning(f"[vad] failed to write timeline: {exc}")

                context.close()
                browser.close()
                logger.info("Browser closed, recording session finished")

                q.put(("stop", None))
        except Exception as e:
            logger.exception(f"Error in recording process: {e}")
            q.put(("exception", str(e)))
        finally:
            # Including the path where the browser threw: the X server is a child
            # of this process and would otherwise sit there until it is killed.
            if display:
                display.stop()
