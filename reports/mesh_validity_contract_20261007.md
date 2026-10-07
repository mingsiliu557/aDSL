# Mesh64 集合语义、方向检查与毫米预算补齐

日期：2026-10-07。分支：`codex/mesh-validity-v1`。修改前 HEAD：`7addd31119760b020c09cde8591136d7c709a566`。仅补用户指定的三个问题，没有恢复 benchmark 或调用真实模型。

后续已补齐翻面进入局部修复搜索；原灯臂、象鼻及含 connector 的完整上部现已通过实际 GLB／STL 读回，见 [后续实现及验证](mesh_precision_flip_repair_20261007.md)。本文保留当次真实结果。

## 实现

1. `adsl-core/core/export/mesh64.py::evaluate_shape()`：INTERSECT 保留每个声明操作数，包括没有任何 piece 的空 Asset。每个操作数内部的多个 piece 先作 UNION，再与其它操作数求交。验证 `A ∩ ∅`、`∅ ∩ A`、嵌套空集及多 piece 操作数；没有改变独立对象、UNION/HULL 或多 base 共用 cutters 的 DIFFERENCE 语义。
2. `local_precision_repair.py::face_orientation_metrics()/require_face_orientation()`：使用缩放后的边与法向，稳定比较全部对应三角面的几何法向。有限、非零法向的点积必须严格大于 0；翻面、垂直或无法比较均拒绝。`target_mesh()` 的直接转换、repair 的 UNCHANGED 返回及修复后最终全部面共用这条规则。局部收缩及翻边也复用它；翻边不再用平均法向代替各新面与实际转换结果的比较。诊断增加翻面数、坍缩/不可比较数、最小点积及前八个失败面索引。
3. `mesh_validity.py::target_mesh()/normalize_blender_input()`：先用局部半径乘 `mm_per_unit` 得到毫米尺寸，再应用固定 **1 mm** 尺度下限，位移预算为 `16 × float32_epsilon × max(1 mm, local_radius_mm)`。执行时才换回场景单位，体积预算仍由表面积及位移预算计算。增加 `local_scale_mm`、`budget_scale_mm`、`absolute_scale_floor_mm` 记录。原单位为毫米的预算不变。

float32 候选 ULP 仍按实际待写坐标计算；不同单位的二进制舍入未承诺完全相同。验证的是同一物理物体的毫米位移/体积预算一致，整体平移不会扩大它。

## 实际验证

- INTERSECT 定向原生验证：27 passed，0 skipped。
- 局部精度/法向定向验证：14 passed，0 skipped。
- 最终受影响的网格、导出与执行器回归：**151 passed，0 failed，0 skipped，67.84 秒**。前两项包含在最终总数中，不重复相加。原生 Blender 标志已开启，另包含已有执行器 stub 测试；没有启动后续物理计算。
- `git diff --check` 通过。

新增反例验证：源与真实 float32 网格均闭合、索引绕序一致、体积为正、Manifold NoError，但一个几何面法向翻转；UNCHANGED 路径拒绝。将此结构与可修复退化邻域组合，局部退化修好后，最终全部面检查仍能捕获未编辑区域的翻面并回滚。另一例直接走 `target_mesh()`，原始转换因两个翻面被拒绝，已有有限简化候选重新通过全部面检查后才采用。

同一物理箱体分别以 `mm_per_unit=0.001、1、1000` 表示，覆盖亚毫米和大于下限两种尺寸；毫米位移预算及体积预算一致。Blender 输入修复的亚毫米下限也使用这三组单位验证。既有真实 0.2 mm 间隙、空腔、材料、Joint、零面积和微裂缝回归通过。

## 原反例复查及仍存在的限制

| 原输入 | canonical / ASCII STL 实际读回 | float32 GLB | 求值与导出耗时 |
|---|---|---|---:|
| 原 53 操作数灯臂 | PASS；零面积、开放边、重复面、非流形边/顶点、方向不一致边均为 0 | FAIL，明确拒绝 | 23.96 秒 |
| 原象鼻 | PASS；上述缺陷均为 0 | PASS | 8.28 秒 |

**灯臂此前的 GLB PASS 被新检查推翻。** 1/2 ULP 候选虽已通过闭合性、流形性及索引绕序检查，逐面比较仍发现 **3 个几何翻面**，因此均拒绝。原始转换/修复候选还有其它退化。没有放宽方向约束、增加搜索策略、扩大预算或修改原始几何来保住旧 PASS；测试现改为验证有效制造输出和明确显示拒绝。这是新增检查发现了遗漏，不是灯臂显示问题已经解决。

原灯源码 SHA256 保持 `286f51cb95bed9d3fe996369ba406378d13465299ce0feb6ee10e908eb70f068`。完整整灯未为这三项修改再次执行；旧整灯显示失败记录仍是历史证据。上一份报告的完整上部定位文字有坐标笔误：冻结诊断中是目标局部 z 约 **73.889 / 73.995**，不能使用文字中的 72.89–72.99。

没有可靠的完整三角自交检测，继续记录 `self_intersection=NOT_EVALUATED`；逐面法向检查也不是自交证明。没有运行 overhang、standing、FEA、完整 Agent 流程或新增压力测试组。

## 证据与复现

[轻量反例结果](mesh_validity_contract_20261007/fixture_results.json)、[源码/实现身份记录](mesh_validity_contract_20261007/provenance.json)、[最终回归日志](mesh_validity_contract_20261007/regression.log)。实际 STL、象鼻 GLB 及完整诊断保存在 `/jiigan-hp/lms/aDSL/experiment/mesh_precision_export_20261007/contract_fixes/`。

```bash
cd /tmp/adsl_mesh_validity_v1_20261007
export PYTHONPATH=/tmp/adsl_mesh_validity_v1_imports:/tmp/adsl_mesh_validity_v1_20261007
export PYTHONDONTWRITEBYTECODE=1
PY=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
ADSL_TEST_FIXED_REAL=1 ADSL_TEST_GEOMETRY_REAL=1 "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_local_precision_repair.py tests/test_mesh_validity.py \
  tests/test_mesh64_evaluation.py tests/test_mesh64_exports.py \
  tests/test_boolean_recovery.py tests/test_boolean_solver.py \
  tests/test_zero_area_tessellation.py tests/test_microcrack_welding.py \
  tests/test_mesh_manufacturing_export.py tests/test_mesh_display_execution.py \
  tests/test_execution_cleanup.py
```
