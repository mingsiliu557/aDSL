# 全局打印件材料互穿检查

实施时间：2026-09-28 UTC；文件名沿用实施计划的 20260929 标识。
基线：`3d047c531ac4eb8e056b2386de2d70dbf7c6e810`。实现及确定性验证已完成。
SF16/SF10 定向复测已完成；随后 SF16 实际 Agent 修补在用户追加一次机会后通过。
所有工作仍在独立分支，按用户要求未合并 master。

## 提交与范围

| 阶段 | 提交 | 内容 |
|---|---|---|
| S1 | `25f818b` | 共用实体求交、局部负间隙豁免、导出器接线、几何夹具 |
| S2 | `7747e98` | pair 子进程、完整结果行、typed evidence、scope v2、旧缓存重测 |
| S3 | `533d22c` | Engineering 提示、真实几何驱动的源码修补测试、API 说明 |
| S4 | `da1e51c` | 真实 Blender 多接口导出及全部件对检查 |

实现修改仅涉及 `adsl-core/core/assembly_topology.py`、
`adsl-core/core/export/export_assembly.py`、`adsl-agents/assembly_topology.py`、
`adsl-agents/fixed_assembly.py`；说明为 `adsl-core/core/docs/fixed_assembly.md`。
另有四个定向测试文件、本报告与当前 memory。

- 检查最终打印实体在装配坐标中的全部无序部件对，包含直接连接及多接口的部件对。
- 复用既有 float32 数值容差原则；只扣除本部件对明确声明的负间隙名义 tab 区域。
  正间隙、面接触和 AABB 相交不直接构成实体互穿。
- `UNDECLARED_PART_INTERFERENCE` 提供双方、实际求交体积、阈值、装配坐标 AABB、
  直接及相关连接。AABB 中心不是接触点，边长不是穿透深度。
- 缺件、求交失败、超时和未执行均保留为 INDETERMINATE，不填伪造的零体积。
  其他有效部件对继续测量；既有外层预算和 120 秒单操作超时不变。
- scope v2 写入报告及结果 assumptions。旧范围缓存只触发 checker 重测，复用合格初始导出；
  既有 source/manifest/spec 身份校验保持。历史 PASS 不改写。
- 双方/区域/关联连接和体积阈值通过现有 typed findings 进入 Engineering/Coder。
  未知求交原因不会自动成为几何修补任务。其他选中 checker 仍参与汇总。

## 验证与证据

证据根目录：`temp/assembly_interference_20260929/`。
测试运行于隔离 checkout `/tmp/adsl_interference_20260929`；实际 core/agents 模块和子进程
均指向该 checkout。环境入口 `/tmp/adsl_interference_env_20260929.sh`；CPU，未调用真实模型。

```bash
# S1
python -m pytest -q -p no:cacheprovider tests/test_assembly_interference.py tests/test_assembly_topology.py -k 'interference or tab_slot_fit or non_axis'
# S2
python -m pytest -q -p no:cacheprovider tests/test_assembly_interference.py tests/test_assembly_topology.py
# S2 测试启动修正后
python -m pytest -q -p no:cacheprovider tests/test_assembly_topology.py -k 'outer_timeout or pair_timeout'
# S3（同时完整复测 topology）
python -m pytest -q -p no:cacheprovider tests/test_assembly_feedback_recovery.py tests/test_assembly_topology.py
# S4
ADSL_TEST_FIXED_REAL=1 python -m pytest -q -p no:cacheprovider tests/test_fixed_assembly_multi_mate.py -k 'real_four_part_cycle_geometry_and_topology or real_same_pair_two_interfaces_geometry_and_topology or real_four_part_cycle_visual_only_and_topology'
```

| 阶段 | 实际结果 | 日志 |
|---|---|---|
| S1 | 14 passed / 26 deselected | `s1.log` |
| S2 初次 | 46 passed / 1 failed；失败为既有进程清理测试的 SDK 冷导入耗时 | `s2.log` |
| S2 定向复测 | 2 passed / 34 deselected；测试直接装载实际 cleanup 函数，生产超时未改 | `s2_timeout_recheck.log` |
| S3 | 47 passed，包含完整 topology 与修补反馈检查 | `s3.log` |
| S4 | 3 passed / 40 deselected，无 skip | `s4.log` |

