"""End-to-end tests for the recurring time rule expansion API."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def make_payload(**overrides) -> dict:
    payload = {
        "timezone": "UTC",
        "start": "2026-01-01T09:00:00",
        "rrule": "FREQ=DAILY;COUNT=3",
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-02-01T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def post(payload: dict):
    return client.post("/occurrences", json=payload)


# --------------------------------------------------------------------------
# Meta endpoints
# --------------------------------------------------------------------------

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root():
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["docs"] == "/docs"


# --------------------------------------------------------------------------
# Basic expansion
# --------------------------------------------------------------------------

def test_basic_daily_expansion():
    resp = post(make_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert body["skipped_nonexistent"] == 0
    assert [o["local_time"] for o in body["occurrences"]] == [
        "2026-01-01T09:00:00",
        "2026-01-02T09:00:00",
        "2026-01-03T09:00:00",
    ]
    for occ in body["occurrences"]:
        assert occ["utc_offset"] == "+00:00"
        assert occ["fold"] == 0
        assert occ["utc_time"].endswith("Z")
    assert body["window"] == {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-02-01T00:00:00Z",
    }


def test_rrule_prefix_and_lowercase_accepted():
    resp = post(make_payload(rrule="rrule:freq=daily;count=2"))
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_utc_offset_preserved_half_quarter_zone():
    resp = post(
        make_payload(timezone="Asia/Kathmandu", rrule="FREQ=DAILY;COUNT=1")
    )
    assert resp.status_code == 200
    occ = resp.json()["occurrences"][0]
    assert occ["local_time"] == "2026-01-01T09:00:00"
    assert occ["utc_offset"] == "+05:45"
    assert occ["utc_time"] == "2026-01-01T03:15:00Z"


def test_window_start_inclusive_end_exclusive():
    resp = post(
        make_payload(
            rrule="FREQ=DAILY;COUNT=2",
            window_start="2026-01-01T09:00:00Z",
            window_end="2026-01-02T09:00:00Z",
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"] for o in resp.json()["occurrences"]] == [
        "2026-01-01T09:00:00"
    ]


def test_window_offsets_normalized_to_utc():
    # Same instant expressed with a non-UTC offset.
    resp = post(
        make_payload(
            rrule="FREQ=DAILY;COUNT=1",
            window_start="2026-01-01T02:00:00+02:00",
            window_end="2026-02-01T05:00:00+05:00",
        )
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-02-01T00:00:00Z",
    }
    assert body["count"] == 1


# --------------------------------------------------------------------------
# DST behaviour
# --------------------------------------------------------------------------

def test_spring_forward_nonexistent_time_is_skipped():
    # 2026-03-08 02:30 does not exist in America/New_York (clocks jump 02:00 -> 03:00).
    resp = post(
        make_payload(
            timezone="America/New_York",
            start="2026-03-07T02:30:00",
            rrule="FREQ=DAILY;COUNT=3",
            window_start="2026-03-06T00:00:00Z",
            window_end="2026-03-11T00:00:00Z",
        )
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped_nonexistent"] == 1
    assert [(o["local_time"], o["utc_offset"]) for o in body["occurrences"]] == [
        ("2026-03-07T02:30:00", "-05:00"),  # EST
        ("2026-03-09T02:30:00", "-04:00"),  # EDT
    ]


def test_fall_back_repeated_time_returns_both_folds():
    # 2026-11-01 01:30 happens twice in America/New_York (clocks fall back 02:00 -> 01:00).
    resp = post(
        make_payload(
            timezone="America/New_York",
            start="2026-10-31T01:30:00",
            rrule="FREQ=DAILY;COUNT=3",
            window_start="2026-10-31T00:00:00Z",
            window_end="2026-11-03T12:00:00Z",
        )
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 4
    assert body["skipped_nonexistent"] == 0
    assert [
        (o["local_time"], o["utc_offset"], o["fold"], o["utc_time"])
        for o in body["occurrences"]
    ] == [
        ("2026-10-31T01:30:00", "-04:00", 0, "2026-10-31T05:30:00Z"),
        ("2026-11-01T01:30:00", "-04:00", 0, "2026-11-01T05:30:00Z"),  # fold 0 (EDT)
        ("2026-11-01T01:30:00", "-05:00", 1, "2026-11-01T06:30:00Z"),  # fold 1 (EST)
        ("2026-11-02T01:30:00", "-05:00", 0, "2026-11-02T06:30:00Z"),
    ]
    # Strictly sorted by UTC instant.
    utc_times = [o["utc_time"] for o in body["occurrences"]]
    assert utc_times == sorted(utc_times)


def test_offset_changes_across_dst_boundary():
    resp = post(
        make_payload(
            timezone="Europe/Berlin",
            start="2026-03-28T10:00:00",
            rrule="FREQ=DAILY;COUNT=3",
            window_start="2026-03-27T00:00:00Z",
            window_end="2026-04-01T00:00:00Z",
        )
    )
    assert resp.status_code == 200
    # Berlin switches CET (+01:00) -> CEST (+02:00) on 2026-03-29.
    assert [o["utc_offset"] for o in resp.json()["occurrences"]] == [
        "+01:00",
        "+02:00",
        "+02:00",
    ]


# --------------------------------------------------------------------------
# Month-end rules
# --------------------------------------------------------------------------

def test_month_end_bymonthday_31_skips_short_months():
    resp = post(
        make_payload(
            start="2026-01-31T09:00:00",
            rrule="FREQ=MONTHLY;BYMONTHDAY=31",
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-07-01T00:00:00Z",
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-01-31",
        "2026-03-31",
        "2026-05-31",
    ]


def test_month_end_bymonthday_minus_one():
    resp = post(
        make_payload(
            start="2026-01-31T09:00:00",
            rrule="FREQ=MONTHLY;BYMONTHDAY=-1",
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-05-01T00:00:00Z",
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
        "2026-04-30",
    ]


def test_month_end_last_friday():
    resp = post(
        make_payload(
            start="2026-01-30T17:00:00",
            rrule="FREQ=MONTHLY;BYDAY=-1FR",
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-04-01T00:00:00Z",
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-01-30",
        "2026-02-27",
        "2026-03-27",
    ]


# --------------------------------------------------------------------------
# EXDATE / RDATE
# --------------------------------------------------------------------------

def test_exdate_excludes_occurrence():
    resp = post(
        make_payload(
            rrule="FREQ=DAILY;COUNT=4",
            exdates=["2026-01-02T09:00:00"],
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-01-01",
        "2026-01-03",
        "2026-01-04",
    ]


def test_rdate_adds_occurrence():
    resp = post(
        make_payload(
            rrule="FREQ=DAILY;COUNT=2",
            rdates=["2026-01-10T09:00:00"],
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-10",
    ]


def test_exdate_wins_over_rdate():
    resp = post(
        make_payload(
            rrule="FREQ=DAILY;COUNT=2",
            rdates=["2026-01-10T09:00:00"],
            exdates=["2026-01-10T09:00:00"],
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-01-01",
        "2026-01-02",
    ]


def test_rdate_duplicate_of_rule_occurrence_is_not_duplicated():
    resp = post(
        make_payload(
            rrule="FREQ=DAILY;COUNT=2",
            rdates=["2026-01-02T09:00:00"],
        )
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


# --------------------------------------------------------------------------
# UNTIL
# --------------------------------------------------------------------------

def test_until_with_utc_suffix():
    resp = post(
        make_payload(
            rrule="RRULE:FREQ=DAILY;UNTIL=20260103T140000Z",
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]


def test_until_with_utc_suffix_converted_to_local_zone():
    # 2026-06-02T13:00:00Z == 2026-06-02 09:00 EDT, so the June 2 occurrence
    # at 09:00 local is still inside the UNTIL bound.
    resp = post(
        make_payload(
            timezone="America/New_York",
            start="2026-06-01T09:00:00",
            rrule="FREQ=DAILY;UNTIL=20260602T130000Z",
            window_start="2026-05-31T00:00:00Z",
            window_end="2026-06-10T00:00:00Z",
        )
    )
    assert resp.status_code == 200
    assert [o["local_time"][:10] for o in resp.json()["occurrences"]] == [
        "2026-06-01",
        "2026-06-02",
    ]


# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

def test_limit_exceeded_returns_structured_error():
    resp = post(make_payload(rrule="FREQ=DAILY;COUNT=5", limit=2))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "too_many_occurrences"


def test_limit_allows_exact_fit():
    resp = post(make_payload(rrule="FREQ=DAILY;COUNT=3", limit=3))
    assert resp.status_code == 200
    assert resp.json()["count"] == 3


# --------------------------------------------------------------------------
# Structured errors
# --------------------------------------------------------------------------

def test_invalid_timezone():
    resp = post(make_payload(timezone="Not/AZone"))
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "invalid_timezone"
    assert error["details"]["timezone"] == "Not/AZone"


def test_invalid_timezone_path_traversal():
    resp = post(make_payload(timezone="../../etc/passwd"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_timezone"


def test_invalid_rrule():
    resp = post(make_payload(rrule="FREQ=NOPE"))
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "invalid_rrule"
    assert "message" in error


def test_rrule_with_embedded_exdate_rejected():
    resp = post(make_payload(rrule="RRULE:FREQ=DAILY;COUNT=3\nEXDATE:20260102T090000"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_rrule"


def test_rrule_with_embedded_dtstart_rejected():
    resp = post(
        make_payload(rrule="DTSTART:20260101T090000\nRRULE:FREQ=DAILY;COUNT=3")
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_rrule"


def test_empty_rrule_rejected():
    resp = post(make_payload(rrule="   "))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_rrule"


def test_invalid_window_end_before_start():
    resp = post(
        make_payload(
            window_start="2026-02-01T00:00:00Z",
            window_end="2026-01-01T00:00:00Z",
        )
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_window"


def test_invalid_window_equal_bounds():
    resp = post(
        make_payload(
            window_start="2026-01-01T00:00:00Z",
            window_end="2026-01-01T00:00:00Z",
        )
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_window"


def test_naive_window_rejected():
    resp = post(make_payload(window_start="2026-01-01T00:00:00"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_window"


def test_aware_start_rejected():
    resp = post(make_payload(start="2026-01-01T09:00:00+00:00"))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_start"


def test_aware_exdate_rejected():
    resp = post(make_payload(exdates=["2026-01-02T09:00:00Z"]))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_exdates"


def test_malformed_payload_returns_structured_validation_error():
    resp = post(make_payload(start="not-a-datetime"))
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["errors"]


def test_missing_required_field():
    payload = make_payload()
    del payload["rrule"]
    resp = post(payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
