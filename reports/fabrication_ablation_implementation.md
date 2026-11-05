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
