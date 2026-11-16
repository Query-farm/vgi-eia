"""Source transport configuration; all analytical work stays in visible SQL."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import certifi

from recorded_api import FIXTURE_KEY, RecordedApi

MANAGED = (
    "EIA_API_KEY",
    "EIA_API_BASE_URL",
    "EIA_ALLOW_INSECURE_HTTP",
    "EIA_TIMEOUT_SECONDS",
    "EIA_MAX_ATTEMPTS",
    "EIA_RETRY_BUDGET_SECONDS",
    "EIA_MIN_INTERVAL_SECONDS",
    "EIA_MAX_RESPONSE_BYTES",
    "SSL_CERT_FILE",
)


def configure(end_month: int, mode: str):
    if type(end_month) is not int or not 1 <= end_month <= 12:
        raise ValueError(
            "END_MONTH must be an integer from 1 through 12, within the fixed 2024 window"
        )
    if mode not in ("recorded", "live"):
        raise ValueError("DATA_MODE must be 'recorded' or 'live'")
    return _source(mode)


def _source(mode: str):
    """Choose one explicit transport before the worker starts in a fresh kernel."""
    previous = {key: os.environ.get(key) for key in MANAGED}
    for key in MANAGED:
        if key not in ("EIA_API_KEY", "SSL_CERT_FILE"):
            os.environ.pop(key, None)
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    os.environ.update(
        EIA_TIMEOUT_SECONDS="60",
        EIA_MAX_ATTEMPTS="1",
        EIA_RETRY_BUDGET_SECONDS="70",
        EIA_MIN_INTERVAL_SECONDS="0.6",
        EIA_MAX_RESPONSE_BYTES=str(16 * 1024 * 1024),
    )
    replay = None
    run_at = datetime.now(UTC).isoformat()
    if mode in ("recorded", "synthetic"):
        directory = "recordings" if mode == "recorded" else "marker_fixtures"
        replay = RecordedApi(Path(__file__).resolve().parent / directory).start()
        os.environ.update(
            EIA_API_KEY=FIXTURE_KEY,
            EIA_API_BASE_URL=replay.url,
            EIA_ALLOW_INSECURE_HTTP="1",
            EIA_MIN_INTERVAL_SECONDS="0",
        )
        if mode == "recorded":
            dates = sorted({r["retrieved_at_utc"][:10] for r in replay.records.values()})
            provenance = "Recorded official responses · captured " + ", ".join(dates) + " UTC"
        else:
            provenance = "Authored synthetic six-month fixture · not EIA observations"
    else:
        os.environ["EIA_API_KEY"] = "DEMO_KEY"
        provenance = "Live official responses · requested " + run_at[:19] + " UTC"
    return replay, previous, run_at, provenance


def cleanup(replay, previous):
    if replay:
        replay.stop()
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def configure_markers(minimum: int):
    """Select only authored local marker fixtures; never use a network API key."""
    if type(minimum) is not int or not 1 <= minimum <= 6:
        raise ValueError("MIN_OBSERVED_MONTHS must be an integer from 1 through 6")
    return _source("synthetic")
