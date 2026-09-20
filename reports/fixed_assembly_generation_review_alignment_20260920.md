# 固定装配：回到 aDSL 生成审核入口

日期：2026-09-20 UTC。代码修改与轻量验证报告，不是新模型实验报告。

## 基线与核对依据

实施起点为本地/远端 master `1299bb707931c1f1c436eb0947acc552e8b72d5c`。
保留工作区已有的报告删除、GPU/tmux脚本修改及实验资产；本轮不提交这些无关变更。

只读获取并逐字核对了[官方固定提交](https://github.com/sig-pku/aDSL/tree/0c10f36a459bf1033e89e2e5dfcf451bc30043b0)：
`adsl-agents/service.py`、`prompt/critic_image.md`、`prompt/critic_code_image.md`
均与仓库保留的官方初始版本 `4d9c1bb` 一致。两份核心Critic提示在我们的当前版本也未改。
因此问题不只是提示正文，而是新增入口实际赋予的任务、上下文与调用条件不同。

官方逻辑见[service.py](https://github.com/sig-pku/aDSL/blob/0c10f36a459bf1033e89e2e5dfcf451bc30043b0/adsl-agents/service.py)：
Image评审当前生成物；Image拒绝后Code读源码，可以纠正Image并提前批准。
这是原版已有行为，不应把所有冲突归因于connector，也不承诺恢复入口即可消除误判。

## 源码对照与修改

| 项目 | 官方 aDSL | 1299bb7固定装配 | 本轮修改 |
| --- | --- | --- | --- |
| 任务 | 当前物体是否满足需求/规划 | candidate_preservation，偏向保留既有外观 | 共用普通生成审核函数，不再使用保留性审核 |
| Image输入 | 需求、checklist、轮次/上限、历史、Code纠正 | 缺对应字段，附装配诊断 | 恢复普通字段，历史随版本记录保存并用于续跑 |
| Code输入 | 需求、完整plan、当前源码路径、Image判断、历史 | 保留性提案和诊断 | 恢复普通字段，另提供冻结装配配置及现有API参考 |
| 图片 | 用户参考图+当前渲染 | 参考图+baseline+candidate，首轮可能重复 | 只提供参考图+当前渲染，不把失败初稿作为视觉基准 |
| Code触发 | Image拒绝后调用 | 几何非PASS也触发，包括NOT_EVALUATED | Image拒绝或无可用渲染；未测几何不触发额外Code调用 |
| 可显示信息 | 不证明语义形状完整 | complete/shown随诊断传入 | 改名display_available，仅控制器使用；不作为正面审核证据 |
| 裁决 | Code可纠正Image | 保留该权限，叠加导出/几何状态 | 保留原版纠正权限；不新增“两个Critic必须都通过”规则 |
| 制造检查 | 无此扩展 | 可选几何验收及四checker隔离 | 保留，不强制启用；visual_only几何仍NOT_EVALUATED |

### 实际改动位置

- `adsl-agents/service.py`：从普通 `_iterate()` 提取 `_review_generation_image()`、
  `_review_generation_code()`，固定装配调用同一函数。普通流程顺序不变。
  `_review_candidate_appearance()`仅保留真正的编辑保留性用途，旧过悬实验不改审核目标。
- `adsl-agents/fixed_assembly.py`：切换共享生成审核、传入plan/checklist/轮次/历史；
  保存历史及Code纠正供后续轮次/恢复使用；移除失败初稿基准图和正面展示状态。
- `adsl-agents/prompts.py`：Image不再附加给Planner/Coder的装配任务；Code只附现有connector API文档。
  核心Image/Code提示及源码阅读约束不变。
- `adsl-core/core/export/export_assembly.py`：诊断`complete`改名`display_available`，
  明确`semantic_completeness=NOT_EVALUATED`；读取端兼容旧manifest，不重新生成历史资产。
- `adsl-agents/utils/asset_executor.py`：诊断图保留未验证声明及实际缺失警告，
  不再用“无无效/缺失部件记录”的字样暗示外观完整。
- `experiments/fixed_assembly_existing/run.py`：旧资产改造实验将原资产图显式作为任务参考图传递，
  避免共享审核后丢失其合法保留依据；没有执行该实验。

装配导出、working/retained、候选隔离与预算适配器仍存在。
本轮没有把整个固定装配调度器硬并入大型多checker `_iterate()`，也没有删除旧geometry模式。
例如原版普通流程最后一轮的跳过审核行为未改变；装配保留已有最终轮审核和版本保存。
本轮统一的是审核职责及实际请求，不声称所有调度细节已与官方完全相同。

## 当前运行边界

本阶段配置使用 `fixed_assembly.validation_mode="visual_only"`：

1. Planner一次规划；Coder一次生成完整程序，使用FixedAssembly/TabSlot成对接口及接口定位。
2. 执行并尽量渲染；打印件、总装/拆分继续复用同一份网格。
3. Image按需求和规划评当前图；拒绝则Code读当前源码，必要时交Coder在原预算内修补。
4. 重新执行、渲染、审核；发布仍绑定retained对应资产与结果。

无图时明确跳过Image而不是伪造通过；Code可继续源码诊断。
部分展示不能确证完整外观，仍保留控制器阻断批准的保护。
导出一致性保留；视觉批准不表示几何/制造通过。
`geometry`仍为向后兼容默认值，调用方须显式选择本阶段visual_only配置。

topology、standing、overhang、FEA的实现、工具入口和故障隔离均保留且未修改。
本轮不调用、不记录为PASS，不提前接入装配物理分析。
后续仍须分别确定打印件内部连通、接口连接假设、分件打印方向及接口传力条件，
再接回统一反馈；本次不实现这些适配。

## 验证

执行命令（仓库根目录）：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_diagnostics.py \
  tests/test_fixed_assembly.py tests/test_fixed_assembly_recovery.py \
  tests/test_fixed_assembly_exports.py tests/test_fixed_assembly_empty_mesh.py \
  tests/test_fixed_assembly_prompt.py tests/test_fixed_assembly_existing.py \
  tests/test_generation_review_contract.py tests/test_checker_fault_isolation.py \
  tests/test_checker_unification.py tests/test_planned_prompt_generation.py \
  tests/test_overhang_candidate_isolation.py tests/test_overhang_review_images.py
```

结果：**217 passed，6 skipped，6.76秒**。跳过的是需要显式开启的真实几何验证；
本轮没有调用模型API、启动Blender/FEA实验、重跑SF03或修改任何生成的模型源码。
部分导出单测用小型网格做真实文件序列化比较，不代表真实新资产验收。

新增/调整的模拟证据包括：

- 实际请求构造经过共享函数：Image拒绝→Code要求修补→mock Coder→下一轮Image；
  检查需求、plan/checklist、轮次上限、历史与当前图确实进入请求，失败初稿图片不重复加入。
- Image通过且几何NOT_EVALUATED时只有一次Image调用，不额外调用Code。
- 无图仍调用Code；可出诊断图的失败仍可进入修补；预算结束仍保存候选/未通过状态。
- 中断恢复保持原上限、历史及纠正意见，不重置已预留修补次数。
- Code推翻Image的原版行为仍被测试明确保留。
- 旧过悬候选隔离、checker故障隔离和普通生成配置回归通过。

## 结论与剩余问题

已修复“从prompt生成却使用编辑保留性审核”的接入错误，补齐普通生成上下文，
去掉展示记录对形状完整性的暗示。connector构造、网格复用和全部物理工具保留。

**尚未证明SF03缺损已修好。**两个已有stepcode失败/误判记录原样保留；本轮没有真实重跑。
原版Code偏向源码并可推翻Image，仍可能在CSG实际输出与源码意图不一致时误判。
这次不增加裁决规则、预算或几何修补机制。真实误判是否减少，需后续授权的小规模运行验证，
不能用模拟调用链通过代替外观或装配成功。
