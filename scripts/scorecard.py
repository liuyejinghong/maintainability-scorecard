"""Maintainability scorecard CLI.

Usage:
    python3 scripts/scorecard.py --repo REPO --config CONFIG \
        --snapshot baseline=REV --snapshot current=REV \
        --json out/scorecard.json --markdown out/scorecard.md
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import scorecard_config
import scorecard_measure
import scorecard_report
from scorecard_extended import hotspot_metrics


class ScorecardError(RuntimeError):
    """Raised for git, export or snapshot failures."""


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ScorecardError(
            "git %s failed (%d): %s"
            % (
                " ".join(args),
                result.returncode,
                result.stderr.decode("utf-8", "replace").strip(),
            )
        )
    return result.stdout


def _git_text(repo: Path, *args: str) -> str:
    return _git(repo, *args).decode("utf-8", "replace")


def _snapshot_path_exists(repo: Path, sha: str, path: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "-e", "%s:%s" % (sha, path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0


def resolve_commit(repo: Path, revision: str) -> dict:
    sha = _git_text(repo, "rev-parse", "--verify", "%s^{commit}" % revision).strip()
    info = (
        _git_text(repo, "show", "-s", "--format=%h%x09%cI%x09%s", sha)
        .rstrip("\n")
        .split("\t", 2)
    )
    return {
        "requested": revision,
        "sha": sha,
        "short": info[0],
        "committed_at": info[1] if len(info) > 1 else "",
        "subject": info[2] if len(info) > 2 else "",
    }


def _extract_tar(data: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        if hasattr(tarfile, "data_filter"):
            archive.extractall(destination, filter="data")
            return
        root = destination.resolve()
        for member in archive.getmembers():
            if not member.isfile():
                continue
            target = (destination / member.name).resolve()
            if root not in target.parents:
                raise ScorecardError("unsafe path in git archive: %s" % member.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def export_snapshot(repo: Path, sha: str, roots: list[str], destination: Path) -> None:
    archive = _git(repo, "archive", "--format=tar", sha, "--", *roots)
    _extract_tar(archive, destination)


def _export_roots(config: scorecard_config.Config) -> list[str]:
    roots: list[str] = []
    for root in config.source_roots + config.test_roots:
        if root not in roots:
            roots.append(root)
    if config.module_index is not None and config.module_index.repo_path:
        root = config.module_index.repo_path
        if root not in roots:
            roots.append(root)
    return roots


def build_card(repo: Path, config: scorecard_config.Config) -> dict:
    if not config.snapshots:
        raise scorecard_config.ConfigError(
            "no snapshots configured; pass --snapshot LABEL=REV or add [[snapshots]]"
        )
    scratch = Path(tempfile.mkdtemp(prefix="maintainability-scorecard-"))
    snapshots: dict[str, dict] = {}
    try:
        roots = _export_roots(config)
        for snapshot in config.snapshots:
            try:
                identity = resolve_commit(repo, snapshot.revision)
                roots = _export_roots(config)
                if config.module_index is not None and config.module_index.repo_path:
                    index_path = config.module_index.repo_path
                    if not _snapshot_path_exists(repo, identity["sha"], index_path):
                        roots = [root for root in roots if root != index_path]
                destination = scratch / snapshot.label
                export_snapshot(repo, identity["sha"], roots, destination)
                metrics, functions, production_files = scorecard_measure.measure_snapshot(
                    destination, config, with_artifacts=True
                )
                metrics["hotspots"] = hotspot_metrics(
                    repo,
                    identity["sha"],
                    identity["committed_at"],
                    config,
                    production_files,
                    functions,
                )
            except (ScorecardError, scorecard_config.ConfigError) as exc:
                raise type(exc)(
                    "snapshot %r (%s): %s" % (snapshot.label, snapshot.revision, exc)
                ) from exc
            snapshots[snapshot.label] = {
                **identity,
                "reason": snapshot.reason,
                "metrics": metrics,
            }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "每个快照用 git archive 导出到临时目录后测量，不 checkout；临时目录测量结束即删除。",
        "config": scorecard_config.config_summary(config),
        "definitions": scorecard_report.definitions(config),
        "definition_metadata": scorecard_report.definition_metadata(config),
        "snapshots": snapshots,
    }


def _parse_snapshot_args(values: list[str] | None) -> tuple[scorecard_config.Snapshot, ...]:
    if not values:
        return ()
    snapshots: list[scorecard_config.Snapshot] = []
    seen: set[str] = set()
    for raw in values:
        label, separator, revision = raw.partition("=")
        if not separator or not label or not revision:
            raise scorecard_config.ConfigError(
                "--snapshot must be LABEL=REV, got %r" % raw
            )
        snapshot = scorecard_config.make_snapshot(label, revision)
        if snapshot.label in seen:
            raise scorecard_config.ConfigError(
                "duplicate snapshot label: %r" % snapshot.label
            )
        seen.add(snapshot.label)
        snapshots.append(snapshot)
    return tuple(snapshots)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure a maintainability scorecard across git snapshots."
    )
    parser.add_argument(
        "--repo", type=Path, default=Path("."), help="repository root (default: .)"
    )
    parser.add_argument("--config", type=Path, required=True, help="TOML or JSON config")
    parser.add_argument(
        "--snapshot",
        action="append",
        metavar="LABEL=REV",
        help="snapshot label and git revision; repeatable; overrides config snapshots",
    )
    parser.add_argument("--json", type=Path, help="write the full card JSON here")
    parser.add_argument(
        "--markdown",
        "--md",
        dest="markdown",
        type=Path,
        help="write the one-page Markdown summary here",
    )
    parser.add_argument(
        "--embed",
        type=Path,
        metavar="HTML",
        help="replace the JSON in an existing <script type=application/json id=...> block",
    )
    parser.add_argument(
        "--embed-id",
        default=None,
        help="script id to replace with --embed (default: scorecard-data)",
    )
    args = parser.parse_args(argv)
    if args.embed_id and not args.embed:
        parser.error("--embed-id requires --embed")

    try:
        config = scorecard_config.load_config(args.config)
        cli_snapshots = _parse_snapshot_args(args.snapshot)
        if cli_snapshots:
            config = replace(config, snapshots=cli_snapshots)
        card = build_card(args.repo.resolve(), config)
        wrote = False
        if args.json:
            _write_text(
                args.json,
                json.dumps(card, ensure_ascii=False, indent=2) + "\n",
            )
            print("wrote %s" % args.json)
            wrote = True
        if args.markdown:
            _write_text(args.markdown, scorecard_report.render_markdown(card))
            print("wrote %s" % args.markdown)
            wrote = True
        if args.embed:
            scorecard_report.embed_json(
                args.embed, args.embed_id or "scorecard-data", card
            )
            print(
                "embedded %s into %s"
                % (args.embed_id or "scorecard-data", args.embed)
            )
            wrote = True
        if not wrote:
            json.dump(card, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        return 0
    except (
        scorecard_config.ConfigError,
        scorecard_report.EmbedError,
        ScorecardError,
    ) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
