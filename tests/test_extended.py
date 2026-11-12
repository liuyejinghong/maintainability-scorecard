from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import cleanup, make_config, make_git_repo, make_tree  # noqa: E402

import scorecard  # noqa: E402
import scorecard_config  # noqa: E402
import scorecard_extended  # noqa: E402
import scorecard_measure  # noqa: E402
import scorecard_report  # noqa: E402
from scorecard_config import ConfigError  # noqa: E402


class ExtendedMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-extended-"))
        self.addCleanup(cleanup, self.tmp)

    def _measure_with_index(
        self, root: Path, index_text: str, path_base: str = "src", name: str = "index.toml"
    ) -> dict:
        index = self.tmp / name
        index.write_text(index_text, encoding="utf-8")
        config_path = self.tmp / "module.toml"
        config_path.write_text(
            '[project]\nname="fixture"\nsource_roots=["src"]\n'
            '[module_index]\nfixed_file="%s"\npath_base="%s"\n' % (index, path_base),
            encoding="utf-8",
        )
        return scorecard_measure.measure_snapshot(
            root, scorecard_config.load_config(config_path)
        )

    @staticmethod
    def _markdown(metrics: dict) -> str:
        card = {
            "generated_at": "2026-01-01T00:00:00Z",
            "config": {
                "project": "fixture",
                "source_roots": ["src"],
                "thresholds": {
                    "giant_function_lines": 150,
                    "complexity": 20,
                    "giant_file_lines": 500,
                },
                "shared_state": None,
                "write_interfaces": None,
                "custom_counters": [],
                "fallbacks": {"words": ["legacy"]},
            },
            "definition_metadata": {},
            "snapshots": {
                "snap": {
                    "short": "abc1234",
                    "committed_at": "",
                    "reason": "",
                    "metrics": metrics,
                }
            },
        }
        return scorecard_report.render_markdown(card)

    def test_risk_tiers_count_parameters_and_keep_variadic_separate(self) -> None:
        root = self.tmp / "tree"
        root.mkdir()
        (root / "src").mkdir()
        (root / "src" / "a.py").write_text(
            "class A:\n"
            "    def method(self, a, b, /, c, *, d, **kwargs):\n"
            "        if a:\n"
            "            return b\n"
            "        return c\n"
            "\n"
            "def variadic(*args, **kwargs):\n"
            "    return args\n",
            encoding="utf-8",
        )
        config_path = make_config(self.tmp, name="risk.toml")
        config = scorecard_config.load_config(config_path)
        metrics = scorecard_measure.measure_snapshot(root, config)
        details = metrics["risk_tiers"]["parameter_details"]
        method = next(item for item in details if item["qualname"] == "A.method")
        variadic = next(item for item in details if item["qualname"] == "variadic")
        self.assertEqual(method["count"], 4)
        self.assertEqual(method["positional_only"], 2)
        self.assertEqual(method["positional_or_keyword"], 1)
        self.assertEqual(method["keyword_only"], 1)
        self.assertEqual((method["varargs"], method["kwargs"]), (0, 1))
        self.assertEqual((variadic["count"], variadic["varargs"], variadic["kwargs"]), (0, 1, 1))
        self.assertEqual(metrics["risk_tiers"]["complexity"]["thresholds"], [5, 10, 25])

    def test_risk_tier_equal_threshold_falls_into_higher_bucket(self) -> None:
        config = scorecard_config.load_config(make_config(self.tmp, name="bounds.toml"))
        functions = [
            {
                "path": "a.py",
                "qualname": "c%d" % value,
                "own_lines": 1,
                "complexity": value,
                "parameters": {"count": 0},
            }
            for value in (4, 5, 10, 25, 26)
        ]
        functions.extend(
            {
                "path": "a.py",
                "qualname": "p%d" % value,
                "own_lines": 1,
                "complexity": 1,
                "parameters": {"count": value},
            }
            for value in (3, 5, 7)
        )
        result = scorecard_extended.risk_tiers(functions, config)
        complexity = result["complexity"]["buckets"]
        self.assertEqual(complexity["<5"]["functions"], 4)  # 4 plus three complexity-1 functions
        self.assertEqual(complexity["<10"]["functions"], 1)  # 5 (equal to 5)
        self.assertEqual(complexity["<25"]["functions"], 1)  # 10 (equal to 10)
        self.assertEqual(complexity[">=25"]["functions"], 2)  # 25 (equal to 25) and 26
        parameters = result["parameters"]["buckets"]
        self.assertEqual(parameters["<3"]["functions"], 5)  # the five complexity fixtures use 0 parameters
        self.assertEqual(parameters["<5"]["functions"], 1)  # count 3 (equal to 3)
        self.assertEqual(parameters["<7"]["functions"], 1)  # count 5
        self.assertEqual(parameters[">=7"]["functions"], 1)  # count 7 (equal to 7)
        self.assertEqual(result["complexity"]["threshold_status"], "tool_default")

        custom = scorecard_config.load_config(
            make_config(
                self.tmp,
                "[risk_tiers]\ncomplexity = [6, 10, 25]\n",
                name="custom.toml",
            )
        )
        custom_result = scorecard_extended.risk_tiers(functions, custom)
        self.assertEqual(custom_result["complexity"]["threshold_status"], "local_config")
        self.assertEqual(custom_result["own_lines"]["threshold_status"], "tool_default")

    def test_module_index_dynamic_import_violations_and_snapshot_missing(self) -> None:
        repo, shas = make_git_repo(
            [
                {
                    "src/pkg/a.py": "import importlib\ndef load():\n    return importlib.import_module('pkg.b')\n",
                    "src/pkg/b.py": "value = 1\n",
                },
                {
                    "src/pkg/a.py": "import importlib\ndef load():\n    return importlib.import_module('pkg.b')\n",
                    "src/pkg/b.py": "value = 2\n",
                    "index.toml": "[[module]]\nid='A'\nname='A'\nkind='leaf'\npaths=['pkg/a.py']\nmay_depend_on=[]\n[[module]]\nid='B'\nname='B'\nkind='leaf'\npaths=['pkg/b.py']\nmay_depend_on=[]\n[[unassigned]]\npath='pkg/b.py'\n",
                },
            ]
        )
        self.addCleanup(cleanup, repo)
        config_path = self.tmp / "module.toml"
        config_path.write_text(
            '[project]\nname="fixture"\nsource_roots=["src"]\nmodule_prefixes=["pkg"]\n'
            '[module_index]\nrepo_path="index.toml"\npath_base="src"\n'
            '[[snapshots]]\nlabel="old"\nrevision="%s"\n'
            '[[snapshots]]\nlabel="new"\nrevision="%s"\n' % (shas[0], shas[1]),
            encoding="utf-8",
        )
        card = scorecard.build_card(repo, scorecard_config.load_config(config_path))
        old = card["snapshots"]["old"]["metrics"]
        new = card["snapshots"]["new"]["metrics"]
        self.assertEqual(old["module_index"]["status"], "unavailable")
        self.assertEqual(old["module_index"]["reason"], "module index missing")
        self.assertEqual(old["module_tangle"]["import_edges"], 0)
        self.assertEqual(old["module_tangle"]["dynamic_import_edges"], 1)
        self.assertEqual(new["module_index"]["unassigned_count"], 1)
        self.assertEqual(new["module_index"]["runtime_violation_import_statements"], 1)
        self.assertEqual(new["module_index"]["runtime_violation_file_pairs"], 1)
        self.assertEqual(new["module_index"]["runtime_violation_edges"][0]["source"], "dynamic")

    def test_module_index_ca_ce_instability_and_allowed_dependencies(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": "from . import b\n",
                "src/pkg/b.py": "value = 1\n",
                "src/pkg/c.py": "from . import b\n",
            }
        )
        self.addCleanup(cleanup, root)
        metrics = self._measure_with_index(
            root,
            "[[module]]\nid='A'\nkind='leaf'\npaths=['pkg/a.py']\nmay_depend_on=['B']\n"
            "[[module]]\nid='B'\nkind='leaf'\npaths=['pkg/b.py']\nmay_depend_on=[]\n"
            "[[module]]\nid='C'\nkind='leaf'\npaths=['pkg/c.py']\nmay_depend_on=['B']\n",
        )
        module_index = metrics["module_index"]
        self.assertEqual(module_index["runtime_violation_import_statements"], 0)
        self.assertEqual(module_index["runtime_violation_edges"], [])
        leaves = {leaf["id"]: leaf for leaf in module_index["leaf_modules"]}
        self.assertEqual((leaves["A"]["Ca"], leaves["A"]["Ce"], leaves["A"]["I"]), (0, 1, 1.0))
        self.assertEqual((leaves["B"]["Ca"], leaves["B"]["Ce"], leaves["B"]["I"]), (2, 0, 0.0))
        self.assertEqual((leaves["C"]["Ca"], leaves["C"]["Ce"], leaves["C"]["I"]), (0, 1, 1.0))
        markdown = self._markdown(metrics)
        self.assertIn("| 叶子模块 | 名称 | 文件数 | 行数 | Ca | Ce | I |", markdown)
        self.assertIn("| B | B | 1 |", markdown)

    def test_module_index_type_checking_violations_listed_separately(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from . import b\n",
                "src/pkg/b.py": "value = 1\n",
            }
        )
        self.addCleanup(cleanup, root)
        metrics = self._measure_with_index(
            root,
            "[[module]]\nid='A'\nkind='leaf'\npaths=['pkg/a.py']\nmay_depend_on=[]\n"
            "[[module]]\nid='B'\nkind='leaf'\npaths=['pkg/b.py']\nmay_depend_on=[]\n",
        )
        module_index = metrics["module_index"]
        self.assertEqual(module_index["runtime_violation_import_statements"], 0)
        self.assertEqual(module_index["runtime_violation_edges"], [])
        self.assertEqual(module_index["type_checking_violation_import_statements"], 1)
        self.assertEqual(module_index["type_checking_violation_file_pairs"], 1)
        self.assertTrue(module_index["type_checking_violation_edges"][0]["type_checking"])

    def test_module_index_statement_unit_stale_paths_and_unmatched_globs(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": (
                    "from typing import TYPE_CHECKING\n"
                    "from .b import value, other\n"
                    "\n"
                    "if TYPE_CHECKING:\n"
                    "    from . import b\n"
                    "\n"
                    "def load():\n"
                    "    from . import c\n"
                ),
                "src/pkg/b.py": "value = 1\nother = 2\n",
                "src/pkg/c.py": "x = 1\n",
                "src/Dockerfile": "FROM scratch\n",
            }
        )
        self.addCleanup(cleanup, root)
        metrics = self._measure_with_index(
            root,
            "[[module]]\nid='A'\nkind='leaf'\npaths=['pkg/a.py']\nmay_depend_on=[]\n"
            "[[module]]\nid='B'\nkind='leaf'\npaths=['pkg/b.py', 'pkg/b*.py']\nmay_depend_on=[]\n"
            "[[module]]\nid='C'\nkind='leaf'\npaths=['pkg/c.py']\nmay_depend_on=[]\n"
            "[[module]]\nid='D'\nkind='leaf'\npaths=['Dockerfile', 'pkg/missing.py', 'pkg/nomatch*.py']\nmay_depend_on=[]\n",
        )
        module_index = metrics["module_index"]
        # One statement with two imported names counts once; the function-local
        # import is a runtime dependency; the TYPE_CHECKING import is separate.
        self.assertEqual(module_index["runtime_violation_import_statements"], 2)
        self.assertEqual(module_index["runtime_violation_file_pairs"], 2)
        self.assertEqual(module_index["type_checking_violation_import_statements"], 1)
        self.assertEqual(module_index["stale_paths"], ["src/pkg/missing.py"])
        self.assertEqual(module_index["unmatched_py_patterns"], ["src/pkg/nomatch*.py"])

    def test_module_index_rejects_module_list_without_leaf(self) -> None:
        root = make_tree({"src/pkg/a.py": "value = 1\n"})
        self.addCleanup(cleanup, root)
        with self.assertRaisesRegex(ConfigError, "leaf"):
            self._measure_with_index(
                root,
                "[[module]]\nid='G'\nkind='group'\npaths=['pkg/a.py']\n",
            )

    def test_fixed_index_matches_overlapping_leaf_paths(self) -> None:
        root = self.tmp / "tree"
        (root / "src/pkg").mkdir(parents=True)
        (root / "src/pkg/a.py").write_text("value = 1\n", encoding="utf-8")
        index = self.tmp / "index.toml"
        index.write_text(
            "[[module]]\nid='A'\nkind='leaf'\npaths=['pkg/*.py']\n"
            "[[module]]\nid='B'\nkind='leaf'\npaths=['pkg/a.py']\n",
            encoding="utf-8",
        )
        config_path = self.tmp / "fixed.toml"
        config_path.write_text(
            '[project]\nname="fixture"\nsource_roots=["src"]\n'
            '[module_index]\nfixed_file="%s"\npath_base="src"\n' % index,
            encoding="utf-8",
        )
        metrics = scorecard_measure.measure_snapshot(
            root, scorecard_config.load_config(config_path)
        )
        self.assertEqual(metrics["module_index"]["multiple_leaf_files"], ["src/pkg/a.py"])

    def test_hotspots_use_snapshot_end_and_exclude_subjects(self) -> None:
        repo, shas = make_git_repo(
            [
                {"src/a.py": "x = 1\n"},
                {"src/a.py": "x = 2\n", "src/b.py": "y = 1\n"},
            ]
        )
        self.addCleanup(cleanup, repo)
        config_path = make_config(
            self.tmp,
            '[hotspots]\nwindow_days=90\nexclude_subject_regex="commit 1"\n',
        )
        config = scorecard_config.load_config(config_path)
        card = scorecard.build_card(
            repo,
            scorecard_config.Config(
                **{
                    **config.__dict__,
                    "snapshots": (scorecard_config.make_snapshot("second", shas[1]),),
                }
            ),
        )
        hotspots = card["snapshots"]["second"]["metrics"]["hotspots"]
        self.assertEqual(hotspots["status"], "available")
        self.assertEqual(hotspots["commits_in_window"], 1)
        self.assertEqual(next(item for item in hotspots["files"] if item["path"] == "src/a.py")["commits"], 1)
        self.assertIn("--no-renames", hotspots["rename_handling"])

    def test_hotspots_exclude_commits_and_unavailable(self) -> None:
        repo, shas = make_git_repo(
            [
                {"src/a.py": "x = 1\n"},
                {"src/a.py": "x = 2\n"},
            ]
        )
        self.addCleanup(cleanup, repo)
        config = scorecard_config.load_config(
            make_config(
                self.tmp,
                '[hotspots]\nwindow_days=90\nexclude_commits=["%s"]\n' % shas[0],
                name="exclude.toml",
            )
        )
        identity = scorecard.resolve_commit(repo, shas[1])
        hotspots = scorecard_extended.hotspot_metrics(
            repo, identity["sha"], identity["committed_at"], config, {"src/a.py"}, []
        )
        self.assertEqual(hotspots["status"], "available")
        self.assertEqual(hotspots["commits_in_window"], 1)
        self.assertEqual(hotspots["files"][0]["commits"], 1)

        missing = scorecard_extended.hotspot_metrics(
            self.tmp, "HEAD", "2026-01-01T00:00:00+00:00", config, set(), []
        )
        self.assertEqual(missing["status"], "unavailable")

    def test_hotspot_window_boundaries(self) -> None:
        dates = [
            "2026-01-07T23:59:59+00:00",
            "2026-01-08T00:00:01+00:00",
            "2026-01-10T00:00:00+00:00",
        ]
        repo, shas = make_git_repo(
            [
                {"src/a.py": "x = 1\n"},
                {"src/a.py": "x = 2\n"},
                {"src/a.py": "x = 3\n"},
            ],
            dates=dates,
        )
        self.addCleanup(cleanup, repo)
        config = scorecard_config.load_config(
            make_config(self.tmp, "[hotspots]\nwindow_days=2\n", name="window.toml")
        )
        identity = scorecard.resolve_commit(repo, shas[-1])
        hotspots = scorecard_extended.hotspot_metrics(
            repo, identity["sha"], identity["committed_at"], config, {"src/a.py"}, []
        )
        self.assertEqual(hotspots["status"], "available")
        self.assertEqual(hotspots["start"], "2026-01-08T00:00:00+00:00")
        self.assertEqual(hotspots["end"], "2026-01-10T00:00:00+00:00")
        # One second before the window start is excluded; one second inside is
        # included, as is the snapshot commit at the window end.
        self.assertEqual(hotspots["commits_in_window"], 2)
        self.assertEqual(hotspots["files"][0]["commits"], 2)

    def test_metadata_preserves_legacy_definitions_and_labels_proxies(self) -> None:
        config_path = make_config(self.tmp)
        config = scorecard_config.load_config(config_path)
        definitions = scorecard_report.definitions(config)
        metadata = scorecard_report.definition_metadata(config)
        self.assertIsInstance(definitions["complexity.count"], str)
        self.assertEqual(metadata["complexity.count"]["display_name"], "近似圈复杂度：count")
        self.assertEqual(metadata["private_access.count"]["display_name"], "跨文件私有名称访问代理")
        self.assertEqual(metadata["tests.test_lines"]["display_name"], "测试表面代理：test_lines")
        self.assertIn("module_index", definitions)
        self.assertIn("hotspots", definitions)
        self.assertEqual(metadata["giant_files.count"]["source_type"], "project_convention")
        self.assertIn("ASCMM", metadata["giant_files.count"]["source_url"])
        self.assertEqual(metadata["complexity.count"]["source_type"], "research")
        self.assertEqual(metadata["tests.test_lines"]["source_type"], "project_convention")
        self.assertEqual(metadata["fallbacks.legacy_words"]["source_url"], "")
        self.assertEqual(metadata["parse_errors"]["role"], "background")


if __name__ == "__main__":
    unittest.main()
