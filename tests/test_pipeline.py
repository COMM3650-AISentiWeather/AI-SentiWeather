import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build
import project
import forum_api
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


class PipelineTests(unittest.TestCase):
    def test_source_selection_rejects_misspellings(self):
        import collect
        with patch.dict("os.environ", {"OBS_SOURCES": "nyt,unknown"}):
            with self.assertRaisesRegex(ValueError, "unknown"):
                collect._selection()
        with patch.dict("os.environ", {"OBS_SOURCES": "nyt, reddit"}):
            self.assertEqual(collect._selection(), ("nyt", "reddit"))

    def test_backfill_preparation_preserves_existing_watermarks(self):
        import backfill_reset
        import json
        with tempfile.TemporaryDirectory() as tmp, patch.object(backfill_reset, "STATE", Path(tmp)), patch.object(sys, "argv", ["backfill_reset.py"]):
            folder = Path(tmp) / project.MEDIA["nyt"]
            folder.mkdir()
            path = folder / "nyt_state.json"
            old = {"OpenAI|openai": "2026-11-26T02:00:00Z"}
            path.write_text(json.dumps(old))
            self.assertEqual(backfill_reset.main(), 0)
            self.assertEqual(json.loads(path.read_text()), old)

    def test_media_backfill_does_not_claim_october_until_complete(self):
        keywords = {"OpenAI": ["openai", "chatgpt"]}
        end = project.iso(project.LEGACY_START + timedelta(days=20))
        state = {"OpenAI|openai": end, "OpenAI|chatgpt": end}
        with patch.object(project, "MEDIA_KEYWORDS", keywords):
            self.assertEqual(project.media_window("nyt", state, {})[0], project.LEGACY_START)
            starts = {"OpenAI|openai": project.iso(project.START)}
            self.assertEqual(project.media_window("nyt", state, starts)[0], project.LEGACY_START)
            starts["OpenAI|chatgpt"] = project.iso(project.START)
            self.assertEqual(project.media_window("nyt", state, starts)[0], project.START)

    def test_bbc_group_preserves_old_coverage_until_group_succeeds(self):
        words = {"OpenAI": ["openai", "chatgpt"]}
        groups = {"OpenAI": ['"openai" OR "chatgpt"']}
        end = project.iso(project.LEGACY_START + timedelta(days=20))
        state = {"OpenAI|openai": end, "OpenAI|chatgpt": end}
        group_key = 'OpenAI|"openai" OR "chatgpt"'
        with patch.object(project, "MEDIA_KEYWORDS", words), patch.object(project, "BBC_QUERIES", groups):
            self.assertEqual(project.media_window("bbc", state, {})[0], project.LEGACY_START)
            state[group_key] = end
            self.assertEqual(project.media_window("bbc", state, {group_key: project.iso(project.START)})[0], project.START)

    def test_forum_old_coverage_remains_available_during_backfill(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(project, "STATE", Path(tmp)):
            row = dict(platform="reddit", status="ok", window_start=project.iso(project.LEGACY_START), window_end=project.iso(project.LEGACY_START + timedelta(days=10)))
            project.write_rows(Path(tmp) / "Collected Data/forum_collection_runs.csv", [row], list(row))
            self.assertEqual(project.forum_window("reddit")[0], project.LEGACY_START)
            self.assertEqual(project.forum_end("reddit"), project.START)

    def test_bbc_legacy_duplicate_preserves_both_matches_and_date(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(build, "STATE", Path(temp)),
        ):
            folder = Path(temp) / project.MEDIA["bbc"]
            a = dict(
                id="a",
                platform="bbc",
                content_type="article",
                title="An update",
                body="",
                language="en",
                published_at="",
                published_date="2026-11-05",
                url="https://www.bbc.com/news/articles/test",
                discovery_sources="mediacloud",
                OpenAI="1",
            )
            b = {
                **a,
                "id": "b",
                "url": "https://www.bbc.co.uk/news/articles/test?at_medium=RSS",
                "discovery_sources": "rss",
                "OpenAI": "",
            }
            other = {**a, "id": "c", "url": "https://www.bbc.com/pidgin/articles/test"}
            project.write_rows(folder / "bbc_articles.csv", [a, b, other], list(a))
            m = dict(id="b", company="Anthropic", query_term="anthropic")
            project.write_rows(folder / "bbc_query_matches.csv", [m], list(m))
            items = list(build.load_media("bbc"))
            self.assertEqual(len(items), 1)
            analyzer = SentimentIntensityAnalyzer()
            row = build.record(
                items[0], analyzer, project.instant("2026-11-06T02:00:00Z")
            )
            self.assertEqual(row["companies"], ["openai", "anthropic"])
            self.assertEqual(row["precision"], "date_only")
            self.assertEqual(row["channels"], ["mediacloud", "rss"])
            self.assertIsNone(
                build.record(
                    items[0], analyzer, project.instant("2026-11-05T02:00:00Z")
                )
            )

    def test_partial_or_disconnected_window_does_not_extend_coverage(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(project, "STATE", Path(temp)),
        ):
            a = project.START

            def entry(i, j, status):
                return dict(
                    platform="reddit",
                    window_start=project.iso(a + timedelta(days=i)),
                    window_end=project.iso(a + timedelta(days=j)),
                    status=status,
                )

            rows = [entry(0, 2, "ok"), entry(2, 3, "partial"), entry(3, 6, "ok")]
            project.write_rows(
                Path(temp) / "Collected Data/forum_collection_runs.csv",
                rows,
                list(rows[0]),
            )
            self.assertEqual(project.forum_end("reddit"), a + timedelta(days=2))

    def test_forum_context_survives_collection(self):
        post = dict(
            id="test",
            title="Claude works great",
            subreddit="ClaudeAI",
            created_utc=project.START.timestamp(),
        )
        self.assertIn("anthropic", forum_api._reddit_post_row(post)["companies"])


class MediaCheckpointTests(unittest.TestCase):
    def test_nyt_backfill_failure_keeps_old_coverage_then_retries_october(self):
        import contextlib
        import io
        import json
        import nyt

        key = {"OpenAI": ["openai"]}
        old_end = project.LEGACY_START + timedelta(days=20)
        cutoff = old_end + timedelta(days=1)
        with tempfile.TemporaryDirectory() as tmp, patch.object(nyt, "MEDIA_KEYWORDS", key), patch.object(nyt, "cutoff_time", return_value=cutoff), contextlib.redirect_stdout(io.StringIO()):
            folder = Path(tmp)
            (folder / "nyt_state.json").write_text(json.dumps({"OpenAI|openai": project.iso(old_end)}))
            with patch.object(nyt, "search_window", side_effect=nyt.SearchError("HTTP 429")) as search:
                nyt.run_nyt_collection("fixture", folder)
            self.assertEqual(search.call_args.args[3], project.START.date())
            self.assertFalse((folder / "nyt_coverage_starts.json").exists())
            self.assertEqual(json.loads((folder / "nyt_state.json").read_text())["OpenAI|openai"], project.iso(old_end))
            with patch.object(nyt, "search_window", return_value=[]) as search:
                nyt.run_nyt_collection("fixture", folder)
            self.assertEqual(search.call_args.args[3], project.START.date())
            self.assertEqual(json.loads((folder / "nyt_coverage_starts.json").read_text())["OpenAI|openai"], project.iso(project.START))

    def test_nyt_failed_query_keeps_watermark_and_retry_deduplicates(self):
        import contextlib
        import io
        import json
        import nyt

        key = {"OpenAI": ["openai", "chatgpt"]}
        cutoff = project.START + timedelta(days=4)
        item = {
            "_id": "test",
            "pub_date": project.iso(project.START + timedelta(hours=1)),
            "headline": {"main": "A new model"},
            "web_url": "https://example.org/a",
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(nyt, "KEYWORDS", key),
            patch.object(nyt, "MEDIA_KEYWORDS", key),
            patch.object(nyt, "cutoff_time", return_value=cutoff),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            folder = Path(tmp)
            with patch.object(
                nyt, "search_window", side_effect=[[item], nyt.SearchError("HTTP 429")]
            ):
                nyt.run_nyt_collection("fixture", folder)
            self.assertEqual(
                json.loads((folder / "nyt_state.json").read_text()),
                {"OpenAI|openai": project.iso(cutoff)},
            )
            with patch.object(nyt, "search_window", return_value=[item]):
                nyt.run_nyt_collection("fixture", folder)
            self.assertEqual(len(project.read_rows(folder / "nyt_articles.csv")), 1)
            self.assertEqual(
                len(project.read_rows(folder / "nyt_query_matches.csv")), 2
            )
            self.assertEqual(
                len(json.loads((folder / "nyt_state.json").read_text())), 2
            )
