"""Optional module-index, risk-tier and Git-hotspot measurements."""

from __future__ import annotations

import ast
import fnmatch
import json
import re
import subprocess
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scorecard_config import (
    DEFAULT_RISK_COMPLEXITY,
    DEFAULT_RISK_FUNCTION_OWN_LINES,
    DEFAULT_RISK_PARAMETERS,
    Config,
    ConfigError,
    ModuleIndexConfig,
)
from scorecard_imports import collect_runtime_import_statements


def _bucket_order(label: str) -> tuple[int, int]:
    if label.startswith(">="):
        return (1, int(label[2:]))
    return (0, int(label[1:]))


def risk_tiers(functions: list[dict], config: Config) -> dict:
    def classify(value: int, thresholds: tuple[int, ...]) -> str:
        for threshold in thresholds:
            if value < threshold:
                return "<%d" % threshold
        return ">=%d" % thresholds[-1]

    dimensions = (
        ("complexity", "complexity", config.risk_complexity),
        ("own_lines", "own_lines", config.risk_own_lines),
        ("parameters", "parameters", config.risk_parameters),
    )
    result: dict[str, Any] = {}
    for name, key, thresholds in dimensions:
        buckets: dict[str, dict[str, int | float]] = {}
        for function in functions:
            value = function[key] if key != "parameters" else function["parameters"]["count"]
            label = classify(value, thresholds)
            bucket = buckets.setdefault(label, {"functions": 0, "own_lines": 0})
            bucket["functions"] += 1
            bucket["own_lines"] += function["own_lines"]
        buckets = dict(sorted(buckets.items(), key=lambda item: _bucket_order(item[0])))
        for bucket in buckets.values():
            bucket["line_share"] = (
                round(bucket["own_lines"] / sum(f["own_lines"] for f in functions), 4)
                if functions and sum(f["own_lines"] for f in functions)
                else 0.0
            )
        result[name] = {
            "thresholds": list(thresholds),
            "threshold_status": (
                "local_config"
                if thresholds != {
                    "complexity": DEFAULT_RISK_COMPLEXITY,
                    "own_lines": DEFAULT_RISK_FUNCTION_OWN_LINES,
                    "parameters": DEFAULT_RISK_PARAMETERS,
                }[name]
                else "tool_default"
            ),
            "buckets": buckets,
        }
    result["parameter_details"] = [
        {
            "path": function["path"],
            "qualname": function["qualname"],
            **function["parameters"],
        }
        for function in functions
    ]
    return result


def _load_index(export_root: Path, index_config: ModuleIndexConfig) -> tuple[dict, str]:
    if index_config.fixed_file:
        path = Path(index_config.fixed_file)
        source = "fixed_file:%s" % path
    else:
        path = export_root / index_config.repo_path
        source = "snapshot:%s" % index_config.repo_path
    if not path.is_file():
        return {}, source
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("cannot read module index %s: %s" % (path, exc)) from exc
    if not isinstance(data.get("module", []), list) or not isinstance(data.get("unassigned", []), list):
        raise ConfigError("module index must contain [[module]] and [[unassigned]] arrays")
    return data, source


def _normalise_index_path(raw: str, base: str) -> str:
    value = raw.lstrip("./")
    if not base:
        return value
    prefix = base.rstrip("/") + "/"
    return value if value == base or value.startswith(prefix) else prefix + value


def _index_paths(record: dict, base: str = "") -> list[str]:
    raw = record.get("paths", [])
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        values = raw
    else:
        raise ConfigError("module index paths must be a string or array of strings")
    return [_normalise_index_path(value, base) for value in values]


def _module_for(path: str, modules: list[dict], base: str) -> list[str]:
    matches: set[str] = set()
    for module in modules:
        for pattern in _index_paths(module, base):
            full_pattern = pattern
            if fnmatch.fnmatchcase(path, full_pattern):
                matches.add(str(module.get("id", "")))
    return sorted(matches)


