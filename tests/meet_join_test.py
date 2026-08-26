"""Guards the green room's reading of the screens it can end up on.

Meet answers ResolveMeetingSpace with a 403 for a call that does not admit
anonymous guests and sends the tab to accounts.google.com. Nothing there is a
Meet screen, so the copy the refusal table matches on is absent: left to the
text alone the join loop polls a sign-in form for a join button until 90s are
gone, then reports the timeout rather than the refusal. The redirect is in the
URL from the moment it lands, which is also what the sign-in flow keys on.
"""

from gravai.recording.common.google_sign_in import (
    GoogleCredentials,
    back_on_meet,
    on_sign_in_page,
)
from gravai.recording.providers.meet.provider import (
    _names_the_bot_could_be,
    _refusal_reason,
    _wait_for_roster,
    _warn_unless_named_as_a_bot,
)

SIGN_IN_URL = (
    "https://accounts.google.com/v3/signin/identifier"
    "?continue=https://meet.google.com/wmn-ovpr-nzk&ltmpl=meet"
)


class FakePage:
    """The page surface these readers touch: a URL and the body text."""

    def __init__(self, url: str, body: str = "", url_raises: bool = False):
        self._url = url
        self._body = body
        self._url_raises = url_raises

    @property
    def url(self) -> str:
        if self._url_raises:
            raise RuntimeError("Execution context was destroyed, most likely by a navigation")
        return self._url

    def inner_text(self, selector: str) -> str:
        assert selector == "body"
        return self._body


def test_the_sign_in_redirect_is_read_from_the_host():
    # Verbatim from the real redirect, whose query string carries the meeting
    # URL back - a substring match on 'meet.google.com' would call this Meet.
    assert on_sign_in_page(FakePage(SIGN_IN_URL)) is True
    assert back_on_meet(FakePage(SIGN_IN_URL)) is False


def test_the_green_room_is_not_mistaken_for_the_sign_in_page():
    # Verbatim from a green room that went on to join: 'Sign in' is in the copy,
    # as the header link offering to stop being a guest. Reading these screens
    # by their words rather than their host would refuse every join here.
    page = FakePage(
        "https://meet.google.com/qpk-nbug-dwu",
        body=(
            "Sign in\nGetting ready...\nYou'll be able to join in just a moment\n"
            "By joining, you agree to the Terms of Service and Privacy Policy. "
            "System info will be sent to confirm you're not a bot."
        ),
    )

    assert on_sign_in_page(page) is False
    assert back_on_meet(page) is True
    assert _refusal_reason(page) is None


def test_text_refusals_still_match_on_meet_itself():
    page = FakePage(
        "https://meet.google.com/qpk-nbug-dwu",
        body="You can't join this video call\nReturn to home screen",
    )

    assert _refusal_reason(page) is not None


def test_a_page_mid_navigation_does_not_break_the_loop():
    # Reading either the URL or the body can fail while the page is moving. The
    # poll has to survive it and ask again, not take the whole recording down.
    mid_navigation = FakePage("", url_raises=True)

    assert on_sign_in_page(mid_navigation) is False
    assert back_on_meet(mid_navigation) is False
    assert _refusal_reason(mid_navigation) is None


def test_credentials_never_carry_the_password_into_a_log():
    # These are held for the length of a join and passed between functions, so
    # anything logging its arguments logs this object.
    credentials = GoogleCredentials(email="bot@example.com", password="hunter2")

    assert "hunter2" not in repr(credentials)
    assert "bot@example.com" in repr(credentials)


def test_a_call_with_a_bot_in_the_roster_is_not_warned_about():
    warnings = []
    _warn_unless_named_as_a_bot(
        [
            {"id": "1", "participantName": "Pedro Silva"},
            {"id": "2", "participantName": "Bot de Gravação de Pedro Silva"},
        ],
        _FakeLogger(warnings),
    )

    assert warnings == []


def test_a_call_where_nothing_is_named_like_a_bot_is_warned_about():
    # The live case: the recording account is named 'Pedro Silva', the same as
    # the host, so the participant list gives nobody any way to tell that one of
    # the two is recording.
    warnings = []
    _warn_unless_named_as_a_bot(
        [{"id": "1", "participantName": "Pedro Silva"}, {"id": "2", "participantName": "Pedro Silva"}],
        _FakeLogger(warnings),
    )

    assert len(warnings) == 1
    assert "does not start with 'Bot'" in warnings[0]


def test_the_prefix_is_matched_however_it_is_cased_and_spaced():
    for name in ("Bot Gravador", "bot gravador", "  BOT gravador"):
        warnings = []
        _warn_unless_named_as_a_bot([{"id": "1", "participantName": name}], _FakeLogger(warnings))
        assert warnings == [], name


def test_an_unread_roster_is_not_warned_about():
    # No evidence is not evidence of a badly named bot, and a warning that fires
    # on an empty read is one people learn to skip past.
    for roster in ([], None, [{"id": "1"}]):
        warnings = []
        _warn_unless_named_as_a_bot(roster, _FakeLogger(warnings))
        assert warnings == [], roster


class _FakeLogger:
    def __init__(self, warnings: list[str]):
        self._warnings = warnings

    def warning(self, message: str) -> None:
        self._warnings.append(message)

    def info(self, message: str) -> None:
        pass


class _RosterPage:
    """A page whose roster fills in only after a few polls."""

    def __init__(self, rosters: list[list]):
        self._rosters = rosters
        self.polls = 0

    def evaluate(self, script: str):
        roster = self._rosters[min(self.polls, len(self._rosters) - 1)]
        return roster

    def wait_for_timeout(self, ms: float) -> None:
        self.polls += 1


def test_the_roster_is_waited_for_rather_than_read_the_instant_we_are_in():
    # An invited account is admitted the moment it asks, so the first read lands
    # before any tile has rendered. Taking that empty read as the answer skips
    # the check silently, which is how it went unnoticed on the invited path.
    page = _RosterPage([[], [], [{"id": "1", "participantName": "Pedro Silva"}]])

    roster = _wait_for_roster(page, _FakeLogger([]), timeout_s=30)

    assert _names_the_bot_could_be(roster) == ["Pedro Silva"]
    assert page.polls == 2


def test_a_roster_that_never_fills_is_given_up_on():
    page = _RosterPage([[]])
    warnings = []

    roster = _wait_for_roster(page, _FakeLogger(warnings), timeout_s=0)

    assert roster == []
