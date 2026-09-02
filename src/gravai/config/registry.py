"""Single source of truth for which provider handles what.

Adding a provider means adding one entry here. Everything that used to branch
per provider - recorder construction, slicing, and URL detection - reads from
this table instead, so a registered provider is registered everywhere at once.
"""

from collections.abc import Callable
from dataclasses import dataclass

from gravai.models.common import ParticipantData, RecordingType, Session
from gravai.recording.providers.meet.provider import MeetMeetingRecorder
from gravai.recording.providers.provider_base import MeetingRecorder
from gravai.recording.providers.teams.provider import TeamsMeetingRecorder
from gravai.slicing.slice import Slicer


class UnsupportedMeetingURL(ValueError):
    """Raised when no registered provider matches a meeting URL."""


class UnsupportedProvider(NotImplementedError):
    """Raised for a provider that is known but has no implementation yet.

    Subclasses NotImplementedError so it keeps reporting as 501 rather than as
    a bad request - the URL was understood, the support just is not there.
    """


@dataclass(frozen=True)
class ProviderSpec:
    """Everything that makes one meeting provider work end to end.

    A provider may be registered with only url_markers, which is how a planned
    provider is declared: its URLs are recognised, and asking for its recorder
    or slicer reports it as unimplemented instead of unknown.
    """

    recording_type: RecordingType
    url_markers: tuple[str, ...]
    recorder: type[MeetingRecorder] | None = None
    slice_session: Callable[[str, bool], Session[ParticipantData]] | None = None

    @property
    def implemented(self) -> bool:
        return self.recorder is not None and self.slice_session is not None


_PROVIDERS: dict[RecordingType, ProviderSpec] = {
    RecordingType.TEAMS: ProviderSpec(
        recording_type=RecordingType.TEAMS,
        url_markers=("teams.live.com", "teams.microsoft.com"),
        recorder=TeamsMeetingRecorder,
        slice_session=Slicer.slice_and_create_session_teams_audio_track,
    ),
    RecordingType.MEET: ProviderSpec(
        recording_type=RecordingType.MEET,
        url_markers=("meet.google.com",),
        recorder=MeetMeetingRecorder,
        slice_session=Slicer.slice_and_create_session_teams_audio_track,
    ),
}


def detect_recording_type(meeting_url: str) -> RecordingType:
    """Resolves a meeting URL to its provider, registered or merely planned."""
    for spec in _PROVIDERS.values():
        if any(marker in meeting_url for marker in spec.url_markers):
            return spec.recording_type

    supported = ", ".join(
        marker for spec in _PROVIDERS.values() for marker in spec.url_markers
    )
    raise UnsupportedMeetingURL(
        f"No provider matches {meeting_url!r}. Supported meeting URLs contain: {supported}."
    )


def get_provider(recording_type: RecordingType) -> ProviderSpec:
    """Returns a fully implemented provider, or explains why there isn't one."""
    spec = _PROVIDERS.get(recording_type)
    if spec is None:
        raise UnsupportedProvider(
            f"No provider registered for {recording_type.value}. "
            f"Registered: {', '.join(t.value for t in _PROVIDERS)}."
        )
    if not spec.implemented:
        raise UnsupportedProvider(f"{recording_type.value} recording is not implemented yet.")
    return spec
