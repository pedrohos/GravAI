"""The meeting routes, as shorthands for submitting a job.

These three paths used to do the work inside the request and answer with the
result, which meant an HTTP connection held open for the length of a meeting: a
proxy timing out, a client reconnecting, or a reload of the server all lost the
recording, and there was no way to ask about one while it ran.

They now submit a job and answer with it, immediately. What they still are is
the short way to say the common things - the body of POST /jobs written out as a
path and two query parameters - and they refuse exactly what that route refuses,
at the same moment, for the same reasons. Poll GET /jobs/{id} for the result, and
GET /recordings/{id} for what it recorded.
"""

from fastapi import APIRouter, status

from gravai.api.routes.errors import handled
from gravai.api.schemas import CaptchaChallenge
from gravai.config.logging_config import get_logger
from gravai.config.settings import get_settings
from gravai.jobs import runner
from gravai.jobs.models import Job, JobSubmission, JobType
from gravai.recording.common.vnc import pending_challenges

logger = get_logger("api.meetings")

router = APIRouter(tags=["meetings"])


@router.post("/record_meeting", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
def record_meeting(meeting_url: str, slice_tracks: bool = True) -> Job:
    """Joins a meeting and records it, cutting one track per participant."""
    logger.info(f"Received /record_meeting request for {meeting_url} (slice_tracks={slice_tracks})")

    with handled("/record_meeting", meeting_url):
        return runner.submit(
            JobSubmission(
                type=JobType.RECORD,
                meeting_url=meeting_url,
                slice_tracks=slice_tracks,
            )
        )


@router.post(
    "/record_meeting_and_transcribe", response_model=Job, status_code=status.HTTP_202_ACCEPTED
)
def record_meeting_and_transcribe(meeting_url: str, group_slices_by_name: bool = True) -> Job:
    """The whole pipeline: join, record, slice, transcribe each speaker and the
    meeting as a whole.

    Two jobs, not one. The recording is what comes back; the transcription that
    follows it is in the job list from the same moment, `waiting`, and names the
    recording it is behind through `depends_on`. Splitting them is what lets a
    whisper outage leave a finished recording and one failed transcription that
    can be run again, rather than a single job reporting the meeting as lost."""
    logger.info(f"Received /record_meeting_and_transcribe request for {meeting_url}")

    with handled("/record_meeting_and_transcribe", meeting_url):
        return runner.submit(
            JobSubmission(
                type=JobType.RECORD_AND_TRANSCRIBE,
                meeting_url=meeting_url,
                group_slices_by_name=group_slices_by_name,
            )
        )


@router.post("/transcribe", response_model=Job, status_code=status.HTTP_202_ACCEPTED)
def transcribe(tracks_output_dir: str, group_slices_by_name: bool = True) -> Job:
    """Slices and transcribes a directory a previous recording left behind."""
    logger.info(f"Received /transcribe request for {tracks_output_dir}")

    with handled("/transcribe", tracks_output_dir):
        return runner.submit(
            JobSubmission(
                type=JobType.TRANSCRIBE,
                tracks_output_dir=tracks_output_dir,
                group_slices_by_name=group_slices_by_name,
            )
        )


@router.get("/captcha_challenges", response_model=list[CaptchaChallenge])
def captcha_challenges() -> list[CaptchaChallenge]:
    """Every recording currently stuck on a CAPTCHA, and where to answer it.

    A Google sign-in that hits one cannot get past it on its own - the challenge
    is asking whether there is a person - so the recorder puts the browser's
    screen on a VNC port and waits. This is how to find that port without tailing
    a session log: it reads the record each waiting recording leaves in its own
    session directory.

    Empty is the ordinary answer. Nothing here starts or stops a VNC server.
    """
    logger.info("Received /captcha_challenges request")

    with handled("/captcha_challenges", "the save directory"):
        waiting = pending_challenges(get_settings().SAVE_DIR)

    if waiting:
        logger.warning(f"{len(waiting)} CAPTCHA(s) waiting for somebody to answer them")
    return [CaptchaChallenge.model_validate(challenge) for challenge in waiting]
