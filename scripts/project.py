"""Shared configuration and a fixed Hong Kong reporting cutoff."""

import csv
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("OBS_STATE", ROOT / "private-data")).resolve()
HK = ZoneInfo("Asia/Hong_Kong")
UTC = timezone.utc
START = datetime(2026, 10, 1, tzinfo=HK).astimezone(UTC)
# First calendar day in Hong Kong time; also the floor written into page ranges.
START_DAY = START.astimezone(HK).date().isoformat()
KEYWORDS = json.loads((ROOT / "scripts/keywords.json").read_text())
MEDIA = {
    "nyt": "Material_NYT",
    "guardian": "Material_Guardian",
    "bbc": "Material_BBC_English",
}
FORUM = ("hacker_news", "reddit", "bluesky")
COMPANY = dict(
    zip(
        KEYWORDS,
        ("openai", "anthropic", "google_deepmind", "deepseek", "bytedance_seed"),
    )
)


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cutoff_time():
    """The most recent 07:55 HKT boundary: where every collection window ends.

    The scheduled job starts at 08:00 HKT, so the window it collects closes at
    07:55 that morning. Keeping the cutoff five minutes behind the trigger also
    means a run that starts a few seconds early still resolves to today's
    boundary instead of silently falling back to yesterday's window.
    """
    if os.getenv("OBS_CUTOFF"):
        return instant(os.environ["OBS_CUTOFF"])
    now = datetime.now(HK)
    cutoff = datetime.combine(now.date(), time(7, 55), HK)
    if now < cutoff:
        cutoff -= timedelta(days=1)
    return cutoff.astimezone(UTC)


def read_rows(path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_rows(path, rows, fields):
    path.parent.mkdir(exist_ok=True, parents=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def forum_end(platform):
    """End of the contiguous stretch of successful forum windows.

    Coverage is anchored on the earliest successful window rather than on
    ``START``: when the project window is extended backwards the first month can
    still be missing, and anchoring on ``START`` would collapse the whole range
    to "no data" until the backfill finishes.
    """
    runs = [
        r
        for r in read_rows(STATE / "Collected Data/forum_collection_runs.csv")
        if r["platform"] == platform and r["status"] == "ok"
    ]
    if not runs:
        return START
    runs.sort(key=lambda r: r["window_start"])
    end = instant(runs[0]["window_start"])
    for r in runs:
        if instant(r["window_start"]) <= end:
            end = max(end, instant(r["window_end"]))
    return end
