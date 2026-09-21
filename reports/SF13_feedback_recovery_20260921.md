# SF13：统一反馈后的一次真实修补

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-09-21
- Verification Status: ANALYZED（一次真实闭环已完成，不是多次复现实验）
- Version Label: SF13_feedback_recovery_20260921T051900Z

## 结果

**流程跑通，且本例开口网格问题实际改善；唯一候选被正式接受。**

| 项目 | 修补前 | 修补后 |
| --- | --- | --- |
| 框架内部连通 | PASS，1 个实体 | PASS，1 个实体 |
| 五块层板 | 各 14 条边界边；OPEN_PRINT_MESH / INDETERMINATE | 各 0 条边界边、0 条非流形边；各 1 个实体，PASS |
| 五条 TabSlot 接口 | 因层板网格不可测而未验证，不是断开 FAIL | 全部 INTERFACE_GEOMETRY_PAIRED / PASS |
| 装配 Topology 汇总 | 1 PASS / 10 INDETERMINATE | 11 PASS / 0 FAIL / 0 INDETERMINATE |
| 导出一致性 | 已有资产结果保留 | PASS |
| 外观审核 | Image 认为木纹不足；Code 读取源码后纠正该判断 | Image PASS，无需再次调用 Code |
| 最终版本 | 原始版本 | `attempt_0001`，qualified / retained / approved=true |

仅验证本轮的件内连通及局部接口几何配对。旧 geometry 全套门槛保持 NOT_EVALUATED；
旧 topology、standing、FEA、overhang 未启用。没有验证真实插入、保持力、承载或制造成功。

## 实际执行与修改

基线 HEAD 为 `8cc8e4c31165399bbeb50a7c8268583aef40eb21`，使用上一轮尚未提交的接入/恢复补丁。
实现 diff、源码快照及哈希保存在本轮目录；运行期间未修改实现代码。
沿用已有 CLIProxy 独立 tmux `adsl_cliproxy_20260919`，模型 `gpt-5.6-sol`。
实验 tmux `adsl_SF13_feedback_recovery_20260921` 已退出 rc=0，保留可交互 shell；代理未关闭。

本轮从保存的 SF13 六件五接口资产开始，不重新生成，不调用 Planner。
冻结 1 mm/scene unit、100×32×200 mm、单侧余量 +0.2 mm，最多一次源码修补、两次审核/检测。
几何执行 120 秒、渲染 300 秒、Topology 900 秒上限未改。
旧 v2/v3 的失败、成本及账本未覆盖，新授权的一次修补单独记录。

1. 基线复测复现五块层板开口，每块 14 条边界边。
2. Image/Code 完成原有顺序的审核；Code 纠正“完全没有木纹”的判断，不覆盖几何未验证。
3. Engineering 读取原源码，结合已测坐标，定位 `ShelfBoard` 三条齐平顶面装饰；提出一个协调方案。
4. Coder 真实 API 请求包含五条局部 findings、两路审核、冻结条件、有效建议、证据绝对路径及本次已预留预算。
5. Coder 通过真实 `read_file` 读取隔离候选，真实 `apply_patch` 成功一次。没有路径错误、额外候选或人工修改生成源码。
6. 重新导出、渲染、Image 审核和 Topology 检测后，候选正式接受。发布源码、模型、结果及哈希一致性复核通过。

**具体改动仅为 `ShelfBoard`：** 原三条宽 0.16 mm、厚 0.08 mm、上表面齐平 z=2 mm 的
独立深色装饰实体，改为同位置宽 0.18 mm、实际深 0.12 mm 的浅凹槽，使用 `boolean_difference`。
五块层板共用该类，因此一次局部源码修改同时作用于五件。
AST 对比确认其他顶层定义未变；框架、部件清单、连接、frame、TabSlot 参数和尺度元数据完全一致。
这支持该局部构造替换在本例有效，**不证明具体 Boolean 内核退化根因已被完全确定**。

注意：本轮 Engineer/Coder 使用了请求内的局部坐标、数量及源码，并没有额外调用工具读取
`boundary_localization.json`。该文件路径已随真实请求提供且存在，但不能宣称此次实测了
“先读错路径再纠正”；那条异常恢复分支的证据仍来自上一轮真实文件工具模拟测试。
本轮 Engineering 正常给出有效建议，也没有触发解析错误回退分支。

## 同视角外观

人工对照下列保存图片：总体轮廓、层板数及位置没有明显变化，局部木纹表现发生改变。
512 像素图片不能证明精确配合；几何结论来自独立检测。

| 原始版本 | 正式保留候选 |
| --- | --- |
| ![原始](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/rounds/round_01/asset/render/render_0002.png) | ![候选](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/render/render_0002.png) |

## 成本

- 基线测量：25.40 秒；修补闭环：367.06 秒（约 6.1 分钟，另计基线）。
- 候选导出器：4.07 秒；候选 Topology：37.70 秒。
- 9 次 API 返回均成功；输入 150,365 token，输出 6,139 token，总计 **156,504 token**。
- 初始生成 0 次，源码修补 1 次；没有追加预算。

| API 阶段 | 调用数 | 调用耗时合计/秒 | 输入 token | 输出 token |
| --- | ---: | ---: | ---: | ---: |
| 初次 Image | 1 | 29.89 | 6,885 | 467 |
| 初次 Code | 2 | 65.27 | 26,852 | 849 |
| Engineering | 2 | 133.08 | 42,463 | 3,453 |
| Coder | 3 | 33.50 | 67,092 | 1,093 |
| 修改后 Image | 1 | 22.43 | 7,073 | 277 |

## 可查阅产物

根目录：`/jiigan-hp/lms/aDSL/experiment/local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z`。
项目中的 `local_experiment/` 软链接也可访问。

- [最终总装 GLB](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/assembly/scene.glb)
- [拆分 GLB](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/assembly/exploded.glb)
- [最终源码](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/source.py)、[原始源码](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/original/source.py)
- [验收结果](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/assembly_result.json)、[只读一致性与成本核验](../local_experiment/assembly_topology_SF13_feedback_recovery_20260921T051900Z/post_run_audit.json)
- `assembly/*.stl`：6 个独立打印件；`render/`：8 张最终渲染。
- `stage_inputs/` 和 `api_calls/`：实际请求、工具事件及逐调用成本；`patch_diagnostics/`：精确补丁证据。
- `rounds/round_02/candidates/01_repair_shelf_grain_as_closed_relief/`：候选、全部复测及审核；失败历史目录未改动。

实际入口：先 `verify_topology.py --saved-case <原 SF13> --output <新目录> --llm-config adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml`，
再在新 tmux 中运行 `verify_topology.py --repair --output <同一新目录>`。本目录已完成，不可重置账本重复调用。
