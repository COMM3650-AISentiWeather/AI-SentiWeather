"""Collect sources independently. Preserve checkpoints, even on a partial run."""

import concurrent.futures
import importlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from project import (
    STATE,
    MEDIA,
    FORUM,
    KEYWORDS,
    START,
    UTC,
    iso,
    instant,
    cutoff_time,
    forum_end,
    read_rows,
    write_rows,
)

KEYS = {
    "nyt": "NYT_API_KEY",
    "guardian": "GUARDIAN_API_KEY",
    "bbc": "MEDIACLOUD_API_KEY",
}
SCRIPT = Path(__file__).resolve()


def _selection():
    raw = os.environ.get("OBS_SOURCES", "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


WANTED = _selection()
# A backfill re-reads the whole project window instead of the trailing week.
BACKFILL = os.environ.get("OBS_BACKFILL", "").strip() not in {"", "0"}


def selected(name):
    return not WANTED or name in WANTED


def media(name):
    folder = STATE / MEDIA[name]
    module = importlib.import_module(name)
    key = os.environ.get(KEYS[name], "").strip()
    if not key:
        print(f"Missing {KEYS[name]}", flush=True)
        return False
    if (folder / f"{name}_state.json").exists() and not all(
        (folder / f"{name}_{suffix}.csv").is_file()
        for suffix in ("articles", "query_matches")
    ):
        print(f"{name}: imported state has no matching article/match files", flush=True)
        return False
    started = iso(datetime.now(UTC))
    getattr(module, f"run_{name}_collection")(key, str(folder))
    # The original collectors can finish normally after a failed query.
    state = json.loads((folder / f"{name}_state.json").read_text())
    ok = all(
        instant(state.get(c + "|" + term, iso(START))) >= cutoff_time()
        for c, terms in KEYWORDS.items()
        for term in terms
    )
    fresh = [
        r
        for r in read_rows(folder / f"{name}_collection_runs.csv")
        if r["run_at"] >= started
    ]
    attempted = {(r["company"], r["query_term"]): r["status"] for r in fresh}
    ok = ok and all(
        attempted.get((c, t)) == "ok" for c, terms in KEYWORDS.items() for t in terms
    )
    if name == "bbc":
        rss = [
            r for r in read_rows(folder / "bbc_rss_runs.csv") if r["run_at"] >= started
        ]
        feeds = {r["feed"]: r["status"] for r in rss}
        ok = ok and len(feeds) == 4 and all(s == "ok" for s in feeds.values())
    return ok


def forums(status):
    folder = STATE / "Collected Data"
    raw = folder / "forum_raw.csv"
    runs = folder / "forum_collection_runs.csv"
    rows = {(r["platform"], r["id"]): r for r in read_rows(raw)}
    if runs.exists() and not raw.exists():
        raise ValueError(
            "forum_collection_runs.csv is present but forum_raw.csv is missing"
        )
    history = read_rows(runs)
    fields = "id source_type platform content_type published_at collected_at url title body text_scope companies".split()
    for p in FORUM:
        if not selected(p):
            continue
        # A backfill re-walks the project from its first day so that newly
        # added months are filled in; an ordinary run only refreshes the tail.
        start = (
            START
            if BACKFILL
            else max(START, min(forum_end(p), cutoff_time() - timedelta(days=7)))
        )
        env = dict(
            os.environ,
            FORUM_WINDOW_START=iso(start),
            FORUM_WINDOW_END=iso(cutoff_time()),
        )
        path = STATE / "scratch" / (p + ".json")
        path.unlink(missing_ok=True)
        state = "failed"
        try:
            subprocess.run(
                [sys.executable, str(SCRIPT.with_name("forum_sample.py")), p],
                env=env,
                check=True,
                timeout=2400,
            )
            payload = json.loads(path.read_text())
            if (
                payload["platform"] != p
                or payload["window_start"] != iso(start)
                or payload["window_end"] != iso(cutoff_time())
            ):
                raise ValueError("Unexpected query window")
            state = payload["status"]
            for row in payload["rows"]:
                if not (
                    row.get("id")
                    and row.get("url")
                    and start <= instant(row["published_at"]) < cutoff_time()
                ):
                    continue
                k = p, row["id"]
                row["collected_at"] = rows.get(k, row)["collected_at"]
                rows[k] = row
            write_rows(raw, rows.values(), fields)
        except Exception as exc:
            print(f"{p}: {type(exc).__name__}", flush=True)
            state = "failed"
        history.append(
            dict(
                platform=p,
                window_start=iso(start),
                window_end=iso(cutoff_time()),
                run_at=iso(datetime.now(UTC)),
                status=state,
            )
        )
        write_rows(
            runs, history, "platform window_start window_end run_at status".split()
        )
        status[p] = state
    return all(state == "ok" for state in status.values())


def main():
    cutoff = cutoff_time()
    if cutoff <= START:
        raise ValueError("Reporting cutoff precedes the project")
    os.environ["OBS_CUTOFF"] = iso(cutoff)
    STATE.mkdir(parents=True, exist_ok=True)
    result = {p: "failed" for p in (*MEDIA, *FORUM) if selected(p)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(media, name): name for name in MEDIA if selected(name)}
        if any(selected(p) for p in FORUM):
            jobs[pool.submit(forums, result)] = "forums"
        for job in concurrent.futures.as_completed(jobs):
            name = jobs[job]
            try:
                ok = job.result()
                if name in MEDIA:
                    result[name] = "ok" if ok else "partial"
            except Exception as exc:
                print(f"{name} failed: {type(exc).__name__}", flush=True)
    (STATE / "latest.json").write_text(
        json.dumps(
            dict(cutoff=iso(cutoff), finished=iso(datetime.now(UTC)), sources=result),
            indent=2,
        )
    )
    print(result)
    return int(any(v != "ok" for v in result.values()))


if __name__ == "__main__":
    sys.exit(main())
