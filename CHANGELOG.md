# Changelog

## 0.1.0 - 2026-11-12

First public release.

- Snapshot measurement via `git archive` (never checks out the repository); standard library only, Python 3.11+.
- Metrics: scale, giant functions/files, approximate cyclomatic complexity, import-graph SCC in three views, cross-file private-name access proxy, broad exception handlers, compatibility-residue words, versioned contract strings, test-surface proxies, configurable shared-state / write-interface / custom counters.
- Optional views: module index checks (unassigned files, multi-leaf ownership, stale paths, per-leaf Ca/Ce/instability, dependency-direction violations counted per import statement including constant-string `importlib.import_module`), risk tiers by complexity / own lines / parameter count, Git change hotspots.
- Every metric carries ISO/IEC 25010 grouping, source type, source URL, threshold status, role and limitations. No total score, ranking or pass/fail gate (module contracts excepted).
