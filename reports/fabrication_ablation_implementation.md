# 消融开关 v3 实施报告

基准 `e5e13fb58aa08f0d717416e8216e1f3ecc7c3e86`；分支 `fabrication_ablation`，独立 worktree。原 `aDSL` 工作目录保持 `action_v2`，原有未跟踪资料未移动或清理。未合并或推送。

## 实际改动

| 文件与锚点 | 修改 |
|---|---|
| `adsl-agents/models.py:82` FixedAssemblyConfig | 单值 ablation 字段 |
| `adsl-agents/fixed_assembly.py:33` ablation；iterate_fixed_assembly | 统一读取、默认 frozen 去键、运行记录；显式跳过 Engineering、禁用依赖提案的可选优化 |
| `adsl-agents/service.py` _validate_request / _plan_generation / generate / resume / _repair / 两个 review 入口 | 冲突校验、普通 Planner 转单件计划、角色上下文与反馈显式传参 |
| `adsl-agents/actions.py:75` edit | 兼容的可选 ablation 参数，传到原 _repair |
| `adsl-agents/assembly_topology.py:487` engineer | 直接入口防护；脱敏反馈和受限文件上下文 |
| `adsl-agents/agent_feedback.py:1040` payload_for_agent | 兼容的可选 ablation/tool_context，脱敏视图按原口径计入预算 |
| `adsl-agents/ablation_views.py` | 隐藏源码绑定、私有回溯日志、可读取脱敏证据 |
| `adsl-agents/tools/context.py:38` / `tools/files.py:56` | 仅当前源码及登记的脱敏文件可读取；原始产物保留原路径 |
| `adsl-agents/prompts.py:39,178` object_prompt / _ablation_prompt | 条件提示过滤；默认文本不变；只修改内存视图，不修改 core 文档 |
| `adsl-agents/utils/ablation_source.py` / `utils/asset_executor.py:61` | 生成源码 AST 检查和原源码错误记录链路 |
| `adsl-agents/feedback_schema.py:188` execution_failure_summary | 保留禁用算子原因及名称 |
| `tests/test_ablation.py` | 74 个配置、提示、读取权限、实际角色入口和流程测试实例 |

各组的能力关闭、配置要求及连带差异见 `docs/fabrication_ablation.md`。fab_plan 要求 NSV 和自动朝向开启，不静默替用户改配置。默认输出有计划要求的 ablation=null 元数据；默认提示和 frozen hash 不变。

## 验证结果

最终回归：**459 passed，12 deselected**。12 个参数化测试实例涉及原生几何／执行或测量，按本次 stub 范围排除，不算失败。pytest 有一个既有 anyio assertion-rewrite 提示，不影响结果。

覆盖新增 74 个测试及原 Action、single、support、反馈、分件选择／权限和恢复测试。重点实际经过：

- 合规 review 启动校验、导出失败后 Coder 修补；Engineering 未调用。
- actions 必要修补及可选优化停止，不创建支撑检查点。
- 四个实际角色入口的 tracing 脱敏与读取白名单；Image Critic 不依赖 payload 内的 fixed_assembly。
- 原始报告、索引、私有日志及指向原报告的符号链接不可读取；源码与脱敏证据可读。
- profile_ops 四类调用写法、错误主码和详情透传、下一轮真实适配器／文件工具修补；基本体源码通过执行器 stub。
- 初始／恢复 Coder 提示；fab_plan 普通 Planner 输入/schema、转换落盘、恢复不重新调用 Planner。
- 默认所有角色三种 metric/shape 组合与基准提示逐字相同，默认 hash 和旧 checkpoint 恢复保持一致。

### 可复现命令

在本 worktree 根目录，使用已有测试依赖环境。由于原环境的 editable 安装仍指向旧工作区，本次用下列仅进程内导入覆盖测试新代码，不重装或改变正在使用的环境：

```python
from pathlib import Path
import importlib.util
import sys
import adsl
import pytest

root = Path.cwd()
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location(
    'adsl.agents', root/'adsl-agents/__init__.py',
    submodule_search_locations=[str(root/'adsl-agents')])
module = importlib.util.module_from_spec(spec)
sys.modules['adsl.agents'] = module
adsl.agents = module
spec.loader.exec_module(module)
files = [
    'tests/test_ablation.py', 'tests/test_actions.py',
    'tests/test_single_print_mode.py', 'tests/test_support_workflow.py',
    'tests/test_agent_feedback.py', 'tests/test_partition_selection.py',
    'tests/test_fixed_assembly_recovery.py', 'tests/test_partition_feedback.py',
]
excluded = [
    'partition_implementation_repair_keeps_intent_through_real_payload',
    'mate_mismatch_source_execution_reaches_coder_diagnostic',
    'single_auto_search_without_partition', 'multibody_checker_does_not_crash',
    'measured_partition', 'zero_gap', 'recommended_regions',
]
raise SystemExit(pytest.main(files + ['-q', '--tb=short', '-k',
    ' and '.join('not ' + name for name in excluded)]))
```

