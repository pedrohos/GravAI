import os
import re
import subprocess
from glob import glob
from pathlib import Path

import requests

from gravai.config.logging_config import get_logger
from gravai.models.common import (
    ParticipantDataTransc,
    Session,
    TranscriptedSession,
    Transcription,
)
from gravai.transcribe.errors import WhisperError
from gravai.transcribe.formatter import format_and_save_single_segment

# (connect, read). Transcribing a long track legitimately takes minutes, so the
# read budget is generous; only the connect needs to fail fast.
_WHISPER_TIMEOUT_S = (10.0, 1800.0)

# Peak level under which a track is taken to hold no speech at all, in dBFS.
# Speech in these recordings peaks between -22 and -14 dBFS, and a track carrying
# nothing measures below -90; -45 sits far enough from both that a track has to
# be genuinely empty to be skipped. The point is to skip only what is certainly
# silent, since whisper answers silence with invented sentences rather than with
# nothing.
_SILENCE_PEAK_DBFS = -45.0

_MAX_VOLUME_PATTERN = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB")


def peak_dbfs(audio_file: str) -> float | None:
    """Loudest sample in the file, in dBFS, or None when it cannot be measured.

    Read with ffmpeg's volumedetect rather than in Python: these are whole
    meetings at 48kHz, and the answer is needed for every track.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-i", audio_file,
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = _MAX_VOLUME_PATTERN.search(result.stderr)
    if match:
        return float(match.group(1))
    # A file with no audio at all reports -inf, which the pattern above skips.
    return float("-inf") if "max_volume: -inf" in result.stderr else None


def is_silent(audio_file: str) -> bool:
    """Whether the file is certainly silent.

    Anything that cannot be measured counts as not silent, so a broken probe
    sends the audio to whisper instead of dropping a participant's words.
    """
    peak = peak_dbfs(audio_file)
    if peak is None:
        return False
    return peak <= _SILENCE_PEAK_DBFS


def to_meeting_time(offset: float, segments: list, at_segment_start: bool = False) -> float:
    """Maps an offset inside the speech track back onto the meeting timeline.

    The speech track is the participant's segments concatenated, so a second at
    its start is whenever their first segment began. Offsets past the end map to
    the end of the last segment, which is where whisper puts a trailing token.

    An offset landing exactly on a seam belongs to whichever side is being asked
    about: the end of a phrase is the end of the segment that carried it, while
    the start of the next phrase is the start of the segment after the seam -
    minutes apart, in a meeting where someone stops talking and resumes later.
    """
    if not segments:
        return offset
    consumed = 0.0
    for segment in segments:
        duration = max(0.0, segment.end - segment.start)
        inside = offset < consumed + duration if at_segment_start else offset <= consumed + duration
        if inside:
            return segment.start + (offset - consumed)
        consumed += duration
    return segments[-1].end

def _write_transcription_files(wav_file_path: str, text: str, segments: list) -> Transcription:
    """Writes the two files a transcription is made of, next to its audio.

    Every transcript in a session - one per participant, plus the meeting as a
    whole - is named after the wav it came from, so the pair can always be found
    from the track and never has to be guessed at.
    """
    track_path = Path(wav_file_path)
    transcription_text_output_path = str(
        track_path.with_name(f"{track_path.stem}_transcription_text.txt")
    )
    segments_input_path = track_path.with_name(f"{track_path.stem}_transcription_segments.txt")
    with open(transcription_text_output_path, "w") as f:
        f.write(text)
    # Returns the .json path it renamed to, which is what actually exists.
    transcription_segments_output_path = str(
        format_and_save_single_segment(segments_input_path, segments)
    )
    return Transcription(
        transcription_text_file_path=transcription_text_output_path,
        transcription_segments_file_path=transcription_segments_output_path,
    )


def _transcribe_or_skip_silence(
    source_path: str,
    whisper_host: str,
    whisper_port: int,
    language: str | None,
    logger,
    label: str,
) -> dict:
    """Whisper's answer for one file, or an empty one when it is certainly silent.

    The gate is here rather than at the call sites because it protects every one
    of them for the same reason: asked to transcribe silence, whisper answers
    with sentences nobody said.
    """
    if is_silent(source_path):
        logger.info(
            f"Skipping whisper for {label}: {os.path.basename(source_path)} peaks at or below "
            f"{_SILENCE_PEAK_DBFS} dBFS"
        )
        return {"text": "", "segments": []}

    logger.info(f"Transcribing {label}")
    return transcribe(source_path, whisper_host, int(whisper_port), language=language)


def _resolve_main_track(session: Session) -> str | None:
    """The mixed track of a session, from the session or from its directory.

    Sessions sliced before main_track_path existed do not carry one, and their
    metadata is still on disk and still worth transcribing, so the directory is
    searched as a fallback. More than one match is left alone: which mix a
    directory holding two sessions belongs to is not answerable here, and slicing
    already refuses that case for the same reason.
    """
    if session.main_track_path and os.path.exists(session.main_track_path):
        return session.main_track_path

    session_dir = _session_dir(session)
    if not session_dir:
        return None
    candidates = sorted(glob(os.path.join(session_dir, "track_mainAudio*.wav")))
    return candidates[0] if len(candidates) == 1 else None


def _session_dir(session: Session) -> str | None:
    """Directory a session's files live in, from whichever path it carries."""
    if session.main_track_path:
        return os.path.dirname(session.main_track_path)
    if session.tracks:
        return os.path.dirname(next(iter(session.tracks.values())).track.wav_file_path)
    return None


