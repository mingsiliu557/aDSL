# 固定装配：初始分组／连接清单可修订

日期：2026-09-20。实施基线：master `498dba8c60c30478aa98371f5cb02dd6e20069e5`。
已有实验入口、轮次配置、tmux 脚本及历史文件删除等工作区修改均保留，未回退。

## 问题与最小修改

问题存在：导出器在 geometry 和 visual_only 两种模式下，都把实际打印件／连接清单
与初始计划严格比较；专用 Coder 提示同时要求冻结 ID 和归属。这会拦截有依据的分组修补。

- `adsl-core/core/export/export_assembly.py`：仅将 `PART_MEMBERSHIP_CHANGED`、
  `CONNECTION_PLAN_CHANGED` 从硬失败改为 `manifest.plan_changes` 下的可追踪差异。
  按 ID 保存 added、removed、changed（initial/actual），另记录连接列表顺序变化。
  一致时为 UNCHANGED，并非无条件声称方案正确。保留初始计划哈希，不改写计划。
- 两种模式均记录当前 `part_declarations`（ID、components、装配变换）及 `connections`
  （ID、两侧部件／端口、共享参数名、实际参数、双方 frame），不序列化 Asset。
  即使部件网格失败，声明仍保留；现有 `parts` 仍保存成功输出的网格记录，
  `interfaces` 仍保存实际执行的几何测量，visual_only 不伪造这些测量。
- `adsl-agents/fixed_assembly.py`：小型上下文辅助函数先核对 manifest 与源码 SHA256，
  再向审核与后续修补提供实际分组、连接及计划差异。缺失／错版本时明确不可用，
  不将旧计划填成当前事实。修补输入标为 repair_starting_version，审核标为 current_candidate。
  上下文随已有版本审核记录及 retained 发布；不改 working/retained、预算和退出规则。
- `adsl-agents/service.py`：共享 Image／Code 生成审核各增加一个可选装配上下文参数，
  仅在 fixed_assembly 启用时传入。审核顺序不变，不增 Planner 或 Critic 调用。
- `adsl-agents/prompt/fixed_assembly.md`：初始分组／连接是参考实现，默认沿用，证据充分
  才最小调整；组织容器不必是一个打印件，重复件需要独立实例和连接。
  Code Critic 并不加载这份 Coder 提示，因此同样规则已放入实际审核请求，而非只修改文档。

## 保留的边界

未修改 `adsl-core/core/assembly.py`：唯一部件 ID、真实引用、材料／祖先后代不重选、
端口占用、接收件优先树形放置、TabSlot 尺寸及共享参数一致性均保留。
`ROOT_CHANGED`、单位、配合余量、原尺寸约束、数值容差仍按原规则执行；
visual_only 原本未做的尺寸几何测量没有新增，也不声明通过。
网格生成、材料、单位换算、网格复用、落盘几何一致性检查均未改。
四个物理 checker、旧过悬实验、Image／Code 裁决职责未改。

## 本地验证

新增 `tests/test_fixed_assembly_plan_revision.py` 共 15 个模拟／本地参数化测试：

- 初始清单一致，以及 Legs 容器改为四腿实例、各自连接 Seat；后者记录实际五件四连接，
  不再因清单差异失败。CSG 和导出在此测试中模拟，不代表真实椅子已几何通过。
- 同 ID 声明改变、网格失败仍保留实际声明；差异不掩盖网格失败。
- 不存在的接收件、重复端口、共享参数不一致、非法 TabSlot 仍抛错。
- 根、尺度、配合余量变更仍为硬失败。
- 实际共享审核请求及后续 Coder 修补参数中包含可修订规则、对应版本的实际结构和差异，
  无额外 Planner；缺失／过期 manifest 不冒充当前结构。
- 模拟修补成功及全部拒绝：发布的源码、模型、manifest、审核和 retained 哈希对应，
  初始 plan.json 字节不变。普通生成不接收装配上下文。