S1 包含接触、分离、容差上下、正间隙、同对双负间隙、接口外碰撞、旋转及非 1 比例，
并比较 exporter 共用 helper 和 checker 包装的数值/判定。碰撞夹具局部接口 PASS、全局 24 mm³ FAIL。
最初夹具肩部共面产生了零体积数值残片，扩大交集 AABB；测试将无关肩部退开 0.25 mm，
只隔离目标碰撞块。未调整生产阈值，也未改变真实案例输入。

S3 的语言模型、外观评价及第二个 checker 使用 mock；源码读取/修改工具、
Manifold 几何、STL、pair 子进程测量和反馈适配均使用真实代码。
候选源码修改后实际重新生成网格，由 FAIL 变 PASS，source hash 更新才被采用；
并非硬编码 `[FAIL, PASS]`。此测试的源程序生成使用 Manifold，Blender 导出由 S4 单独覆盖。

S4 实际 source、STL/GLB、manifest、topology 位于 `s4_exports/`：

| 夹具及目录前缀 | 模式 | part / interface / pair | 结果 |
|---|---|---|---|
| `test_real_four_part_cycle_geom0` | geometry | 4 / 4 / 6 | 全部 PASS |
| `test_real_same_pair_two_interf0` | geometry | 2 / 2 / 1 | 全部 PASS |
| `test_real_four_part_cycle_visu0` | visual_only | 4 / 4 / 6 | topology 全部 PASS |

geometry 模式验证了真实导出和冻结尺寸。visual_only 的 export_status=PASS、
geometry_validation=NOT_EVALUATED 保持原值；独立 topology 通过不改写该状态。

## SF16 / SF10 后台定向复测

2026-09-28 16:45:43 UTC 提交，运行代码提交 `da1e51cb2856efc9e4cf21b79090a00ed3144417`。
输入来自仓库根目录 `adsl_6cases_physics_materials_20260922.zip`，按需复制 source、plan、
原 manifest 到新输出目录；未改动压缩包或历史资产。

| 输入 | 原始 source SHA256 | 目标部件对 | 应有 pair 数 |
|---|---|---|---|
| SF16 | `88daba8f1cd247feadc3b732c3e8678e0ee9a09dfe966371a679245c40b655c6` | slanted_back_panel / top_shelf | 15 |
| SF10 | `4fcdfaff48828d7032ec96935a41dc3c7ea8af5308ef277da96b290fd753c2df` | tabletop_part / underframe_part | 1 |

两例均通过新代码实际重新导出 visual_only，再运行独立 topology，冻结原 mm_per_unit=1、
fit_offset_mm=0.2 和计划尺寸。每例 `provenance.json` 保存输入来源、代码提交及源码 hash；
`summary.json` 保存目标 pair 的体积、容差、区域、状态及所有非 PASS 项。

- 提交清单：`s5_jobs.json`；完成状态：`s5_SF16_completion.json`、`s5_SF10_completion.json`。
- 原始检测：`cases/SF16/original/`、`cases/SF10/original/`。
- 若 SF10 原始目标互穿成功复现，自动执行 `cases/SF10/manual_candidate/`：
  只把共享 `top_bearing` 的高度 32→31、中心 Z 685→684.5，上沿 701→700、下沿 669 不变；
  两处实例同步修改。随后重新导出并检查 part/interface/pair。
  这是**人工定向候选**，不是 Agent 自主修复，不能预先认定它通过。
- 复现脚本：证据目录下 `run_case.py`、`submit_cases.py`；运行日志 `s5_SF*_*.log`。
  脚本记录实际结果，不预填 FAIL/PASS；原始目标未成功复现时不执行盲目修改。

本报告的后台案例状态见下方交付快照；未完成项不计入通过。
未运行真实 LLM smoke 或新的 standing/FEA/overhang，不宣称已验证插入路径、保持力或重力行为。

## 交付快照（2026-09-28 16:49:28 UTC）

用户要求先检查两例再决定合并，当前仅保存在 `feat/assembly-material-interference`，未合并或推送 master。

| 案例 / 候选 | topology | part / interface / pair（PASS数 / 总数） | 目标重叠 mm³ | 容差 mm³ |
|---|---|---|---|---|
| SF16 / original | FAIL | 6/6 / 5/5 / 14/15 | 842681.3089759703 | 6251.241398513488 |
| SF10 / original | FAIL | 2/2 / 1/1 / 0/1 | 288000.0 | 4982.854187435704 |
| SF10 / manual_candidate | PASS | 2/2 / 1/1 / 1/1 | 0.0 | 4978.390991634923 |

