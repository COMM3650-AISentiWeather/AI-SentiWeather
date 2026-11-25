import csv
import hashlib
import html
import json
import os
import re
import xml.etree.ElementTree as ET

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import mediacloud.api
import requests

# ============================================================
# 1. 关键词与来源
# ============================================================

from project import KEYWORDS, cutoff_time

# 已在测试中成功读取的 BBC 英文 RSS。
FEEDS = {
    "BBC News": "https://feeds.bbci.co.uk/news/rss.xml",
    "Technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "World": "https://feeds.bbci.co.uk/news/world/rss.xml",
}

# 测试中找到的 Media Cloud BBC 英文来源。
SOURCE_IDS = [932549]

# 香港时间 2026-11-01 00:00 对应的 UTC 时间。
START = datetime(
    2026,
    10,
    31,
    16,
    0,
    0,
    tzinfo=timezone.utc,
)

# Media Cloud 只提供日期，向前多查一天。
START_DATE = date(2026, 10, 31)


# ============================================================
# 2. 输出字段
# ============================================================

ARTICLE_FIELDS = [
    "id",
    "source_type",
    "platform",
    "content_type",
    "published_at",
    "published_date",
    "date_precision",
    "date_boundary_note",
    "collected_at",
    "indexed_at",
    "url",
    "title",
    "body",
    "text_scope",
    "text_source",
    "language",
    "media_name",
    "media_url",
    "discovery_sources",
    "rss_feed_date",
    *KEYWORDS,
]

MATCH_FIELDS = [
    "id",
    "company",
    "query_term",
    "first_seen_at",
    "last_seen_at",
    "match_sources",
]

RUN_FIELDS = [
    "run_at",
    "company",
    "query_term",
    "window_start",
    "window_end",
    "status",
    "records_found",
    "error",
]

RSS_FIELDS = [
    "run_at",
    "feed",
    "status",
    "items_seen",
    "matched_items",
    "error",
]


# ============================================================
# 3. 文件、时间和文本处理
# ============================================================


def read_csv(path):
    """读取已有 CSV。"""
    if not path.exists():
        return []

    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as file:
        return list(csv.DictReader(file))


def write_csv(path, fields, rows):
    """写入临时文件后替换目标文件。"""
    temp_path = path.with_suffix(path.suffix + ".tmp")

    with temp_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)

    os.replace(temp_path, path)


