# -*- coding: utf-8 -*-
"""COMM3650 final project — shared config + helpers for forum data collection.

Platforms: Hacker News, Reddit, Bluesky.
Output rows follow the raw_items schema in COMM3650_Data_Schema.xlsx:
id, source_type, platform, content_type, published_at, collected_at,
url, title, body, text_scope
"""

import datetime as dt
import html
import json
import os
import re
import ssl
import certifi
import time
import urllib.error
import urllib.parse
import urllib.request

UTC = dt.timezone.utc
HKT = dt.timezone(dt.timedelta(hours=8))


def iso_z(value: dt.datetime) -> str:
    """'2026-11-24T01:20:00Z' (seconds precision, UTC)."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# Collection window.
#   * default: 7 Hong Kong calendar days ending at the run time (today is
#     included, so the last window is partial);
#   * override with FORUM_WINDOW_START / FORUM_WINDOW_END env vars (used by the
#     daily incremental update job).
# ---------------------------------------------------------------------------
NOW = dt.datetime.now(UTC).replace(microsecond=0)
_ENV_START = os.environ.get("FORUM_WINDOW_START")
_ENV_END = os.environ.get("FORUM_WINDOW_END")
if _ENV_START and _ENV_END:
    WINDOW_START = parse_iso(_ENV_START)
    WINDOW_END = min(parse_iso(_ENV_END), NOW)
else:
    TODAY_HKT = NOW.astimezone(HKT).date()
    WINDOW_START = dt.datetime.combine(
        TODAY_HKT - dt.timedelta(days=6), dt.time(0, 0), tzinfo=HKT
    ).astimezone(UTC)
    WINDOW_END = NOW

# Split the window at Hong Kong midnights so per-day sampling works for any
# window length (7 days for the backfill, ~1 day for the daily update).
DAYS: list[tuple[dt.datetime, dt.datetime]] = []
_cursor = WINDOW_START
while _cursor < WINDOW_END:
    _next_midnight_hkt = dt.datetime.combine(
        _cursor.astimezone(HKT).date() + dt.timedelta(days=1),
        dt.time(0, 0),
        tzinfo=HKT,
    )
    _segment_end = min(_next_midnight_hkt.astimezone(UTC), WINDOW_END)
    DAYS.append((_cursor, _segment_end))
    _cursor = _segment_end

USER_AGENT = "COMM3650-final-research/1.0 (CUHK student project; contact: group member)"

# ---------------------------------------------------------------------------
# Search terms mapped to the five target AI companies.  English aliases are
# matched case-insensitively; ambiguous aliases (Claude / Gemini / Seed /
# ByteDance) require an AI context, per the group's shared rules.
# ---------------------------------------------------------------------------
SEARCH_TERMS: list[tuple[str, str]] = [
    ("OpenAI", "openai"),
    ("ChatGPT", "openai"),
    ("Anthropic", "anthropic"),
    ("Claude", "anthropic"),
    ("DeepMind", "google_deepmind"),
    ("Gemini", "google_deepmind"),
    ("DeepSeek", "deepseek"),
    ("深度求索", "deepseek"),
    ("ByteDance", "bytedance_seed"),
    ("ByteDance Seed", "bytedance_seed"),
    ("Seedance", "bytedance_seed"),
    ("Seedream", "bytedance_seed"),
    ("SeedDream", "bytedance_seed"),
    ("Doubao", "bytedance_seed"),
    ("豆包", "bytedance_seed"),
    ("字节跳动", "bytedance_seed"),
]

# Subreddits treated as AI-discussion spaces (used to collect all recent posts
# and as AI context for ambiguous aliases such as Claude / Gemini).
REDDIT_AI_SUBS = [
    "artificial",
    "singularity",
    "OpenAI",
    "ChatGPT",
    "ClaudeAI",
    "LocalLLaMA",
    "DeepSeek",
]
# General technology subreddits: keyword search only (too many posts to scan).
REDDIT_TECH_SUBS = []  # r/technology keyword search times out on the free archive API
REDDIT_TECH_TERMS = [
    "OpenAI",
    "ChatGPT",
    "Anthropic",
    "Gemini",
    "DeepSeek",
    "ByteDance",
]

# ---------------------------------------------------------------------------
# Alias rules
# ---------------------------------------------------------------------------
_OPENAI_RE = re.compile(r"\bopenai\b|\bchatgpt\b", re.I)
_ANTHROPIC_RE = re.compile(r"\banthropic\b", re.I)
_CLAUDE_RE = re.compile(r"\bclaude\b", re.I)
_DEEPMIND_RE = re.compile(r"\bdeepmind\b", re.I)
_GEMINI_RE = re.compile(r"\bgemini\b", re.I)
_DEEPSEEK_RE = re.compile(r"\bdeepseek\b|深度求索", re.I)
_BYTEDANCE_RE = re.compile(r"\bbytedance\b|字节跳动", re.I)
_BYTEDANCE_SEED_RE = re.compile(
    r"\bbytedance\s+seed\b|\bseedance\b|\bseedream\b|\bseeddream\b|\bseed\s+dream\b|\bdoubao\b|豆包",
    re.I,
)
_SEED_RE = re.compile(r"\bseed\b", re.I)

_AI_CONTEXT_RE = re.compile(
    r"\b(?:ai|a\.i\.|artificial intelligence|agi|llm|llms|large language models?|"
    r"language models?|foundation models?|chatbots?|chat bots?|machine learning|"
    r"deep learning|neural|gpt|chatgpt|openai|anthropic|deepmind|deepseek|"
    r"models?|agents?|prompts?|inference|tokens?|superintelligence|generative|"
    r"assistants?|copilot|huggingface|hugging face|transformers?|benchmarks?|"
    r"datasets?|alignment|compute|nvidia|google|microsoft|meta|apple|xai|x\.ai)\b",
    re.I,
)


def find_companies(primary: str, context: str = "") -> list[str]:
    """Return target company keys mentioned in ``primary``.

    ``context`` (thread title / subreddit) is only used to decide whether an
    ambiguous alias (Claude, Gemini, Seed, ByteDance) has an AI context; aliases
    themselves must appear in ``primary`` so that unrelated replies are skipped.
    """
    text = primary or ""
    if not text.strip():
        return []
    ctx = f"{text}\n{context or ''}"
    found: list[str] = []

    if _OPENAI_RE.search(text):
        found.append("openai")

    if _ANTHROPIC_RE.search(text) or (
        _CLAUDE_RE.search(text) and _AI_CONTEXT_RE.search(ctx)
    ):
        found.append("anthropic")

    if _DEEPMIND_RE.search(text) or (
        _GEMINI_RE.search(text) and _AI_CONTEXT_RE.search(ctx)
    ):
        found.append("google_deepmind")

    if _DEEPSEEK_RE.search(text):
        found.append("deepseek")

    if _BYTEDANCE_SEED_RE.search(text):
        found.append("bytedance_seed")
    elif _BYTEDANCE_RE.search(text) and _SEED_RE.search(ctx):
        found.append("bytedance_seed")

    return found


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_P_RE = re.compile(r"</?p\s*/?>", re.I)


def clean_hn_html(text: str | None) -> str:
    """Convert Hacker News comment/story HTML into readable plain text."""
    if not text:
        return ""
    out = html.unescape(text)
    out = _BR_RE.sub("\n", out)
    out = _P_RE.sub("\n\n", out)
    out = _TAG_RE.sub("", out)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def clean_reddit_text(text: str | None) -> str:
    """Reddit archive stores raw markdown with HTML entities; keep markdown."""
    if not text:
        return ""
    out = html.unescape(text)
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    return out.strip()


def clean_plain_text(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def epoch_to_dt(value: float | int) -> dt.datetime:
    return dt.datetime.fromtimestamp(float(value), tz=UTC)


COLLECTED_AT = iso_z(NOW)


# ---------------------------------------------------------------------------
# Row factory
# ---------------------------------------------------------------------------
def make_row(
    item_id: str,
    platform: str,
    content_type: str,
    published_at: dt.datetime,
    url: str,
    title: str = "",
    body: str = "",
    text_scope: str | None = None,
    collected_at: str = COLLECTED_AT,
    context: str = "",
) -> dict:
    title = (title or "").strip()
    body = (body or "").strip()
    if text_scope is None:
        if body:
            text_scope = "full"
        elif title:
            text_scope = "title_only"
        else:
            text_scope = "unavailable"
    return {
        "id": item_id,
        "source_type": "forum",
        "companies": "|".join(find_companies(title + " " + body, context)),
        "platform": platform,
        "content_type": content_type,
        "published_at": iso_z(published_at),
        "collected_at": collected_at,
        "url": url,
        "title": title,
        "body": body,
        "text_scope": text_scope,
    }


# ---------------------------------------------------------------------------
# HTTP helpers with adaptive back-off
# ---------------------------------------------------------------------------
_LAST_CALL = 0.0
_SSL = ssl.create_default_context(cafile=certifi.where())
_BLOCKED = {}


class EndpointUnavailable(RuntimeError):
    pass


def get_json(
    url: str,
    *,
    headers: dict | None = None,
    min_interval: float = 0.0,
    timeout: float = 60.0,
    max_retries: int = 6,
    label: str = "",
) -> tuple[dict, dict]:
    """GET JSON with pacing and retries (handles 429 / rate-limit 422 / 5xx)."""
    global _LAST_CALL
    host = urllib.parse.urlsplit(url).netloc
    if host in _BLOCKED:
        raise EndpointUnavailable(_BLOCKED[host])
    last_err: Exception | None = None
    for attempt in range(max_retries):
        wait = min_interval - (time.monotonic() - _LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL = time.monotonic()
        req = urllib.request.Request(
            url,
            headers=headers or {"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
                return json.load(resp), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403) or (
                exc.code == 429 and attempt == max_retries - 1
            ):
                _BLOCKED[host] = f"{host} returned HTTP {exc.code}"
                raise EndpointUnavailable(_BLOCKED[host]) from exc
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            headers_l = dict(exc.headers or {})
            reset = headers_l.get("X-RateLimit-Reset") or headers_l.get("Retry-After")
            low = body.lower()
            rate_limited = (
                exc.code == 429
                or exc.code == 422
                and any(
                    token in low
                    for token in (
                        "rate",
                        "too many",
                        "slow down",
                        "timeout",
                        "try again",
                    )
                )
            )
            if rate_limited:
                try:
                    pause = float(reset)
                except (TypeError, ValueError):
                    pause = 15.0
                pause = min(max(pause, 5.0) + 2.0, 180.0)
                last_err = RuntimeError(f"{label} HTTP {exc.code}: {body[:160]}")
                print(
                    f"[retry] {label} rate-limited; sleeping {pause:.0f}s", flush=True
                )
                time.sleep(pause)
                continue
            if exc.code in (500, 502, 503, 504):
                last_err = RuntimeError(f"{label} HTTP {exc.code}")
                time.sleep(3.0 * (attempt + 1))
                continue
            raise RuntimeError(f"{label} HTTP {exc.code}: {body}")
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            json.JSONDecodeError,
        ) as exc:
            last_err = exc
            print(f"[retry] {label} network error: {exc}", flush=True)
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"{label}: failed after {max_retries} attempts ({last_err})")


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
def sample_evenly(items: list, n: int) -> list:
    """Evenly spaced subsample of a time-sorted list (keeps whole window)."""
    if n <= 0 or not items:
        return []
    if len(items) <= n:
        return list(items)
    if n == 1:
        return [items[len(items) // 2]]
    idx = sorted({round(i * (len(items) - 1) / (n - 1)) for i in range(n)})
    return [items[i] for i in idx]


def write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    import os

    os.replace(tmp, path)


def log(message: str) -> None:
    print(f"[{dt.datetime.now(HKT).strftime('%H:%M:%S')}] {message}", flush=True)
