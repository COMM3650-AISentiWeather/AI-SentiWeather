"""Build a public, static report from private checkpoints and article/post CSVs."""

import os
import collections
import json
import re
import shutil
from datetime import datetime, timedelta
from urllib.parse import urlsplit
from pathlib import Path
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from project import (
    ROOT,
    STATE,
    START,
    HK,
    MEDIA,
    FORUM,
    COMPANY,
    KEYWORDS,
    UTC,
    instant,
    iso,
    cutoff_time,
    forum_end,
    forum_window,
    media_window,
    coverage_starts,
    read_rows,
)
from forum_common import find_companies
from bbc import canonical_path

STOP = set(
    """the and for not that this was with you your they their them there then were had has have are out who what when from will would shall should could about how into more than over can been but his her she our just some any its may say says said which these those also get use using used like very all new one two did does after why before other only even much most both it's it's""".split()
)
WORD = re.compile(r"(?i)\b[a-z][a-z'-]{2,24}\b")


def public_url(value):
    try:
        u = urlsplit(value)
        return value if u.scheme in {"http", "https"} and u.hostname else ""
    except ValueError:
        return ""


def load_media(p):
    folder = STATE / MEDIA[p]
    matches = collections.defaultdict(set)
    terms = collections.defaultdict(set)
    for m in read_rows(folder / f"{p}_query_matches.csv"):
        if m["company"] in COMPANY:
            matches[m["id"]].add(m["company"])
            terms[m["id"]].add(m["query_term"])
    unique = {}
    for r in read_rows(folder / f"{p}_articles.csv"):
        key = r["id"]
        tags = {c for c in COMPANY if r.get(c) == "1"} | matches[key]
        if p == "bbc":
            key = canonical_path(r["url"])
            if not key or r.get("language", "").lower() != "en":
                continue
        if key not in unique:
            unique[key] = {
                **r,
                "tags": set(),
                "terms": set(),
                "unknown": set(COMPANY),
                "channels": set(),
            }
        old = unique[key]
        old["tags"].update(tags)
        old["terms"].update(terms[r["id"]])
        old["unknown"].difference_update(
            c for c in COMPANY if r.get(c) in {"0", "1"} or c in tags
        )
        old["channels"].update(filter(None, r.get("discovery_sources", "").split("|")))
        for col in ("published_at", "published_date", "rss_feed_date", "title", "body"):
            if r.get(col) and not old.get(col):
                old[col] = r[col]
    return unique.values()


def record(r, analyzer, cutoff):
    p = r["platform"]
    when = r.get("published_at") or ""
    date = r.get("published_date") or ""
    precision = "timestamp"
    if when:
        stamp = instant(when)
        if not START <= stamp < cutoff:
            return
        date = stamp.astimezone(HK).date().isoformat()
    elif date:
        precision = "date_only"
        if date >= cutoff.astimezone(HK).date().isoformat():
            return
    elif r.get("rss_feed_date"):
        stamp = instant(r["rss_feed_date"])
        if not START <= stamp < cutoff:
            return
        when, date, precision = (
            iso(stamp),
            stamp.astimezone(HK).date().isoformat(),
            "rss_time",
        )
    else:
        return
    if date < START.astimezone(HK).date().isoformat():
        return
    title, body = r["title"], r["body"]
    if p in MEDIA:
        tags = [COMPANY[c] for c in KEYWORDS if c in r["tags"]]
        unknown = [COMPANY[c] for c in KEYWORDS if c in r["unknown"]]
        content = title
    else:
        tags = list(filter(None, r.get("companies", "").split("|"))) or find_companies(
            title + " " + body,
            (
                "ai"
                if p == "reddit"
                and re.search(
                    r"/r/(?:chatgpt|claudeai|localllama|artificial|singularity)/",
                    r["url"],
                    re.I,
                )
                else ""
            ),
        )
        unknown = []
        content = (title + " " + body).strip()
    # Non-Latin-dominant material remains countable, with score unavailable.
    latin = sum(ch.isalpha() and ch.isascii() for ch in content)
    scored = latin >= 2 and latin >= 0.65 * sum(ch.isalpha() for ch in content)
    score = analyzer.polarity_scores(content)["compound"] if scored else None
    attitude = (
        None
        if score is None
        else (
            "positive" if score >= 0.05 else "negative" if score <= -0.05 else "neutral"
        )
    )
    words = collections.Counter(
        w.lower() for w in WORD.findall(content) if w.lower() not in STOP
    )
    return dict(
        id=r["id"],
        platform=p,
        type=r["content_type"],
        date=date,
        time=when,
        precision=precision,
        companies=tags,
        unknown=unknown,
        score=score,
        attitude=attitude,
        title=title,
        url=public_url(r["url"]),
        excerpt=" ".join(content.split())[:200],
        terms=words.most_common(35),
        scope="headline" if p in MEDIA else r["text_scope"],
        channels=sorted(r.get("channels", set())),
        query_terms=sorted(r.get("terms", set())),
        collected_at=r.get("collected_at", ""),
        source_type="media" if p in MEDIA else "forum",
        text_scope=r.get("text_scope", ""),
        date_boundary_note=r.get("date_boundary_note", ""),
    )


