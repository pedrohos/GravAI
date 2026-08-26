"""Guards the injected intercept script against a crash that is expensive to rediscover.

The script is added with add_init_script, so it runs at document start in every
frame of the page - including the opaque-origin frames Google Meet creates while
loading. Registering a blob URL in one of those kills the renderer: the page
crashes during navigation, before a meeting can be joined at all, and the failure
surfaces as an unhelpful "Page.goto: Page crashed".

Anything that has to build a blob URL therefore has to wait until it is actually
needed, by which point the intercept is running in a frame that owns a real origin.
"""

from pathlib import Path

from gravai.config.settings import get_settings


def _intercept_source() -> str:
    return Path(get_settings().RTC_INTERCEPT_JS_PATH).read_text(encoding="utf-8")


def test_blob_url_is_not_created_at_document_start():
    source = _intercept_source()
    prologue, _, _ = source.partition("function ")

    assert "createObjectURL" not in prologue, (
        "The intercept script builds a blob URL before its first function, so it runs "
        "at document start in every frame. In Google Meet that crashes the renderer "
        "during navigation. Move the call inside a function that runs when a track "
        "arrives."
    )


def test_blob_url_is_still_built_for_the_worklet():
    source = _intercept_source()

    # The lazy path has to remain wired up, or tracks attach without a worklet
    # and no audio is ever captured.
    assert "createObjectURL" in source
    assert "addModule(getWorkletUrl())" in source


def test_every_placeholder_is_substituted_by_both_providers():
    """A placeholder that reaches the browser is a syntax error at document start.

    The script carries three of them - the socket url, the worklet source and
    whether to publish a mixed track - and each provider substitutes them itself,
    so adding one is exactly the kind of change that gets done in one provider and
    forgotten in the other.
    """
    from gravai.recording.providers.meet.provider import MeetMeetingRecorder
    from gravai.recording.providers.teams.provider import TeamsMeetingRecorder

    for recorder in (MeetMeetingRecorder(), TeamsMeetingRecorder()):
        intercept_js, _, _, _ = recorder.prepare_injection(
            "https://example.invalid/meeting", "ws://127.0.0.1:1/", "session"
        )
        assert "{{" not in intercept_js, f"{type(recorder).__name__} left a placeholder in the intercept"


def test_meet_mixes_a_main_track_and_teams_does_not():
    """Slicing cuts `track_mainAudio*.wav`, and only Teams names one itself.

    Meet reuses its audio receivers between speakers, so its per-receiver tracks
    are not participants and the mix is the only thing slicing can work on.
    """
    from gravai.recording.providers.meet.provider import MeetMeetingRecorder
    from gravai.recording.providers.teams.provider import TeamsMeetingRecorder

    meet_js, _, _, _ = MeetMeetingRecorder().prepare_injection("https://x.invalid", "ws://127.0.0.1:1/", "s")
    teams_js, _, _, _ = TeamsMeetingRecorder().prepare_injection("https://x.invalid", "ws://127.0.0.1:1/", "s")

    assert '"true" === "true"' in meet_js
    assert '"false" === "true"' in teams_js
    # The name slicing globs for has to be the one the mix publishes.
    assert '"mainAudio-"' in meet_js
