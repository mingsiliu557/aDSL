# 雪人初始分组证据（2026-10-20）

基准 action_v1@3cb5edd。本报告仅只读解析已有 JSON、源码文本及 AST；没有执行模型源码、反馈适配器、测试、模型或物理检查。路径以下列档案别名表示，不含主机绝对路径。

- `new/`：support_D_snowman_96k_sol56_20261020T075701Z/jobs/DensiCrafter_21/ours/generation/
- `hat_retry/`：DensiCrafter 档案 hat_body_partition_retry_20261013_v1/

## 结论

鼻子在初始 Planner 的 `plan.json` 中已经是独立 `nose` 打印件，不是 Engineering 拆分或支撑收益比较的结果。冻结 Planner 输入的 `require_multiple_parts=false`；任务要求橙色鼻子，但没有要求鼻子可拆，亦明确写了 no prescribed split count。若该配置为 true，其 API 约束仅为至少两件和连接，不指定鼻子。

实际 Planner 理由（`new/native/plan.json`，`/relations/13`）：

> Keep carrot_nose as its own nose print part so its orange appearance is preserved and it can be oriented along a favorable printing direction without adding support beneath the facial projection.

这是规划假设，不是已测得收益。颜色要求不自动等于独立打印件要求。没有找到本次五件运行中合并鼻子的对照测量。

## 版本链

| 阶段／版本 | 计划／声明／实际打印件 | nose 所属件 | 接口 | 提议与理由 | 是否采纳 | 证据定位 |
| --- | --- | --- | --- | --- | --- | --- |
| 初始 Planner | 计划5件：body_core、hat、nose、left_arm、right_arm | nose，仅carrot_nose | nose_to_head；共4连接 | 上述原文；帽子为平帽檐朝向，双臂为避免横向悬臂 | 是初始计划，不是候选收益采纳 | new/native/plan.json /print_parts/2、/connections/1；new/role_calls/001_plan.json |
| original | 源码声明同5件，执行提前失败，不能写成5件成功导出 | nose | 源码有nose_to_head | 无拆鼻修补提议 | 未合格 | new/native/original/source.py:181、240；参数传了整个shared_parameters字典 |
| attempt_0001 | 声明与实际导出均5件 | nose | 4连接 | 修正connect参数取具体TabSlot实例；未变分组 | 未合格：Topology有8 FAIL | new/native/rounds/round_02/candidates/01_assembly_or_appearance/source.py:181、240；asset/assembly/assembly_manifest.json /parts、/part_declarations |
| attempt_0002 | 声明与实际导出均5件 | nose | 4连接 | repair_attachment_material_boundaries_v2，action=reshape，grouping_change=null；原文要求“Keep all TabSlot dimensions, the 0.2 mm fit allowance, print-part grouping, print orientations, overall dimensions, and visible poses unchanged.” | 未合格：Topology 18 PASS、1 INDETERMINATE；鼻子INTERFACE_EVIDENCE_INSUFFICIENT；Standing未能判定 | new/native/rounds/round_03/candidates/02_repair_attachment_material_boundaries_v2/source.py:227、286；对应manifest；前版engineering_critique.json /required_changes/1 |

`original` 文本已分别构造 SnowBodyCore、TopHat、CarrotNose、两份 TwigArm，并传给不同 add_part。文本独立构造不证明材料互不交叠。第一次可测候选已有接口交叠；后来接口边界修补未改变计划。账本最终 working=attempt_0002、retained=original、qualified=null，停止原因为 assembly_checks_unverified_no_executable_feedback；未进入可选支撑优化，不存在被采用的五件收益结论。

## “只拆帽子”的旧记录：已找到，但未采用

`hat_retry/native/rounds/round_02/candidates/01_standing_base_contact_01/engineering_critique.json` 保存 proposal `partition_split_hat_01`：从 snowman_complete 中拆出 top_hat（components=['hat']），其余语义材料留在主体，新增一个隐藏TabSlot。它是旧 area/Dapper 路径的两件候选，不能混成新NSV五件初始计划。

其原假设是 G 下降超过77.94598031441433体素、两件G<270.0540196855857可超过旧分数1086.0；这些是提议门槛，不是候选实测收益。attempt_0002 未采用；隔离续修的 attempt_0004 也未采用：源码执行 ValueError “interface dimensions and explicit overlaps must be positive”，候选测量 NOT_EVALUATED，接口未验证。源码静态声明还使用 top_hat_brim/top_hat_crown，未落实授权的hat语义ID。最终保留合格单件 attempt_0001。`result_summary.json` 的 effective_stop=remaining_repair_budget_exhausted，与账本保留的 isolated_retry_after_proxy_restore 原始停止标签分开记录。

没有通过新测量解释或修复该旧失败。本次仅给出档案事实。NSV初始分组准则仍是待验证启发式；不会覆盖显式可拆任务要求，也不禁止小特征独立。

## 文件身份

以下SHA-256来自只读文件；完整资料保留在原档案，没有复制原始模型请求到公开报告。

| 档案相对文件 | SHA-256 |
| --- | --- |
| new/native/plan.json | `707de141bea54f35cdeed97fedba615ce610cf1ba7ce701951f39d8a5a0cb9fe` |
| new/native/user_input.json | `7180e763cf070512733c2ed5c05eb010273be60ad9e07018d4ca8dab6e576ef0` |
| new/role_calls/001_plan.json | `9e94cdb4b717a4128ccd635587307154b561dc1b5e1cb0314a89d3f60bdd7bfc` |
| new/native/assembly_versions.json | `008ecc2bc4b76bd8cfbd2b2aed17bced788708ea4d1cc6e9efef15985aa067a0` |
| new/native/original/source.py | `fc30a4165ddd1d86d694825a1f75ef9583bff4a282552c288c0a73d0c2defddb` |
| hat_retry/result_summary.json | `d5f6f48d952c91bf12d7e733994b52f8f896e9f8a8046154dcc4cbf860801d08` |
| hat_retry/native/assembly_versions.json | `7ab966469922696d17682895528a0192fbf774fc8d4fe27de29500152a8a936c` |
| hat_retry/native/rounds/round_02/candidates/01_standing_base_contact_01/engineering_critique.json | `aa152dd8e021ac583fc4db17937ec90b3b0307f75a7ac9c9e76d19c4813c5e2c` |
