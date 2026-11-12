---
name: maintainability-scorecard
description: Use when evaluating a Python project's complexity and maintainability before/after a refactor, or when generating report data. Measures scale, giant functions/files, cyclomatic complexity, module import cycles (three views), cross-file private access, broad exceptions, compatibility residue words, versioned contract strings, test quality, and configured project-specific shared state / write interfaces / custom counters across git snapshots. Self-contained, standard library only, never checks out the repository.
---

# Maintainability Scorecard

一个通用的 Python 项目可维护性记分卡。输入仓库路径、若干 git 快照和一份配置，每个快照用 `git archive` 导出到临时目录测量（不 checkout），输出完整 JSON 与一页 Markdown 摘要；可选把 JSON 写进已有 HTML 的 `<script type="application/json">` 块。

这个目录可以单独成为一个仓库：不 import 任何被测量项目，Python 侧只用标准库（`tomllib`、`ast`、`tokenize`、`tarfile`、`subprocess`、`json` 等）；快照导出依赖本机 `git` 命令。需要 Python 3.11+（`tomllib`）。

## 何时使用

- 重构前建立基线、重构后对比复杂度与可维护性。
- 向人汇报“结构变好还是变差”，需要可复核的数据。
- 找出巨型函数、巨型文件、导入环、跨文件私有访问、宽泛异常、兼容残留等结构性风险。
- 为多个 git 快照生成同一口径的趋势表。

不适用：单文件 lint、运行时性能分析、测试覆盖率、代码风格检查、打分排名。

## 快速开始

```bash
python3 scripts/scorecard.py \
  --repo /path/to/repo \
  --config examples/scorecard.toml \
  --snapshot baseline=<base-commit> \
  --snapshot current=main \
  --json out/scorecard.json \
  --markdown out/scorecard.md
```

- `--snapshot LABEL=REV` 可重复；省略时使用配置文件里的 `[[snapshots]]`。CLI 指定快照时完全覆盖配置快照。
- `--repo` 默认当前目录；`--config` 必填。
- 只给 `--json` / `--markdown` 中一个也可以；都不给时把 JSON 打到 stdout。
- `--embed HTML` 只替换已有 `<script type="application/json" id="scorecard-data">...</script>` 的内容，不生成页面；`--embed-id` 可换 id。
- 配置支持 `.toml` 和 `.json`；未知配置键直接报错，避免拼写错误静默关闭指标。

## 输入

| 输入 | 说明 |
| --- | --- |
| 仓库路径 | 必须是 git 仓库；快照解析为 `REV^{commit}`。 |
| 快照 | `label=revision`，label 只允许 `[A-Za-z0-9][A-Za-z0-9_.-]*`。 |
| 配置 | 项目范围、阈值、测试路径、项目专属指标；见下。 |

导出时只导出配置声明的 `source_roots` 与 `tests.roots`，临时目录在测量结束后删除。

## 指标来源、子特性与新增结构视图

报告不把指标聚合成总分、排名或通过门槛。JSON 的 `definitions` 保持原有字符串字段兼容；新增 `definition_metadata` 为每个定义提供 `display_name`、`iso_characteristic`、`source_type`、`source_url`、`threshold_status`、`role` 和 `limitations`。`source_type` 只使用 `standard`、`research`、`tool_default`、`project_convention`、`domain_signal`；`threshold_status` 只表示 `none`、`tool_default` 或 `local_config`。

Markdown 按 ISO/IEC 25010 可维护性子特性分节：模块化、可复用性、可分析性、可修改性、可测试性；背景指标与测量有效性单列。复杂度明确是 AST 近似圈复杂度，私有访问明确是跨文件私有名称访问代理，测试文件/行/mock/测试私有访问明确是测试表面代理。ISO 只提供组织观察维度的框架，不代表本工具进行标准符合性评估。

### 模块索引（可选）

启用 `[module_index]` 后，配置必须且只能提供 `fixed_file`（当前固定路径，所有快照使用同一份文件）或 `repo_path`（按快照从仓库归档读取），可选 `path_base`。索引采用 `[[module]]` 的 `id`、`name`、`parent`、`paths`、`may_depend_on` 与 `[[unassigned]]`；`paths` 支持显式路径和 glob。每个快照输出未归属文件、多叶子归属、失效路径、未匹配 .py glob、叶子模块文件/行数、Martin `Ca`/`Ce`/`I`、运行时违例样例和单列的 `TYPE_CHECKING` 违例。固定清单不存在或快照清单缺失时输出 `unavailable`，不把缺失当成零。

