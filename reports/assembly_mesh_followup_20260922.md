# 固定装配：精度修复、12 份补评与局部修补续测

> 后续用户授权将 SF02/w 的已接受补测版本用于当前展示汇总，见[最新表与失败原因](assembly_topology_latest_20260922.md)。本报告保留原始同预算成绩和诊断经过，不覆盖历史记录；人工观察不参与最新表的验证器裁决。

## Material Passport

- Origin Skill / Mode: academic-research-suite / experiment-agent run.
- Date: 2026-09-22 UTC; baseline HEAD `558de6ec58de560f5ac17174ccf973d9de9b7975`，工作区原有修改保留。
- Version: `assembly_mesh_followup_20260922`；修复代码、测试与本报告同次提交。`temp/` 下实验原件仅保留本机，本文产物链接不代表已上传 GitHub。
- Verification: 精度与 12 份补评、两个局部阶段复现均真实执行；SF02 完成一次真实修补，SF03 被既有可操作反馈入口跳过（0 API/0 修补）。并非制造、插入力或承载验证。
- 独立目录：[全部产物](../temp/assembly_mesh_followup_20260922/)。原 `assembly_topology_paired_fresh_20260921T173631Z` 及首次路径修复补评目录均不覆盖。

## 1. 精度修复：只修件内合并结果的跨进程传递

`adsl-agents/assembly_topology.py` 原先将 Manifold 双精度结果写为默认单精度 PLY，再供接口 worker 读取。改为 NPZ（float64 顶点、int64 面索引，`allow_pickle=False`，`process=False`），不重新焊接、修面或改几何。写、读及父进程文件引用同步修改。几何判定、阈值、原始 STL、connector 和渲染后端不变。

SF07/wo 对每件仅计算一次原有件内并集，再将**同一份数组**分别走旧、新保存路径：

| 部件 | 合并后面数 | 保存前零面积面 | 旧 PLY 读取后 | 新 NPZ 读取后 | 新路径顶点/索引逐值一致 |
|---|---:|---:|---:|---:|---|
| pedestal（失败部件） | 2058 | 0 | 4 | 0 | 是，最大坐标差 0 |
| top_plate（正常对照） | 148 | 0 | 0 | 0 | 是，最大坐标差 0 |

两者新缓存均重新通过既有实体加载验证。证据：[precision/result.json](../temp/assembly_mesh_followup_20260922/precision/result.json)。成功标准是**中间文件不新增缺陷**，不是 SF07 整件 PASS。

相关单测：`tests/test_assembly_topology.py tests/test_fixed_assembly_paired.py`，**43 passed / 13.02 s**。新增两项覆盖单精度会压塌的小面与正常面，旧拓扑/配对/流程测试保持通过。

## 2. 同一修正版补评既有 12 份 retained

耗时 **218.99 s**；0 新生成、0 修补、0 API；外观使用原离线 Image 结果，不重新评图或回选。检查配置、源码和旧记录哈希、retained 选择全部核对不变。 [CSV](../temp/assembly_mesh_followup_20260922/recheck/case_results.csv) · [完成证据](../temp/assembly_mesh_followup_20260922/recheck/completed.json)

| Case | wo Topology（本次） | w Topology（本次） | wo Image（原） | w Image（原） |
|---|---|---|---|---|
| SF07 | FAIL | PASS | FAIL | FAIL |
| SF03 | INDETERMINATE | INDETERMINATE | FAIL | FAIL |
| SF13 | PASS | INDETERMINATE | FAIL | FAIL |
| SF02 | INDETERMINATE | INDETERMINATE | FAIL | FAIL |
| SF10 | INDETERMINATE | INDETERMINATE | FAIL | FAIL |
| SF16 | PASS | PASS | FAIL | PASS |

Topology 通过数仍为 wo **2/6**、w **2/6**；独立 Image 为 0/6、1/6；二者联合 0/6、1/6。分母包含全部选定资产。小样本独立生成，不能据此确证方法因果优越性。

唯一整体状态变化：SF07/wo 从 INDETERMINATE 变为真实测得的 FAIL。缓存精度问题排除后，`tabletop_to_pedestal` 检出 `INTERFACE_LOCAL_INTERFERENCE`：局部非声明干涉约 **50.00004 mm³**，原有数值容差约 **0.11286 mm³**。两个打印件内部仍 PASS。其余 11 个整体状态不变。本轮没有“因 checker 修复而新增通过”，更不是 agent 改善。

