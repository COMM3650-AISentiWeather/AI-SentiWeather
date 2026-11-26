"""Stratified forum queries with fresh comment trees from sampled Reddit posts."""

import datetime as dt
import os
import sys
import time
import urllib.parse

from forum_common import (
    HKT,
    SEARCH_TERMS,
    UTC,
    WINDOW_END,
    WINDOW_START,
    clean_hn_html,
    find_companies,
    get_json,
    iso_z,
    log,
    make_row,
    parse_iso,
    sample_evenly,
    clean_reddit_text,
    epoch_to_dt,
)
from forum_api import (
    BSKY_BASE,
    HN_BASE,
    REDDIT_FIELDS,
    ArcticClient,
    _bsky_post_row,
    _payload,
    _flatten_comment_tree,
    _reddit_post_row,
)

HN_STORIES_PER_TERM_MONTH = 15
HN_COMMENTS_PER_TERM_WEEK = 3
BSKY_PER_TERM_WEEK = 3
REDDIT_SUBS = ["artificial", "singularity", "ChatGPT", "ClaudeAI", "LocalLLaMA"]
REDDIT_PER_SUB_WINDOW = 12
REDDIT_WINDOW_DAYS = 14


# ---------------------------------------------------------------------------
# Window segmentation
# ---------------------------------------------------------------------------
def month_segments() -> list[tuple[dt.datetime, dt.datetime]]:
    segments = []
    cursor = WINDOW_START
    while cursor < WINDOW_END:
        hk = cursor.astimezone(HKT)
        if hk.month == 12:
            next_month = dt.datetime(hk.year + 1, 1, 1, tzinfo=HKT)
        else:
            next_month = dt.datetime(hk.year, hk.month + 1, 1, tzinfo=HKT)
        end = min(next_month.astimezone(UTC), WINDOW_END)
        segments.append((cursor, end))
        cursor = end
    return segments


def chunk_segments(days: int) -> list[tuple[dt.datetime, dt.datetime]]:
    segments = []
    cursor = WINDOW_START
    span = dt.timedelta(days=days)
    while cursor < WINDOW_END:
        end = min(cursor + span, WINDOW_END)
        segments.append((cursor, end))
        cursor = end
    return segments


# ---------------------------------------------------------------------------
# Hacker News
# ---------------------------------------------------------------------------
def collect_hacker_news_long() -> dict:
    run_at = iso_z(dt.datetime.now(UTC))
    rows: dict[str, dict] = {}
    errors: list[str] = []
    months = month_segments()
    weeks = chunk_segments(
        3.5
    )  # half-week segments: spreads the sample across each week
    stats = {"story_requests": 0, "comment_requests": 0}

    for term, _company in SEARCH_TERMS:
        # Stories: one request per calendar month, up to 1,000 hits.
        for seg_start, seg_end in months:
            params = {
                "query": term,
                "tags": "story",
                "hitsPerPage": 1000,
                "page": 0,
                "numericFilters": (
                    f"created_at_i>={int(seg_start.timestamp())},"
                    f"created_at_i<{int(seg_end.timestamp())}"
                ),
            }
            url = f"{HN_BASE}?{urllib.parse.urlencode(params)}"
            try:
                data, _ = get_json(
                    url,
                    min_interval=0.15,
                    label=f"HN long stories '{term}' {seg_start:%Y-%m}",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"stories '{term}' {seg_start:%Y-%m}: {exc}")
                continue
            stats["story_requests"] += 1
            matched = []
            for hit in data.get("hits") or []:
                object_id = str(hit.get("objectID") or "")
                if not object_id:
                    continue
                title = clean_hn_html(hit.get("title"))
                body = clean_hn_html(hit.get("story_text"))
                if not find_companies(f"{title}\n{body}"):
                    continue
                matched.append(
                    make_row(
                        f"hn_{object_id}",
                        "hacker_news",
                        "post",
                        parse_iso(hit["created_at"]),
                        f"https://news.ycombinator.com/item?id={object_id}",
                        title=title,
                        body=body,
                    )
                )
            span_days = max((seg_end - seg_start).total_seconds() / 86400.0, 1.0)
            story_cap = max(1, round(HN_STORIES_PER_TERM_MONTH * span_days / 30.0))
            for row in sample_evenly(matched, story_cap):
                rows[row["id"]] = row
        log(f"HN long '{term}': {len(rows)} rows so far")

    # Comments: one request per term x week (latest 100 of the week).
    for term, _company in SEARCH_TERMS:
        for seg_start, seg_end in weeks:
            params = {
                "query": term,
                "tags": "comment",
                "hitsPerPage": 100,
                "page": 0,
                "numericFilters": (
                    f"created_at_i>={int(seg_start.timestamp())},"
                    f"created_at_i<{int(seg_end.timestamp())}"
                ),
            }
            url = f"{HN_BASE}?{urllib.parse.urlencode(params)}"
            try:
                data, _ = get_json(
                    url,
                    min_interval=0.15,
                    label=f"HN long comments '{term}' {seg_start:%Y-%m-%d}",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"comments '{term}' {seg_start:%Y-%m-%d}: {exc}")
                continue
            stats["comment_requests"] += 1
            matched = []
            for hit in data.get("hits") or []:
                object_id = str(hit.get("objectID") or "")
                if not object_id:
                    continue
                body = clean_hn_html(hit.get("comment_text"))
                if not body:
                    continue
                story_title = clean_hn_html(hit.get("story_title"))
                if not find_companies(body, story_title):
                    continue
                matched.append(
                    make_row(
                        f"hn_{object_id}",
                        "hacker_news",
                        "comment",
                        parse_iso(hit["created_at"]),
                        f"https://news.ycombinator.com/item?id={object_id}",
                        title="",
                        body=body,
                        context=story_title,
                    )
                )
            for row in sample_evenly(matched, HN_COMMENTS_PER_TERM_WEEK):
                rows[row["id"]] = row

    log(f"HN long done: {len(rows)} rows, errors={len(errors)}, stats={stats}")
    return _payload(
        "hacker_news", rows, errors, run_at, {"window": "2026-10-01+", "stats": stats}
    )


