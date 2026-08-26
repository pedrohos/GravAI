"""Guards the two decisions the sign-in makes before it touches a keyboard.

Whether there is an account to use at all - joining as a guest has to stay the
default, so an unset password cannot become a sign-in attempt against an empty
one - and whether the page in front of it is one it can get past. The second is
the whole value of the errors this raises: a wrong password, a 2FA challenge and
a slow page all look the same to a selector wait, and only the copy tells them
apart.
"""

import time

import pytest
from pydantic import SecretStr

from gravai.recording.common import google_sign_in
from gravai.recording.common.google_sign_in import (
    _CAPTCHA_SELECTORS,
    _EMAIL_SELECTORS,
    _PASSWORD_SELECTORS,
    GoogleCaptchaError,
    GoogleSignInError,
    captcha_visible,
    _blocking_reason,
    _on_sign_in_screen,
    _wait_for_field,
    configured_credentials,
)


class FakeSettings:
    def __init__(self, email: str, password: str):
        self.GOOGLE_ACCOUNT_EMAIL = email
        self.GOOGLE_ACCOUNT_PASSWORD = SecretStr(password)


@pytest.fixture
def settings(monkeypatch):
    def configure(email: str, password: str):
        monkeypatch.setattr(
            google_sign_in, "get_settings", lambda: FakeSettings(email, password)
        )

    return configure


class FakePage:
    """A page whose body text is a script of what each poll sees."""

    def __init__(self, bodies: list[str], url: str = "https://accounts.google.com/v3/signin"):
        self._bodies = bodies
        self.url = url
        self.waits = 0

    def inner_text(self, selector: str) -> str:
        assert selector == "body"
        # The last frame repeats, so a poll that outlives the script keeps
        # seeing the screen it ended on rather than falling off it.
        return self._bodies[min(self.waits, len(self._bodies) - 1)]

    def wait_for_timeout(self, ms: float) -> None:
        self.waits += 1

    def locator(self, selector: str):
        return FakeLocator(visible=selector in self.visible_selectors)

    visible_selectors: tuple[str, ...] = ()


class FakeLocator:
    def __init__(self, visible: bool):
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self) -> bool:
        return self._visible


@pytest.mark.parametrize(
    "email, password",
    [
        ("", ""),
        ("bot@example.com", ""),
        ("", "hunter2"),
        ("   ", "hunter2"),
    ],
)
def test_a_half_configured_account_is_no_account(settings, email, password):
    # Anything short of both is the default: join as a guest. Treating a blank
    # password as a password spends a real sign-in attempt on an empty string,
    # which is how an account gets rate-limited by a config mistake.
    settings(email, password)

    assert configured_credentials() is None


def test_a_configured_account_is_used(settings):
    settings("bot@example.com", "hunter2")

    credentials = configured_credentials()
    assert credentials is not None
    assert credentials.email == "bot@example.com"
    assert credentials.password == "hunter2"


def test_the_email_is_stripped_of_the_whitespace_an_env_file_leaves(settings):
    settings(" bot@example.com ", "hunter2")

    credentials = configured_credentials()
    assert credentials is not None
    assert credentials.email == "bot@example.com"


@pytest.mark.parametrize(
    "screen",
    [
        "Wrong password. Try again or click Forgot password to reset it.",
        "2-Step Verification\nThis extra step shows it's really you trying to sign in",
        "Verify it's you\nTo help keep your account safe, Google wants to make sure",
        # Verbatim from the live page, curly apostrophe included: the folding in
        # _page_text is what makes this match the plain one in the table.
        "Sign in\nUse your Google Account\nEmail or phone\nCouldn’t find this account",
        "Couldn't find your Google Account",
        "This browser or app may not be secure. Try using a different browser.",
        "Couldn't sign you in",
    ],
)
def test_every_screen_that_ends_the_attempt_is_recognised(screen):
    assert _blocking_reason(FakePage([screen])) is not None


def test_the_password_screen_itself_is_not_a_blocking_screen():
    # It contains the word 'password' throughout, which is the obvious thing to
    # match on and would abort every sign-in before it typed anything.
    password_page = (
        "Welcome\nbot@example.com\nEnter your password\nShow password\n"
        "Forgot password?\nNext"
    )

    assert _blocking_reason(FakePage([password_page])) is None


