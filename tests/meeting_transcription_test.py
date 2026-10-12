"""The whole meeting, transcribed from the mix.

The per-participant transcripts say who said what, but each is one voice cut
into the stretches the VAD marked - so a sentence whose opening the observer
missed, or a moment two people talk over each other, reads worse there than it
does on the mix. This is the same meeting read as one conversation, and the
thing that can silently go wrong with it is that it stops being produced at all
and nobody notices, because the per-speaker transcripts still arrive.
"""

import json
from datetime import UTC, datetime

import pytest

from gravai.models.common import ParticipantData, Session, Track
from gravai.transcribe import base


def _session(tmp_path, main_track: str | None, with_participant: bool = True) -> Session:
    tracks = {}
    if with_participant:
        participant_track = tmp_path / "track_Ana.wav"
        participant_track.write_bytes(b"")
        tracks["Ana"] = ParticipantData(
            participant_id="Ana",
            participant_name="Ana",
            track=Track(wav_file_path=str(participant_track)),
        )
    return Session(
        session_id="session-1",
        session_start=datetime.now(UTC),
        session_end=datetime.now(UTC),
        tracks=tracks,
        main_track_path=main_track,
    )


@pytest.fixture
def whisper(monkeypatch):
    """Stands in for the whisper server, recording what it was asked to read."""
    asked = []

    def fake_transcribe(audio_file, host, port, timeout=None, language=None):
        asked.append(audio_file)
        return {"text": "bom dia a todos", "segments": [{"start": 1.0, "end": 2.5, "text": " bom dia"}]}

    monkeypatch.setattr(base, "transcribe", fake_transcribe)
    monkeypatch.setattr(base, "is_silent", lambda path: False)
    return asked


def test_the_mix_is_transcribed_alongside_the_participants(tmp_path, whisper):
    main = tmp_path / "track_mainAudio-1.wav"
    main.write_bytes(b"")

    result = base.transcribe_meeting_tracks(_session(tmp_path, str(main)), "whisper", 8080)

    assert result.meeting_transcription is not None
    assert str(main) in whisper
    text_path = result.meeting_transcription.transcription_text_file_path
    assert text_path.endswith("track_mainAudio-1_transcription_text.txt")
    assert open(text_path).read() == "bom dia a todos"

    segments = json.load(open(result.meeting_transcription.transcription_segments_file_path))
    assert segments == [{"start": 1.0, "end": 2.5, "text": " bom dia"}]
    # And the participants are still transcribed on their own.
    assert result.tracks["Ana"].transcription.transcription_text_file_path


def test_an_older_session_finds_its_mix_in_its_own_directory(tmp_path, whisper):
    """Sessions sliced before main_track_path existed are still worth reading."""
    main = tmp_path / "track_mainAudio-1.wav"
    main.write_bytes(b"")

    result = base.transcribe_meeting_tracks(_session(tmp_path, None), "whisper", 8080)

    assert result.meeting_transcription is not None
    assert str(main) in whisper


def test_two_mixes_in_one_directory_are_not_guessed_between(tmp_path, whisper):
    """Slicing refuses that case outright; reading it must not pick one either."""
    (tmp_path / "track_mainAudio-1.wav").write_bytes(b"")
    (tmp_path / "track_mainAudio-2.wav").write_bytes(b"")

    result = base.transcribe_meeting_tracks(_session(tmp_path, None), "whisper", 8080)

    assert result.meeting_transcription is None


def test_a_meeting_with_no_mix_still_transcribes_its_participants(tmp_path, whisper):
    result = base.transcribe_meeting_tracks(_session(tmp_path, None), "whisper", 8080)

    assert result.meeting_transcription is None
    assert result.tracks["Ana"].transcription is not None


def test_a_silent_mix_is_not_sent_to_whisper(tmp_path, monkeypatch, whisper):
    """A recording that captured nothing must not come back with invented speech."""
    main = tmp_path / "track_mainAudio-1.wav"
    main.write_bytes(b"")
    monkeypatch.setattr(base, "is_silent", lambda path: path == str(main))

    result = base.transcribe_meeting_tracks(
        _session(tmp_path, str(main), with_participant=False), "whisper", 8080
    )

    assert str(main) not in whisper
    assert open(result.meeting_transcription.transcription_text_file_path).read() == ""


def test_the_mix_offsets_are_left_where_whisper_put_them(tmp_path, whisper):
    """A participant's offsets are remapped because their audio was cut; the mix
    was never cut, so remapping it would move every line."""
    main = tmp_path / "track_mainAudio-1.wav"
    main.write_bytes(b"")

    result = base.transcribe_meeting_tracks(_session(tmp_path, str(main)), "whisper", 8080)

    segments = json.load(open(result.meeting_transcription.transcription_segments_file_path))
    assert segments[0]["start"] == 1.0 and segments[0]["end"] == 2.5
