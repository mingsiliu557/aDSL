# 通用网格鲁棒性 v1：实现与离线验证

日期：2026-10-07。实现分支：`codex/mesh-validity-v1`。

统一 Mesh64 求值、目标精度验证和求值失败反馈已实现。嵌套 Boolean 不再经过 Blender float32 中间网格；正常夹具能够导出并通过实际文件和 checker 检查。**原始 53 操作数灯臂仍不能完整导出：内部实体有效，但局部 float32 转换仍退化，已明确拒绝。象鼻原始夹具也遇到这个输出限制。本轮不能宣称原问题已完整解决。**

## 基线、环境与修改状态

- 开始时最新远端 `feat/benchmark-six-method-comparison` 是 `f78e846b36899b72273f4fb80bd64e99a613db1c`，比任务引用的 `3675606` 更新；从它创建独立 worktree。
- 实施目录：`/tmp/adsl_mesh_validity_v1_20261007`。原项目目录及其中未提交的配置、FEA 修改未覆盖。
- 验证对应实现提交：`2c3b75f6899be47887a5abeed8e341ea8c7630da`。此前共用验证模块提交：`1d2ab39`、`8e2cdd4`、`7ffe5d2`；本报告与轻量证据作为后续文档提交保存。
- Python：`/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python`，使用现有 NumPy、Blender/bpy、Manifold 3.5.2、Trimesh。没有增加重建依赖。
- 导入 overlay：`/tmp/adsl_mesh_validity_v1_imports/adsl/{core,agents,tools}` 分别指向本 worktree。测试前已核对模块实际路径。
- 没有调用真实 Planner/Coder/Engineering API，没有启动 FEA，没有恢复暂停的 benchmark，没有修改 baseline、输入或物理评分公式。

## 实际实现

| 文件／入口 | 行为 |
|---|---|
| `adsl-core/core/export/mesh64.py`：`MeshPiece`、`Evaluation`、`evaluate_shape`、`_operate` | 递归求值 UNION、DIFFERENCE、INTERSECT、hull。实体、float64 变换、节点路径、材料注册表与记录一同传递。外层直接读取内层 Manifold。 |
| 同文件：`_unit_mesh`、`_leaf`、`_nearby_frame` | 球／圆柱／立方体只在原点、单位尺寸下使用原 Blender tessellation；尺寸、中心和后续变换在 float64 处理。公共世界平移延后，避免先加到局部中心再减回的精度损失。记录叶节点输入仍来自 float32 采样。 |
| `mesh_validity.py`：`mesh_metrics`、`validate_mesh`、`checked_solid` | 共用有限性、索引、零面积面、重复面、开放边、非流形边／顶点、方向、实体状态及材料连通分量检查；反向空腔壳保留在同一个有向实体中。 |
| 同文件：`normalize_blender_input` | 复用受限零面积重三角化与微裂缝处理，在副本上处理、验证后提交；失败不污染原对象。没有全局补洞、最大分量保留或跨打印件焊接。 |
| 同文件：`target_mesh` | 局部居中后进行实际 float32 roundtrip。固定尝试原网格及 1／2 ULP 的既有 Manifold 简化，验证连通、边界、体积和位移预算。不同材料保留面来源；仅单一实际材质可重置无视觉意义的 CSG ancestry，再恢复材质 ID。失败不删问题面，不扩大预算直到通过。 |
| `export_glb.py`：`export_glb`、`_export_evaluation`、`_patch_glb_transforms` | Blender 实例化仅位于最终显示／文件边界。局部网格和配对变换一起写出；GLB JSON 节点矩阵由 NumPy float64 数据恢复，避免 Blender matrix 存储提前舍入世界平移。独立对象及 joint 层级保留。 |
| `export_assembly.py`：`evaluated`、`_write_mesh_glb`、`_verify_written_exports` | `evaluated()` 直接消费统一求值结果，保留原三元返回形式。只合并当前声明打印件内部主体，不把不同打印件自动 UNION。canonical 网格派生 STL／GLB；实际写出文件分别读回验证。 |
| `adsl-agents/fixed_assembly.py`：`_failure_feedback` 及迭代分支 | 明确输入／Boolean／目标精度失败以增量诊断进入既有修补预算；文件、环境、陈旧 source hash 或未知错误不自动授权几何修改。提前停止判断与实际工程调用一并接通。 |
| `adsl-agents/assembly_topology.py`：`evaluation_evidence_run` 及 Engineering 适配 | 创建求值证据专用结果，保留失败阶段；核对当前 AST、source index、finding/source IDs，传递源码候选。物理 checker 不可测时仍为证据不足，不伪造 topology FAIL。 |

`export_glb()` 调用方式及 `evaluated()` 返回形式兼容。旧低层 Blender Boolean helper 保留给已有受限恢复和回归，但公共求值路径不调用它们。合法中间空集可继续参与操作；最终必需打印件为空时抛出明确错误。

## 测试与真实执行

以下均使用该 worktree 的包，`PYTHONDONTWRITEBYTECODE=1`，pytest 禁用 cache。表中通过数量包含**预期拒绝的负例**，不意味着灯臂或象鼻输出成功。全部所列运行均无 skip；`deselected` 是指定测试筛选产生的未选项。

