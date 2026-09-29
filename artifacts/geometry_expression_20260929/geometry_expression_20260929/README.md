# 通用形状表达增强：实现与三例 A/B 展示

日期：2026-09-29。状态：实现、定向测试与六次真实初始生成均完成。

本轮新增 Polygon、linear_extrude、rotate_extrude、hull，接通公开 API、Blender GLB、bounds/support、源码索引及 Planner/Coder 文档。展示在八视图渲染处结束，未调用 Image/Code/Engineering、Topology、Standing、Overhang、FEA，也未使用 FixedAssembly、connector 或 URDF。

## 版本和修改

- A：增强前本项目 `master@8943a8306ddcce69ad0c043f0079c0a14f0518f1`。
- B：分支 `feat/constructive-geometry`，实际生成代码固定在 `5ed4209`。后续提交只补交付说明。
- `4362537`：轮廓验证、拉伸/旋转 mesh、hull 构造节点、geometry extra、公开接口与 mesh bounds/support。
- `3700dbc`：Blender mesh/hull 求值、临时操作数清理、原生互操作与渲染 smoke。
- `5ed4209`：DSL 文档、采样例子、Planner/Coder 指导、源码索引、展示脚本、固定案例和调用边界测试。
- 工作树：`/tmp/adsl_geometry_20260929`。实现已分阶段提交，未合并/推送本次功能。原主工作区的无关修改未动。

具体文件：`adsl-core/core/constructive.py`、`core/__init__.py`、`core/bounds.py`、`core/export/export_glb.py`、`adsl-core/pyproject.toml`、两份 DSL 文档、`adsl-agents/source_index.py`、`prompt/coder.md`、`prompt/planner.md`、`experiments/geometry_expression/`、`tests/test_constructive_geometry.py`、`tests/test_geometry_demo.py`。

兼容性：旧 API 未改签名；新原生依赖延迟加载，可选 extra 为 `adsl-core[geometry]`（Manifold 3.5.2）。Polygon 不是 Asset，需先拉伸。新网格使用不可变 float64 数值元组；Blender 按自身精度存储。部分旋转将完整圆周分段数换算为弧段数，至少 3 段，避免触发底层自动分段。Hull 复制输入、导出时求凸包；父变换仅应用一次。新几何没有接入旧 URDF 导出器。Boolean 的既有近似 bounds 限制仍适用。

## 定向验证

环境使用已有 Python 3.10、Manifold 3.5.2、Blender bpy 4.0。运行前核对模块路径指向实施 checkout。所有原生验证用 CPU。

| 阶段 | 命令（仓库根目录） | 结果 |
|---|---|---|
| A | `python -m pytest -q -p no:cacheprovider tests/test_constructive_geometry.py` | 22 passed，0 skipped |
| B | `ADSL_TEST_GEOMETRY_REAL=1 python -m pytest -q -p no:cacheprovider tests/test_constructive_geometry.py tests/test_boolean_solver.py` | 30 passed，0 skipped；含 A 的 22 项 |
| C | `python -m pytest -q -p no:cacheprovider tests/test_geometry_demo.py tests/test_prompts.py` | 8 passed，0 skipped |

共 38 个不同测试。A 验证凹轮廓/孔洞体积、绕序和错误输入、变换/副本/support、整周和局部旋转的离散体积。B 实际求值 mesh−Cylinder、包含 Boolean 操作数的 hull、整体变换、材质、层级和布局，GLB 读回有真实 mesh，并生成 8 张中性视图。旧 Boolean 定向测试通过。C 使用 stub 验证仅 Planner/Coder、单次源码错误 patch、基础设施/渲染错误不修改几何；真实解析 prompt 和四个源码索引调用名。

初次 A smoke 发现原生 revolve 的分段参数作用于当前弧段，已在封装层适配后通过。初次 B smoke 的测试夹具误用了 Cylinder 位置参数，改为既有公开关键字签名后通过。二者不是 Agent 样本重抽。最终日志为 `stage_a.log`、`stage_b.log`、`stage_c.log`。

