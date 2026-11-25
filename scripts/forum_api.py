"""Platform adapters for the forum sampler."""

import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse

from forum_common import (
    EndpointUnavailable,
    REDDIT_AI_SUBS,
    WINDOW_END,
    WINDOW_START,
    clean_plain_text,
    clean_reddit_text,
    epoch_to_dt,
    find_companies,
    get_json,
    iso_z,
    log,
    make_row,
    parse_iso,
    write_json,
)

from project import STATE

OUT_DIR = str(STATE / "scratch")

HN_BASE = "https://hn.algolia.com/api/v1/search_by_date"
ARCTIC_BASE = "https://arctic-shift.photon-reddit.com"
BSKY_BASE = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"

REDDIT_FIELDS = "id,title,selftext,created_utc,subreddit,num_comments,score,author"


# ---------------------------------------------------------------------------
# Reddit archive client
# ---------------------------------------------------------------------------
class ArcticClient:
    """Thin client with adaptive pacing for the free Arctic Shift archive API."""

    def __init__(self, min_interval: float = 3.5):
        self.min_interval = min_interval

    def get(self, path: str, params: dict, max_attempts: int = 2):
        url = f"{ARCTIC_BASE}{path}?{urllib.parse.urlencode(params)}"
        last_error = ""
        for _attempt in range(max_attempts):
            try:
                data, headers = get_json(
                    url,
                    min_interval=self.min_interval,
                    max_retries=2,
                    label=f"reddit {path.split('/')[-1]}",
                )
            except EndpointUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                log(f"Arctic Shift request failed ({last_error}); backing off")
                time.sleep(8)
                continue
            payload = data.get("data")
            if isinstance(payload, list):
                return payload
            error = str(data.get("error") or "")
            if not error and payload is None:
                raise ValueError("Archive response has no data list")
            last_error = error
            low = error.lower()
            if any(
                tok in low
                for tok in (
                    "rate",
                    "slow",
                    "too many",
                    "timeout",
                    "timed out",
                    "try again",
                )
            ):
                try:
                    pause = float(headers.get("X-RateLimit-Reset") or 10)
                except (TypeError, ValueError):
                    pause = 10.0
                pause = min(max(pause, 5.0) + 2.0, 180.0)
                self.min_interval = min(self.min_interval + 1.0, 9.0)
                log(
                    f"Arctic Shift says '{error}'; sleeping {pause:.0f}s (pacing={self.min_interval:.1f}s)"
                )
                time.sleep(pause)
                continue
            raise RuntimeError(f"Arctic Shift error: {error}")
        raise RuntimeError(f"Arctic Shift request failed repeatedly: {last_error}")


def _reddit_post_row(post: dict) -> dict | None:
    post_id = str(post.get("id") or "")
    subreddit = str(post.get("subreddit") or "")
    title = clean_reddit_text(post.get("title"))
    if not post_id or not title or title in ("[removed]", "[deleted]"):
        return None
    selftext = clean_reddit_text(post.get("selftext"))
    if selftext in ("[removed]", "[deleted]"):
        selftext = ""
    context = "ai" if subreddit.lower() in {s.lower() for s in REDDIT_AI_SUBS} else ""
    if not find_companies(f"{title}\n{selftext}", context):
        return None
    permalink = post.get("permalink") or f"/r/{subreddit}/comments/{post_id}/"
    url = f"https://www.reddit.com{permalink}"
    published = epoch_to_dt(post.get("created_utc") or 0)
    return make_row(
        f"reddit_{post_id}",
        "reddit",
        "post",
        published,
        url,
        title=title,
        body=selftext,
        context=context,
    )


def _flatten_comment_tree(nodes) -> list[dict]:
    flat: list[dict] = []

    def walk(children):
        for node in children or []:
            if not isinstance(node, dict):
                continue
            if node.get("kind") == "t1":
                data = node.get("data") or {}
                flat.append(data)
                replies = data.get("replies")
                if isinstance(replies, dict):
                    walk((replies.get("data") or {}).get("children"))

    walk(nodes)
    return flat


def _bsky_post_row(post: dict) -> dict | None:
    record = post.get("record") or {}
    text = clean_plain_text(record.get("text"))
    if not text:
        return None
    embed = record.get("embed") or post.get("embed") or {}
    context = ""
    if isinstance(embed, dict) and embed.get("$type") == "app.bsky.embed.external":
        external = embed.get("external") or {}
        context = " ".join(
            str(external.get(key) or "") for key in ("title", "description")
        )
    if not find_companies(text, context):
        return None
    try:
        published = parse_iso(record.get("createdAt") or post.get("indexedAt"))
    except Exception:  # noqa: BLE001
        return None
    if not (WINDOW_START <= published <= WINDOW_END):
        return None
    uri = post.get("uri") or ""
    author = post.get("author") or {}
    did = author.get("did") or ""
    handle = author.get("handle") or did
    rkey = uri.rsplit("/", 1)[-1]
    if not did or not rkey:
        return None
    content_type = "comment" if "reply" in record else "post"
    return make_row(
        f"bsky_{did}_{rkey}",
        "bluesky",
        content_type,
        published,
        f"https://bsky.app/profile/{handle}/post/{rkey}",
        title="",
        body=text,
        context=context,
    )


def _payload(
    platform: str, rows: dict[str, dict], errors: list[str], run_at: str, extra: dict
) -> dict:
    ordered = sorted(rows.values(), key=lambda r: r["published_at"])
    status = "ok"
    if errors and not ordered:
        status = "failed"
    elif errors:
        status = "partial"
    payload = {
        "platform": platform,
        "window_start": iso_z(WINDOW_START),
        "window_end": iso_z(WINDOW_END),
        "run_at": run_at,
        "status": status,
        "row_count": len(ordered),
        "errors": errors,
        "extra": extra,
        "rows": ordered,
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{platform}.json")
    write_json(path, payload)
    log(f"Wrote {path} ({len(ordered)} rows, status={status})")
    return payload
