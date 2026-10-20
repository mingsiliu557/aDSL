# 反馈预算 v3 第一期：实现与隔离重放

基准：`action_v1@095a263e45f0862e7db5f3448361f0d49a3e4384`。
报告按计划日期命名；实际记录 UTC 时间见同名 JSON。

## 结论与边界

第一期代码及雪人 Coder 离线重放已完成。以同一重建输入、相同副本根路径比较：
旧行为准确复现 **35,559 字节**；严格去重后为 **29,822 字节**，低于仍为
32,768 字节的上限 **2,946 字节**。只删除 `/feedback/support_evidence`，保留顶层完整支撑摘要。

这说明该雪人输入的预算阻塞可以由去重消除，不表示真实 Agent 修补、站立或制造性能已经验证。
最初交付只编写测试并执行隔离重放；随后按用户复查要求补齐重复投影边界，
已运行 **93 项反馈定向测试、301 项相关回归**，结果见文末。没有调用真实模型、
执行真实案例、运行物理仿真或 benchmark，没有实施第二期。
首次实现已提交并推送至 action_v1 的 `56a43de`；后续边界补丁由本文件所在 Git 提交记录，推送状态以远端 action_v1 为准，不合并 master。

## 实际实现

| 文件／入口 | 修改 |
| --- | --- |
| `adsl-agents/agent_feedback.py`：`_feedback_view` | 原 feedback_only 等价提取；预算字段、递归范围和严格紧凑 JSON 编码不变 |
| 同文件：`feedback_size_report(payload, *, limit=15)` | 总大小及两组最多 15 项的字段大小；仅 pointer 和 bytes；父子占用不可相加 |
| 同文件：`_deduplicate_support_evidence` | 在现有 NSV 识别、评分过滤后、原投影前去重；完整严格 JSON 和三项来源必须相同 |
| 同文件：超限出口 | 保持错误码及 actual_bytes/limit_bytes，附加有界字段统计；成功 payload 不加统计字段 |
| `scripts/feedback_size_report.py` | 显式输入说明、必要文件复制、进程内引用映射、同根路径旧/新重放、快照归档、pointer/hash 检查及再次投影 |
| `adsl-agents/prompt/support_optimization.md` 与使用文档 | 明确顶层支撑摘要与比较基线位置；不改目标、权限或采纳规则 |
| `tests/test_agent_feedback.py` 与五件雪人 JSON 夹具 | 第一期开关、来源、预算及重复投影断言；后续运行结果见文末 |

`payload_for_agent()` 公共签名不变；不修改 `_support_for_agent()` 的数值、items schema、
区域上限或现有压缩顺序。顶层缺失可提升合法证据，顶层异常不覆盖；来源缺失、冲突或不同版本
不合并。只处理已知反馈容器，不扫描任务数据、不进入 comparison_baseline。
去重事件只用于重放报告，不写回模型 payload，不放入引用占位对象。
控制器、service、Engineering 调用方、checker 原始 domain 及回退逻辑没有修改。

## 输入重建与隔离依据

资料来自已有 `support_pilot_D_local_sol61_20261019T162619Z` 的 `DensiCrafter_21`。
因超限发生在 coder_agent_input.json 写出前，适配前 payload 未被原样捕获，本次必须标记为
**reconstructed_pre_projection**，不能冒充 captured_pre_projection。

- assigned source 为 `rounds/round_02/candidates/01_assembly_or_appearance/source.py`。
- source_version 为 `original`；候选副本、父源码及反馈 hash 一致，均为
  `2ec7cba8eacf2610820a009d8f0a85c0dc60a2da2afb123a58ce4419a2d7863b`。
- 依据账本 feedback、原版本 reviews、runtime_config 的冻结 request、plan.json、proposal.json
  及原调用点静态常量重建；不执行控制器。提案为必要 reshape，不是支撑优化候选。
- assembly_context 只保留原 helper 返回字段，version_role 还原为 repair_starting_version，
  不带入 current_candidate 评审现场额外附加的字段。
- report_ref、evidence_files、修补余额及 NO_CHANGE 合同按真实调用恢复。
- 检查的六份源资料 hash 均未变化；完整重建依据和局限保存在重放说明中。

