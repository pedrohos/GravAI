import pytest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.gravai.api.app import record_meeting_and_transcribe as api_record_meeting_and_transcribe
from src.gravai.transcribe.base import transcribe_meeting_tracks
# from main import main

from src.gravai.recording.service import record_meeting as service_record_meeting
from src.gravai.recording.service import start_ws_server as service_record_start_ws_server
from src.gravai.recording.service import stop_ws_server as service_record_stop_ws_server

from src.gravai.slicing.service import slice_track as service_slice_track

from src.gravai.transcribe.service import transcribe_meeting_tracks as service_transcribe_meeting_tracks

def test_record_meeting_and_transcribe():
    url = "" # Insert URL of teams here
    output = "/tmp"
    tracks_output_dir = service_record_meeting(url, debug=True)
    metadata_output_path, session = service_slice_track(tracks_output_dir)
    transc_session = service_transcribe_meeting_tracks(session)
    # transc_session = transcribe_meeting_tracks(session)
    # print(transc_session)

if __name__ == "__main__":
    test_record_meeting_and_transcribe()
