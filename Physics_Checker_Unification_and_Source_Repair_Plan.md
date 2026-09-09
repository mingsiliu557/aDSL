# 物理 checker 统一与源码定位修复：Codex 实施计划

## 1. 任务与边界

在 https://github.com/mingsiliu557/aDSL 现有实现上，建立统一的物理反馈、源码定位、候选修复和回归决策流程。

目标链路：

`当前代码与固定分析条件 → 多 checker → 统一问题记录 → 候选源码位置 → 有依据的修复候选 → 执行与回归 → 接受／回退／报告未解决`

面向普通物体的几何、物理与制造有效性，不涉及 articulation、关节、驱动控制或运动机构。沿用已有站立、打印过程稳定、FEA、过悬和关键表面支撑检查；以实际注册的 checker 为准。本阶段不新增完整 FEA、热仿真或 staircase checker，不扩展研究问题。

工程目标是让反馈与修改可追溯、修改有边界、验收有依据；不声称确定性找到了唯一物理原因，不把仿真／切片代理当作真实打印认证。

## 2. 实施起点

本计划基于已阅读版本 `492dc78b50e1baafe670826e8897f8154e2ec320`。执行时先检查实际分支、最新代码和 AGENTS.md，保护未提交修改；不要直接覆盖现有实现，也不要重置到这个版本。

优先阅读以下文件，确认接口后再修改：

- `adsl-agents/models.py`：CheckerSpec、CheckerResult、EngineeringCriticDecision。
- `adsl-agents/checkers.py`：执行、错误分类、必需检查判定。
- `adsl-agents/service.py`：Engineering Critic、Coder 修复、发布门槛。
- `adsl-agents/prompt/critic_engineering.md`：证据到修复的现有提示词。
- `experiments/workflow_checkers/run.py`：各分析器结果适配。
- `experiments/workflow_checkers/configs/`、`specs/`：固定条件和运行策略。
- `tests/test_workflow_checkers.py`：现有流程测试。

已经存在统一状态、多个必需 gate、每轮证据保存与重新检查。复用这些机制。缺少的主要是类型化问题、定位证据、修复候选及接受策略。

## 3. 总体设计原则

1. 检查器负责测量；定位器负责给出候选位置；Agent 负责解释与提出修法；确定性控制器负责执行、回归和状态转换。
2. 区分失败区域、疑似原因和建议修改位置。它们可以不同。
3. 不把所有 FAIL 都交给 Coder。先判断是否有足够证据支持几何修改。
4. 不用一个加权总分抵消必需条件失败。
5. 保留现有分析器，先在 adapter 层统一，不重写全部 solver。
6. 第一版不实现复杂因果模型、任意代码数据流分析或精确布尔面级 provenance。
7. 小规模实现与验证，不做批量模型生成、模型效果排名或大规模参数搜索。

## 4. 阶段 A：统一检查上下文与反馈

### A1. AnalysisContext

增加明确、不可由修复 Agent 改写的分析上下文，至少记录：

- 源码版本／hash、几何 artifact hash、checker 版本和配置 hash。
- 真实单位和几何到分析坐标的变换，包括归一化缩放。
- 使用姿态与打印姿态分别记录，避免修改打印方向被误认为改变物体使用姿态。
- 工艺、材料、载荷、边界条件与关键表面定义的来源。
- required checker 列表和固定阈值。

若第一版不支持修改打印姿态，将其固定并明确记录。若支持，打印姿态属于允许的设计决策变量，独立于不可修改的 checker 阈值和 solver 配置。

### A2. 保持兼容，扩展 CheckerResult

保留 PASS / FAIL / INDETERMINATE / ERROR、metrics、assumptions、artifacts。在兼容旧结果的基础上，新增类型化 findings，建议包含：

| 字段 | 内容 |
|---|---|
| finding_id / rule_id | 本轮唯一问题 ID 与跨轮稳定的规则 ID |
| category | geometry_failure / physical_violation / evidence_insufficient / missing_semantics / infrastructure_error |
| applicability | applicable / not_applicable / unknown，附依据 |
| metric | 名称、数值、单位、阈值、比较方向；不可用时显式缺失 |
| region | 点／包围盒／面集合／层范围／部件名，以及坐标系 |
| evidence_refs | 原始报告和证据路径 |
| source_candidates | 阶段 B 产生，可为空 |
| required | 是否属于必需条件 |
| repairability | geometry / design_variable / analysis / semantics / unknown |