def test_a_field_that_never_comes_is_reported_as_the_page_being_unfamiliar():
    page = FakePage(["Sign in\nUse your Google Account"])

    with pytest.raises(GoogleSignInError, match="never showed the email field"):
        _wait_for_field(page, _EMAIL_SELECTORS, "the email field", timeout_s=0.1)


def test_a_blocking_screen_ends_the_wait_rather_than_running_it_out():
    # The distinction the whole module exists for: 45s of waiting followed by
    # 'the page is unfamiliar' says nothing, and the answer was on screen the
    # entire time.
    page = FakePage(["Wrong password. Try again."])

    with pytest.raises(GoogleSignInError, match="GOOGLE_ACCOUNT_PASSWORD was rejected"):
        _wait_for_field(page, _PASSWORD_SELECTORS, "the password field", timeout_s=30)

    # It did not sit out the timeout to get there.
    assert page.waits == 0


def test_the_password_field_is_only_ever_matched_when_visible():
    # The identifier page carries a hidden input[type=password] named
    # hiddenPassword. Matching on presence latches onto that decoy: it never
    # becomes visible, so the real field on the next screen is never reached and
    # the sign-in dies at a 45s timeout with the page sitting right there.
    assert all(":visible" in selector for selector in _PASSWORD_SELECTORS)


@pytest.mark.parametrize(
    "url, still_signing_in",
    [
        ("https://accounts.google.com/v3/signin/identifier?continue=x", True),
        ("https://accounts.google.com/v3/signin/challenge/pwd", True),
        # Where the live run ended up: the recovery-phone nag, on a host of its
        # own and in the account's language rather than the browser's. Waiting
        # for Meet here spends the whole timeout on a page that was never going
        # to become Meet by itself.
        ("https://gds.google.com/web/recoveryoptions?hl=pt-BR&continue=x", False),
        ("https://meet.google.com/ugo-xaqf-qmj", False),
    ],
)
def test_leaving_the_sign_in_path_is_what_counts_as_signed_in(url, still_signing_in):
    assert _on_sign_in_screen(FakePage([""], url=url)) is still_signing_in


def test_an_unreadable_url_is_treated_as_still_signing_in():
    # The safe way round: waiting one more poll costs half a second, while
    # calling it signed in navigates to the meeting with no session.
    class Unreadable:
        @property
        def url(self):
            raise RuntimeError("Execution context was destroyed")

    assert _on_sign_in_screen(Unreadable()) is True


def test_the_email_field_is_found_by_the_id_the_live_page_uses():
    # It is type="text", not type="email", so the obvious selector matches
    # nothing. #identifierId is what the page has.
    page = FakePage(["Sign in\nUse your Google Account"])
    page.visible_selectors = ("#identifierId",)

    assert _wait_for_field(page, _EMAIL_SELECTORS, "the email field", timeout_s=1).is_visible()


class _CaptchaPage(FakePage):
    """A page where a chosen set of selectors is visible."""

    def __init__(self, bodies: list[str], visible: tuple[str, ...] = ()):
        super().__init__(bodies)
        self.visible_selectors = visible


def test_the_captcha_is_detected_by_the_field_google_reveals():
    for selector in _CAPTCHA_SELECTORS:
        assert captcha_visible(_CaptchaPage([""], visible=(selector,))) is True


def test_the_hidden_captcha_field_is_not_a_captcha():
    # It ships on every identifier page, hidden, so presence proves nothing.
    # Treating it as a challenge would abort every sign-in before it typed.
    assert captcha_visible(_CaptchaPage([""], visible=())) is False


def test_a_captcha_stops_the_attempt_as_its_own_kind_of_refusal():
    # Separate from the rest because a person can answer this one, and the
    # caller has to be able to tell it apart to offer them the chance.
    page = _CaptchaPage(
        ["Sign in\nType the text you hear or see"], visible=("input#ca:visible",)
    )

    with pytest.raises(GoogleCaptchaError):
        _wait_for_field(page, _EMAIL_SELECTORS, "the email field", timeout_s=30)

    assert page.waits == 0


def test_a_captcha_is_still_a_sign_in_error_for_anyone_catching_broadly():
    page = _CaptchaPage([""], visible=('input[name="ca"]:visible',))

    with pytest.raises(GoogleSignInError):
        _wait_for_field(page, _PASSWORD_SELECTORS, "the password field", timeout_s=0)