现有空网格测试替身补齐 components/transforms/connections 三项字段，失败断言不变。

执行命令（使用现有 adsl 环境，不开启 ADSL_TEST_FIXED_REAL）：

```bash
PY=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
$PY -m pytest -q tests/test_fixed_assembly_plan_revision.py tests/test_generation_review_contract.py tests/test_fixed_assembly.py tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_exports.py tests/test_fixed_assembly_diagnostics.py tests/test_fixed_assembly_empty_mesh.py tests/test_fixed_assembly_recovery.py tests/test_service_config.py tests/test_fixed_assembly_existing.py tests/test_fixed_assembly_prompt.py
# 150 passed, 6 skipped (5.28s)
$PY -m pytest -q tests/test_overhang_review_images.py tests/test_overhang_candidate_isolation.py
# 27 passed (2.49s)
git diff --check
```

六个跳过项为显式 opt-in 的真实布尔实验。本轮未调用 API、未重跑 SF13，
未手改生成源码、未启动批次或物理 checker。

结论仅限：初始清单不再锁死合理修补，且实际结构可追踪、审核版本不混用。
没有新增自动判断错误分组的工具；没有证据声称 agent 已修好 SF13 或装配可制造。

## 后续用户授权的独立冒烟（非上述本地测试）

新目录 `local_experiment/fixed_assembly_prompt_regroup_SF13_20260920T160523Z` 从 SF13 原始
文本生成，未读取旧源码／图片；StepCode，visual_only，5轮上限，四checker关闭。
实际2轮、1次修补，454.38秒、10次API、105488tokens；无API／流程错误。
初稿即为6打印件／5接口，未触发分组变更。Image指出顶部突起，Code定位木纹条越界；
Coder将该条长度72改为51mm，第二轮Image通过，retained为attempt_0001。
24项落盘一致性检查及发布哈希核对通过；这验证了生成—审核—修补链路，
不证明真实重新分组修补能力或可制造性。实验资产／API日志留在本地，不纳入代码提交。

## 后续最小补充：任务底线与修补预算说明

基线 `edc0dd5fb27f7fb4ec930f2329744afaf8b68def`。只补两处，不接入 topology 或其他物理工具：

- `FixedAssemblyConfig.require_multiple_parts` 默认 false；当前 prompt-to-3D 实验新输入
  显式冻结为 true。导出器检查实际至少2打印件、1连接，否则记录
  `MULTIPART_ASSEMBLY_REQUIRED` 及实际数量。继续尝试展示，并走原失败反馈／候选保留流程，
  不因 Image 通过而批准。只计声明数量，不检查网格连通或接口配合；通用
  `FixedAssembly.validate()` 未改，单件零连接仍合法。分组／清单差异仍不构成硬失败。
- 修补输入改用 `current_repair_authorized=true` 与
  `remaining_repairs_after_this_attempt`，正文明确本次已预留，0仅表示之后无修补机会。
  数值仍是 `max_rounds - number`；计费、轮次、预留和恢复逻辑未变。

已有冻结输入及历史结果不迁移；未配置该字段的旧任务保持原行为。其他明确要求分件的
调用方也应在任务配置中显式设 true，不靠解析自然语言推断约束。

新增6个任务底线参数化／流程用例，并将原5轮预算用例补成末轮成功／失败两种组合。
包括通用单件不变、任务单件被拒、2件与5件均可通过声明底线、Image不能绕过任务失败、
末轮仍可修补并发布，以及旧冻结配置不被改写。

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_fixed_assembly_plan_revision.py tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_prompt.py tests/test_generation_review_contract.py tests/test_fixed_assembly.py
# 99 passed, 6 skipped (4.24s)
git diff --check
```

没有真实 API／Blender 实验，也未重跑 SF13。实现文件为 models.py、fixed_assembly.py、
export_assembly.py、fixed_assembly 专用提示及实验 run.py；相应测试／README 同步更新。
