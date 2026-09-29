# 检查反馈与分件优化协调（2026-09-29）

基线：`feat/fixed-assembly-partition-score` / `ffc55df023337cd63cd3ce69480dd9957c456b68`。
实施工作树：`/tmp/adsl_partition_20260929`。本轮未合并 master。

## A / B / C 实际修改

- A，`366cb8a`：`models.py` 的 VisualIssue 增加 aspect，旧记录默认 geometry；Image/Code 提示区分实体与表面，Code 最终分类用于反馈。HIGH surface 仍阻止完整批准，不按文字关键词重分类。
- B，`334b8f1`：`partition_score.py` 增加纯算术拆分上界和合并 gap 预算；`assembly_overhang.py` 输出 partition_guidance；Engineering/Coder 接收同一测量。未修改目标函数、体素算法或配置哈希。
- C，`082b374`：`fixed_assembly.py` 分离 feasible_without_surface、partition_ready、partition_adopted、accepted；在现有循环内处理主目标、必要主体修复参考更新、比较基线、恢复和停止；`assembly_topology.py` 传递本轮目的及量化提示。没有 qualified 时允许保留有效但表面未批准的分件结果；已有 qualified 时保护其完整合格状态。没有结构化建议时不盲改可选分件候选。
- 新字段写入 reviews/decision；NO_PROPOSAL 和 UNAVAILABLE 分开记录；明确无收益/NO_PROPOSAL 记录 partition_stop_reference，避免同参考重复探索。原候选引入的问题继续使用原优化基线。
- 修改了计划指定的 7 个实现/提示文件及 4 个已有测试文件。未改 mesh、Topology/Standing 几何算法、公开材质 API、渲染超时或普通非 FixedAssembly 流程。

## 定向 smoke

在本工作树、已核对模块路径的 Python 环境运行，CPU；`source /tmp/adsl_partition_env_20260929.sh`。

| 阶段 | 命令（均为 `python -m pytest -q -p no:cacheprovider`） | 结果 |
|---|---|---|
| A | `tests/test_appearance_review_contract.py` | 15 passed |
| B | `tests/test_partition_score.py tests/test_partition_feedback.py` | 15 passed |
| C | `tests/test_partition_selection.py tests/test_partition_feedback.py tests/test_assembly_feedback_recovery.py` | 44 passed |
| 受影响旧流程 | `tests/test_fixed_assembly.py tests/test_fixed_assembly_recovery.py tests/test_fixed_assembly_plan_revision.py tests/test_prompts.py tests/test_appearance_review_contract.py` | 84 passed, 6 skipped |

集合存在重复，不相加宣称独立测试总数。6 项 skip 为未设置 ADSL_TEST_FIXED_REAL=1 的旧真实 Boolean 导出测试；本任务实际 SF10 另外执行原生导出和 checker。

A 覆盖 SDK structured-output schema、往返、历史默认值、HIGH surface 仍拒绝、Code 覆盖 Image。B 使用真实体素夹具与 mock 模型，验证 SF07 拆分上界、SF10 G=39/40、N=1、缺失和负分。C 模型/部分几何和 checker 状态 mock，使用实际循环及评分算术，覆盖表面未批准的收益采用/无收益回退、硬检查失败候选修复、必要修复允许分数下降、参考刷新、同参考停止、resume 和发布资产绑定；不能把这些 mock 算作实际自主分件成功。

首轮 C 有一个旧测试夹具只返回 FAIL 状态，没有可修复 finding，导致原预期的继续修复不成立。已为夹具补入带定位的互穿 finding，最终 44 项通过；生产流程仍不凭不可用测量猜测改形。

日志与完整运行记录：`/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/partition_coordination_20260929/`，分别为 `stage_a.log`、`stage_b.log`、`stage_c_final.log`、`affected_regressions.log`。

## 唯一一次 SF10 实际尝试

输入为上次完整运行 `temp/partition_full_flow_20260929/cases/SF10` 的 `attempt_0001`，不是旧两件素材。`assert_version` 验证资产哈希，主体与冻结参考 MATCH。

- 源码 SHA256：`661c5a12b2971ba430b00c034338a0140cba6dd79936fbfba28e45757113b309`。
- Manifest SHA256：`ec5bb543ebf87e29c83eb6b3728d2d3941346782081306e350f2c2f3e6af12e7`。
- Reference：`969391f2f5e87ffc6b4f84e7c20c1af3d6414c3951f10997b219d417ca2a98ab`。
- 五件：tabletop、left_pedestal、right_pedestal、upper_brace、lower_brace；八接口；lead_in_mm 均为 0。
- 原输入 G=0、V=616、N=5、O=380.092859435526；新结果以本次检查为准。
- 新目录 `temp/partition_coordination_20260929/SF10_one_attempt`；源码和 plan 复制到新目录，用 resume 进入当前评审，未调用 Planner，也未修改旧 completed book。
- 冻结原 prompt、1500×750×750 mm、mm_per_unit=1、fit=.2、原物理与 Dapper 配置。CPU、gpt-5.6-sol、Topology→Overhang→Standing，FEA 关闭。
- 最多基线和一个候选共两轮评估，至多一个 Engineering 分组建议及一个 Coder 实现。实验 runner 仅在有结构化分件建议时调用 Coder；无建议或基线不适合本次纯优化则停止，不顺带追加表面修复实验。此范围限制写在 runner 和 provenance，不伪装成模型主动 NO_CHANGE。

本次重新评审确认唯一 HIGH 为 surface（桌面光泽），主体视觉无 HIGH；Topology/Overhang/Standing 均 PASS。基线参考不变，h=75 mm、V=616、G=0、N=5、O=380.092859435526。

