# v5 实施交付：源码候选视图与初始分组提示

实施基准：`action_v1@3cb5edd33672c2a347782237f5c5e83ca13e2030`。
发布范围：按用户后续指令，仅将本轮 v5 改动提交并推送至 `action_v1`；不合并 master。实际提交身份以本报告的 Git 历史为准。

**测试已编写，尚未运行；离线重放尚未运行；Planner 对照未执行；模型调用、真实生成和物理实验均未执行。** 也没有运行测试收集、编译检查或 smoke。只读 JSON、文本、AST 与 Git diff 的整理不属于新实验。

## 1. 先完成的证据整理

见 [雪人分件证据](snowman_initial_grouping_evidence_20261020.md)。96 KiB 雪人初始 Planner 已将鼻子、帽子和两臂独立，冻结配置 `require_multiple_parts=false`。Planner 鼻子分组理由是橙色外观和有利打印朝向；这是实际原文中的假设，不是测量证据。原源码即声明五件，第一次可测候选也五件；Engineering 后续只提接口边界修补并明确保持分组。没有合并鼻子的对照测量。

找到旧 `hat_body_partition_retry_20261013_v1`：仅帽子与身体的两件目标存在，但未被采用。最后续修源码执行失败，测量未完成，保留合格单件。它与新五件 NSV 初始计划分开记录。

## 2. 预算与工作区前提

HEAD 中为32 KiB；本轮开始时工作树已有96 KiB常量、配套测试和文档修改，本轮沿用，没有再提高。既有96 KiB测试结果不作为本轮新改动的验证结果。其他历史删除、FEA和实验脚本改动未纳入任务、未恢复或覆盖。

等预算对照基线已写入本地 `temp/source_candidates_v5_20261020/baseline_agent_feedback_96k.py`：从3cb5edd提取，只替换32→96 KiB；没有导入或执行。

## 3. 可审阅修改与接口

| 文件／实际锚点 | 改动 |
| --- | --- |
| `adsl-agents/agent_feedback.py:17` | 沿用96 KiB；代码级 `_COMPACT_ASSEMBLY_CANDIDATES` 回退开关 |
| 同文件 `_assembly_candidate_scope`（345附近） | 仅 Engineering/Coder/Code Critic；显式非空装配配置，或 Mapping context（包括{}）+合法类型的装配计划；area/NSV均适用 |
| 同文件 `_compact_source_candidates`（358附近） | 新字典，仅保留六种候选字段、原顺序与数值；不变 finding 的其他内容 |
| 同文件 `_Projection.candidate_ref`（582附近） | 校验 CURRENT 来源、实际文件哈希、pointer 列表、父 finding 身份、来源冲突、完整原候选结构、total 和已显示候选对应性 |
| 同文件 `_Projection.findings`（648附近） | 基础投影、原来源绑定／快照／关联完成后精简；`finding_for_agent`不改；保留并校验输入 preview，从完整原件恢复零数量省略的引用 |
| 同文件 `payload_for_agent` 与预算 reduce | 公共签名不变；仅已知容器递归；只对成功精简的候选局部保留 full_ref；未知 finding 和其他列表不新增规则 |
| `adsl-agents/prompts.py:23,48` | 装配 Coder/Engineering/两种Code Critic共同候选读取短文，area/NSV均有 |
| 同文件 `:50` 和 `prompt/support_grouping.md` | 仅 fixed-assembly NSV初始Planner追加计划指定的完整准则；独立于候选投影 |
| `scripts/feedback_size_report.py:164` 起 | 候选列表/字段统计、精简前后精确字段对照、全量候选引用检查；同一隔离路径比较、固定96 KiB；保留原阶段统计、来源及重复投影检查 |
| `tests/test_agent_feedback.py:1123` 起 | 候选合同与三角色实际方法的 fake runtime 捕获测试 |
| `tests/test_support_workflow.py:653` 起 | 提示只追加、实际 `_plan_generation` 输入及原 schema/尺寸保持测试 |
| `docs/support_optimization.md` | 记录精简合同、读取方式、启发式与未验证状态 |

