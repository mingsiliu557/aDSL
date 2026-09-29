# 分件评分接入实施记录

基线 f264f35，分支 feat/fixed-assembly-partition-score；CPU，暂不合并 master。
证据根目录：temp/partition_score_20260929（主工作区）。

## S1

`python -m pytest -q -p no:cacheprovider tests/test_partition_score.py`：7 passed，5.21s。
真实 Manifold 实体/单元求交，覆盖实心、空腔、格线接触、薄斜体、桥梁空格及24旋转。
相互垂直的纯接触壳体求总体积出现1.39e-17舍入残差，逐壳体检查正体积和非零三维范围后正确排除；没有增加体积过滤阈值。
原式负分、缺件、参考不一致及相等分数均有测试。没有模型调用。

## S2

`ADSL_TEST_FIXED_REAL=1 python -m pytest -q -p no:cacheprovider tests/test_partition_score.py tests/test_partition_exports.py tests/test_assembly_physics.py -k 'not real_ and not fea'`：21 passed，7 deselected，27.97s。
包括真正 Blender 的 geometry/visual_only 导出、2 mm/unit、参考失败不改变 display、推荐 STL 读回评分、既有朝向与过悬调用。

## S3

`python -m pytest -q -p no:cacheprovider tests/test_partition_feedback.py tests/test_assembly_physics.py tests/test_assembly_feedback_recovery.py -k 'not real_ and not fea'`：19 passed，1 skipped，13 deselected，30.39s。
真实实体分件测量进入现有 Engineering 适配器（语言模型 mock）；授权、未知件拒绝、G=0机会、缺件unknown和Topology失败后的执行顺序通过。
旧顺序断言同步为 topology→overhang→standing→fea；skip/deselected未计入通过。

## S4

`python -m pytest -q -p no:cacheprovider tests/test_partition_score.py tests/test_partition_selection.py tests/test_partition_feedback.py tests/test_fixed_assembly.py tests/test_fixed_assembly_recovery.py tests/test_fixed_assembly_plan_revision.py tests/test_assembly_physics.py tests/test_prompts.py -k 'not real_'`：101 passed，1 skipped，14 deselected，36.04s。
候选选择使用真实原式算术、mock模型/物理门槛，验证收益、回退、必要修复、参考切换、优化修复链和resume不重置预算；打印输出逐文件核对选中source。
最后原生复核 `ADSL_TEST_FIXED_REAL=1 python -m pytest -q -p no:cacheprovider tests/test_partition_exports.py tests/test_partition_selection.py`：14 passed，13.47s。

## S5/S6 实际执行

已以 `d87c9d9` 启动 CPU 顺序任务：SF07 P0→P1、SF10 P0→P1，再从首个合格基线执行一次 Engineering/Coder 分组 smoke。
S5 固定源码，按用户确认仅调用真实 Image/Code 评审；S6 最多一次源码修改。基线未合格时不运行该组优化比较。
输入哈希：SF07 `3fe5dd8c176ee79af4dbda6ca93f25f457a6f644c062db85f729a090fd1e48a2`；
SF10 修复基线 `03e6d3f9582cf16951bf090570b88c67fac3e59a09ff4127b9a0995f119514bf`。
脚本 `temp/partition_score_20260929/run_cases.py`，日志 `cases.log`，提交清单 `case_job.json`。
所有实际模型输入/输出/工具调用分别保留在每个版本目录下 actual_model_calls 和 sessions_snapshot.sqlite3。

S4 的 NO_PROPOSAL 停止原因随后补充定向回归：12 passed，6.60s；避免被通用成功文案覆盖。
案例任务已在其进程中加载 d87c9d9 流程，报告以实际保存的 decision/critique 为准；未重启或追加模型预算。


## 实现与提交

- `243201d`：实体占据、24 个正旋转、Dapper 原式及可比性。
- `47677a5`：两种导出模式的 body NPZ、冻结参考和推荐打印资产。
- `ce80156`：检查顺序、分组权限、结构化建议及 Engineering 输入。
- `d87c9d9`：候选目的、硬约束、评分采用/回退、主体参考更新与 resume。
- `98f1576`：保留 NO_PROPOSAL 停止原因；实际案例进程已加载上一提交，未重跑模型。

实现涉及 `partition_score.py`、`assembly_overhang.py`、`assembly_physics.py`、
`assembly_topology.py`（Agent 适配）、`models.py`、`fixed_assembly.py`、
`export_assembly.py` 及两份 FixedAssembly 提示/文档。新增四份 partition 测试，
更新既有 checker 顺序断言。未修改 FEA、Standing 判据或网格修复算法。

配置通过 `fixed_assembly.physics.overhang.partition_objective` 开启；固定
`method=dapper_fdm_2015, alpha=0.3, r_vox=0.1, orientation_set=axis_aligned_24, layout=independent_bed`。
`RepairPolicy.print_partition_editable=True` 才能提出分组修改；两者默认均不启用。
最终打印入口为所选版本 `assembly_result.json` 的 `print_layout` / `print_parts`。

## S5 固定源码结果

任务 03:44:08 UTC 开始，全部限定任务于 03:56:23 UTC 完成，均为 CPU。
真实 Image/Code 评审使用已有 CLIProxy `gpt-5.6-sol` profile；S5 无 Engineering/Coder 改源调用。
Standing 使用 rigid_flex、5 s / 25°，实验统一密度 1240 kg/m³；FEA 关闭。
以下 score 只在同一 reference 的候选之间比较。

