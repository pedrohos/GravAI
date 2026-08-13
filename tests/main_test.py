import pytest

from gravai.models.models import RecordingType

from gravai.api.pipeline import record_and_transcribe  as api_record_meeting_and_transcribe
from gravai.transcribe.base import transcribe_meeting_tracks

from gravai.recording.service import record_meeting as service_record_meeting
from gravai.recording.service import start_ws_server as service_record_start_ws_server
from gravai.recording.service import stop_ws_server as service_record_stop_ws_server

from gravai.slicing.service import slice_track as service_slice_track

from gravai.transcribe.service import transcribe_meeting_tracks as service_transcribe_meeting_tracks

@pytest.fixture(scope="module", autouse=True)
def up_audio_worker():
    try:
        service_record_start_ws_server()
        yield
    finally:
        service_record_stop_ws_server()


def test_record_meeting_and_transcribe(up_audio_worker):
    url = "" # Insert URL of teams here
    output = "/tmp"
    tracks_output_dir = service_record_meeting(RecordingType.TEAMS, url, debug=True)
    metadata_output_path, session = service_slice_track(RecordingType.TEAMS, tracks_output_dir)
    transc_session = service_transcribe_meeting_tracks(session)
    # transc_session = transcribe_meeting_tracks(session)
    # print(transc_session)
