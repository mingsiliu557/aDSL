# 制造／显示解耦与局部精度修复

日期：2026-10-07。分支：`codex/mesh-validity-v1`。实现提交：`bcb9673`，前一版报告提交：`eb57f65`；最初基于远端比较分支 `f78e846b36899b72273f4fb80bd64e99a613db1c`。

本文保留该提交的历史冻结结果。补齐全部对应面法向检查后，原灯臂的显示 PASS 被推翻，制造 STL 仍通过；当前结果及定位文字更正见[三项契约补齐报告](mesh_validity_contract_20261007.md)。

**原灯臂、象鼻及三操作数反例已经能输出有效 GLB 和 ASCII STL。整灯的两个制造 STL 有效，但完整上部的 float32 显示仍被明确拒绝，不能宣称全部显示问题已解决。** 本报告增补前一版的冻结记录，不改写旧失败结果。

## 实现范围

- `export_assembly.py`：`evaluated()` 直接保留 canonical Mesh64 制造网格；显示转换处理独立副本。ASCII STL、实际读回与 GLB 各自记录结果；GLB 失败不会丢弃制造文件。`manufacturing_status`、`display_status` 与完整 `export_status` 分开，visual_only 的装配几何状态仍为 `NOT_EVALUATED`。
- `local_precision_repair.py`、`mesh_validity.py`：只在实际 float32 退化面的邻域尝试 link-safe 边收缩或局部对角线翻转。在有效 float64 副本上编辑，再验证真实 float32；保留材料边界、空腔、连通结构、Euler 特征及固定的累计几何偏移预算。每次采用必须减少退化面；失败回滚。不增加容差、不任意删面、不跨壳焊接。收缩使用有限的端点／中点位置。
- `mesh_validity.validate_mesh()`：构造 Manifold 前用 NumPy float64 局部坐标居中，然后恢复变换；避免有效小实体位于大世界坐标时的体积抵消误差。世界三角面及材料由真实回归验证保持一致，未改体积公式。
- `asset_executor.py`、`execution.py`、`fixed_assembly.py`、`overhang_edit.py`、`assembly_topology.py`：允许明确制造成功而显示失败的资产继续保存；显示失败不冒充物理失败。失效 scene 文件不能被旧预览或视觉批准覆盖；最终发布只取所选版本绑定的文件。求值失败仍走既有源码证据与额度，无模型调用。
- `assembly_overhang.py` 的改动仅解除可选显示图与数值测量的耦合，并验证实际推荐 STL；没有修改评分、朝向搜索或物理判据。本轮后续验证已按用户要求停止，不再测试该计算链路。

