"""Puts every test's database and .env somewhere of its own.

Both are resolved through the settings, and the settings are cached for the
lifetime of the process - so a test that changes either has to clear that cache,
and the store keeps its own note of which database files it has already built a
schema in. Getting this wrong is not a visible failure: the tests pass while
writing into each other's database.
"""

import os

import pytest

# Fixed before gravai.api.app is imported anywhere, because the API reads the
# allowed origins once, when it builds the app - a fixture would be too late,
# and the value would otherwise be whatever the developer happens to have in
# their own .env.
os.environ["CORS_ALLOW_ORIGINS"] = "http://localhost:3000"

from gravai.config.settings import get_settings
from gravai.jobs import store


@pytest.fixture
def database(tmp_path, monkeypatch):
    """A database of this test's own, returned as its path."""
    path = tmp_path / "gravai.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    get_settings.cache_clear()
    store._initialized_paths.clear()
    yield str(path)
    get_settings.cache_clear()
    store._initialized_paths.clear()


@pytest.fixture
def env_path(tmp_path, monkeypatch):
    """An .env of this test's own, seeded with what Settings requires."""
    path = tmp_path / ".env"
    path.write_text(
        "# A comment that has to survive being written through.\n"
        "WHISPER_HOST=whisper\n"
        "WHISPER_PORT=8080\n"
        "\n"
        "GOOGLE_ACCOUNT_EMAIL=bot@example.com\n"
        "GOOGLE_ACCOUNT_PASSWORD=hunter2\n"
    )
    monkeypatch.setenv("GRAVAI_ENV_FILE", str(path))
    # Settings reads the process environment before the file, so a value left in
    # os.environ by another test - or by the developer's own shell - would be
    # what the test sees changing rather than the file.
    for name in ("WHISPER_PORT", "WHISPER_LANGUAGE", "GOOGLE_ACCOUNT_PASSWORD", "VNC_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WHISPER_HOST", "whisper")
    monkeypatch.setenv("WHISPER_PORT", "8080")
    monkeypatch.setenv("GOOGLE_ACCOUNT_PASSWORD", "hunter2")
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()
