import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import requests

# ============================================================
# 1. 检索关键词与设置
# ============================================================

from project import KEYWORDS, cutoff_time

# 香港时间 2026-10-01 00:00 对应的 UTC 时间
START = datetime(
    2026,
    9,
    30,
    16,
    0,
    0,
    tzinfo=timezone.utc,
)

GUARDIAN_URL = "https://content.guardianapis.com/search"

ARTICLE_FIELDS = [
    "id",
    "source_type",
    "platform",
    "content_type",
    "published_at",
    "collected_at",
    "url",
    "title",
    "body",
    "text_scope",
    "text_source",
    "guardian_type",
    "guardian_section",
    "guardian_last_modified",
    *KEYWORDS,
]

MATCH_FIELDS = [
    "id",
    "company",
    "query_term",
    "first_seen_at",
    "last_seen_at",
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


# ============================================================
# 2. 文件和时间处理
# ============================================================


def read_csv(path):
    """读取已有 CSV。"""
    if not path.exists():
        return []

    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, fields, rows):
    """先完成临时文件写入，再替换目标文件。"""
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


def parse_time(value):
    """读取带时区的时间并转换为 UTC。"""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if parsed.tzinfo is None:
        raise ValueError("时间缺少时区")

    return parsed.astimezone(timezone.utc)


