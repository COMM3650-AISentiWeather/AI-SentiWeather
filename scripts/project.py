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
LEGACY_START = datetime(2026, 11, 1, tzinfo=HK).astimezone(UTC)
KEYWORDS = json.loads((ROOT / "scripts/keywords.json").read_text())
# Keep the forum vocabulary intact. Media searches omit Chinese terms and
# redundant DeepSeek version queries; other product names remain distinct.
MEDIA_KEYWORDS = {
    company: [term for term in terms if term.isascii() and not (
        company == "DeepSeek" and term in
        {"deepseek-r1", "deepseek-v3", "deepseek-v4", "deepseek harness"}
    )]
    for company, terms in KEYWORDS.items()
}
# Media Cloud supports OR searches. One query per company substantially reduces
# its two-requests-per-minute bottleneck while retaining company attribution.
BBC_QUERIES = {
    company: [' OR '.join('"' + term + '"' for term in terms)]
    for company, terms in MEDIA_KEYWORDS.items()
}
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


def media_queries(platform):
    return BBC_QUERIES if platform == "bbc" else MEDIA_KEYWORDS


def coverage_starts(folder, platform):
    path = folder / f"{platform}_coverage_starts.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_coverage_starts(folder, platform, starts):
    path = folder / f"{platform}_coverage_starts.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(starts, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def media_window(platform, state, starts):
    """Return the common verified interval, preserving pre-migration coverage."""
    windows = []
    for company, terms in media_queries(platform).items():
        for term in terms:
            key = company + "|" + term
            if key in state:
                windows.append((instant(starts.get(key, iso(LEGACY_START))), instant(state[key])))
            elif platform == "bbc":
                # The old BBC checkpoints used individual terms. A grouped
                # query does not erase the already verified historical range.
                legacy = [state.get(company + "|" + t) for t in MEDIA_KEYWORDS[company]]
                if not all(legacy):
                    return None
                windows.append((LEGACY_START, min(map(instant, legacy))))
            else:
                return None
    start = max(x[0] for x in windows)
    end = min(x[1] for x in windows)
    return (start, end) if start < end else None


def cutoff_time():
    """The latest midnight HKT, an exclusive end for the entire previous day."""
    if os.getenv("OBS_CUTOFF"):
        return instant(os.environ["OBS_CUTOFF"])
    now = datetime.now(HK)
    cutoff = datetime.combine(now.date(), time.min, HK)
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
    end = START
    runs = sorted(
        read_rows(STATE / "Collected Data/forum_collection_runs.csv"),
        key=lambda r: r["window_start"],
    )
    for r in runs:
        if (
            r["platform"] == platform
            and r["status"] == "ok"
            and instant(r["window_start"]) <= end
        ):
            end = max(end, instant(r["window_end"]))
    return end


def forum_window(platform):
    """Return the latest contiguous verified forum interval, even during backfill."""
    intervals = sorted(
        (instant(r["window_start"]), instant(r["window_end"]))
        for r in read_rows(STATE / "Collected Data/forum_collection_runs.csv")
        if r["platform"] == platform and r["status"] == "ok"
    )
    merged = []
    for start, end in intervals:
        if end <= START or end <= start:
            continue
        start = max(start, START)
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged[-1] if merged else None