完整引用优先沿用已有 full_ref，然后原报告，再使用既有恢复快照。显式已有引用失效时不拿短列表另造全集。没有可信完整候选的 finding 返回原视图；新增逻辑不合并跨finding的feature ID。候选各自省略位置/证据时，父级preview仍记录全集引用，omitted_count只表示数量省略。原 `detail_ref` 继续指向domain。

`temp/source_candidates_v5_20261020/candidate_projection.patch` 与 `planner_grouping.patch` 展示两个独立生产改动；前者包含已有96 KiB前提和候选测试，后者只含Planner追加接线与新资源。提示测试覆盖两类追加，另见测试文件diff。这些是审阅补丁，不是测试产物。

没有修改actions、fixed_assembly控制器、checker、schema、S/S/D³公式、τ、Dapper、权限或发布条件。原 `_support_prompt`、support_optimization提示与建模方法段落保持原文。

## 4. 已编写、未运行的验证

- 六候选字段精确相等、判定与权限等其余finding字段不变、原输入不变。
- 无数量裁剪和原九候选裁剪的full_ref、预算进一步裁剪、read_file JSON pointer取回全集。
- 缺文件、错误pointer、陈旧来源、来源冲突、错误文件hash、错误finding及仅摘要时回退。
- 既有恢复快照复用、重复投影不新增快照、不重展开候选；同feature跨finding独立引用。
- 三目标角色×area/NSV与三物理域，空context Engineering正例；非装配、未知finding、mesh/source错误、非目标角色反例。
- 所有约定容器/list checker_evidence路径，任意task_data不扫描。
- 实际engineer、_review_generation_code、_repair方法的runtime输入捕获（fake runtime），NO_CHANGE不修改源码。
- 提示剔除明确新增文本后等于旧组合；NSV Planner恰好一份grouping、area Planner无grouping；真实_plan_generation stub使用原输出类型和尺度。
- 旧support指纹、权限、预算错误和采纳/回退回归保持现有测试；本轮均未运行。开关关闭对照保持相同96 KiB。

本轮不能给出pass数量、实际压缩字节或回归通过结论。直接候选精简与最终预算结果需分开：精简后预算可能更早停止，保留更多历史，不能简单累加节省。

## 5. 重放材料与限制（未执行）

准备目录 `temp/source_candidates_v5_20261020/`：

- `engineering_replay_input.json`：已保存投影后输入，attempt_0001，assigned source实际哈希吻合；原报告参数来自当前context及原调用锚点。
- `coder_replay_input.json`：已保存投影后输入，标为RECONSTRUCTION_INCOMPLETE。它绑定attempt_0001，但assigned_source指向当轮已被修改的round_03文件；不能用最终源码冒充输入时源码。已识别同哈希parent源码，后续授权准备时可在独立副本恢复原位置，不能覆盖原档案。
- 两者均不是适配前payload，不支持完整原始请求压缩收益归因。没有自动重建或补造缺失材料。
- 未准备三个旧area案例的完整重建输入；本轮优先材料是雪人。后续如需它们，要先检查真实保存输入与源码时序，不能从总表补造请求。

脚本已增加候选统计及引用检查输出入口。本轮没有实际运行，不能保证这份雪人已低于预算。所有旧档案保持只读，本地仅写新的报告、补丁和重放描述。

## 6. 后续事项（均另行授权）

1. 先运行定向离线验证，再做**同配置NSV Planner对照**：原提示对仅追加grouping提示，固定任务、参考图、模型与通道、生成参数、权限；记录完整提示/配置hash。重复次数和seed在授权时确定，只Planner、不Coder/几何/物理；保留无效计划，件数下降不能证明可打印性。对照后再考虑完整生成。
2. **件数约束仍较弱**：新增一件和三件使用相同支撑阈值；切分/合并/同件数仍有不同分支。雪人初始五件没有由可选采纳规则决定，本轮未改公式。以后实际出现可选分组时记录ΔN、收益、接口变化及采纳再评估。
3. **area Planner暂不追加分组准则**：保留其Dapper提示。NSV对照有效后再单独决定推广及验证。area装配的候选精简和读取提示仍属于本轮。

交付状态：实现、测试文本及验证入口可审阅；反馈收益、Planner分组改善和物理验收尚未验证。
