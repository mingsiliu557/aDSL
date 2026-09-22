# 公共 EXACT 替换后的验证：局部有效，整件尚未通过

2026-09-22，基于 `ee712bc`。用户授权最小替换并推送；验证发现新回归，**当前保留未提交改动，暂缓推送**，没有放宽阈值或修模型来绕过。

后续状态：用户已授权ASCII STL精度修复，两项真实导出回归及正常支架topology已恢复通过；
详见 [修复与两例全新生成记录](exact_ascii_stl_fresh_20260922.md)。下文保留修复前证据，不改写历史失败。

## 实际修改与小回归

- 公共 `export_glb.py::_apply_boolean()` 仅将求解器序列改为 `("EXACT",)`。
- `tests/test_boolean_solver.py`：三种操作 × 成功/异常，共 6 项；确认只调用 EXACT，保留异常与清理。
- 加现有 visual-only 测试，共 **21 passed**。
- 两项真实导出回归 `test_real_boolean_export[normal/tilted]` **2 failed**：`stem.stl` 无效。
- 同样的两份测试源码/冻结配置，用从 `ee712bc` 读取的原 `_apply_boolean` 在独立进程重放，均 PASS。
  因此不能将这两项失败解释为原有测试问题。未修改测试期望。
- 对先前冻结的 SF02/SF03/SF07 原始 Boolean 操作数，直接调用修改后的公共 `_apply_boolean`，
  三次输出均有效，且与之前独立 EXACT 对照的三角坐标逐值相同。只证明局部接入正确。

第一轮 pytest 命令：

```bash
ADSL_TEST_FIXED_REAL=1 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=1 \
  /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q \
  tests/test_boolean_solver.py tests/test_fixed_assembly_visual_only.py \
  'tests/test_fixed_assembly.py::test_real_boolean_export[normal]' \
  'tests/test_fixed_assembly.py::test_real_boolean_export[tilted]' \
  --basetemp=temp/exact_solver_tests_20260922
```

## 用户追加的 topology 复查

SF02、SF03 使用此前原始源码，而非 SF02 后来的 agent 修补版；源码不变、冻结毫米尺度/余量/visual_only 配置不变。
使用新目录重新导出完整资产，只执行 assembly_topology，不调用 API/agent，不重新审核外观、不改初始成绩。
真实导出及 topology 分别沿用隔离进程、120 秒诊断预算，没有超时。

| 对象 | 件内连通 PASS | 接口配对 PASS | 总项 | 剩余证据 |
| --- | --- | --- | --- | --- |
| SF02 原始源码 → EXACT | 4/6 | 3/5 | INDETERMINATE | `backrest_panel`、`leg_back_left_part` 各 3 条开口边；对应两接口依赖未满足 |
| SF03 原始源码 → EXACT | 1/6 | 0/5 | INDETERMINATE | 座面通过；四腿及 `backrest_unit` 各 3 条开口边；五接口依赖未满足 |
| 正常 T 支架 → EXACT | 1/2 | 0/1 | INDETERMINATE | `stem` 的落盘 STL 有 3 个零面积面；接口未验证 |
| 同一正常 T 支架 → 原策略 | 2/2 | 1/1 | PASS | 对照通过 |

这些未验证不是已确认断开，更不能把依赖未执行的接口称为脱开。SF02/SF03 的 export_status=PASS 在 visual_only 下只表示基本导出可用，不覆盖 topology 的 INDETERMINATE。

此次未重导其余历史模型，因此没有新版 6×2 总通过率。没有生成新 candidate、没有 Image/Code 重新审核，也没有发布新 retained。

## 已证实的新回归位置

正常 T 支架 `stem.glb` 有 44 个三角面、0 个零面积面，最小面面积约 `3.37e-7 mm²`。
读取同一份保存 GLB，恢复 Z-up，再应用现有打印落地平移 `z += 50 mm`：仍为 0 个零面积面。
仅把这些坐标转成 STL 使用的 float32，再转回 float64 检查：变成 **3 个零面积面**；
其所有三角坐标与实际保存 `stem.stl` 的表面集合完全一致（包含退化面，没有删面）。

因此，至少这个正常对照的新问题已定位到 **EXACT 所产生的小面与现有打印平移/float32 STL 编码的组合**。
不是说 EXACT 直接给出了三个零面积面，也不是修好的 topology NPZ 缓存又变回 float32。
SF02/SF03 的其余开口尚未做逐阶段归因，不能直接套用此原因。

下一步如要修导出精度，需作为明确的小修复单独确认；本轮不自动删小面、不换打印尺度、不放宽拓扑容差。
不能凭两个局部 UNION 成功就宣称整条 CSG/装配链已经适合全局替换。

## 保存位置与调用说明

- `temp/exact_solver_tests_20260922/`：21通过/2失败的测试产物。
- `temp/exact_solver_public_check_20260922/`：两次原策略回归及三次公共 EXACT 原操作数验证，均有调用/日志/测量。
- `temp/exact_topology_verification_20260922/SF02|SF03/export/`：新导出完整资产。
- `temp/exact_topology_verification_20260922/corrected_topology_summary.json`：上述 SF02/SF03 正确复查结果。
- 对应 `topology_correct_manifest/checkers/assembly_topology/report.json`：逐件、逐接口证据。
- `T_normal_EXACT` / `T_normal_previous`：正常支架的新旧 topology 对照。

本次临时诊断调用 SF02/SF03 时首次误用了 render 目录作为 manifest 基准，未执行几何测量，错误现场保留。
随后仅修正该次调用参数为 `export/assembly/assembly_manifest.json`，复用同一批落盘资产，不再次导出、不修改生产 adapter。
表格来自修正后的真实检查；不能把第一次路径错误当模型未验证的几何原因。
