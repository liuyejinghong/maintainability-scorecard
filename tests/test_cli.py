from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import SCRIPTS, cleanup, make_git_repo, run_git  # noqa: E402

SCRIPT = SCRIPTS / "scorecard.py"
BASE_COMMITS = [
    {
        "src/pkg/a.py": """
            def one():
                return 1
        """,
        "src/pkg/adapter.py": """
            def signed_post(*args):
                return args
        """,
    },
    {
        "src/pkg/b.py": """
            def two():
                return 2
        """,
        "src/pkg/use.py": """
            def use(client):
                client.signed_post("x")
                mark_degraded("y")
        """,
    },
]
FULL_CONFIG = """
[project]
name = "fixture"
source_roots = ["src"]

[shared_state]
receiver_names = ["runtime"]
file_threshold = 3

[write_interfaces]
call_names = ["signed_post"]
adapter_files = ["src/pkg/adapter.py"]

[[custom_counters]]
name = "mark_degraded_calls"
call_names = ["mark_degraded"]
"""


class CliEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="scorecard-cli-"))
        self.addCleanup(cleanup, self.tmp)
        self.repo, self.shas = make_git_repo(BASE_COMMITS)
        self.addCleanup(cleanup, self.repo)
        self.config = self.tmp / "scorecard.toml"
        self.config.write_text(FULL_CONFIG, encoding="utf-8")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(self.repo), "--config", str(self.config), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def test_archive_two_snapshots_json_and_markdown(self) -> None:
        out_json = self.tmp / "out" / "scorecard.json"
        out_md = self.tmp / "out" / "scorecard.md"
        before_status = run_git(self.repo, "status", "--porcelain")
        before_head = run_git(self.repo, "rev-parse", "HEAD").strip()
        result = self._run(
            "--snapshot",
            "first=%s" % self.shas[0],
            "--snapshot",
            "second=%s" % self.shas[1],
            "--json",
            str(out_json),
            "--markdown",
            str(out_md),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("wrote", result.stdout)
        card = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(card["schema_version"], 1)
        self.assertEqual(list(card["snapshots"]), ["first", "second"])
        first = card["snapshots"]["first"]
        second = card["snapshots"]["second"]
        self.assertEqual(first["sha"], self.shas[0])
        self.assertEqual(second["sha"], self.shas[1])
        self.assertTrue(first["committed_at"])
        self.assertEqual(first["metrics"]["scale"]["prod_files"], 2)
        self.assertEqual(second["metrics"]["scale"]["prod_files"], 4)
        self.assertEqual(second["metrics"]["write_interfaces"]["count"], 1)
        self.assertEqual(second["metrics"]["custom"]["mark_degraded_calls"], 1)
        markdown = out_md.read_text(encoding="utf-8")
        for text in (
            "# 可维护性记分卡",
            "| first |",
            "| second |",
            "共享状态访问次数",
            "直接外部写接口调用点",
            "自定义计数：mark_degraded_calls",
            "导入图最大 SCC（全部边）",
        ):
            with self.subTest(text=text):
                self.assertIn(text, markdown)
        self.assertEqual(run_git(self.repo, "status", "--porcelain"), before_status)
        self.assertEqual(run_git(self.repo, "rev-parse", "HEAD").strip(), before_head)

    def test_config_snapshots_and_cli_override(self) -> None:
        self.config.write_text(
            FULL_CONFIG
            + '\n[[snapshots]]\nlabel = "cfg"\nrevision = "%s"\n' % self.shas[0],
            encoding="utf-8",
        )
        out = self.tmp / "cfg.json"
        result = self._run("--json", str(out))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(json.loads(out.read_text(encoding="utf-8"))["snapshots"]), ["cfg"])
        out2 = self.tmp / "cli.json"
        result = self._run(
            "--snapshot", "cli=%s" % self.shas[1], "--json", str(out2)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(json.loads(out2.read_text(encoding="utf-8"))["snapshots"]), ["cli"])

    def test_missing_adapter_file_is_a_hard_error(self) -> None:
        self.config.write_text(
            FULL_CONFIG.replace(
                'adapter_files = ["src/pkg/adapter.py"]',
                'adapter_files = ["src/pkg/missing_adapter.py"]',
            ),
            encoding="utf-8",
        )
        result = self._run("--snapshot", "only=%s" % self.shas[0])
        self.assertEqual(result.returncode, 2)
        self.assertIn("adapter file does not exist", result.stderr)
        self.assertIn("snapshot 'only'", result.stderr)

    def test_embed_replaces_existing_block(self) -> None:
        html = self.tmp / "index.html"
        html.write_text(
            '<html><script type="application/json" id="scorecard-data">{"old":1}</script></html>',
            encoding="utf-8",
        )
        result = self._run(
            "--snapshot", "only=%s" % self.shas[1], "--embed", str(html)
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = html.read_text(encoding="utf-8")
        self.assertNotIn('{"old":1}', updated)
        self.assertIn('"snapshots"', updated)
        self.assertTrue(updated.startswith("<html><script"))

    def test_stdout_json_without_output_options(self) -> None:
        result = self._run("--snapshot", "only=%s" % self.shas[0])
        self.assertEqual(result.returncode, 0, result.stderr)
        card = json.loads(result.stdout)
        self.assertEqual(list(card["snapshots"]), ["only"])


if __name__ == "__main__":
    unittest.main()
