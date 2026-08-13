from contextlib import asynccontextmanager

from fastapi import FastAPI

from gravai.api.routes import meetings
from gravai.config.logging_config import get_logger
from gravai.recording.service import start_ws_server as service_record_start_ws_server
from gravai.recording.service import stop_ws_server as service_record_stop_ws_server

logger = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    service_record_start_ws_server()
    yield
    service_record_stop_ws_server()


app = FastAPI(
    description="record_bot",
    lifespan=lifespan,
)

app.include_router(meetings.router)
