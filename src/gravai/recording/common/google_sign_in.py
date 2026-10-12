"""Signs the recorder into a Google account, the way a person signs in.

Meet refuses `ResolveMeetingSpace` with a 403 for a call that does not admit
anonymous guests and sends the tab to accounts.google.com. This walks that page:
email, Next, password, Next, and returns as soon as the session exists - not as
soon as the tab is back on Meet, which is a place Google does not reliably send
it. Getting to the meeting afterwards is the caller's job and is one `goto`.

Every field is filled through common/human_input.py rather than `fill()`. The
Meet green room already proved that a click with no pointer path behind it is
refused at `CreateMeetingDevice`, and the account pages are the more heavily
defended half of the same property - so a password that appears in one
assignment, with no keydown behind it, is the last thing this flow should do.

Three things this cannot get past, all by design:

    2-Step Verification    a code from a phone or an authenticator is not
                           something a headless container has. The account this
                           runs as has to be one that signs in with a password
                           alone, or the flow stops at the challenge and says so.
    'Couldn't sign you in' Google's own judgement that the browser is automated.
                           The persona in common/browser_persona.py is what
                           argues otherwise; there is nothing to retry here.
    a CAPTCHA             an image for a person to read, which is the one thing
                           an unattended container is not. Unattended it ends the
                           attempt as its own kind of refusal, never retried. It
                           is also the only one of the three a person could clear
                           if they were here - so the caller may pass a
                           `captcha_solver`, which is handed the page and blocks
                           while somebody answers it over VNC. See
                           common/vnc.py; nothing in this module reads the image
                           or types into the field.

Used from the join flow only when the redirect actually happens - a meeting that
admits guests never reaches this module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from gravai.config.settings import get_settings
from gravai.recording.common.human_input import (
    click_if_present,
    move_and_click,
    pause,
    type_like_a_person,
)

# The screens before a session exists follow the browser's locale, which the
# persona pins to en-US. After it exists they follow the *account's* language -
# a Brazilian account is handed pt-BR pages regardless of the browser - so the
# copy below is matched in both, and nothing structural is read from it at all.
_MEET_HOST = "meet.google.com"
_ACCOUNTS_HOST = "accounts.google.com"

# Every sign-in screen lives under this path, challenges included. Leaving it is
# what 'the password was accepted' looks like: Google may hand the tab straight
# back to Meet, or divert it through an interstitial on a host of its own
# (gds.google.com/web/recoveryoptions, asking for a recovery phone).
_SIGN_IN_PATH = "/signin"

_POLL_INTERVAL_MS = 500

# Each account page is one form load, not a green room being assembled.
_FIELD_TIMEOUT_S = 45.0

# From the password being submitted to Google deciding about it, either way.
_ACCEPTED_TIMEOUT_S = 90.0

# The fields, in the order each screen is tried. Read off the live page rather
# than assumed, because both are surprising:
#
#   the email field is type="text", not type="email" - the obvious selector
#   matches nothing at all
#
#   the identifier page carries a *hidden* input[type=password] called
#   hiddenPassword, so a locator on that selector resolves to a decoy that never
#   becomes visible, and the real field that renders on the next screen is never
#   reached. Hence ':visible', which is the whole point of the selector.
_EMAIL_SELECTORS = ("#identifierId", 'input[name="identifier"]', 'input[type="email"]:visible')
_PASSWORD_SELECTORS = ('input[type="password"]:visible', 'input[name="Passwd"]:visible')

# The distorted-text challenge, 'Type the text you hear or see'. The field ships
# on the identifier page from the start, hidden, and is revealed when Google
# decides to challenge - so presence proves nothing and only ':visible' does.
#
# Keyed on the id and the name rather than the label: those are the same in every
# language, and this screen in particular is one a Brazilian account is served in
# pt-BR.
_CAPTCHA_SELECTORS = ("input#ca:visible", 'input[name="ca"]:visible')

# Screens that end the attempt, and what each one means. Matched against the
# whole visible page, for the same reason the Meet refusals are: the copy is
# split across nodes and rewritten as it animates in.
_BLOCKING_SCREENS: tuple[tuple[str, str], ...] = (
    (
        # What the live page says, apostrophe and all - _page_text folds the
        # curly one. 'Couldn't find your Google Account' is the older wording,
        # kept because the two rotate.
        "couldn't find this account",
        "Google does not recognise GOOGLE_ACCOUNT_EMAIL.",
    ),
    (
        "couldn't find your google account",
        "Google does not recognise GOOGLE_ACCOUNT_EMAIL.",
    ),
    (
        "enter a valid email",
        "GOOGLE_ACCOUNT_EMAIL is not a well-formed address.",
    ),
    (
        "não foi possível encontrar esta conta",
        "Google does not recognise GOOGLE_ACCOUNT_EMAIL.",
    ),
    (
        "wrong password",
        "GOOGLE_ACCOUNT_PASSWORD was rejected. If the account has an app password, "
        "note that Meet is not a service that issues one - this needs the account's "
        "own password.",
    ),
    (
        "senha incorreta",
        "GOOGLE_ACCOUNT_PASSWORD was rejected. If the account has an app password, "
        "note that Meet is not a service that issues one - this needs the account's "
        "own password.",
    ),
    (
        "2-step verification",
        "The account is behind 2-Step Verification, which needs a code this container "
        "has no way to produce. Use an account that signs in with a password alone.",
    ),
    (
        "verificação em duas etapas",
        "The account is behind 2-Step Verification, which needs a code this container "
        "has no way to produce. Use an account that signs in with a password alone.",
    ),
    (
        "verify it's you",
        "Google wants to verify the account by another channel, which this container "
        "has no way to answer. It usually means the account is new to this address, or "
        "has 2-Step Verification on.",
    ),
    (
        "confirme que é você",
        "Google wants to verify the account by another channel, which this container "
        "has no way to answer. It usually means the account is new to this address, or "
        "has 2-Step Verification on.",
    ),
    (
        "this browser or app may not be secure",
        "Google judged the browser automated and refused the sign-in outright. The "
        "persona in common/browser_persona.py is what argues otherwise; this is that "
        "argument failing.",
    ),
    (
        "este navegador ou app pode não ser seguro",
        "Google judged the browser automated and refused the sign-in outright. The "
        "persona in common/browser_persona.py is what argues otherwise; this is that "
        "argument failing.",
    ),
    (
        "couldn't sign you in",
        "Google refused the sign-in without saying why, which is usually its automation "
        "judgement.",
    ),
    (
        "não foi possível fazer login",
        "Google refused the sign-in without saying why, which is usually its automation "
        "judgement.",
    ),
    (
        "too many failed attempts",
        "Google has rate-limited sign-ins for this account. Signing in by hand once, "
        "from a browser, is what clears it.",
    ),
)


# Handed the page the moment the challenge is recognised, and expected to block
# while a person answers it: True once it is gone from the page, False when
# nobody came. common/vnc.py's CaptchaHandoff.solve is the implementation; this
# module only ever calls it, so that the part that needs eyes is somewhere else
# entirely.
CaptchaSolver = Callable[[object], bool]


class GoogleSignInError(RuntimeError):
    """Raised when the account could not be signed in."""


class GoogleCaptchaError(GoogleSignInError):
    """Raised when Google puts a CAPTCHA in front of the sign-in.

    Its own type because it is the one refusal on this page that a person can
    clear. Everything else in _BLOCKING_SCREENS needs a different account or a
    different machine; this needs somebody to read the image and type what it
    says, which is exactly what the challenge is asking for and is not something
    this flow should attempt on its own.
    """


@dataclass(frozen=True)
class GoogleCredentials:
    """The account the recorder joins meetings as."""

    email: str
    password: str

    def __repr__(self) -> str:
        # A credentials object reaches a log the moment anything logs the join
        # flow's arguments, and this is the one field that must not be there.
        return f"GoogleCredentials(email={self.email!r}, password=<redacted>)"


def configured_credentials() -> GoogleCredentials | None:
    """The configured account, or None when the recorder has no account to use.

    None is the ordinary case, not an error: joining as an anonymous guest is
    still the default, and the account is only wanted for meetings that refuse
    one.
    """
    settings = get_settings()
    email = settings.GOOGLE_ACCOUNT_EMAIL.strip()
    password = settings.GOOGLE_ACCOUNT_PASSWORD.get_secret_value()
    if not email or not password:
        return None
    return GoogleCredentials(email=email, password=password)


def on_sign_in_page(page) -> bool:
    """Whether the tab has left Meet for the account chooser.

    Read from the URL rather than the page: the sign-in copy is served in the
    browser's language, the hostname is not - and 'Sign in' is on Meet's green
    room too, as the header link offering to stop being a guest, so the words
    are not evidence of anything.
    """
    return _host(page) == _ACCOUNTS_HOST


def back_on_meet(page) -> bool:
    return _host(page) == _MEET_HOST


def _host(page) -> str:
    try:
        return urlparse(page.url).hostname or ""
    except Exception:
        # Mid-navigation, or the page is gone. Either answers next round.
        return ""


def _page_text(page) -> str:
    try:
        return page.inner_text("body").replace("’", "'").replace("ʼ", "'").casefold()
    except Exception:
        return ""


def captcha_visible(page) -> bool:
    """Whether Google is showing the 'type the text you see' challenge."""
    for selector in _CAPTCHA_SELECTORS:
        try:
            if page.locator(selector).first.is_visible():
                return True
        except Exception:
            continue
    return False


def _blocking_reason(page) -> str | None:
    """What the page says, when what it says ends the attempt."""
    body = _page_text(page)
    if not body:
        return None
    for text, reason in _BLOCKING_SCREENS:
        if text in body:
            return reason
    return None


def _raise_if_stopped(page, captcha_solver: CaptchaSolver | None = None) -> float:
    """Ends the attempt on any screen it cannot get past on its own.

    Returns the seconds it spent waiting on a person, which is zero on every
    screen but an answered CAPTCHA. Callers add it to their own deadline: a
    handoff takes minutes by design, and a field timeout that ran through one
    would fire the moment the person finished, reporting a page it never gave
    itself the chance to look at.

    The CAPTCHA goes first and separately: it is a DOM check rather than a copy
    match, and it is the one refusal here a person can clear. With a solver, it
    is handed to them; without one - the unattended case, and the default - it
    ends the attempt exactly as the rest do.
    """
    if captcha_visible(page):
        if captcha_solver is None:
            raise GoogleCaptchaError(
                "Google is showing a CAPTCHA on the sign-in page. It is asking for a person "
                "to read an image and type what it says, which is what the challenge is for "
                "- so it needs somebody to answer it, not another attempt."
            )
        started = time.time()
        answered = captcha_solver(page)
        waited = time.time() - started
        if not answered:
            raise GoogleCaptchaError(
                "Google put a CAPTCHA on the sign-in page and nobody answered it. The "
                "browser's screen was offered over VNC for somebody to type what the image "
                f"says; after {waited:.0f}s it was still on screen, so the attempt is over. "
                "Where that offer was made is in this session's log."
            )
        return waited

    reason = _blocking_reason(page)
    if reason:
        raise GoogleSignInError(reason)
    return 0.0


def _wait_for_field(
    page,
    selectors: tuple[str, ...],
    what: str,
    timeout_s: float = _FIELD_TIMEOUT_S,
    captcha_solver: CaptchaSolver | None = None,
):
    """Waits for the first of `selectors` to be visible on the page.

    Polled rather than `wait_for_selector`: a wrong password and a page that is
    simply slow look identical to a selector wait, and the difference is the
    whole value of the error this raises.

    Visibility is the test, not presence - see the note on hiddenPassword above.

    Returns None rather than raising when the field never came *and* the tab has
    left the sign-in flow: there is no field to wait for on a sign-in that is
    already done. See the comment at the timeout.
    """
    deadline = time.time() + timeout_s
    while True:
        # A CAPTCHA answered by hand takes minutes out of this wait, and none of
        # them are the page being slow, so none of them count against it.
        deadline += _raise_if_stopped(page, captcha_solver)

        for selector in selectors:
            try:
                field = page.locator(selector).first
                if field.is_visible():
                    return field
            except Exception:
                continue

        if time.time() >= deadline:
            if not _on_sign_in_screen(page):
                # The field never came because there is nothing left to fill in:
                # the tab is off the sign-in flow entirely, which only happens
                # once Google has set a session. Somebody who was already at the
                # keyboard for a CAPTCHA and carried on through the rest of the
                # screens leaves exactly this state behind, and it is a finished
                # sign-in, not a page this flow failed to read.
                return None
            raise GoogleSignInError(
                f"Google never showed {what} within {timeout_s:.0f}s. The sign-in page "
                f"is not the one this flow knows how to walk - the selectors tried were "
                f"{', '.join(selectors)}."
            )
        page.wait_for_timeout(_POLL_INTERVAL_MS)


def _type_into(page, field, text: str) -> None:
    """Clicks into a field and types it out, keystroke by keystroke."""
    move_and_click(page, field)
    # The beat between clicking a field and starting to type it.
    pause(page, 0.2, 0.7)
    type_like_a_person(page, text)


def _submit(page, logger) -> None:
    """Presses whichever 'Next' this step renders.

    Both steps render a plain <button>Next</button> with no id - the
    #identifierNext / #passwordNext wrappers that this page used to be driven by
    are gone - so the accessible name is what there is to aim at.
    """
    # The gap between finishing a field and deciding to move on.
    pause(page, 0.5, 1.6)
    if click_if_present(page, r"^next$", timeout=5000):
        return
    # Submitting from the field itself, which is what a person who does not find
    # the button does, and what the form is wired to accept.
    logger.info("Sign-in stage: no 'Next' button found, submitting with Enter")
    page.keyboard.press("Enter")


def _on_sign_in_screen(page) -> bool:
    """Whether the tab is still on a screen that is part of signing in."""
    try:
        parsed = urlparse(page.url)
    except Exception:
        return True
    return (parsed.hostname or "") == _ACCOUNTS_HOST and _SIGN_IN_PATH in (parsed.path or "")


def _wait_until_signed_in(
    page,
    logger,
    timeout_s: float = _ACCEPTED_TIMEOUT_S,
    captcha_solver: CaptchaSolver | None = None,
) -> None:
    """Blocks until the password has been accepted and the tab has moved on.

    Deliberately not 'until we are back on Meet'. Google puts a variable number
    of interstitials in the way - a recovery phone, a 'stay signed in?' - each on
    a host and in a language of its own choosing: the recovery nag arrives on
    gds.google.com in the *account's* language, which is not the browser's. There
    is no set of buttons to learn here. The caller navigates to the meeting once
    the session exists, and every one of those screens is simply left behind.

    A failed sign-in never reaches this: challenges and errors stay under
    accounts.google.com/signin, which is what this waits to leave.
    """
    deadline = time.time() + timeout_s
    while True:
        deadline += _raise_if_stopped(page, captcha_solver)

        if not _on_sign_in_screen(page):
            if not back_on_meet(page):
                logger.info(f"Sign-in stage: accepted, Google diverted to {_host(page)}")
            return

        if time.time() >= deadline:
            raise GoogleSignInError(
                f"Google neither accepted nor refused the password within {timeout_s:.0f}s. "
                f"The tab is on {page.url[:120]!r}."
            )
        page.wait_for_timeout(_POLL_INTERVAL_MS)


def sign_in(
    page,
    credentials: GoogleCredentials,
    logger,
    screenshot: Callable[[str], str | None] | None = None,
    captcha_solver: CaptchaSolver | None = None,
) -> None:
    """Signs in and returns once the session exists.

    Where the tab ends up is the caller's problem: Google may hand it back to
    Meet or park it on an interstitial, so the caller navigates to the meeting
    itself rather than this module trying to steer through them.

    `screenshot` is the caller's own screenshotting, so the evidence lands in
    the session directory with the rest of the run's - this module does not know
    where that is. It is called with a name and may return the path it wrote.

    `captcha_solver`, when there is one, is what a CAPTCHA is handed to instead
    of ending the attempt. Without it the challenge is fatal, which is the
    unattended default and what every caller did before there was one.

    Raises GoogleSignInError, which carries what the account page said, on
    anything that ends the attempt.
    """
    logger.info(f"Sign-in stage: Google is asking for an account, using {credentials.email}")

    try:
        email_field = _wait_for_field(
            page, _EMAIL_SELECTORS, "the email field", captcha_solver=captcha_solver
        )
        if email_field is None:
            logger.info("Sign-in stage: the whole sign-in was answered by hand")
            return
        _type_into(page, email_field, credentials.email)
        _submit(page, logger)
        logger.info("Sign-in stage: email submitted")

        password_field = _wait_for_field(
            page, _PASSWORD_SELECTORS, "the password field", captcha_solver=captcha_solver
        )
        if password_field is None:
            logger.info("Sign-in stage: the password screen was answered by hand")
            return
        # Not logged, at any level, in any form.
        _type_into(page, password_field, credentials.password)
        _submit(page, logger)
        logger.info("Sign-in stage: password submitted")

        _wait_until_signed_in(page, logger, captcha_solver=captcha_solver)
    except GoogleSignInError:
        if screenshot:
            shot = screenshot("google_sign_in_failed")
            logger.error(f"Sign-in failed, screenshot: {shot}")
        raise

    logger.info("Sign-in stage: signed in")