叶子判定：只有 `kind = "leaf"` 的 `[[module]]` 条目拥有文件；出现 `[[module]]` 却没有任何 `kind = "leaf"` 条目时直接报 `ConfigError`，不会静默把所有文件列为未归属。`paths` 的显式路径按“快照归档中是否存在该文件”判定，`stale_paths` 列出缺失项；glob 只按生产 `.py` 文件匹配，匹配不到任何生产 `.py` 的 glob 单列在 `unmatched_py_patterns`，不混入 `stale_paths`。

违例计数口径：每个运行时 import 语句计 1，模块顶层与函数内 import 都算（函数内 import 也是运行时依赖）；同一语句导入同一模块的多个名字只算 1；同一语句如果引用多个不同目标模块，按每个目标模块各计 1。排除 `if TYPE_CHECKING:` / `if X.TYPE_CHECKING:` 内的 import 并单列；常量字符串 `importlib.import_module("...")`（含 `from importlib import import_module` 后的裸名调用）解析后计入。from-import 先解析别名子模块，全部解析不到才回退基模块，避免每次 from-import 都把包 `__init__` 当成一条依赖。主字段 `runtime_violation_import_statements` 按上述语句口径计数；`runtime_violation_file_pairs` 是按“文件对去重”的辅助列，不能替代主数字。`.pyi` 是类型存根，不产生 import 边。

字符串常量 `importlib.import_module("...")` 会按同一项目模块解析，计入新的动态导入边与模块索引合同；旧 `module_tangle` 原有静态指标口径保持不变。

### 风险分档与热点

`risk_tiers` 按函数自身代码行、近似圈复杂度和参数个数输出各档函数数与代码行占比。参数统计位置参数、普通关键字参数和仅关键字参数，排除 `self`/`cls`，`*args` 与 `**kwargs` 单列。默认复杂度分界为 5/10/25、参数分界为 3/5/7（SIG/TÜViT 风险分布形式）；函数行数默认 100/150（分别参考 Sonar Python 与 Checkstyle 工具默认），均只作趋势参考，不输出星级。分档边界是 `value < threshold` 落入下一档，恰好等于阈值时落入更高一档（如复杂度 `5` 落入 `<10` 档，`25` 落入 `>=25` 档）。

`hotspots` 默认回看快照提交终点前 90 天，使用 `git log --no-renames`，统计每个生产文件提交次数和增删行，并组合原始复杂度总和与最长函数；排名只按提交次数、增删行和路径展示，不合成分数。窗口为 `[提交时间 - window_days, 提交时间]`；可按完整 SHA 或前缀（`exclude_commits`）以及 subject 正则排除批量格式化/生成提交。窗口内没有可用提交或 git 历史不可读时输出 `unavailable`。


### 规模

- **生产代码非空非注释行数（prod_sloc）**：`tokenize` 判定整行注释；空行不计；docstring 与多行字符串正文按代码行计，不做语义剥离。tokenize 失败的文件改用“不以 `#` 开头的非空行”近似，并单独记录 `sloc_tokenize_fallback_files`（正常应为 0）。
- **生产代码文件数（prod_files）**：范围内非测试 `.py` 文件数。
- **生产代码物理行数（prod_physical_lines）**：含空行与注释，只用于巨型文件行数占比。

**怎么读**：行数是背景不是成绩。删代码可能是在删能力，加代码可能是在加防护；不要用单一数字判断好坏，看同一口径下的结构指标变化。

### 巨型函数

- **own_lines** = 函数 AST 跨度 − 其内部嵌套函数/类跨度（合并重叠区间）。方法与嵌套函数各自计一次。
- `giant_functions.count`：own_lines > 阈值的函数个数（默认 150）。
- `giant_functions.line_share`：这些函数 own_lines 合计 / 全部函数 own_lines 合计。
- `giant_functions.longest`：own_lines 最长的函数及所在文件。

**怎么读**：长函数通常混合了多个状态转换和失败路径，难以单独测试。占比比个数更稳，因为文件总数和总行数会变。单个超长函数值得优先拆；分布性变长要看是否只是生成代码或数据表。

