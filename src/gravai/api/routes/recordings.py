"""The catalogue: meetings that have been recorded, and what came of them.

A job says whether work finished. This says what the work produced, and keeps
saying it long after the job that did it has been forgotten - one row per
meeting, with when it ran, who spoke, when each of them was speaking, and what
each of them said.

The audio is not in these responses; it is on disk, and there are two routes at
the end here that stream it.
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from gravai.api.routes.errors import handled
from gravai.api.schemas import Recording
from gravai.config.logging_config import get_logger
from gravai.jobs import store
from gravai.jobs.models import Job
from gravai.models.database import RecordingDTO

logger = get_logger("api.recordings")

router = APIRouter(prefix="/recordings", tags=["recordings"])


def _to_schema(row: RecordingDTO) -> Recording:
    started, ended = row.started_at, row.ended_at
    return Recording(
        **row.model_dump(),
        duration_seconds=(ended - started).total_seconds()
        if started and ended
        else None,
    )


@router.get("", response_model=list[Recording])
def list_recordings(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Recording]:
    """Every meeting, newest first, each with its speakers and their transcripts.

    Meetings still in progress are in here too, from the moment the recorder has
    a directory - they carry status `recording` and no timings yet, because
    nothing knows when a meeting will end until it has.
    """
    with handled("GET /recordings", "the recording list"):
        return [
            _to_schema(row) for row in store.list_recordings(limit=limit, offset=offset)
        ]


@router.get("/{recording_id}", response_model=Recording)
def get_recording(recording_id: str) -> Recording:
    """One meeting: its timings, its speakers, their segments and their text."""
    row = store.get_recording(recording_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No recording with id {recording_id}."
        )
    return _to_schema(row)


@router.get("/{recording_id}/jobs", response_model=list[Job])
def recording_jobs(recording_id: str) -> list[Job]:
    """The jobs that produced this meeting, oldest first.

    More than one is ordinary: a recording job and, later, a transcription job
    run over the directory it left behind.
    """
    if store.get_recording(recording_id) is None:
        raise HTTPException(
            status_code=404, detail=f"No recording with id {recording_id}."
        )
    return store.jobs_for_recording(recording_id)


@router.get("/{recording_id}/audio")
def recording_audio(recording_id: str) -> FileResponse:
    """The mixed track of the whole meeting."""
    row = store.get_recording(recording_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No recording with id {recording_id}."
        )
    return _audio_response(row.main_track_path, f"{recording_id}.flac")


@router.get("/{recording_id}/participants/{participant_id}/audio")
def participant_audio(
    recording_id: str, participant_id: str, speech_only: bool = False
) -> FileResponse:
    """One speaker's track.

    Two exist per speaker and they are not interchangeable. The default is the
    aligned one, which is the length of the meeting with everything but that
    speaker silenced, so it plays against the others and against the mix.
    `speech_only=true` is their turns concatenated with the silence removed,
    which is the audio whisper was given and is what to listen to when checking a
    transcript against it.
    """
    row = store.get_recording(recording_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No recording with id {recording_id}."
        )

    for participant in row.participants:
        if participant.participant_id == participant_id:
            path = (
                participant.speech_track_path if speech_only else participant.track_path
            )
            return _audio_response(path, f"{participant_id}.flac")

    raise HTTPException(
        status_code=404,
        detail=f"Recording {recording_id} has no participant {participant_id}.",
    )


def _audio_response(path: str | None, download_name: str) -> FileResponse:
    """Serves a track, or explains which of the two ways it is missing.

    Only paths this service itself recorded reach here, so nothing a caller sends
    is ever joined into a filesystem path - a recording id that is not in the
    catalogue is a 404 long before this.
    """
    if not path:
        raise HTTPException(
            status_code=404, detail="No audio was recorded for this track."
        )
    if not os.path.exists(path):
        # The catalogue outlives the files: SAVE_DIR defaults to /tmp, and a row
        # pointing at audio somebody has since cleaned up is a gone resource, not
        # a broken service.
        raise HTTPException(
            status_code=410, detail=f"The audio file is no longer on disk: {path}"
        )
    suffix = Path(path).suffix.lower()
    media_type = "audio/flac" if suffix == ".flac" else "audio/wav"
    download_name = str(Path(download_name).with_suffix(suffix))
    return FileResponse(path, media_type=media_type, filename=download_name)


@router.delete("/{recording_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_recording(recording_id: str) -> None:
    """Forgets a meeting. Its audio and transcripts on disk are left alone."""
    if not store.delete_recording(recording_id):
        raise HTTPException(
            status_code=404, detail=f"No recording with id {recording_id}."
        )


def _deletable_recording(recording_id: str) -> RecordingDTO:
    row = store.get_recording(recording_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"No recording with id {recording_id}."
        )
    active_jobs = [
        job
        for job in store.active_jobs()
        if not job.status.is_terminal
        and (
            job.recording_id == recording_id
            or job.session_dir == row.session_dir
            or job.params.tracks_output_dir == row.session_dir
        )
    ]
    if row.status in {"recording", "processing"} or active_jobs:
        raise HTTPException(
            status_code=409,
            detail="Wait for recording and transcription jobs to finish before deleting.",
        )
    return row


def _delete_files(row: RecordingDTO, *, audio: bool) -> None:
    # Derive only pipeline-owned filenames from catalogued tracks. Keep session
    # metadata and unrelated files, and never follow a directory supplied by HTTP.
    tracks = {row.main_track_path}
    for participant in row.participants:
        tracks.update((participant.track_path, participant.speech_track_path))
    paths = set()
    for track in filter(None, tracks):
        path = Path(track)
        if audio:
            paths.add(path)
        for suffix in (
            "_transcription_text.txt",
            "_transcription_segments.txt",
            "_transcription_segments.json",
        ):
            paths.add(path.with_name(path.stem + suffix))
    with handled("DELETE /recordings files", "the recording files"):
        for path in paths:
            path.unlink(missing_ok=True)


@router.delete("/{recording_id}/transcription", status_code=status.HTTP_204_NO_CONTENT)
def delete_transcription(recording_id: str) -> None:
    """Delete meeting and speaker transcripts, retaining recorded audio."""
    row = _deletable_recording(recording_id)
    _delete_files(row, audio=False)
    store.delete_transcripts(recording_id)


@router.delete("/{recording_id}/files", status_code=status.HTTP_204_NO_CONTENT)
def delete_recording_and_transcription(recording_id: str) -> None:
    """Delete catalogued audio and transcripts and remove the catalogue entry."""
    row = _deletable_recording(recording_id)
    _delete_files(row, audio=True)
    store.delete_recording(recording_id)