收缩的拓扑条件参考 [CGAL Euler operations 官方说明](https://doc.cgal.org/latest/BGL/group__PkgBGLEulerOperations.html)。实现使用现有 NumPy／Manifold，没有引入 CGAL 或其它重建依赖。没有按案例名称、编号或路径写生产特例。

## 第一组：原反例与实际文件

原灯源码 SHA256：`286f51cb95bed9d3fe996369ba406378d13465299ce0feb6ee10e908eb70f068`；象鼻源码 SHA256：`8aa32c2db1b75f7ef9aa928eb39dfed06a162c2cec4211ab7fa929116ff50be6`。没有修改其半径、位置、分辨率或几何组合。

| 输入 | canonical 体积 mm³ | 实际 STL | 实际 GLB | 局部修复数量 | 几何偏移上界／固定预算 mm |
|---|---:|---|---|---:|---:|
| 原三操作数反例 | 4.341099575950211 | PASS | PASS | 3 | 6.033e-7／2.7527e-5 |
| 原 53 操作数灯臂 | 58.25702841476196 | PASS | PASS | 6 | 1.591e-6／2.7535e-5 |
| 原象鼻 | 0.4002794861268741 | PASS | PASS | 11 | 2.081e-7／2.0027e-6 |
| 整灯底座 | 1969.4814598698445 | PASS，2062 面 | PASS | — | — |
| 整灯完整上部 | 1023.7809129490752 | PASS，16076 面 | FAIL，拒绝发布 | 有界尝试失败 | 仍在原预算内，但合法性不通过 |

三个独立反例的表格及位移来自第一次持久 replay，最终原生回归再次验证其实际文件。整灯行来自最后一次真实 `execute_asset_source`：52.80 秒，冻结配置与源码不变；两个 STL 读回零面积面、开放边、非流形边／顶点、重复面均为 0。`manufacturing_status=PASS`，`display_status=FAIL`，完整 `export_status=FAIL`。原配置为 visual_only，`status=NOT_EVALUATED`，没有改写为完整装配认证。

完整上部的原始转换候选剩余：零面积面 2、开放边 0、重复面 2、非流形边 3、非流形顶点 4。1／2 ULP 候选剩余：零面积面 2、开放边 0、重复面 0、非流形边 2、非流形顶点 3、方向不一致边 1。定位证据是目标局部坐标 z 约 72.89–72.99 处高度约 1e-8 的小三角形在 float32 下变平；输入 float64 有效。

另外只做了一次离线、未接入生产的有限 ±1 ULP 邻位探索。原始分支虽把零面积面降到 0，仍出现重复面 2、非流形边 3，被共用验证拒绝。该结果说明不能把“零面积面消失”当成网格合法。`TARGET_PRECISION_UNREPRESENTABLE` 表示当前有界策略没有通过，不是不存在任何合法 float32 表达的数学证明。

## 第二组：18 例离线网格压力测试

固定 seed 为 20261007，同一组覆盖 12／4／4 体积 Boolean、嵌套与近共面运算、32／96／160 实体并集、非轴向圆滑链、大平移、非单位毫米尺度、不同材料、反向空腔、真实 0.2 mm 间隙及非法输入。只做实体求值、精度转换和实际 GLB／ASCII STL 读回。

- 第一次：17/18 符合预期，10.88 秒。发现大平移的体积查询偏差；网格及文件本身有效。
- 同一组修正后复核：**18/18 符合预期，10.44 秒**。14 例完整 GLB/STL 有效；1 例制造有效但细小材料边界的显示受限；1 例超小真实间隙正确拒绝显示转换；2 例非法开放／重复面输入正确拒绝。后四例不是成功制造／显示的正例。
- 大平移实例原体积 4.00002，旧世界坐标构造查询为 4.000013330718502；局部构造恢复平移后为 4.000020001300548。真实世界三角面哈希及材料保持一致。原压力结果未覆盖。

## 最终验证及复现

最终网格原生回归：**130 passed，0 failed，0 skipped，65.50 秒**。之前的 39 项定向验证已包含在其中，不重复相加。`git diff --check` 通过。

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
"$PY" reports/mesh_precision_export_20261007/replay.py --only lamp --output /fresh/lamp
"$PY" reports/mesh_precision_export_20261007/stress.py --output /fresh/stress
```

持久证据根目录：`/jiigan-hp/lms/aDSL/experiment/mesh_precision_export_20261007/`。

| 目录／文件 | 内容 |
|---|---|
| `fixtures/` | 三反例原文件、GLB/STL、读回诊断；此前已完成的 CPU 展示图片 |
| `lamp_final/` | 最终只做网格验证的真实整灯执行、manifest、两个 STL、独立读回及完整精度失败 |
| `stress/` | 第一次 17/18 结果、大平移反例及局部坐标探测 |
| `stress_final/` | 最终同 18 例结果、实际文件、源码／配置／实现 SHA、执行脚本副本 |
| `diagnosis/` | 完整上部 canonical NPZ、失败邻域、未采用邻位原型及其诊断 |
| `adsl_precision_mesh_final.log` | 最终 130 项原生回归日志 |

仓库中另有 [首次反例轻量结果](mesh_precision_export_20261007/first_group_results.json)、[网格 replay](mesh_precision_export_20261007/replay.py)、[压力 replay](mesh_precision_export_20261007/stress.py)。原完整几何模式及 topology 接线的旧记录保持历史身份，不代替本轮最终网格验证。

范围限制：没有可靠的完整三角自交检测，明确记录 `self_intersection=NOT_EVALUATED`；闭合、正体积或 Manifold NoError 不作为无自交证明。收到缩小范围的要求后已停止后续测试：当时已启动的 overhang 被终止，不计有效结果；standing 未启动。最终 replay 不调用这些工具。没有恢复 benchmark、调用真实模型 API、运行 FEA 或更改物理评分公式。未修改原工作区的未提交文件，结果保存在独立实现分支。