| 阶段／实际命令范围 | 结果 | 时间 |
|---|---:|---:|
| `test_mesh_validity`、`test_mesh64_evaluation`、`test_mesh64_exports`、Boolean recovery/solver、零面积、微裂缝、fixed assembly exports/visual_only/empty，真实开关开启 | 132 passed | 25.97 s |
| `test_fixed_assembly_multi_mate.py -k real_`，`ADSL_TEST_FIXED_REAL=1` | 5 passed，38 deselected | 123.46 s |
| `test_mesh_evaluation_feedback`、`test_assembly_feedback_recovery`、`test_fixed_assembly_recovery` | 53 passed | 36.01 s |
| `test_constructive_geometry`、`test_loft_geometry`、`test_geometry_demo`、`test_prompts`，`ADSL_TEST_GEOMETRY_REAL=1` | 67 passed | 40.73 s |
| 为持久保存实际文件，重跑 `test_mesh64_exports` 及 multi-mate 单元／两项真实导出夹具 | 47 passed，3 deselected | 73.67 s |

完整命令及原始日志保存在 [本轮证据目录](general_mesh_robustness_v1_20261007/README.md)。反馈模型均为 fake runtime／stub，检测、源码工具和候选路由使用实际代码。API suite 的真实执行器及 CPU renderer 生成六环绕＋俯视＋仰视八张图片。

验证覆盖：12／4／4 体积的 UNION／DIFFERENCE／INTERSECT；嵌套运算禁止调用旧 Blender Boolean hook；多对象 base 共享 cutters；中间空集；独立对象／打印件；joint；镜像时外／内壳方向；材料；零面积与微裂缝原回归；真实窄间隙负例。

## 原灯臂：修改前后与剩余失败

原源码固定为 `reports/benchmark_six_boolean_failure_20261007/source.py`，SHA256：

`286f51cb95bed9d3fe996369ba406378d13465299ce0feb6ee10e908eb70f068`

53 个原始实体保留，未改半径、位置、分辨率或组合几何。修改前证据引用仓库已有冻结诊断 `reports/benchmark_six_boolean_failure_20261007/summary.json`，**本轮没有把旧实现重新跑一遍**；其历史生产提交为 `521fe081`，与本轮起始分支 SHA 区分。

| 阶段 | 修改前已存档结果 | 本轮真实结果 |
|---|---|---|
| Boolean 内部／中间结果 | Blender EXACT 输出：2 零面积面、453 开放边、242 非流形边、14 重复面；恢复中的 Mesh64 单独有效，但写回失败 | 递归 Mesh64：13,760 三角面；零面积／开放边／非流形边均为 0，体积 58.257028414762 scene units³，0.480 s |
| 局部 float32 转换 | 原世界坐标转换：103 零面积面；精确坐标合并后仍有 18 开放边、27 非流形边；有界恢复失败 | 初次转换：128 零面积面、0 开放边、104 非流形边；1／2 ULP 尝试仍各有 12 零面积面、0 开放边、12 非流形边。0.877 s；明确 `TARGET_PRECISION_UNREPRESENTABLE` |
| 原完整上部件 | 在灯臂中断，未完成 | 内部 16,076 三角面有效，体积 1023.780912949075，0.779 s；转换最优尝试仍有 74 零面积面、60 非流形边，拒绝 |
| 生产 GLB／STL | 未完整导出 | **上部 GLB／STL 未发布**；底座实际文件可读。完整导出 `export_status=FAIL`，耗时 6.034 s。visual_only 的制造状态仍为 `NOT_EVALUATED` |
| 独立诊断 ASCII STL | 不用来表示生产成功 | 从内部 Mesh64 单独写出的诊断 STL 实际读回 PASS，part checker PASS，缺陷计数为 0；这些是**诊断文件，不是已接受的打印候选** |
| 实际 topology | 旧流程求值失败未得到完整检查 | 总体 INDETERMINATE；底座 PASS，上部 `PRINT_MESH_UNAVAILABLE`，接口与 pair `DEPENDENCY_MESH_UNAVAILABLE`；没有伪造 FAIL |
| 修补反馈 | 旧错误归 unknown，未使用剩余修补预算 | 明确 target_precision 求值证据、当前源码候选和已有预算入口；真实模型修补未运行 |

旧／新内部体积存在约 0.000121 scene units³ 的差异：旧恢复叶节点先在实际坐标中被 Blender float32 量化；本轮只保留单位 tessellation 的采样精度，尺寸／中心改在 float64 中实现。原源码及主要尺寸未修改，不能将两者误称为完全相同数值输入。

三个位置对照使用同一几何：原点附近、原故障位置、平移 `(1e7, 2e7, -3e7)`。内部体积与局部缺陷指标一致；转换失败指标相同，位移预算均为 **0.0000275353995676 mm**。整体平移不会放大修复允许误差。较大平移的诊断 ASCII STL 也独立读回及测量；GLB 精确节点矩阵仍不等于下游 GPU 采用 float64 渲染。

