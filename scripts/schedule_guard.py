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
        day = cutoff.astimezone(HK).date()
        for source in (*MEDIA, *FORUM):
            if data["status"].get(source) != "ok":
                return False
            expected = day - timedelta(days=1) if source == "bbc" else day
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