def _has_glob_magic(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")


def module_index_metrics(
    export_root: Path,
    config: Config,
    production: dict[str, str],
    trees: dict[str, ast.AST],
    module_graph: dict,
    module_info: dict[str, tuple[str, bool]],
    module_map: dict[str, str],
) -> dict:
    if config.module_index is None:
        return {}
    index, index_source = _load_index(export_root, config.module_index)
    if not index:
        return {"status": "unavailable", "reason": "module index missing", "source": index_source}
    all_modules = index["module"]
    modules = [item for item in all_modules if item.get("kind") == "leaf"]
    if all_modules and not modules:
        raise ConfigError(
            'module index declares [[module]] entries but no kind = "leaf" module'
        )
    active_ids = {str(module.get("id")) for module in modules}
    if any("id" not in item or "paths" not in item for item in modules):
        raise ConfigError("module-index leaf entries require id and paths")

    assigned: dict[str, list[str]] = {}
    for path in production:
        assigned[path] = _module_for(path, modules, config.module_index.path_base)
    explicit_unassigned = {
        _normalise_index_path(str(item.get("path", "")), config.module_index.path_base)
        for item in index.get("unassigned", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    unassigned = sorted(
        path
        for path in production
        if (not assigned[path] and path not in explicit_unassigned)
        or path in explicit_unassigned
    )
    multiple = sorted(path for path, owners in assigned.items() if len(owners) > 1)

    modules_by_id = {str(item["id"]): item for item in modules}
    edge_rows: list[dict] = []
    graph_edges = list(module_graph.get("edge_details", []))
    for edge in graph_edges:
        source_owners = assigned.get(edge["from"], [])
        target_owners = assigned.get(edge["to"], [])
        for source_id in source_owners:
            for target_id in target_owners:
                edge_rows.append({**edge, "source": edge.get("source", "dynamic"), "from_module": source_id, "to_module": target_id})
    module_edges = {
        (row["from_module"], row["to_module"])
        for row in edge_rows
        if row["from_module"] != row["to_module"]
    }
    dependents: dict[str, set[str]] = {module_id: set() for module_id in active_ids}
    dependencies: dict[str, set[str]] = {module_id: set() for module_id in active_ids}
    for source_id, target_id in module_edges:
        dependents[target_id].add(source_id)
        dependencies[source_id].add(target_id)
    runtime_rows: list[dict] = []
    type_checking_rows: list[dict] = []
    runtime_statements: set[tuple[str, int, str, str]] = set()
    type_checking_statements: set[tuple[str, int, str, str]] = set()
    runtime_file_pairs: set[tuple[str, str]] = set()
    type_checking_file_pairs: set[tuple[str, str]] = set()
    for statement in collect_runtime_import_statements(
        trees, module_info, module_map, config.module_prefixes
    ):
        source_owners = assigned.get(statement["path"], [])
        if len(source_owners) != 1:
            continue
        from_module = source_owners[0]
        allowed = set(
            str(item) for item in modules_by_id[from_module].get("may_depend_on", [])
        )
        targets_by_module: dict[str, list[str]] = {}
        for target in statement["targets"]:
            target_owners = assigned.get(target, [])
            if len(target_owners) != 1:
                continue
            to_module = target_owners[0]
            if to_module == from_module or to_module in allowed:
                continue
            targets_by_module.setdefault(to_module, []).append(target)
        for to_module in sorted(targets_by_module):
            targets = targets_by_module[to_module]
            key = (statement["path"], statement["lineno"], from_module, to_module)
            row = {
                "from": statement["path"],
                "to": targets[0],
                "from_module": from_module,
                "to_module": to_module,
                "lineno": statement["lineno"],
                "source": statement["source"],
                "type_checking": statement["type_checking"],
            }
            pairs = {(statement["path"], target) for target in targets}
            if statement["type_checking"]:
                if key not in type_checking_statements:
                    type_checking_statements.add(key)
                    type_checking_file_pairs.update(pairs)
                    type_checking_rows.append(row)
            elif key not in runtime_statements:
                runtime_statements.add(key)
                runtime_file_pairs.update(pairs)
                runtime_rows.append(row)
    runtime_rows.sort(
        key=lambda item: (item["from"], item["lineno"], item["to_module"], item["to"])
    )
    type_checking_rows.sort(
        key=lambda item: (item["from"], item["lineno"], item["to_module"], item["to"])
    )

    leaf_results: list[dict] = []
    for module in sorted(modules, key=lambda item: str(item["id"])):
        module_id = str(module["id"])
        paths = sorted(path for path, owners in assigned.items() if module_id in owners)
        lines = sum(len(production[path].splitlines()) for path in paths)
        ca, ce = len(dependents[module_id]), len(dependencies[module_id])
        leaf_results.append(
            {
                "id": module_id,
                "name": module.get("name", module_id),
                "files": len(paths),
                "lines": lines,
                "Ca": ca,
                "Ce": ce,
                "I": round(ce / (ca + ce), 4) if ca + ce else None,
            }
        )
    archive_files = {
        path.relative_to(export_root).as_posix()
        for path in export_root.rglob("*")
        if path.is_file()
    }
    production_paths = set(production)
    stale_paths: list[str] = []
    unmatched_py_patterns: list[str] = []
    for module in all_modules:
        for pattern in _index_paths(module, config.module_index.path_base):
            if _has_glob_magic(pattern):
                if not any(
                    fnmatch.fnmatchcase(path, pattern) for path in production_paths
                ):
                    unmatched_py_patterns.append(pattern)
            elif pattern not in archive_files:
                stale_paths.append(pattern)
    for item in index.get("unassigned", []):
        raw = item.get("path", "") if isinstance(item, dict) else ""
        raw = _normalise_index_path(raw, config.module_index.path_base)
        if raw and raw not in archive_files:
            stale_paths.append(raw)
    unique_edges = sorted(module_edges)
    return {
        "status": "available",
        "source": index_source,
        "unassigned_files": unassigned,
        "unassigned_count": len(unassigned),
        "explicit_unassigned_files": sorted(path for path in production if path in explicit_unassigned),
        "multiple_leaf_files": multiple,
        "stale_paths": sorted(set(stale_paths)),
        "unmatched_py_patterns": sorted(set(unmatched_py_patterns)),
        "leaf_modules": leaf_results,
        "runtime_violation_import_statements": len(runtime_statements),
        "runtime_violation_file_pairs": len(runtime_file_pairs),
        "runtime_violation_edges": runtime_rows,
        "type_checking_violation_import_statements": len(type_checking_statements),
        "type_checking_violation_file_pairs": len(type_checking_file_pairs),
        "type_checking_violation_edges": type_checking_rows,
        "runtime_edge_count": len(unique_edges),
    }


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise ConfigError("git %s failed: %s" % (" ".join(args), result.stderr.decode("utf-8", "replace").strip()))
    return result.stdout


def hotspot_metrics(
    repo: Path,
    sha: str,
    committed_at: str,
    config: Config,
    production_files: set[str],
    functions: list[dict],
) -> dict:
    try:
        end = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
        start = end - timedelta(days=config.hotspots.window_days)
        raw = _git(
            repo,
            "log",
            "--no-renames",
            "--since=%s" % start.isoformat(),
            "--until=%s" % end.isoformat(),
            "--format=%H%x09%s",
            "--numstat",
            sha,
            "--",
            *config.source_roots,
        ).decode("utf-8", "replace")
    except (ValueError, ConfigError):
        return {"status": "unavailable", "reason": "git history unavailable"}
    excluded_commits = set(config.hotspots.exclude_commits)
    subject_pattern = re.compile(config.hotspots.exclude_subject_regex) if config.hotspots.exclude_subject_regex else None
    counts: dict[str, dict[str, int]] = {}
    current_files = set(production_files)
    commit_id = ""
    commit_subject = ""
    available_commits = 0
    for line in raw.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) == 2 and len(fields[0]) >= 7 and all(char in "0123456789abcdefABCDEF" for char in fields[0]):
            commit_id, commit_subject = fields
            excluded = commit_id in excluded_commits or any(
                commit_id.startswith(value) or value.startswith(commit_id)
                for value in excluded_commits
            )
            if not excluded and not (subject_pattern and subject_pattern.search(commit_subject)):
                available_commits += 1
            continue
        if len(fields) != 3:
            continue
        excluded = commit_id in excluded_commits or any(
            commit_id.startswith(value) or value.startswith(commit_id)
            for value in excluded_commits
        )
        if excluded:
            continue
        if subject_pattern and subject_pattern.search(commit_subject):
            continue
        added, removed, path = fields
        if path not in current_files or added == "-" or removed == "-":
            continue
        metric = counts.setdefault(path, {"commits": 0, "additions": 0, "deletions": 0})
        metric["commits"] += 1
        metric["additions"] += int(added)
        metric["deletions"] += int(removed)
    if available_commits == 0:
        return {"status": "unavailable", "reason": "no commits in configured history window"}
    complexity_by_file: dict[str, int] = {}
    longest_by_file: dict[str, int] = {}
    for function in functions:
        complexity_by_file[function["path"]] = complexity_by_file.get(function["path"], 0) + function["complexity"]
        longest_by_file[function["path"]] = max(longest_by_file.get(function["path"], 0), function["own_lines"])
    files = []
    for path in sorted(current_files):
        churn = counts.get(path, {"commits": 0, "additions": 0, "deletions": 0})
        files.append(
            {
                "path": path,
                **churn,
                "complexity_sum": complexity_by_file.get(path, 0),
                "longest_function_own_lines": longest_by_file.get(path, 0),
            }
        )
    ranked = sorted(files, key=lambda item: (-item["commits"], -(item["additions"] + item["deletions"]), item["path"]))
    return {
        "status": "available",
        "window_days": config.hotspots.window_days,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rename_handling": "--no-renames; rename is represented as delete/add",
        "commits_in_window": available_commits,
        "files": ranked,
    }
