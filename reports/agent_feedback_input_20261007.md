# Agent 反馈输入修复：离线实施结果

日期：2026-10-07。基线 `a4a43ac95ffd9e3dad72d2f2263a4ce98fa84f59`；实现提交 `f31a8b713c3de9e1ca3aefaacf529db3feb6443e`。工作分支 `codex/agent-feedback-input-v1`，独立 worktree `/tmp/adsl_agent_feedback_20261007`。本轮未合并、未推送；主工作区既有未提交修改保持原状。

已完成有限反馈视图、四角色接线、请求错误分类与停止、源码读取与来源绑定，以及离线验证。完整证据仍保存在版本文件中，控制器使用原始 finding；本轮未解决 float32 几何失败，也未验证真实 Agent 自主修复。

## 实现位置

| 阶段 | 文件／入口 | 实际变化 |
|---|---|---|
| A | `adsl-agents/agent_feedback.py`：`evaluation_details`、`finding_for_agent`、`payload_for_agent` | 兼容顶层／嵌套诊断；白名单投影；同源去重；有限尝试与历史；原始 JSON pointer、确定性快照及聚合索引；32 KiB 反馈预算、严格 JSON、幂等和明确构建错误 |
| A/B | `assembly_topology.py`：求值 finding 适配、`prepare_evidence`、Engineering；`service.py`：`_checker_evidence` 和各角色调用边界 | 内部定位仍读完整 operation_nodes；Image、Code、Engineering、Coder 的 runtime 实际收到统一视图；普通 Debugger／Engineering／过悬修改复用投影 |
| B/D | `fixed_assembly.py`：`_failure_feedback`、`_assembly_context`、manifest 绑定、版本反馈 | 原始内部记录不截短；Critic 引用当时已存在的 manifest，后续引用实际 result；保存 `geometry_report_ref`；恢复反馈重新投影，失配／未知诊断不授权源码修补 |
| C | `utils/request_errors.py`；`fixed_assembly.py`、`service.py`、`overhang_edit.py` | 区分 context_limit、request_rejected、input_construction、shared_fault、transient；明确拒绝在记账后停止调用链；Coder 保留 TOOL_ERROR 并附 request_error；共享／程序异常保存账本后传播 |
| C | `providers/codex_cli.py`；`experiments/benchmark_six/run.py` | 本地 prompt guard 提供机器可读 code／大小；批运行记录嵌套请求原因与输入大小。新冻结 runner 附带仅依赖标准库的 classifier，已有 batch／checkpoint 不重新生成快照 |
| D/E | `service.py`：`_has_assigned_source_read`、Critic 历史；五份相关 prompt | 具体源码建议必须成功读取准确 assigned_source；NO_PROPOSAL 不增加门槛，已有可信内联源码路径保持豁免；平坦历史附候选 hash／版本元数据 |

新的反馈只内联摘要和引用，不内联逐面 vertices、normals、neighbors 或任意诊断大字典。文件存在与来源匹配分开判断；UNKNOWN 不使用当前 source hash 补齐。来源未知时，去重限制在同一个原始证据容器。发生聚合截短时，全集指向真实 canonical 索引，而不是错误复用原数组下标。测量非有限值在模型视图中表示为 null，并记录 NONFINITE，原值仍保留在原证据。

原 checker verdict、repair 权限、分件主目标和 resolved_visual_feedback 路径保持原合同。没有增加重试层或退款；已经预约的 Coder 尝试继续保留。模型输出不可解析仍走现有解析失败策略，明确程序异常不包装成网络错误。

## 实际验证

| 阶段／范围 | 结果 | 时间 | 证据 |
|---|---|---:|---|
| A：诊断、投影与证据初步 smoke | 12 passed | 105.10 s | 先前工具输出；未另存原日志 |
| B：角色／评审／反馈接线 | 58 passed | 27.38 s | 先前工具输出；未另存原日志 |
| C：请求、来源与相关回归 | 146 passed | 52.54 s | 先前工具输出；未另存原日志 |
| D/E：相关离线回归合跑 | **234 passed，1 deselected** | 60.00 s | [pytest_final.log](agent_feedback_input_20261007/pytest_final.log) |
| 最后新增冻结 helper 初始化／resume 验证 | **46 passed** | 2.66 s | [pytest_runner.log](agent_feedback_input_20261007/pytest_runner.log) |
| 最后新增未知来源容器隔离；完整 feedback 文件 | **42 passed** | 27.71 s | 最新工具输出；[validation.json](agent_feedback_input_20261007/validation.json) 记录结果 |

各行是分别执行的测试，包含重复覆盖，不能相加作为不同测试总数。合跑后增加的两处小修改分别重跑了整个相关测试文件；没有再进行无关扩大验证。

唯一 deselected 是 `test_interference_real_geometry_drives_engineering_coder_recheck`，避免启动真实物理 checker。没有运行原生网格压力文件、Blender、Standing／Topology／Overhang／FEA 子进程或真实模型 API。已有分件回归中的纯数学几何／体素夹具仍正常执行，不把 stub checker 状态称为真实物理 PASS。

新增 `tests/test_agent_feedback.py`；相关更新位于 `test_mesh_evaluation_feedback.py`、`test_fixed_assembly_recovery.py`、`test_assembly_feedback_recovery.py`、`test_benchmark_six_runner.py`、`test_codex_cli_transport.py`。`test_mesh_repair_smoke.py` 仅更新读取 Agent 新字段的 stub 断言，本轮未执行该原生文件。内部 raw book 的断言保持不变。