复制了 35 个必要依赖文件。基准及去重版本重用同一 `replay_final/workspace`，每轮恢复初始副本。
副本是普通文件；原 JSON 证据字节不改写，原绝对引用由仅限重放进程的 resolver 映射到副本。
两轮各自归档诊断快照；未发现缺失依赖，原文件及传入 payload 均未修改。
副本输出大小与历史 35,559 字节一致，本输入的对照未观察到路径映射造成的字节偏差。
不过原始 payload 没有直接捕获，时间现场一致性仍属于有依据的重建，而不是原请求捕获证明。

本雪人没有实际 Engineering 调用记录，因此 Engineering 实际前后重放为 **NOT_AVAILABLE**；
没有构造虚假的同案例 Engineering 请求。单份 Engineering 兼容测试在后续定向集合中已通过。

## 真实前后数据

| 阶段／结果 | 统计补丁下旧行为 | 严格去重后 |
| --- | ---: | ---: |
| input_selected | 37,468 | 37,468 |
| 既有 NSV 评分过滤后 | 37,468 | 37,468 |
| deduplicated_selected | 37,468 | 29,756 |
| final_selected | 35,559 | 29,822 |
| 距 32,768 字节上限余量 | −2,791 | +2,946 |
| 输出引用检查 | 47/47 可读 | 41/41 可读 |

**局部去重节省 7,712 字节；最终请求反馈节省 5,737 字节。**
不能把从 input_selected 到 final_selected 的全部下降称为去重收益。
原压缩流程在新输入上较早停止：历史保留由旧最终的 194 字节增加到 701 字节，
其余 preview 元数据也有所变化，因此局部节省与最终节省不同。

原最终反馈中的两份支撑证据各占 5,720 字节；新最终只剩顶层这一份，内容与旧最终支撑摘要
严格一致。typed_findings 保持 18,338 字节、checker_summary 保持 1,814 字节，二者内容未变。
上述父子字段存在包含关系，不能相加当作总大小。

支撑总量、τ、件数、装配尺寸及控制标志均保持精确相等。对新的完整输出再次执行同输入适配，
结果一致，诊断快照文件集合及 hash 不变。这是该实际重放输入的证据，不是对任意输入的普遍测试结论。

实现指纹及有界字段分布在同名 JSON 中；统计快照 SHA256：
`6140465c97ecf119885b3c163163acf952fff3cf3055cec66cc27a7d548bc64d`；
去重实现 SHA256：`cba7e4502d5422c875889e561327d509cbc34ba6c721dd95dd1b6bc288ad3765`。

本地完整材料位于 `temp/feedback_budget_v3_phase1_20261020/`：
重放说明、重建 payload、统计基准快照、原始 stdout、按实现 hash 归档的结果、
reference_checks.json、final_feedback_view.json 及新版本 agent_payload.json。
该目录未加入提交；同名交付 JSON 保留不含服务器绝对路径的关键证据。

复现命令（使用已有 aDSL 环境；保留现有结果时另选新的 output）：

```bash
"$ADSL_PYTHON" -B scripts/feedback_size_report.py \
  --input temp/feedback_budget_v3_phase1_20261020/replay_input.json \
  --output temp/feedback_budget_v3_phase1_20261020/replay_final \
  --adapter-source temp/feedback_budget_v3_phase1_20261020/agent_feedback_stats_only.py

"$ADSL_PYTHON" -B scripts/feedback_size_report.py \
  --input temp/feedback_budget_v3_phase1_20261020/replay_input.json \
  --output temp/feedback_budget_v3_phase1_20261020/replay_final
```

旧行为按预期返回超限并以 exit 1 结束；新行为返回 PASS/exit 0。
这两个 exit 表达输入适配结果，不是测试套件或生成结果。

## 第一期测试与第二期建议

新增断言覆盖：输入不变、已知容器去重、来源与完整内容冲突、缺来源、非有限值、
顶层提升和异常顶层保留、事件有界、比较基线/标志不变、Engineering 单份与 area 兼容、
严格数值/矩阵保持、重复投影、统计范围及 pointer 转义、超限统计和真实五件支撑结构。
夹具只含实际支撑结构，不将其冒充完整历史 Coder 请求。
上述测试最初未执行；后续验证结果单独列在下节，不将旧重放当作 pytest 证据。