## 展示协议与来源

证据根目录：`/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929`。

三个历史输入取自仓库实际案例清单，固定文本保存在 `experiments/geometry_expression/cases.json`；对应旧源码/GLB 的存在与 hash 另存 `historical_provenance.json`。历史模型不作为生成输入。

- SF06：ABO `B07DBHCKJ5`，MARVEL 描述，圆润扶手椅。
- SF21：ShapeNet `7b39100755e9578799284d844aba7576`，CAP3D 描述，扭转灯杆和三只曲脚落地灯。
- T02-bookshelf：本项目 2026-08-30 本地 audit 的四层曲边书架需求。

这些是 aDSL 历史运行案例，不宣称为原论文公开测试编号。A 是本项目增强前 master，不是未修改的官方上游发行版。

两组使用相同 CLIProxy `gpt-5.6-sol` profile、原始文本，无参考图。每组每例 1 次初始生成，最多 1 次真正执行错误修补。共 6 次初始生成、1 次源码 patch；记录到的模型 requests 共 21（包括工具往返），没有额外视觉修补或择优抽样。两个 arm 各自按案例顺序运行，运行期间未改动实现和提示。

两组均为 CPU Cycles、4 线程、512×512、32 samples、neutral、gray、review_eight（6 环绕 + 俯仰），执行和渲染各 300 秒。沿用同一相机归一化；GLB 尺寸记录为 glTF Y-up 的 authored scene units，输入没有统一指定 mm，不能当作毫米结果。色彩在 GLB 中保留，中性图用于看形状。

## 实际结果

| 案例 | 组 | 初始/修补 | 最终源码行数 | 三角面 | 执行合计 s | 渲染 s | 状态 |
|---|---|---|---:|---:|---:|---:|---|
| SF06 | A | 1 / 1 | 274 | 25,326 | 8.15 | 20.18 | rendered，8 图 |
| SF06 | B | 1 / 0 | 155 | 21,132 | 5.79 | 18.12 | rendered，8 图 |
| SF21 | A | 1 / 0 | 276 | 15,936 | 5.77 | 18.66 | rendered，8 图 |
| SF21 | B | 1 / 0 | 225 | 96,000 | 5.66 | 18.85 | rendered，8 图 |
| T02-bookshelf | A | 1 / 0 | 294 | 1,468 | 6.15 | 15.90 | rendered，8 图 |
| T02-bookshelf | B | 1 / 0 | 276 | 1,048 | 3.16 | 16.91 | rendered，8 图 |

源码行数包含注释和格式，仅是本次程序长度记录；两组生成设计不同，面数和长度不能单独当成效果分数。

增强组的实际执行几何记录：SF06 有 9 个 hull 构造节点（源码 7 处调用，部分复用）；SF21 有 5 个 rotate_extrude mesh；书架有 4 个 linear_extrude mesh 和 4 个 hull 节点。Polygon 的采样轮廓、拉伸/旋转参数均进入 analysis_geometry。所有导出 source_index/analysis_geometry 的 source hash 与最终源码一致。A 实际 Coder prompt 不含新增 API 章节；B 包含；各案例 A/B 输入 hash 与模型元数据一致。

唯一生成执行失败：A/SF06 首次在既有导出网格规范化阶段报 `EVALUATED_MESH_DEGENERATE`（4 个零面积三角形），Coder 根据执行错误作了一次 patch 后导出成功。该记录位于 `A/SF06/execution_error_0.txt` 与 `coder_execution_patch/`。没有为此修改本轮网格算法，也没有启动 checker。其余五组首次导出成功；没有超时或渲染失败。

## 人工形状观察

这是对实际中性视图和源码的人工观察，不是运行了一次自动 Critic。