### 复杂逻辑

- **圈复杂度（近似）**：1 + `If`/`IfExp`/`For`/`AsyncFor`/`While`/`ExceptHandler`/`match_case`/`Assert`；`BoolOp` 每个额外操作数 +1；推导式每个 `for` +1、每个 `if` +1。统计函数自身跨度，嵌套定义的函数体不重复计入，但嵌套定义的装饰器计入外层。
- `complexity.count`：复杂度 > 阈值的函数个数（默认 20）。
- `complexity.top10_share`：最复杂 10 个函数复杂度合计 / 全部函数复杂度合计。
- `complexity.sum`：全部函数复杂度合计。
- `complexity.max`：最复杂函数。

**怎么读**：复杂度是代理指标，不等于 bug 数量。`top10_share` 上升说明风险向少数函数集中；`sum` 下降可能只是代码变少。结合巨型函数和测试指标看。

### 巨型文件

- `giant_files.count`：生产文件物理行 > 阈值的个数（默认 500）。
- `giant_files.line_share`：这些文件物理行合计 / 生产物理行合计。

**怎么读**：500 行是模块边界的信号，不是机械拆分线。按职责、依赖方向和事务边界拆；不要为了数字把需要一起理解的逻辑拆散。

### 模块缠绕（导入图强连通分量）

节点是生产 `.py` 文件；边是项目内 import：

- 绝对导入按配置的 `module_prefixes` 去掉项目前缀后解析。
- 相对导入按当前包解析。
- `from pkg import x` 同时解析基模块 `pkg`（若存在）和别名子模块 `pkg.x`。
- 非项目内 import 不产生边。

给出三行，必须分开看：

| 指标 | 含义 |
| --- | --- |
| `module_tangle.max_scc_size` | **全部边**的最大强连通分量，含函数内 import 与 TYPE_CHECKING import。 |
| `module_tangle.max_scc_size_excluding_type_checking_edges` | 去掉 `if TYPE_CHECKING:` / `if X.TYPE_CHECKING:` 内 import 边后的最大 SCC。 |
| `module_tangle.max_scc_size_top_level_imports` | 只保留模块顶层、非 TYPE_CHECKING 的加载期 import 边后的最大 SCC。 |

另有 `import_edges`（全部去重边数）、`import_edges_excluding_type_checking`、`top_level_import_edges`、`function_local_imports`（函数体内解析到项目模块的 import 语句条数）。

**怎么读**：SCC 大小 1 表示无环。判断真实运行时纠缠优先看“仅顶层加载期 import”一行；它大，说明模块在加载时就互相依赖，拆分和测试隔离都难。全部边很大但顶层行很小，说明纠缠主要来自函数内延迟 import 或仅类型导入；这仍可能是为打破循环而付出的维护成本，`function_local_imports` 会上升。TYPE_CHECKING 行介于两者之间，用来区分“类型注解需要”与“运行期需要”。

### 跨文件私有属性访问

统计 `obj._name` 形式的单下划线私有属性访问（`__dunder__` 不算），近似判断是否跨文件访问了别人的私有实现。

**修正后的口径**：访问方文件中任何 `_name` 的定义都算该文件内定义，不计入越界：

1. `def _name` / `class _name`；
2. Name 赋值目标，例如 `_name = ...`、`for _name in ...`、`with ... as _name`；
3. Attribute 赋值目标，例如 `self._name = ...`、`cls._name = ...`、`obj._name = ...`，包括注解赋值和增量赋值。

只要访问方文件里有同名定义，该文件所有同名 Attribute 访问都不计数。

**怎么读**：这是近似指标，不是封装违规证明。它无法区分同一文件中的不同类（A 类定义 `_x` 会掩盖 B 类对 `other._x` 的访问），也不识别动态属性。用于看趋势：跨文件私有访问上升，通常说明模块边界在退化或测试在依赖内部实现。

### 宽泛异常捕获

- `except_exception`：`except Exception:` 或元组中包含 `Exception` 的 except 块数。
- `bare_except`：裸 `except:` 块数。
- `broad_except_total`：两者合计。

**怎么读**：宽泛捕获会把真实失败变成静默降级；总数上升需要看是否新增了吞错路径。`except Exception` 有时是必要的边界兜底，不能只看数字。

### 兼容残留字样

