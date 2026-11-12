"""Shared unittest helpers for the maintainability scorecard skill."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import scorecard_config  # noqa: E402
import scorecard_measure  # noqa: E402


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content).lstrip("\n"), encoding="utf-8")


def make_tree(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp(prefix="scorecard-tree-"))
    write_files(root, files)
    return root


def make_config(
    directory: Path,
    extra: str = "",
    source_roots: str = '["src"]',
    module_prefixes: str | None = None,
    name: str = "scorecard.toml",
) -> Path:
    text = '[project]\nname = "fixture"\nsource_roots = %s\n' % source_roots
    if module_prefixes is not None:
        text += "module_prefixes = %s\n" % module_prefixes
    text += "\n" + textwrap.dedent(extra)
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def measure_tree(root: Path, config_path: Path) -> dict:
    config = scorecard_config.load_config(config_path)
    return scorecard_measure.measure_snapshot(root, config)


def run_git(
    repo: Path, *args: str, env: dict[str, str] | None = None
) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(
            "git %s failed: %s"
            % (" ".join(args), result.stderr.decode("utf-8", "replace"))
        )
    return result.stdout.decode("utf-8", "replace")


def make_git_repo(
    commits: list[dict[str, str]], dates: list[str] | None = None
) -> tuple[Path, list[str]]:
    root = Path(tempfile.mkdtemp(prefix="scorecard-repo-"))
    run_git(root, "init", "-q")
    run_git(root, "config", "user.email", "scorecard@example.invalid")
    run_git(root, "config", "user.name", "Scorecard Test")
    shas: list[str] = []
    for index, files in enumerate(commits, 1):
        write_files(root, files)
        run_git(root, "add", "-A")
        env = None
        if dates is not None:
            env = {
                **os.environ,
                "GIT_AUTHOR_DATE": dates[index - 1],
                "GIT_COMMITTER_DATE": dates[index - 1],
            }
        run_git(root, "commit", "-q", "-m", "commit %d" % index, env=env)
        shas.append(run_git(root, "rev-parse", "HEAD").strip())
    return root, shas


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
