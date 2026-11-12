"""Select Python files, parse them and aggregate one snapshot's metrics."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scorecard_ast import (
    WordCounter,
    code_line_count,
    collect_functions,
    custom_counter_metrics,
    fallback_metrics,
    private_access_count,
    shared_state_metrics,
    test_metrics,
    write_interface_sites,
)
from scorecard_config import Config, ConfigError
from scorecard_imports import analyze_import_graph, build_module_index
from scorecard_extended import hotspot_metrics, module_index_metrics, risk_tiers


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    parts: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            if pattern[index:index + 2] == "**":
                if pattern[index:index + 3] == "**/":
                    parts.append("(?:.*/)?")
                    index += 3
                    continue
                parts.append(".*")
                index += 2
                continue
            parts.append("[^/]*")
            index += 1
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    parts.append("$")
    return re.compile("".join(parts))


def matches_glob(path: str, pattern: str) -> bool:
    """Match a root-relative POSIX path; no slash in the pattern means basename."""
    target = path if "/" in pattern else path.rsplit("/", 1)[-1]
    return bool(_glob_to_regex(pattern).match(target))


def is_test_path(relative_path: str, patterns: tuple[str, ...]) -> bool:
    return any(matches_glob(relative_path, pattern) for pattern in patterns)


def _excluded(relative_path: str, patterns: tuple[str, ...]) -> bool:
    return any(matches_glob(relative_path, pattern) for pattern in patterns)


def _require_dir(export_root: Path, root: str, kind: str) -> Path:
    base = export_root / root
    if not base.is_dir():
        raise ConfigError("%s is not a directory in the snapshot: %s" % (kind, root))
    return base


def collect_python_files(
    export_root: Path, config: Config
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (production, tests) mapping repository-relative path to text."""
    production_candidates: dict[str, tuple[str, str]] = {}
    test_paths: set[str] = set()

    for root in config.source_roots:
        base = _require_dir(export_root, root, "source root")
        for path in sorted(base.rglob("*.py")):
            if not path.is_file():
                continue
            repo_relative = path.relative_to(export_root).as_posix()
            root_relative = path.relative_to(base).as_posix()
            if _excluded(root_relative, config.exclude):
                continue
            if is_test_path(root_relative, config.test_patterns):
                test_paths.add(repo_relative)
                continue
            if not any(matches_glob(root_relative, pattern) for pattern in config.include):
                continue
            existing = production_candidates.get(repo_relative)
            if existing is None or len(root) > len(existing[0]):
                production_candidates[repo_relative] = (root, root_relative)

    for root in config.test_roots:
        base = _require_dir(export_root, root, "test root")
        for path in sorted(base.rglob("*.py")):
            if not path.is_file():
                continue
            repo_relative = path.relative_to(export_root).as_posix()
            root_relative = path.relative_to(base).as_posix()
            if _excluded(root_relative, config.exclude):
                continue
            test_paths.add(repo_relative)

    for path in test_paths:
        production_candidates.pop(path, None)

    production: dict[str, str] = {}
    for path in sorted(production_candidates):
        production[path] = (export_root / path).read_text(encoding="utf-8", errors="replace")
    tests: dict[str, str] = {}
    for path in sorted(test_paths):
        tests[path] = (export_root / path).read_text(encoding="utf-8", errors="replace")
    return production, tests


def validate_adapter_files(export_root: Path, config: Config) -> None:
    if config.write_interfaces is None:
        return
    for adapter in config.write_interfaces.adapter_files:
        if not (export_root / adapter).is_file():
            raise ConfigError(
                "write_interfaces.adapter_files: adapter file does not exist in the "
                "snapshot: %s" % adapter
            )


