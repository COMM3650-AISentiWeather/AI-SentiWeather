"""Avoid duplicate scheduled collection once all sources have been published."""

import json
import os
from datetime import timedelta
from pathlib import Path

from project import FORUM, HK, MEDIA, cutoff_time, instant


def snapshot_complete(path, cutoff):
    """Require today's cutoff, all source statuses, and their verified windows."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if instant(data["cutoff"]) < cutoff:
            return False
        local_cutoff = cutoff.astimezone(HK)
        day = local_cutoff.date()
        for source in (*MEDIA, *FORUM):
            if data["status"].get(source) != "ok":
                return False
            # Midnight is an exclusive boundary: every source covers through
            # the previous date. BBC date-only records also require a full day.
            midnight = not (local_cutoff.hour or local_cutoff.minute or
                            local_cutoff.second or local_cutoff.microsecond)
            expected = day - timedelta(days=1) if source == "bbc" or midnight else day
            if data["ranges"][source][1] < expected.isoformat():
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError, IndexError, AttributeError):
        return False


def main():
    needed = os.getenv("EVENT_NAME") != "schedule" or not snapshot_complete(
        Path("docs/dashboard.json"), cutoff_time()
    )
    print("Collection needed:", needed)
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        print(f"needed={'true' if needed else 'false'}", file=output)


if __name__ == "__main__":
    main()
