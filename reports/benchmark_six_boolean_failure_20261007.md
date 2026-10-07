# 落地灯 ours Boolean 失败离线诊断

运行：`benchmark_six_main_20261007T045229Z / ABO_B075X2XZDD / ours`。
源码 SHA256：`286f51cb95bed9d3fe996369ba406378d13465299ce0feb6ee10e908eb70f068`。`mm_per_unit=1`，本报告 scene units 与 mm 数值相同。

只对原源码的 `upper_structure` 求值，未修改源码、生产导出器或清理规则，未调用 LLM，未运行完整渲染或 checker，暂停批次未恢复。

## 已确认原因

第一个失败节点为 `CurvedArm.continuous_rounded_arm`（原源码第 58 行）的 UNION：27 根圆柱和 26 个球，总计 53 个操作数。它失败后尚未执行外层 `connected_upper_components` UNION，也尚未执行 connector 所在外层 UNION；因此这次故障不是 connector 首先引起。

53 个输入分别通过现有输入网格检查和 Manifold 构造（NoError、非空、无零面积面）。Blender EXACT 求并集后产生 2 个零面积三角形（polygon 69、698），同时三角化边统计有 453 条开放边、242 条多面共边和 14 个重复三角形。这是求值产物故障，不是“没有写上部”。原有局部重新三角化因 polygon 69 非平面而拒绝：最大平面偏离 `6.12624994e-05`，现行平面性界限 `1.98951658e-14`。

自动恢复实际已运行：Manifold 重新求并集得到 NoError、单连通、非空的 float64 参考，体积 `58.25714957343866 mm³`，8748 个顶点、17492 个三角形。原始参考没有零面积、开放边或多面共边；但有非常薄小的面。

将参考写回 Blender mesh 的 float32 坐标时，不同顶点取整到同一坐标；最大坐标舍入位移 `7.6290233364e-06 mm`，被合并顶点组的最大真实分离 `2.4207138836e-06 mm`。不简化的第一次回写生成 103 个零面积三角形；精确坐标焊接及 `dissolve_degenerate(dist=0)` 后仍非流形。现有两个有限简化尝试减少缺陷但未清除，故安全回滚并抛 `BOOLEAN_RECOVERY_FAILED`。

| simplify tolerance | float32 写回零面积面 | float32 重坐标顶点 | 焊接后开放边 | 焊接后非流形边 | 焊接后非流形顶点 |
| --- | --- | --- | --- | --- | --- |
| 0 | 103 | 43 | 18 | 27 | 18 |
| 1.52587890625e-05 | 19 | 11 | 2 | 3 | 2 |
| 3.0517578125e-05 | 23 | 13 | 2 | 3 | 2 |

两次非零简化最后均剩 2 条开放边、1 条三面共边（共 3 条非流形边），局部位置为 `x≈-0.7, y≈-0.2122794, z≈146.5857544 mm`。三条边长约 `1.34e-07 / 6.56e-07 / 6.69e-07 mm`。边坐标及入射面数在 `summary.json` / `trace.json` 中保存。

## 判断边界

已经证明：原输入各自可测；EXACT 产物失效；Manifold 并集本身能成功；float32 写回及精确退化处理仍留下不闭合/非流形局部结构。本诊断没有测试新的恢复方法，也没有证明该处必需改变目标设计。有限 simplify 并不保证目标精度下可表达所有局部薄面；现有自动处理已调用但未成功。

非零 simplify 的 float64 输出内部出现两组精确重坐标；它们仍为 Manifold NoError。JSON 的 `stats64` 同时列出“按坐标合并”的边统计，以便后续区分 Manifold 索引拓扑与导出坐标焊接规则，不能把 NoError 单独当作目标 mesh 已有效。

## 文件

- `source.py`：原始输入副本。
- `trace_boolean.py`：仅运行时 monkeypatch / sys.settrace 的复现脚本。
- `trace.json`、`summary.json`：完整与精简证据。
- `exact_boolean_failed.npz`：Blender EXACT 求值产物。
- `reference.npz`：Manifold float64 原始参考。
- `attempt_*_preweld.npz`：每档 float64 / float32 坐标及三角形。
- `attempt_*_postweld.npz`：每档实际 BMesh 焊接后的坐标及面。
- `trace_first.json`、`trace_second.json`、`run*.log`：补充采集过程记录。

共 3 次相同单部件离线复现，仅为补充不同阶段快照；单次耗时约 7.84、7.27、9.01 秒（Python 导入启动之外）。无模型 API 调用。

## 为什么没有进入 Agent 修补

这与上述几何故障是两件事。`adsl-core/core/export/export_assembly.py:482–491` 只把三个已有显式 mesh 诊断前缀判为 `candidate_geometry`；本例 `BOOLEAN_RECOVERY_FAILED` 被记录为 `PART_DISPLAY_UNAVAILABLE / failure_kind=unknown`。`adsl-agents/fixed_assembly.py:25–46` 因而输出 `geometry_repair_allowed=false`。

错误没有丢失：当前 manifest、part ID、`evaluate_part` 阶段、原始异常以及 Code Critic 建议都进入了版本反馈。但 Topology 对缺失上部只有依赖不可用的 INDETERMINATE，没有可执行的几何 finding；存在 `render_issue` 又使 `image_pending=false`。最终 `fixed_assembly.py:659–667` 触发 `export_unassessed_no_geometry_repair`，初稿即结束，9 次源码修补额度未使用。Engineering 的 actionable 入口也未触发。

因此，增加模型重试或修补额度本身不会解决这次停止。后续需要分别处理目标精度网格恢复与可定位几何求值失败的反馈合同，不能把所有未知导出／文件错误都开放为修改物体形状。本次未实施这两项修复。

## 发布与验证

本轮代码发布在 `feat/benchmark-six-method-comparison`。包含六例原生对照 harness、Astra SSH 配置／完整 Planner 流式结束校验、有限 API 错误隔离、已有过悬区域索引的 list／NumPy 兼容修正及定向测试；没有包含主工作区的无关 FEA、GPU 脚本与历史文件删除。原始 run 的 frozen harness 为 `a2f26da`，ours 原生基线为 `521fe08`，真实主环境的兼容修正现在以 `e2f4180` 发布。仓库 profile 默认 retries=2，真实实验冻结 profiles 为3，二者在 README 中明确区分。

本次离线回归命令：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
PYTHONPATH=/tmp/adsl_six_method_comparison_20261006 \
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_benchmark_six_runner.py tests/test_benchmark_six_evaluation.py \
  tests/test_overhang_region_component_types.py
```

结果：**58 passed，8.30s，0 failed／0 skipped**。这验证 harness 与该兼容修正，不代表本例 Boolean 已修好。几何诊断实际重复3次，约7.84／7.27／9.01s，均保留失败，不新增候选或模型调用。

轻量证据在 [同名目录](benchmark_six_boolean_failure_20261007/README.md)，包括源码、完整 trace、summary、复现脚本与 SHA256；完整阶段 NPZ 在数据盘 `/jiigan-hp/lms/aDSL/experiment/benchmark_six_main_20261007T045229Z/diagnostics/lamp_boolean_20261007/`。实验仍为用户暂停状态，不会自动启动下一例。
