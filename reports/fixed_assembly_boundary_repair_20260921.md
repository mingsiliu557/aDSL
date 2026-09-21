# 固定装配：SF13 开口网格一次修补与 SF07 新生成

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-09-21
- Verification Status: ANALYZED（两次 StepCode 运行及后续 CLIProxy 补跑均已结束，未完成有效模型修改）
- Version Label: open_mesh_repair_v1

## 冻结范围

起始 HEAD：`d7c2cec92230b29f74d6b9fa1adefdbf59688388`。本轮为其工作区上的最小反馈补充。
保留用户已有修改和旧实验；不人工修改生成源码、不修补导出网格、不调整容差。
只启用 `assembly_topology`；旧 topology、standing、FEA、overhang 不运行。
几何执行上限 120 秒、装配 topology 原有预算 900 秒；使用 StepCode `gpt-5.6-sol`。

## SF13：已有网格的定位

原资产：`fixed_assembly_prompt_regroup_SF13_20260920T160523Z/SF13/generate`。
原源码 SHA256：`2ae975928cf4dd73ce31bfffb8314cbe55571a3ef0ca98025253d96c4815de75`。
新目录：[SF13 一次修补](../local_experiment/assembly_topology_SF13_repair_20260921_v2)。
尺度 1 mm/scene unit，尺寸 100×32×200 mm，单侧余量 +0.2 mm 不变。
无初始生成、无 Planner；最多一次源码修补、两次审核/检测。

使用保存的 STL，逆转打印落地平移恢复局部毫米坐标；精确坐标去重，没有近距离焊接或补洞。
五块层板均为 14 条边界边、0 条非流形边，均位于 z=2 mm。

| 顶面区域 | X 范围/mm | Y 范围/mm | 边界边 |
|---|---|---|---|
| grain_1 | -37～23 | -23.58～-23.42 | 4 |
| grain_2 | -32～40 | -16.08～-15.92 | 6 |
| grain_3 | -37～17 | -8.08～-7.92 | 4 |

对应当前原源码 `ShelfBoard`（83–103 行），特别是 93–103 行齐平顶面装饰。
位置/尺寸与三条嵌条吻合，远离后端 y=0、插入方向 +Y 的榫头。
这是定位证据，不是精确面片源码归属证明，也不能确定哪一次 Boolean/三角化首先导致不闭合。
未重新执行原始 CSG 来定位；未将五个未验证接口描述为五条断开的连接。

![已有层板边界定位](../local_experiment/assembly_topology_SF13_repair_20260921_v2/boundary_localization.png)

检测基线：框架 PASS；五块层板 `INDETERMINATE / OPEN_PRINT_MESH`；
五条接口 `INDETERMINATE / DEPENDENCY_MESH_UNAVAILABLE`。
详细坐标与核对过的源码范围见 `boundary_localization.json`，边列表在各件 `boundary_edges.json`。

## 最小代码修改与验证

- 核心拓扑：记录开口边坐标、区域和数量；仍不进行不可靠的实体/接口判断。
- 装配 adapter：对有边界证据的 `OPEN_PRINT_MESH` 允许局部源码修补；其他未知/基础设施错误不因此变为几何目标。
- 短反馈：仅为 `assembly_topology` 增加该类型的显式入口；原 FEA 分支不变。
- 单次修补脚本：允许这个已定位的未验证状态进入既有 Engineering→Coder 循环，不重置预算。
- 原有新生成入口：显式配置 StepCode 和唯一 `assembly_topology` 工具；不复制生成循环。

实际执行的无 API 测试：

```text
test_assembly_topology.py + test_fixed_assembly.py + test_generation_review_contract.py:
56 passed, 6 skipped
test_fixed_assembly_prompt.py + test_assembly_topology.py:
70 passed
test_mesh_failure_feedback.py + test_fea_mesh_invalid.py:
30 passed
```

上述集合有重复用例，不将它们相加作为唯一测试数。测试包括真实小网格及模拟反馈/预算/发布，未运行真实 FEA。

## 真实运行结果

SF13：已正常结束，`approved=false`，`no_executable_engineering_proposal`。

