"""Guard the CI workflow's security and cost boundaries."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


class CIWorkflowTests(unittest.TestCase):
    def test_triggers_are_limited_to_main_prs_and_manual_runs(self):
        self.assertRegex(
            WORKFLOW,
            r"(?m)^on:\n  push:\n    branches:\n      - main\n"
            r"  pull_request:\n    branches:\n      - main\n  workflow_dispatch:\n",
        )
        self.assertNotIn("pull_request_target", WORKFLOW)
        self.assertNotRegex(WORKFLOW, r"(?m)^\s+schedule:")

    def test_actions_are_sha_pinned_and_tokens_are_read_only(self):
        uses = re.findall(r"(?m)^\s+uses:\s+([^\s#]+)", WORKFLOW)
        self.assertTrue(uses)
        for reference in uses:
            self.assertRegex(reference, r"@[0-9a-f]{40}$")

        self.assertIn("permissions:\n  contents: read", WORKFLOW)
        self.assertNotRegex(WORKFLOW, r"\$\{\{\s*secrets\.")
        self.assertEqual(WORKFLOW.count("persist-credentials: false"), 3)

    def test_jobs_are_bounded_and_cancel_superseded_runs(self):
        jobs = WORKFLOW.split("jobs:\n", maxsplit=1)[1]
        self.assertEqual(jobs.count("timeout-minutes:"), 3)
        self.assertEqual(jobs.count("runs-on: ubuntu-24.04"), 3)
        self.assertIn("cancel-in-progress: true", WORKFLOW)
        self.assertNotIn("actions/cache", WORKFLOW)
        self.assertNotIn("upload-artifact", WORKFLOW)

    def test_security_checks_use_full_history_redaction_and_strict_hash_audit(self):
        self.assertIn("fetch-depth: 0", WORKFLOW)
        self.assertIn("--redact --no-banner --log-opts='--all'", WORKFLOW)
        self.assertIn("gitleaks_8.30.1_linux_x64.tar.gz", WORKFLOW)
        self.assertIn("sha256sum --check --strict", WORKFLOW)
        self.assertIn("--ignore-gitleaks-allow", WORKFLOW)
        self.assertIn("require-hashes: true", WORKFLOW)
        self.assertIn("disable-pip: true", WORKFLOW)
        self.assertIn("internal-be-careful-extra-flags: --strict", WORKFLOW)

    def test_python_and_browser_coverage_are_explicit(self):
        self.assertIn("python-version: ['3.10', '3.13']", WORKFLOW)
        self.assertEqual(WORKFLOW.count("tests/run_browser_checks.cjs --all"), 1)
        self.assertIn("tests/ui_preferences.test.cjs", WORKFLOW)
        self.assertIn("tests/public_share_image_policy.test.cjs", WORKFLOW)
        self.assertIn("AUCTIONRADAR_TEST_PYTHON", WORKFLOW)
        self.assertIn("AUCTIONRADAR_TEST_CHROME", WORKFLOW)
        self.assertIn("NODE_PATH", WORKFLOW)

    def test_node_entry_points_exist(self):
        for entry_point in re.findall(r"(?m)^\s+node (tests/[\w./-]+\.cjs)", WORKFLOW):
            with self.subTest(entry_point=entry_point):
                self.assertTrue((ROOT / entry_point).is_file())


if __name__ == "__main__":
    unittest.main()
