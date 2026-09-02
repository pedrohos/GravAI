import json
import os
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from glob import glob
from pathlib import Path

from gravai.config.logging_config import get_logger
from gravai.models.common import (
    ActionType,
    ParticipantData,
    Session,
    SessionDataDTO,
    SpeechSegment,
    Track,
    TrackInfoDTO,
)


def _participant_track_path(audio_tracks_path: str, participant_id: str) -> str:
    """Path of a participant's sliced track.

    When slices are grouped by name the id is a display name the participant
    typed, so it has to be sanitized before it goes in a filename - a '/' would
    otherwise point at a directory that does not exist. Both the writer and the
    reader go through here so they cannot disagree.

    Mirrors _sanitize_name in the ws audio server; they cover different id
    namespaces, so they are deliberately not shared.
    """
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in participant_id)
    return os.path.join(audio_tracks_path, f"track_{safe_id}.wav")

# The ws audio server rewrites the session sidecar once per track, from each
# track's connection handler, after re-encoding that track (ffmpeg pitch
# correction) - which for long meetings takes well over a second. So the sidecar
# existing only proves that *some* track finished, not the one we need.
_FILE_WAIT_TIMEOUT_S = 120.0
_FILE_WAIT_POLL_INTERVAL_S = 0.5


def _wait_for_file(path: str, label: str, log_dir: str, timeout: float = _FILE_WAIT_TIMEOUT_S) -> None:
    if os.path.exists(path):
        return
    logger = get_logger("slicing", log_dir)
    logger.info(f"Waiting for {label} file to be written: {path}")
    deadline = time.time() + timeout
    while not os.path.exists(path):
        if time.time() >= deadline:
            raise RuntimeError(f"Missing {label} file: {path}")
        time.sleep(_FILE_WAIT_POLL_INTERVAL_S)
    logger.info(f"{label} file is now available: {path}")


def _wait_for_finalized_sidecar(
    path: str,
    main_track_key: str,
    log_dir: str,
    timeout: float = _FILE_WAIT_TIMEOUT_S,
) -> dict:
    """Waits until the sidecar records ended_at for the main track.

    ended_at is the last field written for a track, so its presence is what
    marks that track's audio as complete - unlike the file merely existing,
    which any other track's handler may have caused.
    """
    logger = get_logger("slicing", log_dir)
    deadline = time.time() + timeout
    announced = False

    while True:
        session_info = None
        if os.path.exists(path) and os.path.getsize(path) > 0:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    session_info = json.load(f)
            except json.JSONDecodeError:
                session_info = None  # caught mid-rewrite, poll again

        if session_info is not None:
            track = session_info.get("tracks", {}).get(main_track_key, {})
            if "ended_at" in track:
                if announced:
                    logger.info(f"Session sidecar finalized for main track {main_track_key}")
                return session_info

        if time.time() >= deadline:
            raise RuntimeError(
                f"Timed out after {timeout:.0f}s waiting for session sidecar {path} to record "
                f"ended_at for main track {main_track_key!r}. The ws audio server may still be "
                f"re-encoding, or it died before closing the track."
            )

        if not announced:
            logger.info(f"Waiting for session sidecar to finalize main track {main_track_key}: {path}")
            announced = True
        time.sleep(_FILE_WAIT_POLL_INTERVAL_S)


def _load_json_or_raise(path: str, label: str) -> dict:
    if not os.path.exists(path):
        raise RuntimeError(f"Missing {label} file: {path}")
    if os.path.getsize(path) == 0:
        raise RuntimeError(f"Empty {label} file: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {label} file: {path} ({exc})") from exc


