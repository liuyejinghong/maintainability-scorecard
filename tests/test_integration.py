from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import cleanup, make_git_repo  # noqa: E402

import scorecard  # noqa: E402
import scorecard_config  # noqa: E402

COMMITS = [
    {
        "src/pkg/a.py": '''
            """compat docstring."""
            from typing import TYPE_CHECKING

            if TYPE_CHECKING:
                from pkg import b


            def _helper():
                return 1


            def big(x):
                if x:
                    if x > 1:
                        return 1
                return 0


            def use(obj, client):
                obj._cross
                client.send("x")
                from pkg import b
                runtime["k"] = 1
                try:
                    pass
                except Exception:
                    pass
                try:
                    pass
                except:
                    pass
                return big(1)


            kind = "state/v1"
            note = "compatible"
        ''',
        "src/pkg/b.py": '''
            from pkg import a

            runtime["k"] = 2
        ''',
        "src/pkg/adapter.py": '''
            def send(*args):
                return args

            send("adapter")
        ''',
        "src/pkg/test_a.py": '''
            from unittest.mock import MagicMock

            def test_one():
                obj = MagicMock()
                obj._private
        ''',
    },
    {
        "src/pkg/c.py": '''
            def huge():
                if True:
                    return 1
                return 0

            legacy_path = 1
            # TODO remove compatibility shim
            runtime["m"] = 3
        ''',
    },
]


class GitFixtureIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-integration-"))
        self.addCleanup(cleanup, self.tmp)
        self.repo, self.shas = make_git_repo(COMMITS)
        self.addCleanup(cleanup, self.repo)

    def test_every_metric_on_a_two_commit_repository(self) -> None:
        config_path = self.tmp / "scorecard.toml"
        config_path.write_text(
            """
            [project]
            name = "integration"
            source_roots = ["src"]

            [thresholds]
            giant_function_lines = 3
            complexity = 2
            giant_file_lines = 5

            [shared_state]
            receiver_names = ["runtime"]
            file_threshold = 2

            [write_interfaces]
            call_names = ["send"]
            adapter_files = ["src/pkg/adapter.py"]

            [[custom_counters]]
            name = "todo_markers"
            regex = "TODO"

            [[snapshots]]
            label = "first"
            revision = "%s"
            reason = "first commit"

            [[snapshots]]
            label = "second"
            revision = "%s"
            reason = "second commit"
            """
            % (self.shas[0], self.shas[1]),
            encoding="utf-8",
        )
        config = scorecard_config.load_config(config_path)
        card = scorecard.build_card(self.repo, config)
        first = card["snapshots"]["first"]["metrics"]
        second = card["snapshots"]["second"]["metrics"]

        self.assertEqual(card["snapshots"]["first"]["reason"], "first commit")
        self.assertEqual(first["scale"]["prod_files"], 3)
        self.assertEqual(second["scale"]["prod_files"], 4)
        self.assertEqual(first["giant_functions"]["count"], 2)
        self.assertEqual(second["giant_functions"]["count"], 3)
        self.assertEqual(first["giant_files"]["count"], 1)
        self.assertEqual(first["complexity"]["count"], 2)
        self.assertEqual(first["module_tangle"]["import_edges"], 2)
        self.assertEqual(first["module_tangle"]["max_scc_size"], 2)
        self.assertEqual(
            first["module_tangle"]["max_scc_size_excluding_type_checking_edges"], 2
        )
        self.assertEqual(first["module_tangle"]["max_scc_size_top_level_imports"], 1)
        self.assertEqual(first["module_tangle"]["function_local_imports"], 1)
        self.assertEqual(first["private_access"]["count"], 1)
        self.assertEqual(first["fallbacks"]["except_exception"], 1)
        self.assertEqual(first["fallbacks"]["bare_except"], 1)
        self.assertEqual(first["fallbacks"]["broad_except_total"], 2)
        self.assertEqual(first["fallbacks"]["word_counts"]["compat"], 1)
        self.assertEqual(first["fallbacks"]["word_counts"]["legacy"], 0)
        self.assertEqual(second["fallbacks"]["word_counts"]["legacy"], 1)
        self.assertEqual(first["fallbacks"]["versioned_contract_strings_distinct"], 1)
        self.assertEqual(first["fallbacks"]["versioned_contract_strings_occurrences"], 1)
        self.assertEqual(first["tests"]["test_files"], 1)
        self.assertEqual(first["tests"]["test_lines"], 5)
        self.assertEqual(first["tests"]["private_attr_accesses"], 1)
        self.assertEqual(first["tests"]["mock_helpers"], 1)
        self.assertEqual(first["shared_state"]["accesses"], 2)
        self.assertEqual(first["shared_state"]["keys_written_by_ge_n_files"], 1)
        self.assertEqual(second["shared_state"]["accesses"], 3)
        self.assertEqual(first["write_interfaces"]["count"], 1)
        self.assertEqual(first["custom"]["todo_markers"], 0)
        self.assertEqual(second["custom"]["todo_markers"], 1)
        self.assertEqual(first["parse_errors"], [])
        self.assertEqual(second["parse_errors"], [])


if __name__ == "__main__":
    unittest.main()
