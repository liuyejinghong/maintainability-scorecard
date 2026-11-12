from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import cleanup, make_config, make_tree, measure_tree  # noqa: E402


class ImportGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-test-"))
        self.addCleanup(cleanup, self.tmp)

    def test_three_scc_views(self) -> None:
        root = make_tree(
            {
                "src/pkg/t1.py": '''
                    import typing

                    if typing.TYPE_CHECKING:
                        from pkg import t2
                ''',
                "src/pkg/t2.py": "from pkg import t3\n",
                "src/pkg/t3.py": "from pkg import t1\n",
                "src/pkg/t4.py": '''
                    from typing import TYPE_CHECKING

                    if TYPE_CHECKING:
                        from pkg import t5
                ''',
                "src/pkg/t5.py": "from pkg import t4\n",
                "src/pkg/x.py": '''
                    def f():
                        from . import y
                ''',
                "src/pkg/y.py": '''
                    def g():
                        from . import x
                ''',
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        tangle = metrics["module_tangle"]
        # All edges: t1->t2->t3->t1 is a 3-cycle; t4<->t5 and x<->y are 2-cycles.
        self.assertEqual(tangle["import_edges"], 7)
        self.assertEqual(tangle["max_scc_size"], 3)
        # Removing TYPE_CHECKING edges leaves only the x<->y 2-cycle.
        self.assertEqual(tangle["import_edges_excluding_type_checking"], 5)
        self.assertEqual(tangle["max_scc_size_excluding_type_checking_edges"], 2)
        # Only top-level load-time edges leaves no cycle at all.
        self.assertEqual(tangle["top_level_import_edges"], 3)
        self.assertEqual(tangle["max_scc_size_top_level_imports"], 1)
        self.assertEqual(tangle["function_local_imports"], 2)

    def test_module_prefix_resolution(self) -> None:
        root = make_tree(
            {
                "src/mypkg/a.py": '',
                "src/mypkg/b.py": "from mypkg import a\n",
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            source_roots='["src/mypkg"]',
            module_prefixes='["mypkg"]',
        )
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["module_tangle"]["import_edges"], 1)
        self.assertEqual(metrics["module_tangle"]["max_scc_size"], 1)

    def test_acyclic_graph_has_scc_one(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": "from pkg import b\n",
                "src/pkg/b.py": "",
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["module_tangle"]["max_scc_size"], 1)
        self.assertEqual(metrics["module_tangle"]["max_scc_size_top_level_imports"], 1)


if __name__ == "__main__":
    unittest.main()