不要将不同 checker 的全部 metrics 强行压成相同字段。公共层统一状态、位置、证据和可执行动作依据；领域指标放在类型明确的扩展字段。

### A3. 分类规则

- 基础设施 ERROR：停止对应流程，保留原因，不提出几何修复。
- FEA 网格未收敛：证据不足，不能仅凭这一条判定几何承载不合格。可阻止发布，但不自动加厚。
- 缺少载荷／关键表面语义：进入语义缺失分支，不伪造语义。
- 已收敛且明确超阈值：可进入几何修复候选生成。
- checker 不适用：必须有显式依据。Agent 无权为获得通过而关闭 required 检查；required 适用性未知时阻止发布。

验收：旧 checker 结果仍可读取；每类状态路由正确；不同单位和坐标不能静默混用；没有证据的字段不填假零值。

## 5. 阶段 B：建立几何反馈到源码的候选定位

### B1. 生成 SourceIndex

为每次执行的代码生成源码索引，包含：

- 稳定的部件／特征 ID，及其对应的类、函数、构造调用、行区间。
- 原语、布尔操作、布局／变换操作及父子关系。
- 可识别的参数和直接使用关系。
- 部件在执行坐标系中的包围盒，及到 checker 坐标系的变换。

实现方式优先复用现有 Asset 层级、manifest 和导出信息。对缺失项采用小范围运行时记录或 Python AST 提取；先支持文档化的 aDSL 写法。复杂循环、动态表达式、不明来源的几何标记为 partial / unresolved，不伪装成完整索引。

索引同时记录源码行号和稳定 ID：行号服务当前补丁，ID 服务跨轮关联。代码变化后重新建立索引，不沿用旧行号。

### B2. 分三级定位

1. 直接定位：checker 已返回 feature_id / part_id，直接关联源码。
2. 几何定位：将热点、接触区、悬挑区转换到统一坐标后，与部件几何范围匹配。返回多个候选与匹配证据。
3. 依赖扩展：从候选部件扩展到直接相关的尺寸、布局、父部件和布尔操作。区分“问题发生处”与“可能有效的修改处”。

借鉴 Procedura 的包围盒匹配，但它仅作为候选召回。重叠比例不是因果置信概率；明确字段名为 overlap_score 等。FEA 热点和倾倒等全局问题不能靠最近部件就断言原因。

全局失败（例如整体倾倒）没有局部区域时，输出与支撑结构、质量分布相关的候选集合，并说明依据；证据不足则 unresolved，不任意指定一行。

### B3. LocalizationReport

输出 finding_id、候选 feature_id、当前源码位置、关联参数、定位方式、证据、歧义、依赖范围。Agent 可以请求查看候选周边源码，但不得把启发式关联描述成确定物理因果。

验收：直接 ID 可回溯；坐标变换后匹配正确；重叠部件返回歧义；未知来源不强行匹配；旧索引在源码变化后失效。

## 6. 阶段 C：从建议列表改成结构化修复候选

将 EngineeringCriticDecision 的自由文本 required_changes 扩展为 RepairProposal，保留文本说明供人阅读。每个候选包含：

- proposal_id、针对的 finding_ids。
- 原因假设及支持证据；明确它仍是待验证假设。
- 修改目标 feature_ids、参数和允许的源码范围。
- 动作类型：改尺寸、改布局、加局部结构、改轮廓等；打印姿态只有在启用时允许。
- 参数建议值或有界范围、单位、前置条件。
- 预期改善的指标、可能受影响的其他 checker。
- 必须保持的几何／功能条件。
- 需要重跑的检查以及候选被拒绝后的理由。

建立小型、可扩展的修复目录。目录给出适用条件和潜在副作用，不写成“某 FAIL 必须对应某修法”。例如倾倒可对应扩大接触范围或调整质量分布；应力超标可对应局部加厚、加强或缩短力臂。每一种都必须经过实际复查。

