from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import cleanup  # noqa: E402

from scorecard_report import EmbedError, embed_json, render_markdown  # noqa: E402


def _metrics(label: str = "a") -> dict:
    return {
        "scale": {
            "prod_files": 2,
            "prod_sloc": 100,
            "prod_physical_lines": 120,
            "sloc_tokenize_fallback_files": 0,
        },
        "giant_functions": {
            "count": 1,
            "own_lines": 20,
            "all_function_own_lines": 50,
            "line_share": 0.4,
            "longest": {
                "path": "src/pkg/a.py",
                "qualname": "big",
                "own_lines": 20,
            },
        },
        "complexity": {
            "count": 1,
            "sum": 12,
            "top10_sum": 6,
            "top10_share": 0.5,
            "max": {"path": "src/pkg/a.py", "qualname": "big", "complexity": 6},
        },
        "giant_files": {"count": 0, "lines": 0, "line_share": 0.0},
        "module_tangle": {
            "import_edges": 2,
            "import_edges_excluding_type_checking": 2,
            "top_level_import_edges": 1,
            "max_scc_size": 2,
            "max_scc_size_excluding_type_checking_edges": 2,
            "max_scc_size_top_level_imports": 1,
            "function_local_imports": 1,
        },
        "private_access": {"count": 1},
        "fallbacks": {
            "legacy_words": 2,
            "word_counts": {"legacy": 1, "compat": 1},
            "versioned_contract_strings_distinct": 1,
            "versioned_contract_strings_occurrences": 1,
            "except_exception": 1,
            "bare_except": 0,
            "broad_except_total": 1,
        },
        "tests": {
            "test_files": 1,
            "test_lines": 10,
            "private_attr_accesses": 0,
            "mock_helpers": 0,
            "parse_errors": [],
        },
        "parse_errors": [],
    }


def _card(label: str = "a") -> dict:
    return {
        "generated_at": "2026-01-01T00:00:00Z",
        "config": {
            "project": "fixture",
            "source_roots": ["src"],
            "module_prefixes": [],
            "include": ["**/*.py"],
            "exclude": [],
            "thresholds": {
                "giant_function_lines": 150,
                "complexity": 20,
                "giant_file_lines": 500,
            },
            "tests": {
                "patterns": ["test_*.py"],
                "roots": [],
                "mock_helpers": ["MagicMock"],
            },
            "fallbacks": {
                "words": ["legacy", "compat", "retired", "historical", "deprecated"],
                "contract_string_pattern": "[A-Za-z0-9_.-]+/v[0-9]+",
            },
            "shared_state": None,
            "write_interfaces": None,
            "custom_counters": [],
        },
        "snapshots": {
            label: {
                "short": "abc1234",
                "committed_at": "2026-01-01T00:00:00+00:00",
                "reason": "base snapshot",
                "metrics": _metrics(label),
            }
        },
    }


class MarkdownTests(unittest.TestCase):
    def test_required_rows_and_three_scc_views(self) -> None:
        markdown = render_markdown(_card())
        for text in (
            "# 可维护性记分卡",
            "生产代码非空非注释行数",
            "巨型函数 >150 行（个数）",
            "圈复杂度 >20 函数（个数）",
            "导入图最大 SCC（全部边）",
            "导入图最大 SCC（去掉 TYPE_CHECKING 边）",
            "导入图最大 SCC（仅顶层加载期 import）",
            "跨文件私有属性访问",
            "兼容残留字样（次数）",
            "版本化合同字符串（去重）",
            "except Exception（个数）",
            "bare except（个数）",
            "测试 mock 助手引用",
            "已知近似",
            "行数是背景不是成绩",
        ):
            with self.subTest(text=text):
                self.assertIn(text, markdown)

    def test_optional_project_rows(self) -> None:
        card = _card()
        card["config"]["shared_state"] = {
            "receiver_names": ["runtime"],
            "receiver_attributes": [],
            "file_threshold": 3,
        }
        card["config"]["write_interfaces"] = {
            "call_names": ["signed_post"],
            "adapter_files": ["src/adapter.py"],
        }
        card["config"]["custom_counters"] = [
            {
                "name": "mark_degraded_calls",
                "call_names": ["mark_degraded"],
                "regex": None,
                "description": "degraded markers",
            }
        ]
        metrics = card["snapshots"]["a"]["metrics"]
        metrics["shared_state"] = {
            "accesses": 5,
            "keys_written_by_ge_n_files": 1,
        }
        metrics["write_interfaces"] = {"count": 2}
        metrics["custom"] = {"mark_degraded_calls": 3}
        markdown = render_markdown(card)
        self.assertIn("共享状态访问次数", markdown)
        self.assertIn("被 ≥3 文件写入的共享状态键", markdown)
        self.assertIn("直接外部写接口调用点", markdown)
        self.assertIn("自定义计数：mark_degraded_calls", markdown)

    def test_parse_errors_are_reported(self) -> None:
        card = _card()
        card["snapshots"]["a"]["metrics"]["parse_errors"] = [
            {"path": "src/bad.py", "error": "invalid syntax"}
        ]
        markdown = render_markdown(card)
        self.assertIn("src/bad.py", markdown)
        self.assertIn("invalid syntax", markdown)


class EmbedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-embed-"))
        self.addCleanup(cleanup, self.tmp)

    def test_replaces_only_the_target_block(self) -> None:
        html = (
            "<html><body>\n"
            '<script id="other" type="application/json">{"keep": true}</script>\n'
            '<script id="data" type="application/json">{"old": 1}</script>\n'
            "</body></html>\n"
        )
        path = self.tmp / "index.html"
        path.write_text(html, encoding="utf-8")
        payload = {"new": 2, "s": "</script>", "u": "  "}
        embed_json(path, "data", payload)
        updated = path.read_text(encoding="utf-8")
        self.assertIn('{"keep": true}', updated)
        embedded = updated.split('<script id="data" type="application/json">', 1)[1]
        embedded = embedded.split("</script>", 1)[0]
        self.assertEqual(json.loads(embedded), payload)
        self.assertIn("<\\/script>", embedded)
        self.assertIn("\\u2028", embedded)
        self.assertIn("\\u2029", embedded)
        self.assertEqual(updated.count('type="application/json"'), 2)

    def test_attribute_order_is_irrelevant(self) -> None:
        path = self.tmp / "index.html"
        path.write_text(
            '<script type="application/json" id="data">{}</script>',
            encoding="utf-8",
        )
        embed_json(path, "data", {"ok": True})
        self.assertIn('{"ok":true}', path.read_text(encoding="utf-8"))

    def test_missing_or_duplicate_id_raises(self) -> None:
        path = self.tmp / "index.html"
        path.write_text(
            '<script type="application/json" id="other">{}</script>',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(EmbedError, "no <script"):
            embed_json(path, "data", {})
        path.write_text(
            '<script type="application/json" id="data">{}</script>'
            '<script type="application/json" id="data">{}</script>',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(EmbedError, "multiple"):
            embed_json(path, "data", {})


if __name__ == "__main__":
    unittest.main()
