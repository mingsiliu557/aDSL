# Agent feedback 六项复查修补

日期：2026-10-08。实施基线 `73abfebccf558d98f85af25cb58a1a35ac0e26a9`；分支 `codex/agent-feedback-input-v1`；工作目录 `/tmp/adsl_agent_feedback_20261007`，开始时工作区干净。本次直接在服务器修补，没有转发任务、合并或推送。

## 实际修改

| 复查项 | 修改位置 | 实现与验证 |
|---|---|---|
| 1. Critic 终止后多发 Engineering | `adsl-agents/fixed_assembly.py` 的 Engineering 调用条件 | 同时检查 `terminal_request_error is None` 和 `propagate_error is None`。几何退步／分件 reason 仍可记录，但不重新授予请求权限。自然两轮 fixture 中第二轮 Code Critic 注入 context-limit／quota、checker 退步；错误之后新增 Engineering／Coder 调用均为 0，版本和既有一次预约保留；quota 保存账本后重抛同一异常。 |
| 4. benchmark 共享故障判定分叉 | `experiments/benchmark_six/run.py::shared_fault` | 先复用统一 classifier 的 shared_fault，再保留 frozen/hash/disk/module 等原有基础设施判定。message-only quota 和 code-based quota 为 True，普通 429、context-limit 为 False，TypeError／KeyError 不新增为共享故障。这里只测试函数，不启动 batch。 |
| 2. 同源分类／权限丢失 | `adsl-agents/agent_feedback.py::_Projection.add`、`process`、typed finding 适配 | 内部新增 `classification_origin`，`failure_feedback`／`evaluation_failures` 及原始 typed domain 使用控制器分类来源，原始 failures 为诊断来源；控制器显式分类覆盖原始行，False 不做 OR。同等来源的矛盾分类抛 `AGENT_FEEDBACK_INVALID`，不默默放行。验证两种先后布局、Engineering／Coder 一致性、显式 False、冲突和无授权原始诊断。 |
| 3. 裁剪后全集引用不完整 | `agent_feedback.py::_preview`、canonical index 和预算 reduction | 保留已有全集 full_ref；初次裁剪前保存完整 canonical 组，预算只缩短预览。source_candidates 使用真实 result pointer，缺报告时保存未裁剪数据或引用原 finding 快照；历史预算裁剪引用裁剪前原记录。默认 32 KiB 测试实际读取所有省略列表的 pointer，数组长度等于 total，二次投影相等，输入未修改。 |
| 5. 缺报告的 raw finding 丢证据 | `agent_feedback.py::_Projection.findings` | 原报告不能读取而 raw domain 尚在时，先写确定性 finding 快照；详情指向快照，原始 result_ref 和不可用 evidence_ref 继续保留。关联状态与 availability／source binding 分开；只剩旧摘要时不重建原数据。覆盖 result_ref 为 None／缺文件、raw／仅摘要四种组合，没有凭快照推导修补权限，重投影稳定。 |
| 6. executor TypeError 被吞 | `fixed_assembly.py` 的 executor 外层 catch | 接入同一传播判定，先保存 flow_error 与版本账本，统一收尾后重抛原异常对象。stub executor TypeError 后无角色请求、无 Coder 预约；AssetExecutionError／正常导出错误的原处理继续由相关回归验证。 |

三个公共投影入口的签名、输出 DTO、几何／物理判据和预算均未改动。新测试放在原 `tests/test_agent_feedback.py` 与 `tests/test_benchmark_six_runner.py`，未建立新测试框架。

## 实际 smoke 与回归

实施顺序为停止守卫／classifier → 控制器分类合并 → 全集引用、缺报告快照和 executor 异常传播。

| 运行 | 实际结果 | 说明 |
|---|---|---|
| 第一步：停止标记及 batch classifier 定向 smoke | 6 passed，88 deselected，2.91 s | 初次新增两轮夹具漏了必填 target 字段，出现 2 failed；补齐夹具后上述目标分支全部通过，未改生产阈值。 |
| 第二步：分类／权限定向 smoke | 6 passed，44 deselected，1.75 s | 先后布局、False 和同等权威冲突。 |
| 六项新增 smoke 合跑 | 16 passed，88 deselected，4.22 s | 引用初测还发现 source_version 在第二次绑定才补齐；改为首次引用即绑定原证据版本后通过。 |
| 相关原回归首次合跑 | 174 passed，1 deselected，31.75 s | 新增 None result_ref 和无授权断言之前的结果。 |
| 最终相关回归 | **176 passed，1 deselected，31.50 s** | [原始日志](agent_feedback_review_patch_20261008/pytest_final.log)。 |
| 审查提供的四个失败控制流探针复跑 | **4 passed，2.82 s** | [原始日志](agent_feedback_review_patch_20261008/review_probes.log)；使用原 `/tmp/test_adsl_feedback_review_73abfeb_extra.py`，未改探针。 |

这些执行有重复覆盖，不相加作为不同测试总量。唯一 deselected 是 `test_interference_real_geometry_drives_engineering_coder_recheck`，保持本轮离线边界。此次没有重新确认上次报告中的完整 234 项。

最终相关回归命令：

```bash
cd /tmp/adsl_agent_feedback_20261007
PYTHONPATH=/tmp/adsl_feedback_imports PYTHONDONTWRITEBYTECODE=1 \
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest \
  -q -p no:cacheprovider \
  tests/test_agent_feedback.py tests/test_mesh_evaluation_feedback.py \
  tests/test_generation_review_contract.py tests/test_fixed_assembly_recovery.py \
  tests/test_read_file_recovery.py tests/test_assembly_feedback_recovery.py \
  tests/test_benchmark_six_runner.py -k 'not interference_real_geometry'
```

原审查探针的复跑使用同一 Python，PYTHONPATH 增加当前 worktree 的 tests 与根目录，运行 `/tmp/test_adsl_feedback_review_73abfeb_extra.py`。实际 import origin 已核对为本 worktree 的 agent_feedback、fixed_assembly、benchmark_six 和 request_errors，没有误用主工作区。

## 引用与停止证据

默认预算样例的紧凑 JSON 为 **21,431 UTF-8 字节**；6 条 source_candidates 被预算裁为 1 条，其 full_ref 仍读回 6 条；10 次尝试预览为 3 条，其 canonical pointer 仍读回 10 条。首次、再次投影完全相等。测试对每个被省略的列表实际读取文件和 JSON pointer，而非仅比较 metadata。

[离线证据摘要](agent_feedback_review_patch_20261008/validation.json) 保存 pointer／总数／实际长度、幂等结果、终止账本字段与 source hash。场景数据全部为人工反馈／stub，不是 mesh 测量值或真实物理结论。

第二轮 Critic context-limit：账本 completed=true、stop_reason=agent_input_too_large，保存 original／attempt_0001；quota：账本 completed=false，保存相同版本后重抛。executor TypeError：保存 original 与 flow_error，completed=false，原异常重抛。四探针和新测试均断言错误后没有新增角色调用；原有 Coder 预约不退款。

## 边界

真实模型调用 **0**；未启动 benchmark、Blender、真实 mesh 或物理实验。本轮只修输入投影、证据完整性及错误出口，没有修改 checker、网格修复或评分公式。主工作区既有修改未覆盖，实验继续暂停；不宣称真实 Agent 已成功修补旧几何故障。
