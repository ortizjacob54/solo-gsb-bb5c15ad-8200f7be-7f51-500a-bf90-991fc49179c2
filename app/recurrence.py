"""Core expansion of RFC 5545 recurrence rules into DST-aware occurrences.

Strategy: the RRULE is expanded with a *naive* dtstart, so python-dateutil
performs pure wall-clock arithmetic (e.g. "every day at 09:00 local"). Each
resulting naive local time is then localized into the requested IANA zone:

- non-existent local times (spring-forward gap) are skipped;
- ambiguous local times (fall-back fold) are emitted twice, once per fold;
- everything else is attached the zone's offset for that instant.

Results are filtered by the UTC query window and sorted by UTC instant.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil import tz
from dateutil.rrule import rruleset, rrulestr

from . import config
from .errors import DomainError
from .models import Occurrence, OccurrenceRequest

# The widest IANA UTC offsets are UTC-12:00 .. UTC+14:00. Padding the query
# window by 24 h on both sides guarantees every local occurrence whose UTC
# instant falls inside the window is generated, folds included.
_WINDOW_PAD = timedelta(hours=24)

_TZ_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_+\-/\.]{0,63}$")
_UNTIL_UTC_RE = re.compile(r"UNTIL=(\d{8}T\d{6})Z")

# RFC 5545 lines that must be supplied through dedicated request fields
# instead of being embedded in the rrule string.
_REJECTED_LINES = {
    "DTSTART": "the 'start' field",
    "EXDATE": "the 'exdates' field",
    "RDATE": "the 'rdates' field",
    "EXRULE": "the 'rrule' and 'exdates' fields (EXRULE is not supported)",
}


def load_timezone(name: str):
    """Return a dateutil tzinfo for a strictly validated IANA time zone name."""
    if not _TZ_NAME_RE.match(name) or ".." in name:
        raise DomainError(
            "invalid_timezone",
            f"{name!r} is not a valid IANA time zone name",
            {"timezone": name},
        )
    try:
        ZoneInfo(name)  # strict IANA validation
    except (ZoneInfoNotFoundError, ValueError):
        raise DomainError(
            "invalid_timezone",
            f"Unknown IANA time zone: {name!r}",
            {"timezone": name},
        )
    tzinfo = tz.gettz(name)
    if tzinfo is None:  # defensive: ZoneInfo already validated the name
        raise DomainError(
            "invalid_timezone",
            f"Unknown IANA time zone: {name!r}",
            {"timezone": name},
        )
    return tzinfo


def normalize_rrule(raw: str, tzinfo) -> str:
    """Reduce user input to a single bare RRULE value.

    - unfolds RFC 5545 continuation lines and accepts an optional 'RRULE:' prefix;
    - rejects embedded DTSTART/EXDATE/RDATE/EXRULE lines (dedicated request
      fields exist for those, so silently dropping them would corrupt results);
    - rewrites a UTC ('Z'-suffixed) UNTIL to the equivalent local wall time,
      because occurrences are generated as naive local times.
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]  # RFC 5545 line folding
        else:
            lines.append(line)

    values: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        name = line.split(":", 1)[0].split(";", 1)[0].strip().upper()
        if name in _REJECTED_LINES:
            raise DomainError(
                "invalid_rrule",
                f"Do not embed {name} in 'rrule'; use {_REJECTED_LINES[name]} instead",
                {"rrule": raw},
            )
        if name == "RRULE":
            values.append(line.split(":", 1)[1].strip())
        elif line.upper().startswith("FREQ="):
            values.append(line)
        else:
            raise DomainError(
                "invalid_rrule",
                f"Unsupported content in 'rrule': {line!r}; expected a single RRULE "
                "such as 'FREQ=DAILY;INTERVAL=1'",
                {"rrule": raw},
            )

    if len(values) != 1:
        raise DomainError(
            "invalid_rrule",
            f"Exactly one RRULE is required, got {len(values)}",
            {"rrule": raw},
        )

    value = values[0].upper()

    def _until_to_local(match: re.Match) -> str:
        until_utc = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(
            tzinfo=timezone.utc
        )
        return "UNTIL=" + until_utc.astimezone(tzinfo).strftime("%Y%m%dT%H%M%S")

    return _UNTIL_UTC_RE.sub(_until_to_local, value)


def build_rruleset(req: OccurrenceRequest, tzinfo) -> rruleset:
    """Combine the RRULE with EXDATE/RDATE into a single rruleset."""
    value = normalize_rrule(req.rrule, tzinfo)
    try:
        rule = rrulestr(value, dtstart=req.start)
    except (ValueError, TypeError, KeyError) as exc:
        raise DomainError(
            "invalid_rrule", f"Invalid RRULE: {exc}", {"rrule": req.rrule}
        )
    rules = rruleset()
    rules.rrule(rule)
    for exdate in req.exdates:
        rules.exdate(exdate)
    for rdate in req.rdates:
        rules.rdate(rdate)
    return rules


