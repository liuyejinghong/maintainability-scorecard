from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import cleanup, make_config  # noqa: E402

from scorecard_config import (  # noqa: E402
    ConfigError,
    config_from_dict,
    config_summary,
    load_config,
    make_snapshot,
)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-config-"))
        self.addCleanup(cleanup, self.tmp)

    def test_toml_defaults_and_threshold_override(self) -> None:
        path = make_config(
            self.tmp,
            """
            [thresholds]
            giant_function_lines = 42
            complexity = 7
            giant_file_lines = 99
            """,
        )
        config = load_config(path)
        self.assertEqual(config.giant_function_lines, 42)
        self.assertEqual(config.complexity_threshold, 7)
        self.assertEqual(config.giant_file_lines, 99)
        self.assertEqual(
            config.fallback_words,
            ("legacy", "compat", "retired", "historical", "deprecated"),
        )
        self.assertEqual(config.test_patterns[0], "test_*.py")
        self.assertIsNone(config.shared_state)
        self.assertIsNone(config.write_interfaces)

    def test_json_config_is_supported(self) -> None:
        data = {
            "project": {
                "name": "json-project",
                "source_roots": ["lib"],
                "module_prefixes": ["mypkg"],
            },
            "thresholds": {"complexity": 12},
            "tests": {"patterns": ["test_*.py"], "mock_helpers": ["Fake"]},
            "shared_state": {
                "receiver_names": ["state"],
                "receiver_attributes": ["state"],
                "file_threshold": 2,
            },
            "write_interfaces": {
                "call_names": ["send"],
                "adapter_files": ["lib/adapter.py"],
            },
            "custom_counters": [
                {"name": "todo", "regex": "TODO"},
                {"name": "send_calls", "call_names": ["send"]},
            ],
            "snapshots": [{"label": "base", "revision": "abc1234", "reason": "base"}],
        }
        path = self.tmp / "scorecard.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        config = load_config(path)
        self.assertEqual(config.project_name, "json-project")
        self.assertEqual(config.source_roots, ("lib",))
        self.assertEqual(config.module_prefixes, ("mypkg",))
        self.assertEqual(config.complexity_threshold, 12)
        self.assertEqual(config.shared_state.file_threshold, 2)
        self.assertEqual(config.write_interfaces.call_names, ("send",))
        self.assertEqual(len(config.custom_counters), 2)
        self.assertEqual(config.snapshots[0].label, "base")

    def test_unknown_keys_are_rejected_at_every_level(self) -> None:
        cases = [
            {"project": {"source_roots": ["src"], "typo": 1}},
            {"project": {"source_roots": ["src"]}, "scope": {"inclde": []}},
            {"project": {"source_roots": ["src"]}, "thresholds": {"complexityy": 3}},
            {"project": {"source_roots": ["src"]}, "shared_state": {"receiver_names": ["r"], "nope": 1}},
        ]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaisesRegex(ConfigError, "unknown key"):
                    config_from_dict(data)

    def test_missing_source_roots_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "source_roots"):
            config_from_dict({"project": {}})

    def test_duplicate_fallback_word_is_rejected_case_insensitively(self) -> None:
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            config_from_dict(
                {
                    "project": {"source_roots": ["src"]},
                    "fallbacks": {"words": ["legacy", "LEGACY"]},
                }
            )

    def test_invalid_contract_regex_is_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "not a valid regular expression"):
            config_from_dict(
                {
                    "project": {"source_roots": ["src"]},
                    "fallbacks": {"contract_string_pattern": "("},
                }
            )

    def test_custom_counter_needs_exactly_one_form(self) -> None:
        base = {"project": {"source_roots": ["src"]}}
        for counters in (
            [{"name": "both", "call_names": ["x"], "regex": "x"}],
            [{"name": "neither"}],
            [{"name": "bad name", "regex": "x"}],
            [{"name": "dup", "regex": "x"}, {"name": "dup", "regex": "y"}],
        ):
            with self.subTest(counters=counters):
                with self.assertRaises(ConfigError):
                    config_from_dict({**base, "custom_counters": counters})

    def test_duplicate_snapshot_labels_are_rejected(self) -> None:
        with self.assertRaisesRegex(ConfigError, "duplicate snapshot label"):
            config_from_dict(
                {
                    "project": {"source_roots": ["src"]},
                    "snapshots": [
                        {"label": "base", "revision": "a"},
                        {"label": "base", "revision": "b"},
                    ],
                }
            )

    def test_absolute_and_parent_paths_are_rejected(self) -> None:
        for bad in ("/abs/src", "../src", "src\\pkg"):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    config_from_dict({"project": {"source_roots": [bad]}})
        with self.assertRaises(ConfigError):
            config_from_dict(
                {
                    "project": {"source_roots": ["src"]},
                    "write_interfaces": {
                        "call_names": ["send"],
                        "adapter_files": ["/abs/adapter.py"],
                    },
                }
            )

    def test_shared_state_needs_a_receiver(self) -> None:
        with self.assertRaisesRegex(ConfigError, "at least one"):
            config_from_dict(
                {
                    "project": {"source_roots": ["src"]},
                    "shared_state": {"receiver_names": [], "receiver_attributes": []},
                }
            )

    def test_snapshot_label_validation(self) -> None:
        self.assertEqual(make_snapshot("base-1", "abc").label, "base-1")
        for bad in ("", "-base", "base/one", "base one"):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    make_snapshot(bad, "abc")

    def test_config_summary_echoes_effective_values(self) -> None:
        config = config_from_dict(
            {
                "project": {"source_roots": ["src"]},
                "thresholds": {"complexity": 5},
                "shared_state": {"receiver_names": ["state"]},
                "write_interfaces": {"call_names": ["send"], "adapter_files": ["src/a.py"]},
            }
        )
        summary = config_summary(config)
        self.assertEqual(summary["thresholds"]["complexity"], 5)
        self.assertEqual(summary["shared_state"]["receiver_names"], ["state"])
        self.assertEqual(summary["write_interfaces"]["call_names"], ["send"])
        self.assertIsNone(summary["shared_state"]["receiver_attributes"] or None)


if __name__ == "__main__":
    unittest.main()