可复现合跑命令（本机已有环境；命名空间 overlay 指向此 worktree，避免 editable 安装误导入主工作区）：

```bash
PYTHONPATH=/tmp/adsl_feedback_imports PYTHONDONTWRITEBYTECODE=1 \
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/test_agent_feedback.py tests/test_mesh_evaluation_feedback.py \
  tests/test_generation_review_contract.py tests/test_fixed_assembly_recovery.py \
  tests/test_benchmark_six_runner.py tests/test_codex_cli_transport.py \
  tests/test_partition_feedback.py tests/test_partition_selection.py \
  tests/test_read_file_recovery.py tests/test_assembly_feedback_recovery.py \
  tests/test_overhang_candidate_isolation.py tests/test_prompts.py \
  tests/test_appearance_review_contract.py -k 'not interference_real_geometry'
```

最后两次分别使用同一环境运行 `tests/test_benchmark_six_runner.py` 和 `tests/test_agent_feedback.py`。分阶段实现提交为 `57bb135`、`d197d97`、`52e8b4e`；最后两项局部收尾为 `911ec4f`、`f31a8b7`。

## 人工巨大反馈及四角色实际输入

本轮使用人工构造的大型逐面诊断，不读取恐龙／龙等历史案例。原始共同 payload 为 **3,218,732 UTF-8 字节**；不是 token 数，也不是旧真实运行的四角色请求大小。下表是四个真实适配函数送到 stub runtime 的完整 JSON 输入文本，包括各自正常任务字段，因此角色之间大小不同。

| 角色 | 输入 UTF-8 字节 | 独立故障数 | 文件 |
|---|---:|---:|---|
| Image Critic | 3,417 | 1 | [captured_image_critic.json](agent_feedback_input_20261007/captured_image_critic.json) |
| Code Critic | 4,247 | 1 | [captured_code_critic.json](agent_feedback_input_20261007/captured_code_critic.json) |
| Engineering | 18,469 | 1 | [captured_assembly_engineering.json](agent_feedback_input_20261007/captured_assembly_engineering.json) |
| Coder | 14,577 | 1 | [captured_repair.json](agent_feedback_input_20261007/captured_repair.json) |

三输出同源诊断只保留一个故障组，输出位置、原 finding IDs 和证据仍可追踪。扩大逐面数组十倍不让摘要同比增长；不同部件或不同未知来源容器不误合并。超过六组时真实 full_ref 可以读回完整聚合列表。再次投影结果一致，原对象不变；图片数据不参与反馈预算。

完整人工 manifest／report／result／source 在 [synthetic_original_evidence.zip](agent_feedback_input_20261007/synthetic_original_evidence.zip)。已实际使用现有 `read_file` 读取：

```text
path: evaluation_feedback/result.json
json_pointer: /findings/0/domain/diagnostic/internal_metrics
response: {"valid": true, "zero_area_triangles": 0, "boundary_edges": 0}
```

这是夹具诊断内容，不是原生网格检查结论。[capture_evidence.json](agent_feedback_input_20261007/capture_evidence.json) 保存实际调用返回值；[implementation_evidence.json](agent_feedback_input_20261007/implementation_evidence.json) 保存源码版本、实际模块路径和交付样例 hash。

## 停止链与来源证据

Engineering 的 HTTP 400／context_too_large 注入后，stub runtime 仅被 Engineering 调用一次，**Coder 调用为 0**；账本完成，stop_reason=`agent_input_too_large`，working／retained 保持 original，源码 hash 不变，没有 repair_history 预约。对 completed resume 再进入流程，不重新请求模型。未知 400 与 schema 拒绝分别归为 request_rejected，不猜成 context_limit。

独立 Coder 拒绝测试保留一次已有预约及 attempt_0001，状态为 TOOL_ERROR，附 context_limit/request_error 和真实错误文件引用；before／after source hash 相同。认证／quota 与程序错误保存版本后重抛。[terminal_request_evidence.json](agent_feedback_input_20261007/terminal_request_evidence.json) 从真实 stub 运行账本提取停止、版本、预约和 hash。

只读报告的具体源码 proposal 被拒绝，准确读取 assigned_source 后可通过；NO_PROPOSAL 不要求源码读取。fresh/stale manifest 和缺来源候选诊断不会被重标为当前。旧格式只通过已有 assert_version 文件绑定兼容，缺证据时明确 UNKNOWN。Critic 调用时 result.json 尚未创建，引用 manifest 原下标；后续 Engineering 从实际 result finding 的 domain pointer 读取。历史保留候选版本，回滚不把旧意见变成当前事实。

## 边界与后续状态

benchmark 仍暂停；真实模型调用 **0**。未修改参考输入、生成模型配置／预算、baseline 实现、物理公式或网格修复算法。此次成果是输入表示与错误路由修复，不能据此宣布旧 float32 反例已修好、真实 Agent 已能自主修复或实验完成。

旧失败 session 的原始大请求没有删除或迁移。本轮覆盖的是新调用、book 恢复与历史重新投影；若另外手工重试旧 session role，需要使用当前有限输入，不能回灌此前被拒绝的完整请求。未自动恢复实验或追加真实调用。