def format_utc(dt: datetime) -> str:
    """ISO 8601 in UTC with a trailing 'Z'."""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def format_offset(offset: timedelta) -> str:
    """'+HH:MM' / '-HH:MM' (seconds appended for historical sub-minute zones)."""
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    minutes, seconds = divmod(abs(total), 60)
    hours, minutes = divmod(minutes, 60)
    text = f"{sign}{hours:02d}:{minutes:02d}"
    return f"{text}:{seconds:02d}" if seconds else text


def _require_naive(value: datetime, field: str, code: str) -> None:
    if value.tzinfo is not None:
        raise DomainError(
            code,
            f"'{field}' must be a naive local datetime without UTC offset; "
            "the 'timezone' field defines the zone",
            {"field": field, "value": value.isoformat()},
        )


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None:
        raise DomainError(
            "invalid_window",
            f"'{field}' must be timezone-aware, e.g. '2026-01-01T00:00:00Z'",
            {"field": field, "value": value.isoformat()},
        )


def expand(
    req: OccurrenceRequest,
) -> tuple[list[Occurrence], int, datetime, datetime]:
    """Expand the request into occurrences.

    Returns (occurrences, skipped_nonexistent, window_start_utc, window_end_utc).
    """
    tzinfo = load_timezone(req.timezone)
    _require_naive(req.start, "start", "invalid_start")
    for exdate in req.exdates:
        _require_naive(exdate, "exdates", "invalid_exdates")
    for rdate in req.rdates:
        _require_naive(rdate, "rdates", "invalid_rdates")
    _require_aware(req.window_start, "window_start")
    _require_aware(req.window_end, "window_end")

    window_start = req.window_start.astimezone(timezone.utc)
    window_end = req.window_end.astimezone(timezone.utc)
    if window_start >= window_end:
        raise DomainError(
            "invalid_window",
            "'window_start' must be before 'window_end'",
            {
                "window_start": req.window_start.isoformat(),
                "window_end": req.window_end.isoformat(),
            },
        )

    limit = req.limit if req.limit is not None else config.DEFAULT_LIMIT
    if limit > config.MAX_LIMIT:
        raise DomainError(
            "invalid_limit",
            f"'limit' may not exceed {config.MAX_LIMIT}",
            {"limit": limit, "max_limit": config.MAX_LIMIT},
        )

    rules = build_rruleset(req, tzinfo)

    after_naive = (window_start - _WINDOW_PAD).replace(tzinfo=None)
    before_naive = (window_end + _WINDOW_PAD).replace(tzinfo=None)

    # (utc instant, naive local time, utc offset, fold)
    hits: list[tuple[datetime, datetime, timedelta, int]] = []
    skipped_nonexistent = 0
    scanned = 0
    for naive_dt in rules:  # rruleset iterates in ascending local order
        scanned += 1
        if scanned > config.SCAN_CAP:
            raise DomainError(
                "expansion_too_large",
                "The rule generates too many candidates; move 'start' closer to "
                "the query window or use a coarser frequency",
                {"scan_cap": config.SCAN_CAP},
            )
        if naive_dt < after_naive:
            continue
        if naive_dt > before_naive:
            break
        if not tz.datetime_exists(naive_dt, tzinfo):
            # Non-existent local time (spring-forward gap): skipped by design.
            skipped_nonexistent += 1
            continue
        if tz.datetime_ambiguous(naive_dt, tzinfo):
            # Repeated local time (fall-back fold): emit both folds.
            candidates = (
                naive_dt.replace(tzinfo=tzinfo, fold=0),
                naive_dt.replace(tzinfo=tzinfo, fold=1),
            )
        else:
            candidates = (naive_dt.replace(tzinfo=tzinfo),)
        for aware in candidates:
            utc_dt = aware.astimezone(timezone.utc)
            if window_start <= utc_dt < window_end:
                hits.append((utc_dt, naive_dt, aware.utcoffset(), aware.fold))
                if len(hits) > limit:
                    raise DomainError(
                        "too_many_occurrences",
                        f"The window contains more than {limit} occurrences; "
                        "narrow the window or raise 'limit'",
                        {"limit": limit},
                    )

    hits.sort(key=lambda hit: (hit[0], hit[3], hit[1]))
    occurrences = [
        Occurrence(
            local_time=local.isoformat(),
            utc_time=format_utc(utc_dt),
            utc_offset=format_offset(offset),
            fold=fold,
        )
        for utc_dt, local, offset, fold in hits
    ]
    return occurrences, skipped_nonexistent, window_start, window_end
