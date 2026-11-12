# maintainability-scorecard

A self-contained Claude Code skill that measures maintainability metrics for a
Python project across git snapshots without checking anything out. It exports
each snapshot with `git archive` into a temporary directory, parses the source
with the standard library, and writes a full JSON card plus a one-page Markdown
summary.

- No third-party Python dependencies and no dependency on the measured project;
  the snapshot export invokes the local `git` command. Requires Python 3.11+
  for `tomllib`.
- Configurable thresholds, test paths, compatibility words, project-specific
  shared state, external write interfaces and custom counters.
- Three import-graph SCC views: all edges, excluding `TYPE_CHECKING` edges, and
  top-level load-time imports only.
- Optional ISO/IEC 25010 subcharacteristic metadata, module-index boundary checks,
  risk distributions and 90-day Git hotspots; these are trends and contracts,
  not a score.
- Optional `--embed` replaces an existing
  `<script type="application/json" id="...">` block; it never generates HTML.

## Quick start

```bash
python3 scripts/scorecard.py \
  --repo /path/to/repo \
  --config examples/scorecard.toml \
  --snapshot baseline=<base-commit> \
  --snapshot current=main \
  --json out/scorecard.json \
  --markdown out/scorecard.md
```

## Layout

```
SKILL.md                     agent-facing usage and exact metric definitions
README.md                    this file
scripts/scorecard.py         CLI, git export, snapshot orchestration
scripts/scorecard_config.py  config loading, defaults, validation
scripts/scorecard_ast.py     AST metric visitors
scripts/scorecard_imports.py import graph and SCC analysis
scripts/scorecard_measure.py file selection and snapshot aggregation
scripts/scorecard_extended.py optional module-index, risk-tier and hotspot metrics
scripts/scorecard_report.py  definitions, Markdown, HTML data-block embed
examples/scorecard.toml      annotated example configuration
tests/                       unittest suite with synthetic git repositories
```

## Test

```bash
python3 -m unittest discover -s tests
```

The suite covers scale, giant functions/files, complexity, the three SCC views,
all three private-access definition kinds, compatibility word boundaries,
broad exceptions, versioned contract strings, test metrics, shared state,
write interfaces (including missing-adapter hard failure), custom counters,
thresholds, JSON/Markdown output and `--embed` replacement. Module-index tests
cover the `Ca`/`Ce`/`I` formula, allowed dependencies, separate
`TYPE_CHECKING` violations, statement counting (multiple names in one
statement count once), stale paths, unmatched `.py` globs and the zero-leaf
`ConfigError`; risk-tier tests cover threshold boundaries; hotspot tests cover
the time window, `exclude_commits` and `unavailable`.

The optional views are explicitly non-scoring:

- `definition_metadata` groups each legacy definition by ISO/IEC 25010 maintenance subcharacteristic and records source type, URL, threshold status, role and limitations while preserving the legacy `definitions` strings.
- `[module_index]` accepts a fixed absolute TOML file or a repository-relative snapshot file. Only `kind = "leaf"` entries own files; a module list without any leaf is a hard error. It reports unassigned/stale paths (explicit paths checked against snapshot-archive existence), `unmatched_py_patterns` (globs judged only against production `.py` files), multi-owned paths, leaf module `Ca`/`Ce`/`I`, and boundary violations. Runtime violations are counted per import statement: top-level and function-local imports count, one statement importing several names from the same module counts once, and constant-string `importlib.import_module` calls count. `TYPE_CHECKING` violations are listed separately. `runtime_violation_import_statements` is the main number; `runtime_violation_file_pairs` is a file-pair-deduplicated auxiliary column.
- `risk_tiers` reports function counts and own-line shares for configurable complexity, function-size and parameter bands. A value equal to a threshold falls into the higher band. Defaults are documented in `SKILL.md`; no stars are emitted.
- `hotspots` uses `git log --no-renames` over a 90-day window by default and reports raw commit/churn, complexity sum and longest-function values. It reports `unavailable` when history cannot support the view.

For metric meaning, exact counting rules and known approximations, read
[`SKILL.md`](SKILL.md).

## Install as a Claude Code skill

```bash
git clone https://github.com/liuyejinghong/maintainability-scorecard.git \
  ~/.claude/skills/maintainability-scorecard
```

Claude Code picks the skill up from `SKILL.md`. The scripts can also be run
directly with `python3` as shown above.

## License

MIT. See [LICENSE](LICENSE).