## 3. 仅两个代表的逐阶段复现

诊断脚本 `experiments/fixed_assembly_prompt/diagnose_mesh_stages.py`：执行原源码，只对指定最终打印件走现有 `evaluated()`；观察包装器在每次原 `_apply_boolean()` 前后保存多边形与三角化网格，不改变运算、求解器、容差或输入。末端用原打印平移/STL 序列化；与原保存 STL 的局部三角坐标逐值相同。每例由既有 checker 子进程执行器限时 120 秒。

| 代表 | 首次异常 | Boolean 后证据 | 三角化/落盘证据 | 归因边界 |
|---|---|---|---|---|
| SF02/w `leg_front_left_part` | 腿主体 `post` 与 `grain_line_1` UNION | 两个输入各自闭合；第一次 union 后多边形边界 4 条，第二木纹 union 后 8 条 | 保存前后均 52 面、0 零面积面、8 边界边；与原 STL 相同 | 定位到共面木纹交界的 Boolean 求值，不是落盘新增开口；未深挖 Blender 内核 |
| SF03/w `backrest_unit` | 立柱与 `back_lower_rail` UNION | 输入无退化；输出 2 个零面积多边形，其三角化含 8 个零面积面；其余横梁加入后增至 14 | 最终保存前后均 279 面、14 零面积面；与原 STL 相同 | 异常在构造求值时已有，不是缓存精度或 STL 序列化新增 |

SF02 首个木纹区域局部毫米范围约 `x=[-35.5,-34.9], y=-42, z=[11.5,58.5]`；第二条约 `x=[-26.425,-25.975], y=-42, z=[29,63]`。SF03 下横梁与立柱底面齐于 `z=82`；局部共线三角形已在 Boolean 输出中存在。

局部构造实际耗时约 **0.37 s / 0.31 s**（不含 Python/检查器启动）。完整记录：[SF02](../temp/assembly_mesh_followup_20260922/stages/SF02/checkers/stage_diagnostic/stages.json) · [SF03](../temp/assembly_mesh_followup_20260922/stages/SF03/checkers/stage_diagnostic/stages.json)。每个 boolean_NN 文件夹保存输入/输出多边形、三角形与边界位置。

目前证据足以将这两例交给 agent 尝试局部构造修补；没有据此全局替换 Boolean 求解器、改 connector、自动补洞或删除面。共同确认的文件精度缺陷已在第 1 节修复；后两例并非同一个保存精度问题。

## 4. 补充真实修补（不纳入 6×2 主结果）

仅 SF02/w 和 SF03/w 原 retained 源码的副本；使用原尺度、尺寸、fit=0.2 mm 和模型配置。每例初始测量/审核加**最多两次源码修补**，没有初始生成或额外预算。使用现有 `verify_topology.measure/repair`、Image/Code、Engineering、隔离候选和 retained 逻辑，不人工改生成源码。

SF03 的原始通用 `PRINT_MESH_UNMEASURABLE` 结果完整保留在 `baseline_raw_result.json`。本补测用第 3 节真实位置证据标注这一个初始 finding 可尝试源码修补，**状态仍为 INDETERMINATE**；不修改生产 checker 规则，不将未知连接伪称断开。标注文件记录原因/源码哈希，修改后的候选使用正常 checker 重新测量，不复制初始注释为当前事实。

运行已结束，tmux 返回交互 shell、exit=0；没有残余修补任务，未关闭独立代理。

| 结果层级 | SF02 初始 → 补充候选 | SF03 |
|---|---|---|
| 网格/件内连通 | 1/6 PASS、5 件开口未验证 → **6/6 PASS** | 已完成靠背退化定位；没有源码修补，不报改善 |
| 接口配对 | 0/5 PASS（全依赖阻塞）→ **5/5 PASS** | 原靠背接口仍未验证 |
| 现有 Image/Code 裁决 | 初始拒绝 → 新 Image 通过；依照原顺序不额外调用 Code | 未重做审核 |
| 控制器最终接受 | **接受 attempt_0001**，retained/qualified/发布与检测版本一致 | 保留输入；没有合格修补版本 |
| 独立人工观察 | **发现靠背弧顶消失，最高点 150 → 145 mm**；因此不能称外观/尺寸严格保留成功 | 不评价修补外观 |
| 实际修补次数 | **1**（同一候选内两次成功 apply_patch，不是两轮候选） | **0** |
| API 与耗时 | 9 次成功响应；input 155,507 / output 7,019 / total **162,526 tokens**；约 **375.06 s**（含初始复测） | **0 API / 0 token**；约 28.52 s 准备/测量后退出 |

