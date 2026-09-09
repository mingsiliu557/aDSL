# 论文主张与公开实现对照

Verification Status：**ANALYZED**

对照对象为 [aDSL arXiv v1](https://arxiv.org/html/2608.17975v1)。判断只针对当前公开仓库和本轮定向诊断，不是对作者未公开实验基础设施的推断。

## 对照矩阵

| 论文主张/协议 | 公开代码或本轮证据 | 判断 |
|---|---|---|
| aDSL 用 composability 与 spatial reasoning 减少 fragile numeric choices；Planner、Coder、Critic共享可验证的关系表示（§1、§3.1） | DSL 确有 place_on_axis、align、grid、radial、stack 等操作。T03 用 radial_shapes 可靠表达 16 根辐条，T01 用 grid/stack 表达重复结构。但 ObjectPlan.relations 和 critic_checklist 都只是 list[str]；Critic 没有执行结构化谓词。T01 仍有大量绝对坐标，T02 还出现 AABB 近似导致书本悬空。 | **部分支持。** 关系 API 有实际价值，但“共享、可检查表示”在 Agent 层主要仍是自然语言，不是可执行约束。 |
| Planner 输出 component decomposition、spatial relations、precise verifiable checklist（§3.2） | Pydantic 只验证字段类型：组件 name/description、relations 字符串列表、checklist 字符串列表。没有实体引用、关系类型、数量谓词、容差或 schema 级一致性检查。 | **接口存在，验证性不足。** |
| 工作流为 Plan–Execute–Critic，自修复至约束满足或 R=10（§3.2、Appendix A） | 公开 ObjectRequest 默认 max_rounds=2。循环在最后允许轮次执行成功后立即以 round_limit_after_execution 结束，Critic 不再运行。T01 max_rounds=1 控制组因此 approved=false 却 status=completed。 | **与论文协议不一致。** 轮数可配置，但实现语义让最多可 Critic 的轮数少 1。 |
| Image Critic 后由 Code Critic 作为 final adjudicator（§3.2 Critique Stage） | service.py 在 Image Critic approved 时直接 break，完全跳过 Code Critic。15 次中多数由 Image Critic 直接终止。 | **公开实现与论文叙述矛盾。** |
| 执行后进行 visual and constraint-based evaluations / automatic checks（§1、§3.2） | 当前只有 LLM Image Critic 和 LLM Code Critic；没有 deterministic component-count、contact、alignment、collision、joint-trajectory、watertight 或 degenerate-face gate。 | **未在公开主流程实现。** |
| 八视图、45° 方位间隔、固定 15° elevation、1024×1024（Appendix A） | 原代码默认正是 8 views、15°、1024×1024；本地因成本用 512×512/16 samples，仍沿用原相机。T04 内腔在所有视图不可见，S01 有 3/8 视图被墙面遮住。 | **协议基本对应，覆盖质量存在结构性缺陷。** “多视图”不自动等于局部细节或场景覆盖充分。 |
| Code Critic 应避免 occlusion/perspective 造成的视觉误判（§3.2） | T02 Code Critic 正确否定部分视觉误判并发现 AABB 放置问题，体现代码审查价值。另一方面提示词要求 MUST TRUST THE CODE LOGIC；T04 即使“open top/visible interior”在最终 render 不可见仍由代码批准，A01 也在全关闭姿态上推断关节有效。 | **价值与风险并存。** 能纠正视觉误判，也会把“代码看似表达了意图”当成“输出已满足视觉/运动要求”。 |
| selective memory 保留约束、裁剪 transient state，并在每轮后移除多视图（§3.2） | 每个角色使用独立 SQLite session，prompt 也只显式带最近状态；但数据库未物理裁剪旧图。本轮 DB 总计 53.9 MB、195 个 data:image occurrence。 | **上下文选择性部分成立；持久存储 pruning 未体现。** |
| text-to-shape 在论文 benchmark 上 100% execution success，并优于 code baselines 的 CLIP/VQA（§4.1） | 本轮 15/15 最终有可渲染输出，但 3/15 首轮执行因同类非法数字词语法失败。没有论文 200 prompts、baseline、CLIP/VQA，也不是同一模型/轮数/分辨率。 | **不能复现或反驳论文统计。** 本轮只说明 Debugger 能救回这三个语法失败。 |
| image-to-shape 在 Toys4K 上有较强 CLIP/FID，并比 code baselines 结构更干净（§4.2） | 本轮只有两个仓库示例图。I01/I02 可辨识，但 primitive 化明显、材质平、比例/把手/腿型/木纹等细节弱；I01/I02 首轮均出现非法数字词。未算 CLIP/FID。 | **只支持“可运行、可辨识”，不支持论文指标或高保真主张。** |
| relational structure 与 iterative repair 减少 floating/misaligned/interpenetrating parts（§4.1/§4.2） | T02 的第一轮恰有 AABB 引起的 floating books，Critic 后修复。A02 第一轮有 floating lever support，第二轮修复。另一方面没有确定性碰撞 gate。 | **局部支持 iterative repair；不能证明系统性减少。** |
| articulated program 同时表达 geometry 与 kinematics，并可导出 URDF（§3.1、§4.4） | I02、A01、A02 都生成精确 3 个 movable joints，轴/limits/名称合理。额外 pose probe 把全部关节置于 85% 行程并产生变化后的 GLB/八视图，门、抽屉、knob/lever 实际移动。 | **核心 DSL/URDF 能力得到强支持。** |
| Critic 检查 articulated motion/function（§4.4 的功能语义） | GLB exporter 只把 Joint.initial 烘焙进当前 GLB，Executor 不采样 min/mid/max；Code Critic prompt 虽要求 representative nonzero pose，模型没有对应 render/tool。 | **Agent 验证链不支持该主张。** 本轮 pose probe 是审计补充，不是原 workflow 能力。 |
| shape editing 是 localized program rewrite，保持不相关几何（§4.4） | E01 由 3 slats 改为 5，仅 +6/-6 行，视觉上其他几何保持。M01 cyberpunk edit 为 +99/-3 行，基体保存但修改范围和 token 成本较大。 | **简单局部编辑强支持；复杂风格编辑为混合结果。** |
| memory reuse 可使后续 motorcycle edit 从 5 rounds/845s 降到 1 round/164s（§4.1 Efficiency/Fig.8） | 本轮 base 1 round/161.294s；edit 2 rounds/256.868s/123,800 tokens；scratch 2 rounds/325.917s/91,806 tokens。edit 更快且无执行失败，但请求/token 更多，视觉 cyberpunk 更弱。 | **没有复现论文示例的轮数与成本优势；只观察到中等 wall-time 优势。** |
| scene hierarchy 支持结构化 scene composition（§4.4） | S01 的 sofa/table/TV/lamp/rug 关系大致成立，但固定物体环视使墙面遮挡 3/8 视图，仍被 Image Critic 批准。原始 primitive scene 视觉粗糙。 | **结构表达部分支持；评估相机和高保真输出不足。** |
| high-fidelity 通过 SpaceControl + Trellis 外部生成器补足 DSL 细节（§4.4） | 当前公开 Agent 主流程只输出 primitive/CSG aDSL + Eevee；未见 SpaceControl/Trellis 集成路径。T05 用 13 个细 Cube/boolean intersection 模拟桌面条纹，而不是纹理/材质；图片 case 也只有平面颜色。 | **公开主流程不包含论文高保真阶段。** |
| 论文明确承认 DSL 表达力、2D Critic 视角歧义、强 proprietary LLM 依赖是限制（§5） | 本轮 T04/S01 的相机盲区、I01/I02 的粗糙外观、Stepcode 模型依赖都直接复现这些限制。 | **与论文自述限制一致。** |

## 为什么“14/15 approved”不是成功率

当前 approved 是控制流状态，不是独立评测指标：

1. Image Critic 可以独立终止，Code Critic 常被跳过。
2. approved 不包含 topology、collision、count 或 sampled-motion 的确定性 gate。
3. max_rounds=1 的同一输出会变成 approved=false，而不是因为几何更差。
4. Code Critic 可以依赖代码逻辑批准最终 render 中不可见的属性。
5. 这 15 次是人工设计的诊断 case，不是独立同分布 benchmark。

因此报告只保留“14 次被当前工作流标记 approved”和“15 次最终产生输出”，不把它们换算成论文 execution success、CLIP/VQA/FID 或用户偏好结论。

## 协议差异清单

| 维度 | 论文 | 本轮 |
|---|---|---|
| 模型 | Gemini 3 Pro | gpt-5.6-sol / Stepcode |
| temperature | 1.0 | profile 当前配置 |
| 最大轮数 | R=10 | 每 case 1/4/6；公开默认 2 |
| 图像 | 1024×1024 | 512×512 |
| views | 8 × 45°, elevation 15° | 相同角度协议 |
| samples/renderer | 统一论文 renderer | 原项目 Eevee，16 samples，CPU surfaceless |
| text benchmark | 200 prompts | 10 个文本方向逻辑 case + controls/edits |
| image benchmark | Toys4K 30 | 仓库示例图 2 张 |
| metrics | CLIP/VQA/FID/Success + human study | AST/GLB/URDF/critic/usage + 人工诊断 |
| baselines | 多个 code/field/mesh 方法 | 无 |
| high fidelity | SpaceControl + Trellis | 未接入 |