- 默认词表：`legacy`、`compat`、`retired`、`historical`、`deprecated`，配置可改。
- 统计标识符（Name、Attribute、函数/类名、参数、关键字、导入别名）与字符串字面量中的出现次数；不统计注释；不区分大小写；每次出现计 1。
- **词边界**：一般词要求前后不是 ASCII 字母或数字，下划线和连字符视为分隔符。因此 `legacy_path` 和 `my_legacy` 计 1，`legacy2` 和 `xlegacy` 不计。
- **compat 例外**：只匹配独立 `compat` 或 `compat_` / `compat-` 前缀；`compatible`、`compatibility`、`my_compat` 不计。

**怎么读**：兼容字样上升说明补丁、双路径或历史兼容层在增加。它不区分“必要的迁移期兼容”和“忘记删除的残留”；结合版本化合同字符串和自定义计数看。

### 版本化合同字符串

- `versioned_contract_strings_distinct`：完整字符串字面量匹配配置正则（默认 `[A-Za-z0-9_.-]+/v[0-9]+`）的**真实去重**个数。
- `versioned_contract_strings_occurrences`：上述匹配的出现次数。

**怎么读**：版本化合同字符串是跨进程/持久化/协议的兼容面。数量快速增长通常意味着合同版本没有收敛；出现次数高于去重数说明同一合同在多处重复声明。f-string 中的常量片段也会被当作字符串字面量扫描，这是已知近似。

### 测试质量

- `test_files` / `test_lines`：测试代码文件数与物理行数。
- `private_attr_accesses`：测试代码中 `obj._name` 次数（不做定义方判断）。
- `mock_helpers`：测试代码中配置助手名（默认 `MagicMock`、`monkeypatch`、`SimpleNamespace`）的 Name/Attribute 引用次数；导入语句本身不计。

测试分类：`source_roots` 下匹配 `tests.patterns` 的文件、以及 `tests.roots` 下所有 `.py` 都算测试；生产文件排除测试文件。

**怎么读**：测试行数是背景不是成绩。测试私有访问高说明测试耦合内部实现，重构时容易碎；mock 助手引用上升可能意味着集成验证变少、测试替身变多。不要用测试行数单独证明质量。

### 项目专属指标（写在配置里）

#### 共享状态对象

配置接收者名字（如 `runtime`、`rt`）和属性名（如 `.runtime`、`._runtime`），统计：

- `.get` 读、`[]` 读、`[]` 赋值、`.update`、`.pop`、`.setdefault`、`runtime.attr = ...` 赋值；
- `update({...})`、`update(key=...)`、`[] =`、`setdefault` 的字符串字面量键；
- `keys_written_by_ge_n_files`：被至少 N 个文件写入的键数。

**怎么读**：访问次数高说明共享可变状态耦合重；被多个文件写入的键事实上是全局变量。只认配置的接收者名和字面量键，`to_dict()` 副本、动态键无法识别。

#### 直接外部写接口

配置函数名（如 `signed_post`、`signed_delete`）和适配器定义文件：

- `count` / `files` / `sites`：生产代码中直接调用这些函数的 Call 节点数、文件数、位置；
- 适配器文件本身排除，避免把封装实现算成调用点；
- **适配器文件在每个快照中必须存在，否则直接报错**。不能静默失效：配置写错或文件被删时，调用点计数会虚假升高/降低，必须让运行失败。

**怎么读**：每个直接调用点都要自己处理幂等、重试、超时和结果未知；数量应随适配器收口而下降。动态 `getattr` 调用无法识别。

#### 自定义计数

每个计数器二选一：

- `call_names`：AST Call 节点中 Name 或 Attribute 名字匹配的次数；
- `regex`：原始源码文本中正则匹配的次数（含注释和字符串，非重叠匹配）。

**怎么读**：用于项目自定义的降级路径、TODO、特定 API 等。正则计数把注释也算进去，适合“搜索字样”；调用名计数适合“真实调用点”。

## 配置文件

完整示例见 [`examples/scorecard.toml`](examples/scorecard.toml)。

