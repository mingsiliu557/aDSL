# aDSL 实验归档：初始 Agent 审计与 TRELLIS/CLIP 对比

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: validate
- Origin Date: 2026-09-02
- Verification Status: ANALYZED
- Version Label: adsl_experiment_archive_v1
- 项目分支：api-native-development
- 初始审计数据：/jiigan-hp/lms/aDSL/experiment/audit_20260830
- 对比实验数据：/jiigan-hp/lms/aDSL/experiment/clip_trellis_pilot_20260902
- 论文：https://arxiv.org/abs/2608.17975

本文件是整个本地复现阶段的总入口，归档两部分证据：

1. 2026-08-30 完成的 12 个逻辑 case、15 次 Agent 调用诊断；
2. 2026-09-02 完成的 7-case aDSL/TRELLIS 公平渲染与 CLIP pilot。

它不是论文完整 benchmark，也不能支持总体 SOTA、100% success、FID/VQA 或用户偏好结论。

## 第一阶段：初始 case 与 Agent 流程审计

### 协议

- 12 个逻辑 case、15 次独立 invocation。
- 模型：本机 Stepcode OpenAI-compatible API 的 gpt-5.6-sol，不是论文的 Gemini 3 Pro。
- 不做 launcher 自动重试；Agent 内部 Debugger/repair 计入被测流程。
- 原项目 Blender Eevee，8 views、15° elevation；当时使用 512×512、16 samples、CPU Mesa fallback。
- 分析材料包括 source.py、GLB、URDF、render、Critic 日志、session DB、token/耗时、AST 和网格拓扑。

### 总体结果

- 15/15 invocation 最终产生 GLB/URDF/PNG。
- 14/15 被当前 workflow 标为 approved。
- 共 118 个模型请求、836,614 tokens；invocation wall time 求和 4,226.324 s。
- 3 次首轮执行出现同类非法数字词语法，均由 Debugger 修复。
- 15 个 SQLite session 合计约 53.9 MB，包含 195 次 data:image。

这里的 14/15 是 Agent 自己的审批状态，不是独立 success rate。现有审批链会跳过部分 Critic，也没有确定性几何 verifier。

### Agent 与代码缺陷

| 严重度 | 缺陷 | 影响 |
|---|---|---|
| Critical | 模型生成的 source.py 经 runpy.run_path 以当前用户权限执行 | 没有真正 sandbox；可访问文件、网络和子进程 |
| High | 最后一轮 execution 成功后直接结束，不运行 Critic | max_rounds 的实际可审查轮数少 1；max_rounds=1 恒定 critic_skipped |
| High | Image Critic approved 后跳过 Code Critic | 与论文 final-adjudicator 叙述不一致，代码/结构覆盖不稳定 |
| High | Planner relations/checklist 只是 list[str] | 没有 typed、可执行的 count/contact/alignment 约束 |
| High | 没有 deterministic verifier chain | 计数、接触、碰撞、拓扑、关节运动错误不会自动阻断 approved |
| High | articulated 主流程只渲染 Joint.initial | 无法验证 lower/mid/upper pose、运动方向、碰撞和全行程 |
| Medium | 固定 8×15° object orbit | 看不到 mug interior；室内场景被墙遮挡 |
| Medium | Code Critic 被要求 MUST TRUST THE CODE LOGIC | 容易把源码意图误判为最终几何/视觉事实 |
| Medium | 没有 AST/compile preflight | 3/15 首轮算力浪费在可廉价发现的语法错误 |
| Medium | AABB 对 transform/boolean 是近似 | T02 已出现 floating books |
| Medium | session 图片 data URL 不物理裁剪 | 长时间运行会持续增大数据库 |
| Medium | ObjectWorkflow 单体且大量硬编码 | 相机、verifier、state sampler 和 ablation 难以替换 |
| Medium | primitive/flat material 表达能力有限 | image reconstruction 和复杂外观明显粗糙 |
| Low | 缺 Draco 时 Blender 输出 ERROR 但 GLB 仍成功 | 日志容易把可用输出误判为失败 |

### 逐 case 生成物结论

| Case | 主要结果 | 主要缺陷 |
|---|---|---|
| T01 chair | 4 腿、3 slats、无扶手满足；grid/stack 有效 | 仍混用大量绝对坐标 |
| T01 one-round control | 几何可用 | 因 round bug 完全没有 Critic，completed 但 approved=false |
| T02 bookshelf | 4 shelves 和内容成立，repair 修复 floating books | curved nosing 不明显；4 个 CSG 网格共 41 个 zero-area faces |
| T03 wheel | 16 spokes、均匀 radial layout、hub/rim 接触清晰 | 外观仍 primitive，是少数关系 DSL 强正例 |
| T04 mug | 源码确实构造 hollow cavity | 八视图看不见内壁/底；handle 粗糙且 union non-watertight |
| T05 desk | drawer、side supports 和条纹语义基本成立 | 条纹用 13 个薄 Cube+boolean 模拟，不是真材质/纹理 |
| I01 table | 木桌语义可辨识 | 腿型、比例、围板、木纹和接缝显著弱于参考；首轮语法错误 |
| I02 nightstand | 三抽屉语义和 3 个 prismatic joints 成立 | 轮廓、把手、材质粗糙；首轮语法错误 |
| A01 cabinet | 2 revolute + 1 prismatic，额外 pose probe 确认可动 | 正式八视图全关闭，workflow 没看见运动 |
| A02 faucet | 3 revolute joints，repair 修复 floating support | 更像粗糙工业塔，faucet 识别度弱 |
| E01 chair edit | 3→5 slats，只 +6/-6 行，其他几何保持 | 只证明简单局部编辑 |
| S01 living room | 五项空间关系大致成立 | 3/8 视图几乎被墙完全遮住，场景高度 primitive |
| M01 motorcycle base | 基础摩托车可辨识 | 细节有限 |
| M01 cyberpunk edit | 保留基体，wall time 比 scratch 少 | tokens/requests 更多、源码膨胀、视觉改动更弱 |
| M01 cyberpunk scratch | cyberpunk 外观更统一 | 首轮语法错误；power-unit CSG 有退化面 |