def build():
    latest_path = STATE / "latest.json"
    latest = json.loads(latest_path.read_text()) if latest_path.exists() else {}
    cutoff = instant(latest["cutoff"]) if latest else cutoff_time()
    analyzer = SentimentIntensityAnalyzer()
    rows, ranges = [], {}
    for p in (*MEDIA, *FORUM):
        if p in MEDIA:
            source = load_media(p)
            folder = STATE / MEDIA[p]
            path = folder / f"{p}_state.json"
            state = json.loads(path.read_text()) if path.exists() else {}
            window = media_window(p, state, coverage_starts(folder, p))
        else:
            source = [
                r
                for r in read_rows(STATE / "Collected Data/forum_raw.csv")
                if r["platform"] == p
            ]
            window = forum_window(p)
        for r in source:
            item = record(r, analyzer, cutoff)
            if item:
                rows.append(item)
        if window:
            start, end = window
            day = min(end, cutoff).astimezone(HK)
            # BBC date-only records on the cutoff day are deferred until tomorrow.
            if p == "bbc" or day.hour == day.minute == day.second == 0:
                day -= timedelta(days=1)
            first = start.astimezone(HK).date()
            if day.date() >= first:
                ranges[p] = [first.isoformat(), day.date().isoformat()]
    dist = Path(os.environ.get("OBS_OUTPUT", ROOT / "dist"))
    dist.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "site", dist, dirs_exist_ok=True)
    (dist / "plotly.min.js").write_text(get_plotlyjs())
    layout = go.Layout(
        template="none",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Arial, sans-serif", color="#153e38", size=14),
        margin=dict(l=55, r=22, t=25, b=60),
        legend=dict(orientation="h", y=-0.22),
        colorway=["#126c63", "#a34f6d", "#426bb3"],
        hovermode="closest",
    )
    (dist / "dashboard.json").write_text(
        json.dumps(
            dict(
                version=1,
                cutoff=iso(cutoff),
                updated=latest.get("finished"),
                status=latest.get("sources", {}),
                ranges=ranges,
                rows=rows,
                layout=layout.to_plotly_json(),
                traces={
                    "trend": go.Scatter(
                        mode="lines+markers", connectgaps=False
                    ).to_plotly_json(),
                    "average": go.Bar(
                        orientation="h", textposition="auto"
                    ).to_plotly_json(),
                    "distribution": go.Bar().to_plotly_json(),
                    "share": go.Pie(
                        hole=0.45, sort=False, textinfo="percent"
                    ).to_plotly_json(),
                },
            ),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    print(f"Built {len(rows)} records; selectable sources: {', '.join(ranges)}")


if __name__ == "__main__":
    build()