def _extract_session_dto(slice_audio_tracks: str) -> SessionDataDTO:
    sidecar_path = os.path.join(slice_audio_tracks, "session_sidecar.json")
    vad_timeline_path = os.path.join(slice_audio_tracks, "vad_timeline.json")
    # Sorted so the choice is reproducible, and more than one is rejected rather
    # than silently picked between - a second main track means tracks from two
    # sessions landed in one directory, and slicing the wrong one is unrecoverable.
    main_track_candidates = sorted(glob(os.path.join(slice_audio_tracks, "track_mainAudio*.wav")))
    if not main_track_candidates:
        raise RuntimeError(f"Missing main audio track in {slice_audio_tracks}")
    if len(main_track_candidates) > 1:
        raise RuntimeError(
            f"Expected exactly one main audio track in {slice_audio_tracks}, found "
            f"{len(main_track_candidates)}: {[os.path.basename(p) for p in main_track_candidates]}"
        )

    main_audio_track_path = main_track_candidates[0]
    main_audio_track_name = os.path.basename(main_audio_track_path)
    # `track_mainAudio-90205.wav` -> `mainAudio-90205`, the key the ws audio
    # server writes into the sidecar.
    main_audio_track_name_sidecar_key = Path(main_audio_track_name).stem.removeprefix("track_")

    session_info = _wait_for_finalized_sidecar(
        sidecar_path, main_audio_track_name_sidecar_key, slice_audio_tracks
    )
    _wait_for_file(vad_timeline_path, "VAD timeline", slice_audio_tracks)

    vad_timeline = _load_json_or_raise(vad_timeline_path, "VAD timeline")

    session_data = SessionDataDTO(
        start_time=datetime.fromisoformat(session_info["tracks"][main_audio_track_name_sidecar_key]["started_at"]),
        end_time=datetime.fromisoformat(session_info["tracks"][main_audio_track_name_sidecar_key]["ended_at"]),
        main_track_name=main_audio_track_name,
        session_id=session_info["session_id"],
        events=[
            TrackInfoDTO(
                # action=ActionType.START if event["type"] == "start" else ActionType.END,
                participant_name=event["data"]["participantName"],
                element_id=event["data"]["id"],
                class_count=event["data"]["classCount"],
                timestamp=event["timestamp"],
            ) for event in vad_timeline["events"]
        ],
    )
    return session_data

def _process_participant_data(session_data: SessionDataDTO, group_slices_by_name: bool = True) -> dict:
    all_participants_data = defaultdict(list)
    logger = get_logger("slicing")
    logger.info(f"Slicing participants data for session {session_data.session_id} (group_slices_by_name={group_slices_by_name})")
    for event in session_data.events:
        target_key = event.participant_name if group_slices_by_name else event.element_id
        all_participants_data[target_key].append({
            "class_count": event.class_count,
            "timestamp": event.timestamp,
            "participant_name": event.participant_name,
        })
        # print(f"{event.timestamp} - {event.participant_name} - {event.element_id} - classCount={event.class_count}")

    logger.info(f"Sliced {len(all_participants_data)} participants data for session {session_data.session_id}")
    return all_participants_data

def _find_participant_tracks(session_data: SessionDataDTO, all_participants_data: dict) -> dict:
    participants_tracks = {}
    for participant_id, participant_data in all_participants_data.items():
        class_counts = [event["class_count"] for event in participant_data]
        end_action = min(class_counts)
        start_action = max(class_counts)
        assert len([c for c in class_counts if c == end_action or c == start_action]) == len(class_counts), "Expected only two distinct class counts per participant, representing start and end actions."

        # Alternating: a start is taken only when nothing is open, an end only
        # when something is. Both halves matter - the version that only ever
        # appended an end after the first start kept one segment per participant
        # and dropped every utterance after it, so a caller reading the result
        # saw a plausible transcript of the opening sentence and no sign that the
        # rest of the meeting had been discarded.
        track_changes = []
        for event in participant_data:
            time_in_s = datetime.fromtimestamp(event["timestamp"] / 1000).astimezone() - session_data.start_time
            is_open = bool(track_changes) and track_changes[-1][0] == ActionType.START
            if event["class_count"] == start_action:
                if not is_open:
                    track_changes.append((ActionType.START, time_in_s))
            elif is_open:
                track_changes.append((ActionType.END, time_in_s))
        if track_changes[-1][0] == ActionType.START:
            track_changes.append((ActionType.END, session_data.end_time - session_data.start_time))

        participants_tracks[participant_id] = track_changes
    
    return participants_tracks