最后一次 tracing 提示澄清后单独重跑新增定向集合：**74 passed**（2.29 秒）。`git diff --check` 通过。

## 边界

没有模型调用、真实生成、仿真、正式评测或消融效果结论；没有修改 adsl-core、检查器判据或阈值。没有新增批处理消融脚本。

AST 检查覆盖约定的公开 API 写法，不是任意动态 Python 的安全沙箱。文件读取限制作用于 Agent 的 read_file 工具。tracing 私有日志保留原始定位供离线研究使用，不发送给模型；原始 checker 报告和版本账本保持不变。

单件自动朝向继续复用既有实现，本次没有执行真实朝向搜索；今后的消融结果必须说明 single 自动朝向与完整版提案式朝向调整的流程差异。

## 修复计划 v2（基准 09c8e3a）

本节替代前文关于 tracing 引用一刀切移除及 actions 停止全部可选优化的说明。未修改配置接口、adsl-core、检查器阈值或支撑采纳公式。

### §3.0 前置确认

完整版允许带 partition_objective 的纯形状候选被采用。`iterate_fixed_assembly` 在 `can_refresh` 分支对合法 CHANGED 几何刷新参考，随后用候选与刷新后的参考重新计算 shape_comparison，可得到 MATCH。后面的 body_ok 检查使用这个刷新后的结果，并未要求候选与原始几何相同。因此 §3 可以实施，无需放宽采纳条件。`test_actions_differential_shape_with_partition` 同时跑完整版及 actions，覆盖采用、无收益恢复，以及中途 Topology FAIL 后续修的采用／恢复。

### 改动位置与测试对应

| 要求 | 实际位置 | 测试 |
|---|---|---|
| 运行时拦截、恢复及最内层源码行号 | `utils/ablation_source.py:45` disabled_profile_runtime；`utils/asset_executor.py:61` | test_runtime_dynamic_guard_restores_objects_and_line |
| 子模块、Load、常量 getattr 的 AST 检查 | `utils/ablation_source.py:13` check_profile_source | test_ast_submodule_and_load；既有 test_profile_ast / test_profile_error_survives_summary |
| 收窄物理回溯隐藏、保留执行诊断 | `ablation_views.py:29` tracing_view | test_tracing_nested_evidence_preserves_geometry_and_diagnostic |
| 递归 JSON 副本、pointer、白名单与内存测量 | `ablation_views.py:65` sanitized_evidence；`agent_feedback.py:1040` | test_tracing_nested_evidence_preserves_geometry_and_diagnostic；既有四角色入口测试 |
| 压缩完成后记录物理定位，不记录 Python traceback | `ablation_views.py:40` record_hidden_tracing；payload_for_agent 最后输出边界 | test_tracing_nested_evidence_preserves_geometry_and_diagnostic |
| 原 candidate_ref 等仍读原件 | 未修改 _Projection.ref/source_ref/candidate_ref/snapshot | test_tracing_keeps_internal_candidate_verification |
| actions 只过滤分组／手动朝向机会，保留形状支撑优化 | `fixed_assembly.py:1501` 附近的筛选及后续预算／提案分支 | test_actions_differential_shape_with_partition；test_actions_only_structured_opportunity_stops |
| actions 分组／程序声明旋转变化不能采用 | `fixed_assembly.py:54` _actions_structure_preserved；`:1377` 候选拒绝 | test_actions_structured_edit_is_rejected_in_loop；test_actions_forbids_group_and_authored_rotation |
| 禁用 Engineering 不等于找不到提案，仍执行预算检查 | iterate_fixed_assembly 的预算及无提案处理 | test_actions_budget_exhaustion_uses_existing_reason |
| actions 精确提示替换，去掉正则删句 | `prompts.py:200` _ablation_prompt | 更新 test_actions_prompt；默认提示逐字比较保留 |
| 共享 NSV 启动校验、四变体要求 topology/overhang | `fixed_assembly.py:40` validate_nsv_config；service._validate_request | test_nsv_errors_before_first_model；test_required_checkers |

差分断言核对 accepted/reason、retained/qualified、stop_reason、最终源码、support_baseline_version、comparison_baseline_version 和 partition_reference 的身份。动态兜底测试让拼接名字通过 AST 后在运行时拒绝，并在同进程检查全部属性恢复及普通模式再次调用；恢复调用使用 stub，不生成几何。

tracing 的 measure 只规划内存结果，不写脱敏文件；最终输出阶段统一写入、登记白名单。原件及内部反馈不改写。循环引用在递归栈上标为不可读取，不制造不完整的原始证据。已脱敏输出在来源绑定仍有效时保持稳定；来源变动则重新进入原投影核验。源码执行诊断中的 node_path 和 source_locations 保留，物理 overhang scope 说明也保留。

