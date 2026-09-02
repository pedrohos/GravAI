"""Changing settings from the configuration page.

The three things that go wrong here are all quiet. A write that reformats the
file loses the comments that explain every setting in it - which in this
project's .env is where the reasoning lives. A form posted back whole overwrites
a password with the placeholder it was displayed as. And a value Settings will
refuse gets into the file, after which the service does not start at all, and
the page that wrote it is no longer running to fix it.
"""

import pytest

from gravai.config import env_file as config_env
from gravai.config.settings import get_settings


def test_a_change_reaches_both_the_process_and_the_file(env_path):
    result = config_env.write_config({"WHISPER_PORT": "9099"})

    assert get_settings().WHISPER_PORT == "9099"
    assert "WHISPER_PORT=9099" in env_path.read_text()
    assert {field["name"]: field["value"] for field in result["fields"]}["WHISPER_PORT"] == "9099"


def test_the_comments_and_the_untouched_keys_survive(env_path):
    config_env.write_config({"WHISPER_PORT": "9099"})

    written = env_path.read_text()
    assert "# A comment that has to survive being written through." in written
    assert "WHISPER_HOST=whisper" in written
    assert "GOOGLE_ACCOUNT_EMAIL=bot@example.com" in written


def test_a_setting_that_was_not_in_the_file_is_appended(env_path):
    config_env.write_config({"WHISPER_LANGUAGE": "pt"})

    assert "WHISPER_LANGUAGE=pt" in env_path.read_text()
    assert get_settings().WHISPER_LANGUAGE == "pt"


def test_a_secret_reads_back_as_a_placeholder(env_path):
    password = next(
        field for field in config_env.read_config()["fields"]
        if field["name"] == "GOOGLE_ACCOUNT_PASSWORD"
    )

    assert password["value"] == config_env.SECRET_PLACEHOLDER
    assert password["secret"] is True
    assert password["is_set"] is True


def test_posting_a_secret_back_unchanged_leaves_it_alone(env_path):
    """A page that renders the form and posts it whole sends the placeholder
    back; it must not become the password."""
    config_env.write_config({"GOOGLE_ACCOUNT_PASSWORD": config_env.SECRET_PLACEHOLDER})

    assert get_settings().GOOGLE_ACCOUNT_PASSWORD.get_secret_value() == "hunter2"
    assert "GOOGLE_ACCOUNT_PASSWORD=hunter2" in env_path.read_text()


def test_a_secret_can_still_be_changed_and_cleared(env_path):
    config_env.write_config({"GOOGLE_ACCOUNT_PASSWORD": "correct-horse"})
    assert get_settings().GOOGLE_ACCOUNT_PASSWORD.get_secret_value() == "correct-horse"

    config_env.write_config({"GOOGLE_ACCOUNT_PASSWORD": ""})
    assert get_settings().GOOGLE_ACCOUNT_PASSWORD.get_secret_value() == ""


def test_a_setting_outside_the_allowlist_is_refused(env_path):
    """The allowlist is what stops a request repointing the injected JavaScript."""
    with pytest.raises(config_env.ConfigError):
        config_env.write_config({"VAD_OBSERVER_MEET_JS_PATH": "/etc/passwd"})

    assert "VAD_OBSERVER_MEET_JS_PATH" not in env_path.read_text()


def test_a_value_settings_would_reject_never_reaches_the_file(env_path):
    before = env_path.read_text()

    with pytest.raises(config_env.ConfigError):
        config_env.write_config({"VNC_PORT": "not-a-port"})

    assert env_path.read_text() == before
    assert get_settings().VNC_PORT == 5900