| 环节 | 实际结果 |
|---|---|
| 保存网格测量 | 已执行；框架 PASS、五件开口及五接口未验证 |
| Image Critic | 已执行；认为中央背部支撑显眼，拒绝 |
| Code Critic | 读取源码后认可上述外观意见，提出背部宽度 14→12 mm |
| Engineering | 已执行并读取完整源码；收到 14 条边界边、三处局部区域和未验证事实 |
| 提案 | 仅提出背部外观修改，明确不承诺修复层板开口；没有针对木纹处开口提出几何方案 |
| 提案校验 | 拒绝：`RearSpine.solid` 是属性，不是当前校验接受的类/函数 scope；`RearSpine.__init__` 存在，但列表中另一项不合法 |
| Coder / 候选重新导出复测 | **未执行**；没有通过校验的提案，0 次实际源码编辑 |
| 最终资产 | 原版本，源码哈希不变，装配 topology 保持 INDETERMINATE；未获得合格版本 |

实际首轮 Engineering 请求确实包含五件边界计数、位置、源码读取入口和未验证状态；
索引候选无 source_id 未阻断请求。`api_calls/004.json` 为真实请求，
`005.json` 包含读取 `original/source.py` 的工具返回。完整参数没有被整批塞入短反馈。
Engineer's `finding_ids` 虽引用五条开口问题，提案正文却只修外观：不能据此声称它实际尝试了网格修复。
具体证据为 `rounds/round_01/engineering_critique.json` 和 `proposal_rejected.json`。

5 次 API 返回均成功，实际输入 70,150 token、输出 3,464 token，总计 73,614。
API 耗时合计 564.98 秒，首个请求到最后返回 569.18 秒。
没有追加规划/编辑或放宽 scope 校验来制造成功；不是 checker 测量超时，也不能据此证明 Coder 无法修网格。
本轮验证了定位反馈送达与拒绝后保存/收尾，**没有验证实际网格恢复、候选复测或修复成功**。

SF07：已结束，使用原始圆桌 prompt，不提供旧源码/旧生成图片。
新目录：`local_experiment/assembly_topology_prompt_SF07_20260921_v1`。
尺寸 120×120×120 mm，尺度 1 mm/scene unit，余量 +0.2 mm。
最多五轮审核（一次初始生成、四次修补上限），早停/失败不追加机会。
实际初始生成 1 次、源码修补 0 次、总墙钟 507.82 秒、8 次 API / 86,390 token。
Planner 自主决定底座、立柱、桌面三个打印件、两条 TabSlot 接口；没有提供分件答案。
原始/最终保留源码 SHA256：`c676cc1cc27621ed18657e1a674ce44893c2d0ff7d53b81c1f6fd0ec7f526cc3`。
`input_audit.json` 的输入审计、实际装配 API 审计均通过。
Planner/Coder 实际请求与保存输入一致；两者开始时源码为空，参考图片为零。

| 首轮检测对象 | 实际结果 |
|---|---|
| pedestal_base | INDETERMINATE / OPEN_PRINT_MESH；41 条边界边，0 条非流形边，位于局部 z=24 mm 顶面 |
| central_stem | PASS；1 个材料分量 |
| top_plate | PASS；1 个材料分量 |
| connect_stem_to_base | INDETERMINATE / DEPENDENCY_MESH_UNAVAILABLE |
| connect_top_to_stem | PASS / INTERFACE_GEOMETRY_PAIRED；仅局部几何配对范围 |

初始导出一致性检查 PASS，全部五项 topology 测量/依赖结果保存，耗时见 checker `report.json`。
Image Critic 拒绝底座/立柱处明显的黑色环状外观；人工看首图也可见该现象，没有被视觉审核放过。
Code Critic 认为源码中的径向台阶应被修正，而不是按 Image 的字面解释填洞；
这属于源码审核意见，**不是实际网格闭合或连接通过的证明**。网格测量的未验证状态仍保留。

Engineering 的真实请求含 41 条开口边、局部位置和源码入口，随后读取源码。
第二次 Engineering API 调用返回后，SDK 解析结构化输出报
`ModelBehaviorError: Invalid JSON when parsing model output`。
证据：`SF07/generate/rounds/round_01/engineering_error.json`。
既有每调用日志保存输入、用量及 API 是否返回，但没有原始输出正文；
因此本轮**不能归因**为 Markdown 包裹、截断、具体字段或代理损坏。

