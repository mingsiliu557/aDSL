# 原始 Prompt 完整流程重跑（2026-09-29）

## 重跑前核对

历史证据：主工作区 `temp/partition_score_20260929/cases/`。
SF07 Engineering 收到实际分组/连接、逐件 G=0、N=3、O=143.844618665、最佳姿态，
`print_partition_editable=true`，尚余一次修改。它以“没有预先测量的替代方案”为由 NO_PROPOSAL。
输入完整，但把假设建议当成需要先证明收益。现只澄清：基于当前结构/G/N提出可检验假设，
无需预先有候选分数，不捏造收益，无合理方案仍可停止。

SF10 当时是固定源码 S5：`max_rounds=1`、明确不调用 Engineering/Coder。
因此外观失败后没有继续修复是预算与测试设计，不是自动修复失败。
Image/Code 的主要拒绝是显式光泽要求；木纹只被列为可选。
Code Critic 建议 roughness/specular 设置，但当前公开 DSL 仅提供 color/alpha，
export_glb._make_material 使用 Principled 默认设置。补充这一 API 边界，禁止虚构方法，
不凭不存在的材质调用判定外观，也不把无法表达的明确要求自动视为已通过。
不改变外观验收等级、评分公式或任何 checker 阈值。

`python -m pytest -q -p no:cacheprovider tests/test_partition_feedback.py tests/test_prompts.py`：
9 passed，29.93s。日志 `temp/partition_score_20260929/rerun_prompt_preflight.log`。

## 固定运行条件

运行脚本与记录：主工作区 `temp/partition_full_flow_20260929/`。
顺序 SF07 → SF10 → SF16，各一次 `ObjectWorkflow.generate()`，从空源码和原始文本开始。
原 Prompt 来自 case_manifest，逐项与历史 ZIP 的 ORIGINAL TASK 核对；原任务无参考图，
不把历史模型/渲染/人工补丁喂给新生成。只从旧计划读取冻结尺度，不继承分组/连接。

- 模型：现有 CLIProxy gpt-5.6-sol profile；实际 system/user/tools 均逐调用存档。
- CPU Cycles，512×512，32 samples，8视图；无 GPU。
- SF07 750³ mm；SF10 1500×750×750 mm；SF16 1125×450×1800 mm；mm_per_unit=1，fit=0.2 mm。
- Topology → Overhang → Standing；FEA关闭。alpha=.3、Rvox=.1、24朝向。
- Standing rigid_flex，5秒/25°，dt=.002，friction=[.5,.005,.0001]，实验密度1240 kg/m³。
- 每件一次初始生成、最多5轮评估/4个源码修改候选、既有7200秒修复时间预算。
- `print_partition_editable=true`；不强制分件数、合并或拆分；正常视觉/几何修复共享预算。
- 代码/profile/input hash 在提交任务时冻结；不重置预算、不自动从头重跑。

## 验收记录入口

`job.json` 是冻结条件和实际 commit；`status.json` 标明当前 case。
`results.json` 随每件完成保存；`completion.json` / `completion.md` 仅全部结束后产生。
每件 `completion.json` 保留所有版本实际分组/连接、score/reference、checker、比较/决定和模型调用路径。
真实 Engineering→Coder 分组修改→复测→采用或回退才算流程覆盖；
必要检查通过且同参考 O 提高才算效果。NO_PROPOSAL 不算自主修改能力已验证。
当前是提交准备记录，不预填任何案例结果；未合并 master。
