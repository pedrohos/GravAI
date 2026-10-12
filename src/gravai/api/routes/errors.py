"""One mapping from pipeline failures onto status codes, for every route.

Kept in one place because the distinction that matters - a request this service
was wrong to accept, against an upstream that is down - is the same distinction
whichever route was asked, and a route that got it slightly differently would
have callers retrying the wrong failures.
"""

from contextlib import contextmanager

from fastapi import HTTPException

from gravai.api import pipeline
from gravai.config.env_file import ConfigError
from gravai.config.logging_config import get_logger
from gravai.jobs.runner import JobError, JobNotFound, JobNotStoppable
from gravai.transcribe.errors import WhisperError

logger = get_logger("api.routes")


@contextmanager
def handled(operation: str, target: str):
    try:
        yield
    except HTTPException:
        raise
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except JobNotStoppable as exc:
        # The job is real and the request is well formed; it is the job's current
        # state that makes it impossible, which is what 409 says.
        logger.info(f"{operation} refused for {target}: {exc}")
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (JobError, ConfigError) as exc:
        logger.warning(f"{operation} rejected {target}: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except pipeline.UnsupportedMeetingURL as exc:
        logger.warning(f"{operation} rejected {target}: {exc}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotImplementedError as exc:
        logger.warning(f"{operation} unsupported for {target}: {exc}")
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except WhisperError as exc:
        # Upstream transcription service failed - this request was fine, so it
        # reports as a gateway failure rather than an error in this service.
        logger.error(f"{operation} failed for {target}: {exc}")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"{operation} failed for {target}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
