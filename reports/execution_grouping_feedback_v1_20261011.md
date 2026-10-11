# 执行反馈、语义分组与评分参数：离线实施结果

日期：2026-10-11。基准：`552e9ef2569792aae1b1dbd489ea7db517b98a8c`。

实施目录：`/tmp/adsl_execution_grouping_20261011`，分支：`codex/execution-grouping-feedback-v1`。主目录原有未提交修改没有覆盖。测试实际导入路径为 `/tmp/adsl_execution_grouping_imports/adsl/agents/fixed_assembly.py`，其链接目标属于本分支。

本轮仅人工 Python 错误、小型算术/参考文件夹具、历史 JSON/hash 回放和工作流 stub。真实模型调用为 **0**，没有启动 benchmark、真实物体生成、渲染、Topology/Standing/Overhang/FEA checker 子进程。网格修复、Boolean、精度转换算法均未修改。历史源码仅用于哈希和来源绑定，没有执行。

## 1. 四阶段实际修改

### 第一提交：执行错误摘要和路由

本地提交 `b40c568`。

- `utils/asset_executor.py::_execute_generated_source` 将 runpy 和 scene/assembly 顶层合同作为明确边界。完整 traceback 写入 `source_execution_error.json`；异常类型、512 字符消息、最近最多 3 个真实源码帧、source hash 可向上传递。导出和渲染位于该边界之外。
- `utils/execution.py::AssetExecutionError` 增加可选 diagnostic，旧消息构造兼容。父进程核对本次源码 hash；超时、依赖/文件错误不伪造 source repair 权限。
- `feedback_schema.py::execution_failure_summary` 验证 stage/hash/帧来源。控制器补 ledger 版本和实际 evidence pointer。
- `fixed_assembly.py::iterate_fixed_assembly` 在可靠 source_execution/source_contract 错误时跳过当轮 Image/Code，记录 `SKIPPED_SOURCE_EXECUTION_FAILED`，由原 Coder 修补预算修复源码使用错误。已配置物理检查保持 INDETERMINATE/NOT_EVALUATED、无虚假测量；Engineering 不针对未产出的形状发建议。成功后恢复原评审/检查链。
- `agent_feedback.py` 将短异常纳入唯一 canonical feedback。同源多入口合并，不同异常不按 EXECUTION_UNAVAILABLE 误合并；完整 traceback 留文件。`repair_target=generated_source` 不扩大 `geometry_repair_allowed`。

### 第二提交：历史、预算和重复源码

本地提交 `a679dd9`。

- `_repair_history` 对齐 source_version/hash、parent、purpose、已记录 proposal action/hypothesis、实际结果/检查状态/错误签名和报告引用。旧 version 可读；不推测未记录的实际修改。
- 最近 3 条修补历史进入同一反馈预算；压力下至少保留最近 1 条错误/结果签名，全集保留文件引用。reduce 与 process 同样进入嵌套 checker_evidence。
- `_previous_failed_source` 比较本运行的源码 hash、冻结执行配置 hash、requirement 和参考图 hash，仅拦截已有明确源码/几何求值失败的完全相同源码。临时基础设施失败、不同配置/输入不复用。
- A 失败→B 失败→A 重复的 stub 中，第二个 A 消耗一次已预约 Coder 额度但不执行、不渲染、不检查；working 保持 B，后续从 B 修补为 C。重复记录保留 A 的原版本、结果引用和短错误。完成后 resume 没有新增执行或额度预约。

### 第三提交：语义实例、API、姿态和动作

本地提交 `3f6f3d5`。