def measure_snapshot(
    export_root: Path, config: Config, *, with_artifacts: bool = False
):
    """Measure one snapshot.

    With ``with_artifacts=True`` return ``(metrics, functions, production_paths)``
    so callers that also need hotspot inputs do not re-parse the same AST.
    """
    production, tests = collect_python_files(export_root, config)
    validate_adapter_files(export_root, config)

    prod_sizes = {path: len(text.splitlines()) for path, text in production.items()}
    trees: dict[str, ast.AST] = {}
    parse_errors: list[dict] = []
    sloc_total = 0
    sloc_fallback_files = 0
    for path, text in production.items():
        count, tokenized = code_line_count(text)
        sloc_total += count
        if not tokenized:
            sloc_fallback_files += 1
        try:
            trees[path] = ast.parse(text, filename=path)
        except (SyntaxError, ValueError) as exc:
            parse_errors.append({"path": path, "error": str(exc)})

    functions = collect_functions(trees)
    own_total = sum(function["own_lines"] for function in functions)
    giant_functions = [
        function
        for function in functions
        if function["own_lines"] > config.giant_function_lines
    ]
    giant_own = sum(function["own_lines"] for function in giant_functions)
    longest_function = max(
        functions,
        key=lambda function: (function["own_lines"], function["span_lines"]),
        default=None,
    )

    complexity_total = sum(function["complexity"] for function in functions)
    complexity_order = sorted(
        functions,
        key=lambda function: (-function["complexity"], function["path"], function["lineno"]),
    )
    top10 = complexity_order[:10]
    top10_total = sum(function["complexity"] for function in top10)
    most_complex = top10[0] if top10 else None

    giant_files = [
        size for size in prod_sizes.values() if size > config.giant_file_lines
    ]
    prod_physical_total = sum(prod_sizes.values())

    module_info, module_map = build_module_index(
        sorted(production), config.source_roots
    )
    module_tangle = analyze_import_graph(
        trees, module_info, module_map, config.module_prefixes
    )

    private_accesses = sum(private_access_count(tree) for tree in trees.values())

    word_counter = WordCounter(config.fallback_words)
    contract_regex = re.compile(config.contract_pattern)
    word_counts = {word: 0 for word in config.fallback_words}
    except_exception = 0
    bare_except = 0
    contract_values: set[str] = set()
    contract_occurrences = 0
    for tree in trees.values():
        file_fallbacks = fallback_metrics(tree, word_counter, contract_regex)
        for word, amount in file_fallbacks["word_counts"].items():
            word_counts[word] += amount
        except_exception += file_fallbacks["except_exception"]
        bare_except += file_fallbacks["bare_except"]
        contract_values.update(file_fallbacks["contract_strings"])
        contract_occurrences += file_fallbacks[
            "versioned_contract_strings_occurrences"
        ]

    metrics: dict = {
        "scale": {
            "prod_files": len(production),
            "prod_sloc": sloc_total,
            "prod_physical_lines": prod_physical_total,
            "sloc_tokenize_fallback_files": sloc_fallback_files,
        },
        "giant_functions": {
            "count": len(giant_functions),
            "own_lines": giant_own,
            "all_function_own_lines": own_total,
            "line_share": round(giant_own / own_total, 4) if own_total else 0.0,
            "longest": (
                {
                    "path": longest_function["path"],
                    "qualname": longest_function["qualname"],
                    "own_lines": longest_function["own_lines"],
                }
                if longest_function
                else None
            ),
        },
        "complexity": {
            "count": sum(
                function["complexity"] > config.complexity_threshold
                for function in functions
            ),
            "sum": complexity_total,
            "top10_sum": top10_total,
            "top10_share": (
                round(top10_total / complexity_total, 4)
                if complexity_total
                else 0.0
            ),
            "max": (
                {
                    "path": most_complex["path"],
                    "qualname": most_complex["qualname"],
                    "complexity": most_complex["complexity"],
                }
                if most_complex
                else None
            ),
        },
        "giant_files": {
            "count": len(giant_files),
            "lines": sum(giant_files),
            "line_share": (
                round(sum(giant_files) / prod_physical_total, 4)
                if prod_physical_total
                else 0.0
            ),
        },
        "module_tangle": module_tangle,
        "private_access": {"count": private_accesses},
        "fallbacks": {
            "legacy_words": sum(word_counts.values()),
            "word_counts": word_counts,
            "versioned_contract_strings_distinct": len(contract_values),
            "versioned_contract_strings_occurrences": contract_occurrences,
            "except_exception": except_exception,
            "bare_except": bare_except,
            "broad_except_total": except_exception + bare_except,
        },
        "tests": test_metrics(tests, config.mock_helpers),
        "parse_errors": parse_errors,
        "risk_tiers": risk_tiers(functions, config),
    }
    if config.module_index is not None:
        metrics["module_index"] = module_index_metrics(
            export_root, config, production, trees, module_tangle, module_info, module_map
        )
    if config.shared_state is not None:
        metrics["shared_state"] = shared_state_metrics(trees, config.shared_state)
    if config.write_interfaces is not None:
        metrics["write_interfaces"] = write_interface_sites(
            trees, config.write_interfaces
        )
    if config.custom_counters:
        metrics["custom"] = custom_counter_metrics(
            production, trees, config.custom_counters
        )
    if with_artifacts:
        return metrics, functions, set(production)
    return metrics
