# SF13 数值微裂缝局部焊接（2026-09-28）

发布补记：本实现现与 rigid_flex 后端一同纳入 master。下文为最初几何验证记录；
后续 SF13 站立验证已完成并按新判据 PASS，见
[联合发布记录](contact_mesh_publication_20260928.md)。历史产物状态不改写。

已接入公共固定装配导出路径，原始 SF13 source、connector 参数和历史产物未改。
使用同一候选重新导出后，8 件、24 接口均通过现有 topology；没有运行 Agent 或 standing。

## 实现范围

- `adsl-core/core/export/export_glb.py`：既有零面积处理之后，以独立 BMesh 副本检测
  三边边界环；逐环调用 find_doubles，再筛选 targetmap，最后 weld_verts。
- 候选须同时满足：每个本地坐标差不超过两个 float32 ULP、位移不超过该环最长边
  的 1e-4、物理位移不超过 **1e-4 mm**。后者包含世界变换及其浮点舍入影响。
  阈值由代码固定，不读取 fit_offset_mm，不向 Agent 增加调参入口。
- 一个目标环只接受一对近邻点，且不折叠真实三角面；不跨环、跨网格对象、跨打印件寻找候选。
- 提交前验证闭合、边/顶点流形、绕序、分量数、所有原多边形及材质、未参与点的位置、
  零面积面、重复点、包围盒、表面积和有符号体积变化。失败丢弃副本，原网格继续进入
  原有导出/checker 反馈，记录 REJECTED；不匹配的开口记录 SKIPPED。
- `export_assembly.evaluated` 传递实际 mm_per_unit，STL、GLB、显示和 checker 共用处理后的
  同一网格。manifest 的 mesh_normalizations 记录次数、前后开放边和毫米位移。
  不带实际单位的普通 export_glb 调用保持原行为。
- 新增 `tests/test_microcrack_welding.py`；更新一个受新 keyword 参数影响的 export mock。
  没有修改 topology 判据、物理后端、Agent 修复预算或模型设计。

## SF13 真实结果

基线远程提交 `377dc44f5dd723cefdf42f46f8691d1e063f7e3d` 加本次工作区改动。
Run ID：`sf13_multi_mate_20260928T111227Z`。
候选 SHA256：`a4212de6738c3c598eb53286bcb10036ef26cad5c7eeb6fbd5d5c5a8e43eb6f2`。

| 打印件 | 开放边（原始→处理后） | 合并顶点数 | 最大顶点位移 mm |
| --- | ---: | ---: | ---: |
| cap_top | 36 → 0 | 12 | 0.00000762939453125 |
| shelf_level_5 | 12 → 0 | 4 | 0.000003814697265625 |

两者独立验证通过闭合性、流形性、绕序及单连通体检查；没有沿用顶盖推断替代第五层板验证。
8 件全部无零面积三角形；原有两侧板继续由既有重三角化处理。
24 个 connection ID 的接口几何测量全部 PASS，8 个打印件全部 PASS。

本次使用实际工作流的 visual_only 导出：export_status=PASS，geometry status 仍为
NOT_EVALUATED；**独立 topology=PASS**。另执行 64 项实际 STL、GLB、总装/爆炸显示的
三角形与姿态比较，全部通过，未将 visual_only 状态改成 geometry PASS。
逐件旧 STL 到新 STL 的最近顶点距离与上述焊接位移一致。
最终实现再次求值这两件，其三角形集合与已检查的 STL 完全相同。

## 回归与产物

CPU 原生及相关回归 **139 passed**，包括焊接成功、不同单位、超范围开口保持原样、
原生操作失败/残留其它孔/绕序错误/非目标点移动时回滚、邻近壳与对象隔离、
两种导出模式的 STL/GLB/checker 接线、既有零面积处理和真实多接口/T 支架。

```bash
ADSL_TEST_FIXED_REAL=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python -m pytest -q \
  tests/test_microcrack_welding.py tests/test_zero_area_tessellation.py \
  tests/test_fixed_assembly_empty_mesh.py tests/test_fixed_assembly_diagnostics.py \
  tests/test_fixed_assembly.py tests/test_fixed_assembly_multi_mate.py tests/test_mesh_failure_feedback.py
```

产物目录：
`local_experiment/sf13_multi_mate_20260928T111227Z/microcrack_repair_20260928/`

- `output/assembly/`：新 source 对应 manifest、各件 STL/GLB、总装和爆炸 GLB。
- `topology/checkers/assembly_topology/report.json`：32 项原始测量。
- `result.json`、`geometry_audit.json`：部件、接口、开放边、位移和 64 项导出比较。
- `recheck.py/.log`、`audit.py/.log`、`tests_regression.log`、`implementation_sha256.json`。
- 首次辅助 audit 对未去重的 STL 三角形编码直接数边，断言失败；已按既有 checker 的
  face-group 内精确坐标去重重做并通过。`audit_initial.log` 保留；此修正未改变产品代码或判据。

本次处理有意只覆盖满足上述限制的数值边界形态，不是通用补洞。裂缝与 lead-in/Boolean
求值路径的关系来自已有诊断；这里证明局部焊接修复及其几何不变量，不声称定位了内核舍入指令。
standing 动态覆盖仍未验证；未启动 FEA、overhang、新 Agent 或新实验批次。

## 官方 API 依据

Blender [find_doubles](https://docs.blender.org/api/5.3/bmesh.ops.html#bmesh.ops.find_doubles)
给出候选映射，[weld_verts](https://docs.blender.org/api/5.3/bmesh.ops.html#bmesh.ops.weld_verts)
执行映射。本机 Blender 4.0.0 的原生接口签名也已核对；实现仅使用该版本支持的
verts/dist 和 targetmap。数值裂缝识别、阈值、回滚及验收均由本项目代码限定。
