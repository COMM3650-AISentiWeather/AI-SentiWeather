"""Reset source watermarks so a backfill re-reads the project from its first day.

Collectors resume from a per-(company, term) watermark in ``*_state.json``.
When the project window is extended backwards (2026-11-01 -> 2026-10-01) an
existing watermark would keep the old queries from reaching the new month, so
this script drops every watermark back to ``START`` once.

It records what it applied in ``backfill.json``. A second run with the same
start therefore does nothing, which keeps a backfill that times out and is
re-dispatched resumable instead of restarting from scratch every time.

Run it from the repository root with ``OBS_STATE`` pointing at the checkpoints:

    python scripts/backfill_reset.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from project import MEDIA, START, instant, iso, read_rows

ROOT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("OBS_STATE", ROOT / "private-data")).resolve()
MARKER = STATE / "backfill.json"


def reset_source(name: str, start: str, dry_run: bool) -> tuple[int, int]:
    """Set every watermark in one source's state file to ``start``."""
    folder = STATE / MEDIA[name]
    path = folder / f"{name}_state.json"
    if not path.is_file():
        print(f"{name}: no state file, nothing to reset", flush=True)
        return 0, 0
    state = json.loads(path.read_text(encoding="utf-8"))
    changed = sum(1 for value in state.values() if value != start)
    if dry_run:
        print(f"{name}: would pin {changed} of {len(state)} watermarks", flush=True)
        return changed, len(state)
    if changed:
        path.write_text(
            json.dumps({key: start for key in state}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    print(f"{name}: pinned {changed} of {len(state)} watermarks to {start}", flush=True)
    return changed, len(state)


def forum_windows(start: str) -> int:
    """Report how much history the forum runs already cover."""
    runs = read_rows(STATE / "Collected Data" / "forum_collection_runs.csv")
    ok = [r for r in runs if r.get("status") == "ok"]
    print(f"forum runs on record: {len(runs)} ({len(ok)} ok)", flush=True)
    return len(ok)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Apply even when the marker already records this start",
    )
    parser.add_argument(
        "--to",
        default="",
        help="ISO instant to pin every watermark to (default: the project start)",
    )
    args = parser.parse_args()

    start = iso(instant(args.to)) if args.to.strip() else iso(START)
    applied = ""
    if MARKER.is_file():
        try:
            applied = json.loads(MARKER.read_text(encoding="utf-8")).get("start", "")
        except ValueError:
            applied = ""
    if applied == start and not (args.dry_run or args.force):
        print(f"backfill from {start} already applied; leaving watermarks alone")
        return 0

    print(f"backfill start: {start} (previous marker: {applied or 'none'})")
    for name in MEDIA:
        reset_source(name, start, args.dry_run)
    forum_windows(start)

    if not args.dry_run:
        MARKER.write_text(
            json.dumps({"start": start, "note": "one-off watermark reset"}, indent=2),
            encoding="utf-8",
        )
        print(f"marker written: {MARKER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