def format_utc(value):
    """输出统一的 UTC 时间字符串。"""
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class TextExtractor(HTMLParser):
    """从 Guardian 返回的 HTML 摘要中提取文字。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def clean_html(text):
    parser = TextExtractor()
    parser.feed(text)
    return " ".join(" ".join(parser.parts).split())


class SearchError(Exception):
    """记录采集或响应解析错误。"""


# ============================================================
# 3. Guardian API 请求与分页
# ============================================================


def request_page(
    session,
    api_key,
    term,
    first_day,
    last_day,
    page,
):
    """获取一个关键词在指定日期范围内的一页结果。"""

    params = {
        "api-key": api_key,
        "q": '"' + term + '"',
        "from-date": first_day.isoformat(),
        "to-date": last_day.isoformat(),
        "page": page,
        "page-size": 50,
        "order-by": "oldest",
        "show-fields": "bodyText,trailText,lastModified",
    }

    for attempt in range(4):
        # 官方 Developer 级别为每秒最多 1 次请求。
        time.sleep(1.2)

        try:
            response = session.get(
                GUARDIAN_URL,
                params=params,
                timeout=45,
            )

        except requests.RequestException:
            if attempt == 3:
                raise SearchError("网络请求失败")

            time.sleep(5 * (attempt + 1))
            continue

        if response.status_code in (401, 403):
            raise SearchError(f"HTTP {response.status_code}，请检查 API Key 和权限")

        if response.status_code == 429:
            raise SearchError("HTTP 429，达到请求限制，稍后重试")

        if response.status_code >= 500:
            if attempt == 3:
                raise SearchError(f"HTTP {response.status_code}")

            time.sleep(5 * (attempt + 1))
            continue

        if response.status_code != 200:
            raise SearchError(f"HTTP {response.status_code}")

        try:
            result = response.json()["response"]

            if result["status"] != "ok":
                raise ValueError("status 不是 ok")

            items = result["results"]
            pages = int(result["pages"])
            total = int(result["total"])
            current_page = int(result["currentPage"])

            if (
                not isinstance(items, list)
                or pages < 0
                or total < 0
                or current_page != page
            ):
                raise ValueError("分页字段不符合预期")

            if not items and total > 0 and page <= pages:
                raise ValueError("结果页为空")

            return items, pages, total

        except (ValueError, KeyError, TypeError) as exc:
            raise SearchError(f"无法解析 API 响应：{exc}")

    raise SearchError("请求失败")


def search_window(
    session,
    api_key,
    term,
    first_day,
    last_day,
):
    """获取某个关键词在指定日期范围内的全部结果。"""

    items, pages, total = request_page(
        session,
        api_key,
        term,
        first_day,
        last_day,
        page=1,
    )

    for page in range(2, pages + 1):
        batch, reported_pages, reported_total = request_page(
            session,
            api_key,
            term,
            first_day,
            last_day,
            page,
        )

        if reported_pages != pages or reported_total != total:
            raise SearchError("分页期间结果发生变化，请重试")

        items.extend(batch)

    unique_ids = {item.get("id") for item in items}

    if len(items) < total or len(unique_ids) < total:
        raise SearchError("分页结果不完整或出现重复")

    return items


# ============================================================
# 4. 采集、去重与保存
# ============================================================


def run_guardian_collection(api_key, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    session = requests.Session()

    # 仅纳入本次运行开始前发布的内容。
    cutoff = cutoff_time()
    watermark = format_utc(cutoff)
    stamp = format_utc(datetime.now(timezone.utc))

    article_path = folder / "guardian_articles.csv"
    match_path = folder / "guardian_query_matches.csv"
    log_path = folder / "guardian_collection_runs.csv"
    state_path = folder / "guardian_state.json"

    articles = {row["id"]: row for row in read_csv(article_path)}

    matches = {
        (row["id"], row["company"], row["query_term"]): row
        for row in read_csv(match_path)
    }

    logs = read_csv(log_path)

    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {}

    def sync_articles():
        """根据命中记录和采集进度更新公司列。"""

        matched_pairs = {(row["id"], row["company"]) for row in matches.values()}

        for article in articles.values():
            published = parse_time(article["published_at"])

            for company, terms in KEYWORDS.items():
                covered = all(
                    company + "|" + term in state
                    and parse_time(state[company + "|" + term]) > published
                    for term in terms
                )

                if (article["id"], company) in matched_pairs:
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

    # 根据已有命中记录恢复公司标记。
    sync_articles()

    print(f"本次采集截止时间：{watermark}")
    print(f"已有文章：{len(articles)} 篇")
    print(f"输出文件夹：{folder}")
    print("-" * 60)

    for company, terms in KEYWORDS.items():
        for term in terms:
            state_key = company + "|" + term

            if state_key in state:
                previous = parse_time(state[state_key])
            else:
                previous = START

            # 重查最近两天，帮助捕捉延迟进入搜索结果的文章。
            first_day = max(
                START.date(),
                (previous - timedelta(days=2)).date(),
            )

            print(f"采集 {company} / {term}，" f"{first_day} 至 {cutoff.date()}")

            record = {
                "run_at": stamp,
                "company": company,
                "query_term": term,
                "window_start": first_day.isoformat(),
                "window_end": cutoff.date().isoformat(),
                "status": "failed",
                "records_found": 0,
                "error": "",
            }

            try:
                docs = search_window(
                    session,
                    api_key,
                    term,
                    first_day,
                    cutoff.date(),
                )

                for doc in docs:
                    native_id = doc.get("id")
                    published = doc.get("webPublicationDate")

                    if not native_id or not published:
                        raise SearchError("记录缺少 id 或 webPublicationDate")

                    published_dt = parse_time(published)

                    # 使用精确 UTC 时间过滤研究范围。
                    if not START <= published_dt < cutoff:
                        continue

                    article_id = "guardian_" + native_id
                    old = articles.get(article_id, {})

                    fields = doc.get("fields") or {}

                    body = fields.get("bodyText") or ""
                    text_source = "bodyText" if body else ""

                    if not body and fields.get("trailText"):
                        body = clean_html(fields["trailText"])
                        text_source = "trailText" if body else ""

                    title = doc.get("webTitle") or ""

                    if body and text_source == "bodyText":
                        text_scope = "full"
                    elif body:
                        text_scope = "excerpt"
                    elif title:
                        text_scope = "title_only"
                    else:
                        text_scope = "unavailable"

                    articles[article_id] = {
                        **old,
                        "id": article_id,
                        "source_type": "media",
                        "platform": "guardian",
                        "content_type": "article",
                        "published_at": format_utc(published_dt),
                        "collected_at": old.get("collected_at") or stamp,
                        "url": doc.get("webUrl") or "",
                        "title": title,
                        "body": body,
                        "text_scope": text_scope,
                        "text_source": text_source,
                        "guardian_type": doc.get("type") or "",
                        "guardian_section": doc.get("sectionName") or "",
                        "guardian_last_modified": (fields.get("lastModified") or ""),
                    }

                    match_key = (
                        article_id,
                        company,
                        term,
                    )

                    prior_match = matches.get(match_key, {})

                    matches[match_key] = {
                        "id": article_id,
                        "company": company,
                        "query_term": term,
                        "first_seen_at": (prior_match.get("first_seen_at") or stamp),
                        "last_seen_at": stamp,
                    }

                record["status"] = "ok"
                record["records_found"] = len(docs)

                print(f"  查询成功，返回 {len(docs)} 条。")

            except (SearchError, ValueError, TypeError) as exc:
                record["error"] = str(exc)
                print(f"  查询失败：{exc}")

            logs.append(record)

            # 每个关键词完成后保存已有结果。
            sync_articles()

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

                sync_articles()

            # 遇到权限或配额问题时停止，保留已有进度。
            if record["error"].startswith(("HTTP 401", "HTTP 403", "HTTP 429")):
                print("采集已停止，已保存的进度会保留。")
                return

    print("-" * 60)
    print("本次采集结束。")
    print(f"文章总数：{len(articles)}")
    print(f"关键词命中记录：{len(matches)}")
    print(f"输出文件夹：{folder}")
