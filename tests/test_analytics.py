import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eventos.analytics import RunReporter, format_duration, load_reconcilable_sources


class AnalyticsTests(unittest.TestCase):
    def test_format_duration_rounds_to_nearest_second(self):
        self.assertEqual(format_duration(0), "00:00:00")
        self.assertEqual(format_duration(3661.6), "01:01:02")
        self.assertEqual(format_duration(-5), "00:00:00")

    def test_report_writes_last_run_json(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_dir = Path(temporary_directory) / "data"

            summary = RunReporter(output_dir).report(42, 61.234)

            stored = json.loads((output_dir / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary.total_events, 42)
            self.assertEqual(stored["total_events"], 42)
            self.assertEqual(stored["duration_seconds"], 61.23)
            self.assertEqual(stored["duration"], "00:01:01")
            self.assertEqual(stored["status"], "succeeded")

    def test_report_appends_github_job_summary(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            github_summary = root / "github-summary.md"
            with patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(github_summary)}):
                RunReporter(root / "data").report(125, 732.1)

            markdown = github_summary.read_text(encoding="utf-8")
            self.assertIn("| Total de eventos | Tiempo total |", markdown)
            self.assertIn("| 125 | 00:12:12 |", markdown)

    def test_complete_empty_sources_can_be_reconciled_without_accepting_failures(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            metrics = Path(temporary_directory) / "source_metrics.json"
            metrics.write_text(
                json.dumps(
                    [
                        {"source": "Completa", "status": "succeeded", "events": 3},
                        {"source": "Fallida", "status": "failed", "events": 0},
                        {"source": "Vacía", "status": "complete_empty", "events": 0},
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(load_reconcilable_sources(metrics), {"Completa", "Vacía"})


if __name__ == "__main__":
    unittest.main()