最终 `engineering_unavailable`、`approved=false`，保留 original，topology 为 INDETERMINATE。
没有进入 Coder、没有重新导出修补候选，也没有自动重试或扩大预算。
脚本 exit 0 / `pause_batch=false` 只说明容错保存与退出，不代表整个 agent 流程正确或装配通过；
本轮没有其他批次可继续。错误被记录而非抹成成功。

![SF07 首稿总装](../local_experiment/assembly_topology_prompt_SF07_20260921_v1/SF07/generate/render/render_0001.png)

模型、独立 STL 和拆分图分别位于 `SF07/generate/assembly/scene.glb`、
该目录的 `pedestal_base.stl` / `central_stem.stl` / `top_plate.stl` 和 `exploded_render/`。
这些是未通过的诊断资产，不是合格制造件。

会话 `adsl_topology_prompt_SF07_20260921` 已回到交互 shell；本轮启动的 StepCode 代理已关闭，其他代理/tmux 未动。
主日志为该目录 `run.log`，逐调用记录在 `SF07/generate/api_calls/`。

运行入口（已运行，不应对相同目录重启或覆盖）：

```bash
python experiments/fixed_assembly_prompt/verify_topology.py --repair \
  --output local_experiment/assembly_topology_SF13_repair_20260921_v2
python experiments/fixed_assembly_prompt/run.py \
  --root local_experiment/assembly_topology_prompt_SF07_20260921_v1 \
  --cases SF07 --assembly-topology --max-rounds 5 \
  --llm-config adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml
```

两例都不据数字几何通过声称实物插接、保持力、承重或制造成功。

## 结论边界

检测与短反馈送达已实测；SF13 三处边界可以关联到源码中的齐平木纹候选区域。
但 **SF13 因提案选题/位置校验而停止，SF07 因 Engineering 输出解析而停止**，
二者都还未测试 Coder 对这个网格问题的真实修补效果，不能将其概括成“agent 修不好网格”。
保留当前失败证据，后续若处理提案字段/输出恢复应单独授权，不在本轮自动扩大修复。

## 用户授权的 CLIProxy 补跑（独立记录，已结束）

用户随后要求不用 StepCode，改用 CLIProxy。复用原 SF13 与相同边界定位证据；
新目录 `local_experiment/assembly_topology_SF13_cliproxy_20260921_v3`。
没有重新生成模型，不覆盖上面两次失败记录；只换为已有 `cliproxy-gpt-5.6-sol.yaml`，
该 profile 使用 gpt-5.6-sol、900秒 API timeout、max_retries=0。
原一次编辑/两次评估上限、几何阈值、Image/Code 与提案校验不变。

仅为现有单次修补脚本增加可冻结的 `--llm-config` 参数；新配置选择的2项模拟测试通过。
代理窗口为 `adsl_cliproxy_20260919`（原服务仍在，未重启），实验窗口为
`adsl_topology_SF13_cliproxy_20260921`；二者分离，实验结束不自动关闭代理。
本地模型列表检查通过，首个真实 Image Critic API 在15.40秒返回，随后进入 Engineering。
这只证明当时 API 可用，不证明该代理总体更快或解决了之前的提案/JSON问题。
最终4次API均返回，逐调用总计62,913 token，API耗时合计66.02秒，首个请求到最后返回71.91秒。
注意 `run_result.json` 的旧 usage 汇总仅含3次/47,392 token，漏记失败的修补阶段；这里按4条 `api_calls` 的实际返回计费记录报告。
Image批准外观；Engineering正确关联到 `ShelfBoard` 的齐平木纹，并提出局部修补方案，位置校验通过。
Coder已进入隔离候选并读取源码，但读取 `boundary_localization.json` 时失败：
文件存在于实验根目录，工具却解析到 `rounds/round_02/candidates/01_repair_shelf_flush_grain_boundaries/` 下。
最终 `TOOL_ERROR`，源码前后哈希相同，没有执行补丁或候选重新导出；原资产与 INDETERMINATE 状态保留。
证据在该候选的 `edit_outcome.json`。这是尚未修复的实验诊断路径传递问题；
不是API不可用，也没有发生可以据以判断成败的网格源码修补。
本次仅归档上传当前版本，不修改上述路径逻辑、不追加重跑；独立CLIProxy仍保留。
