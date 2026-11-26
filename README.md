# AI SentiWeather · COMM3650

Kelly 与 Fufu 的 AI 媒体及论坛观察网站。线上地址：<https://comm3650-aisentiweather.github.io/AI-SentiWeather/>

首页是「媒体 / AI」时间线与情绪仪表盘，毛玻璃界面会跟随系统浅色 / 深色外观自动切换，顶部每个图标都可以点进对应公司的官网。仪表盘可以用 `‹ ›`、日期选择器或时间轴上的任意一天回看过去窗口的情绪分数，也能切换 1 天 / 7 天 / 30 天窗口；当前日期会写进网址（例如 `?date=2026-11-20&days=1`），可以直接分享；点击 **Get Start!** 进入选择页，左选平台、右选公司，点 **GO!** 生成比较报告。报告包含数量、平均情绪、情绪分布、情绪走势、高频词和代表发言六个板块，并可导出 PDF / PNG / CSV。

## 仓库与网址

- 组织：<https://github.com/COMM3650-AISentiWeather>（GitHub Free 计划）。`qiqiisverygood` 是 owner；`kelly0631` 已收到成员邀请（待接受），组织成员的默认仓库权限已设为 write。
- 代码仓库：<https://github.com/COMM3650-AISentiWeather/AI-SentiWeather>（Public）
- 线上站点：<https://comm3650-aisentiweather.github.io/AI-SentiWeather/>（Pages 从 `main` 分支 /docs 文件夹发布）
- 协作：两人都在组织里，可以直接推 `main`，也可以开分支发 Pull Request，另一位预览后再合并；组织设置和 Pages 只有 owner 能改。

## 目录

| 路径 | 用途 |
|---|---|
| `site/` | 网站源码：`index.html`（首页）、`start.html`（选择页）、`report.html`（报告）、`style.css`、`app.js`、`icons/` |
| `docs/` | **自动生成**的线上版本，GitHub Pages 从这里发布。不要手改，改 `site/` 后重新发布 |
| `scripts/` | 采集、分析、构建脚本。`build.py` 生成数据快照，`publish.py` 构建到 `docs/`，`demo.py` 生成本地演示 |
| `tests/` | 回归测试 |
| `private-data/` | 原始数据与采集进度（被 Git 忽略，不上传） |
| `.github/workflows/` | 每日采集与发布、PR 检查 |

## 当前状态

- 六个来源全部接入并在线上：NYT、Guardian、BBC 与 Hacker News、Reddit、Bluesky。数据起点是 **2026-10-01 00:00 HKT**；具体条数以 `docs/dashboard.json` 为准。
- 数据每天由 GitHub Actions 自动采集并发布，不需要任何人的电脑开机。最近一次运行：`gh run list --workflow=observatory.yml`。
- 媒体侧的 BBC 偶尔会因 Media Cloud 限流被标为 `partial`：当次仍会照常发布已有数据，下一次运行自动补齐。

## 本机预览

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests

# 用已保存的私有数据构建（放在 private-data/ 或由 OBS_STATE 指定）
python scripts/publish.py
python -m http.server 8000 --directory docs
```

浏览器打开 <http://localhost:8000>。没有真实数据时可以用合成演示数据：

```sh
python scripts/demo.py
python -m http.server 8000 --directory demo-dist
```

演示模式会在页面上标明 Demo，数据全部为合成内容。

## 修改并发布网站

1. 改 `site/` 里的文件（首页 `index.html`、选择页 `start.html`、报告 `report.html`、样式 `style.css`、逻辑 `app.js`）。
2. 在本机预览：`python scripts/publish.py` 后 `python -m http.server 8000 --directory docs`。
3. 提交 `site/` 与 `docs/` 两处改动，推送到 `main`。GitHub Pages 会在约一分钟内更新线上页面。

数据口径（谁算一家公司、情绪阈值、时间窗口）在 `scripts/build.py`；只要数据没变，改前端后重跑 `publish.py` 即可。

两个人协作时各自新建分支、发 Pull Request，另一位预览后再合并。PR 会自动运行 `check.yml`：单元测试、`node --check site/app.js` 和演示构建。

## GitHub Pages 设置

仓库 **Settings → Pages → Build and deployment**：

- Source 选 **Deploy from a branch**
- Branch 选 **main**，文件夹选 **/docs**

改完保存，几十秒后 <https://comm3650-aisentiweather.github.io/AI-SentiWeather/> 即可访问。

## 每日自动更新（已配置好，无需人工）

`.github/workflows/observatory.yml` 每天 **00:00 UTC（08:00 HKT）** 自动运行：采集 → 保存进度 → 分析 → 构建 `docs/` → 提交回 `main`，Pages 随后自己发布。整个流程跑在 GitHub 的服务器上，本机不用开机，也不需要任何人（或 AI）盯着。

每次采集的窗口在前一日 07:55 HKT 到今天 07:55 HKT 之间。截止点定在 07:55 而不是 08:00：留出这五分钟，任务哪怕提前几秒启动也仍然落在当天，不会退回昨天的窗口、把站点的截止时间往回拨。首页那张情绪卡片里有一行灰色小字，实时倒数距离下一次更新的时间（香港时间与 UTC 并列）。

- 想立刻更新一次：`gh workflow run observatory.yml -f operation=update`（约 10–20 分钟）。
- 只从已保存的数据重建页面、不重新采集：`gh workflow run observatory.yml -f operation=rebuild`。
- 补历史数据（回填）：`gh workflow run observatory.yml -f operation=backfill -f sources=reddit`。第一次运行会把媒体源的水位重置到项目起点（记在私有仓库的 `backfill.json`，只做一次），之后只采集、存档、**不发布**；回填跑完再用 `operation=rebuild` 发布。受 API 配额限制时，重复触发会从断点继续。
- 运行结果：`gh run list --workflow=observatory.yml`。若某个来源 `partial`，页面照常发布，任务最后一步会标红提醒，第二天自动重试。
- 采集进度存在私有数据仓库里，公开仓库只放图表数据。

以下 Secrets 和 Variable 已经配好，换环境时才需要重配：

| 类型 | 名称 | 值 |
|---|---|---|
| Secret | NYT_API_KEY | NYT Article Search 的 Key |
| Secret | GUARDIAN_API_KEY | Guardian Open Platform 的 Key |
| Secret | MEDIACLOUD_API_KEY | Media Cloud API Key |
| Secret | DATA_REPO_TOKEN | 私有数据仓库的 fine-grained token（Contents: Read and write） |
| Variable | DATA_REPO | 私有数据仓库的 `所有者/仓库名`，例如 `your-account/ai-sentiweather-data` |

私有数据仓库的根目录放两个文件夹：

| 文件夹 | 内容 |
|---|---|
| `Material_NYT/`、`Material_Guardian/`、`Material_BBC_English/` | 各来源的 `*_articles.csv`、`*_query_matches.csv`、`*_collection_runs.csv`、`*_state.json` |
| `Collected Data/` | 论坛的 `forum_raw.csv`、`forum_collection_runs.csv` |

同一来源的四个文件必须来自同一次备份。原始数据、API Key 不要放进公开仓库；网站公开的是图表数据、标题、短摘录、词频和原文链接。

## 数据解释

- 五家公司可多标签，每个比较组内一篇文章／一条发言计一次。媒体保留 API 检索命中或 RSS 关键词证据，查询尚未覆盖的公司标记留空。论坛沿用 Fufu 的带语境匹配。媒体和论坛均映射到同一套公司 ID。
- 整合 BBC 时按规范化文章路径合并英美域名和追踪参数的重复项，合并公司标签及发现渠道。仅有来源日期时保留日精度；RSS 条目时间作为单独标记的替代值。日期从 11 月 1 日开始，月初边界可能受来源时区影响。
- 日期选取依据所有选中平台的共同覆盖范围。媒体要求全部关键词已完成到该日期，论坛要求抽样查询窗口从项目起点连续成功。BBC 报告可选到上一个完整自然日。
- 情绪使用 VADER compound 分数（−1 至 +1）。媒体统一用标题，论坛用标题和可用正文；拉丁字母占比低或文字不足的条目仅计数量。VADER 是英文词典方法，反讽、混合语言及财经标题会影响结果。正面阈值 ≥0.05，负面阈值 ≤−0.05，其余中立。
- 首页「本周情绪」取最近 168 小时内、至少命中一家公司的可评分记录，按内容等权，每条只算一次。
- 每组的正面、负面示例分别取最高、最低分；中立取最接近零者。词云汇总每条分析文本中最常见的最多 35 个英文词。周从星期一开始，月按香港日历划分。
- Hacker News、Bluesky 和 Reddit 都采用抽样，因此「代表发言」是当前选中样本中的极值，不是全平台舆论。

## 参考

[GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site) · [Actions 定时任务](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) · [VADER](https://github.com/cjhutto/vaderSentiment)