- **SF16 / original**：source SHA256 `88daba8f1cd247feadc3b732c3e8678e0ee9a09dfe966371a679245c40b655c6`；目标 `slanted_back_panel:top_shelf`，状态 FAIL；装配毫米 AABB `[[-522.5, 180.3668524371798, 1632.0], [522.5, 205.0, 1668.0]]`。
  实际报告：`temp/assembly_interference_20260929/cases/SF16/original/verification/checkers/assembly_topology/report.json`。

- **SF10 / original**：source SHA256 `4fcdfaff48828d7032ec96935a41dc3c7ea8af5308ef277da96b290fd753c2df`；目标 `tabletop_part:underframe_part`，状态 FAIL；装配毫米 AABB `[[-580.0, -240.0, 700.0], [580.0, 240.0, 701.0]]`。
  实际报告：`temp/assembly_interference_20260929/cases/SF10/original/verification/checkers/assembly_topology/report.json`。

- **SF10 / manual_candidate**：source SHA256 `03e6d3f9582cf16951bf090570b88c67fac3e59a09ff4127b9a0995f119514bf`；目标 `tabletop_part:underframe_part`，状态 PASS；装配毫米 AABB `[[210.1999969482422, -90.19999694824219, 700.0], [272.7838581329621, 125.25894217015579, 700.0]]`。
  实际报告：`temp/assembly_interference_20260929/cases/SF10/manual_candidate/verification/checkers/assembly_topology/report.json`。

SF10 人工候选检查的是最终材料实体，接口本身也重新通过。零体积交集可能留下退化 AABB；不把该区域解释为正体积互穿。

## 用户追加：SF16 真实 Agent 修复（2026-09-28 16:59:17 UTC 提交）

用户追问自主修复能力后，另开 `temp/assembly_interference_20260929/cases/SF16/agent_repair/`，
使用原始 SF16 source/plan，经现有 `ObjectWorkflow.resume()` / `iterate_fixed_assembly()` 执行。
初始评价加最多一次源码修补（max_rounds=2），实际 CLIProxy/gpt-5.6-sol、CPU 八视图、
仅 topology；未提供预制补丁，未调用 Planner 重新生成模型。保留正常 Image/Code 评审和接受规则。

- 已确认现有代理直连预检 HTTP 200、runtime_config 创建、checker_specs 仅 assembly_topology。
  先前预检误走环境代理返回 502；修正任务脚本为生产同样的 localhost 直连后启动。
  该失败发生在创建工作流和模型调用之前，记录 `sf16_agent_preflight_failure.log`。
- 实际 system prompt、输入输出和工具消息存入 `actual_model_calls/`；既有 session/tool/usage
  记录保持，结束时备份 sessions。`completion.json` 记录各候选检查、源码 hash 和实际接受结果，
  `attempt_*.diff` 保存模型补丁。
- 后台清单 `sf16_agent_job.json`；日志 `sf16_agent.log`。按用户要求只确认启动，不持续监督。
  本节为提交记录，不代表实际模型已修复成功；以 completion 和新 topology 报告为准。
- 代码仍在独立分支，等待用户检查，未合并 master。

## 追加结果：真实 Agent 第二次修补与确定性网格处理

首次 SF16 修补已结束：斜背板因两个零面积面导出失败，topology INDETERMINATE。
用户明确追加一次修补后，Coder 使用上一轮 Code Critic 的具体反馈，将背板旋转从局部实体
改为接口帧表达；保留第一轮的局部让位修改。第二次候选外观及 topology 全部通过，
包含全部 6 件、5 接口、15 个部件对。未追加预算之外的修补。

最终接受源码 SHA256：`75ffc413c593e22da3dd1384cb1248f1ae162cb4eb609277b7cd1cc3d15ad4f0`。
记录：`temp/assembly_interference_20260929/cases/SF16/agent_repair/extra_repair_completion.json`；
补丁 `attempt_0002.diff`（相对第一次候选）。原运行记录在 `before_extra_repair/` 保留。

用户随后要求补齐网格处理缺口；`b103671` 新增受限的精确零长度边处理，不放宽数值界限。
用第一次失败源码原样做公共导出及 topology 验证，结果独立于 Agent 第二次建模修补；
详见 `reports/exact_zero_length_mesh_20260928.md`。该实现同样未合入 master。
