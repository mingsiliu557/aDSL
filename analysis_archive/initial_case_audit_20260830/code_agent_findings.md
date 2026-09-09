# 当前代码与 Agent 流程缺陷

Verification Status：**ANALYZED**

本文件区分两类内容：

- **流程缺陷**：会改变生成正确性、安全性或论文主张，审计阶段没有擅自修复，以免改写被测 baseline。
- **运行兼容修复**：只让原流程在本机原生 API/Eevee/共享盘环境可运行，已经实现并有测试。

## 严重度总览

| 级别 | 问题 | 直接影响 |
|---|---|---|
| Critical | 生成源码由 runpy.run_path 不受限执行 | 模型生成代码可访问文件、网络、子进程和当前用户权限；不是安全 sandbox |
| High | 最后一轮不经过任何 Critic | max_rounds 的实际可审查轮数少 1；max_rounds=1 恒定 approved=false |
| High | Image Critic 可绕过 Code Critic | 与论文 final adjudicator 叙述不一致；代码/结构验证覆盖不稳定 |
| High | articulated 只渲染 Joint.initial | 无法由原 workflow 验证门、抽屉、lever 的方向、碰撞和全行程 |
| High | checklist/relations 不是可执行约束 | “automatic checks / constraint satisfaction”主要仍靠 LLM 自述 |
| High | 没有 AST/compile preflight | 3/15 首轮执行浪费在同类非法 0. thirty / 0. forty 语法 |
| Medium | 固定 8×15° object orbit | 杯内腔不可见，室内墙面遮挡，仍可能被批准 |
| Medium | Code Critic 被要求 MUST TRUST THE CODE LOGIC | 容易把源码意图当成最终几何/视觉事实 |
| Medium | 无 topology/contact/collision gate | 退化面、non-watertight CSG、interpenetration 不阻止批准 |
| Medium | AABB 与 boolean/transform 是近似 | 布局可能浮空或错位；T02 已实际触发 |
| Medium | SQLite 保留 data-URL 图片 | selective context 不等于存储 pruning，长运行会持续增大 |
| Medium | Agent workflow 单体、依赖硬编码 | 难以替换 verifier、相机策略、state sampler 或做单元级 ablation |
| Medium | primitive/material 表达能力有限 | image-to-shape 和外观 case 粗糙；高保真阶段不在主流程 |
| Low | Blender 缺 Draco 时输出 ERROR 但 GLB 成功 | 成功日志含误导性错误，路径还会受 cwd 影响 |

## 1. Critical：生成代码没有安全边界

证据：

- adsl-agents/utils/asset_executor.py:27-32 解析绝对 source 路径后，直接 runpy.run_path。
- 子进程仅做进程隔离；没有 seccomp、容器、namespace、权限降级、只读根目录、网络禁用或 import allowlist。
- Coder prompt 要求“只使用 adsl.core”只是自然语言约束，不是执行时 policy。

风险：任何被 prompt injection、恶意参考图或模型失控诱导的 source.py，都继承运行用户的文件/网络/进程能力。在 root 运行环境中风险尤其高。

建议优先级 P0：

1. 先 ast.parse；拒绝 Import/ImportFrom（只允许精确 from adsl.core import *）、Attribute 链和危险 builtins。
2. 生成代码放入独立低权限容器/namespace；workspace 单目录读写，repo/credential 只读或不可见，禁网。
3. 资源限制：wall time、CPU、内存、文件大小、子进程数。
4. 把几何构造改成受控 IR/interpreter 会比执行任意 Python 更可靠；若保留 Python embedding，应明确它不是 sandbox。

## 2. High：round budget 的 off-by-one 语义

adsl-agents/service.py:462-547 的顺序是：

1. execute；
2. 如果 round_number == end_round，立即设置 approved=false / round_limit_after_execution；
3. 只有非最后一轮才运行 Critic。

结果：

- max_rounds=1 没有任何 Critic。
- 论文 R=10 在该语义下最多只有 9 次 critique。
- 可用资产仍在 service.py:638-675 被 publish，并以 status=completed 写入 manifest。

T01-one-round-control 精确验证了该问题：它与 T01-main 使用同一需求，成功生成 4 腿/3 slats，但没有 Critic，最终 completed + approved=false。

建议 P0：把一个 round 定义成 execute + critique + optional repair。最后一轮可以“不再 repair”，但必须完成 critique；另将状态拆成 execution_status、verification_status、publish_status，避免 completed 与 unverified 混用。

## 3. High：Code Critic 并非稳定的最终裁决者

