"""JSON definitions, Markdown summary and HTML data-block embedding."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scorecard_config import Config

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(
    r"""([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')"""
)


class EmbedError(ValueError):
    """Raised when the target JSON script block cannot be found or is ambiguous."""


def _legacy_definitions(config: Config) -> dict[str, str]:
    test_scope = "、".join(config.test_patterns)
    shared = (
        "名为 %s 的对象及属性 %s 的共享状态访问；键统计只覆盖字符串字面量。"
        % (
            "/".join(config.shared_state.receiver_names) or "（无）",
            "/".join(config.shared_state.receiver_attributes) or "（无）",
        )
        if config.shared_state is not None
        else ""
    )
    write = (
        "直接调用 %s 的 Call 节点；排除适配器文件 %s。适配器文件缺失即报错。"
        % (
            "/".join(config.write_interfaces.call_names),
            "、".join(config.write_interfaces.adapter_files) or "（无）",
        )
        if config.write_interfaces is not None
        else ""
    )
    result = {
        "scope.production": "source_roots=[%s] 下的生产 Python 文件；include=%s，exclude=%s。"
        % (
            ", ".join(config.source_roots),
            ", ".join(config.include),
            ", ".join(config.exclude) or "（无）",
        ),
        "scope.tests": "source_roots/test_roots 下匹配 %s 的测试文件；test_roots=%s。"
        % (test_scope, ", ".join(config.test_roots) or "（无）"),
        "scale.prod_sloc": "生产代码非空非注释物理行；tokenize 判定整行注释，空行不计；docstring 与多行字符串正文算代码行。",
        "scale.prod_files": "生产 Python 文件数。",
        "scale.prod_physical_lines": "生产代码物理行（含空行与注释），用于巨型文件占比。",
        "scale.sloc_tokenize_fallback_files": "tokenize 失败、改用整行 # 前缀近似的文件数（应为 0）。",
        "giant_functions.count": "函数自身跨度（own_lines：AST 跨度减嵌套函数/类跨度）> %d 的函数个数。"
        % config.giant_function_lines,
        "giant_functions.line_share": "巨型函数 own_lines 合计 / 全部函数 own_lines 合计。",
        "giant_functions.longest": "own_lines 最长的函数（路径、限定名、own_lines）。",
        "complexity.count": "AST 圈复杂度（1 + If/IfExp/For/AsyncFor/While/ExceptHandler/match_case/Assert + BoolOp 额外操作数 + 推导式 for/if）> %d 的函数个数。"
        % config.complexity_threshold,
        "complexity.top10_share": "复杂度最高的 10 个函数复杂度合计 / 全部函数复杂度合计。",
        "complexity.sum": "全部生产函数复杂度总和。",
        "complexity.max": "复杂度最高的单个函数（路径、限定名、复杂度）。",
        "giant_files.count": "生产代码物理行 > %d 的文件个数。" % config.giant_file_lines,
        "giant_files.line_share": "巨型文件物理行合计 / 生产代码物理行合计。",
        "module_tangle.max_scc_size": "项目内 import 图的最大强连通分量（全部边，含函数内 import 与 TYPE_CHECKING import）。",
        "module_tangle.max_scc_size_excluding_type_checking_edges": "去掉 `if TYPE_CHECKING:` 内 import 边后的最大强连通分量。",
        "module_tangle.max_scc_size_top_level_imports": "仅保留模块顶层加载期 import 边（排除函数内 import 与 TYPE_CHECKING import）后的最大强连通分量。",
        "module_tangle.import_edges": "全部去重导入边数；节点为生产 .py 文件，from-import 同时解析基模块和别名子模块。",
        "module_tangle.import_edges_excluding_type_checking": "去掉 `if TYPE_CHECKING:` 内 import 边后的去重边数。",
        "module_tangle.top_level_import_edges": "模块顶层、非 TYPE_CHECKING import 的去重边数（加载期依赖）。",
        "module_tangle.function_local_imports": "函数体内 import 且至少解析到一个项目内模块的 import 语句条数。",
        "private_access.count": "跨文件私有属性访问近似值：访问方文件中不存在同名单下划线定义时才计数；定义包括 def/class 名、Name 赋值目标、Attribute 赋值目标（self._x=/cls._x=/obj._x=）。",
        "fallbacks.legacy_words": "配置词表在标识符与字符串字面量中的出现次数；按词边界、不区分大小写。compat 只计独立 compat 或 compat_/compat- 前缀。",
        "fallbacks.word_counts": "上述总和的按词拆分。",
        "fallbacks.versioned_contract_strings_distinct": "完整字符串字面量匹配配置正则的去重个数。",
        "fallbacks.versioned_contract_strings_occurrences": "上述匹配的出现次数。",
        "fallbacks.except_exception": "except 子句捕获 Exception（含元组中包含 Exception）的处理块个数。",
        "fallbacks.bare_except": "bare except 处理块个数。",
        "fallbacks.broad_except_total": "except Exception 与 bare except 合计。",
        "tests.test_files": "测试代码 .py 文件数。",
        "tests.test_lines": "测试代码物理行数。",
        "tests.private_attr_accesses": "测试代码中 obj._name 形式的单下划线私有属性访问次数（不做定义方文件判断）。",
        "tests.mock_helpers": "测试代码中 MagicMock/monkeypatch/SimpleNamespace 等配置助手名的 Name/Attribute 引用次数；导入别名不计。",
        "parse_errors": "生产/测试 AST 解析失败的文件；非空表示相关 AST 指标不完整。",
    }
    if config.shared_state is not None:
        result["shared_state.accesses"] = "共享状态访问次数：" + shared
        result["shared_state.keys_written_by_ge_n_files"] = (
            "被 >= %d 个文件写入的共享状态键数。" % config.shared_state.file_threshold
        )
    if config.write_interfaces is not None:
        result["write_interfaces.count"] = "直接外部写接口调用点。" + write
        result["write_interfaces.files"] = "含直接外部写接口调用点的生产文件数（适配器文件不计）。"
    for counter in config.custom_counters:
        if counter.regex is not None:
            result["custom.%s" % counter.name] = "原始源码文本中匹配正则 `%s` 的次数（含注释与字符串）。" % counter.regex
        else:
            result["custom.%s" % counter.name] = "调用名为 %s 的 Call 节点数。" % "/".join(counter.call_names)
    result["risk_tiers.complexity"] = "函数复杂度按配置分界统计函数数与 own_lines 占比。"
    result["risk_tiers.own_lines"] = "函数自身代码行按配置分界统计函数数与 own_lines 占比。"
    result["risk_tiers.parameters"] = "函数参数数按配置分界统计函数数与 own_lines 占比；排除 self/cls，*args/**kwargs 单列。"
    result["module_index"] = "可选模块索引：未归属、多叶子归属、失效路径、未匹配 .py glob、叶子模块 Ca/Ce/I 与依赖方向违例；违例按运行时 import 语句计，TYPE_CHECKING 单列。"
    result["hotspots"] = "快照终点前配置窗口内 git log --no-renames 的文件提交次数、增删行、复杂度总和和最长函数。历史不足时 unavailable。"
    return result


def definition_metadata(config: Config) -> dict[str, dict[str, Any]]:
    """Return source and interpretation metadata without changing legacy definitions."""
    mapping = {
        "scale": ("background", "research", "https://www.softwareimprovementgroup.com/wp-content/uploads/2019/10/APracticalModelForMeasuringMaintainability.pdf", "background"),
        "giant_functions": ("modifiability", "tool_default", "https://raw.githubusercontent.com/SonarSource/sonar-python/master/python-checks/src/main/java/org/sonar/python/checks/TooManyLinesInFunctionCheck.java", "hotspot"),
        "giant_files": ("modifiability", "project_convention", "https://www.omg.org/spec/ASCMM/1.0/PDF", "hotspot"),
        "complexity": ("analysability", "research", "https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication500-235.pdf", "hotspot"),
        "module_tangle": ("modularity", "research", "https://staff.cs.utu.fi/~jounsmed/doos_06/material/DesignPrinciplesAndPatterns.pdf", "trend"),
        "private_access": ("reusability", "tool_default", "https://docs.astral.sh/ruff/rules/private-member-access/", "trend"),
        "fallbacks": ("analysability", "project_convention", "", "trend"),
        "tests": ("testability", "project_convention", "", "trend"),
        "parse_errors": ("measurement_validity", "standard", "https://www.iso.org/standard/72117.html", "background"),
        "shared_state": ("modularity", "domain_signal", "https://doi.org/10.1145/361598.361623", "hotspot"),
        "write_interfaces": ("modularity", "domain_signal", "", "contract_check"),
        "custom": ("background", "domain_signal", "", "trend"),
        "risk_tiers": ("analysability", "tool_default", "https://softwareimprovementgroup.com/wp-content/uploads/SIG-TUViT-Evaluation-Criteria-Trusted-Product-Maintainability.pdf", "trend"),
        "module_index": ("modularity", "project_convention", "https://staff.cs.utu.fi/~jounsmed/doos_06/material/DesignPrinciplesAndPatterns.pdf", "contract_check"),
        "hotspots": ("modifiability", "research", "https://arxiv.org/abs/2008.09569", "hotspot"),
    }
    metadata: dict[str, dict[str, Any]] = {}
    for key in _legacy_definitions(config):
        group = key.split(".", 1)[0]
        characteristic, source_type, source_url, role = mapping.get(
            group, ("background", "project_convention", "", "background")
        )
        threshold_status = "none"
        if key in {"complexity.count", "giant_functions.count", "giant_files.count"}:
            threshold_status = "local_config"
        metadata[key] = {
            "display_name": key,
            "iso_characteristic": characteristic,
            "source_type": source_type,
            "source_url": source_url,
            "threshold_status": threshold_status,
            "role": role,
            "limitations": "静态代理；不能单独推出质量、缺陷率或标准符合性。",
        }
    for key in ("complexity.count", "complexity.sum", "complexity.max", "complexity.top10_share"):
        if key in metadata:
            metadata[key]["display_name"] = "近似圈复杂度：" + key.split(".", 1)[1]
    if "private_access.count" in metadata:
        metadata["private_access.count"]["display_name"] = "跨文件私有名称访问代理"
    for key in ("tests.test_files", "tests.test_lines", "tests.private_attr_accesses", "tests.mock_helpers"):
        if key in metadata:
            metadata[key]["display_name"] = "测试表面代理：" + key.split(".", 1)[1]
    return metadata


def definitions(config: Config) -> dict[str, str]:
    return _legacy_definitions(config)


def render_markdown(card: dict[str, Any]) -> str:
    snapshots = card["snapshots"]
    labels = list(snapshots)
    config = card["config"]
    thresholds = config["thresholds"]
    metrics_config = config["shared_state"]
    write_config = config["write_interfaces"]

    def value(label: str, path: tuple[str, ...]):
        current: Any = snapshots[label]["metrics"]
        for key in path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    rows: list[tuple[str, str, tuple[str, ...], str]] = [
        ("生产代码非空非注释行数", "tokenize 排除整行注释与空行；字符串正文算代码行", ("scale", "prod_sloc"), "int"),
        ("生产代码文件数", "生产 Python 文件数", ("scale", "prod_files"), "int"),
        ("生产代码物理行数", "含空行与注释；仅用于巨型文件占比", ("scale", "prod_physical_lines"), "int"),
        (
            "巨型函数 >%d 行（个数）" % thresholds["giant_function_lines"],
            "own_lines（AST 跨度减嵌套定义）> %d" % thresholds["giant_function_lines"],
            ("giant_functions", "count"),
            "int",
        ),
        ("巨型函数行数占比", "巨型函数 own_lines / 全部函数 own_lines", ("giant_functions", "line_share"), "share"),
        ("最长函数（own_lines）", "单个函数自身跨度", ("giant_functions", "longest", "own_lines"), "int"),
        (
            "圈复杂度 >%d 函数（个数）" % thresholds["complexity"],
            "AST 圈复杂度 > %d" % thresholds["complexity"],
            ("complexity", "count"),
            "int",
        ),
        ("复杂度 top10 占比", "最复杂 10 个函数合计 / 全部函数复杂度", ("complexity", "top10_share"), "share"),
        ("复杂度总和", "全部生产函数复杂度合计", ("complexity", "sum"), "int"),
        ("最复杂函数复杂度", "单个函数复杂度最大值", ("complexity", "max", "complexity"), "int"),
        (
            "巨型文件 >%d 行（个数）" % thresholds["giant_file_lines"],
            "生产文件物理行 > %d" % thresholds["giant_file_lines"],
            ("giant_files", "count"),
            "int",
        ),
        ("巨型文件行数占比", "巨型文件物理行 / 生产物理行", ("giant_files", "line_share"), "share"),
        ("导入图边数（全部）", "项目内 import 图去重边数", ("module_tangle", "import_edges"), "int"),
        ("导入图最大 SCC（全部边）", "含函数内与 TYPE_CHECKING import", ("module_tangle", "max_scc_size"), "int"),
        (
            "导入图最大 SCC（去掉 TYPE_CHECKING 边）",
            "去掉 if TYPE_CHECKING 内 import",
            ("module_tangle", "max_scc_size_excluding_type_checking_edges"),
            "int",
        ),
        (
            "导入图最大 SCC（仅顶层加载期 import）",
            "只保留模块顶层、非 TYPE_CHECKING import",
            ("module_tangle", "max_scc_size_top_level_imports"),
            "int",
        ),
        ("函数内项目 import（条）", "函数体内解析到项目模块的 import 语句", ("module_tangle", "function_local_imports"), "int"),
        ("跨文件私有属性访问", "访问方文件无同名 def/赋值才计数（近似）", ("private_access", "count"), "int"),
        ("except Exception（个数）", "捕获 Exception 的 except 块", ("fallbacks", "except_exception"), "int"),
        ("bare except（个数）", "裸 except 块", ("fallbacks", "bare_except"), "int"),
        ("宽泛异常捕获合计", "except Exception + bare except", ("fallbacks", "broad_except_total"), "int"),
        (
            "兼容残留字样（次数）",
            "词表 %s；按词边界" % "/".join(config["fallbacks"]["words"]),
            ("fallbacks", "legacy_words"),
            "int",
        ),
        ("版本化合同字符串（去重）", "完整字符串匹配配置正则", ("fallbacks", "versioned_contract_strings_distinct"), "int"),
        ("版本化合同字符串（出现次数）", "上述匹配的出现次数", ("fallbacks", "versioned_contract_strings_occurrences"), "int"),
        ("测试代码行数", "测试文件物理行", ("tests", "test_lines"), "int"),
        ("测试代码文件数", "测试 .py 文件数", ("tests", "test_files"), "int"),
        ("测试私有属性访问", "测试中的 obj._name 次数", ("tests", "private_attr_accesses"), "int"),
        ("测试 mock 助手引用", "MagicMock/monkeypatch/SimpleNamespace 等", ("tests", "mock_helpers"), "int"),
    ]
    if metrics_config is not None:
        rows.append(
            (
                "共享状态访问次数",
                "配置接收者 %s 的访问"
                % "/".join(
                    dict.fromkeys(
                        metrics_config["receiver_names"]
                        + metrics_config["receiver_attributes"]
                    )
                ),
                ("shared_state", "accesses"),
                "int",
            )
        )
        rows.append(
            (
                "被 ≥%d 文件写入的共享状态键" % metrics_config["file_threshold"],
                "字面量键，按写入文件数统计",
                ("shared_state", "keys_written_by_ge_n_files"),
                "int",
            )
        )
    if write_config is not None:
        rows.append(
            (
                "直接外部写接口调用点",
                "%s；排除适配器" % "/".join(write_config["call_names"]),
                ("write_interfaces", "count"),
                "int",
            )
        )
    for counter in config["custom_counters"]:
        rows.append(
            (
                "自定义计数：%s" % counter["name"],
                counter["description"] or "配置的自定义计数",
                ("custom", counter["name"]),
                "int",
            )
        )

    lines: list[str] = []
    lines.append("# 可维护性记分卡")
    lines.append("")
    lines.append("生成时间：%s" % card["generated_at"])
    lines.append("")
    lines.append("项目：%s" % config["project"])
    lines.append(
        "范围：%s 下的生产 Python 文件；测试指标单列。"
        % "、".join("`%s`" % root for root in config["source_roots"])
    )
    lines.append("每个快照都用 `git archive` 导出到临时目录测量，不 checkout。")
    lines.append("")
    lines.append("ISO/IEC 25010 可维护性维度：模块化、可复用性、可分析性、可修改性、可测试性。此分组是观察框架，不代表 ISO 符合性。")
    lines.append("")
    metadata = card.get("definition_metadata", {})
    characteristic_names = {
        "modularity": "模块化",
        "reusability": "可复用性",
        "analysability": "可分析性",
        "modifiability": "可修改性",
        "testability": "可测试性",
        "background": "背景指标",
        "measurement_validity": "测量有效性",
    }
    for characteristic in characteristic_names:
        metric_keys = [
            key for key, item in metadata.items()
            if item.get("iso_characteristic") == characteristic
        ]
        if not metric_keys:
            continue
        lines.append("### " + characteristic_names[characteristic])
        for key in metric_keys:
            item = metadata[key]
            source = item.get("source_url") or "项目约定/领域信号"
            lines.append(
                "- %s：来源类型 `%s`；角色 `%s`；阈值 `%s`；%s（%s）。"
                % (
                    item.get("display_name", key),
                    item.get("source_type", "project_convention"),
                    item.get("role", "background"),
                    item.get("threshold_status", "none"),
                    item.get("limitations", ""),
                    source,
                )
            )
        lines.append("")
    lines.append("风险分档（独立于原指标）：")
    for label in labels:
        risk = snapshots[label]["metrics"].get("risk_tiers", {})
        lines.append("- %s：" % label)
        for dimension in ("complexity", "own_lines", "parameters"):
            item = risk.get(dimension, {})
            lines.append("  - %s 阈值 %s（%s）" % (dimension, item.get("thresholds", []), item.get("threshold_status", "none")))
            for bucket_name, bucket in item.get("buckets", {}).items():
                lines.append("    - %s：%s 函数，代码行占比 %.1f%%" % (bucket_name, bucket["functions"], bucket["line_share"] * 100))
    lines.append("")
    lines.append("改动热点：")
    for label in labels:
        hotspot = snapshots[label]["metrics"].get("hotspots", {"status": "unavailable"})
        lines.append("- %s：%s" % (label, hotspot.get("status", "unavailable")))
        if hotspot.get("status") == "available":
            for item in hotspot["files"][:10]:
                lines.append("  - `%s`：提交 %d，+%d/-%d，复杂度和 %d，最长函数 %d 行" % (item["path"], item["commits"], item["additions"], item["deletions"], item["complexity_sum"], item["longest_function_own_lines"]))
    lines.append("")
    lines.append("模块索引：")
    for label in labels:
        module_index = snapshots[label]["metrics"].get("module_index")
        if not module_index:
            continue
        if module_index.get("status") != "available":
            lines.append("- %s：unavailable（%s）" % (label, module_index.get("reason", "")))
            continue
        lines.append(
            "- %s：未归属 %d 个文件；多叶子归属 %d 个；失效路径 %d；未匹配 .py glob %d；"
            "运行时违例 import 语句 %d（文件对去重 %d）；TYPE_CHECKING 违例语句 %d（文件对去重 %d）。"
            % (
                label,
                module_index["unassigned_count"],
                len(module_index["multiple_leaf_files"]),
                len(module_index["stale_paths"]),
                len(module_index["unmatched_py_patterns"]),
                module_index["runtime_violation_import_statements"],
                module_index["runtime_violation_file_pairs"],
                module_index["type_checking_violation_import_statements"],
                module_index["type_checking_violation_file_pairs"],
            )
        )
        leaves = module_index.get("leaf_modules", [])
        if leaves:
            lines.append("")
            lines.append("叶子模块 Ca/Ce/I（%s）：" % label)
            lines.append("")
            lines.append("| 叶子模块 | 名称 | 文件数 | 行数 | Ca | Ce | I |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for leaf in leaves:
                instability = "—" if leaf["I"] is None else "%.2f" % leaf["I"]
                lines.append(
                    "| %s | %s | %d | %d | %d | %d | %s |"
                    % (
                        leaf["id"],
                        leaf["name"],
                        leaf["files"],
                        leaf["lines"],
                        leaf["Ca"],
                        leaf["Ce"],
                        instability,
                    )
                )
    lines.append("")
    lines.append("| 快照 | 提交 | 时间 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    for label in labels:
        item = snapshots[label]
        lines.append(
            "| %s | `%s` | %s | %s |"
            % (
                label,
                item["short"],
                item.get("committed_at") or "—",
                item.get("reason") or "—",
            )
        )
    lines.append("")
    change_label = "%s→%s" % (labels[0], labels[-1]) if len(labels) > 1 else "—"
    lines.append(
        "| 指标 | 口径 | %s | 变化（%s） |"
        % (" | ".join(labels), change_label)
    )
    lines.append("| --- | --- | %s | --- |" % " | ".join("---" for _ in labels))
    for title, definition, path, kind in rows:
        values = [value(label, path) for label in labels]
        lines.append(
            "| %s | %s | %s | %s |"
            % (
                title,
                definition,
                " | ".join(_format_value(item, kind) for item in values),
                _percent_change(values[0], values[-1]) if len(values) > 1 else "—",
            )
        )
    lines.append("")
    lines.append("行数是背景不是成绩：规模与测试行数用于解释变化，不单独构成质量结论。")
    lines.append("")
    lines.append("最长函数与最复杂函数：")
    for label in labels:
        longest = value(label, ("giant_functions", "longest"))
        most_complex = value(label, ("complexity", "max"))
        longest_text = (
            "%s（%s，%d 行）"
            % (longest["qualname"], longest["path"], longest["own_lines"])
            if isinstance(longest, dict)
            else "—"
        )
        complex_text = (
            "%s（%s，复杂度 %d）"
            % (
                most_complex["qualname"],
                most_complex["path"],
                most_complex["complexity"],
            )
            if isinstance(most_complex, dict)
            else "—"
        )
        lines.append("- %s：最长 %s；最复杂 %s" % (label, longest_text, complex_text))
    lines.append("")
    parse_lines: list[str] = []
    for label in labels:
        production_errors = snapshots[label]["metrics"].get("parse_errors", [])
        test_errors = snapshots[label]["metrics"].get("tests", {}).get("parse_errors", [])
        for error in production_errors:
            parse_lines.append(
                "- %s 生产：%s（%s）" % (label, error["path"], error["error"].splitlines()[0])
            )
        for error in test_errors:
            parse_lines.append(
                "- %s 测试：%s（%s）" % (label, error["path"], error["error"].splitlines()[0])
            )
    lines.append("AST 解析失败：")
    if parse_lines:
        lines.extend(parse_lines)
    else:
        lines.append("- 无。")
    lines.append("")
    lines.append("已知近似：")
    lines.append(
        "- 私有属性访问按访问方文件是否有同名 def/class、Name 赋值目标或 Attribute 赋值目标近似；"
        "无法区分同文件不同类。"
    )
    lines.append(
        "- 导入图把 from-import 的基模块与别名子模块都算边；TYPE_CHECKING 只识别 "
        "`if TYPE_CHECKING:` 与 `if X.TYPE_CHECKING:`，其他条件导入按加载期计。"
    )
    lines.append(
        "- 兼容字样只统计标识符与字符串字面量，不统计注释；同一标识符出现多个词时分别计数。"
    )
    lines.append(
        "- 共享状态只认配置的接收者名/属性名；键统计只覆盖字符串字面量，非字面量键无法统计。"
    )
    lines.append(
        "- 直接写接口只认静态 Call；动态 getattr 调用无法识别。适配器文件缺失会报错。"
    )
    lines.append(
        "- 正则自定义计数扫描原始源码文本，包含注释与字符串；调用名计数只看 AST Call 节点。"
    )
    lines.append(
        "- 测试分类由配置的路径模式与 test_roots 决定；测试 mock 助手导入别名不计。"
    )
    return "\n".join(lines) + "\n"


def _format_value(value: Any, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "int":
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)
    if kind == "share":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "%.1f%%" % (float(value) * 100.0)
        return str(value)
    return str(value)


def _percent_change(before: Any, after: Any) -> str:
    if (
        before is None
        or after is None
        or isinstance(before, bool)
        or isinstance(after, bool)
        or not isinstance(before, (int, float))
        or not isinstance(after, (int, float))
    ):
        return "—"
    if not before:
        return "—"
    change = (after - before) / before * 100.0
    if abs(change) < 0.05:
        return "持平"
    arrow = "↓" if change < 0 else "↑"
    return "%s %.1f%%" % (arrow, abs(change))


def embed_json(html_path: str | Path, script_id: str, card: dict[str, Any]) -> None:
    """Replace the contents of one application/json script block; no rendering."""
    path = Path(html_path)
    if not path.is_file():
        raise EmbedError("HTML file not found: %s" % path)
    html = path.read_text(encoding="utf-8")
    matches: list[tuple[int, int, re.Match[str]]] = []
    for match in _SCRIPT_RE.finditer(html):
        attrs = _attribute_map(match.group(1))
        if attrs.get("type", "").lower() != "application/json":
            continue
        if attrs.get("id") != script_id:
            continue
        matches.append((match.start(2), match.end(2), match))
    if not matches:
        raise EmbedError(
            "no <script type=\"application/json\" id=%r> block found in %s"
            % (script_id, path)
        )
    if len(matches) > 1:
        raise EmbedError(
            "multiple application/json script blocks with id=%r in %s"
            % (script_id, path)
        )
    start, end, _ = matches[0]
    payload = json.dumps(card, ensure_ascii=False, separators=(",", ":"))
    payload = (
        payload.replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    updated = html[:start] + "\n" + payload + "\n" + html[end:]
    path.write_text(updated, encoding="utf-8")


def _attribute_map(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw):
        name = match.group(1).lower()
        value = match.group(2) if match.group(2) is not None else match.group(3)
        attrs[name] = value
    return attrs
