from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import cleanup, make_config, make_tree, measure_tree  # noqa: E402


class ScaleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-test-"))
        self.addCleanup(cleanup, self.tmp)

    def test_sloc_excludes_blank_and_comment_only_lines(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": '''
                    """Doc.

                    More doc.
                    """

                    # comment
                    x = 1

                    def f():
                        return x  # trailing comment
                '''
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["scale"]["prod_files"], 1)
        self.assertEqual(metrics["scale"]["prod_sloc"], 6)
        self.assertEqual(metrics["scale"]["prod_physical_lines"], 10)
        self.assertEqual(metrics["scale"]["sloc_tokenize_fallback_files"], 0)

    def test_tokenize_failure_is_counted_and_parse_error_recorded(self) -> None:
        root = make_tree({"src/pkg/broken.py": "def broken(:\n    pass\n"})
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["scale"]["prod_files"], 1)
        self.assertEqual(metrics["scale"]["sloc_tokenize_fallback_files"], 1)
        self.assertEqual(len(metrics["parse_errors"]), 1)
        self.assertEqual(metrics["parse_errors"][0]["path"], "src/pkg/broken.py")

    def test_include_and_exclude_patterns(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": "x = 1\n",
                "src/pkg/generated/b.py": "y = 2\n",
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            """
            [scope]
            include = ["**/*.py"]
            exclude = ["**/generated/**"]
            """,
        )
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["scale"]["prod_files"], 1)


class FunctionMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-test-"))
        self.addCleanup(cleanup, self.tmp)

    def test_giant_functions_complexity_and_nested_own_lines(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": '''
                    def big(x):
                        if x:
                            if x > 1:
                                return 1
                        return 0

                    def small():
                        return 1

                    def outer():
                        def inner():
                            return 2
                        return inner()
                '''
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            """
            [thresholds]
            giant_function_lines = 3
            complexity = 2
            """,
        )
        metrics = measure_tree(root, config)
        giant = metrics["giant_functions"]
        self.assertEqual(giant["count"], 1)
        self.assertEqual(giant["own_lines"], 5)
        self.assertEqual(giant["all_function_own_lines"], 11)
        self.assertEqual(giant["line_share"], 0.4545)
        self.assertEqual(giant["longest"]["qualname"], "big")
        self.assertEqual(giant["longest"]["own_lines"], 5)
        # outer's own_lines excludes the two-line nested inner definition.
        self.assertEqual(metrics["complexity"]["count"], 1)
        self.assertEqual(metrics["complexity"]["sum"], 6)
        self.assertEqual(metrics["complexity"]["top10_share"], 1.0)
        self.assertEqual(metrics["complexity"]["max"]["qualname"], "big")
        self.assertEqual(metrics["complexity"]["max"]["complexity"], 3)

    def test_giant_file_threshold_and_share(self) -> None:
        root = make_tree(
            {
                "src/pkg/big.py": "a = 1\n\nb = 2\nc = 3\n",
                "src/pkg/small.py": "x = 1\ny = 2\n",
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp, "[thresholds]\ngiant_file_lines = 3\n")
        metrics = measure_tree(root, config)
        giant = metrics["giant_files"]
        self.assertEqual(giant["count"], 1)
        self.assertEqual(giant["lines"], 4)
        self.assertEqual(giant["line_share"], 0.6667)

    def test_thresholds_are_strictly_greater_than(self) -> None:
        root = make_tree({"src/pkg/a.py": "def f():\n    return 1\n"})
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            "[thresholds]\ngiant_function_lines = 2\ncomplexity = 1\n",
        )
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["giant_functions"]["count"], 0)
        self.assertEqual(metrics["complexity"]["count"], 0)


class PrivateAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-test-"))
        self.addCleanup(cleanup, self.tmp)

    def test_three_definition_kinds_suppress_same_file_access(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": '''
                    class _Hidden:
                        def _method(self):
                            return 1


                    def _function():
                        return 2


                    _module_private = 3


                    class A:
                        def run(self, obj, cls):
                            self._attr = 1
                            cls._attr = 2
                            obj._attr = 3
                            self._attr
                            obj._attr
                            obj._method()
                            obj._module_private
                            obj._function
                            obj._Hidden
                            obj._missing
                            obj._also_missing = 1
                            obj._also_missing
                            obj.__dunder
                ''',
                "src/pkg/b.py": '''
                    from pkg import a

                    def use():
                        a._method()
                ''',
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        # a.py: self._attr/cls._attr/obj._attr are Attribute assignment
        # targets; _module_private is a Name target; _method/_function/
        # _Hidden are def/class names.  Only obj._missing escapes locally.
        # b.py accesses a._method, whose definition lives in a.py.
        self.assertEqual(metrics["private_access"]["count"], 2)

    def test_name_assignment_target_suppresses_attribute_read(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": '''
                    _shared = 1

                    def f(obj):
                        obj._shared
                '''
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["private_access"]["count"], 0)


class FallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-test-"))
        self.addCleanup(cleanup, self.tmp)

    def test_compat_word_boundaries_and_per_word_counts(self) -> None:
        root = make_tree(
            {
                "src/pkg/words.py": '''
                    compat = 1
                    compat_mode = 2
                    compat_mode_extra = 3
                    xcompat = 4
                    compatible = 5
                    compatibility = 6
                    legacy_path = 7
                    my_legacy = 8
                    legacy2 = 9
                    # legacy in a comment is not counted
                    deprecated = "deprecated"

                    def f(historical_value):
                        return "compat-mode", "compatible", "compatibility"
                '''
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        counts = metrics["fallbacks"]["word_counts"]
        self.assertEqual(counts["compat"], 4)
        self.assertEqual(counts["legacy"], 2)
        self.assertEqual(counts["deprecated"], 2)
        self.assertEqual(counts["historical"], 1)
        self.assertEqual(counts["retired"], 0)
        self.assertEqual(metrics["fallbacks"]["legacy_words"], 9)

    def test_broad_exception_forms(self) -> None:
        root = make_tree(
            {
                "src/pkg/exc.py": '''
                    try:
                        pass
                    except Exception:
                        pass

                    try:
                        pass
                    except (ValueError, Exception):
                        pass

                    try:
                        pass
                    except:
                        pass

                    try:
                        pass
                    except ValueError:
                        pass
                '''
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        fallbacks = metrics["fallbacks"]
        self.assertEqual(fallbacks["except_exception"], 2)
        self.assertEqual(fallbacks["bare_except"], 1)
        self.assertEqual(fallbacks["broad_except_total"], 3)

    def test_versioned_contract_strings_distinct_and_occurrences(self) -> None:
        root = make_tree(
            {
                "src/pkg/contracts.py": '''
                    a = "state/v1"
                    b = "state/v1"
                    c = "other/v2"
                    d = "not a contract"
                    e = "prefix/state/v1"
                '''
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        fallbacks = metrics["fallbacks"]
        self.assertEqual(fallbacks["versioned_contract_strings_distinct"], 2)
        self.assertEqual(fallbacks["versioned_contract_strings_occurrences"], 3)

    def test_contract_strings_deduplicate_across_files(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": 'x = "state/v1"\n',
                "src/pkg/b.py": 'y = "state/v1"\n',
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        fallbacks = metrics["fallbacks"]
        self.assertEqual(fallbacks["versioned_contract_strings_distinct"], 1)
        self.assertEqual(fallbacks["versioned_contract_strings_occurrences"], 2)


class TestMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-test-"))
        self.addCleanup(cleanup, self.tmp)

    def test_test_files_lines_private_access_and_mock_helpers(self) -> None:
        root = make_tree(
            {
                "src/pkg/product.py": "x = 1\n",
                "src/pkg/test_alpha.py": '''
                    from unittest.mock import MagicMock

                    class TestA:
                        def test_x(self):
                            obj = MagicMock()
                            obj._private
                            monkeypatch.setattr("a", "b")
                ''',
                "src/pkg/testsupport/helper.py": '''
                    def helper():
                        return MagicMock
                ''',
                "src/nested/testsupport/helper2.py": "x = 1\n",
                "src/pkg/conftest.py": "import pytest\n",
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp)
        metrics = measure_tree(root, config)
        tests = metrics["tests"]
        self.assertEqual(tests["test_files"], 4)
        self.assertEqual(tests["test_lines"], 11)
        self.assertEqual(tests["private_attr_accesses"], 1)
        self.assertEqual(tests["mock_helpers"], 3)
        self.assertEqual(tests["parse_errors"], [])

    def test_configured_test_root_is_always_test_code(self) -> None:
        root = make_tree(
            {
                "src/pkg/product.py": "x = 1\n",
                "specs/helper.py": "y = 2\n",
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(self.tmp, '[tests]\nroots = ["specs"]\n')
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["tests"]["test_files"], 1)
        self.assertEqual(metrics["scale"]["prod_files"], 1)


class ProjectSpecificTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-test-"))
        self.addCleanup(cleanup, self.tmp)

    def test_shared_state_accesses_and_keys_written_by_three_files(self) -> None:
        root = make_tree(
            {
                "src/pkg/a.py": '''
                    runtime["shared"] = 1
                    runtime.get("shared")
                    rt.pop("x")
                    runtime.setdefault("k", 0)
                    self.runtime["shared"] = 2
                ''',
                "src/pkg/b.py": 'runtime.update({"shared": 1})\n',
                "src/pkg/c.py": "runtime.update(shared=1)\n",
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            """
            [shared_state]
            receiver_names = ["runtime", "rt"]
            receiver_attributes = ["runtime", "_runtime"]
            file_threshold = 3
            """,
        )
        metrics = measure_tree(root, config)
        shared = metrics["shared_state"]
        self.assertEqual(shared["accesses"], 7)
        self.assertEqual(shared["gets"], 1)
        self.assertEqual(shared["subscript_reads"], 0)
        self.assertEqual(shared["writes"], 4)
        self.assertEqual(shared["pops"], 1)
        self.assertEqual(shared["setdefaults"], 1)
        self.assertEqual(shared["key_file_threshold"], 3)
        self.assertEqual(shared["keys_written_by_ge_n_files"], 1)

    def test_write_interface_calls_exclude_adapter_file(self) -> None:
        root = make_tree(
            {
                "src/pkg/adapter.py": '''
                    def signed_post(*args):
                        return args

                    signed_post("adapter")
                ''',
                "src/pkg/use.py": '''
                    client.signed_post("a")
                    client.signed_delete("b")
                    signed_post("c")
                ''',
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            """
            [write_interfaces]
            call_names = ["signed_post", "signed_delete", "_signed_post", "_signed_delete"]
            adapter_files = ["src/pkg/adapter.py"]
            """,
        )
        metrics = measure_tree(root, config)
        writes = metrics["write_interfaces"]
        self.assertEqual(writes["count"], 3)
        self.assertEqual(writes["files"], 1)
        self.assertEqual({site["path"] for site in writes["sites"]}, {"src/pkg/use.py"})

    def test_missing_adapter_file_raises(self) -> None:
        root = make_tree({"src/pkg/use.py": "signed_post('a')\n"})
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            """
            [write_interfaces]
            call_names = ["signed_post"]
            adapter_files = ["src/pkg/missing_adapter.py"]
            """,
        )
        from scorecard_config import ConfigError

        with self.assertRaisesRegex(ConfigError, "adapter file does not exist"):
            measure_tree(root, config)

    def test_custom_counter_regex_and_call_names(self) -> None:
        root = make_tree(
            {
                "src/pkg/custom.py": '''
                    mark_degraded("x")
                    obj._mark_degraded("y")
                    # TODO fix
                    label = "TODO"
                '''
            }
        )
        self.addCleanup(cleanup, root)
        config = make_config(
            self.tmp,
            """
            [[custom_counters]]
            name = "mark_degraded_calls"
            call_names = ["mark_degraded", "_mark_degraded"]

            [[custom_counters]]
            name = "todo_markers"
            regex = "TODO"
            """,
        )
        metrics = measure_tree(root, config)
        self.assertEqual(metrics["custom"]["mark_degraded_calls"], 2)
        self.assertEqual(metrics["custom"]["todo_markers"], 2)


if __name__ == "__main__":
    unittest.main()
