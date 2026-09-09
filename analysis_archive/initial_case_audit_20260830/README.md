# aDSL 公开代码诊断审计

更新时间：2026-08-30（UTC）
Verification Status：**ANALYZED**

## 结论先行

公开代码的核心能力是真实可运行的：文本/图片输入能生成结构化 Python/aDSL 程序，关系布局、局部编辑、URDF 关节和多视角 Eevee 渲染均已在本机跑通。本轮 12 个逻辑 case、15 次独立调用最终都产出了 GLB/URDF/PNG，14 次被当前工作流标记为 approved。

但这不能复述为“论文效果已复现”。论文实验使用 Gemini 3 Pro、最多 10 轮、1024×1024 渲染、公开 benchmark 和 CLIP/VQA/FID；本轮使用本机 Stepcode 的 gpt-5.6-sol、512×512/16 samples、定向小样本且没有论文 benchmark/baseline。更重要的是，公开工作流和论文叙述存在几处实质偏差：

- 最后一轮只执行不审查；max_rounds=1 会把可用资产以 approved=false、status=completed 发布。
- Image Critic 一旦批准，Code Critic 就被跳过，与论文“Code Critic 是最终裁决者”不一致。
- Planner 的关系/checklist 只是字符串；系统没有自动接触、计数、碰撞、拓扑或关节轨迹验证。
- articulated case 默认只渲染 Joint.initial。提示词虽要求检查非零姿态，Executor 却不生成非零姿态证据。
- 生成代码通过 runpy 不受限执行，是当前最严重的安全边界问题。
- 八个固定 15° 环视对杯内腔和室内场景覆盖不足；2D Critic 仍可在明显不可见时批准。
- 15 次调用中有 3 次首轮源码出现同类 0. thirty / 0. forty 非法 Python；Debugger 都修复了，但缺少廉价的 AST/compile preflight。
- CSG 结果存在实际拓扑异常：T02 四个弧形 shelf nosing 网格共 41 个退化面，T04 的 C 形把手 union 非 watertight，M01 scratch 的 power-unit union 有 2 个退化面。
- 图片重建可辨识，但材质、曲面和细节明显粗糙；论文高保真结果依赖未包含在公开主流程中的 SpaceControl/Trellis 外部生成器。

因此，当前更准确的判断是：**结构化程序生成、关系布局和可编辑性得到局部支持；论文级鲁棒性、自动约束验证、最终视觉质量和 benchmark 指标尚未复现。**

## Material Passport