- **SF06：局部轮廓改善。** B 的座垫边缘和扶手连接较连贯，A 的若干椭球软包更像分离的拼块；B 用局部 hull 形成过渡。两者仍是简化造型，背部/比例并不完全理想。原需求写有黑色背景，两组都创建了地板和背景板，B/A 背景大小不同，导致归一化后的主体占画面比例不同，且遮挡部分后视图；本轮保留原样，没有补抽样或改取景掩盖问题。
- **SF21：灯罩改善最明确，整件并非全面更好。** A 用 12 层圆柱差集近似锥形壳，侧面出现明显水平台阶，上下 trim 的实心圆柱封住开口。B 直接旋转薄壁剖面，侧面连续、上下口真实保留，俯视可见支撑结构。B 的三脚底座偏小，曲脚总体比例没有改善；其大量 Sphere/Cylinder 采样使总面数反而增加，不能宣称整体更轻量。
- **T02-bookshelf：两组都能生成四层曲边架，未证明明显的整体视觉提升。** A 用 Cylinder/Boolean 构造弧形边，B 直接采样曲边轮廓并拉伸，表达路径更直接。两组顶层都较小，但支架、物品数量和比例不同；B 在连接处仍有突兀处。因此该例证明新算子可用于生成路径，不把更少的面数当作形状更优的证据。

结论边界：确定性小样证明接口/组合/导出成立；真实 Agent 源码和几何记录证明新算子已进入生成；本次可见收益集中在软包过渡与连续薄壁灯罩。三个单样本案例不足以推出普遍性能提升，也未验证制造、装配或物理性能。没有因此追加 loft/sweep、材质 API 或额外修复流程。

## 交付入口

证据目录中的 `comparison_data.json` 保存完整 source hash、模块版本、usage、角色耗时、静态调用位置数量及实际求值 primitive 数量。`run_arm.sh` 保存运行命令；`make_comparisons.py` 仅将原视图拼成对照图，不重渲染/修图。

每个 `{A,B}/{case}/` 包含 `input.json`、`plan.json`、`source.py`、初始/修补源码快照、`runtime_config.json`、`demo_result.json`、usage、会话数据库、实际角色 system prompt/input/output/messages/tool_events。`exec_*/render/scene.glb`、`source_index.json`、`analysis_geometry.json` 与 `views/render_0001.png` 至 `render_0008.png` 是最终展示资产。最终执行目录见各 `demo_result.json`，A/SF06 使用 exec_1，其余使用 exec_0。

对照图（上 A，下 B）：

- [SF06：选定同视角](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/comparisons/SF06_selected.jpg)，[全部八视图](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/comparisons/SF06_all.jpg)。
- [SF21：选定同视角](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/comparisons/SF21_selected.jpg)，[全部八视图](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/comparisons/SF21_all.jpg)。
- [T02-bookshelf：选定同视角](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/comparisons/T02-bookshelf_selected.jpg)，[全部八视图](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_expression_20260929/comparisons/T02-bookshelf_all.jpg)。

最终源码版本：

| 案例 | 组 | SHA256 |
|---|---|---|
| SF06 | A | `a0774b6659eab593ade0be18bfef91e1a0b4ae1879fcaa2601565102162da99b` |
| SF06 | B | `8b1d215c97048b413ad2d54f5e1c21a51d599ecae26ea84262f0f6d49247ac0e` |
| SF21 | A | `fcdb0a7cfc28fd22bed43f32b0b61d614ea84c37ef02f5191554e3e00507af55` |
| SF21 | B | `5ba97d0af313e3fc12695342481d054f953c509badeccc516187c7d905e6d936` |
| T02-bookshelf | A | `6e9eb54349ca9f2d14a66eee6647466f34c585cd0d0916e23859a02af6d65817` |
| T02-bookshelf | B | `353f92957174c7d0db442669b31a2d64bdf44d49c069bef4ba11c77907c7fc24` |