# ---------------------------------------------------------------------------
# Bluesky
# ---------------------------------------------------------------------------
def collect_bluesky_long() -> dict:
    run_at = iso_z(dt.datetime.now(UTC))
    rows: dict[str, dict] = {}
    errors: list[str] = []
    weeks = chunk_segments(3.5)  # half-week segments
    stats = {"requests": 0}

    for term, _company in SEARCH_TERMS:
        lang = "en" if any(ch.isascii() and ch.isalpha() for ch in term) else None
        for seg_start, seg_end in weeks:
            params = {
                "q": term,
                "limit": 100,
                "sort": "latest",
                "since": iso_z(seg_start),
                "until": iso_z(seg_end),
            }
            if lang:
                params["lang"] = lang
            url = f"{BSKY_BASE}?{urllib.parse.urlencode(params)}"
            try:
                data, _ = get_json(
                    url,
                    min_interval=0.25,
                    label=f"bsky long '{term}' {seg_start:%Y-%m-%d}",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"bsky '{term}' {seg_start:%Y-%m-%d}: {exc}")
                continue
            stats["requests"] += 1
            matched = []
            for post in data.get("posts") or []:
                row = _bsky_post_row(post)
                if row:
                    matched.append(row)
            for row in sample_evenly(matched, BSKY_PER_TERM_WEEK):
                rows[row["id"]] = row
        log(f"Bluesky long '{term}': {len(rows)} rows so far")
        time.sleep(0.05)

    log(f"Bluesky long done: {len(rows)} rows, errors={len(errors)}, stats={stats}")
    return _payload(
        "bluesky", rows, errors, run_at, {"window": "2026-10-01+", "stats": stats}
    )


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------
def collect_reddit_long() -> dict:
    run_at = iso_z(dt.datetime.now(UTC))
    rows: dict[str, dict] = {}
    errors: list[str] = []
    client = ArcticClient()
    windows = chunk_segments(3.5)  # 3.5-day windows; cap is scaled to keep density
    stats = {"requests": 0, "reused_comments": 0}

    for subreddit in REDDIT_SUBS:
        for seg_start, seg_end in windows:
            params = {
                "subreddit": subreddit,
                "after": int(seg_start.timestamp()),
                "before": int(seg_end.timestamp()),
                "limit": 100,
                "sort": "desc",
                "fields": REDDIT_FIELDS,
            }
            try:
                posts = client.get("/api/posts/search", params)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"reddit r/{subreddit} {seg_start:%Y-%m-%d}: {exc}")
                log(f"Reddit long r/{subreddit} {seg_start:%Y-%m-%d} failed: {exc}")
                continue
            stats["requests"] += 1
            matched = []
            for post in posts:
                row = _reddit_post_row(post)
                if row:
                    matched.append(row)
            span_days = max((seg_end - seg_start).total_seconds() / 86400.0, 1.0)
            cap = max(2, round(REDDIT_PER_SUB_WINDOW * span_days / REDDIT_WINDOW_DAYS))
            for row in sample_evenly(matched, cap):
                rows[row["id"]] = row
        log(f"Reddit long r/{subreddit}: {len(rows)} rows so far")

    # Refresh a bounded set of comment trees for this window.
    for post in sample_evenly(list(rows.values()), 10):
        post_id = post["id"].removeprefix("reddit_")
        try:
            tree = client.get("/api/comments/tree", {"link_id": post_id, "limit": 200})
        except Exception as exc:
            errors.append(f"thread {post_id}: {exc}")
            continue
        selected = []
        for comment in _flatten_comment_tree(tree):
            body = clean_reddit_text(comment.get("body"))
            published = epoch_to_dt(comment.get("created_utc") or 0)
            cid = comment.get("id")
            if (
                cid
                and WINDOW_START <= published < WINDOW_END
                and find_companies(body, post["title"])
            ):
                selected.append(
                    make_row(
                        f"reddit_{cid}",
                        "reddit",
                        "comment",
                        published,
                        post["url"].rstrip("/") + f"/{cid}/",
                        body=body,
                        context=post["title"],
                    )
                )
        for row in sample_evenly(selected, 10):
            rows[row["id"]] = row

    log(f"Reddit long done: {len(rows)} rows, errors={len(errors)}, stats={stats}")
    return _payload(
        "reddit", rows, errors, run_at, {"window": "2026-10-01+", "stats": stats}
    )


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    platform = sys.argv[1].strip().lower()
    if platform in ("hacker_news", "hn"):
        collect_hacker_news_long()
    elif platform == "bluesky":
        collect_bluesky_long()
    elif platform == "reddit":
        collect_reddit_long()
    else:
        print(f"Unknown platform: {platform}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
