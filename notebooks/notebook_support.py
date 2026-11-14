"""Source selection and presentation; all price weighting stays in visible SQL."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import certifi
import matplotlib.pyplot as plt
import seaborn as sns
from IPython.display import Markdown, display

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
        raise ValueError("END_MONTH must be an integer from 1 through 12, within the fixed 2024 window")
    if mode not in ("recorded", "live"):
        raise ValueError("DATA_MODE must be 'recorded' or 'live'")
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
    if mode == "recorded":
        replay = RecordedApi(Path(__file__).resolve().parent / "recordings").start()
        os.environ.update(
            EIA_API_KEY=FIXTURE_KEY,
            EIA_API_BASE_URL=replay.url,
            EIA_ALLOW_INSECURE_HTTP="1",
            EIA_MIN_INTERVAL_SECONDS="0",
        )
        dates = sorted({r["retrieved_at_utc"][:10] for r in replay.records.values()})
        provenance = "Recorded official responses · captured " + ", ".join(dates) + " UTC"
    else:
        os.environ.setdefault("EIA_API_KEY", "DEMO_KEY")
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


def prose(text: str) -> None:
    display(Markdown(text))


def theme() -> None:
    sns.set_theme(
        style="whitegrid",
        rc={
            "figure.dpi": 145,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "grid.color": "#E8EDE9",
            "text.color": "#243B2F",
            "axes.labelcolor": "#405047",
            "text.parse_math": False,
            "legend.frameon": False,
        },
    )


def chart(fig, title: str, subtitle: str, source: str) -> None:
    fig.text(0.08, 0.98, title, ha="left", va="top", fontsize=17, fontweight="semibold")
    fig.text(0.08, 0.91, subtitle, ha="left", va="top", fontsize=10, color="#58685F")
    fig.tight_layout(rect=(0.02, 0.10, 1, 0.83))
    fig.text(0.08, 0.03, source, fontsize=8, color="#6C7D73")
    plt.show()
