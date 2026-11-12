# Python 可维护性记分卡

本工具按同一口径扫描多个 Git 快照，展示 Python 项目的规模、结构指标和变化趋势；不打总分、不排名，也不把代码行数当成绩。

适合在重构前建立基线、重构后核对结构变化，或定位值得检查的复杂函数、导入关系和代码热点。本工具不替代测试、代码审查、运行验证、覆盖率或缺陷统计。

## 快速开始

需要 Python 3.11+ 和本机 `git`。只用 Python 标准库，不导入被测项目。

在本仓库目录运行：

```bash
python3 scripts/scorecard.py \
  --repo /path/to/python-project \
  --config examples/scorecard.toml \
  --snapshot baseline=<重构前提交> \
  --snapshot current=<重构后提交> \
  --json out/scorecard.json \
  --markdown out/scorecard.md
```

测量时用 `git archive` 导出快照到临时目录，不 checkout 或修改被测仓库；临时目录在测量结束后删除。

`--snapshot LABEL=REV` 可以重复使用。命令行快照会覆盖配置里的 `[[snapshots]]`。只指定 `--json` 或 `--markdown` 也可以；都不指定时，JSON 会写到标准输出。

## Claude Code Skill

克隆仓库到 Claude Code 的 skill 目录：

```bash
git clone https://github.com/liuyejinghong/maintainability-scorecard.git \
  ~/.claude/skills/maintainability-scorecard
```

Claude Code 会读取目录中的 `SKILL.md`。需要时可请求“用 maintainability-scorecard 对比两个 Python 快照”。也可以直接运行：

```bash
python3 ~/.claude/skills/maintainability-scorecard/scripts/scorecard.py \
  --repo /path/to/python-project \
  --config /path/to/scorecard.toml \
  --snapshot baseline=<重构前提交> \
  --snapshot current=<重构后提交> \
  --markdown /path/to/scorecard.md
```

## 示例：扫描一个真实项目

下面是对 AItrading 交易网关（`live_trading/live_gateway`，Python 生产代码约 12 万行）重构前快照的扫描结果节选，用来说明报告里有哪几类内容。实际报告会把每个指标按快照并排对比，并附口径说明和出处。

**规模**

| 指标 | 值 |
| --- | ---: |
| 生产 Python 文件 | 191 |
| 非空非注释代码行 | 120,050 |
| 测试文件 / 测试代码行 | 226 / 158,499 |

**函数与复杂度**

| 指标 | 值 |
| --- | ---: |
| 巨型函数（自身超过 150 行） | 118 个，占函数代码 30.9% |
| 圈复杂度超过 20 的函数 | 348 个 |
| 全部函数复杂度合计 | 30,812 |
| 最长、最复杂的函数 | `RiskSyncBridge.ensure_risk_orders`：2,636 行，复杂度 641 |

**文件**

| 指标 | 值 |
| --- | ---: |
| 巨型文件（超过 500 行） | 46 个，占全部代码 81.0% |

**模块结构**

| 指标 | 值 |
| --- | ---: |
| 导入关系最大环（全部导入 / 排除类型标注导入 / 仅加载时导入） | 45 / 45 / 9 个文件 |
| 函数内导入 | 254 处 |
| 跨文件访问别人的私有名称 | 465 处 |
| 共享运行时状态访问 / 被 3 个以上文件写入的键 | 827 处 / 17 个 |
| 直接调用外部写接口（不经适配层） | 21 处，分布在 10 个文件 |

**异常与兼容残留**

| 指标 | 值 |
| --- | ---: |
| `except Exception` | 616 处 |
| 兼容残留字样（legacy、retired 等） | 415 处 |
| 带版本号的合同字符串 | 96 个，出现 165 次 |

**风险分档**（按函数圈复杂度，看各档代码占比，不只数超标个数）

| 复杂度 | 函数数 | 代码占比 |
| --- | ---: | ---: |
| 小于 5 | 1,829 | 16.4% |
| 5–9 | 747 | 16.1% |
| 10–24 | 577 | 25.5% |
| 25 及以上 | 281 | 42.1% |

同样的分档还有“函数自身行数”和“参数个数”两组。

**改动热点**（快照前 90 天的提交次数，与复杂度并列）

| 文件 | 提交数 | 复杂度合计 | 最长函数 |
| --- | ---: | ---: | ---: |
| `gateway/strategy_instance.py` | 122 | 3,064 | 1,165 行 |
| `trading_node.py` | 80 | 930 | 567 行 |
| `gateway/risk_guard.py` | 60 | 882 | 537 行 |