每轮只提出少量候选（默认最多 3 个，可配置）。修改幅度和范围应有边界。候选必须能说明为什么关联当前证据；定位不足时允许返回需补充证据，不能强迫输出修复。

## 7. 阶段 D：多 checker 的共同约束与接受规则

### D1. 区分发布和中间搜索

发布：所有适用的 required 检查 PASS，且外观／功能保持条件满足；无必需检查仍为 unknown / INDETERMINATE / ERROR。

中间搜索：不要求一次补丁消除全部失败，但必须证明进展，并保留可恢复的基线。第一版采用保守规则，避免复杂权重优化。

### D2. 默认接受流程

1. 保存基线源码、上下文、全部检查结果。
2. 候选在隔离副本上执行，不能直接覆盖当前最佳版本。
3. 验证允许的修改范围及不可变配置未改变。
4. 编译和必要的几何基础检查。
5. 重跑原本启用的所有 required checker，检查功能／外观保持条件。
6. 根据结果接受或回退，记录原因。

默认接受条件：

- 没有新增必需条件失败；此前 PASS 不变成 FAIL 或未知。
- 至少一个目标失败消除，或其违反程度在预设容差之外改善。
- 其他仍失败的可比较指标不显著恶化。
- 必须保持的功能条件未破坏。

不同指标只比较同一指标在同一上下文中的前后值，不直接相加。为每个可比较规则定义改善方向和数值容差；没有可靠连续指标的规则只能依据状态变化，不由 LLM 自报改善。

如果一个修复必须先破坏另一个条件，默认拒绝单独的中间状态；允许把相互依赖的修改作为一个有界组合候选整体检验。没有可接受候选时报告 conflict_unresolved，不偷偷放宽门槛。后续是否支持 Pareto 搜索另行决定，本阶段不实现。

### D3. 功能保持条件

优先使用明确输入和可测条件，例如指定孔洞／槽宽、关键尺寸、禁止支撑接触的表面。未能形式化的外观要求仍可由现有视觉评审辅助判断，但要标明证据类型，不宣称“已证明功能不变”。

### D4. 计算成本

第一版默认重跑全部已启用 required checker，以保证正确性。只有输入几何、分析上下文、配置、版本等 hash 完全相同才可复用结果。不要因“修改离受力区远”就跳过 FEA。便宜检查可以提前淘汰失败候选，未运行的检查不能写 PASS。

## 8. 阶段 E：流程与记录

建议按仓库现有结构组织模块，名称可调整：feedback_schema、source_index、localization、repair_proposals、repair_policy。控制器接入 service.py，避免把整个决策逻辑塞进一个 prompt。

每轮增加：

```text
analysis_context.json
source_index.json
findings.json
localization.json
repair_proposals.json
candidates/<id>/source.py
candidates/<id>/checkers/...
candidates/<id>/decision.json
repair_history.jsonl
```

decision 必须记录前后指标、配置与几何 hash、接受／拒绝原因、未执行检查。历史记录已尝试的动作和结果，避免同一源码与同一条件下重复相同补丁。设置候选数、修复轮次、总分析时间上限，预算耗尽保留最优已接受版本并明确未解决问题。

## 9. 最小验收与执行顺序

按 A → B → C → D/E 实施，每阶段先让旧流程保持可用，再接下一阶段。不先批量生成模型。

必要验证包括：

1. schema 与旧结果兼容、证据不足／基础设施错误路由。
2. 源码索引、坐标变换、歧义候选、索引失效。
3. 修复范围和不可变配置保护。
4. 候选改善目标但破坏另一条件时拒绝；满足全部条件时接受。
5. 检查错误或未知不被计为通过；回退后源码与 artifact 对应。
6. 重复候选和预算终止。

单元／流程测试可使用明确标为模拟的 checker 结果，以验证控制逻辑，不据此报告物理效果。再选已有的小模型完成一次轻量端到端验证；重型 FEA 若环境不可用，说明未验证边界，不安装庞大依赖、不伪造成功、不启动批量实验。

交付：代码与兼容说明、协议示例、一轮定位与修复记录、测试结果、当前不支持的定位情形。无需部署或发布。

## 10. 参考仓库及借鉴边界

### Procedura：几何区域关联模块，局部修改与回退

