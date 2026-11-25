import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ============================================================
# 1. 关键词与设置
# ============================================================

from project import KEYWORDS, cutoff_time

# 香港时间 2026-11-01 00:00 对应的 UTC 时间
START = datetime(
    2026,
    10,
    31,
    16,
    0,
    0,
    tzinfo=timezone.utc,
)

API_URL = "https://api.nytimes.com/svc/search/v2/articlesearch.json"

# 每次请求前等待，实际配额以 NYT 账户信息为准。
REQUEST_INTERVAL = 15

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
    "nyt_document_type",
    "nyt_type_of_material",
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
# 2. 文件与时间处理
# ============================================================


def read_csv(path):
    """读取已有 CSV。"""
    if not path.exists():
        return []

    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path, fields, rows):
    """完成临时文件写入后，再替换目标文件。"""
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
    """读取带时区的时间，并转换为 UTC。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def format_utc(value):
    """将时间转换为统一的 UTC 字符串。"""
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


class SearchError(Exception):
    """记录采集或响应解析错误。"""


# ============================================================
# 3. API 请求与分页
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
        "begin_date": first_day.strftime("%Y%m%d"),
        "end_date": last_day.strftime("%Y%m%d"),
        "sort": "oldest",
        "page": page,
    }

    for attempt in range(4):
        time.sleep(REQUEST_INTERVAL)

        try:
            response = session.get(
                API_URL,
                params=params,
                timeout=45,
            )
        except requests.RequestException:
            if attempt == 3:
                raise SearchError("网络请求失败")

            time.sleep(15 * (attempt + 1))
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 3:
                raise SearchError(f"HTTP {response.status_code}")

            time.sleep(30 * (attempt + 1))
            continue

        if response.status_code != 200:
            raise SearchError(f"HTTP {response.status_code}")

        try:
            payload = response.json()
            result = payload["response"]

            metadata = result.get("metadata")
            if metadata is None:
                metadata = result.get("meta")

            if not isinstance(metadata, dict):
                raise ValueError("没有可识别的 metadata 或 meta")

            if "hits" not in metadata:
                raise ValueError(
                    f"metadata 中没有 hits，字段为 {list(metadata.keys())}"
                )

            hits = int(metadata["hits"])
            docs = result["docs"]

            # 仅当总命中数明确为 0 时，把 null 转成空列表。
            if docs is None and hits == 0:
                docs = []

            if not isinstance(docs, list):
                raise ValueError(f"docs 类型为 {type(docs).__name__}，hits 为 {hits}")

            return hits, docs

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
    """获取指定关键词与日期范围内的结果。"""

    hits, docs = request_page(session, api_key, term, first_day, last_day, page=0)

    if hits > 1000:
        if first_day >= last_day:
            raise SearchError("单日查询结果超过 1000 条")

        middle = first_day + (last_day - first_day) // 2

        return search_window(session, api_key, term, first_day, middle) + search_window(
            session,
            api_key,
            term,
            middle + timedelta(days=1),
            last_day,
        )

    if hits and not docs:
        raise SearchError("API 显示有结果，但第一页为空")

    page_count = (hits + 9) // 10

    for page in range(1, page_count):
        _, batch = request_page(session, api_key, term, first_day, last_day, page)

        if not batch:
            raise SearchError(f"第 {page} 页结果为空")

        docs.extend(batch)

    if len(docs) < hits:
        raise SearchError(f"分页结果不完整：显示 {hits} 条，获取 {len(docs)} 条")

    return docs


# ============================================================
# 4. 采集、去重并保存
# ============================================================


def run_nyt_collection(api_key, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    cutoff = cutoff_time()
    watermark = format_utc(cutoff)
    stamp = format_utc(datetime.now(timezone.utc))

    article_path = folder / "nyt_articles.csv"
    match_path = folder / "nyt_query_matches.csv"
    log_path = folder / "nyt_collection_runs.csv"
    state_path = folder / "nyt_state.json"

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
        """根据已保存的命中记录与进度更新公司列。"""
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

    # 启动时先用已有命中记录恢复公司标记。
    sync_articles()

    print(f"本次采集截止时间：{watermark}")
    print(f"已有文章：{len(articles)} 篇")
    print("已根据现有命中记录更新公司列。")
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
                    native_id = doc.get("_id")
                    published = doc.get("pub_date")

                    if not native_id or not published:
                        raise SearchError("文章缺少 _id 或 pub_date")

                    published_dt = parse_time(published)

                    if not START <= published_dt < cutoff:
                        continue

                    article_id = "nyt_" + native_id
                    old = articles.get(article_id, {})

                    text_source = next(
                        (
                            name
                            for name in (
                                "abstract",
                                "lead_paragraph",
                                "snippet",
                            )
                            if doc.get(name)
                        ),
                        "",
                    )

                    body = doc.get(text_source, "") if text_source else ""
                    title = (doc.get("headline") or {}).get("main") or ""

                    if body:
                        text_scope = "excerpt"
                    elif title:
                        text_scope = "title_only"
                    else:
                        text_scope = "unavailable"

                    articles[article_id] = {
                        **old,
                        "id": article_id,
                        "source_type": "media",
                        "platform": "nyt",
                        "content_type": "article",
                        "published_at": format_utc(published_dt),
                        "collected_at": old.get("collected_at") or stamp,
                        "url": doc.get("web_url") or "",
                        "title": title,
                        "body": body,
                        "text_scope": text_scope,
                        "text_source": text_source,
                        "nyt_document_type": doc.get("document_type") or "",
                        "nyt_type_of_material": (doc.get("type_of_material") or ""),
                    }

                    match_key = (article_id, company, term)
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

                print(f"  查询成功，返回 {len(docs)} 条结果。")

            except (SearchError, ValueError, TypeError) as exc:
                record["error"] = str(exc)
                print(f"  查询失败：{exc}。下次运行会重试。")

            logs.append(record)

            # 每个关键词完成后保存数据与公司标记。
            sync_articles()
            write_csv(match_path, MATCH_FIELDS, matches.values())
            write_csv(log_path, RUN_FIELDS, logs)

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
            elif record["error"].startswith(("HTTP 401", "HTTP 403", "HTTP 429")):
                return

    sync_articles()

    print("-" * 60)
    print("本次采集结束。")
    print(f"文章总数：{len(articles)}")
    print(f"关键词命中记录：{len(matches)}")
    print(f"输出文件夹：{folder}")
