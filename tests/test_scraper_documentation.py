"""Coherencia de la documentación operativa con las fuentes y el workflow."""
import re
import unittest
from pathlib import Path

from eventos.config import SourceRepository


ROOT = Path(__file__).resolve().parents[1]


class ScraperDocumentationTests(unittest.TestCase):
    def test_source_count_matches_readme_reference_and_latex(self):
        permanent = sum(source.enabled and not source.get("active_months")
                        for source in SourceRepository().load())
        for name in ("README.md", "docs/SOURCES.md", "docs/DOCUMENTACION_SCRAPER.tex"):
            text = (ROOT / name).read_text(encoding="utf-8")
            match = re.search(r"(\d+) fuentes permanentes", text)
            self.assertIsNotNone(match, name)
            self.assertEqual(int(match[1]), permanent, name)

    def test_new_sources_are_documented_without_inventing_inventory_scores(self):
        reference = (ROOT / "docs/SOURCES.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        additions = [s for s in SourceRepository().load() if s.get("integration_verified_at") in {"2026-10-27", "2026-10-28"}]
        self.assertEqual(len(additions), 7)
        for source in additions:
            self.assertIn(source.name, reference)
            self.assertIn(source.name, readme)
            self.assertIn(source.url, reference)

    def test_stage_budget_settings_exist_in_example_workflow_and_manual(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/scrape-events.yml").read_text(encoding="utf-8")
        manual = (ROOT / "docs/DOCUMENTACION_SCRAPER.tex").read_text(encoding="utf-8")
        for stage in ("OCR", "REWRITE", "LOCATION"):
            budget = f"NVIDIA_{stage}_STAGE_BUDGET_SECONDS"
            threshold = f"NVIDIA_{stage}_FAILURE_THRESHOLD"
            for key in (budget, threshold):
                setting = re.search(rf"^{key}=(\d+)$", example, re.M)
                self.assertIsNotNone(setting, key)
                self.assertRegex(workflow, rf'{key}: "{setting[1]}"')
                self.assertIn(key, manual)


if __name__ == "__main__":
    unittest.main()