```toml
[project]
name = "example"
source_roots = ["src"]                 # 必填，仓库相对路径
module_prefixes = ["mypkg"]            # 可选，绝对导入前缀

[scope]
include = ["**/*.py"]                  # 相对 source root
exclude = []

[thresholds]
giant_function_lines = 150
complexity = 20
giant_file_lines = 500

[tests]
patterns = ["test_*.py", "conftest.py", "*_test_support.py", "**/testsupport/**"]
roots = []                             # 额外的测试根目录
mock_helpers = ["MagicMock", "monkeypatch", "SimpleNamespace"]

[fallbacks]
words = ["legacy", "compat", "retired", "historical", "deprecated"]
contract_string_pattern = "[A-Za-z0-9_.-]+/v[0-9]+"

[risk_tiers]
complexity = [5, 10, 25]
own_lines = [100, 150]
parameters = [3, 5, 7]

# Optional: exactly one of fixed_file or repo_path.
# [module_index]
# fixed_file = "/absolute/path/to/module-index.toml"
# path_base = "src"
# repo_path = "docs/module-index.toml"

[hotspots]
window_days = 90
exclude_commits = []
# exclude_subject_regex = "format|generated"

# 可选：共享状态
[shared_state]
receiver_names = ["runtime", "rt"]
receiver_attributes = ["runtime", "_runtime"]
file_threshold = 3

# 可选：直接外部写接口
[write_interfaces]
call_names = ["signed_post", "signed_delete"]
adapter_files = ["src/gateway/exchange_client.py"]

# 可选：自定义计数
[[custom_counters]]
name = "mark_degraded_calls"
call_names = ["mark_degraded", "_mark_degraded"]
description = "降级路径"

[[custom_counters]]
name = "todo_markers"
regex = "TODO"

# 可选：快照；也可用 --snapshot 覆盖
[[snapshots]]
label = "baseline"
revision = "<base-commit>"
reason = "重构前基线"
```

规则：

- 路径一律仓库相对、使用 `/`；拒绝绝对路径和 `..`。
- `[project]` 和 `source_roots` 必填。
- 未知键、错误类型、无效正则、重复词/重复快照标签/重复计数器名都会报错。
- `fallbacks.words` 大小写不敏感去重。
- `custom_counters` 每项必须且只能有 `call_names` 或 `regex` 之一。
- `adapter_files` 必须在每个快照导出范围内存在。

## 输出

### JSON

顶层：

- `schema_version`
- `generated_at`（UTC）
- `method`（说明 archive 测量，不 checkout）
- `config`（所有影响结果的配置回声）
- `definitions`（每项指标的精确口径；阈值已代入）
- `snapshots`：label → `requested` / `sha` / `short` / `committed_at` / `subject` / `reason` / `metrics`

`metrics` 分组：`scale`、`giant_functions`、`complexity`、`giant_files`、`module_tangle`、`private_access`、`fallbacks`、`tests`、`parse_errors`、`risk_tiers`、`hotspots`，以及配置启用时的 `module_index`、`shared_state`、`write_interfaces`、`custom`。

### Markdown

一页表格：快照列表、每项指标在每个快照的值与首尾变化、最长/最复杂函数、解析失败、已知近似。行数明确标注为背景，不构成单独质量结论。

### HTML 数据块

`--embed` 只做替换：

```html
<script type="application/json" id="scorecard-data">{ ... }</script>
```

脚本按属性匹配，`id` 与 `type="application/json"` 顺序不限；找不到或重复目标块直接报错；JSON 中的 `</`、U+2028、U+2029 会被转义。不生成 HTML 页面、不注入脚本。

## 已知误差

- 模块 `Ca`/`Ce`/`I` 用导入边视图（含 from-import 的基模块边与动态导入边），与“违反依赖方向”的语句计数口径不同，不要用 `Ca`/`Ce` 反推违例数。
- 违例语句按（文件、行号、来源、目标）去重：同一行用分号写两条 import 会少计 1；相对导入层级超过包深度时按绝对名解析。

