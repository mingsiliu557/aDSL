# 04 Support Requirement & Critical-Surface-Aware Support

## Material Passport

- Material ID: `adsl-physics-04-support-20260904`
- Type: experiment execution and analysis report
- Origin Skill: `academic-research-suite/experiment-agent`
- Origin Mode: `run`
- Verification Status: ANALYZED
- Generated (UTC): 2026-09-04T12:29:51.701335+00:00

## 结论摘要

- 合成基准：PASSED；当前案例成功分析 14/14。
- PrusaSlicer 实际生成 support：14/14；至少一个 non-watertight collision part：4/14。
- 关键表面结果：PASS=4，VIOLATION=3，INDETERMINATE=7。
- `nominal contact` 同时覆盖 interface 承托的模型下表面，以及 support 从已有模型顶面起长的底部接触；它不是两个网格零距离相交。
- 单一合并 GLB 没有部件/三角面语义来源；有 support 时其关键表面结论 fail closed 为 INDETERMINATE。

## 当前案例

| Case | Status | support | overhang mm² | bridge mm | contact mm² | critical overlap mm² | critical verdict | mesh warning |
|---|---|---|---:|---:|---:|---:|---|---|
| A01-cabinet | ANALYZED | True | 33992.978 | 126506.978 | 8535.994 | 109.200 | VIOLATION | none |
| A02-faucet | ANALYZED | True | 14481.071 | 25118.215 | 2486.643 | 523.541 | VIOLATION | none |
| E01-chair-slat-edit | ANALYZED | True | 10677.773 | 44333.264 | 1130.082 | 0.000 | PASS | none |
| I01-example-image | ANALYZED | True | 18886.500 | 68699.647 | 2581.129 | 0.000 | PASS | none |
| I02-example-arti | ANALYZED | True | 61483.708 | 209589.414 | 22656.433 | 253.287 | VIOLATION | none |
| M01-base-motorcycle | ANALYZED | True | 12647.598 | 30048.254 | 3781.546 | 0.000 | INDETERMINATE | none |
| M01-cyberpunk-edit | ANALYZED | True | 13524.449 | 31021.631 | 3966.963 | 0.000 | INDETERMINATE | none |
| M01-cyberpunk-scratch | ANALYZED | True | 11394.121 | 29151.487 | 4000.466 | 0.000 | INDETERMINATE | open_parts=1; degenerate=2 |
| T01-main | ANALYZED | True | 9832.938 | 41116.107 | 1130.082 | 0.000 | PASS | none |
| T01-one-round-control | ANALYZED | True | 12603.600 | 53394.772 | 734.394 | 0.000 | PASS | none |
| T02-bookshelf | ANALYZED | True | 15606.140 | 8613.174 | 3335.573 | 1204.717 | INDETERMINATE | open_parts=1; degenerate=41 |
| T03-radial-wheel | ANALYZED | True | 9714.423 | 11742.817 | 1021.195 | 0.000 | INDETERMINATE | none |
| T04-hollow-mug | ANALYZED | True | 800.536 | 15300.799 | 297.291 | 0.000 | INDETERMINATE | open_parts=1; degenerate=0 |
| T05-patterned-desk | ANALYZED | True | 30764.532 | 57292.686 | 1985.782 | 0.000 | INDETERMINATE | open_parts=1; degenerate=0 |

## 接触区域定位