| 案例/候选 | reference（前缀） | source（前缀） | N | h mm | G | O（越大越好） | Topology | Standing | 外观评审 | 采用 |
|---|---|---|---:|---:|---:|---:|---|---|---|---|
| SF07 P0 | 53e109e6af7d | 3fe5dd8c176e | 3 | 75 | 0 | 143.844618665 | PASS | PASS | 通过 | 是 |
| SF07 P1 人工合并 | 同 P0 | cddbb92fa76e | 2 | 75 | 28 | 139.707412173 | PASS | PASS | 通过 | 否 |
| SF10 P0 已有互穿修复主体 | 1dd48f92a63f | 03e6d3f9582c | 2 | 75 | 62 | 527.964057632 | PASS | PASS | 未通过 | 尚无合格方案 |
| SF10 P1 | — | — | — | — | — | — | 未运行 | 未运行 | 未运行 | 未比较 |

SF07 P1 从原 body 合并底座和立柱，保留 root ID/坐标，撤销组内 connector，更新到桌面的跨件连接。
主体并集对冻结参考的对称差为 0，语义归属和 root 检查通过；最终接口数由 2 减为 1。
G 增加 28，少一件的收益未抵消空格增加，差值 -4.137206492，因此比较选择 P0。
此比较由固定源码候选和真实测量完成，不是 Agent 自主改进。

SF10 P0 的三个工具均 PASS，但 Image 与 Code Critic 均认为桌面缺少需求中的光亮木质表面。
Code Critic 核对到源码只有平面 RGB，`glossy_wood_upper_slab` 名称本身不设置光泽材质。
按预定基线门槛停止，未执行 P1、未追加外观修复调用；这不属于评分器测量失败。

四次真实运行都使用 visual_only：geometry 状态仍为 NOT_EVALUATED，未改写为 PASS。
Topology/Standing 是各自独立执行的真实工具结果；geometry/visual_only 双入口实体导出覆盖来自 S2 的原生小夹具。

## S6 实际 Agent smoke

从 SF07 P0 合格基线启动，使用相同 reference，允许一次建议和最多一次 Coder 源码修改。
实际调用为 Image Critic、Code Critic、Engineering 各一次。Engineering 实际执行
`read_file(original/source.py)`，收到了当前实际分组/两条连接、N=3/G=0/O=143.844618665、
各件最优姿态与全部工具 PASS；未提供人工 P1 补丁或对照结果。

Engineering 返回 `repair_proposals=[]`（NO_PROPOSAL）：它认为缺少证明合并可提高分数的证据，
选择保留当前方案。因此没有调用 Coder，没有生成新分组；最终源代码与 P0 hash 相同。
此次证明真实模型收到评价并能停止，不证明自主分件改进，也没有真实 Engineering→Coder→修改后 checker 证据。
完整建议解析、修改/候选比较/回退链路目前由 S3/S4 的 mock 模型测试覆盖。
“没有预先测量的候选就不提议”是本次模型表现的缺口；按一次 smoke 预算没有追加尝试。

实际过程文件：
- `cases/SF07/agent_smoke/rounds/round_01/engineering_input.json`
- `cases/SF07/agent_smoke/rounds/round_01/engineering_critique.json`
- `cases/SF07/agent_smoke/actual_model_calls/03_engineering-critic_assembly_round_01/{input,output}.json`
- `cases/SF07/agent_smoke/rounds/round_01/decision.json`

上述历史 decision 保留实际 d87c9d9 的通用成功 reason；没有修改历史运行数据。
新代码已用 12 项定向回归确认 NO_PROPOSAL 专用停止原因不会再被覆盖。

## 最终打印资产复核及证据

`python temp/partition_score_20260929/verify_print_assets.py`（主工作区绝对路径执行）：
4 组、10 个实际 STL 全部通过读回检查。闭合/单连通、床面 z=0、逐件 G、总 G、
source/reference/STL hash 均与对应 print_layout 一致。结果 `print_asset_verification.json`。
SF10 打印文件可测不代表其外观已获批准。

新增 split 归属/root/目标 ID/merge 连通性定向测试：
`python -m pytest -q -p no:cacheprovider tests/test_partition_feedback.py -k split_ownership`：
1 passed，5 deselected，5.89s。S3/S4 的 skip 是未开启的既有显式原生 smoke，不计为通过；
实际新增 Blender 导出测试及上述真实案例已另行运行。

证据目录下有 S1–S4 smoke 日志、`case_job.json`、`cases_completion.json`、紧凑 `summary.json`、
各候选 source/plan/manifest/GLB/checker 原报告和实际模型记录。
SF07 采用入口 `cases/SF07/P0/assembly_result.json`；推荐 STL 在其 `print_parts/original/`。
人工 P1 补丁 `cases/SF07/P1/manual_candidate.diff`；两例比较决策在各自 `comparison.json`。
完整 source/reference hash 见各 `completion.json` 与 `summary.json`。

本轮仅采用 Dapper 的目标函数；75 mm 体素会掩盖细小 connector 差异，G 是竖直空隙代理量，
不等于切片支撑耗材或打印时间，不包含桥接、支撑移除或打印过程稳定性验证。
未执行 SF10 拆分和 SF16 回归；没有宣称新接口插入路径或保持力已验证。
截至交付代码仅在独立分支提交，未合并 master、未推送该新分支。
