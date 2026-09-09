# Code-to-3D × 真实世界可用可打印：调研、代码库诊断与研究路线

> **工具状态**：写作时 WebSearch（该 model 不支持）、WebFetch（域名验证被挡）、subagent（网关
> 格式不兼容，7 次失败）全部失效。**文献部分基于我的领域知识，非本轮新检索**，标 `[待核实]`
> 的请务必查证。**代码库诊断部分是我直接读源码得出的，带行号证据，可靠。**

---

## Context

目标：通用日常物体 → agent 流程 → **在真实物理世界可用、可 3D 打印**的物体。Baseline：aDSL。
你点名四个问题：overhang、slide effect、FEA 应力（承重件）、自重下站立稳定性。
你的两个需求：(1) 从大 benchmark 挖易失效物体构建新 benchmark；(2) 发掘更多 3D 打印问题，
要求便于仿真验证、agent 的 coding→检测→定位修改逻辑完善、易实现。

已有 PDF 调研（截点 2026-08-23）推荐 Candidate B + D，即
`aDSL + deterministic FDM checker + source-grounded localized repair`。我同意大方向，
但读完代码后发现**几个会实质改变方案设计的事实**，先讲这些。

---

# 一、代码库诊断：三个会改变方案的事实

## 1.1 aDSL 的表达力比 PDF 假设的窄得多——只有 3 个几何原语

`export_glb.py:264-379` 的分派只认四种 `type`：

| primitive | 定义位置 |
|---|---|
| `sphere` | `primitives.py:39` |
| `cube` | `primitives.py:65` |
| `cylinder` | `primitives.py:94` |
| `boolean`（伪原语） | `boolean.py:18` |

其他一律 `raise ValueError(f"Unknown primitive type: {t}")`（`export_glb.py:379`）。

**没有 extrude / revolve / sweep / loft / fillet。** 这意味着：

- PDF 第 20 页给的示例定位链 `source node = revolve(profile=shade_profile, ...)`
  → `parameter = flare_angle / lower_radius`，**在 aDSL 里根本不存在**。
  source grounding 的设计必须建立在 `primitive + transform + boolean` 上，而不是 sweep 操作。
- 曲面/有机形状只能用大量 primitive 堆叠近似。audit 里 T05 用 **13 个薄 Cube + boolean** 模拟条纹
  （`reports/adsl_audit_20260830/agent_logic_and_model_defects.md:116`）。
  **这本身就是 printability 失效的来源**：13 个薄片 + 布尔 = 薄壁 + 非流形风险。
- **但这对我们其实是好消息**：CSG + 标量参数是**参数敏感度归因最容易做的情形**。
  每个参数都是有明确几何意义的标量（`radius`、`center`、`scale`、`p0/p1`），
  扰动重执行的语义非常干净。比在 revolve profile 上做归因简单得多。

## 1.2 Boolean 是惰性的 → 程序结构完整保留到导出，provenance 的「源码侧」已经免费拿到

`boolean.py:5-25` 不执行任何布尔运算，只是往树里塞一个节点，并把操作数作为**命名子节点**挂上：

```python
container.attach_part("base", base)          # boolean.py:15
container.attach_part(f"op_{i}", s)          # boolean.py:17
container.add_primitive({"type": "boolean", "params": {"mode": mode}, ...})   # boolean.py:18
```

真正的布尔在导出时才由 Blender 执行（`export_glb.py:290-377`）。因此：

- **整个程序结构以 `Asset` 树的形式活到最后**（`asset.py:44-56`：`label`、`_primitives`、
  `_children`、`_joints`）。
- `export_manifest.py:44-90` 已经把**整棵树连同每个 primitive 的 type/params/xform 和命名层级
  全部序列化成 JSON**。
- `export_glb.py:194-223` 的 `_new_asset_node` 给每个 Asset 节点写自定义属性：
  `adsl_kind`、`adsl_name`、**`adsl_path`（语义层级路径，如 `mug/handle`）**、`adsl_attach_mode`；
  几何对象命名为 `geometry_{primitive_index}_{type}`（`export_glb.py:277,282,287`）；
  且导出时 `export_extras=True`（`export_glb.py:443`）**保证这些属性写进 GLB**。

> **结论：`part → mesh object` 的 provenance 已经存在，不用做。**
> PDF 把 source grounding 当成核心创新，但**在 aDSL 上，part 级定位是免费的**。
> 真正缺的只有 `face → 具体 primitive/parameter`，而且缺口只在布尔之后。

## 1.3 唯一的断点在布尔，而且布尔本身有质量问题——这是最高优先级的工程风险

`export_glb.py:162-192` 的 `_apply_boolean`：

```python
for solver in ("FAST", "EXACT"):        # export_glb.py:170  ← FAST 优先！
    modifier.solver = solver
    bpy.ops.object.modifier_apply(...)
    bpy.data.objects.remove(other_obj, do_unlink=True)   # 操作数被删除，映射关系丢失
```

两个问题：

1. **face → operand 的映射没有记录**，操作数对象被直接删除。这是 provenance 的唯一缺口。
2. **FAST solver 优先于 EXACT**。FAST 是非精确求解器，会产出非流形、自交、零面积面。

第 2 点已经在 audit 里造成了实际损害（`agent_logic_and_model_defects.md`）：

| case | 缺陷 | 行号 |
|---|---|---|
| T02-bookshelf | 4 个 `board+nosing` boolean union 非 watertight，**41 个零面积面** | :113, :135 |
| T04-hollow-mug | 把手非 watertight，**Euler characteristic = -7** | :115, :149 |
| M01-cyberpunk-scratch | power-unit CSG 非 watertight，2 个退化面，**3 个连通分量** | :125 |

审计自己的结论是：「关系算子能改善『放在哪里』，但不能保证 CSG 产物有效」（:135）。

另外 `export_urdf.py:537` 的 `_mesh_from_shape_csg` 走的是**体素化**路径
（`pitch=0.01, max_voxels=2_000_000`）——这会抹掉薄特征、产生阶梯面，
**对 printability 指标是灾难性的**，不能用于制造检查。

> ### ⚠️ 这是本次诊断最重要的结论
>
> **在做任何物理/制造研究之前，必须先修布尔管线**（EXACT 优先，或换 manifold3d / CGAL）。
> 否则你的 benchmark 测的是 **aDSL 布尔求解器的 bug**，不是 agent 的物理推理能力。
> 这正是 PDF 列的 kill signal #5（「失效主要来自 DSL 表达力/运行时而非 physics」），
> 而 audit 数据表明**这个风险已经实现了，不是假设**。
>
> 好的一面：这本身可以成为论文的一个发现——
> 「现有 code-to-3D 管线以 X% 的比例产出非法固体，因此在没有修复层的情况下无法用于制造」。

## 1.4 Agent loop 有会污染 ablation 实验的结构性缺陷

`agent_logic_and_model_defects.md:45-87` 记录了三条，都会直接破坏你的对照实验：

1. **最后一轮必定不跑 Critic**（`:45-55`）：到 `end_round` 就直接 publish，
   `approved=false, critic_skipped=true`，但运行状态写 `completed`。
2. **Image Critic 可以绕过 Code Critic**（`:57-66`）：15 次调用里 12 次由 Image Critic 直接批准，
   Code Critic 只在 5 次里出现。
3. **两个 Critic 循环信任**（`:68-74`）：Image Critic 被要求服从 Code Critic，
   Code Critic 又被要求 `MUST TRUST THE CODE LOGIC`。
   → T04 的假阳性就是这么来的：代码里确实有 cavity，但渲染始终看不到内部，仍被批准。

> **如果不先修这三条，「结构化反馈 vs 自然语言反馈」的 ablation 是有混淆的**
> ——因为轮次预算和 critic 是否运行都不一致。第 1 周就要修。

## 1.5 已有基础设施可以直接复用