SF02 的 Engineer 阅读了真实源码，定位 `ChairLeg.__init__` 和 `BackrestPanel.__init__`。Coder 真实 `read_file` 成功，随后两次精确补丁成功：将齐平木纹附加实体改为浅的 `boolean_difference` 刻槽。未改 add_part/connect、TabSlot、工况、阈值或后端；未手工修改生成源码。木纹的 x/z 位置和长度沿用，沟槽从表面向内约 0.30 mm、向外约 0.10 mm。第二次候选机会没有使用，控制器在第一次通过后正常停止。

**重要负面结果：**虽然靠背弧顶的 Sphere/共享尺寸代码并未删改，新构造求值导出的靠背最高 z 为 145 mm，原来是 150 mm。对应同视角图片可见顶部变平；Image 声称保留 subtle arch，但该判断被保存网格和人工对图反驳。该结果说明**局部网格恢复、接口验证与控制器接受是真实发生的**，却不证明整件外观和冻结目标尺寸都可靠保留。本次不修改裁决规则、不追加修补，也不深入追查这个新候选的另一处 Boolean 异常。

SF03 跳过原因并非 API、预算耗尽或模型主动放弃：`feedback_schema.py::localized_mesh_feedback()` 对 assembly_topology 的未验证几何只接收 `OPEN_PRINT_MESH` + 位置 + 边界数量。SF03 的 `PRINT_MESH_UNMEASURABLE`（零面积面）即使补充位置/repairability 注释仍不在入口内，`verify_topology.repair()` 在调用任何模型前退出。保留了原始和注释版本，**没有伪造 OPEN_PRINT_MESH 来绕过限制，也没有把它改成 FAIL/PASS**。本轮用户允许 1–2 个真实修补，已有 SF02 的一个完整闭环；此处停止，不扩展生产反馈类型。SF03 的 agent 修补能力仍未验证。

独立汇总：[supplement_summary.json](../temp/assembly_mesh_followup_20260922/supplement_summary.json)。补测没有加入第 2 节的原 6×2 成绩，原 12 个 retained 及其绑定文件再核验不变。

### SF02 实际证据与同视角图

- [原始源码](../temp/assembly_mesh_followup_20260922/repairs/SF02/original/source.py) / [最终发布源码](../temp/assembly_mesh_followup_20260922/repairs/SF02/source.py)
- [Engineering 输入](../temp/assembly_mesh_followup_20260922/repairs/SF02/rounds/round_01/engineering_input.json) / [方案](../temp/assembly_mesh_followup_20260922/repairs/SF02/rounds/round_01/engineering_critique.json)
- [Coder 真实输入与工具事件](../temp/assembly_mesh_followup_20260922/repairs/SF02/stage_inputs/assembly_repair_2.json) / [最终审核和验收](../temp/assembly_mesh_followup_20260922/repairs/SF02/assembly_result.json)
- [总装 GLB](../temp/assembly_mesh_followup_20260922/repairs/SF02/assembly/scene.glb) / [分件 STL、拆分 GLB 与 manifest 目录](../temp/assembly_mesh_followup_20260922/repairs/SF02/assembly/)

原始：

![SF02 original](../temp/assembly_mesh_followup_20260922/repairs/SF02/rounds/round_01/asset/render/render_0001.png)

修补后：

![SF02 repaired; crown visibly flattened](../temp/assembly_mesh_followup_20260922/repairs/SF02/rounds/round_02/candidates/01_repair_coplanar_face_grain_01/asset/render/render_0001.png)

## 执行命令

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_assembly_topology.py tests/test_fixed_assembly_paired.py
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python temp/verify_solid_precision_20260922.py
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python temp/recheck_assembly_topology_20260922.py --original temp/assembly_topology_paired_fresh_20260921T173631Z --output temp/assembly_mesh_followup_20260922/recheck
bash experiments/tmux_session.sh adsl_mesh_followup_20260922 /vepfs_default/chanxueyan/lhp/lms/aDSL bash temp/run_mesh_followup_20260922.sh
```

目录创建拒绝覆盖；以上是已执行记录，不应对同一目录重放来重置预算。诊断脚本通过 `CheckerSpec(name='stage_diagnostic', timeout_seconds=120)` 运行，命令参数和日志保存在各阶段目录。
