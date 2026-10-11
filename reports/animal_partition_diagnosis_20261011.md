# 动物打印分件排查与 ours 暂停记录

日期：2026-10-11（UTC）。状态：**读取已有源码、计划、版本账本和调用日志完成排查；尚未实施修复。ours 当前批次已按用户要求暂停。**

## 1. 范围与主要结论

核对仓库为 [mingsiliu557/aDSL](https://github.com/mingsiliu557/aDSL)，代码为 `810f713d00058eeec58ff2f226ad48d12aaa2505`。以下证据路径均相对于实验数据主库，不公开服务器绝对路径、访问凭据或进程标识。

本次检查主库已有的 31 个 ours 案例，重点逐个核对其中 9 个 Toys4K 动物的初始 `plan.json`、最终 assembly manifest、`assembly_versions.json`、Engineering 实际输入与输出、错误记录。没有重新生成、调用模型、运行 Blender 或物理 checker；本报告中的测量均引用已有记录。

结论：**分件权限已经开启，但 9 个动物初始均选择整件打印，最终也均为 1 件。后续没有落地的原因并不相同：有算术上不值得拆的案例，有模型保守返回 NO_PROPOSAL，有切分建议被协议拒绝，也有工具、输入构建或必要修复阻断。不能把它们统称为“动物不适合分件”或“分件评分没有收益”。**

`components` 是语义部件清单，`print_parts` / 最终 manifest 的 `part_declarations` 才表示实际打印件。身体、腿、脚被识别为不同语义部件，不意味着已经独立打印。所有已核对的 Engineering 实际输入中，`print_partition_editable` 都是 `true`；进入可选优化的案例同时收到了 `next_edit_purpose=partition_optimization`。

## 2. 已有真实分件证据

31 个最终 manifest 中，2 例有多个打印件，29 例只有 1 件。

| 案例 | 最终实际分组 | 接口数 | 已有证据与限制 |
| --- | --- | ---: | --- |
| `ABO_B075X2XZDD` 落地灯 | 2 件：底座；灯杆、灯臂与灯头整体 | 1 | 从合格的 `attempt_0002` 单件基线生成 `attempt_0003` 两件候选；同参考下 G 从 540 降为 35，Dapper 从 12 增为 419.934489；`partition_adopted=true`，Topology、Standing、制造与显示均 PASS。初始 Planner 曾规划 3 件，不能把初始 3 件误当成此次比较基线。 |
| `ABO_B082JGPBLQ` 柜子 | 7 件：柜体、两个抽屉、四个轮子 | 8 | 初始即为 7 件，最终未采用分组优化；制造、Topology、Standing 为 PASS，显示导出 FAIL。它证明多件与多接口实际生成，不能作为后续自主分件优化成功或完整显示成功的证据。 |

两例模型均为 GPT-6-Astra，恐龙亦使用 Astra；主库另外 28 例使用 GPT-5.6-Sol。以下结果不是固定同模型的能力比较。

## 3. 九个动物逐例结果

以下数值取各自账本 `retained` 所选版本的原生 Overhang 结果，未把最后一个被拒候选的数值当成最终结果。所有案例 N=1，Overhang 测量状态为 PASS。G 是体素数；不同案例的 h 与参考量不同，不跨案例相加或按原始 G 排名。

| 案例 | 模型 | 选中版本 | 最终 G | 最终 Dapper | Topology | Standing 原生状态 | 未落地分件的直接原因 |
| --- | --- | --- | ---: | ---: | --- | --- | --- |
| `bunny_000` | GPT-5.6-Sol | `original` | 343 | 2360 | PASS | PASS | Engineering 认为拆分无分数收益，提出脚底 reshape；该候选被标为纯分件优化，主体改变后回退。 |
| `cow_016` | GPT-5.6-Sol | `attempt_0001` | 476 | 1580 | PASS | PASS | 分件阶段返回 NO_PROPOSAL：担心重叠材料归属、切面与接口，并提到没有已测候选。 |
| `dinosaur_020` | GPT-6-Astra | `attempt_0001` | 1799 | 2202 | PASS | PASS | 提出左右两半切分；中央语义部件在两半重复，被归属校验拒绝，未交给 Coder。 |
| `dog_046` | GPT-5.6-Sol | `original` | 245 | 855 | PASS | PASS | 分件阶段返回 NO_PROPOSAL：没有明确的安全切面证据，担心连续外观和接口风险。 |
| `fox_024` | GPT-5.6-Sol | `attempt_0002` | 998 | 2337 | PASS | PASS | 已进入分件阶段，但 Engineering 的 read_file 失败；此前另有输出 JSON 解析错误。 |
| `frog_008` | GPT-5.6-Sol | `original` | 58 | 700 | FAIL | INDETERMINATE | 评审输出 JSON 解析错误，FLOW_ERROR；未进入 Engineering 分件尝试。 |
| `lizard_012` | GPT-5.6-Sol | `original` | 260 | 216 | FAIL | INDETERMINATE | 必要内部断连修复未完成，五轮耗尽；部分 Engineering 输出解析失败。 |
| `penguin_001` | GPT-5.6-Sol | `original` | 338 | 927 | FAIL | INDETERMINATE | 先修连接与站立，随后 Engineering 本地反馈构建超过 32 KiB，停止。 |
| `sheep_038` | GPT-5.6-Sol | `original` | 343 | 1141 | FAIL | INDETERMINATE | 先修连接与显示网格，随后 Coder 本地反馈构建超过 32 KiB，停止。 |

Standing 的原生 INDETERMINATE 在主库 CSV 被归一化为 NOT_EVALUATED，表示没有有效站立结论，不等于有效的站立 FAIL。Overhang PASS 只表示测量完成，不表示无需支撑或具备合格连接结构。

### 3.1 初始整件倾向：计划理由与真实任务要求有区别

9 个初始计划都把语义部件合到一个打印组。反复出现的理由包括：保持连续有机外观、避免接缝、减少薄脚踝或尾巴的 connector，以及请求允许单件。

牛、狗、兔子的冻结 requirement 要求的是“完整静态物体”和保持原使用姿态，没有要求只能输出一个打印件。实验配置 `require_multiple_parts=False` 允许单件，也允许多件，并非禁止分件。

据此可以观察到模型的单件选择倾向，但不能断言生产代码存在“动物必须整件”的硬编码。后续 Engineering 提示也明确允许 split / merge 并说明初始 plan 不是实际分组事实。

### 3.2 恐龙：切分被语义归属协议挡住

恐龙最初为一件、无接口。必要站立修复只将现有脚趾向前延长，原始 G=1791，修复后 G=1799；不是分件导致 G 增加。主体修复建立了新参考，前后不同参考的 Dapper 分数不能写成分件收益。

随后 Engineering 完成真实输出，提出 `partition_sagittal_two_halves_01`：沿 X=0 对原主体做互补 Boolean 截取，保留装配姿态，并新增隐藏的躯干 TabSlot 接口。

建议的左右两组都列出 `torso`、`neck`、`head_upper_jaw`、`lower_jaw`、`tail`。Engineering 在说明中称这些名称代表互补片段，不是重复整个实体，但当前 `PrintGroupingChange` 只能给完整语义部件分配唯一归属。

`adsl-agents/models.py::PrintGroupingChange.validate_current_assembly()` 因重复成员抛出：

```text
grouping must preserve complete unique semantic ownership
```

账本记为 `partition_engineering_unavailable`。Engineering 模型调用本身 COMPLETED；没有分件 Coder 调用，也没有两件候选、候选 G 或候选接口检查结果。

这是协议表达范围的已确认限制。后续若支持同一语义部件的互补片段，应同时定义来源、材料归属及主体保持验证，不能直接取消重复归属保护。

### 3.3 牛、狗：有理论窗口，模型未提出可执行假设

两例 Engineering 实际输入均为分件优化、权限开启，包含逐件测量和收益边界。牛理由包括重叠着色主体和嵌套容器使材料归属困难；狗理由包括没有安全切面定位及连续外观风险。

这类风险可以是拒绝的合理依据。但当前提示已明确：候选是可证伪假设，无需在提议前有已测候选分数；候选完成后才测量并决定采用。牛的输出仍把缺少已测候选作为理由的一部分，说明 Agent 对“先提议、再验证”的执行较保守。没有实测拆分候选，尚不能证明这两例没有可行方案。

当前采用目标不是单独最小化 G，而是：

```text
O = (V_ref - G) / N**0.3
```

在相同参考下，从 N=1 改为 N=2，改进要求 `G_new < V_ref - O_current * 2**0.3`：

| 案例 | V_ref | 当前 G / O | 两件候选改进要求 |
| --- | ---: | --- | --- |
| 牛 | 2056 | 476 / 1580 | G_new < 110.791827；整数 G_new ≤ 110 |
| 狗 | 1100 | 245 / 855 | G_new < 47.371527；整数 G_new ≤ 47 |
| 兔子 | 2703 | 343 / 2360 | 两件零空隙上界只有 2195.518227，低于当前 2360；同主体纯拆分无法提高当前公式的分数 |

这些是确定性的算术界限，不是已测候选收益。本轮没有改变公式或验收门槛。

### 3.4 兔子：修改目的与建议动作不一致

Engineering 收到 `next_edit_purpose=partition_optimization`，明确拒绝 split / merge，却提出动作 `reshape`，修改四个脚底的闭合接触几何。

该候选被继续记录为 `partition_optimization`，不是必要修复。候选仍为一件；各 checker PASS、分数差为 +22，但主体参考比较 CHANGED，对称差体积约 2185.602764 mm³，最终以 `partition_reference_shape_changed_or_unavailable` 回退原版。

主体保持守卫在这里按现有规则拒绝了候选。需要复核的是主目标、提案动作和候选 purpose 的一致性，而不是放松主体保持检查。该记录不是实际分件失败或收益证明。

### 3.5 工具、输入构建与必要修复阻断

- 狐狸：最后一次 Engineering 已进入纯分件优化，`read_file` 读取当前 `source.py` 返回 `READ_INVALID_JSON`，错误为 `Expecting value: line 1 column 1 (char 0)`；此前另一次为 `Invalid JSON when parsing model output`。源码读取为什么进入 JSON 解析，具体工具参数还需后续复核。本轮没有将其归因为上游 API 不可用。
- 青蛙：`rounds/round_01/flow_error.json` 为 `ModelBehaviorError: Invalid JSON when parsing model output`，没有 Engineering 分件调用。
- 蜥蜴：主要在 `required_repair` 中修内部断连，最后 `round_budget_exhausted`。必要检查没有完成，不能以更高分越过它；重分组本身仍可作为有证据的必要修复方式，并非一律禁止。
- 企鹅：Engineering 本地构建报 `AGENT_FEEDBACK_TOO_LARGE`，34477 字节 > 32768 字节，kind=`input_construction`；当时仍在必要修复链。
- 羊：最后一次 Coder 本地构建报相同错误，33877 字节 > 32768 字节；源码前后哈希一致，tool_events 为空。记录是本地反馈边界触发，不能叙述为本次模型服务返回 context400，也不是实际执行了最后一次修补。

## 4. ours 暂停记录

按用户要求，于 **2026-10-11 11:36:18 UTC** 暂停当前 ours 30 例批次。该批次标识为 `benchmark_risk_thirty_stepcode_sol56_20261010T092454Z`，与本报告检查的主库旧结果分别保存。

暂停时快照：

| 状态 | 数量 |
| --- | ---: |
| 生成完成（不等于所有检查通过） | 5 |
| 生成失败 | 2 |
| 当前暂停：`Toys4K_horse_027/ours` | 1 |
| 待生成 | 22 |
| 合计 | 30 |

已停止 ours 向后推进，保留现有输出、日志、冻结输入和预算记录；checkpoint/results 标为 PAUSED，不把当前暂停任务伪记为生成失败。原版 aDSL 对照任务仍独立运行。

详细暂停记录留存在实验数据目录，不随本报告公开。这次暂停不是已修复后的重新提交；修复后应重新确认实际导入版本和已有预算，不能仅恢复旧任务便声称新代码已接入。

上述运行数字是暂停时快照，不用于持续描述原版任务的最新进度。排查的主库 9 个动物与当前暂停的 30 例是不同批次，不能混算完成量。

## 5. 后续修复建议（未实施）

建议先修复明确的接口与调用阻塞，再验证新的分件尝试：

1. 在分组协议中表达同一语义主体的互补片段，并保留完整材料归属、根坐标框架、主体保持及接口重查。先确定所需的最小协议扩展，不直接移除唯一归属校验。
2. 对齐本轮主目标、Engineering 动作和候选 edit_purpose：纯分件优化不能默默转成主体 reshape。没有合理分组建议时走明确的停止或必要修复分支。
3. 复核源码 read_file 的 JSON pointer 参数及返回错误；检查已压缩反馈仍超过 32 KiB 的实际组成。保留完整证据引用与请求停止分类，不通过发送原始大诊断绕过边界。
4. 修复后再检查 Planner/Engineering 对整件外观与分件风险的权衡。允许合理切分假设，但不强迫每个动物拆分，也不把没有已测候选当作必然拒绝条件。

本报告没有修改生产实现、checker、评分参数、冻结输入或预算，没有恢复 ours。后续建议不计为已完成的修复或已验证的分件收益。

## 6. 证据定位与本次核对

每个动物的证据相对主库路径为：

```text
cases/Toys4K_<source_id>/case.json
cases/Toys4K_<source_id>/ours/final/plan.json
cases/Toys4K_<source_id>/ours/final/source.py
cases/Toys4K_<source_id>/ours/final/assembly/assembly_manifest.json
cases/Toys4K_<source_id>/ours/raw/generation/native/assembly_versions.json
cases/Toys4K_<source_id>/ours/raw/generation/role_calls/*engineering*.json
```

关键文件：

- 恐龙：`009_assembly_engineering_01_standing_forward_toe_extension_01.json`，含切分建议；账本 feedback 含归属拒绝理由。
- 兔子：`005_assembly_engineering_round_01.json`，含拒绝拆分与脚底 reshape 建议；账本含 candidate purpose、主体差与回退。
- 牛：`007_assembly_engineering_01_assembly_or_appearance.json`，含 NO_PROPOSAL 理由。
- 狗：`005_assembly_engineering_round_01.json`，含 NO_PROPOSAL 理由。
- 狐狸：`012_assembly_engineering_02_assembly_or_appearance.json`，含分件权限、目标与 read_file 错误。
- 企鹅：`raw/generation/native/rounds/round_02/candidates/01_penguin_round1_connected_scale_and_webbed_feet/engineering_error.json`。
- 羊：`raw/generation/native/rounds/round_04/candidates/03_r4_horn_tube_precision_and_visible_marbling/repair_request_error.json`。

对应实现为 `adsl-agents/models.py::PrintGroupingChange.validate_current_assembly()`、`assembly_topology.py::engineer()` 和 `fixed_assembly.py` 的 purpose/比较/停止分支；以上定位按函数核对，不依赖历史行号。

本次实际执行的是 JSON/CSV 读取、最终 manifest 件数与接口数统计、selected version 指标核对、实际角色输入的权限/目标核对、日志错误核对及少量评分算术核算。**没有运行测试套件或重新物理测量，不引用它们为本次验证结果。**完整原始实验数据仍在数据盘，本次 GitHub 交付仅为本报告。
