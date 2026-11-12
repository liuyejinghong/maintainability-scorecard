"""Configuration loading and validation for the maintainability scorecard.

The configuration is a plain JSON object or TOML document.  Unknown keys are
rejected so a typo cannot silently disable a metric (in particular an adapter
exclusion list).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_WORDS = ("legacy", "compat", "retired", "historical", "deprecated")
DEFAULT_TEST_PATTERNS = (
    "test_*.py",
    "conftest.py",
    "*_test_support.py",
    "**/testsupport/**",
)
DEFAULT_MOCK_HELPERS = ("MagicMock", "monkeypatch", "SimpleNamespace")
DEFAULT_CONTRACT_PATTERN = r"[A-Za-z0-9_.-]+/v[0-9]+"
DEFAULT_GIANT_FUNCTION_LINES = 150
DEFAULT_COMPLEXITY_THRESHOLD = 20
DEFAULT_GIANT_FILE_LINES = 500
DEFAULT_RISK_COMPLEXITY = (5, 10, 25)
DEFAULT_RISK_FUNCTION_OWN_LINES = (100, 150)
DEFAULT_RISK_PARAMETERS = (3, 5, 7)
DEFAULT_HOTSPOT_WINDOW_DAYS = 90

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_COUNTER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class ConfigError(ValueError):
    """Raised when the configuration is missing, malformed or inconsistent."""


@dataclass(frozen=True)
class Snapshot:
    label: str
    revision: str
    reason: str = ""


@dataclass(frozen=True)
class SharedStateConfig:
    receiver_names: tuple[str, ...]
    receiver_attributes: tuple[str, ...]
    file_threshold: int


@dataclass(frozen=True)
class WriteInterfacesConfig:
    call_names: tuple[str, ...]
    adapter_files: tuple[str, ...]


@dataclass(frozen=True)
class CustomCounter:
    name: str
    call_names: tuple[str, ...] = ()
    regex: str | None = None
    description: str = ""


@dataclass(frozen=True)
class ModuleIndexConfig:
    fixed_file: str | None
    repo_path: str | None
    path_base: str


@dataclass(frozen=True)
class HotspotsConfig:
    window_days: int
    exclude_commits: tuple[str, ...]
    exclude_subject_regex: str | None


@dataclass(frozen=True)
class Config:
    project_name: str
    source_roots: tuple[str, ...]
    module_prefixes: tuple[str, ...]
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    giant_function_lines: int
    complexity_threshold: int
    giant_file_lines: int
    test_patterns: tuple[str, ...]
    test_roots: tuple[str, ...]
    mock_helpers: tuple[str, ...]
    fallback_words: tuple[str, ...]
    contract_pattern: str
    shared_state: SharedStateConfig | None
    write_interfaces: WriteInterfacesConfig | None
    custom_counters: tuple[CustomCounter, ...]
    risk_complexity: tuple[int, ...]
    risk_own_lines: tuple[int, ...]
    risk_parameters: tuple[int, ...]
    module_index: ModuleIndexConfig | None
    hotspots: HotspotsConfig
    snapshots: tuple[Snapshot, ...]


def load_config(path: str | Path) -> Config:
    """Load a TOML or JSON configuration file."""
    file_path = Path(str(path))
    if not file_path.is_file():
        raise ConfigError("config file not found: %s" % file_path)
    suffix = file_path.suffix.lower()
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("cannot read config %s: %s" % (file_path, exc)) from exc
    if suffix == ".toml":
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError("invalid TOML in %s: %s" % (file_path, exc)) from exc
    elif suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError("invalid JSON in %s: %s" % (file_path, exc)) from exc
    else:
        raise ConfigError("config must end in .toml or .json: %s" % file_path)
    if not isinstance(data, dict):
        raise ConfigError("config root must be an object")
    return config_from_dict(data)


def config_from_dict(data: dict[str, Any]) -> Config:
    """Validate an in-memory configuration object."""
    if not isinstance(data, dict):
        raise ConfigError("config root must be an object")
    _check_keys(
        data,
        {
            "project",
            "scope",
            "thresholds",
            "tests",
            "fallbacks",
            "shared_state",
            "write_interfaces",
            "custom_counters",
            "risk_tiers",
            "module_index",
            "hotspots",
            "snapshots",
        },
        "config",
    )

    project = _table(data.get("project"), "project")
    _check_keys(project, {"name", "source_roots", "module_prefixes"}, "project")
    source_roots = _path_list(project.get("source_roots"), "project.source_roots", nonempty=True)
    project_name = _string(project.get("name", "project"), "project.name")
    module_prefixes = _string_list(
        project.get("module_prefixes", []), "project.module_prefixes"
    )
    for prefix in module_prefixes:
        if not _PREFIX_RE.fullmatch(prefix):
            raise ConfigError(
                "project.module_prefixes: invalid dotted module prefix: %r" % prefix
            )

    scope = _table(data.get("scope", {}), "scope")
    _check_keys(scope, {"include", "exclude"}, "scope")
    include = _string_list(scope.get("include", ["**/*.py"]), "scope.include", nonempty=True)
    exclude = _string_list(scope.get("exclude", []), "scope.exclude")

    thresholds = _table(data.get("thresholds", {}), "thresholds")
    _check_keys(
        thresholds,
        {"giant_function_lines", "complexity", "giant_file_lines"},
        "thresholds",
    )
    giant_function_lines = _positive_int(
        thresholds.get("giant_function_lines", DEFAULT_GIANT_FUNCTION_LINES),
        "thresholds.giant_function_lines",
    )
    complexity_threshold = _positive_int(
        thresholds.get("complexity", DEFAULT_COMPLEXITY_THRESHOLD),
        "thresholds.complexity",
    )
    giant_file_lines = _positive_int(
        thresholds.get("giant_file_lines", DEFAULT_GIANT_FILE_LINES),
        "thresholds.giant_file_lines",
    )

    tests = _table(data.get("tests", {}), "tests")
    _check_keys(tests, {"patterns", "roots", "mock_helpers"}, "tests")
    test_patterns = _string_list(
        tests.get("patterns", list(DEFAULT_TEST_PATTERNS)), "tests.patterns", nonempty=True
    )
    test_roots = _path_list(tests.get("roots", []), "tests.roots")
    mock_helpers = _string_list(
        tests.get("mock_helpers", list(DEFAULT_MOCK_HELPERS)), "tests.mock_helpers"
    )

    fallbacks = _table(data.get("fallbacks", {}), "fallbacks")
    _check_keys(fallbacks, {"words", "contract_string_pattern"}, "fallbacks")
    fallback_words = _string_list(
        fallbacks.get("words", list(DEFAULT_WORDS)), "fallbacks.words", nonempty=True
    )
    seen_words: set[str] = set()
    for word in fallback_words:
        folded = word.casefold()
        if folded in seen_words:
            raise ConfigError("fallbacks.words contains a duplicate: %r" % word)
        seen_words.add(folded)
    contract_pattern = _string(
        fallbacks.get("contract_string_pattern", DEFAULT_CONTRACT_PATTERN),
        "fallbacks.contract_string_pattern",
    )
    try:
        re.compile(contract_pattern)
    except re.error as exc:
        raise ConfigError(
            "fallbacks.contract_string_pattern is not a valid regular expression: %s" % exc
        ) from exc

    shared_state = _shared_state_config(data.get("shared_state"))
    write_interfaces = _write_interfaces_config(data.get("write_interfaces"))
    custom_counters = _custom_counters(data.get("custom_counters", []))
    risk_complexity, risk_own_lines, risk_parameters = _risk_tiers(
        data.get("risk_tiers")
    )
    module_index = _module_index_config(data.get("module_index"))
    hotspots = _hotspots_config(data.get("hotspots"))
    snapshots = _snapshots(data.get("snapshots", []))

    return Config(
        project_name=project_name,
        source_roots=source_roots,
        module_prefixes=module_prefixes,
        include=include,
        exclude=exclude,
        giant_function_lines=giant_function_lines,
        complexity_threshold=complexity_threshold,
        giant_file_lines=giant_file_lines,
        test_patterns=test_patterns,
        test_roots=test_roots,
        mock_helpers=mock_helpers,
        fallback_words=fallback_words,
        contract_pattern=contract_pattern,
        shared_state=shared_state,
        write_interfaces=write_interfaces,
        custom_counters=custom_counters,
        risk_complexity=risk_complexity,
        risk_own_lines=risk_own_lines,
        risk_parameters=risk_parameters,
        module_index=module_index,
        hotspots=hotspots,
        snapshots=snapshots,
    )


def make_snapshot(label: str, revision: str, reason: str = "") -> Snapshot:
    label = _string(label, "snapshot.label")
    revision = _string(revision, "snapshot.revision")
    if not _LABEL_RE.fullmatch(label):
        raise ConfigError(
            "snapshot label must match [A-Za-z0-9][A-Za-z0-9_.-]*: %r" % label
        )
    return Snapshot(label=label, revision=revision, reason=_string(reason, "snapshot.reason", allow_empty=True))


def config_summary(config: Config) -> dict[str, Any]:
    """A JSON-safe echo of every setting that affects the measurement."""
    shared_state = None
    if config.shared_state is not None:
        shared_state = {
            "receiver_names": list(config.shared_state.receiver_names),
            "receiver_attributes": list(config.shared_state.receiver_attributes),
            "file_threshold": config.shared_state.file_threshold,
        }
    write_interfaces = None
    if config.write_interfaces is not None:
        write_interfaces = {
            "call_names": list(config.write_interfaces.call_names),
            "adapter_files": list(config.write_interfaces.adapter_files),
        }
    return {
        "project": config.project_name,
        "source_roots": list(config.source_roots),
        "module_prefixes": list(config.module_prefixes),
        "include": list(config.include),
        "exclude": list(config.exclude),
        "thresholds": {
            "giant_function_lines": config.giant_function_lines,
            "complexity": config.complexity_threshold,
            "giant_file_lines": config.giant_file_lines,
        },
        "tests": {
            "patterns": list(config.test_patterns),
            "roots": list(config.test_roots),
            "mock_helpers": list(config.mock_helpers),
        },
        "fallbacks": {
            "words": list(config.fallback_words),
            "contract_string_pattern": config.contract_pattern,
        },
        "shared_state": shared_state,
        "write_interfaces": write_interfaces,
        "custom_counters": [
            {
                "name": counter.name,
                "call_names": list(counter.call_names),
                "regex": counter.regex,
                "description": counter.description,
            }
            for counter in config.custom_counters
        ],
        "risk_tiers": {
            "complexity": list(config.risk_complexity),
            "own_lines": list(config.risk_own_lines),
            "parameters": list(config.risk_parameters),
        },
        "module_index": (
            {
                "fixed_file": config.module_index.fixed_file,
                "repo_path": config.module_index.repo_path,
                "path_base": config.module_index.path_base,
            }
            if config.module_index is not None
            else None
        ),
        "hotspots": {
            "window_days": config.hotspots.window_days,
            "exclude_commits": list(config.hotspots.exclude_commits),
            "exclude_subject_regex": config.hotspots.exclude_subject_regex,
            "rename_handling": "--no-renames; rename is represented as delete/add",
        },
    }


def _positive_int_list(value: Any, where: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise ConfigError("%s must be an array of integers" % where)
    result = tuple(_positive_int(item, "%s[%d]" % (where, index)) for index, item in enumerate(value))
    if not result or tuple(sorted(set(result))) != result:
        raise ConfigError("%s must be non-empty and strictly increasing" % where)
    return result


def _risk_tiers(value: Any) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    table = _table(value or {}, "risk_tiers")
    _check_keys(table, {"complexity", "own_lines", "parameters"}, "risk_tiers")
    return (
        _positive_int_list(table.get("complexity", list(DEFAULT_RISK_COMPLEXITY)), "risk_tiers.complexity"),
        _positive_int_list(table.get("own_lines", list(DEFAULT_RISK_FUNCTION_OWN_LINES)), "risk_tiers.own_lines"),
        _positive_int_list(table.get("parameters", list(DEFAULT_RISK_PARAMETERS)), "risk_tiers.parameters"),
    )


def _module_index_config(value: Any) -> ModuleIndexConfig | None:
    if value is None:
        return None
    table = _table(value, "module_index")
    _check_keys(table, {"fixed_file", "repo_path", "path_base"}, "module_index")
    fixed_file = table.get("fixed_file")
    repo_path = table.get("repo_path")
    if (fixed_file is None) == (repo_path is None):
        raise ConfigError("module_index must set exactly one of fixed_file or repo_path")
    if fixed_file is not None:
        fixed_file = _string(fixed_file, "module_index.fixed_file")
        if not Path(fixed_file).is_absolute():
            raise ConfigError("module_index.fixed_file must be an absolute path")
    if repo_path is not None:
        repo_path = _relative_path(repo_path, "module_index.repo_path")
    path_base = _relative_path(table.get("path_base", "."), "module_index.path_base")
    if path_base == ".":
        path_base = ""
    return ModuleIndexConfig(fixed_file, repo_path, path_base)


def _hotspots_config(value: Any) -> HotspotsConfig:
    table = _table(value or {}, "hotspots")
    _check_keys(table, {"window_days", "exclude_commits", "exclude_subject_regex"}, "hotspots")
    window_days = _positive_int(table.get("window_days", DEFAULT_HOTSPOT_WINDOW_DAYS), "hotspots.window_days")
    exclude_commits = _string_list(table.get("exclude_commits", []), "hotspots.exclude_commits")
    pattern = table.get("exclude_subject_regex")
    if pattern is not None:
        pattern = _string(pattern, "hotspots.exclude_subject_regex")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ConfigError("hotspots.exclude_subject_regex is invalid: %s" % exc) from exc
    return HotspotsConfig(window_days, exclude_commits, pattern)


def _shared_state_config(value: Any) -> SharedStateConfig | None:
    if value is None:
        return None
    table = _table(value, "shared_state")
    _check_keys(
        table, {"receiver_names", "receiver_attributes", "file_threshold"}, "shared_state"
    )
    receiver_names = _string_list(
        table.get("receiver_names", []), "shared_state.receiver_names"
    )
    receiver_attributes = _string_list(
        table.get("receiver_attributes", []), "shared_state.receiver_attributes"
    )
    if not receiver_names and not receiver_attributes:
        raise ConfigError(
            "shared_state needs at least one of receiver_names/receiver_attributes"
        )
    file_threshold = _positive_int(
        table.get("file_threshold", 3), "shared_state.file_threshold"
    )
    return SharedStateConfig(
        receiver_names=receiver_names,
        receiver_attributes=receiver_attributes,
        file_threshold=file_threshold,
    )


def _write_interfaces_config(value: Any) -> WriteInterfacesConfig | None:
    if value is None:
        return None
    table = _table(value, "write_interfaces")
    _check_keys(table, {"call_names", "adapter_files"}, "write_interfaces")
    call_names = _string_list(
        table.get("call_names"), "write_interfaces.call_names", nonempty=True
    )
    adapter_files = _path_list(
        table.get("adapter_files", []), "write_interfaces.adapter_files"
    )
    return WriteInterfacesConfig(call_names=call_names, adapter_files=adapter_files)


def _custom_counters(value: Any) -> tuple[CustomCounter, ...]:
    if not isinstance(value, list):
        raise ConfigError("custom_counters must be an array of tables")
    counters: list[CustomCounter] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        where = "custom_counters[%d]" % index
        table = _table(raw, where)
        _check_keys(table, {"name", "call_names", "regex", "description"}, where)
        name = _string(table.get("name"), where + ".name")
        if not _COUNTER_NAME_RE.fullmatch(name):
            raise ConfigError(
                "%s.name must match [A-Za-z_][A-Za-z0-9_]*: %r" % (where, name)
            )
        if name in seen:
            raise ConfigError("duplicate custom counter name: %r" % name)
        seen.add(name)
        call_names = _string_list(table.get("call_names", []), where + ".call_names")
        regex = table.get("regex")
        if regex is not None:
            regex = _string(regex, where + ".regex")
        if bool(call_names) == (regex is not None):
            raise ConfigError(
                "%s must set exactly one of call_names or regex" % where
            )
        if regex is not None:
            try:
                re.compile(regex)
            except re.error as exc:
                raise ConfigError("%s.regex is invalid: %s" % (where, exc)) from exc
        description = _string(
            table.get("description", ""), where + ".description", allow_empty=True
        )
        counters.append(
            CustomCounter(
                name=name,
                call_names=call_names,
                regex=regex,
                description=description,
            )
        )
    return tuple(counters)


def _snapshots(value: Any) -> tuple[Snapshot, ...]:
    if not isinstance(value, list):
        raise ConfigError("snapshots must be an array of tables")
    snapshots: list[Snapshot] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        where = "snapshots[%d]" % index
        table = _table(raw, where)
        _check_keys(table, {"label", "revision", "reason"}, where)
        snapshot = make_snapshot(
            _string(table.get("label"), where + ".label"),
            _string(table.get("revision"), where + ".revision"),
            _string(table.get("reason", ""), where + ".reason", allow_empty=True),
        )
        if snapshot.label in seen:
            raise ConfigError("duplicate snapshot label: %r" % snapshot.label)
        seen.add(snapshot.label)
        snapshots.append(snapshot)
    return tuple(snapshots)


def _table(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError("%s must be a table/object" % where)
    return value


def _check_keys(table: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(
            "%s: unknown key(s): %s" % (where, ", ".join(repr(key) for key in unknown))
        )


def _string(value: Any, where: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError("%s must be a string" % where)
    if not allow_empty and not value.strip():
        raise ConfigError("%s must not be empty" % where)
    return value


def _string_list(value: Any, where: str, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError("%s must be an array of strings" % where)
    result = tuple(_string(item, "%s[%d]" % (where, index)) for index, item in enumerate(value))
    if nonempty and not result:
        raise ConfigError("%s must not be empty" % where)
    return result


def _positive_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError("%s must be an integer" % where)
    if value < 1:
        raise ConfigError("%s must be >= 1" % where)
    return value


def _path_list(value: Any, where: str, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConfigError("%s must be an array of strings" % where)
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_relative_path(item, "%s[%d]" % (where, index)))
    if nonempty and not result:
        raise ConfigError("%s must not be empty" % where)
    return tuple(result)


def _relative_path(value: Any, where: str) -> str:
    text = _string(value, where)
    if "\\" in text:
        raise ConfigError("%s must use forward slashes: %r" % (where, text))
    path = PurePosixPath(text)
    if path.is_absolute() or text.startswith("/"):
        raise ConfigError("%s must be repository-relative: %r" % (where, text))
    if any(part == ".." for part in path.parts):
        raise ConfigError("%s must not contain '..': %r" % (where, text))
    normalized = path.as_posix()
    if normalized in ("", "."):
        return "."
    return normalized.rstrip("/")
