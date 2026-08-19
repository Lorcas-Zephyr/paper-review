import json
import os
import sys
import unittest
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))


class TestRuleEngine(unittest.TestCase):
    def test_load_rules_from_yaml_has_expected_sections(self):
        from core.rule_engine import RuleEngine

        engine = RuleEngine()
        self.assertIsInstance(engine.rules, dict)
        self.assertTrue(engine.rules)
        self.assertIn("typo_check", engine.rules)
        self.assertIn("punctuation_check", engine.rules)
        self.assertIn("citation_check", engine.rules)


class TestRegressionSamplesManifest(unittest.TestCase):
    def test_manifest_is_valid_json_and_nonempty(self):
        manifest = AGENT_DIR / "scripts" / "regression_samples.json"
        self.assertTrue(manifest.exists())
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("samples"), list)
        self.assertGreaterEqual(len(data["samples"]), 1)

    @unittest.skipUnless(os.getenv("RUN_REGRESSION") == "1", "set RUN_REGRESSION=1 to enable")
    def test_manifest_paths_exist(self):
        manifest = AGENT_DIR / "scripts" / "regression_samples.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))

        repo_root = AGENT_DIR.parent
        missing = []
        for s in data.get("samples", []) or []:
            rel = str(s.get("path") or "").strip()
            if not rel:
                continue
            p = repo_root / rel
            if not p.exists():
                missing.append(rel)

        self.assertEqual(missing, [])