def transcribe_meeting_tracks(
        session: Session,
        whisper_host: str,
        whisper_port: int,
        language: str | None = None,
    ):
    log_dir = _session_dir(session)
    logger = get_logger("transcribe", log_dir)

    logger.info(f"Transcription started for session {session.session_id} ({len(session.tracks)} track(s))")
    tracks = {}
    for participant_id, participant_data in session.tracks.items():
        wav_file_path = participant_data.track.wav_file_path
        os.makedirs(os.path.dirname(wav_file_path), exist_ok=True)

        # The speech-only track when slicing produced one: whisper fills silence
        # with sentences nobody said, at one per 30-second window.
        speech_path = participant_data.track.speech_wav_file_path
        segments_map = participant_data.track.speech_segments
        source_path = speech_path if speech_path and os.path.exists(speech_path) else wav_file_path

        transcription = _transcribe_or_skip_silence(
            source_path,
            whisper_host,
            whisper_port,
            language,
            logger,
            f"participant {participant_data.participant_name} (id: {participant_id})",
        )

        transcription_text = transcription.get("text", "")
        transcription_segments = transcription.get("segments") or []
        if source_path == speech_path:
            # Offsets come back relative to the concatenated audio; the rest of
            # the pipeline reads them as times in the meeting.
            for segment in transcription_segments:
                if not isinstance(segment, dict):
                    continue
                for key, at_start in (("start", True), ("end", False)):
                    if segment.get(key) is not None:
                        segment[key] = round(
                            to_meeting_time(float(segment[key]), segments_map, at_start), 3
                        )

        logger.info(f"Transcription finished for participant {participant_data.participant_name} (id: {participant_id})")
        tracks[participant_id] = ParticipantDataTransc(
            participant_id=participant_id,
            participant_name=participant_data.participant_name,
            track=participant_data.track,
            transcription=_write_transcription_files(
                wav_file_path, transcription_text, transcription_segments
            ),
        )

    meeting_transcription = _transcribe_meeting(
        session, whisper_host, whisper_port, language, logger
    )

    logger.info(f"Transcription completed for session {session.session_id}")
    return TranscriptedSession(
        session_id=session.session_id,
        session_start=session.session_start,
        session_end=session.session_end,
        tracks=tracks,
        main_track_path=session.main_track_path,
        meeting_transcription=meeting_transcription,
    )


def _transcribe_meeting(
    session: Session,
    whisper_host: str,
    whisper_port: int,
    language: str | None,
    logger,
) -> Transcription | None:
    """Transcribes the meeting from its mixed track, in one pass.

    The per-participant transcripts say who said what but each one is that voice
    alone, cut into the stretches the VAD marked - so a sentence the observer
    missed the start of, or a moment two people talk over each other, reads worse
    there than it does on the mix. This is the same meeting read as one
    conversation, offsets already on the meeting's timeline because the mix was
    never cut.

    Its silence gate matters less than the participants' - a real meeting is not
    silent - but it is what keeps a recording that captured nothing from coming
    back with invented speech.
    """
    main_track_path = _resolve_main_track(session)
    if not main_track_path:
        logger.warning(
            f"No mixed track found for session {session.session_id}; "
            f"skipping the whole-meeting transcription"
        )
        return None

    transcription = _transcribe_or_skip_silence(
        main_track_path,
        whisper_host,
        whisper_port,
        language,
        logger,
        f"the whole meeting ({os.path.basename(main_track_path)})",
    )
    written = _write_transcription_files(
        main_track_path,
        transcription.get("text", ""),
        transcription.get("segments") or [],
    )
    logger.info(f"Whole-meeting transcription written to {written.transcription_text_file_path}")
    return written


def transcribe(
    audio_file: str,
    whisper_server_host: str,
    whisper_server_port: int,
    timeout: tuple[float, float] = _WHISPER_TIMEOUT_S,
    language: str | None = None,
) -> dict:
    # Without a timeout a stalled whisper server pins this thread forever, and
    # since the routes run in the threadpool, enough of those stop the API from
    # serving anything at all.
    url = f"http://{whisper_server_host}:{whisper_server_port}/inference"
    data = {
        "temperature": "0.0",
        "response_format": "verbose_json",
    }
    # 'auto' and an empty value both mean 'let whisper decide', which is what it
    # does when the field is absent - so neither is sent.
    if language and language.casefold() != "auto":
        data["language"] = language
    with open(audio_file, "rb") as audio:
        response = requests.post(
            url,
            files={
                "file": (os.path.basename(audio_file), audio, "audio/wav")
            },
            data=data,
            timeout=timeout,
        )
    if response.status_code != 200:
        raise WhisperError(response.status_code, response.text, url, audio_file)

    # whisper.cpp usually returns the transcription under the "text" key when using verbose_json
    return response.json()