建议第一期收口，**暂缓第二期 A/B**。当前输入去重后已可容纳，且引用完整；无证据要求新增
S1–S4、提高上限或改控制流。本次失败调用属于必要修补，没有活动支撑优化基线，
不能用“放弃可选优化”来掩盖其失败。

剩余空间约 2.88 KiB，单个五件案例不能证明 8–10 件可容纳。扩大实验前若另获授权，
应先做代表性多件反馈验证，再按占用选择最小第二期修改；本期不自动执行。

## 复查补丁：完整入口的重复投影边界

补丁基于 `action_v1@56a43deefdc3def2e7101ac484701d3036bf3580`。
先在完整 `payload_for_agent()` 入口添加测试：相同来源、仅 raw-only 字段不同，
或仅说明第 240 字符后的内容不同；覆盖五种已知反馈容器。未修复时 **10 项全部失败**，
均表现为第二次投影误删第一次保留的嵌套证据。

修复在同来源冲突的证据旁保留 `support_evidence_provenance`，含输入内容与当前摘要的
严格 JSON SHA256。只有当前摘要 hash 匹配才沿用原指纹；输入改变时重新绑定。
不同的旧预览即使来自同一原始记录，也保留其输入差异。原投影和原预算裁剪后同步摘要 hash，
两项标记计入原 32 KiB 预算。它不是来源授权、checker 结论或测量有效性证明。
普通单份证据及真正相同的副本不增加标记。没有修改采纳规则、数值精度、区域上限、
控制器回退、公共函数签名或增加压缩级别。

| 最终验证集合 | 实际结果 |
| --- | --- |
| `tests/test_agent_feedback.py` | **93 passed，0 skipped，28.81 s** |
| 支撑三个文件、Action、分件、源码反馈、评审与恢复回归，精确清单见同名 JSON | **301 passed，0 skipped，1 deselected，43.49 s** |

新增边界覆盖完整入口两次投影、已截短说明、原始差异字段、既有区域压缩、陈旧指纹、
缺顶层时的提升及删除副本后的标记清理；继续断言原输入、矩阵和数值不变。
支撑单测使用人工 CPU 几何；工作流、模型和物理检查调用均使用 stub。
排除项是已有真实几何干涉测试 `interference_real_geometry`，未启动它。

首次相关回归为 299 passed、2 failed、1 deselected。两项失败均因已被清理的历史夹具
`reports/benchmark_six_boolean_failure_20261007/source.py` 缺失，尚未进入断言。
仅把 Git HEAD 中原样源码复制到本次 temp 目录，将这两项的读文件路径临时映射到副本，
未恢复或修改已清理的目录；它们只检查构造树及源码索引，不导出/测量该物体。
最终整个相关集合在此隔离夹具下得到上述 301 passed。
另有一条 pytest 启动时 anyio 已导入的 rewrite 警告，不涉及业务断言。
各次重复执行不累加为更多已验证配置。

最终实现 SHA256 为 `237ed5a0939cf552bfcd68801ef1ea0d2c382d934db0096c5601ceb47222610e`。
再次隔离重放同一雪人重建输入：**29,822 字节、余量 2,946 字节，41/41 引用可读**；
完整输出与第一期相等，再次投影相等，输入、六份源资料及诊断快照未改变。
这仍是已有五件雪人输入的重放，不是重新生成或真实 Agent 成功证据。

日志、JUnit、精确回归清单、Git 夹具绑定及重放产物在
`temp/feedback_budget_boundary_20261020/`；不含服务器绝对路径的摘要已写入同名报告 JSON。
复现入口（使用现有项目环境）：

```bash
"$ADSL_PYTHON" -B -m pytest -q -p no:cacheprovider tests/test_agent_feedback.py
"$ADSL_PYTHON" -B temp/feedback_budget_boundary_20261020/run_regression.py
"$ADSL_PYTHON" -B scripts/feedback_size_report.py \
  --input temp/feedback_budget_v3_phase1_20261020/replay_input.json \
  --output temp/feedback_budget_boundary_20261020/replay
```

第一期的此项边界和定向验证已完成。**8–10 件代表性真实反馈仍未验证**；
扩充多件 benchmark 前应先取得并离线验证相应反馈，若超限再按实际大字段选择第二期最小改动。
本次不启动新生成、不扩充 benchmark、不自动实施第二期。
