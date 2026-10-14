"""Core column adapters preserving the SQLite catalogue's existing TEXT format."""

from collections.abc import Callable
from datetime import datetime

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

_DATETIME = TypeAdapter(datetime)


class ISODateTime(TypeDecorator[datetime]):
    """Keep timezone offsets and read ISO timestamps from older recordings."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        return (
            _DATETIME.validate_python(value).isoformat() if value is not None else None
        )

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


class DTOJSON[ValueT](TypeDecorator[ValueT]):
    """Validate JSON columns as DTOs while retaining malformed-JSON fallbacks."""

    impl = Text
    cache_ok = True

    def __init__(
        self, adapter: TypeAdapter[ValueT], default_factory: Callable[[], ValueT]
    ) -> None:
        super().__init__()
        self.adapter = adapter
        self.default_factory = default_factory

    def process_bind_param(self, value: object, dialect: Dialect) -> str | None:
        if value is None:
            return None
        validated = self.adapter.validate_python(value)
        return self.adapter.dump_json(validated, exclude_unset=True).decode()

    def process_result_value(self, value: str | None, dialect: Dialect) -> ValueT:
        if not value:
            return self.default_factory()
        try:
            return self.adapter.validate_json(value)
        except ValidationError as exc:
            if all(error["type"] == "json_invalid" for error in exc.errors()):
                return self.default_factory()
            raise
