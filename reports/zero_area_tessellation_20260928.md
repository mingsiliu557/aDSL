# 零面积三角形的确定性处理（2026-09-28）

## 已完成的范围

SF13 左右侧板各 1 张零面积三角形均已通过确定性代码消除，原生成源码、接口和尺寸不变。
生产改动仅位于公共 GLB 求值/写出路径及固定装配诊断接线；另有针对性测试与文档。
没有修改 topology 判据、Agent 修补入口、模型参数或倒角尺寸，没有新模型调用。

`export_glb.py::_normalize_zero_area_tessellation()` 在完整 CSG 求值后、文件写出前，
检测叉积严格为零的三角形。仅对其所属、具有有效面积的平面多边形调用现有 Blender
BMesh 的局部 triangulate（quad BEAUTY / ngon EAR_CLIP）。这次侧板只需改变一个四边形
的内部对角线；没有删除面、合并或移动顶点，也没有按小面积阈值过滤有效细节。

在副本中处理，验证后才替换：所有目标零面积面消失、网格封闭、边/顶点流形、朝向一致、
连通分量数不变、顶点逐值相同、表面积和有符号体积在浮点算术误差内一致。
几何不满足上述条件时原对象不提交修改，报告 `EVALUATED_MESH_DEGENERATE`，包含对象、
坏三角数量、多边形 IDs 和原因；该错误保持求值原因未明，不自动宣布源设计错误。

完全共线的原三角形、不能安全重三角化的多边形、或同时不闭合的网格，不在本轮自动修复范围。
不会通过删除任意退化三角形或填洞制造通过。没有引入 CGAL 依赖或通用重网格化系统。

公共求值结果同时用于 GLB、STL、总装/拆分显示和后续 checker；每件 manifest 保存
`mesh_normalizations` 的发生位置、前后计数和验证结果。未发生处理的部件保持原诊断字段。

## 测试

以下相关回归 **156 passed / 123.62 s**，CPU，开启 ADSL_TEST_FIXED_REAL=1：

```text
tests/test_zero_area_tessellation.py
tests/test_boolean_solver.py
tests/test_fixed_assembly_visual_only.py
tests/test_fixed_assembly_empty_mesh.py
tests/test_fixed_assembly_exports.py
tests/test_assembly_topology.py
tests/test_fixed_assembly.py
tests/test_fixed_assembly_multi_mate.py
```

新增文件含 12 项原生 Blender 测试（其中装配集成使用人工网格注入，实际写出 GLB/STL，
并调用真实 topology），覆盖局部重三角化、材质保持、正反朝向、幂等、极小有效面保留、
开放/朝向错误/原生三角化错误时回滚、多个组件不合并、失败不误标设计问题，
以及 geometry / visual_only 两模式的实际文件一致性。其余集合包含 mock 流程和真实几何，
不将 156 全部称为独立真实建模案例。四件闭环、同对双接口、旧正常/倾斜 T 支架真实回归通过。

首次测试暴露人工夹具所选顶点顺序未触发零面积面，已改为确实重现坏对角线的顺序；
没有调整检测阈值让夹具过关。初始日志保留，最终结果以 tests_regression.log 为准。

## 同一份 SF13 源码的独立复查

原 source SHA256：`a4212de6738c3c598eb53286bcb10036ef26cad5c7eeb6fbd5d5c5a8e43eb6f2`。
直接执行该原件到新目录，仅导出和 topology；未重开 Agent、增加修补预算或运行 standing。
保留 8 个打印件和 24 个接口，visual_only 的几何状态仍为 NOT_EVALUATED。

| 指标 | 原候选 | 确定性处理后 |
| --- | ---: | ---: |
| 左侧板零面积三角形 | 1 | 0 |
| 右侧板零面积三角形 | 1 | 0 |
| 全部部件零面积三角形 | 2 | 0 |
| 部件 topology PASS | 4/8 | 6/8 |
| 接口配对 PASS | 0/24 | 16/24 |
| 第五层板开放边 | 12 | 12 |
| 顶盖开放边 | 36 | 36 |

两侧板各记录 1 次多边形重三角化。所有 8 件顶点集合逐值不变；表面积、有符号体积保持。
新 STL、各件 GLB、总装和拆分 GLB 的 64 条回归比较全部通过。
旧 manifest 所列产物哈希和原 source 哈希复核相同；原 Agent 的 retained/approved/结果不重写。
这是公共代码改动的离线收益，不是 Agent 自主修补的成功率。

整体 topology 仍为 INDETERMINATE：第五层板/顶盖的倒角裂缝及其 8 个依赖接口未解决。
standing 没有运行，不把静态 16 个接口通过表述为重力检测完成。

## 产物与参考

目录：`local_experiment/sf13_multi_mate_20260928T111227Z/zero_triangle_repair/`。

- `probe.json`：两侧板最小局部对照。
- `tests_regression.log`：完整相关回归日志。
- `recheck.py`、`recheck.log`、`result.json`：同源码重新导出与实际 topology。
- `output/assembly/`：新 manifest、STL、GLB 和总装/拆分资产。
- `topology/checkers/assembly_topology/report.json`：逐件、逐接口测量。
- `geometry_audit.json`：顶点/表面积/体积、旧件哈希复核、新显示/STL 一致性比较。

用户提供的方法方向与官方能力一致，但过滤退化面并不等于验证完整拓扑：
[Trimesh nondegenerate_faces](https://trimesh.org/trimesh.html#trimesh.Trimesh.nondegenerate_faces)
提供面筛选掩码且含高度参数；本轮不使用它的默认小面过滤。
[CGAL Polygon Mesh Processing](https://doc.cgal.org/latest/Polygon_mesh_processing/)
提供退化检测与修复能力；本例复用已安装 Blender 的局部重三角化即可。

本次主分支提交包含此实现、API 文档、回归测试、验证报告及 memory；
用户已有 FEA/standing 等工作区改动不纳入。实验大体积资产与 API 日志仅保留本机。