- `prompt/fixed_assembly.md` 明确可独立重分组的重复结构分别给语义实例 ID；可复用类/函数，初始仍可放一个打印件。旧 four_legs 不自动改名；完整唯一归属校验未放宽。
- `prompts.py` / `assembly_topology.py::engineer` 给 Engineering 注入 FixedAssembly 核心 API 参考，不追加 Planner/Coder 角色任务。
- `assembly_overhang.py::partition_measurement` 复用已有推荐姿态 measure_part 的区域，增加 `recommended_pose_regions`：实际毫米坐标、recommended_print frame、part-local-mm→print-mm 矩阵、最多 3 个区域与完整 JSON pointer。largest_regions 的旧输入姿态含义不变；不新增朝向搜索或第二次推荐姿态测量。
- `validate_partition_proposal` 在 Engineering 建议后及 Coder 预约前（含 resume）检查：首次纯分件优化必须有授权的 regroup_print_parts 和合法 grouping_change。同一候选后续接口修复可以用现有动作，保留比较基线；必要修复不套首轮分组限制。非法建议为 UNAVAILABLE/INVALID_PROPOSAL，不降格为 NO_PROPOSAL 或通用 reshape。
- NO_PROPOSAL 继续正常停止，不强迫动物拆分。提示要求可核对的候选/理论边界/语义粒度/接口空间理由；区域面积不被当作 G 的逐区域贡献。
- benchmark 新请求文字明确 Planner 是初始方案；已有冻结文件不覆写。

### 第四提交：新默认 .2、冻结 .3 与收尾合同

- `partition_score.py::OBJECTIVE` 新默认 alpha=.2，其他固定参数不变。objective_config 明确支持 .2 和旧冻结 .3；保留传入配置，不静默替换。pitch、reference loading/comparison、hash 均使用本运行冻结配置。
- FixedAssembly 两个 reference_shape_comparison 调用显式传 config；benchmark request_fields 复用统一默认，已有显式 .3 保持 .3。跨配置比较为 NOT_COMPARABLE，旧 .3 参考哈希规则未改变。
- split_one_more 增加严格 gap 上界和所需 reduction。V=2056/G=476/N=1、alpha=.2 时，拆为 2 件要求 G'<241.056599，整数 G=241 有改善、G=242 无改善。
- 面积 evaluator 算法没有修改。人工测量 stub 验证 A 最优姿态可为 rotation 4、G 最优为 rotation 9；两打印件面积最小值求和为 4，G=14；缺一件时两个总量均为 null，实际声明 N=2 不变。
- 离线回放发现长证据目录仍能超预算：仅在预算紧张时裁剪 evidence_files 预览，完整列表先持久化且 total/pointer 一致。修补历史不减至零。
- 补齐 canonical 组被预览省略后的再次投影：从原完整索引恢复关联，避免计数增长；outside-workspace 引用原因保持稳定。预算不提高，required checker 状态/权限/当前指标不删。
- 补充反馈 validator 的非列表帧/布尔行号拒绝，以及重复候选的 prior failure 短摘要。

本轮没有新增 children API，也不建议 Coder 使用私有 _children。测试用 fake_export 因旧夹具缺少现有 display/diagnostic/manufacturing 字段而更新；这不是生产导出器改动或原生导出验证。

## 2. 实际验证

阶段测试有重叠，**不相加**作为总测试数量。日志在 [附件目录](execution_grouping_feedback_v1_20261011/)。

| 运行 | PASS | FAIL | SKIP | DESELECT | 秒 |
|---|---:|---:|---:|---:|---:|
| 阶段 1：源码异常/恢复/反馈定向集合 | 74 | 0 | 0 | 0 | 45.62 |
| 阶段 2：历史/重复源码/反馈恢复 | 74 | 0 | 0 | 1 | 36.91 |
| 阶段 3：推荐姿态/动作定向 stub | 5 | 0 | 0 | 5 | 2.58 |
| 阶段 3：提示/语义/候选选择等 | 107 | 0 | 0 | 0 | 6.67 |
| 阶段 4：评分/旧配置定向 | 6 | 0 | 0 | 6 | 2.71 |
| 阶段 4：选择/runner/evaluator stub | 95 | 0 | 0 | 2 | 7.17 |
| 最终 Coder 适配器+文件工具链补充 smoke | 1 | 0 | 0 | 9 | 2.52 |
| 最终受影响回归 | 331 | 0 | 0 | 8 | 45.42 |

最终回归范围：source execution、agent feedback、assembly recovery、FixedAssembly recovery/prompt/plan revision、partition feedback/score/selection、benchmark runner/evaluator、mesh evaluation feedback、generation review、read_file recovery、prompts。8 项主动排除为真实互穿、实际过悬/推荐 STL、实际 topology 子进程等；不是 SKIP/PASS。

