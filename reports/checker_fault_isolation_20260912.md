# 多 checker 故障隔离补齐（2026-09-12）

基于本地 master `a7b6a1f`，只修改 checker 执行、候选验收和工作流结束逻辑。
未改模型、OCC 重建、仿真参数、缩放、checker 阈值或已有未提交的其他修改。

## 修改位置与规则

- `adsl-agents/checkers.py`：沿用每 checker 子进程及超时回收；补齐配置、启动、执行异常的边界。崩溃、超时、缺失/无效结果记为 `ERROR`，完整异常只存文件。单 checker 输出目录不可写时仍返回内存中的 ERROR，其他检查继续。
- 同文件 `run_checkers()`：配置了 topology 时先执行它；非 PASS 则 FEA 返回 `INDETERMINATE`，保留依赖状态及报告路径，不执行 FEA。返回结果仍按配置顺序排列；standing 等独立检查继续。没有配置 topology 的独立 FEA 调用保持原行为，不增加通用依赖调度。
- `adsl-agents/repair_policy.py::assess_candidate()`：前后均不可用的检查不再一票否决；但必须有其他有效目标的实际改善，并通过既有数值回归与外观检查。ERROR/INDETERMINATE 中的数值不参与改善计算，恢复可用或跳过检查本身也不是改善。
- 原 PASS 变 FAIL/ERROR/INDETERMINATE 必须拒绝；原已评估 FAIL 变不可用也保守拒绝，不能通过丢失证据掩盖问题；候选缺失 checker 结果仍拒绝。返回 `unavailable_checks` 保留未验证项。
- `adsl-agents/service.py`：删除候选 required checker ERROR 的提前统一拒绝，统一交给上述验收；只有验收接受才复制候选源码。仍运行原有 image critic/必要时 code critic。
- 同文件反馈：不可用检查只向 Engineering Critic 提供短摘要、失败阶段、代码及报告路径，不传完整日志或把错误包装成几何修复目标。Coder 继续只收到当前方案的相关物理/几何证据。
- 没有可执行修复方案、修复预算或轮数耗尽时正常完成并保存现有模型，不再因 publication gate 未通过抛出工作流异常。`completed` 只表示流程结束，不代表物理通过。
- 模型对应的状态保存到 `checker_results.json`，并写入运行 manifest 和 checkpoint：`checker_statuses`、`unverified_checks`、`required_checkers_passed`。required 未完成时 `approved=false`，不得声明全部验证通过。历史结果仍保留于 checker_history 和各 checker 的 result.json。

## 验证

新增 `tests/test_checker_fault_isolation.py`：使用假子进程、假 checker 结果、模拟 agent/runtime 和模型占位文件；没有真实渲染、FEA、API 请求或批量实验。

覆盖：进程崩溃、启动失败、配置异常、缺少结果、解析失败；topology 四种状态及逆序配置；前后均不可用但其他指标改善；原 PASS 回归；仅跳过/恢复 checker 不算改善；短反馈；视觉循环继续；无方案/预算结束保存；真实候选方法仅在验收后复制源码；最终状态对应接受后的模型。

并复用原有超时测试，验证超时及忽略 SIGTERM 的子进程被回收，独立 checker 和后续模拟 case 继续。

```sh
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest \
  tests/test_checker_fault_isolation.py tests/test_checker_timeout_feedback.py \
  tests/test_bounded_checker_inputs.py tests/test_checker_unification.py \
  tests/test_workflow_checkers.py tests/test_publish.py tests/test_service_config.py \
  -q --tb=short
```

结果：**96 passed in 10.97s**。相关代码 `git diff --check` 通过。

## 参考边界

外部 controller 执行评估并给出紧凑失败反馈，参考 [Self-Improving CAD Generation Agents with Finite Element Analysis as Feedback §3.4](https://arxiv.org/html/2605.17448v2#S3.SS4)；其[附录 H](https://arxiv.org/html/2605.17448v2#A8)列出独立模型/评估超时，评估预算为 900 秒。
本项目的部分改善接受、PASS 保护和依赖跳过规则来自本次用户要求，不声称是论文原有验收规则。

本轮未提交或推送 Git。
