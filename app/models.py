"""Pydantic request/response schemas for the expansion API."""

from datetime import datetime

from pydantic import BaseModel, Field


class OccurrenceRequest(BaseModel):
    """Payload for POST /occurrences."""

    timezone: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="IANA time zone name, e.g. 'America/New_York'",
        examples=["America/New_York"],
    )
    start: datetime = Field(
        ...,
        description="Naive local wall-clock start of the recurrence (no UTC offset); "
        "interpreted in 'timezone'",
        examples=["2026-01-01T09:00:00"],
    )
    rrule: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="RFC 5545 RRULE, e.g. 'FREQ=DAILY;INTERVAL=1' (an 'RRULE:' prefix "
        "is accepted; DTSTART/EXDATE/RDATE lines are not)",
        examples=["FREQ=DAILY;INTERVAL=1"],
    )
    window_start: datetime = Field(
        ...,
        description="Start of the query window, timezone-aware, inclusive "
        "(e.g. '2026-01-01T00:00:00Z')",
        examples=["2026-01-01T00:00:00Z"],
    )
    window_end: datetime = Field(
        ...,
        description="End of the query window, timezone-aware, exclusive",
        examples=["2026-02-01T00:00:00Z"],
    )
    exdates: list[datetime] = Field(
        default_factory=list,
        max_length=1000,
        description="Naive local datetimes to exclude from the recurrence set",
    )
    rdates: list[datetime] = Field(
        default_factory=list,
        max_length=1000,
        description="Naive local datetimes to add to the recurrence set",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of occurrences to return "
        "(defaults to OCCURRENCES_DEFAULT_LIMIT)",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "timezone": "America/New_York",
                    "start": "2026-01-01T09:00:00",
                    "rrule": "FREQ=WEEKLY;BYDAY=MO,WE,FR",
                    "window_start": "2026-01-01T00:00:00Z",
                    "window_end": "2026-02-01T00:00:00Z",
                    "exdates": ["2026-01-19T09:00:00"],
                    "rdates": ["2026-01-20T09:00:00"],
                }
            ]
        }
    }


class Occurrence(BaseModel):
    """A single expanded occurrence."""

    local_time: str = Field(..., description="Naive local wall-clock time, ISO 8601")
    utc_time: str = Field(..., description="The same instant in UTC, ISO 8601 with 'Z'")
    utc_offset: str = Field(..., description="UTC offset of the local time, '+HH:MM'/'-HH:MM'")
    fold: int = Field(
        ...,
        description="0 or 1; disambiguates repeated local times during the "
        "DST fall-back transition",
    )


class Window(BaseModel):
    start: str
    end: str


class OccurrenceResponse(BaseModel):
    timezone: str
    window: Window = Field(..., description="The query window normalized to UTC")
    count: int
    skipped_nonexistent: int = Field(
        ...,
        description="How many generated local times were skipped because they do not "
        "exist (DST spring-forward gap)",
    )
    occurrences: list[Occurrence]