实际 `rounds/round_01/engineering_input.json` 已包含当前五件/八接口、全部逐件 G=0、最佳姿态、reference、`next_edit_purpose=partition_optimization` 与本轮主目标；分件权限开启。收益提示给出：拆为六件即使 G=0，上界仅 359.861459537805；合为四件需严格 G<39.88695614126527（整数 G≤39）。这是理论界限，不是对候选的预设分数。

实际 Engineering 在 `06:32:18Z` 开始，经 `read_file(original/source.py)` 读取源码后，于 `06:35:18Z` 前返回一条 `regroup_print_parts` 建议：合并 `left_pedestal` 与 `lower_brace` 为 `left_pedestal_lower_brace`，目标五件变四件；撤销内部 `lower_brace_left_mount`，更新另外四处受影响连接端点，保留其他接口及帧。它明确认为收益尚未测量，光泽问题本轮暂缓且仍未满足。

实际 Coder 于 `06:35:18Z` 收到同一原始源码 SHA、`source_version=original`、`edit_purpose=partition_optimization`、分组建议和同一 partition_guidance。没有预先写好的合并源码。Coder 通过实际 read_file / apply_patch 修改指定候选，随后完成第二轮评审与全部选中检查。


## SF10 最终结果与发布一致性

本次于 `2026-09-29T06:40:56Z` 完成，用时 784.32 秒（约 13.1 分钟）。实际 6 次角色调用：Image×2、Code×2、Engineering×1、Coder×1；没有 Planner、额外候选或额外重跑。Coder 在一次角色调用内进行了多处补丁编辑，不代表追加候选预算。

| 版本 | N / 接口 | h / V / G | O（越大越好） | Topology / Overhang / Standing | 外观 | 选择 |
|---|---|---|---:|---|---|---|
| 五件基线 original | 5 / 8 | 75 mm / 616 / 0 | 380.092859435526 | PASS / PASS / PASS | HIGH surface，未批准 | 比较基线 |
| Agent 候选 attempt_0001 | 4 / 7 | 75 mm / 616 / 38 | 381.337786213366 | PASS / PASS / PASS | HIGH surface，未批准 | 保留候选 |

- **接口与流程：已验证。** Engineering 实际读源码并给出合并建议，schema 传递到 Coder，实际主体注册和连接发生修改，重新导出、评审、检查、比较并采用。
- **分件收益：已验证本候选的小幅改善。** 同一 reference / 配置，分数增加 `1.244926777841`（约 **0.328%**）。合并件 G=38，其他三件 G=0；增加的 gap 被少一件带来的公式收益抵消。不是支撑耗材或打印时间的实测收益，h=75 mm 仍是指定的粗体素代理。
- **完整批准：仍为 false。** `partition_ready=true`、`partition_adopted=true`、`accepted=false`、`qualified=null`；reason 为 `partition_improved_surface_unresolved`。最终 `retained=attempt_0001`，没有退回旧五件源，也没有将光泽 HIGH 降级。停止原因为用完本次两轮评估，不追加表面修复。
- 候选 source SHA256：`35909286dcb68a4e03f7140446d9f8d541c4d78877ebc8995abdd1a741c12acb`；主体参考仍为上述 `969391f…`。原主体对称差体积 `0 mm³`、AABB 差 `0 mm`，语义归属和 root 坐标保持。
- 候选 Topology 的 4 个 part、7 个 interface、6 个 pair 全部 PASS。Standing 为当前既有配置下的 PASS，不扩展为载荷、装配路径或保持力验证。
- 实际采用 `visual_only`：export_status=PASS；几何导出总状态仍 `NOT_EVALUATED`。独立 Topology/Standing 的 PASS 分开报告。
- 选中版本的 4 个推荐打印 STL 已重新读取：闭合实体检查、床面坐标、逐件 G、总 G、STL 哈希和 source/reference 绑定均 PASS。最终源码、manifest、两个发布 GLB、三工具 source 哈希、评分、print_layout/STL 都绑定所选候选。

### 可直接读取的证据

以下相对路径均位于 `/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/partition_coordination_20260929/`：

- 输入来源与历史哈希：`input_provenance.json`；新运行冻结配置：`SF10_one_attempt/provenance.json`、`runtime_config.json`。
- 每次真实完整 prompt / 输入 / 输出 / 工具调用：`SF10_one_attempt/actual_model_calls/01_*` 至 `06_*`；便于检索的汇总 `actual_model_evidence.json`。
- Engineering 实际输入及结构化输出：`SF10_one_attempt/rounds/round_01/engineering_input.json`、`engineering_critique.json`。
- Coder 源码补丁：`attempt_0001.diff`；原始工具记录 `actual_model_calls/04_coder_assembly_2/output.json`。
- 候选及原始检查：`SF10_one_attempt/rounds/round_02/candidates/01_partition_merge_left_pedestal_lower_brace_v1/`，其中 `source.py`、`asset/assembly/assembly_manifest.json`、`checkers/*/result.json`、`decision.json`。
- 最终版本账本/结果：`SF10_one_attempt/assembly_versions.json`、`assembly_result.json`、`completion.json`、`working_candidate.json`。
- **最终打印入口：`SF10_one_attempt/print_parts/attempt_0001/print_layout.json` 及该目录 4 个 STL。** 源码指定的旧打印姿态与本次推荐姿态不混用。
- 发布一致性和真实 STL 读回：`selected_asset_verification.json`、`selected_asset_verification.log`、`verify_selected.py`。
- 运行脚本：`run_sf10.py` / `run_sf10.sh`；没有手工候选、修改旧预算或改 checker 阈值。实际渲染图在两轮各自 `asset/render/`。

本轮完成计划规定的一次实际尝试，未追加 SF07/SF16 或新的模型候选。代码提交及本报告留在分件分支，未合并 master。