def format_utc(value):
    """输出统一的 UTC 时间字符串。"""
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def parse_time(value):
    """读取带时区的时间字符串。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        raise ValueError("时间缺少时区")

    return parsed.astimezone(timezone.utc)


ENGLISH_PATHS = (
    "/news/",
    "/newsbeat/",
    "/sport/",
    "/business/",
    "/innovation/",
    "/culture/",
    "/future/",
    "/travel/",
    "/worklife/",
    "/reel/",
    "/audio/",
    "/programmes/",
    "/articles/",
)


def canonical_path(url):
    """
    提取用于跨来源去重的 BBC 文章路径。

    bbc.com 与 bbc.co.uk 的相同路径按同一候选文章处理。
    """
    parts = urlsplit(url or "")
    host = (parts.hostname or "").lower()

    is_bbc = host in {"bbc.com", "bbc.co.uk"} or host.endswith(
        (".bbc.com", ".bbc.co.uk")
    )

    if not is_bbc or not parts.path.lower().startswith(ENGLISH_PATHS):
        return ""

    return re.sub(r"/+", "/", parts.path).rstrip("/").lower()


def add_source(value, source):
    """给以竖线分隔的来源列表添加一个来源。"""
    values = set(filter(None, (value or "").split("|")))
    values.add(source)
    return "|".join(sorted(values))


def text_only(value):
    """清理 RSS 标题或摘要中的简单 HTML 标记。"""
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]*>", " ", value)
    return " ".join(value.split())


# ============================================================
# 4. BBC RSS 与 Media Cloud 采集
# ============================================================


def run_bbc_collection(api_key, output_folder):
    folder = Path(output_folder)
    folder.mkdir(parents=True, exist_ok=True)

    search = mediacloud.api.SearchApi(api_key)
    http = requests.Session()

    now = cutoff_time()
    watermark = format_utc(now)
    stamp = format_utc(datetime.now(timezone.utc))
    end_date = now.date()

    article_path = folder / "bbc_articles.csv"
    match_path = folder / "bbc_query_matches.csv"
    log_path = folder / "bbc_collection_runs.csv"
    rss_log_path = folder / "bbc_rss_runs.csv"
    state_path = folder / "bbc_state.json"

    articles = {row["id"]: row for row in read_csv(article_path)}

    # 旧版正式代码中的文章来自 Media Cloud。
    for row in articles.values():
        if not row.get("discovery_sources"):
            row["discovery_sources"] = "mediacloud"

    matches = {
        (
            row["id"],
            row["company"],
            row["query_term"],
        ): row
        for row in read_csv(match_path)
    }

    # 旧版正式代码中的命中记录来自 Media Cloud。
    for row in matches.values():
        if not row.get("match_sources"):
            row["match_sources"] = "mediacloud"

    logs = read_csv(log_path)
    rss_logs = read_csv(rss_log_path)

    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {}

    # 建立 BBC 路径到已有文章 ID 的映射。
    by_path = {}

    for row in articles.values():
        path = canonical_path(row.get("url", ""))

        if path and path not in by_path:
            by_path[path] = row["id"]

    def get_article(url, preferred_id):
        """按路径查找已有文章；不存在时创建记录。"""
        path = canonical_path(url)

        if not path:
            return None

        article_id = by_path.get(path, preferred_id)
        by_path[path] = article_id

        if article_id not in articles:
            articles[article_id] = {
                "id": article_id,
                "source_type": "media",
                "platform": "bbc",
                "content_type": "article",
                "url": url,
                "collected_at": stamp,
            }

        return articles[article_id]

    def add_match(article_id, company, term, source):
        """保存文章、公司、关键词及命中渠道。"""
        key = (article_id, company, term)
        previous = matches.get(key, {})

        matches[key] = {
            "id": article_id,
            "company": company,
            "query_term": term,
            "first_seen_at": (previous.get("first_seen_at") or stamp),
            "last_seen_at": stamp,
            "match_sources": add_source(
                previous.get("match_sources"),
                source,
            ),
        }

    def save_results():
        """根据命中记录更新公司列，并保存所有结果。"""
        matched_pairs = {(row["id"], row["company"]) for row in matches.values()}

        for article in articles.values():
            for company, terms in KEYWORDS.items():
                found = (
                    article["id"],
                    company,
                ) in matched_pairs

                published = article.get("published_date") or ""

                covered = published and all(
                    company + "|" + term in state
                    and parse_time(state[company + "|" + term]).date()
                    > date.fromisoformat(published)
                    for term in terms
                )

                if found:
                    article[company] = 1
                elif covered:
                    article[company] = 0
                else:
                    article[company] = ""

        write_csv(
            article_path,
            ARTICLE_FIELDS,
            articles.values(),
        )
        write_csv(
            match_path,
            MATCH_FIELDS,
            matches.values(),
        )
        write_csv(
            log_path,
            RUN_FIELDS,
            logs,
        )
        write_csv(
            rss_log_path,
            RSS_FIELDS,
            rss_logs,
        )

    save_results()

    print(f"更新开始：{stamp}")
    print(f"已有文章：{len(articles)} 篇")
    print(f"输出文件夹：{folder}")
    print("-" * 60)

    # --------------------------------------------------------
    # 4A. 读取 BBC 官方 RSS
    # --------------------------------------------------------

    for feed_name, feed_url in FEEDS.items():
        rss_log = {
            "run_at": stamp,
            "feed": feed_name,
            "status": "failed",
            "items_seen": 0,
            "matched_items": 0,
            "error": "",
        }

        try:
            response = http.get(
                feed_url,
                timeout=30,
            )
            response.raise_for_status()

            root = ET.fromstring(response.content)
            items = root.findall(".//item")
            rss_log["items_seen"] = len(items)

            for item in items:
                url = (item.findtext("link") or "").strip()
                path = canonical_path(url)

                if not path:
                    continue

                title = text_only(item.findtext("title"))
                description = text_only(item.findtext("description"))

                searchable_text = title + " " + description

                found_terms = [
                    (company, term)
                    for company, terms in KEYWORDS.items()
                    for term in terms
                    if re.search(
                        r"(?<!\w)" + re.escape(term) + r"(?!\w)",
                        searchable_text,
                        flags=re.IGNORECASE,
                    )
                ]

                if not found_terms:
                    continue

                raw_time = (item.findtext("pubDate") or "").strip()

                feed_time = None

                if raw_time:
                    try:
                        parsed = parsedate_to_datetime(raw_time)

                        if parsed.tzinfo:
                            feed_time = parsed.astimezone(timezone.utc)

                    except (
                        TypeError,
                        ValueError,
                        OverflowError,
                    ):
                        pass

                # 只对具有可识别时间的条目进行时间过滤。
                if feed_time and not START <= feed_time < now:
                    continue

                # 新 RSS 文章使用路径生成稳定 ID。
                preferred_id = (
                    "bbc_rss_" + hashlib.sha256(path.encode("utf-8")).hexdigest()
                )

                article = get_article(
                    url,
                    preferred_id,
                )

                article["discovery_sources"] = add_source(
                    article.get("discovery_sources"),
                    "rss",
                )

                article["title"] = article.get("title") or title

                # 这些 feed 为英文 BBC feed。
                article["language"] = article.get("language") or "en"

                article["date_precision"] = article.get("date_precision") or "unknown"

                if description:
                    article["body"] = description
                    article["text_scope"] = "excerpt"
                    article["text_source"] = "bbc_rss_description"
                else:
                    article["text_scope"] = article.get("text_scope") or "title_only"
                    article["text_source"] = article.get("text_source") or "rss_title"

                # RSS 时间单独保存，暂不假定它一定是首次发布时间。
                if feed_time:
                    article["rss_feed_date"] = format_utc(feed_time)

                for company, term in found_terms:
                    add_match(
                        article["id"],
                        company,
                        term,
                        "rss",
                    )

                rss_log["matched_items"] += 1

            rss_log["status"] = "ok"

            print(
                f"RSS {feed_name}："
                f"读取 {len(items)} 条，"
                f"命中 {rss_log['matched_items']} 条。"
            )

        except (
            requests.RequestException,
            ET.ParseError,
            ValueError,
        ) as exc:
            rss_log["error"] = type(exc).__name__

            print(f"RSS {feed_name} 失败：" f"{rss_log['error']}")

        rss_logs.append(rss_log)
        save_results()

    # --------------------------------------------------------
    # 4B. 更新 Media Cloud 数据
    # --------------------------------------------------------

    for company, terms in KEYWORDS.items():
        for term in terms:
            state_key = company + "|" + term

            if state_key in state:
                previous = parse_time(state[state_key])
            else:
                previous = START

            # 最近两天重查，用于发现延迟进入索引的文章。
            first_date = max(
                START_DATE,
                (previous - timedelta(days=2)).date(),
            )

            record = {
                "run_at": stamp,
                "company": company,
                "query_term": term,
                "window_start": first_date.isoformat(),
                "window_end": end_date.isoformat(),
                "status": "failed",
                "records_found": 0,
                "error": "",
            }

            print(f"Media Cloud {company} / {term}：" f"{first_date} 至 {end_date}")

            token = None
            seen_tokens = set()

            try:
                while True:
                    page, next_token = search.story_list(
                        f'"{term}"',
                        start_date=first_date,
                        end_date=end_date,
                        source_ids=SOURCE_IDS,
                        pagination_token=token,
                        page_size=100,
                    )

                    if not isinstance(page, list):
                        raise ValueError("响应页不是列表")

                    for story in page:
                        native_id = story.get("id")
                        url = story.get("url") or ""
                        published = story.get("publish_date")

                        if not native_id or not published:
                            raise ValueError("记录缺少 ID 或发表日期")

                        language = (story.get("language") or "").lower()

                        if not canonical_path(url):
                            continue

                        if language != "en":
                            continue

                        if isinstance(published, datetime):
                            published = published.date()
                        elif isinstance(published, str):
                            published = date.fromisoformat(published[:10])

                        if not START_DATE <= published <= end_date:
                            continue

                        article = get_article(
                            url,
                            "bbc_" + str(native_id),
                        )

                        article["published_date"] = published.isoformat()
                        article["date_precision"] = "date_only"

                        if published in {
                            date(2026, 10, 31),
                            date(2026, 11, 1),
                        }:
                            article["date_boundary_note"] = "start_boundary_uncertain"
                        else:
                            article["date_boundary_note"] = ""

                        article["title"] = (
                            story.get("title") or article.get("title") or ""
                        )

                        article["language"] = "en"
                        article["media_name"] = story.get("media_name") or ""
                        article["media_url"] = story.get("media_url") or ""
                        article["indexed_at"] = str(story.get("indexed_date") or "")

                        article["discovery_sources"] = add_source(
                            article.get("discovery_sources"),
                            "mediacloud",
                        )

                        # 已有 RSS 摘要时保留摘要。
                        article["text_scope"] = (
                            article.get("text_scope") or "title_only"
                        )
                        article["text_source"] = (
                            article.get("text_source") or "mediacloud_title"
                        )

                        add_match(
                            article["id"],
                            company,
                            term,
                            "mediacloud",
                        )

                        record["records_found"] += 1

                    if not next_token:
                        break

                    if next_token in seen_tokens:
                        raise ValueError("分页令牌重复")

                    seen_tokens.add(next_token)
                    token = next_token

                record["status"] = "ok"

            except Exception as exc:
                # 只记录错误类型，避免意外输出凭证。
                record["error"] = type(exc).__name__

                print(f"  查询未完成：{record['error']}。" "下次运行会重试。")

            logs.append(record)
            save_results()

            if record["status"] == "ok":
                state[state_key] = max(state.get(state_key, watermark), watermark)

                temp_path = state_path.with_suffix(".tmp")
                temp_path.write_text(
                    json.dumps(
                        state,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                os.replace(temp_path, state_path)

    save_results()

    print("-" * 60)
    print("本次更新结束。")
    print(f"文章总数：{len(articles)}")
    print(f"关键词命中记录：{len(matches)}")
    print(f"输出文件夹：{folder}")