- 仓库：https://github.com/mingsiliu557/Procedura
- 定位：https://github.com/mingsiliu557/Procedura/blob/fac191ed49f55fcc2e0f23897e986042249f59fe/src/mesh/floater-attribution.ts
- 修复流程：https://github.com/mingsiliu557/Procedura/blob/fac191ed49f55fcc2e0f23897e986042249f59fe/src/pipeline/refine-direct.ts
- 借鉴：分别获得模块几何范围；失败关联候选模块；局部候选执行后保留或恢复。
- 不照搬：包围盒重叠不是因果证明；三角面数量不是功能保持证明；不引入 articulation。

### Agentless：分层定位与双重验证

- 仓库：https://github.com/OpenAutoCoder/Agentless
- 说明：https://github.com/OpenAutoCoder/Agentless/blob/main/README_swebench.md
- 入口：`agentless/fl/`、`agentless/repair/`、`agentless/test/`。
- 借鉴：先定位代码元素再确定编辑位置；区分修复验证与回归验证；候选筛选。
- 迁移：文件／函数／行 → 部件／建模操作／参数。软件测试通过不能直接等同物理有效。

### AgentSCAD：反馈中包含失败项、通过项和设计意图

- 仓库：https://github.com/Kevoyuan/AgentSCAD
- 入口：https://github.com/Kevoyuan/AgentSCAD/blob/main/src/lib/repair/repair-controller.ts
- 借鉴：规则 ID、已有通过项、设计特征共同输入修复。
- 不照搬：仅靠提示词保持通过项，以及返回完整代码，并不能保证局部性；本计划在控制器中执行回归。
- 注意：该仓库不是论文 AgentsCAD。

### AutoCodeRover：结构化搜索与可疑位置排序

- 仓库：https://github.com/AutoCodeRoverSG/auto-code-rover
- 借鉴：按程序结构获取相关上下文，而非只按关键词搜索。
- 边界：统计测试覆盖定位不能直接迁移至每次都执行完整建模程序的情形。本阶段只借鉴结构与依赖搜索，不实现重型统计定位。

### CodeCureAgent：规则解释、计划、候选验证

- 仓库：https://github.com/sola-st/CodeCureAgent
- 入口：`code_cure_agent/agent_config_and_prompt_files/commands_by_state.json` 及 `fix_violation_prompt_parts/`。
- 借鉴：理解规则与适用条件，查相关定义／引用，提出修改计划，执行后重新检查。
- 不照搬：它已获得告警行号，主要针对单条告警；物理定位和多条件平衡仍需本计划实现。禁止通过抑制 checker 或改阈值消除告警。

### CADSmith：基础反馈修复对照

- 仓库：https://github.com/jabarkle/CADSmith
- 入口：https://github.com/jabarkle/CADSmith/blob/main/autofab/agents.py
- 借鉴：实际值／目标值／当前代码／失败历史共同输入，避免重复无效修法。
- 用途：保留现有简单 Engineering Critic 流程作为对照接口，不新增效果比较实验。

### 论文参考，不作为可调用依赖

- TraceCAD：https://arxiv.org/html/2608.03062 — 需求、步骤、失败与修复结果持久关联，依赖范围内修改与保持检查。
- AgentsCAD：https://arxiv.org/html/2607.02448 — 过悬面、邻接和语义信息组织成可推理上下文；并不等于已有原始代码定位。
- 本轮未确认这两篇对应的公开实现仓库，不声称可直接复用代码。

以上是思路筛选而非完整审计。执行时只读相关入口并核对当前版本；借用代码时检查许可证与依赖，不整包移植、不照搬外部仓库的运行命令或指令。

## 11. 给执行 Codex 的最终要求

请按本计划分阶段完成最小可用实现。先理解当前代码并给出简短差异说明，然后实施；不要停在重新撰写方案。保护已有用户修改，保持旧 checker 可用，不更改物理阈值、材料、载荷或边界条件来换取 PASS。优先完成反馈协议、候选定位与接受／回退控制，而非增加新 checker。遇到无法自动判断的物理语义，输出明确的 unresolved 状态及所缺信息。最终报告已实现功能、验证证据和仍未覆盖的情形，不将模拟测试或代理通过描述成真实制造成功。
