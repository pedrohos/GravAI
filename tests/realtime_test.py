import os

import pytest

from gravai.models.models import RecordingType
from gravai.recording.service import record_meeting as service_record_meeting
from gravai.slicing.service import slice_track as service_slice_track
from gravai.transcribe.service import transcribe_meeting_tracks as service_transcribe_meeting_tracks

_TEAMS_MEETING_URL = os.environ.get("GRAVAI_TEST_TEAMS_MEETING_URL")
_MEET_MEETING_URL = os.environ.get("GRAVAI_TEST_MEET_MEETING_URL")


@pytest.mark.skipif(not _TEAMS_MEETING_URL, reason="GRAVAI_TEST_MEETING_URL is not set")
def test_record_meeting_and_transcribe_teams():
    tracks_output_dir = service_record_meeting(RecordingType.TEAMS, _TEAMS_MEETING_URL, debug=True)
    metadata_output_path, session = service_slice_track(RecordingType.TEAMS, tracks_output_dir)
    transc_session = service_transcribe_meeting_tracks(session)

    assert os.path.exists(metadata_output_path)
    assert transc_session.tracks

@pytest.mark.skipif(not _MEET_MEETING_URL, reason="GRAVAI_TEST_MEETING_URL is not set")
def test_record_meeting_and_transcribe_meet():
    tracks_output_dir = service_record_meeting(RecordingType.MEET, _MEET_MEETING_URL, debug=True)
    metadata_output_path, session = service_slice_track(RecordingType.MEET, tracks_output_dir)
    transc_session = service_transcribe_meeting_tracks(session)

    assert os.path.exists(metadata_output_path)
    assert transc_session.tracks