### 第一阶段结论

结构化程序生成、关系布局、局部编辑和 URDF 导出能力得到局部支持。当前最致命问题不是“能不能生成文件”，而是：

1. 生成代码缺少安全执行边界；
2. Agent 的 approved 不能稳定证明结果满足约束；
3. 关节、拓扑和遮挡属性没有对应的确定性/多状态证据；
4. 公开 primitive 主流程无法达到论文展示的高保真外观。

## 第二阶段：aDSL vs TRELLIS CLIP pilot

### 公平协议

- 复用第一阶段的 T01-T05 和 I01-I02，共 5 个 text cases、2 个 image cases。
- TRELLIS clean commit 6b0d64751ad54d9c32d7b05fec482eb29178f56f。
- 权重：TRELLIS-text-xlarge-original、TRELLIS-image-large；每 case seed=1、一次生成、无 prompt expansion、无自动 retry。
- 两侧统一用项目 Eevee：8 views、45° 间隔、15° elevation、1024×1024、256 samples、neutral material、相同 AABB normalization。
- CLIP：本地 openai/clip-vit-large-patch14；8-view cosine×100 的 mean，失败按 0。
- 单卡 A800 严格串行运行 TRELLIS→Blender→CLIP，避免显存重叠。

### 运行完整性

- 7/7 TRELLIS GLB 成功。
- 14/14 render groups、112/112 PNG 成功。
- trellis/render/clip 三阶段 exit code 均为 0，worker 已自动释放。
- 只经 127.0.0.1:7892 下载公开 Toys4k.csv 和 u2net 缓存；没有下载完整 Toys4K。

### CLIP 结果

| Track | n | aDSL | TRELLIS | aDSL−TRELLIS | 解释 |
|---|---:|---:|---:|---:|---|
| text | 5 | 24.171 | 25.201 | -1.031 | 在预设 ±2 工程线内，粗略接近 |
| image | 2 | 66.822 | 70.719 | -3.897 | TRELLIS 更高 |

| Case | Track | aDSL | TRELLIS | Delta |
|---|---|---:|---:|---:|
| T01 | text | 25.164 | 25.878 | -0.714 |
| T02 | text | 22.277 | 23.637 | -1.360 |
| T03 | text | 22.739 | 24.344 | -1.604 |
| T04 | text | 27.874 | 27.748 | +0.125 |
| T05 | text | 22.799 | 24.399 | -1.600 |
| I01 | image | 70.015 | 74.400 | -4.385 |
| I02 | image | 63.629 | 67.037 | -3.409 |

乱序 reference control 均低于 matched score：text gap 约 8.2/8.5，image gap 约 6.7/8.3；两张 image self-control 约 100。评分链路有基本区分力，但 image 仅有两个同类家具，不能外推到 Toys4K。

### 视觉复核

- Text 轨可以说 CLIP 接近，但不能说总体质量等价。
- T01/T03 显示 aDSL 更容易精确表达四腿/三横档和 16 spokes；CLIP 本身不可靠计数。
- T05 是 CLIP 局限的关键反例：TRELLIS 更像常见办公桌，却增加 prompt 未允许的侧柜/额外结构。
- I01/I02 中 TRELLIS 的比例、完整轮廓、抽屉和接缝明显更接近参考；aDSL 更 primitive，与 image CLIP 差距一致。
- T04 的 hollow、wall thickness 和 watertightness 不能由“像 mug”的 CLIP 分数证明。

### 渲染记录

manifest 的 background=white 是 world color 设置。项目的 Filmic/Medium High Contrast/exposure=-0.8 使最终 PNG 角像素为 RGB(134,134,134)，视觉上是灰背景。两种方法完全相同，因此不构成方法间偏置，但不可称为纯白像素背景。

## 综合判断

当前本地证据支持两条互补结论：

1. aDSL 的优势在结构化、精确关系和可编辑程序，而不是天然高保真外观；
2. TRELLIS 在纯图像形状与视觉先验上更强，但 CLIP 高分不能保证 exact constraints。

下一步若要提高证据强度，应优先加入 deterministic count/contact/topology gate、joint 多状态渲染，并把 Toys4K 扩展建立在获得授权的少量真实对象上；不能用 metadata 行代替 image-to-3D 实验。

## 归档索引

- clip/scores.json：第二阶段机器可读逐 view/逐 case 分数。
- clip/scores.csv：第二阶段表格分数。
- analysis/adsl_trellis_view1_contact.jpg：全部 case 的两方法并排视图。
- analysis/image_reference_contact.jpg：输入图、aDSL、TRELLIS 三列对照。
- archive/initial_case_audit_20260830/：第一阶段详细 Markdown 报告。