论文 §3.2 说 Image Critic 的观察交给 Code Critic，后者 final adjudicator。公开代码 service.py:572-576 在 image_decision.approved 时直接结束，Code Critic 只在 Image Critic 拒绝时运行（:578-609）。

后果：

- 通过路径依赖 Image Critic 是否先拒绝，而不是固定 verifier contract。
- 同一个结构问题可能因视觉模型置信度不同而完全跳过源码检查。
- 本轮多数 approved case 没有 Code Critic 记录。

建议 P0：每次成功 execution 都固定运行 Image Critic → deterministic verifier → Code Critic/aggregator。Code Critic 应综合全部证据，而不是只作为视觉拒绝后的二审。

## 4. High：关节 prompt 要求和工具能力矛盾

critic_code_image.md:22-23 要求在 representative nonzero pose 评估 axis、limit、initial 与穿模方向；但：

- export_glb.py:29-45 只读取 joint.initial。
- export_glb.py:398-415 把这一状态烘焙到单个 GLB。
- asset_executor.py:53-59 只对该 GLB 渲染一组八视图。
- service.py 没有 state sampler 或轨迹 collision checker。

A01 的八视图全是关闭门/关闭抽屉，Code Critic 仍依据程序结构批准。本轮额外 pose probe 证明三个 case 的部件确实能移动，但它是审计工具，不是被测流程。

建议 P0/P1：

- 对每个 movable joint 至少采样 lower/mid/upper，嵌套 joint 可用 pairwise/关键组合而非笛卡尔爆炸。
- 输出 pose-labelled render、AABB/mesh collision、连通/attachment 和 limit report。
- Code Critic 只能对实际提供的 pose evidence 下结论；不能声称检查了不可见状态。

## 5. High：Planner 的“可验证约束”仍是字符串

models.py:17-21：

- components 有 name/description；
- relations 是 list[str]；
- critic_checklist 是 list[str]。

这比无结构 prompt 好，但没有：

- typed subject/relation/object；
- exact count / tolerance / axis / contact distance；
- 对 component name 的引用完整性；
- 可执行 predicate 或 verifier binding。

因此 Planner、Coder、Critic 并未真正共享机器可检查的同一种关系表示。T03 的 exact count 是靠源码列表长度和人工 AST 审计确认，不是主流程检查。

建议 P1：引入 typed Constraint union，例如 Count(component, eq=16)、Contact(a,b,tol)、Aligned(a,b,axis,tol)、JointMotion(name, samples, collision_free)，执行器返回同 schema 的 evidence 和 pass/fail。

## 6. High：缺 AST/compile preflight

I01、I02、M01 scratch 的首轮 source.py 分别包含非法 Python 数字词：

- 0. thirty
- 0. forty
- x * 0. thirty

三次都先启动完整 Blender executor 才在 compile 阶段失败，再消耗 Debugger/Coder repair。3/15 不是统计性失败率，但同一模式重复三次足以说明缺少便宜的本地 gate。

建议 P0：

1. write_file/apply_patch 后立即 ast.parse + compile；
2. 将 SyntaxError 直接作为工具失败回传当前 Coder，避免进入 Blender；
3. 对模型常见 numeric-word corruption 加小型 lint；
4. 验证 top-level scene 赋值存在，再启动 bpy。

## 7. Medium：Critic 证据设计不充分

### 固定相机

render.py 只有固定 15° 单层 orbit。实际后果：

- T04：open-top 杯的内壁/底面在八视图中几乎不可见。
- S01：房间外部绕拍，3/8 图几乎完全是墙。
- T02：侧板遮挡 curved nosing。
- A01：只看 closed initial，运动不可见。

建议：根据资产类型选择相机。容器增加 top-down；scene 使用室内语义相机/墙体隐藏；articulation 用 pose + view 联合采样；参考图 case 增加参考视角对齐。

### “信代码”过强

critic_code_image.md:17-23 明确要求 MUST TRUST THE CODE LOGIC。这能修正 occlusion 假阳性，但也会掩盖渲染器/CSG/材质实际输出与代码意图的偏差。T04 是直接例子：源码有 boolean cavity，不代表用户要求的 visible interior 已被满足。

建议：将判断拆成 program intent、executed geometry evidence、render evidence 三栏。代码只能证明“写了什么”，不能单独证明“执行结果看起来如何”或“网格有效”。

### 一轮只找一个视觉问题

critic_image.md:7-13 要求只处理一个最关键问题。优点是反馈集中，缺点是在 round budget 很小时会留下多个独立缺陷；而公开默认 max_rounds=2 实际最多只有一次 Critic。

建议：保留 priority，但返回 bounded list（例如最多 3 个）并分 blocker/major/minor。