def test_an_ordinary_refusal_is_not_reported_as_a_captcha():
    page = _CaptchaPage(["Wrong password. Try again."], visible=())

    with pytest.raises(GoogleSignInError) as caught:
        _wait_for_field(page, _PASSWORD_SELECTORS, "the password field", timeout_s=0)

    assert not isinstance(caught.value, GoogleCaptchaError)


# --------------------------------------------------------------------------
# The CAPTCHA handed to a person
# --------------------------------------------------------------------------


class _AnsweredPage(_CaptchaPage):
    """A challenge that a person clears, and a field that arrives after it.

    The two are separate on purpose: answering the CAPTCHA submits a form, so
    the screen the flow was waiting for is not there the instant the challenge
    goes - it arrives a poll or two later, like any other page load.
    """

    def __init__(self, solver_takes_s: float = 0.0, reveals_after: int = 2):
        super().__init__([""], visible=("input#ca:visible",))
        self._solver_takes_s = solver_takes_s
        self._reveals_after = reveals_after
        self.polls_after_the_answer = 0
        self.handed_over = 0

    def solve(self, page) -> bool:
        self.handed_over += 1
        # A person reading an image and typing what it says.
        time.sleep(self._solver_takes_s)
        self.visible_selectors = ()
        return True

    def wait_for_timeout(self, ms: float) -> None:
        super().wait_for_timeout(ms)
        if not self.visible_selectors or self.visible_selectors == ():
            self.polls_after_the_answer += 1
            if self.polls_after_the_answer >= self._reveals_after:
                self.visible_selectors = ("#identifierId",)


def test_a_captcha_is_handed_to_whoever_can_answer_it_rather_than_ending_the_attempt():
    page = _AnsweredPage()

    field = _wait_for_field(
        page, _EMAIL_SELECTORS, "the email field", timeout_s=5, captcha_solver=page.solve
    )

    assert page.handed_over == 1
    assert field is not None and field.is_visible()


def test_the_time_a_person_takes_is_not_time_the_page_was_slow():
    # The wait for a field is 45s because a form load is not a person. Counting
    # a handoff against it fails the sign-in the moment they finish typing, on
    # a page it never gave itself the chance to look at.
    page = _AnsweredPage(solver_takes_s=0.4)

    field = _wait_for_field(
        page, _EMAIL_SELECTORS, "the email field", timeout_s=0.2, captcha_solver=page.solve
    )

    assert field is not None


def test_nobody_answering_is_still_the_end_of_the_attempt():
    page = _CaptchaPage([""], visible=("input#ca:visible",))

    with pytest.raises(GoogleCaptchaError, match="nobody answered"):
        _wait_for_field(
            page,
            _EMAIL_SELECTORS,
            "the email field",
            timeout_s=30,
            captcha_solver=lambda _page: False,
        )


def test_an_unattended_recording_is_unchanged_by_any_of_this():
    # No solver is the default and the whole of the previous behaviour: there is
    # nobody to ask, so the challenge ends the sign-in immediately.
    page = _CaptchaPage([""], visible=("input#ca:visible",))

    with pytest.raises(GoogleCaptchaError, match="needs somebody to answer it"):
        _wait_for_field(page, _EMAIL_SELECTORS, "the email field", timeout_s=30)


def test_a_person_who_carries_on_past_the_captcha_has_not_broken_the_sign_in():
    # They are already at the keyboard, and typing the password too is the
    # obvious thing to do. What is left behind is a session and a tab that is no
    # longer on any sign-in screen - which is a finished sign-in, not a page the
    # flow failed to read.
    page = FakePage([""], url="https://myaccount.google.com/")

    assert _wait_for_field(page, _PASSWORD_SELECTORS, "the password field", timeout_s=0.1) is None


def test_a_field_that_never_comes_on_a_page_still_signing_in_is_still_an_error():
    page = FakePage([""], url="https://accounts.google.com/v3/signin/challenge/pwd")

    with pytest.raises(GoogleSignInError, match="never showed the password field"):
        _wait_for_field(page, _PASSWORD_SELECTORS, "the password field", timeout_s=0.1)
