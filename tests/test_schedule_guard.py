import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import schedule_guard
from datetime import datetime
import project
from project import FORUM, MEDIA, instant


class ScheduleGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "dashboard.json"
        self.cutoff = instant("2026-11-27T16:00:00Z")
        self.data = {
            "cutoff": "2026-11-27T16:00:00Z",
            "status": {p: "ok" for p in (*MEDIA, *FORUM)},
            "ranges": {p: ["2026-10-01", "2026-11-27"] for p in (*MEDIA, *FORUM)},
        }

    def check(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        return schedule_guard.snapshot_complete(self.path, self.cutoff)

    def test_current_complete_snapshot_is_skipped(self):
        self.assertTrue(self.check())

    def test_midnight_cutoff_uses_previous_date_for_bbc_too(self):
        self.data["ranges"]["bbc"][1] = "2026-11-26"
        self.assertFalse(self.check())

    def test_partial_stale_or_missing_source_retries(self):
        self.data["status"]["nyt"] = "partial"
        self.assertFalse(self.check())
        self.data["status"]["nyt"] = "ok"
        self.data["ranges"]["reddit"][1] = "2026-11-26"
        self.assertFalse(self.check())
        self.data["ranges"].pop("reddit")
        self.assertFalse(self.check())

    def test_old_cutoff_and_missing_or_corrupt_file_retry(self):
        self.data["cutoff"] = "2026-11-26T23:55:00Z"
        self.assertFalse(self.check())
        self.path.write_text("not json")
        self.assertFalse(schedule_guard.snapshot_complete(self.path, self.cutoff))
        self.assertFalse(schedule_guard.snapshot_complete(self.path.with_name("missing"), self.cutoff))

    def test_manual_operation_is_never_skipped(self):
        output = Path(self.tmp.name) / "output"
        with patch.dict(os.environ, {"EVENT_NAME": "workflow_dispatch", "GITHUB_OUTPUT": str(output)}):
            schedule_guard.main()
        self.assertEqual(output.read_text(), "needed=true\n")

    def test_cutoff_changes_at_midnight_hong_kong(self):
        class FixedDatetime(datetime):
            current = None

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz)

        with patch.dict(os.environ, {}, clear=True), patch.object(project, "datetime", FixedDatetime):
            FixedDatetime.current = instant("2026-11-27T15:59:59Z")
            self.assertEqual(project.cutoff_time(), instant("2026-11-26T16:00:00Z"))
            FixedDatetime.current = instant("2026-11-27T16:00:00Z")
            self.assertEqual(project.cutoff_time(), instant("2026-11-27T16:00:00Z"))


if __name__ == "__main__":
    unittest.main()