## 8. Medium：几何验证缺失

对 GLB 先按空间位置焊接 glTF 因 normals/UV 分裂的重复顶点后：

- 大多数 primitive mesh 是 watertight。
- T02 四个 board+nosing union 非 watertight，合计 41 个 zero-area faces。
- T04 vessel 本身闭合，但 C-handle union 非 watertight。
- M01 scratch power-unit union 非 watertight，有 2 个 zero-area faces。

这些 case 仍被工作流批准，说明 Critic 没有读 topology evidence。

建议 P1：Executor 生成 geometry_report.json，至少包含 welded watertight、degenerate faces、components、self-intersection（可行时）、bounds、empty mesh、NaN。将用户明确要求的 watertight 变成 hard gate。

## 9. Medium：AABB 不是精确约束求解器

bounds.py:9 已直接注明 transformed case 可能 over-estimate；boolean difference/intersection 也有近似 fallback（:178-212, :214-256）。

T02 第一轮把书放在整个 CurvedShelf AABB 上，较高 nosing 抬高了支撑面；Code Critic 发现后，第二轮改为 shelf.board。这说明 relational operator 仍依赖“选对语义子部件”，并不天然免疫几何近似。

建议：在 planner/coder 中区分 structural part 和 decorative protrusion；对 contact 用 directional support/实际 mesh raycast，而不是组合 AABB；将 tolerance 和证据坐标写入 report。

## 10. Medium：memory 是上下文策略，不是数据库生命周期策略

runner.py:109-115 为每个 role 绑定 SQLiteSession，sessions.py:23-32 把所有角色写入同一 sessions.sqlite3。service.py 的 _CONTEXT_POLICY 是描述性 metadata，未实现数据库内容删除。

本轮：

- 15 个 session DB 合计 53,882,880 bytes；
- 原始字节里有 195 次 data:image；
- 最大单 case 接近 8 MB。

建议 P1：将 render 作为 content-addressed 外部 artifact，session 只保存引用；每轮明确 prune/compact；记录 logical context token 与 persistent artifact bytes 两组指标。

## 11. Medium：工程结构与可测试性

ObjectWorkflow 同时负责 profile、session、Agent 构建、iteration、execution、critic、repair、checkpoint 和 publish。相机、8 views、15°、URDF 也在 service.py:338-349 硬编码。

影响：

- 难以替换 deterministic verifier；
- 难以做 paper ablation；
- resume/round/status 组合复杂；
- 集成测试成本高。

建议 P2：拆成 PlannerService、SourceSynthesisService、Executor、EvidenceBuilder、VerifierPipeline、RefinementPolicy、Publisher，并用 protocol/interface 注入。

## 12. 已完成的运行兼容修复

这些修改尽量不改变 Agent 决策逻辑：

- Native Stepcode HTTP profile：只走 OpenAI-compatible Responses API，不使用 Codex Exec。
- key 只由 argv-only stepcode config get apiKey 在内存读取；profile/repr/runtime metadata 不落 secret。
- trust_env=false，避免 localhost 被代理劫持。
- Coder import 修正为论文/DSL 文档一致的 from adsl.core import *。
- render 支持 env override，但默认仍是原始 BLENDER_EEVEE、1024、256。
- 用户目录 EGL/Mesa + .bashrc 让原 Eevee 在 CPU surfaceless 成功；无需 GPU。
- executor timeout/interruption 使用独立 process group，TERM → KILL 有界清理。
- publish 保留 scene.joint_states.json 与 render/meta.json。
- executor 从 python -m adsl.agents.utils.asset_executor 改为直接执行同目录脚本，避免子进程无意义导入完整 Agent/OpenAI stack；同一 source smoke 从 308.634 s 降到 3.727 s（有 /tmp bpy cache）。
- tests 不再被 .gitignore 整体排除，并增加 config/execution/prompt/publish/service 回归测试。

## 推荐修复顺序

1. **P0 安全**：生成代码 sandbox + AST allowlist。
2. **P0 正确性**：修复 round off-by-one、固定 Code Critic/aggregator contract、syntax preflight。
3. **P1 证据**：typed constraints、deterministic geometry/contact/count verifier、joint pose sampling。
4. **P1 观察**：asset-aware camera policy、scene/interior/容器专用视角。
5. **P1 生命周期**：图片 artifact 外置与 session pruning。
6. **P2 质量**：材质/texture 支持、SpaceControl/Trellis 等高保真阶段的明确可选集成。
7. **P2 工程**：拆分 ObjectWorkflow，建立 benchmark runner 和可复现实验配置。