- A01-cabinet [VIOLATION]: `storage_cabinet/left_door_hinge/pull#collision_0`，64 faces，8.027 mm²，centroid=[92.695, 12.547, 48.945] mm。
- A01-cabinet [VIOLATION]: `storage_cabinet/right_door_hinge/pull#collision_0`，64 faces，8.027 mm²，centroid=[107.305, 12.547, 48.945] mm。
- A01-cabinet [VIOLATION]: `storage_cabinet/upper_drawer_slide/drawer_pull#collision_0`，24 faces，93.147 mm²，centroid=[99.829, 11.511, 143.318] mm。
- A02-faucet [VIOLATION]: `SinkFaucetAssembly/central_spout/aerator#collision_0`，25 faces，15.601 mm²，centroid=[74.054, 22.076, 111.402] mm。
- A02-faucet [VIOLATION]: `SinkFaucetAssembly/left_knob_rotation/grip_bar#collision_0`，22 faces，97.286 mm²，centroid=[24.281, 54.329, 94.906] mm。
- A02-faucet [VIOLATION]: `SinkFaucetAssembly/left_knob_rotation/grip_end#collision_0`，159 faces，62.670 mm²，centroid=[23.567, 40.332, 93.603] mm。
- A02-faucet [VIOLATION]: `SinkFaucetAssembly/right_knob_rotation/grip_bar#collision_0`，22 faces，95.731 mm²，centroid=[123.628, 55.198, 94.893] mm。
- A02-faucet [VIOLATION]: `SinkFaucetAssembly/right_knob_rotation/grip_end#collision_0`，157 faces，63.057 mm²，centroid=[124.268, 40.311, 93.596] mm。
- A02-faucet [VIOLATION]: `SinkFaucetAssembly/central_lever_hinge/lever_grip#collision_0`，2 faces，129.844 mm²，centroid=[73.814, 50.45, 166.545] mm。
- A02-faucet [VIOLATION]: `SinkFaucetAssembly/central_lever_hinge/grip_tip#collision_0`，151 faces，59.353 mm²，centroid=[73.92, 31.838, 164.727] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/top_drawer_slide/pull/bar#collision_0`，32 faces，60.763 mm²，centroid=[94.009, 11.441, 148.601] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/top_drawer_slide/pull/left_post#collision_0`，25 faces，8.841 mm²，centroid=[77.299, 14.354, 148.592] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/top_drawer_slide/pull/right_post#collision_0`，25 faces，8.871 mm²，centroid=[110.43, 14.336, 148.59] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/middle_drawer_slide/pull/bar#collision_0`，32 faces，70.725 mm²，centroid=[93.768, 11.431, 109.068] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/middle_drawer_slide/pull/left_post#collision_0`，27 faces，9.320 mm²，centroid=[77.264, 14.274, 109.077] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/middle_drawer_slide/pull/right_post#collision_0`，27 faces，9.348 mm²，centroid=[110.52, 14.298, 109.077] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/bottom_drawer_slide/pull/bar#collision_0`，32 faces，69.474 mm²，centroid=[93.876, 11.41, 69.522] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/bottom_drawer_slide/pull/left_post#collision_0`，24 faces，7.983 mm²，centroid=[77.244, 14.313, 69.508] mm。
- I02-example-arti [VIOLATION]: `ThreeDrawerMidCenturyNightstand/bottom_drawer_slide/pull/right_post#collision_0`，24 faces，7.962 mm²，centroid=[110.5, 14.335, 69.508] mm。
- T02-bookshelf [INDETERMINATE]: `FreestandingFourShelfBookshelf#collision_0`，7 faces，1204.717 mm²，centroid=[59.294, 27.31, 56.255] mm。

完整 global/local face IDs 与 bounds 位于 results.json；surface_classes.ply 提供逐三角面颜色证据。


## 方法与判据

- authored URDF Z-up、initial joint state；第 03 项语义实物尺度只作元数据，实际 FDM 网格等比限制为最长边 180 mm。
- 0.4 mm nozzle、0.2 mm layer、PLA、45°（从水平面计）overhang threshold、everywhere rectilinear support、3 层 interface、0.2 mm top Z gap。
- 几何 overhang 是高于首层且法向达到阈值的向下三角面；bridge 与 support 结论来自实际 G-code feature role。
- 关键面用 collision regex + face selector；有效名义接触重叠 >0.01 mm² 判 VIOLATION。面积和三角面覆盖图保留为证据。
- 橙=overhang、蓝=critical、红=contact、紫=critical/contact overlap。

## 证据边界

- 切片器会隐式 repair STL；原始模型的 open/non-manifold/断开体问题单独记录，不能因切片成功而视为模型正确。
- bridge 成功与否依赖材料、冷却、速度和校准；本报告只证明切片器将路径标为 bridge，不证明实物不会下垂。
- support 接触痕迹仍受温度、Z gap、界面密度和拆除操作影响；本报告不是打印认证。

## 复现

- Input: `/jiigan-hp/lms/aDSL/experiment/audit_20260830`
- Output: `/jiigan-hp/lms/aDSL/experiment/physics_analysis/04_support_requirement_critical_surfaces`
- Slicer: `PrusaSlicer-2.4.0+UNKNOWN based on Slic3r (with GUI support)`
- Profile: `/vepfs_default/chanxueyan/lhp/lms/aDSL/experiments/support_requirement_critical_surfaces/fff_profile.ini`
- Command: `experiments/support_requirement_critical_surfaces/analyze.py --output-dir /jiigan-hp/lms/aDSL/experiment/physics_analysis/04_support_requirement_critical_surfaces`
- 全部 CPU 执行，无需 GPU；原始模型哈希前后必须一致。