| 项目 | 本轮材料 |
|---|---|
| 论文 | [aDSL: Agentic 3D Creation via Joint Agent-Program Design, arXiv v1](https://arxiv.org/html/2608.17975v1) |
| 代码基线 | 原项目 commit e1742e3；本分支叠加本机 API/Eevee/执行兼容修复与审计工具 |
| 分支 | api-native-development |
| 模型 | gpt-5.6-sol，经 Stepcode OpenAI-compatible Responses API；已作为 adsl-run 的本机默认 profile；不是论文的 Gemini 3 Pro |
| case | 12 个逻辑 case，15 次 invocation；manifest 见 experiments/adsl_audit_20260830/case_manifest.json |
| 自动重试 | 无；每个 invocation 最多运行一次，Agent 自身的 Debugger/repair 属于被测流程 |
| 渲染 | 原项目 Blender Eevee；CPU Mesa surfaceless；8 views，15° elevation，512×512，16 samples |
| GPU | 未申请、未使用 |
| 输出 | temp/audit_20260830/（本地小数据，Git 排除） |
| 结构分析 | AST、URDF、GLB/trimesh（按位置焊接 glTF 重复顶点后统计）、session DB、critic/usage/source diff |
| 总消耗 | 118 个模型 request，836,614 tokens；各 invocation 耗时求和 4,226.324 s |
| 判定边界 | 环境相关定向诊断；不可用于统计性 SOTA、论文指标或 100% 成功率主张 |

## 主要实证结果

- 结构约束：T01 确有 4 腿/3 横档，T03 用 radial_shapes 明确生成 16 根辐条，E01 只用 +6/-6 行把 3 横档改为 5，未改其他几何。
- 关系工具：有明显价值，但并未消除数字坐标。T01 仍混用手写坐标；T02 第一轮因 shelf 整体 AABB 含较高 nosing，place_on_axis 把书抬空，后续才改为对 board 放置。
- 关节：I02、A01、A02 的 URDF 分别有 3/3/3 个可动关节。审计 pose probe 把关节推到 85% 行程后重新导出和渲染，抽屉/门/控制件都实际移动且保持连接。
- Critic：T04 的固定视角看不到杯内腔，Code Critic 依据源码批准；A01 的全关闭八视图无法证明运动，Code Critic仍批准。说明当前通过状态包含“从代码推断”，不是对最终视觉/运动的充分验证。
- 图片输入：I01 木桌、I02 三抽屉床头柜均可辨识；但只有平面颜色和粗 primitive，比例、腿型、把手、木纹和接缝细节明显弱于参考图。
- 场景：S01 的语义关系基本成立，但 3/8 视图几乎被房间墙面完全遮住；物体中心的 360° 环视不适合作为室内场景评估相机。
- memory edit：摩托车 edit 保留基体并更快（256.868 s 对 scratch 325.917 s），但用了更多 tokens（123,800 对 91,806）和 requests，且 cyberpunk 视觉变化比 scratch 更弱；未复现论文“复用后 1 轮/显著降本”的示例。
- 会话存储：15 次调用的 SQLite 共 53,882,880 bytes，发现 195 次 data:image 数据。角色隔离/选择性上下文不等同于从持久存储中删除旧 render。

## Eevee 与本机环境

原项目渲染器就是 Blender Eevee，默认 1024×1024、256 samples；不要求 GPU。此前失败是缺 libEGL.so.1，不是 GPU 不够。

依赖只解压到用户目录：

- /vepfs_default/chanxueyan/lhp/lms/adsl_runtime/egl
- 环境变量写入 /vepfs_default/chanxueyan/lhp/lms/.bashrc
- 修改前备份：/vepfs_default/chanxueyan/lhp/lms/.bashrc.before-adsl-egl-20260830

运行前：

~~~bash
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
~~~

日志中的 EGL_NOT_INITIALIZED 后接 “fallback to surfaceless EGL rendering” 是预期 CPU fallback；最终 PNG 正常生成。本轮没有申请 GPU。

## 报告索引

- paper_claim_matrix.md：论文主张、公开实现和本轮证据逐项对照。
- code_agent_findings.md：代码与 Agent 流程缺陷，含严重度、证据和修复顺序。
- generated_output_findings.md：逐 case 生成物质量、误判、拓扑和 edit/memory 对照。
- case_results.json：机器可读的全部 invocation、AST、GLB、URDF、Critic、session 和 usage 数据。
- case_results.md：自动生成的运行总表。
- prior_temp_inventory.json：清理旧 temp 前的审计清单。

## 复查命令

~~~bash
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python experiments/adsl_audit_20260830/analyze_cases.py
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q
~~~

姿态探针示例：

~~~bash
python experiments/adsl_audit_20260830/render_pose_probe.py \
  --source temp/audit_20260830/A01-cabinet/source.py \
  --output temp/audit_20260830/pose_probes/A01-cabinet \
  --fraction 0.85
~~~

## 明确没有完成的事

- 没有下载或重建论文的 200 text prompts、30 Toys4K image inputs、120 ablation prompts。
- 没有运行 Scene Language、ShapeCraft、BlenderMCP、Trellis 等 baseline。
- 没有计算 CLIP、VQA、FID，也没有做人类用户实验。
- 没有复现 Gemini 3 Pro、temperature=1、R=10 的完全相同协议。
- 没有接入论文中的 SpaceControl/Trellis 高保真阶段。
- 没有把 temp 迁到 jiigan-hp；该盘状态仍不应作为本轮依赖。
