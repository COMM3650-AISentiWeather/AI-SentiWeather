# AI Sentiweather

AI Sentiweather is a COMM3650 project by Kelly and Fufu. It explores how selected news outlets and online communities discuss five AI companies over time.

**[Visit AI Sentiweather](https://comm3650-aisentiweather.github.io/AI-SentiWeather/)**

## Explore the data

The project follows OpenAI, Anthropic, Google DeepMind, DeepSeek and ByteDance Seed across The New York Times, The Guardian, BBC, Hacker News, Reddit and Bluesky. BBC discovery combines Media Cloud and official RSS feeds. Forum data is sampled; it does not represent every post or every user on a platform.

On the homepage, explore the sentiment timeline and recent sentiment. Select **Get Started** to choose a time period and either one platform with up to three companies or one company with up to three platforms. The report shows volume, sentiment, sentiment over time, frequent words and selected examples. Report sections can be hidden, and the available export controls provide PDF, PNG and CSV output.

The project aims to collect data from **1 October 2026**, in Hong Kong time (UTC+8). The date picker uses the verified coverage shared by the selected sources. A source's available dates may be shorter when collection is incomplete; BBC date-only records are included after their day is complete. The data cutoff is **07:55 HKT** and the daily workflow is scheduled for **08:00 HKT**. Publication follows after collection completes and may be delayed.

## How to interpret the results

- Each item can be associated with more than one company. Search matches and keyword rules identify mentions; a mention alone does not necessarily mean an article is mainly about that company. Search terms can have ambiguous meanings and search services may omit or delay results.
- Sentiment uses VADER's English-language compound score, from −1 to +1. Scores of at least 0.05 are positive, at most −0.05 are negative, and the rest are neutral. Media sentiment uses headlines; forum sentiment uses available titles and text. Items with insufficient English text can count toward volume without receiving a sentiment score.
- Content is weighted equally. Selected examples are extreme scores within the available sample, not representative quotes from an entire platform. VADER may misread context, sarcasm, mixed languages and financial language, and it does not measure whether a statement is true.
- Searches, sampling, source availability, rate limits and differing text lengths affect comparisons. The dashboard describes the collected sample, not a complete or representative measure of public opinion.

## For maintainers

### How the daily update works

GitHub Actions schedules `.github/workflows/observatory.yml` on the default branch for 08:00 Hong Kong time, with backup triggers at 08:17 and 08:37. A scheduled run checks the latest committed public snapshot and skips collection when all six sources have successfully reached the current cutoff; otherwise it collects, saves private checkpoints, builds `docs/`, and publishes. Manual operations always run. Neither maintainer's computer needs to be on. GitHub can delay or drop any scheduled trigger, and APIs, credentials, quotas, GitHub Actions availability and repository permissions can still cause a run to fail. GitHub can disable scheduled workflows in an inactive public repository; check the Actions page regularly.

The cutoff is the most recent 07:55 HKT boundary at the start of a run. A manual run before 07:55 uses the previous day's cutoff. The homepage countdown indicates the next scheduled start, not a guaranteed completion time. The source status and verified dates in the published data reflect the last run; investigate any `partial` or failed source in the Actions logs.

A successful query advances its checkpoint; an unsuccessful query retains the previous checkpoint for a later retry. Media queries revisit recent dates to catch delayed indexing. Historical coverage has separate checkpoints. A partial collection is saved and may publish existing verified data; the workflow then reports failure so the issue is visible. Inspect the relevant logs before retrying.

### Configuration

Configure these under the public repository's **Settings → Secrets and variables → Actions**:

| Type | Name | Purpose |
| --- | --- | --- |
| Secret | `NYT_API_KEY` | New York Times Article Search |
| Secret | `GUARDIAN_API_KEY` | Guardian Open Platform |
| Secret | `MEDIACLOUD_API_KEY` | Media Cloud Search for BBC |
| Secret | `DATA_REPO_TOKEN` | Access to the private data repository, with Contents read/write |
| Variable | `DATA_REPO` | Private data repository in `owner/repository` form |

The private data repository must have a `main` branch. Keep each source's articles, query matches, collection logs, state and coverage-start files together. Forum records and run logs are stored in `Collected Data/`; `latest.json` records the last run's cutoff and statuses. Do not put API keys, raw private data or the private repository token in the public repository. Check token expiry and API quotas when troubleshooting.

GitHub Pages serves `docs/` from the public repository's `main` branch. A site build copies `site/`, generates Plotly's JavaScript bundle and writes `dashboard.json`; the dashboard, article titles, short excerpts and links are public.

### Running and troubleshooting

Open **Actions → AI Sentiweather → Run workflow** and choose an operation on `main`:

- `update`: collect and publish. Leave `sources` blank for all sources, or specify comma-separated names: `nyt`, `guardian`, `bbc`, `hacker_news`, `reddit`, `bluesky`.
- `rebuild`: publish a new site from saved data without collecting.
- `backfill`: collect the selected sources and save checkpoints without publishing; run `rebuild` afterwards. Forum backfill rereads the project window, while media collectors retry any incomplete historical coverage.
- `restore`: explicitly set media watermarks using `reset_to`. This changes collection progress; use it only when recovering from a known checkpoint problem and after backing up the private data.

For a failed or partial run, expand **Collect sources**, check the affected source's response and whether its key and quota are valid, then retry after resolving the cause. A `429` indicates rate limiting. Avoid concurrent runs or overlapping manual backfills. **Persist private checkpoints** must succeed before new progress is safely stored; **Build and check public snapshot** and **Publish the rebuilt site to docs/** must succeed for a new snapshot to reach Pages. A completed Pages deployment is a separate check.

### Repository and local development

| Path | Purpose |
| --- | --- |
| `site/` | Website source files |
| `scripts/` | Collection, data processing and site build |
| `tests/` | Automated regression tests |
| `docs/` | Generated site and public snapshot served by GitHub Pages |
| `.github/workflows/` | Daily workflow and pull-request checks |
| `private-data/` | Local private data checkout; excluded from the public repository |

For a local demo with synthetic data:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s tests
node --check site/app.js
python scripts/demo.py
python -m http.server 8000 --directory demo-dist
```

Open `http://localhost:8000`. To build with an existing private data checkout, use `python scripts/publish.py`; set `OBS_STATE` first if the data is outside `private-data/`. Coordinate changes using branches and pull requests, run the checks, and avoid overwriting a newer `docs/dashboard.json` snapshot when merging.

## References

[GitHub Actions scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) · [GitHub Pages](https://docs.github.com/en/pages) · [VADER](https://github.com/cjhutto/vaderSentiment)