- 私有属性访问按“访问方文件是否有同名定义”近似，无法区分同文件不同类，也无法识别动态属性。
- 导入图把 from-import 的基模块与别名子模块都算边；相对导入按包名解析；`module_prefixes` 未配置时绝对导入可能解析不到项目模块。
- 动态 `importlib.import_module` 只在新增模块索引/边界合同视图中计入；旧 `module_tangle` 的历史指标保持静态 import 口径，避免旧数值漂移。
- source root 自身的顶层 `__init__.py` 不映射为模块名（空模块名），因此包名本身不会成为导入图节点；把包目录作为 source root 时需用 `module_prefixes` 解析绝对导入。
- TYPE_CHECKING 只识别 `if TYPE_CHECKING:` 与 `if X.TYPE_CHECKING:`；其他条件导入按加载期计。
- 兼容字样只统计标识符与字符串字面量，不统计注释；f-string 的常量片段会按字符串扫描。
- 共享状态只认配置的接收者名/属性名；键只统计字符串字面量。
- 写接口只认静态 Call；`getattr`、字典分发、字符串拼接的调用无法识别。
- 正则自定义计数扫描原始源码，包含注释和字符串；非重叠匹配。
- 测试分类依赖配置模式；mock 助手的导入别名不计。
- AST 解析失败的文件仍计入规模，但函数、复杂度、导入图、私有访问、兼容字样等 AST 指标不包含它们；`parse_errors` 会列出。
- SLOC 在 tokenize 失败时用行首 `#` 近似，`sloc_tokenize_fallback_files` 非 0 时应先检查文件是否合法 Python。

## 公开质量模型的使用边界

ISO/IEC 25010:2023 可维护性包含模块化、可复用性、可分析性、可修改性和可测试性。本工具只借这些子特性组织观察结果，不声称符合 ISO；LOC 与测试行数始终是背景，复杂度、私有名称访问和静态导入都是代理，不能单独推出缺陷率、测试有效性或实际修改成本。[ISO/IEC 25010](https://www.iso.org/standard/78176.html)

测量值依赖范围、语言、算法和阈值，跨工具或跨项目不自动可比；解析错误会让 AST 指标不完整，应优先确认测量有效性。[ISO/IEC 25023](https://www.iso.org/standard/35747.html) [ISO/IEC 25020](https://www.iso.org/standard/72117.html)

复杂度分布参考 McCabe/NIST 的控制流与测试设计用途，默认风险分档借鉴 SIG/TÜViT 的占比分布，不复制其商业星级。参数分界采用报告列出的公开工具/规范参考；阈值可以按项目配置，均不是统一质量线。[McCabe/NIST SP 500-235](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication500-235.pdf) [SIG/TÜViT](https://softwareimprovementgroup.com/wp-content/uploads/SIG-TUViT-Evaluation-Criteria-Trusted-Product-Maintainability.pdf) [Ruff](https://github.com/astral-sh/ruff/blob/main/crates/ruff_linter/src/rules/pylint/settings.rs) [OMG ASCMM](https://www.omg.org/spec/ASCMM/1.0/PDF)

模块 `Ca`/`Ce`/不稳定度 `I` 没有普适 cutoff；模块合同表达项目选择。Git 热点受提交习惯、重命名、批量格式化和生成文件影响，缺少足够历史时应标 unavailable。静态分析无法完整观察 Python 动态导入、运行时注册或过程质量。[Martin](https://staff.cs.utu.fi/~jounsmed/doos_06/material/DesignPrinciplesAndPatterns.pdf) [Git history hotspot research](https://arxiv.org/abs/2008.09569)

因此本工具只报告指标定义、分布、趋势和显式合同结果；不打总分、不排名、不设总体通过门槛。合同检查可单独 pass/fail，但不与总体质量混合。公开模型是观察框架，不是本工具的合规声明。

## 不做的事

- 不生成 HTML 页面或图表；`--embed` 只替换已有 JSON 块。
- 不打分、不排名、不设通过门槛。
- 不 checkout、不修改被测量仓库；所有测量在 `git archive` 的临时目录中完成。
- 不判断业务正确性；结构指标只能提示风险，不能替代测试、审查和运行验证。

## 自带测试

```bash
python3 -m unittest discover -s tests
```

测试使用临时合成仓库（`git init` + 提交，可指定提交时间）和临时源码树，覆盖每项指标计数、私有访问三类定义、SCC 三种口径、compat 词边界、适配器缺失报错、配置阈值、JSON/Markdown 输出和 `--embed` 替换。模块索引覆盖 `Ca`/`Ce`/`I` 公式、允许依赖不计违例、`TYPE_CHECKING` 单列、语句计数单位与 from-import 别名解析、显式失效路径、未匹配 .py glob、0 叶子报错和 Markdown 叶子表；风险分档覆盖恰好等于阈值的落档；热点覆盖窗口边界、`exclude_commits`、subject 排除和 `unavailable`。
