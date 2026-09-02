"""Guards every utterance after the first from being thrown away.

The walk that turns VAD events into cut points used to append a START only while
track_changes was empty, so once the first pair closed nothing could reopen: each
participant came out with exactly one segment, however long they had spoken, and
everything after their first sentence was dropped.

It stayed invisible because the result is not empty - it is a plausible
transcript of the opening sentence, with no sign the rest was discarded. What
made it visible was a Meet call where three tiles shared one display name: the
single surviving pair belonged to the bot's own tile animating as it rendered,
which is 1.4s of silence, so the transcript came back empty instead of short.
"""

from datetime import UTC, datetime, timedelta

from gravai.models.common import ActionType, SessionDataDTO, TrackInfoDTO
from gravai.slicing.slice import _find_participant_tracks, _process_participant_data

START = datetime(2026, 8, 19, 19, 50, 28, tzinfo=UTC)


def _event(name: str, element_id: str, offset_s: float, speaking: bool) -> TrackInfoDTO:
    return TrackInfoDTO(
        participant_name=name,
        element_id=element_id,
        timestamp=int((START + timedelta(seconds=offset_s)).timestamp() * 1000),
        class_count=1 if speaking else 0,
    )


def _session(events: list[TrackInfoDTO]) -> SessionDataDTO:
    return SessionDataDTO(
        start_time=START,
        end_time=START + timedelta(seconds=200),
        main_track_name="track_mainAudio-1.wav",
        session_id="test-session",
        events=events,
    )


def _segments(session: SessionDataDTO, grouped: bool = True) -> dict[str, list[tuple[float, float]]]:
    tracks = _find_participant_tracks(session, _process_participant_data(session, grouped))
    out: dict[str, list[tuple[float, float]]] = {}
    for participant, changes in tracks.items():
        segments, opened = [], None
        for action, delta in changes:
            if action == ActionType.START:
                opened = round(delta.total_seconds(), 1)
            elif opened is not None:
                segments.append((opened, round(delta.total_seconds(), 1)))
                opened = None
        out[participant] = segments
    return out


def test_every_utterance_is_kept_not_just_the_first():
    session = _session([
        _event("Ada", "device/1", 10, True),
        _event("Ada", "device/1", 12, False),
        _event("Ada", "device/1", 30, True),
        _event("Ada", "device/1", 35, False),
        _event("Ada", "device/1", 50, True),
        _event("Ada", "device/1", 51, False),
    ])

    assert _segments(session)["Ada"] == [(10.0, 12.0), (30.0, 35.0), (50.0, 51.0)]


def test_a_participant_still_speaking_at_the_end_is_closed_at_the_end():
    session = _session([
        _event("Ada", "device/1", 10, True),
        _event("Ada", "device/1", 12, False),
        _event("Ada", "device/1", 190, True),
    ])

    assert _segments(session)["Ada"] == [(10.0, 12.0), (190.0, 200.0)]


def test_a_repeated_start_does_not_open_a_second_segment():
    # The observer emits one start per burst, but a duplicate must not leave two
    # opens outstanding - the second would silently swallow the real end.
    session = _session([
        _event("Ada", "device/1", 10, True),
        _event("Ada", "device/1", 11, True),
        _event("Ada", "device/1", 12, False),
    ])

    assert _segments(session)["Ada"] == [(10.0, 12.0)]


def test_an_end_with_nothing_open_is_ignored():
    session = _session([
        _event("Ada", "device/1", 5, False),
        _event("Ada", "device/1", 10, True),
        _event("Ada", "device/1", 12, False),
    ])

    assert _segments(session)["Ada"] == [(10.0, 12.0)]


def test_tiles_sharing_a_name_are_merged_rather_than_reduced_to_one():
    # The live case: a signed-in bot and the host both named 'Pedro Silva', on
    # separate devices. Grouped by name their events interleave into one list,
    # and the walk has to keep reopening across them - the bot's 1.4s blip must
    # not be the only thing that survives.
    session = _session([
        _event("Pedro Silva", "device/bot", 22.5, True),
        _event("Pedro Silva", "device/bot", 23.3, False),
        _event("Pedro Silva", "device/host", 32.8, True),
        _event("Pedro Silva", "device/host", 36.9, False),
        _event("Pedro Silva", "device/host", 43.5, True),
        _event("Pedro Silva", "device/host", 45.6, False),
    ])

    segments = _segments(session, grouped=True)["Pedro Silva"]

    assert segments == [(22.5, 23.3), (32.8, 36.9), (43.5, 45.6)]
    assert sum(end - start for start, end in segments) > 5.0


def test_ungrouped_each_device_keeps_its_own_utterances():
    session = _session([
        _event("Pedro Silva", "device/bot", 22.5, True),
        _event("Pedro Silva", "device/bot", 23.3, False),
        _event("Pedro Silva", "device/host", 32.8, True),
        _event("Pedro Silva", "device/host", 36.9, False),
        _event("Pedro Silva", "device/host", 43.5, True),
        _event("Pedro Silva", "device/host", 45.6, False),
    ])

    segments = _segments(session, grouped=False)

    assert segments["device/bot"] == [(22.5, 23.3)]
    assert segments["device/host"] == [(32.8, 36.9), (43.5, 45.6)]