# Meet's level indicator animates after the audio it reflects, and the observer
# waits for a second class mutation before calling it speech - together about
# 1.7s, measured against a live call where the words began at 187.75s and the
# timeline marked 190.99s. Cutting on the timeline alone loses the opening words
# of every sentence, so each segment is widened before the audio is taken.
_SEGMENT_LEAD_S = 2.0

# Speech also ends before the indicator stops, so the tail is smaller: it covers
# the observer's own 600ms silence hold-off.
_SEGMENT_TAIL_S = 0.8


def _pad_segments(
    segments: list[tuple[float, float]],
    lead: float = _SEGMENT_LEAD_S,
    tail: float = _SEGMENT_TAIL_S,
) -> list[tuple[float, float]]:
    """Widens each segment and merges the ones that then overlap.

    Overlapping segments would make ffmpeg's concat replay the same audio twice,
    so a sentence broken into three detections comes back stuttering.
    """
    if not segments:
        return []
    widened = [(max(0.0, start - lead), end + tail) for start, end in sorted(segments)]
    merged = [widened[0]]
    for start, end in widened[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _speech_track_path(audio_tracks_path: str, participant_id: str) -> str:
    """Path of a participant's speech-only track, next to the aligned one."""
    aligned = Path(_participant_track_path(audio_tracks_path, participant_id))
    return str(aligned.with_name(f"{aligned.stem}_speech.wav"))


def _ffmpeg_slice(audio_tracks_path, session_data, participants_tracks_locations) -> dict:
    """Writes two files per participant and returns the segments behind them.

    The aligned track keeps the meeting's timeline, silence and all, so anything
    reading it can line it up against the main track. The speech track is those
    segments concatenated, which is what goes to whisper: asked to transcribe
    minutes of silence around a few seconds of talking, it fills the silence with
    invented sentences, one per 30-second window.
    """
    participants_segments: dict[str, list[tuple[float, float]]] = {}
    for participant_id, track_changes in participants_tracks_locations.items():
        participant_track_path = _participant_track_path(audio_tracks_path, participant_id)
        speech_track_path = _speech_track_path(audio_tracks_path, participant_id)
        main_track_path = os.path.join(audio_tracks_path, session_data.main_track_name)
        segments = []
        start_time = None
        for change in track_changes:
            if change[0] == ActionType.START:
                start_time = change[1].total_seconds()
            elif start_time is not None:
                end_time = change[1].total_seconds()
                segments.append((start_time, end_time))
                start_time = None

        segments = _pad_segments(segments)
        participants_segments[participant_id] = segments

        if segments:
            active_expr = " + ".join(
                f"between(t,{start},{end})" for start, end in segments
            )
        else:
            active_expr = "0"

        filter_chain = ",".join([
            "apad=pad_dur=2",
            f"volume=enable='{active_expr}':volume=1",
            f"volume=enable='not({active_expr})':volume=0",
        ])

        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i", main_track_path,
            "-af", filter_chain,
            participant_track_path,
        ]
        subprocess.run(ffmpeg_cmd, check=True)

        _write_speech_track(main_track_path, speech_track_path, segments)

    return participants_segments