- `experiments/adsl_audit_20260830/analyze_cases.py:137-202` 已经在算
  `watertight_after_weld`、`euler_number_after_weld`、`degenerate_face_count`、
  `connected_components_after_weld`、`bounds`、`extents`。**Layer 0 基本已经 instrumented。**
- 注意它有一个正确且重要的细节（`:157-163`）：glTF 每个面角一个顶点，
  **必须先 `merge_vertices` 再判拓扑**，否则一个普通立方体会被误判成 6 个非 watertight 分片。
  这个坑你们已经踩过并解决了，新 checker 必须沿用。
- `adsl-core/pyproject.toml:17` 已依赖 `trimesh`，`mesh` extra 里有 `scikit-image`、`yourdfpy`。
- 没有 STL 导出（只有 `export_glb`/`export_urdf`/`export_manifest`），需要新增，但很简单。

---

# 二、我对 PDF 调研的补充与分歧

## 2.1 【分歧】漏掉 computational fabrication 文献簇，导致「不做 FEA」的论证有漏洞

PDF 的 90+ 条参考几乎全是 2024–2026 的 LLM agent 论文。但成熟、经验证、带 published 阈值的
确定性物理 checker，主要来自 2012 年起的图形学计算制造文献。后果之一是关键论证站不住：

✅ **已核实（Crossref DOI 实拉）**：**Zhou, Panetta, Zorin, *Worst-case structural analysis*,
ACM TOG 2013, DOI [10.1145/2461912.2461967](https://doi.org/10.1145/2461912.2461967)**
——解决的恰恰是「载荷未知」：不指定具体载荷，在容许载荷空间上求最坏情况，输出与载荷无关的
弱点图。**这正是通用物体需要的形式，我的分歧判断成立。**
配套已核实：**Make It Stand**, TOG 2013, DOI [10.1145/2461912.2461957](https://doi.org/10.1145/2461912.2461957)；
**Stress Relief**, TOG 2012, DOI [10.1145/2185520.2185544](https://doi.org/10.1145/2185520.2185544)；
**Ahn et al., *Anisotropic material properties of fused deposition modeling ABS*, Rapid Prototyping J.
2002**, DOI [10.1108/13552540210441166](https://doi.org/10.1108/13552540210441166)（FDM 各向异性的实测依据）。

`⚠️ 仍未核实`：Cross-sectional structural analysis (Umetani & Schmidt, SIGGRAPH Asia 2013)
——截面法这一篇没查到，但**截面思路本身已被下面的 A3+F1 checker 以更好的形式实现**（见 §7.8）。

> **我的判断**：不做**完整 FEA**（四面体化 + CalculiX）是对的。但**轻量结构代理应进第一版**：
> part 连接截面积 + 细长杆欧拉屈曲解析解 + 截面弯曲应力。
> 理由：(a) 你明确要求覆盖承重物体；(b) 三项都是解析/准解析，成本近零；
> (c) 都天然定位到具体 part / 截面；(d) 有 2013 年文献背书。
>
> **而且在 aDSL 上，连接截面积特别好算**——因为 boolean 是惰性的，
> 两个 part 的交叠体积/界面面积可以在树上直接算，不需要等 mesh。

这批文献还提供**修复动作词汇表**（加厚/挖空/加支撑杆/加宽底座/重定向/加圆角/切分），
`[待核实]` Stress Relief (SIGGRAPH 2012) 就是「检测 + 自动修复」。
**这正好做成 agent 的确定性 baseline**，回答 reviewer「为什么不用几何优化器」。

## 2.2 【补充】被低估的一类：slice-level checker，而不是 mesh-level

PDF 的 checker 全是静态 mesh 属性。但 FDM 失效本质是**过程失效**，活在「层」这个维度。
定位粒度天然是 `(layer_index, 2D polygon)`，反查链条比 mesh-level 更短。见 §3 Layer 2。

## 2.3 【补充】最强的 motivating example：薄特征被切片器静默丢弃

agent 生成镂空灯罩，花纹厚 0.3 mm，喷嘴 0.4 mm：mesh 检查全过、渲染很漂亮、CLIP 很高，
但 **G-code 里那些花纹一条路径都没有**。一句话讲清 Gap E，且不是构造的极端案例。

> ### ✅ 已找到「铁证」：slicer 源码原话
> OrcaSlicer `src/libslic3r/PrintConfig.cpp` 的 `min_feature_size` 定义（实际抓取源码）：
>
> > *"Minimum thickness of thin features. Model features that are thinner than this value
> > **will not be printed**, while features thicker than this value will be widened to the
> > minimum wall width. It's expressed as a percentage over nozzle diameter."*
> > —— 默认值 **25%**（0.4mm 喷嘴 → 0.1mm 以下直接消失）
>
> **"will not be printed" 是 slicer 作者的原话。** 这条从我的猜测升级为有源码依据的事实，
> 而且**阈值来自 machine profile，不是拍脑袋的常数**——正好满足论文对阈值溯源的要求。
> 相关 Arachne 参数：`min_bead_width`、`wall_transition_*`；PrusaSlicer 有对应的 `thin_walls`。
> 学术引用：Kuipers et al., *A Framework for Adaptive Width Control of Dense Contour-Parallel
> Toolpaths in FDM*, CAD 2020, DOI [10.1016/j.cad.2020.102907](https://doi.org/10.1016/j.cad.2020.102907)（Arachne 原始论文）。
>
> **还可以做一个干净的端到端验证实验**：(1) 静态预测丢失区域；(2) 实跑 PrusaSlicer CLI
> 解析 G-code 重建每层挤出覆盖区域；(3) 对比 → 「我们的预测与真实 slicer 行为一致」。

**而 aDSL 特别容易触发这个**——因为没有 extrude/revolve，薄结构只能靠堆薄 Cube
（T05 的 13 个薄片就是），厚度参数很容易掉到挤出线宽以下。

## 2.4 【补充】「可用」比「可打印」更适合做 general object 的故事

你的原话是「可用**可打印**」。PDF 有 90% 篇幅讲 printability，但 printability 单独看，
`[待核实]` AgentsCAD (2026-07) 是很强的在先工作。PDF 自己也承认「只是 aDSL → STL →
slicer 说支撑太多 → LLM 减支撑，投稿风险很高」。

而**通用日常物体的功能可用性**拥挤度低得多，更贴 general object，且同样确定性。见 §3 Layer 3。
其中 **wobble（四脚不共面）是一颗宝石**：极常见、纯几何检测、
**定位精确到单个 part 单个参数**（`leg_3` 的 `p1[2]` 差 1.8 mm）、修复是改一个数。
在 aDSL 里椅腿就是 Cylinder 的 `p0/p1`，定位链条短到不可思议。

## 2.5 【补充，我认为最重要】「为什么需要 Agent」的最强回答 = 约束冲突

PDF 给的 4 条理由都偏软。最硬的是：

> **这些约束彼此真实冲突，而化解冲突需要「这个物体是干什么用的」这一语义知识。**

| 修 A | 副作用 → 破坏 B |
|---|---|
| 加厚薄壁修可打印性 | 质量↑ → CoM↑ → 稳定性失败 |
| 加宽底座修稳定性 | 违反 prompt 语义（「纤细的花瓶」） |
| 内偏移加厚杯壁 | **把手孔被堵死** → 手指伸不进，功能丧失 |
| 重定向以消除支撑 | 「预期放置平面」变了 → 换一组稳定性问题 |
| 挖空减重 | 产生封闭腔 → 支撑取不出来 |
| 加大圆角减应力集中 | 吃掉配合间隙 → 盖子合不上 |

**几何优化器会很乐意把马克杯的把手孔填平来消除 overhang。** 只有知道「一根手指必须穿过这个孔」
的东西才会正确取舍——这个语义约束来自 prompt，不来自几何。

> **这条应反过来驱动 benchmark 设计**：每个物体必须至少有一个失效，
> **其朴素修法会破坏另一个约束**。否则确定性 baseline 全解，agent 没有存在理由
> （PDF kill signal #2）。这是我对 benchmark 最重要的建议。

## 2.6 Teaser 已经在你们硬盘上了：T04-hollow-mug

我原本想设计一个马克杯 teaser，结果发现 **audit 的 T04 已经是了**
（`agent_logic_and_model_defects.md:115, 137-149`）：

- 源码里有 cavity（outer cylinder 减 cavity cylinder），程序层面壁厚、开口、杯底都在
- 但两轮八视图**始终看不清内部**
- 把手由多段 cylinder/sphere 合并，**非 watertight，Euler characteristic = -7**
- Image Critic 两次都指出问题，Code Critic 最终以「代码里有 boolean cavity」为准**批准了**

再叠加物理检查，这一个杯子可以同时踩中：壁厚 vs 喷嘴、把手下缘 overhang、
把手圈内支撑难移除、把手-杯体连接截面积、杯口平面度、装满水后 CoM 上移失稳。
**六个失效、全部确定性可检、全部渲染看不出来、且修复彼此冲突。现成的第一张图。**

---

# 三、失效模式全景：五层分类法

按**失效活在哪一层**组织，因为层次直接决定检测成本、定位粒度、修复词汇表、
以及**这一层需不需要 agent**。

### Layer 0 — Mesh 表示层（不是合法固体）

| 失效 | 检测 | 定位 | 难度 | agent 价值 | aDSL 现状 |
|---|---|---|---|---|---|
| non-watertight / 开边 | trimesh `is_watertight` | edge IDs | 1 | 低 | **已有** |
| non-manifold | CGAL PMP / Blender | edge/vertex | 1 | 低 | 部分 |
| self-intersection | CGAL PMP | **triangle pairs** | 1 | 低 | 无 |
| 退化面（零面积） | 面积阈值 | face IDs | 1 | 低 | **已有** |
| 断开/浮空组件 | 连通分量 | component | 1 | **中**（需分辨合法 assembly） | **已有** |
| Euler characteristic 异常 | trimesh `euler_number` | 全局 | 1 | 低 | **已有** |

→ 纯 gate，**不是创新点**。但注意 §1.3：aDSL 自己的布尔就在大量制造这一层的失效，
**必须先修管线，否则这一层的失效率会淹没所有其他层**。

### Layer 1 — 尺度与工艺契约层

| 失效 | 检测 | 定位 | 难度 | agent 价值 |
|---|---|---|---|---|
| **单位/尺度歧义** ⭐ | 包围盒 vs 类别常识区间 | 程序尺寸常量 | 1 | **高** |
| 超出 build volume | 包围盒 vs profile | 全局 | 1 | 中 |
| 最小壁厚 < N×挤出线宽 | local thickness (SDF/ray) | violating surfaces | 2 | 高 |
| **薄特征被切片器静默丢弃** ⭐ | 切片前体积 vs 挤出路径体积 | layer + 区域 | 2 | **最高** |
| 最小孔径 / 最小柱径 | 特征识别 + 量测 | 面片环 / part | 3 | 中 |

⭐ **单位歧义是 code-to-3D 特有的坑，PDF 完全没提**。aDSL 程序里 `radius=1.0` 是 1 mm 还是 1 m？
生成式程序常常没有单位概念。检测极简，定位到程序常量，修复是改一个 scale。
**而且它会污染下游所有物理检查**——不先钉死单位，稳定性和壁厚检查全是噪声。
建议作为第一道 gate。

### Layer 2 — 切片/过程层

| 失效 | 检测 | 定位 | 难度 | agent 价值 |
|---|---|---|---|---|
| overhang 角度/面积 | 面法向 vs build dir；或 slicer | face IDs + 角度 | 2 | 高 |
| **island（悬空起印）** ⭐ | 第 k 层与 k-1 层无重叠 | **layer + polygon** | 2 | 高 |
| bridge span | slicer bridge 分类 | layer + 线段 | 3 | 中 |
| 支撑体积/接触面积/悬空面积 | slicer（三个**不同**指标） | 支撑体 | 2 | 高 |
| **支撑可达性/可移除性** ⭐ | 支撑体到外部连通性 + 通道直径 | 支撑连通分量 | 3 | **最高** |
| 封闭空腔 | 体素 flood fill | 腔体 | 2 | 高 |
| 每层截面积突变 | 逐层面积一维信号 | layer | 2 | 中 |
| 首层附着面积 vs 总高 | 底面积 + 高度 | 全局 | 1 | 中 |
| 打印中倾覆 | 长细比 + 已打印质心 | layer | 2 | 中 |
| slicability（硬 gate） | slicer CLI | pass/fail | 1 | — |
| 打印时间/材料 | slicer 估计 | 全局 | 1 | soft objective |

⭐ **支撑可达性**特别推荐：支撑生成在把手圈内、镂空球内、深窄槽里 → 人手工具够不到，
等于打印失败。纯确定性连通性问题，日常物体高频触发，**修复选项多样**
（改朝向/改开口/分件/改造型），正是 agent 该决策的地方。

### Layer 3 — 静力学/功能层（打出来了，但用不了）

| 失效 | 检测 | 定位 | 难度 | agent 价值 |
|---|---|---|---|---|
| 自重下站不稳 | CoM 投影 vs support polygon | CoM + 多边形 | 1 | 高 |
| **四脚不共面 → 晃 (wobble)** ⭐ | 接触点集平面度 | **哪条腿差几 mm** | 1 | **最高** |
| 倾覆角过小 | 绕多边形各边倾倒角 | 最弱边 | 2 | 高 |
| **承载物滑动（你的 slide effect）** | 摩擦锥 `tanθ > μ` | 承载面 + 倾角 | 1 | 高 |
| 自身滑动 | μ vs 支撑面倾角 | 接触面 | 1 | 中 |
| **settling 姿态 ≠ 预期姿态** ⭐ | 丢地面沉降，比对姿态 | 姿态 | 2 | 高 |
| 容器容积 / 是否漏 | 重力方向 flood fill | 内腔面片 | 2 | 高 |
| **杯口平面度** | rim 环共面性 | rim 面片环 | 2 | 高 |
| **装满后失稳** ⭐ | 液体质量并入 → 重算倾覆角 | CoM | 2 | **最高**（耦合） |
| 把手伸不进手指 | 标准手指圆柱 clearance query | 把手内环 | 2 | 高 |
| 挂钩脱钩 | 开口宽度/挂载力矩/脱钩角 | 钩尖 | 3 | 中 |
| 堆叠/盖合 | 接触与间隙查询 | 配合面对 | 3 | 低（可选） |

⭐ **装满后失稳是耦合检查的最佳范例**：把「容器功能」和「静力学稳定」绑在一起，
naive 修法必然顾此失彼。直接服务 §2.5。

### Layer 4 — 结构层（承重会坏）

| 失效 | 检测 | 定位 | 难度 | agent 价值 |
|---|---|---|---|---|
| **part 连接截面积过小** ⭐ | 界面面积（aDSL 上可在树上直接算） | **具体连接对** | 2 | **最高** |
| 细长杆屈曲 | 欧拉临界载荷解析解 | 具体杆件 | 2 | 高 |
| 悬臂根部弯曲应力 | 截面法 `σ = M·c/I` | 根部截面 | 3 | 高 |
| 长跨度挠度 | 梁公式 | 跨中 | 3 | 中 |
| 应力集中 | 曲率 + 截面积梯度 | 面片区域 | 3 | 中 |
| 层向 vs 主应力方向 | 夹角（各向异性廉价代理） | 全局/区域 | 3 | 中 |
| 完整 FEA | 四面体化 + CalculiX | 单元级 | 5 | **第一版不做** |

⭐ **连接截面积是性价比最高的结构 checker**：真实世界最常见的断裂位置就是连接处
（杯把、钩根、腿与座）。检测纯几何，**定位精确到「哪两个 part 的连接」**。
而且可以用「截面积 < 类别经验阈值」而非「应力 > 屈服强度」表述，
**完全绕开「load case 从哪来」的难题**。

---

# 四、回答问题一：还有什么 3D 打印问题值得做

在你已有四项之外，按 `(定位质量 × 触发率 × 实现难度 × 新颖度)` 推荐这 10 项：

| # | Checker | 为什么值得做 | aDSL 上的触发源 |
|---|---|---|---|
| 1 | **薄特征被切片器静默丢弃** | 最强 motivation：mesh 过、渲染过、CLIP 过，东西不存在 | 没有 extrude，薄结构靠堆薄 Cube |
| 2 | **四脚共面性 (wobble)** | 定位精度最高（单腿单参数），触发率极高，难度 1 | 腿是 Cylinder 的 `p0/p1` |
| 3 | **支撑可达性/可移除性** | 确定性连通性，高频触发，修复选项多样 | 布尔挖出的腔体 |
| 4 | **单位/尺度契约违反** | code-to-3D 独有，不先钉死会污染所有下游检查 | 程序无单位概念 |
| 5 | **part 连接截面积** | 结构失效最便宜代理，绕开 load case 难题 | 惰性布尔，树上可直接算 |
| 6 | **容器功能三件套**（容积/漏/杯口平面度） | 「可用」的最好体现，纯几何 | T04 已经是现成案例 |
| 7 | **装满后稳定性** | 耦合检查最佳范例，直接论证 agent 必要性 | — |
| 8 | **island detection** | FDM 经典，(layer, polygon) 定位 | 悬空 primitive |
| 9 | **封闭空腔** | 确定性 flood fill | 布尔挖空 |
| 10 | **settling 姿态检验** | 廉价，抓「以为自己站着」的错误 | 无重力概念 |

**明确不做**（第一版）：完整 FEA、疲劳、热翘曲仿真、各向异性本构、print-in-place、
自动分件+连接件生成、支撑生成算法本身、跌落动力学、训练 VLM 判断壁厚/overhang。

---

# 五、回答问题二：怎么构建新 benchmark

## 5.1 关键认识：输入是 prompt，不是 mesh

pipeline 是 `text → program → mesh`，所以**要构建的是「容易诱发失效的 prompt 集」**，
不是「易失效 mesh 集」。已有 mesh 数据集的作用是**校准**，不是直接做 benchmark 内容。

## 5.2 三步法：Mine → Author → Validate

**Step 1 — Mine（用 mesh 数据集标定风险区）**

| 风险量 | 用什么算 | 预示 |
|---|---|---|
| 长细比 | 包围盒 / PCA | 屈曲、打印中倾覆 |
| 最小局部厚度分布 | shape diameter function / SDF | 薄壁、特征丢弃 |
| 悬垂面积比例 | 面法向 vs +Z | overhang / 支撑量 |
| CoM 高度 / 支撑多边形尺寸 | trimesh mass properties + 凸包 | 稳定性 |
| 接触点共面性残差 | 底部面片聚类 + 平面拟合 | **wobble** |
| part 连接截面积 | part 分割 + 界面面积 | 连接断裂 |

数据集：**ABO**（有真实物理尺寸，对「单位契约」最有价值）、**Thingi10K**
（真实打印意图 + defect 统计 `[待核实具体数字]`）、**PartNet**（part 语义）、
**ShapeNet**（aDSL 自己用了 60 个实例，便于对齐）。

**Step 2 — Author（把风险配置写进 prompt + 轻量 reality contract）**

写「一把腿很细的高脚凳」而不是「一把椅子」：

```yaml
prompt_id: stool_slender_01
prompt: "A tall bar stool with four slender legs and a round seat"
units: mm
intended_ground_plane: -Z
process: FDM
printer_profile: <fixed>
nozzle_mm: 0.4
material: PLA
build_volume_mm: [250, 210, 220]
functional_constraints:                  # ← 见 §5.4，反 gaming 的关键
  - height_mm: [650, 750]
  - seat_must_support_load_N: 500
  - legs_coplanar_within_mm: 0.5
  - must_have_n_legs: 4
```

**Step 3 — Validate（跑 baseline 测 headroom）**
目标是每个 checker 有 **20–60% 触发率**：太低没 headroom（kill signal #1），
太高说明 baseline 太弱、结论不可信。

> **你们已经有起点了**：audit 的 15 次调用里 3 个已批准资产带真实 non-watertight/退化几何
> （≈20%）。但那是 **Layer 0**，是最不有趣的一层，而且**主要是布尔求解器的锅**（§1.3）。
> 真正要测的是修完布尔管线之后，**Layer 2–4** 的失效率。

## 5.3 高风险类别 × 失效模式配对表

| 类别 | prompt 方向 | 预期失效 | 层 |
|---|---|---|---|
| 马克杯（带把手） | "with a slender handle" | 把手 overhang、把手圈内支撑难移除、连接截面积、杯口平面度、壁厚 | 1,2,3,4 |
| 高脚杯 | "tall stemmed" | 细杆屈曲、稳定性 margin 小、装满后失稳 | 3,4 |
| 花瓶（细颈） | "narrow neck, wide body" | 内腔支撑取不出、薄壁、漏 | 1,2,3 |
| 镂空灯罩 | "intricate cutout pattern" | **薄特征被丢弃**、overhang、薄壁 | 1,2 |
| 台灯（悬臂） | "with an angled arm" | 悬臂弯曲、稳定性、overhang | 2,3,4 |
| 细腿椅/高脚凳 | "four slender legs" | **wobble**、屈曲、稳定性 | 3,4 |
| 三脚凳 | "three-legged" | 稳定性 margin（三点必共面 → **不**触发 wobble，好对照组） | 3 |
| 书架/书立 | "long shelf span" | 跨中挠度、承载物滑动、倾覆 | 3,4 |
| 手机支架 | "angled phone stand" | **承载物滑动 (slide effect)**、倾覆、overhang | 2,3 |
| 挂钩 | "wall hook" | 悬臂应力、脱钩、overhang | 2,3,4 |
| 带盖收纳盒 | "box with fitted lid" | 配合间隙、封闭腔、首层附着 | 1,2,3 |
| 镂空球/灯笼 | "hollow sphere with holes" | **内部支撑无法移除**、island、薄壁 | 2 |
| 提篮 | "basket with a handle" | 把手连接、手指 clearance、overhang | 2,3,4 |
| 浇水壶 | "watering can with spout" | 容积、漏、壶嘴 overhang、装满后失稳 | 2,3 |
| 烛台 | "tall candlestick" | 长细比、打印中倾覆、稳定性 | 2,3 |
| 手工具 | "hand tool with grip" | 手柄 clearance、强度、层向 vs 载荷向 | 3,4 |
| 塔状装饰 | "tall thin tower" | 打印中倾覆、翘曲、首层附着 | 2 |
| 桌（细腿） | "table with thin legs" | wobble、屈曲、跨中挠度 | 3,4 |
| 首饰/眼镜架 | "delicate display stand" | 薄特征丢弃、稳定性、屈曲 | 1,2,3,4 |
| 笔筒 | "cylindrical pen holder" | 壁厚、稳定性（**低风险对照组**） | 1,3 |

建议 **8–12 类、30–60 个 prompt**，每类放 2–3 个难度档（低风险对照/中/高），
以便画难度-通过率曲线而不只给一个标量。

## 5.4 【最重要的方法论建议】把语义保真度变成可执行 predicate

PDF 担心「agent 把所有东西改成大方块刷 printability」，但没给确定性解法（CLIP/DINO 不确定）。
我的建议是把 CADTests 的思路**正确地**迁移到通用物体：

> **在 reality contract 里显式声明可验证的功能约束，让语义保真度本身成为一组确定性 predicate。**

```yaml
functional_constraints:
  - handle_must_admit_cylinder_mm: 20      # 手指能穿过
  - must_hold_volume_ml: [200, 300]        # 真的是个杯子
  - height_mm: [90, 120]
  - must_have_n_legs: 4
```

好处：**大方块过不了功能 predicate** → 反 gaming；judge 不是 LLM；**不需要 reference mesh**
（绕开「一把能打印的椅子有无数正确形态」）；且天然构成 §2.5 的**约束冲突**。

这一条比多加几个 checker 重要得多。注意它和 audit 的 P1 建议
（`agent_logic_and_model_defects.md:271`「Planner 输出 typed constraints，例如 Count、Contact、
Aligned、JointMotion」）方向一致——你们团队已经想到了，我建议把它扩展到物理/功能维度。

## 5.5 Mutation testing：先验证 checker，再相信失效率

对已知良好模型注入已知失效，检查 checker 能否抓到：缩短一条腿 0.5/1/2 mm（wobble 灵敏度曲线）、
壁厚 ×0.4、把手分离 2 mm、引入 60° overhang、删底座、特征厚度降到 0.3 mm、内腔封死。

**副产品很值钱**：这给出一组**已知 ground-truth 定位**的样本，
可以直接量化「source grounding 定位准不准」，**不需要人工标注**。

## 5.6 Reality contract 要钉死的东西

单位、process、printer profile、nozzle、层高、挤出线宽、材料密度与摩擦系数、build volume、
重力方向、intended ground plane、支撑策略与阈值角度。全部固定并随 benchmark 发布；
**并做一次 profile 敏感性分析**（换 2–3 个合理 profile 看排名是否稳定）写进附录，
主动堵住 kill signal #6。

---

# 六、回答问题三：我建议的研究框架

## 6.1 论文定位

PDF 的提法是 *General 3D Coding Agents with Source-Grounded Deterministic Printability Feedback*。
我建议往「冲突」上收紧：

> **Physically-Grounded Program Repair for General 3D Objects
> under Conflicting Manufacturability and Usability Constraints**

把「可打印」扩到「可打印**且**可用」，并把「约束冲突」提到标题层面。三个好处：
与 AgentsCAD（单件、已有 STEP、只管 overhang、朝向搜索能解大半）拉开距离；
「冲突」直接回答「为什么要 agent」；「可用」让 general everyday object 这个设定变成**必要的**
——只有日常物体才有丰富的功能约束。

## 6.2 三个贡献

1. **五层确定性 reality checker suite + 结构化 source-grounded 诊断**
   （不只 printability，还有 usability；不只 pass/fail，而是带 part/region/参数的 typed failure）
2. **一个约束真实冲突的 benchmark**：每个物体至少有一个失效其朴素修法会破坏另一个约束；
   语义保真度以可执行 predicate 表达 → judge 不是 LLM，也不需要 reference mesh
3. **source-grounding 机制及其有效性证据**：结构化+定位的反馈 vs **等信息量但无定位**的反馈，
   在 pass rate / repair rounds / source diff locality / 语义保持上的差异

**注意贡献 3 的措辞要谨慎**：§1.2 表明 aDSL 上 **part 级定位是免费的**。
所以我们的增量不是「实现了 part 级定位」，而是
**「face/region/layer 级证据 → 具体 primitive 与参数」这一段，以及它是否真的提升修复**。

## 6.3 必须设的 baseline

| Baseline | 目的 |
|---|---|
| 原始 aDSL（视觉 critic loop） | 下界 |
| **确定性 heuristic 修复**（朝向搜索 + 加厚 + 加宽底座 + 腿长对齐） | 回答「为什么不用几何优化器」 |
| generic-feedback LLM agent（自然语言失效描述，不给定位） | **等信息量对照** |
| **source-grounded agent**（我们的） | — |

第 2 个 baseline 取自 §2.1 的经典修复动作词汇表，有文献背书。
**如果它就能解掉 80%，我们应该及早知道并转向**（kill signal #2）。

## 6.4 推荐的技术路线：provenance 怎么做

基于 §1.1–1.3 的代码事实，我推荐**三层递进**，前两层就够第一篇：

1. **Part 级（免费，已有）**：`adsl_path` + `export_manifest` 的树。
   → 「哪个 part 有问题」直接可得。
2. **Primitive 级（要做，但简单）**：布尔前给每个操作数 mesh 写 face attribute，
   布尔后做**最近源面片归因**（对每个输出面片，在原操作数 mesh 里找最近面片）。
   因为**布尔是惰性的，操作数可以重新求值**（§1.2），这个方案不依赖 Blender 内部行为，
   鲁棒且好实现。
   > **调研 agent 独立收敛到同一方案**：布尔前记录各 part 三角形质心，布尔后用
   > `trimesh.proximity` 重新打标，并为 CSG 新生成的交线面定一个归属规则。
   >
   > 备选：实测 Blender boolean 是否传播 custom face attribute（本机 `bpy 4.0.0`）。
   > **⚠️ 此项至今未实测**（分类器不可用，脚本已备好在 `/tmp/bool_attr_test.py` 待跑）。
   > 需回答四个子问题：(a) boolean 后 `part_id` 还在吗 (b) A 面是否仍带 101、B 面仍带 202
   > 还是被置 0 (c) 交线新生成的面拿到什么值 (d) domain 是否仍为 FACE。
   > **不要把方案押在这上面**——若不可靠就走上面的质心 + proximity 兜底。
   >
   > 另需确认：`bmesh.ops` 里**可能根本没有 boolean 算子**。若没有，程序化 pipeline
   > 只能走 modifier 路径或换外部内核（`manifold3d` / CGAL）。

   > ### ⚠️ 已核实的实现风险：`mesh_multiplane` 只给 segment → face，不给 polygon → face
   > 调研 agent 在 **trimesh 4.11.2** 上实测确认：`trimesh.intersections.mesh_multiplane`
   > 能回传**线段级**的源 face_index（一对一），但**闭合多边形 → face 集合**这一步要自己做
   > ——`shapely.ops.polygonize` **不保留附加属性**，必须在 polygonize 时手工把 face_index 带下去。
   >
   > **这是所有 slice-level checker 实现里唯一有实质风险的一步。**
   > 建议第一周优先打通并写单测。兜底路径同样是 `trimesh.proximity` 最近面反查
   > （对 G-code 坐标也只能走这条，因为 G-code 里没有 face 信息）。
3. **Parameter 级（第一篇的方法核心）**：对每个 named scalar 参数做有限差分扰动、重执行、
   看哪些面片移动 → `face × parameter` 敏感度矩阵。
   **aDSL 特别适合**：只有 3 个原语，参数全是有明确几何意义的标量（§1.1）。
   还顺带给出修改方向的符号。

## 6.5 两周 pilot

**第 0 步（1–2 天，前置，不可跳过）**
0. **环境前置**：`pip install shapely`（第一周全部 slice-level 工作的依赖，本机未装）；
   **锁 `trimesh==4.11.2`**（已有验证基于此版本，5.0.0 的 `mesh_multiplane` 签名未验证）；
   `pip install thingi10k`（支持 §7.3 的万级触发率实验）。
1. **修布尔管线**：`export_glb.py:170` 的 solver 顺序改成 EXACT 优先；
   评估换 manifold3d。重跑 audit 的 12 个 case，看 Layer 0 失效率降到多少。
   **这一步决定后面所有数字可不可信。**
2. **修 agent loop 的三个混淆**（§1.4）：每轮必跑 verifier、Image Critic 不得绕过、
   去掉 `MUST TRUST THE CODE LOGIC`。
3. 加 STL 导出。

**第 1 周——只做 checker，不碰 agent**
```
aDSL → 每 part mesh + 合并 mesh → [Layer 0 + 1 + 3 静力学] → 结构化 JSON
                                → PrusaSlicer CLI → [Layer 2] → 结构化 JSON
```
在 30–50 个 prompt 上跑通，**统计原始 aDSL 到底失败多少**；同时用 §5.5 的 mutation testing
验证 checker 敏感度。**这步比 agent 重要——它决定方向有没有东西可修。**

**第 2 周——provenance 原型 + 四组对照**
§6.4 的第 1–2 层 + §6.3 的四个 baseline，每组最多 3 轮修复。

**关键实验不是「有反馈 > 没反馈」，而是「有定位的反馈 > 等信息量但无定位的反馈」。**

## 6.6 风险（按可能性排序，已用代码证据更新）

1. **【已确认，最高】失效主要来自 aDSL 布尔求解器，而非 agent 的物理推理。**
   证据：FAST solver 优先（`export_glb.py:170`）+ audit 的 41 个零面积面、Euler -7、
   3 个连通分量。**必须先修，否则论文变成 DSL debugging**（kill signal #5）。
2. **【已确认】ablation 被 agent loop 缺陷混淆**（§1.4）。必须先修。
3. **【中】朝向搜索 + 简单加厚就解掉大部分** → agent 无必要。
   §5.4 的功能约束是制造「无法被单一 heuristic 解掉的冲突」的手段。
4. **【已降低】provenance 做不出来。** §1.2 表明 part 级已免费，风险比 PDF 预期的小得多。
   真正的不确定性只在 parameter 级归因。
5. **【中】Checker 不敏感导致失效率虚低** → §5.5 的 mutation testing 是解药。
6. **【新】aDSL 表达力天花板**（§1.1，只有 3 个原语）可能导致某些 prompt 类别
   （有机曲面的花瓶、灯罩）根本生成不出合理形状，失效不是「物理错误」而是「表达力不足」。
   选 prompt 类别时要避开，或明确区分这两类失效。

---

# 七、已核实的调研发现（一个 subagent 经 arXiv API / curl 实际核实）

> 来源：调研 agent 用 shell curl 直连 arXiv API 与 Crossref 核实。**该 agent 完成时安全分类器
> 不可用，其输出未经复核**；下列 DOI/arXiv 号请在引用前自行再确认一次。

## 7.1 三个必须引用的新 related work

| 工作 | 与我们的关系 |
|---|---|
| **LLM-ADAM**, arXiv 2605.03328 — Eslaminia et al., *A Generalizable LLM Agent Framework for Pre-Print Anomaly Detection in AM* | 三角色 LLM（Extractor/Reference/Judge）检 G-code **参数**异常（欠挤出/过挤出/warping/stringing），200 条 FFF 语料，87.5% vs 单 LLM 59.5%。摘要原话 *"A syntactically valid slicer profile can still encode thermally or geometrically harmful settings"* ——**这是我们「语法合法但物理有害」论点在参数侧的版本**。<br>**区别务必写清**：它查参数不查几何、用 LLM 判断不用确定性算法、不闭环改源码。干净的互补关系。 |
| **Correct-by-Construction G-Code Generation**, arXiv 2605.10568 — Yeonseok Lee | 用 Separation Logic 把物理碰撞形式化为 "Spatial Data Races"，**证明失败浓缩成 bounding box 喂回 LLM 迭代自纠**。这个范式与我们同构，是最好的方法论对照。<br>⚠️ 单作者 v3 预印本，引用了无法确认的 "GLLM"。**只作为「相近思路的预印本」提及，不当既立 baseline。** |
| **MUSE**, arXiv 2605.28579 — Dong, Li, Wu — *Benchmarking Manufacturable, Functional, and Assemblable Text-to-CAD* | **我们最该对标的 benchmark。** 它批评现有工作 *"evaluate them using geometric similarity metrics that fail to capture functionality, manufacturability, and assemblability"*——和我们动机一致。但它的三阶段协议里，**manufacturability 那一阶段用的是 LLM rubric 打分**（主观、不可复现）。<br>→ **我们的 positioning 可以直接写成：用确定性 checker 取代 rubric，使 manufacturability 成为可复现、可定位、可自动化的度量。建议写进 intro。** |

## 7.2 【重要】发现一个此前不知道的直接竞品，但它的几何层极其单薄

arXiv **2608.22128** — ***Task-Driven 3D Printability Assistance via Geometry- and
Knowledge-Grounded LLM Reasoning*** — Zhaoda Du, Qiaojie Zheng, Xiaoli Zhang，**2026-08-22**，cs.AI
（标题/作者/摘要经 arXiv API 直拉验证）。结果：STL + 自然语言任务 → 结构化推荐，
**96 次实物试打，printability 75.0%**，成功件 task suitability 88.9%。

其 Geometry-Grounded Layer **总运行时间仅 0.003 秒**（对比 KG 推理 5.35s、LLM 40.5s）：

- Mesh 级：尺寸/面积/体积 + watertightness/boundary edges/non-manifold/degenerate
- 方向打分：**只评 6 个轴对齐方向**，4 个加权指标（support-risk / base-contact / slenderness /
  height-dominance），权重是 "empirically determined"
- 纯规则触发的文字警告

**它没有的（逐条对应我们的机会）**：无任何壁厚测量、**无 per-face overhang 角度分类**（只有一个
聚合的朝下面积比）、无 self-intersection、**完全没有切片没有 G-code**、方向只有 6 个轴对齐。
**benchmark 仅 4 个 STL × 2 种任务描述 = 8 个场景。**

它自己的 Related Work 还承认既有 rule-based checker 已覆盖 build volume / minimum feature size /
wall thickness / overhang / support / mesh validity——**即它实现的检查比它引用的先行工作还少**。
它真正的价值是 96 次实物验证（75% printability），那才是我们该对标的数字。

## 7.3 【高价值实验】Thingi10K 有官方 PyPI 包 → 万级规模实验几乎免费

`pip install thingi10k`（1.5.0, 2026-08-18）可程序化加载筛选。

> **建议做的实验**：拉全量 10,000 个模型，跑一遍全部 checker，报告**每个 checker 的真实触发率
> 分布**。这既用真实数据回答了「预期触发率」，又能与 Thingi10K 原文的 non-manifold /
> self-intersection 统计形成呼应。
>
> **规模对比本身就是一张图**：竞品 2608.22128 用 **4 个模型**，AgentsCAD 用 **1 个 birdhouse**，
> 我们用 **10,000**。

注意：Thingi10K 是「真实打印意图的 mesh」，用途是**标定 checker 触发率与阈值**，
不是我们的主 benchmark（我们的输入是 prompt，见 §5.1）。两者角色不同，别混。

## 7.4 生态空白（可写进 intro 的证据）

- **G-code 分析生态基本为空**：GitHub 搜 `gcode analysis` 按 star 排，**total_count 仅 26**，
  最高 208 stars 且只是打印时间估计插件。`pygcode` **停更于 2017**，`ArcWelderLib` 停更 2024，
  唯一活跃的是 `gcodeparser` 0.3.0。→ **支撑「必须自建 `;TYPE:`-aware 分析器」的判断。**
- **FDM 翘曲在 arXiv 上几乎不存在**：`abs:"warpage prediction"` 全库 **1 条**，且是半导体封装，
  与 FDM 无关。→ **G1 翘曲只能做几何 proxy，绝不承诺定量预测。** 但也正因空白，
  一个诚实的 proxy + 实证相关性分析反而有发表价值。
- `abs:"text-to-CAD"` 全库 **31 篇**，2026 集中爆发 → **投出去时这个领域会很拥挤，
  用「确定性可制造性 checker + 源码闭环」区分是必要的。**

## 7.5 Blender 3D Print Toolbox 的厚度检查其实很粗糙 → 我们的改进可以明确主张

agent 读了源码：厚度检查是**每个面取 6 个随机重心采样点**（`bmesh_face_points_random`,
`random.seed(f.index)`, `margin=0.05`），沿 `-normal` 投射 `thickness` 距离；
self-intersection 用 `BVHTree.overlap(自身)`；`threshold_zero = 0.0001`；**厚度阈值硬编码 1mm**。

→ 业界参考实现是**随机采样的、有偏的、阈值不可溯源的**。
我们用 slice-level 的确定性形态学开运算在**精度、可复现性、阈值溯源**三方面都是严格改进，
这是可以写进论文明确主张的。

## 7.6 已核实的 DfAM 规则来源（替换我 §2.1 里的模糊引用）

- **Thompson et al., *Design for Additive Manufacturing: Trends, opportunities, considerations,
  and constraints*, CIRP Annals 2016**, DOI 10.1016/j.cirp.2016.05.004
  → **DfAM 领域的规范性综述，建议作为 DFAM 规则的主引用**
- Pei, Rosen, Seepersad, *Design Rules*, ASM Handbook 2023, DOI 10.31399/asm.hb.v24a.a0006948
- **ISO/ASTM 52902**（Test artifacts — Geometric capability assessment，2019，2023 修订）
  与 **ISO/ASTM 52910:2018**（Design — Requirements, guidelines）
  ⚠️ **条款级内容仍需买标准原文核对**，不能只凭标题引用具体尺寸
- Ghadai et al., *Learning localized features in 3D CAD models for manufacturability analysis of
  drilled holes*, CAGD 2018, DOI 10.1016/j.cagd.2018.03.024
- Hu, Jin, Wang, *Support slimming for single material based AM*, CAD 2015,
  DOI 10.1016/j.cad.2015.03.001
- support removability 主引用：arXiv **2107.07686** *Optimizing Build Orientation for Support
  Removal using Multi-Axis Machining*
- 其他已验证 ID：2102.10013 (Curvy)、2511.09298 (DensiCrafter)、**2405.18515 (Atlas3D)**、
  1906.03027 (CrossFill)、2408.14307 (LLM-3D Print)、2605.10873 / 2605.10865 (CADBench/BenchCAD)

## 7.7 工程侧修正（影响排期）

| 项 | 修正 |
|---|---|
| `pyslm` | ❌ PyPI 上不存在。真实名是 **`PythonSLM`** v0.6.1，`import pyslm`，GitHub `drlukeparry/pyslm` |
| `trimesh` | 最新 5.0.0，**本仓库环境是 4.11.2**。⚠️ 跨大版本升级需验证 `mesh_multiplane` 返回签名。**先锁 4.11.2 完成第一版** |
| `admesh` Python 绑定 | 停更于 2018 → **用 CLI 版** |
| `shapely` | 2.1.2 存在，需 `pip install` |
| `thingi10k` | **1.5.0 有官方 PyPI 包** |
| `raw.githubusercontent.com` | 本环境**被屏蔽**；改用 `api.github.com/repos/.../contents/PATH` + `Accept: application/vnd.github.raw`（未认证 60 次/小时） |
| arXiv API | 本 IP 快于 ~1 次/25 秒会 429 |

---

# 七点八、已核实的 checker 清单与 slicer 接口结论

> 全部由调研 agent 用 `curl` 直连 GitHub API / arXiv API / Crossref **实际抓取源码验证**。

## 7.8.1 【关键发现】Blender 3D Print Toolbox 的 7 个检查**全部在 mesh 层，零个 slice-level**

实测源码 `blender/blender-addons/object_print3d_utils/`：`check_solid`（非流形边）、
`check_intersect`（`BVHTree.overlap` 自交）、`check_degenerate`、`check_distort`、
`check_thick`（**每面 6 个随机重心采样点** ray_cast）、`check_sharp`、`check_overhang`（面法向）。
默认阈值：**`thickness_min=0.001`（硬编码 1mm，与 nozzle/machine profile 无关）**、
`threshold_zero=0.0001`、`angle_overhang=45°`。

→ **直接证实了我 §2.2 的判断**：业界参考实现全停在 mesh 层，厚度检查是随机采样、有偏、
阈值不可溯源的。**「slice-level 是空白」+「阈值必须来自 machine profile」是两个可主张的贡献点。**

## 7.8.2 推荐的 8 个 checker（含实现路径与已验证阈值来源）

| # | Checker | 实现 | 定位粒度 | 阈值来源（已验证） | 难度 |
|---|---|---|---|---|---|
| ① | **Island / 悬空孤岛** | `mesh_multiplane` → shapely 成环 → 与 L−1 层 union 求交为空 | layer + polygon + **face IDs** | `layer_height`；`nozzle²` 滤噪 | 2 |
| ② | **薄特征静默丢失** | 形态学开运算 `buffer(-w/2).buffer(+w/2)` 求差 | layer + 丢失区域 + face IDs | **`min_feature_size`=25%×nozzle** | 2 |
| ③ | **Support 不可达** | 体素 → `binary_opening(ball(r_tool))` → `label` → 与外部不连通 | 体素区域 + 最近 face IDs | 工具半径（镊子~1mm/钳~3mm） | 3 |
| ④ | **封闭腔 + 排料孔** | 体素 → 边界 flood fill → 未填充连通域 | 腔体 bbox + 包围 face IDs | 存在性；工艺条件化 | 2 |
| ⑤ | **打印中逐层失稳** | 逐层累积质心 vs 首层 bed 多边形 | **首次失稳的 layer idx** | `extruder_clearance_radius` | 2 |
| ⑥ | **最弱层截面 + 各向异性夹角** | 逐层面积 argmin + 该处主轴与 build Z 夹角 | layer + face IDs + 角度 | Ahn 2002 各向异性实测 | 1 |
| ⑦ | **部件间隙 / 意外粘连** | `trimesh.proximity.ProximityQuery` 最小距离 | **两侧 face IDs + 距离值** | `nozzle_diameter` | 2 |
| ⑧ | **首层凸锐角翘曲代理** | 首层多边形顶点内角 < 阈值（**即 brim ears 算法**） | 首层顶点 + face IDs | **`brim_ears_max_angle`=125°** | 1 |

⑥ 的合并很妙：**关键不是最小截面积多大，而是这个最小截面是不是一个层面**——
FDM 层间粘接最弱，水平 neck 比同面积竖直 neck 危险得多。比完整 FEA 便宜 3 个数量级。

⑦ **反馈质量最高**：两侧 face IDs + 精确距离，agent 得到可直接执行的单参数修改
（「把 `gear_a` 与 `gear_b` 间距从 0.15mm 改到 0.4mm」）。

## 7.8.3 【重大实现利好】PrusaSlicer 的 `;TYPE:` 已经把 overhang/bridge 分类好了

实测 `src/libslic3r/ExtrusionRole.cpp` 的 `role_to_string()`，完整取值集：
```
Unknown | Perimeter | External perimeter | Overhang perimeter | Internal infill
| Solid infill | Top solid infill | Ironing | Bridge infill | Gap fill
| Skirt/Brim | Support material | Support material interface | Wipe tower | Custom
```

> **`Overhang perimeter`、`Bridge infill`、`Gap fill` 是独立的 extrusion role，
> 带完整坐标逐段写进 G-code。我们不需要自己实现 overhang/bridge 检测。**
> 逐层统计即可得：每层悬空周长、最长桥跨距、gap fill 占比。
> Z 由 `;LAYER_CHANGE` / `;Z:` / `;HEIGHT:` 恢复；文件尾还有完整
> `; prusaslicer_config = begin...end` 配置块，**自带可复现参数快照**（reality contract 白拿）。

对比 **CuraEngine**：`Cura.proto` 提供真正结构化的逐层 polygon + 逐特征耗时，
但 `Polygon.Type` 枚举**没有 Overhang、没有 Bridge**。→ **两者互补，PrusaSlicer 不可替代。**

**结论：没有任何 slicer 提供「per-layer overhang/bridge 报告」的一等公民 API。**
`--info` 只输出控制台文本，非 JSON。需自写约 200–400 行 `;TYPE:`-aware 分析器
（**视为基础设施，不是贡献**）。G-code 库只有 `gcodeparser` 0.3.0 可用。

⚠️ **两个必须注意的坑**：
1. **G-code 里没有 triangle ID**。反查只能靠 `(layer_z, XY) → trimesh.proximity.on_surface`
   投回最近 face，误差约一个 extrusion width。
2. **libslic3r / PrusaSlicer / CuraEngine 均为 AGPL-3.0**，会影响论文附带代码的许可选择。
3. OrcaSlicer 仓库**已迁移**至 `OrcaSlicer/OrcaSlicer`（原 `SoftFever/OrcaSlicer`）；
   Orca/Bambu 另有 `Overhang wall`、`Internal bridge` 等变体字符串，**需对着你实际用的版本核对**。

## 7.8.4 术语与引用陷阱

- **「island」在金属 AM 文献里指扫描策略的棋盘分区，是完全不同的东西**，引用务必避开。
  FDM 的 island checker **没有找到专门论文 → 空白对我们有利，可主张原创**。
- **不要引用 `h/w > 8~10` 这类 tall-print 长细比阈值**——社区常说但**无同行评审来源**。
  正确做法：阈值参数化 + 敏感性分析。
- **不存在标题为 "survey of DfAM rules" 的论文** → 改引 Thompson et al., CIRP Annals 2016。
- SLA/SLS 排料孔的具体直径与数量、print-in-place 具体 clearance 数值：**均需核实**，
  应引厂商设计指南 URL，不要当既定结论。

---

# 七点九、Benchmark 方法论：已核实的关键证据

## 7.9.1 【最强证据】测试驱动评测在人类对齐上碾压几何相似度与 VLM judge

CADTestBench 实测的人类对齐 AUC：

| 指标 | AUC |
|---|---|
| **RS（requirement score，可执行 predicate 通过比例）** | **0.928** |
| Chamfer distance | 0.663 |
| CLIP | 0.665 |
| **LVM judge（VLM-as-judge）** | **0.659** |

> **这是目前最强的「可执行测试 > 几何相似度 > VLM judge」的定量证据，直接支撑我 §5.4 的主张。
> 建议在 intro 和 evaluation 两处都引用。** 注意 RS 优于 PR（pass rate）：分辨力更高、更抗噪。

## 7.9.2 三段漏斗已是行业事实标准，但第三段各家不同 —— 这正是我们的切入点

| Benchmark | 第三段做什么 |
|---|---|
| MUSE | **VLM / LLM rubric 打分**（主观、不可复现） |
| CADEngBench | FEA |
| **我们** | **切片器 + 物理判据 + 功能 predicate（确定性）** |

→ 「你的 benchmark 若要主打真实可用 + 可打印，第三段应做成 FEA/切片器/物理判据，而不是 rubric judge」。

## 7.9.3 两个必须继承的协议设计

1. **「invalid 计为失败」**：既往协议把不可执行样本剔除，导致各方法在各自子集上算分**不可比**。
   CADEngBench 的 L0→L3、MUSE 的三段漏斗都用「前段失败则下游记 0」实现。**必须照做。**
2. **Mutation testing 目前只有 CADTestBench 一家做**。CADTestBench 精修后 **MScore > 90%**
   ——**这是我们要对标的数字**。
   > **我们可以显著扩展**：把 mutant 从「违反 prompt 的几何要求」扩到
   > **「违反可打印性/可用性契约」**（壁厚 < nozzle、悬垂角超限、质心投影出支撑多边形、
   > 装配间隙为负、缩短一条腿破坏共面性），并报 mutation score 作为 checker suite 的信誉指标。
   > 这既是方法贡献，也顺带产出**已知 ground-truth 定位**的样本用于评估 source grounding。

## 7.9.4 打印契约的合法且有标准背书的来源组合

- **ISO/ASTM 52902**：给出官方 test-geometry 词汇与检验等级概念，
  但**明确不规定制造程序与机器设置**（所以机器参数仍须我们自己写死）
- **NISTIR 7858**：test artifact 特征的完整综述
- **3DBenchy**：已进入 **Public Domain**，可自由纳入
  （⚠️ 从 CC BY-ND 转 PD 的**具体时间需核实**）

→ 三者组合成一个**有标准背书、法律上干净**的 printer profile 参照集。

## 7.9.5 物理判据的可复用清单（全部已核实出处）

| 判据 | 出处 |
|---|---|
| build volume 装得下 | Chopper |
| 自重不塌 + 最小壁厚 | Stress Relief (TOG 2012) |
| 静态平衡不倾倒（质心投影落在支撑多边形内） | Make It Stand (TOG 2013) |
| **最坏工况载荷下不失效（无需假设载荷）** | **Worst-case Structural Analysis (TOG 2013)** |
| 截面弯矩下的摆放方向优化 | **Umetani & Schmidt** ✅ **确认存在** |
| 体素/形态学可打印性相对喷嘴分辨率 | Telea & Jalba |

> **Worst-case Structural Analysis「无需假设载荷」这一点，对「未知用途的生成物体」特别合适**
> ——这正是我 §2.1 分歧判断的核心，现在两个独立来源都确认了。
> 而 Umetani & Schmidt 也确认存在，我 §2.1 里的 `⚠️ 仍未核实` 可以划掉。

---

# 八、仍待核实清单

**✅ 本轮已核实（可划掉）**：竞品 2608.22128 标题作者；Worst-Case Structural Analysis /
Make It Stand / Stress Relief / Ahn 2002 的 DOI；**Umetani & Schmidt 确认存在**；
`mesh_multiplane` 的 segment→face 回传；slicer CLI 能力与 `;TYPE:` 取值集；
AgentsCAD 范围（**只检测 45° overhang 一项，验证仅一个 birdhouse**，arXiv 2607.02448）。

**仍然开放**：

- [ ] **Blender boolean 是否传播 custom face attribute**（决定 §6.4 第 2 层路径；
      脚本已备好在 `/tmp/bool_attr_test.py`，**分类器恢复后第一件事就跑它**）
- [ ] **`bmesh.ops` 里是否有 boolean 算子**（若无，程序化 pipeline 只能走 modifier 或换外部内核）
- [ ] **polygon → face 的成环映射**（`shapely.ops.polygonize` 不保留属性，需自己带 face_index 下去；
      **这是 slice-level checker 唯一有实质风险的实现点**，第一周优先打通 + 写单测）
- [ ] 家具/挂钩承重的工业测试标准（BIFMA X5.1、EN 1728）具体载荷数值
- [ ] LLM APR 领域「结构化定位反馈 vs 自然语言反馈」的 ablation 先例（支撑贡献 3）
- [ ] manifold3d / CGAL 替换 Blender boolean 的工程量与鲁棒性对比
- [ ] SLA/SLS 排料孔直径与数量、print-in-place clearance 的可引用数值来源
- [ ] OrcaSlicer/BambuStudio 的 `;TYPE:` 变体字符串（对着实际使用版本核对）
- [ ] ISO/ASTM 52902 / 52910 条款级内容（Crossref 只返回标题，需买标准原文）
- [ ] 上述 2026 年 arXiv benchmark 论文（CADTestBench / CADEngBench / MUSE / 3DCodeBench 等）
      的**发表会议**与**代码仓库 URL**（arXiv comment 中未声明）
- [ ] 3DBenchy 从 CC BY-ND 转 Public Domain 的具体时间与来源
