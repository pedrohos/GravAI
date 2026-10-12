"""The callback that makes a recording findable while it is still running.

A recording is a process that will not return for the length of a meeting, so
the moment the recorder has a session directory is the only moment anything
outside can learn where it is writing. Everything that reaches an in-flight
recording hangs off that: the log route, the catalogue entry that shows a
meeting in progress, and the stop request, which is a file in exactly that
directory. If the callback stops firing, all three quietly stop working while
recordings themselves keep succeeding.

A stand-in recorder is used rather than a browser: what is under test is the
lifecycle around record_meeting, not the joining.
"""

from multiprocessing import Queue
from pathlib import Path

import pytest

from gravai.recording.providers.provider_base import MeetingRecorder


class _RecorderThatJoinsNothing(MeetingRecorder):
    """Reports a finished recording the moment it is started."""

    vad_observer_js_path: Path | None = None

    def prepare_vad_observer(self, meeting_url: str, session_id: str):
        return "", [], {"session_id": session_id, "meeting_url": meeting_url}

    def record_meeting(self, meeting_url, q: Queue, output_dir, debug,
                       vad_observer_js, vad_events, vad_meta, audio_sink):
        q.put(("stop", None))


class _RecorderThatDies(_RecorderThatJoinsNothing):
    def record_meeting(self, meeting_url, q, output_dir, debug,
                       vad_observer_js, vad_events, vad_meta, audio_sink):
        q.put(("exception", "the meeting refused the bot"))


def test_the_directory_is_announced_before_the_recording_runs(tmp_path):
    seen = []

    directory = _RecorderThatJoinsNothing().record_meeting_with_audio_capture(
        "https://meet.google.com/abc-defg-hij",
        output_dir=str(tmp_path),
        on_session_start=lambda session_id, session_dir: seen.append((session_id, session_dir)),
    )

    assert len(seen) == 1
    session_id, session_dir = seen[0]
    assert session_dir == directory
    assert session_dir.endswith(f"{session_id}_tracks")
    assert Path(session_dir, "session.log").exists()


def test_a_recording_that_fails_still_announced_where_it_was_writing(tmp_path):
    """A failed join leaves a log, and the log is the only account of why."""
    seen = []

    with pytest.raises(RuntimeError, match="refused the bot"):
        _RecorderThatDies().record_meeting_with_audio_capture(
            "https://meet.google.com/abc-defg-hij",
            output_dir=str(tmp_path),
            on_session_start=lambda session_id, session_dir: seen.append(session_dir),
        )

    assert len(seen) == 1
    assert Path(seen[0], "session.log").exists()


def test_a_callback_that_raises_does_not_take_the_recording_with_it(tmp_path):
    """The catalogue being unwritable is not a reason to lose a meeting."""

    def explode(session_id, session_dir):
        raise RuntimeError("the database is on fire")

    directory = _RecorderThatJoinsNothing().record_meeting_with_audio_capture(
        "https://meet.google.com/abc-defg-hij",
        output_dir=str(tmp_path),
        on_session_start=explode,
    )

    assert Path(directory).is_dir()


def test_recording_without_a_callback_is_unchanged(tmp_path):
    directory = _RecorderThatJoinsNothing().record_meeting_with_audio_capture(
        "https://meet.google.com/abc-defg-hij", output_dir=str(tmp_path)
    )

    assert Path(directory).is_dir()
