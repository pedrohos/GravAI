from fastapi import FastAPI

from gravai.api.routes import meetings
from gravai.config.logging_config import get_logger

logger = get_logger("api")


app = FastAPI(
    description="record_bot",
)

app.include_router(meetings.router)
