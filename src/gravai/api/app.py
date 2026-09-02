"""The HTTP surface.

Four groups of routes: /jobs, which is where work is asked for and watched;
/recordings, the catalogue of what that work produced; /config, the settings a
person is expected to change; and the three meeting shorthands that submit a job
for the common cases.

The front end is not here. It is a Next.js server in src/frontend, running as a
container of its own, and it reaches this API over the Docker network rather
than from the browser - so the page and the API are the same origin as far as
the browser is concerned, and none of these routes are called cross-origin.

CORS_ALLOW_ORIGINS is therefore empty by default, and is only for a deployment
that puts a browser on this API directly. It is worth being deliberate about:
this API has no authentication, so an origin listed there can start recordings,
read every transcript and change the settings.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gravai.api.routes import config, jobs, meetings, recordings
from gravai.config.logging_config import get_logger
from gravai.config.settings import get_settings
from gravai.jobs import runner, store

logger = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    # Jobs run in their own process group and outlive the API deliberately, so a
    # restart leaves the ones that are still going alone. The ones whose process
    # died with the last one would otherwise claim to be running for good.
    runner.reconcile()
    yield


app = FastAPI(
    title="GravAI",
    description=(
        "Records online meetings, cuts the mix into one track per participant, and "
        "transcribes both the participants and the meeting as a whole. Every piece of "
        "that is a job: submit one, poll it, and read what it recorded under /recordings."
    ),
    lifespan=lifespan,
)

_ALLOWED_ORIGINS = get_settings().cors_allow_origins
if _ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        # Named origins only. Never "*": with no authentication in front of this
        # API, a wildcard would let any page a user has open drive the service.
        allow_origins=_ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        # Nothing here is authenticated by a cookie or an Authorization header,
        # so the browser has no credentials to attach and asking for them would
        # only widen what a listed origin is trusted with.
        allow_credentials=False,
    )
    logger.info(f"Allowing browser requests from: {', '.join(_ALLOWED_ORIGINS)}")

app.include_router(meetings.router)
app.include_router(jobs.router)
app.include_router(recordings.router)
app.include_router(config.router)
