# float32 翻面进入局部修复搜索

日期：2026-10-07。分支：`codex/mesh-validity-v1`。实施前 HEAD：`abcbdb31adf4a2c8d138336bdcd14d7663e35226`。本次仅补齐用户批准的翻面搜索与邻域证据，保持制造 Mesh64、原始模型、现有误差预算和几何保护规则。

## 修改

- `adsl-core/core/export/local_precision_repair.py`：统一零面积、几何翻面、正交及不可比较法向的坏面集合，去重计数。快返须同时满足网格与方向检查；搜索停止条件使用全部坏面，纯翻面也能进入现有边收缩／对角线翻转。每步严格减少全局坏面总数，并保护原先正常的存续邻面。最终仍检查全部对应面方向、实体、闭合／流形、材料、空腔、分量、Euler 特征和固定预算，失败回滚。
- 同文件记录初始及剩余失败面、完整一环、F64／真实 F32 坐标、法向／点积、面高、ULP、材料及边链接。详细证据最多记录八个坏面，优先记录翻面，并显式给出完整总数及截断标记；每个已记录面的一环不截断。索引明确属于初始或当前编辑网格，避免与最终压缩索引混淆。派生诊断溢出记 null，保持严格 JSON。
- `mesh_validity.py::target_mesh()`：将 `mm_per_unit` 传给局部修复，仅用于诊断换算。没有增加收缩距离、简化候选或新的修复算子。
- `tests/test_local_precision_repair.py`、`test_mesh_validity.py`、`test_mesh64_exports.py`：覆盖纯翻面、混合缺陷、正常邻面保护、材料／预算拒绝、截断证据、有限 JSON、真实灯臂输出与制造结果不变。

## 定向及回归结果

- 局部修复／精度验证：**53 passed，0 skipped，2.81 秒**。
- 受影响的网格、原生 Blender 导出及执行器回归：**155 passed，0 failed，0 skipped，55.88 秒**。前一项包含在此总数中，不重复相加。开启原生测试标志；总数也包含既有执行器 stub 测试。
- `git diff --check` 通过。

纯翻面反例的实际 F32 网格闭合、正体积且 Manifold NoError，但一个面的几何法向翻转；现有对角线翻转现在能修复，而非快返拒绝。混合反例同时含坍缩面与远处翻面，两类均被处理。受限材料／预算反例仍被拒绝，真实 0.2 mm 间隙与空腔等既有回归通过。

## 冻结原反例：实际导出与文件读回

原始源码未修改。灯源码 SHA256 为 `286f51cb95bed9d3fe996369ba406378d13465299ce0feb6ee10e908eb70f068`，象鼻为 `8aa32c2db1b75f7ef9aa928eb39dfed06a162c2cec4211ab7fa929116ff50be6`。全部使用 `mm_per_unit=1`；表中缺陷数是最终采用的有限候选在局部修复前后的数值，不能解释为所有重试候选的合计。

| 冻结输入 | 坏面／翻面：前→后 | 局部操作 | Mesh64／ASCII STL 读回 | F32／GLB 读回 | 耗时 |
|---|---|---|---|---|---:|
| 原 53 实体灯臂 | 15→0／3→0 | 9 次收缩 | PASS／PASS | PASS／PASS | 18.35 秒 |
| 原象鼻 | 82→0／16→0 | 43 次收缩、6 次翻边 | PASS／PASS | PASS／PASS | 5.37 秒 |
| 原灯完整上部，含 connector | 22→0／4→0 | 13 次收缩 | PASS／PASS | PASS／PASS | 26.20 秒 |

灯臂坏面序列为 `15→14→12→10→8→6→4→3→1→0`，不会在零面积面归零、仍有翻面时提前停止。三者最终全部对应面最小法向点积分别为 **0.469629、0.025283、0.264273**，均严格大于零。

| 输入 | 局部编辑最大原顶点位移 mm | 转换总误差上界 mm | 固定毫米预算 |
|---|---:|---:|---:|
| 灯臂 | 1.48228e-7 | 1.63311e-6 | 2.75354e-5 |
| 象鼻 | 1.00277e-7 | 1.59987e-7 | 2.00272e-6 |
| 完整上部＋connector | 1.88663e-7 | 1.17456e-5 | 1.41525e-4 |

总误差上界包含既有有限简化、局部编辑和实际 F32 舍入；局部顶点位移不包含另外两项。制造灯臂与象鼻 ASCII STL 的字节 SHA 与上一轮证据完全一致。完整上部的 canonical STL 本轮另行验证；旧打印 STL 有打印平移，不作直接字节比较。

实际 STL 及恢复装配坐标后的 GLB 读回均检查：零面积、重复面、开放边、非流形边／顶点、方向不一致边为 **0**，一个材料实体、一个有向壳。GLB 材质拆分产生的共享位置重复顶点经过现有实体读取规则处理，不能将该数值误称为重复面。目标转换的 `file_validation=NOT_EVALUATED` 仅描述转换阶段；本轮分别执行的真实文件读回结果在独立记录中为 PASS。

完整上部是一个最终打印件的离线求值与导出，包含 connector；本轮没有执行完整整灯装配工作流。没有调用 Agent、后续 Topology／Overhang／Standing／FEA、渲染或恢复 benchmark。没有可靠的完整自交检测，继续报告 `self_intersection=NOT_EVALUATED`；正常法向与 Manifold NoError 不构成无自交证明。

## 证据与复现

[轻量结果](mesh_precision_flip_repair_20261007/fixture_results.json)、[输入及实现身份](mesh_precision_flip_repair_20261007/provenance.json)、[实际回归日志](mesh_precision_flip_repair_20261007/regression.log)。完整初始坏面／一环、GLB、ASCII STL、日志与离线重放脚本在：

`/jiigan-hp/lms/aDSL/experiment/mesh_precision_export_20261007/flip_search/`

上一份 [方向检查与毫米预算报告](mesh_validity_contract_20261007.md) 中的灯臂 GLB FAIL 是补搜索前的真实历史结果，本报告记录补齐搜索后实际通过的结果，没有改写旧证据。

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

"$PY" /jiigan-hp/lms/aDSL/experiment/mesh_precision_export_20261007/flip_search/replay.py \
  --repo /tmp/adsl_mesh_validity_v1_20261007 --output /tmp/adsl_flip_search_fresh
```

重放须使用新的输出目录；每个结果分别记录制造、转换和实际文件读回，源文件哈希在运行前后核对。