### 验证结果与命令

- 最终相关 stub 回归：**495 passed，12 deselected**（41.15 秒）。排除项与前文相同，均涉及原生几何执行／测量。
- 最后补齐两种结构化机会测试后的修复定向集合：**38 passed**（1.97 秒）。与回归集合有重叠，不能将两个计数直接相加。
- git diff --check 通过。仍有既有 anyio assertion-rewrite 提示。
- 两处旧测试配置补选 assembly_overhang：test_single_print_mode.test_config_conflicts 与 test_support_workflow.test_initial_and_resumed_coder_receive_nsv_policy。这对应本计划明确要求的非法 NSV 校验提前，未改变合法完整版行为。

使用前文的进程内 worktree 导入命令，回归 files 列表增加 `tests/test_ablation_repairs.py`；其余文件及排除表达式相同。修复定向命令为该导入环境下 `pytest tests/test_ablation_repairs.py -q --tb=short`。

**模型调用、真实生成、仿真及评测均未执行。** 本次结果只验证消融接线与 stub 决策，不构成消融实验效果。

## 修复计划 v3（基准 2e755ce）

本次只修复固定结构基线、非有限数值与显式幂等标记。未改动运行时算子拦截、AST、NSV 校验、采纳公式及已验收的形状优化／停止逻辑；未修改 adsl-core。

| 问题 | 修改位置 | 测试名（tests/test_ablation_repairs_v3.py） |
|---|---|---|
| P1 固定结构基线、分组／旋转差异 | fixed_assembly.py:54 `_actions_structure_diff`；`:1398` 固定版本比较；`:1429` 首个完整结构版本登记 | test_fixed_baseline_rejects_cross_round_bypass_and_resume（分组／朝向，直接／续跑四个实例）；test_first_successful_export_establishes_baseline_after_source_failure；test_structure_diff_connections_and_invalid_records |
| P1 支撑回退保留结构基线 | 沿用 `_restore_support_baseline`，不修改其恢复字段 | test_support_restore_keeps_structure_baseline |
| P1 给 Coder 明确恢复目标 | fixed_assembly.py:1483 structured_action_violation；`:1658` 专用 primary_objective | test_fixed_baseline_rejects_cross_round_bypass_and_resume；test_source_failure_instruction_precedes_structure_restore |
| P2 严格 JSON 及最近 dict 标记 | ablation_views.py:65 `_finite_view`；在副本与输出序列化前共用转换 | test_nonfinite_nested_evidence_and_memory_measurement |
| P3 显式签名标记替代内容猜测 | agent_feedback.py:1047 标记核验及捷径；最终输出增加 ablation_view | test_explicit_marker_is_required_and_invalidated_by_new_feedback；test_nonfinite_nested_evidence_and_memory_measurement |

### 行为

- actions 的 `book.actions_structure_baseline` 只在首次非空 part_declarations/parts 版本登记，并随原 book 持久化。之后候选始终和该版本比较，working 仍按原逻辑前进。完整版及其他变体不添加该字段。差异返回打印件 ID；缺失结构、非法矩阵标为 invalid。只改连接声明可通过结构检查。
- 连续保留违规改动仍被拒绝，恢复固定基线后可进入原验收。源码执行失败时，修复源码的指令在恢复分组／旋转的指令之前。feedback 包含具体基线与违规件，不再误用“实现授权分组提案”的指令。
- 副本中的 NaN/Infinity/-Infinity 变为 null；列表长度、索引和 pointer 不变。最近 dict 以 setdefault 添加 NONFINITE，不覆盖已有 value_status。原始证据字节不改写；内容哈希以转换后的数据计算。measure 与最终写入共用转换，内存测量不写文件。
- tracing 输出顶层 ablation_view 保存 variant 及 `_encoded` 计算的正文摘要。有效标记才允许原样返回；无标记、摘要不匹配都执行正常投影和压缩。旧标记在正常路径中先移除，再对最终 Agent 视图重新签名。内部 payload、feedback 和 book 不写该标记。

### 验证

- 相关 stub 回归：**506 passed，12 deselected**，39.44 秒。排除项仍为既有原生几何执行／测量测试，不算失败。
- 最后补齐“源码失败指令在前”测试后，v3 定向集合：**10 passed**，1.43 秒；与回归有重叠，不相加当总数。
- `test_ablation_repairs.py` 更新旧 bool 断言，并在完整版差分分支确认 book 没有 actions_structure_baseline。
- 默认提示逐字比较、frozen hash、旧 checkpoint 及完整版差分序列继续通过。git diff --check 通过。
- 使用前文同一进程内 worktree 导入方法，回归文件列表增加 `tests/test_ablation_repairs_v3.py`。定向参数为 `tests/test_ablation_repairs_v3.py -q --tb=short`，其余回归文件与原生测试排除表达式不变。

**没有运行模型调用、真实生成、仿真或评测。**