剩余失败不是当前这些副本中的开放边：float32 使近邻交点／细小三角形坍缩，产生零面积及非流形。统一高精度路径消除了中间反复转换，但没有保证任何最终曲面均可无损表示为 float32。

### 小型反例

按原始顺序，首次失败前缀有 29 个操作数。一次保留原几何的贪心删减得到 **3 个原操作数 `op_2`、`op_26`、`op_28`**：内部 1,350 面、体积 4.341099575950211、缺陷为 0；局部转换初次有 7 零面积面／6 非流形边，两个有界尝试仍有 2／2。没有证明这是全局最小子集。

可重现数据、脚本和真实结果：`general_mesh_robustness_v1_20261007/minimal_operands.json`、`minimal_repro.py`、`logs/minimal_repro.json`。生产实现没有该案例、名字或路径特例。

## 象鼻与兼容风险

原 `tests/fixtures/elephant_trunk.py` 不变，SHA256 `8aa32c2db1b75f7ef9aa928eb39dfed06a162c2cec4211ab7fa929116ff50be6`。内部 7,818 面、体积 0.4002794861268741、缺陷为 0，0.355 s；float32 原始转换有 66 零面积面、62 非流形边，两个有界尝试仍分别 22／22、20／21，0.443 s；诊断 ASCII STL 读回和 part checker PASS。

**这是输出兼容性风险：旧恢复曾能从其提前量化的几何导出象鼻，本轮固定原参数的高精度实体在目标转换阶段被拒绝。** 回归测试明确检查“内部有效、输出拒绝、诊断可追溯”，不能把该测试 passed 解释成象鼻 GLB 成功，也没有替换已归档图片。

## 材料、位置、空腔和接口证据

- 材料来源经 Manifold face IDs 和纯数据注册表传递，实际 GLB 保留红／蓝材质。三个独立对象、joint 的节点 extras／初始变换读回存在；没有保存 Blender reset 后失效的 datablock。
- `(4³ - 2³)` 空腔在 `mm_per_unit=2.5` 时体积 **875 mm³**，两层有向壳对应一个材料实体。GLB、ASCII STL、checker 实体一致，没有把空腔内壳正体积化再 UNION。负行列式变换新增回归保留外正／内负绕序。
- 嵌套夹具在三个位置实际 GLB 读回体积 **4 scene units³**，装配 bounds 符合变换；两个重合的独立打印声明仍保留两个对象。
- 四件四接口真实 geometry 夹具：4 个 part、4 个 interface、6 个 pair 全部 PASS。同一对部件双接口：2 个 part、2 个 interface、1 个 pair 全部 PASS。visual_only 多接口及原 T-bracket/tilted 回归也实际运行。
- 受限微裂缝回归包含 **0.2 mm 的真实榫槽间隙**，仍保持间隙，不自动焊接；真实缺面负例仍拒绝。

## 反馈链路证据

原灯臂实际求值 finding：

`assembly_mesh_evaluation:0:upper_structure:TARGET_PRECISION_UNREPRESENTABLE`

源码、manifest、source index 与证据使用同一当前 source SHA。候选通过 AST 核对：`source:2ff80837c231a11b40a1` → 第 58 行的 `continuous_rounded_arm`，`source:a568e3657f71be94669d` → 第 69 行的 `connected_upper_components`。候选标明 `ambiguous=true`，只是已验证的操作／源码上下文，不能宣称唯一致错代码行。

fake runtime 流程保存了 Engineering 输入、源码读取、结构化 proposal、四次成功文件工具操作、Coder 的 CHANGED、候选重测与采用。原始物理证据不足没有被修改。另有 file/environment、过期 hash、非法 source/finding、NO_CHANGE、NO_PROPOSAL 和预算终止负例；未发送模型请求。

## 验证边界与后续最小研究方向

所有结果的三角自交项均为 **NOT_EVALUATED**。当前依赖没有实际执行可靠、完整的三角自交检测；Manifold NoError、闭合、有向或正体积都不被用来证明无自交。原始单位 tessellator 也未改为完全高精度采样。

本轮按要求停止在实现和离线验证。下一步可直接研究三操作数反例的**最终精度转换**，比较局部有向网格重三角化是否能在固定误差预算、材料和间隙约束内保持拓扑；任何方案仍须通过实际 GLB／STL 读回。尚未实现新算法，未引入全局重建或放宽阈值。

## 产物与复现

完整持久证据：

`/jiigan-hp/lms/aDSL/experiment/general_mesh_robustness_v1_20261007/release_evidence/`

其中 `lamp/` 保存未改源码、内部 NPZ、诊断 STL、部分实际资产、manifest、source index、topology 与求值 finding；`elephant_trunk/` 保存同类诊断；`minimal_prefix/` 保存前缀及删减结果；`positive_checks/` 保存成功导出、两类多接口夹具、八视图和 fake 修补证据。各阶段状态独立查看，不把旧探索目录当作最新结果。

Git 中的轻量证据、命令与日志见 [README](general_mesh_robustness_v1_20261007/README.md)、[汇总 JSON](general_mesh_robustness_v1_20261007/evidence_summary.json)。大模型／网格与图片留在数据盘。本轮没有推送或合并到 master。