最终 Coder 接线使用生产 `_repair` 和 read_file/apply_patch 工具，runtime 仅 stub。捕获输入见 [stub_coder_source_error_input.json](execution_grouping_feedback_v1_20261011/stub_coder_source_error_input.json)。它直接包含 AttributeError、source frame/hash、generated_source repair target；读取实际 evidence pointer 后修改源码，下一轮 stub 执行成功并批准。只有 1 次 Coder 调用，没有错误轮的外观或 Engineering 调用。工具所读 pointer 的实际选中数据另存为 [stub_evidence_pointer_readback.json](execution_grouping_feedback_v1_20261011/stub_evidence_pointer_readback.json)。

完整复现命令（CPU；模型/物理工具均 stub）：

```bash
cd /tmp/adsl_execution_grouping_20261011
export PYTHONPATH=/tmp/adsl_execution_grouping_imports
/tmp/adsl_six_envs_20261006T174926Z/ours/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_source_execution_feedback.py tests/test_agent_feedback.py \
  tests/test_assembly_feedback_recovery.py tests/test_fixed_assembly_recovery.py \
  tests/test_fixed_assembly_prompt.py tests/test_fixed_assembly_plan_revision.py \
  tests/test_partition_feedback.py tests/test_partition_score.py tests/test_partition_selection.py \
  tests/test_benchmark_six_evaluation.py tests/test_benchmark_six_runner.py \
  tests/test_mesh_evaluation_feedback.py tests/test_generation_review_contract.py \
  tests/test_read_file_recovery.py tests/test_prompts.py \
  -k 'not interference_real_geometry and not measured_partition and not zero_gap_still and not solid_box_interior and not vertical_gap_hand and not axis_orientations and not body_reference and not existing_topology_subprocess and not print_metrics_real_solid'
```

## 3. 已有超限反馈的离线回放

只读已有 assembly_versions.json、报告和源码字节，复制到 `/tmp/adsl_feedback_bound_replay_20261011` 后回放投影。没有执行这些源码。coder / engineering 是投影 role 参数，不是实际模型请求；该输入采用已有 book.feedback 加当前 assembly_context，不宣称是过去网络请求的逐字还原。

| JSON 来源 | 未投影 payload（UTF-8 bytes） | 投影后整个 payload | 预算内反馈子集 | 限额 | canonical 独立故障数 | 幂等 |
|---|---:|---:|---:|---:|---:|---|
| Toys4K_penguin_001 | 1,918,709 | 45,732 | 31,717 | 32,768 | 1 | 是 |
| Toys4K_sheep_038 | 2,622,871 | 56,746 | 31,259 | 32,768 | 1 | 是 |

两种 role 的数字相同，明细见 [feedback_replay_summary.json](execution_grouping_feedback_v1_20261011/feedback_replay_summary.json)。32 KiB 仅限制既定 feedback 子集，不是全文、图片或模型 token 上限；表中两个总 payload 因现有任务/视觉字段仍大于 32 KiB。

原始逐面数据保持在旧文件/快照；规范组、source candidate 和证据目录裁剪都有完整 pointer。新人工默认预算测试实际读回 evidence_files 全集 100 项；预览 3 项、omitted=97，再次投影相等。7 组故障中最后一组被预览省略时，typed finding 关联重投影不产生第 8 组。已有 request-error 停止/权限/完整引用回归继续通过。

回放脚本：[offline_feedback_replay.py](execution_grouping_feedback_v1_20261011/offline_feedback_replay.py)，需要原历史数据目录；仅标准库读文件和适配器调用。

## 4. 验证边界与提交状态

已验证：源码异常穿过子进程→父进程→控制器→有限 Coder 输入；历史保留；完全相同失败源码不重复执行；语义分组/API/区域坐标和初次动作合同接通；新 .2 与旧 .3 的 reference/config/resume 兼容；面积与 G 最优姿态区分。

未验证：真实 Agent 是否能消除鹿的源码错误、动物是否自主有效分件、外观或面积是否改善、原灯具 GLB/float32 是否通过。本轮没有重放灯具或鹿源码，没有修改网格后端，不声称任何真实物体制造/站立成功。

本地四阶段提交，未合并、未推送；主目录及其未提交修改未触碰。完整 patch 可使用 `git diff 552e9ef..HEAD` 查看。