**模块索引检查**（可选，需要提供模块清单文件）：未登记模块的文件、一个文件归属多个模块、清单里的失效路径、各模块被依赖与依赖别人的数量、违反依赖方向的导入。这个项目当时有 32 个文件没有归属，75 条导入违反依赖方向。

这些数字只说明“哪里大、哪里复杂、哪里改得多、哪里互相缠绕”，用来决定先看哪里；本工具不据此打分或判断代码好坏。

## 指标一览

ISO/IEC 25010 的 5 个可维护性子特性用于整理观察结果，不代表本工具评估标准符合性。来源类型说明指标依据：标准、研究、工具默认值、项目约定或领域信号。阈值可按项目配置，不构成通用质量线。

| 维度 | 指标与用途 | 依据 |
| --- | --- | --- |
| 模块化 | 导入边、三种 SCC 和函数内导入，观察模块依赖；共享状态和直接写接口，检查项目约定；模块索引检查归属与依赖方向。 | 研究；领域信号；项目约定。 [模块设计原则](https://staff.cs.utu.fi/~jounsmed/doos_06/material/DesignPrinciplesAndPatterns.pdf) |
| 可复用性 | 跨文件私有名称访问代理，观察对其他文件内部名称的引用。 | 工具规则参考：[Ruff PLW0211](https://docs.astral.sh/ruff/rules/private-member-access/) |
| 可分析性 | AST 近似圈复杂度、宽泛异常捕获、兼容残留字样和版本化合同字符串；风险分档用于看函数分布。 | 研究、项目约定、工具默认值。 [NIST SP 500-235](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication500-235.pdf)；[SIG/TÜViT](https://softwareimprovementgroup.com/wp-content/uploads/SIG-TUViT-Evaluation-Criteria-Trusted-Product-Maintainability.pdf) |
| 可修改性 | 巨型函数、巨型文件和 Git 改动热点，用于定位需复核的修改成本信号。 | 工具默认值、项目约定、研究。 [OMG ASCMM](https://www.omg.org/spec/ASCMM/1.0/PDF)；[热点研究](https://arxiv.org/abs/2008.09569) |
| 可测试性 | 测试文件和行数、测试中的私有属性访问及 mock 助手引用，描述测试表面。不能据此判断覆盖率或测试有效性。 | 项目约定；ISO/IEC 25023 提供测量背景：[ISO 标准](https://www.iso.org/standard/35747.html) |

生产代码行数、文件数、自定义计数器和 AST 解析错误另作背景或测量有效性信息。模块 `Ca`/`Ce`/不稳定度 `I` 来自模块索引配置，不是 ISO 评分。

## 配置

从 [`examples/scorecard.toml`](examples/scorecard.toml) 复制并修改配置。主要字段：

- `[project]`：项目名、生产代码根目录和可选导入前缀。
- `[scope]`：扫描与排除的 Python 文件模式。
- `[thresholds]`：巨型函数、复杂度和巨型文件的项目阈值。
- `[tests]`、`[fallbacks]`、`[risk_tiers]`：测试路径、兼容字样和风险分档。
- `[module_index]`、`[shared_state]`、`[write_interfaces]`、`[[custom_counters]]`：可选的模块合同和项目专属指标。
- `[[snapshots]]`：可选的快照列表；也可在命令行提供快照。

配置支持 TOML 和 JSON。路径必须相对仓库根目录，未知键、重复项、错误类型和无效正则都会报错。写接口配置中的适配器文件必须存在于每个快照，否则测量失败。

## 已知局限

- AST 指标不执行代码，无法完整识别动态属性、运行时注册和动态导入。
- 私有属性访问按文件检查同名定义，无法区分同一文件中的不同类。
- 测试文件、测试行数和 mock 助手引用不代表覆盖率、断言质量或测试结果。
- Git 热点受提交习惯、批量格式化和生成文件影响；历史不足时会标为 `unavailable`。
- 正则自定义计数会扫描注释与字符串；调用名计数只识别 AST `Call` 节点。
- AST 解析失败的文件仍计入规模，但对应的 AST 指标不完整。先检查报告中的解析错误。

## 测试

在 skill 仓库目录运行：

```bash
python3 -m unittest discover -s tests
```

测试使用临时 Git 仓库和源码树。测试覆盖指标、配置、三种 SCC 口径、模块索引、风险分档和热点。测试也覆盖 JSON/Markdown 输出及 HTML JSON 块替换。

## 许可证

MIT，见 [`LICENSE`](LICENSE)。