def _write_speech_track(main_track_path: str, output_path: str, segments: list[tuple[float, float]]) -> None:
    """Concatenates just the segments, in order, into one file.

    A participant with no segments still gets a file, so every consumer can
    assume it exists; it is empty, and the silence check before transcription is
    what keeps it away from whisper.
    """
    if not segments:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
             "-t", "0.05", output_path],
            check=True,
        )
        return

    # One trim per segment, concatenated in the filter graph rather than through
    # intermediate files, so this stays a single pass over the main track.
    filters = []
    for index, (start, end) in enumerate(segments):
        filters.append(
            f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[s{index}]"
        )
    concat_inputs = "".join(f"[s{index}]" for index in range(len(segments)))
    filters.append(f"{concat_inputs}concat=n={len(segments)}:v=0:a=1[out]")

    subprocess.run(
        ["ffmpeg", "-y", "-i", main_track_path,
         "-filter_complex", ";".join(filters), "-map", "[out]", output_path],
        check=True,
    )

class Slicer:
    @staticmethod
    def slice_audio_tracks_teams(audio_tracks_path: str, group_slices_by_name: bool = True):
        session_data_dto = _extract_session_dto(audio_tracks_path)
        all_participants_data = _process_participant_data(session_data_dto, group_slices_by_name)
        participants_tracks_locations = _find_participant_tracks(session_data_dto, all_participants_data)
        participants_segments = _ffmpeg_slice(
            audio_tracks_path, session_data_dto, participants_tracks_locations
        )

        with open(os.path.join(audio_tracks_path, "slice_metadata.json"), "w") as f:
            json.dump({
                "session_data": session_data_dto.model_dump(),
                "all_participants_data": all_participants_data,
                "participants_tracks_locations": participants_tracks_locations,
                # Seconds from the start of the main track, in the order they were
                # concatenated into the speech track - transcription maps whisper's
                # offsets back onto the meeting through these.
                "participants_segments": participants_segments,
            }, f, default=str)

    @staticmethod
    def slice_and_create_session_teams_audio_track(audio_tracks_path: str, group_slices_by_name: bool = True) -> Session:
        Slicer.slice_audio_tracks_teams(audio_tracks_path, group_slices_by_name) # TODO: Add support for Google Meet slicing in the future
        session = Slicer.create_session_teams(audio_tracks_path)
        return session
    
    @staticmethod
    def save_session_data(session: Session, output_path: str):
        with open(output_path, "w") as f:
            json.dump(session.model_dump(), f, default=str)

    @staticmethod
    def create_session_teams(audio_tracks_path: str):
        with open(os.path.join(audio_tracks_path, "slice_metadata.json"), "r") as f:
            metadata = json.load(f)

        session_data_dto = SessionDataDTO(**metadata["session_data"])
        all_participants_data = metadata["all_participants_data"]
        participants_tracks_locations = metadata["participants_tracks_locations"]

        participants_segments = metadata.get("participants_segments", {})

        session_tracks = {}
        for participant_id, track_changes in participants_tracks_locations.items():
            participant_name = all_participants_data[participant_id][0]["participant_name"]
            participant_track_path = _participant_track_path(audio_tracks_path, participant_id)
            speech_track_path = _speech_track_path(audio_tracks_path, participant_id)
            session_tracks[participant_id] = ParticipantData(
                participant_id=participant_id,
                participant_name=participant_name,
                track=Track(
                    wav_file_path=participant_track_path,
                    speech_wav_file_path=(
                        speech_track_path if os.path.exists(speech_track_path) else None
                    ),
                    speech_segments=[
                        SpeechSegment(start=float(start), end=float(end))
                        for start, end in participants_segments.get(participant_id, [])
                    ],
                )
            )
            assert os.path.exists(participant_track_path), f"Expected track file for participant {participant_name} (id: {participant_id}) not found at path: {participant_track_path}. Retry slicing or check for errors in ffmpeg slicing step."
        return Session(
            session_id=session_data_dto.session_id,
            session_start=session_data_dto.start_time,
            session_end=session_data_dto.end_time,
            tracks=session_tracks,
            # The mix the participant tracks were cut from, so that transcription
            # can also read the meeting as one conversation. Resolved here rather
            # than re-globbed later: this is the one place that already knows
            # which of the files in the directory the slicing actually used.
            main_track_path=os.path.join(audio_tracks_path, session_data_dto.main_track_name),
        )