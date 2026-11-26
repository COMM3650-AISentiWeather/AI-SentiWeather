"""Inspect backfill checkpoints without altering existing watermarks.

Collectors retain a separate verified coverage start for each query and
automatically fill the earlier period. This entry point stays available for
existing workflow configurations, but it never resets saved progress.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from project import MEDIA, START, iso, read_rows

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("OBS_STATE", ROOT / "private-data")).resolve()
MARKER = STATE / "backfill.json"


def reset_source(name: str, start: str, dry_run: bool) -> tuple[int, int]:
    """Inspect existing watermarks; collectors now track backfill separately."""
    folder = STATE / MEDIA[name]
    path = folder / f"{name}_state.json"
    if not path.is_file():
        print(f"{name}: no state file, nothing to reset", flush=True)
        return 0, 0
    state = json.loads(path.read_text(encoding="utf-8"))
    print(f"{name}: preserving {len(state)} watermarks; backfill runs automatically", flush=True)
    return 0, len(state)


def forum_windows(start: str) -> int:
    """Report how much history the forum runs already cover."""
    runs = read_rows(STATE / "Collected Data" / "forum_collection_runs.csv")
    ok = [r for r in runs if r.get("status") == "ok"]
    print(f"forum runs on record: {len(runs)} ({len(ok)} ok)", flush=True)
    return len(ok)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    start = iso(START)
    applied = ""
    if MARKER.is_file():
        try:
            applied = json.loads(MARKER.read_text(encoding="utf-8")).get("start", "")
        except ValueError:
            applied = ""
    print(f"backfill start: {start} (previous marker: {applied or 'none'})")
    for name in MEDIA:
        reset_source(name, start, args.dry_run)
    forum_windows(start)

    print("Existing watermarks and markers were not modified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
