# 静态 Image/Code Critic 审查修正（2026-09-28）

基线 `54e7df1470c9829c5645d73067d39d3c75868ad1`，分支 `fix/visual-critic-review`。
范围来自上一段对话的静态视觉审查计划：结构化分级、新提示词、八视图、最终意见传递，以及末轮审查。

## 实现

- 静态 Image/Code 输出必填 `issues`，每项有 `severity/target/problem/suggested_fix`。控制器根据最终 HIGH 重建 `required_changes` 和视觉批准状态；MED/LOW 留作建议。Code 仍必须成功读取指定源码才能批准。
- Image 根据当前多视图重新判断历史纠偏。Code 确认、降级或驳回问题时必须同时给出源码与视图依据，部件名称存在不能证明位置正确；非必要木纹等细节不强制修补。
- 静态渲染总数仍为 8：6 个环绕视角、顶视、底视。元数据显式关联实际 PNG 文件、标签与附件序号；参考图和候选去重后的映射均保留正确序号。旧无标签资产不猜方向。
- `resolved_visual_feedback` 以本轮 Code 终审为准，没有 Code 时采用 Image 结果。普通、固定装配及 planned/overhang 共享路径把它传给 Engineering/Coder；被驳回的问题不因原始 Image 报告再次成为强制修补项。
- 有工具问题和视觉 HIGH 时使用同一次修补及既有预算。物理结果仍来自原 checker，不由 Engineering 的建议替代。
- 无 checker 的静态最后一轮也执行视觉审查；耗尽预算后拒绝并停止，不再生成未经审查的补丁。
- `view_layout` 贯穿直接执行、固定装配 render-only 与已有队列接口。默认直接渲染布局仍为 orbit，静态工作流显式选择 review_eight；队列本次仅做 mock 参数测试。

## 验证

- 相关回归：**299 passed, 6 skipped，8.61 秒**。6 项跳过的是需显式开启的真实几何测试。
- 测试夹具简化后曾对受影响的三个文件另跑 **41 passed，2.58 秒**；最终 299 项回归已包含全部改动。
- 覆盖 HIGH/MED/LOW、Code 降级/驳回、成功源码读取门槛、普通/装配/共享控制流、混合反馈、单次候选预算、末轮无额外修补、标签/去重映射，以及 CLI 请求和 chat 的新旧保存报告读取。
- 21 个改动 Python 文件语法检查、`git diff --check` 通过。控制流修补测试使用 mock 模型/执行器；不据此宣称真实 Coder 修复成功。

真实渲染全部使用 CPU，512×512、32 samples、BLENDER_EEVEE，关闭物理 checker。三个手写椅子各 8 图；固定装配 T 支架 8 图及原有 2 张拆分图，导出 PASS、几何 NOT_EVALUATED。首轮共 113.72 秒。
初次人工验图发现两个正例夹具靠背与座面意外相隔 0.04 单位，在任何真实 Critic 调用之前修正并重渲染；初始源码、输入和图保留，记录在 `fixture_correction.json`，未根据模型判断挑选输入。

## 真实 Critic 抽查

三个手写诊断样例分别调用真实 Image 和 Code 一次。即使 Image 已通过，也额外调用 Code 以检查其源码读取；这不是一次正常生成/接受循环，不调用 Planner/Coder，不改变样例源码，不运行物理 checker。

使用现有 Stepcode / gpt-5.6-sol，三类均完成，未启用 CLIProxy 后备。

| 诊断样例 | Image | Code 终审 | 结果说明 |
|---|---|---|---|
| 未要求木纹的普通棕色椅子 | 通过，无 issues | 通过，无 issues | 未强制新增木纹；从多视角确认四腿 |
| 前视遮挡／前后腿对应 | HIGH | 保留 HIGH | 均识别四条腿，但认为题面要求的前视对齐未满足；Code 读取相同 x 坐标后仍要求调整投影关系 |
| 源码含 Backrest，但面板错位悬空 | HIGH | 保留 HIGH | 定位 `Backrest.__init__.panel` 中 `x=0.75`，超过座面 x 范围 `[-0.23,0.23]`，结合环绕和顶/底图确认位置错误 |

**遮挡样例不能记作误报消除成功。** 该题面还明确提及前视前后腿对应，透视图中后腿看起来内缩；现有两级 Critic 仍把投影关系判为必修 HIGH。这说明该边界仍有过度约束风险。本轮保留原始结果，不反复改题面或样例追求通过，也不据此宣称模型精度全面改善。

三次 Code 均成功 `read_file(source.py)`，三份样例源码哈希均未变化。最终必修列表来自归一化后的 Code issues，与模型原始 required_changes 分别保存。

实际完成 6 次 Critic 运行、9 次 SDK 记账请求；input 63,613、output 5,012、total **68,625 tokens**，其中 reasoning 2,942（已含于 output）。代理未返回金额，未推算费用；首次连接失败没有返回 usage。


首次启动时 API 因本地 44949 端口无监听而连接失败，日志保留。将代理和抽查放在同一长会话中并先做健康检查后连接恢复；短会话进程清理是推测的启动原因，未经独立证明。未更改模型配置、密钥或上游认证。

## 证据与边界

本地证据目录：`temp/visual_critic_review_20260928/`。

- `regression_final_scoped.log`、`regression_fixture_cleanup.log`：测试结果。
- `render_results.json`、`fixture_correction.json`、`render_contact_sheet.jpg`：真实 CPU 渲染与夹具修正。
- `smoke.py`、`correct_fixtures.py`、各样例 `input.json/source.py/execution_saved.json`：诊断输入及执行脚本。
- `critic_results.json`、各样例 `review_live_proxy/*_input.json`、`*_raw.json`、`*_critique.json`、`resolved_visual_feedback.json`：真实模型输入、结构化输出及工具读取事件。
- `api_launch_diagnostic.json`、`critics.log`、`critics_live_proxy.log`：首次连接故障和本次完成记录。
- `code_snapshot.json`：最终代码与测试 SHA256；`code_snapshot_before_scope_guard.json` 保存最后范围保护前的状态（真实调用使用其中产品实现；之后只增加静态路径条件保护，静态调用内容不变）。各样例输入另保存源码 SHA256。

这是小规模功能抽查，不能推断大样本误判率或真实修补成功率。没有重新生成历史实验、重评旧成绩或启动 GPU；真实物理 checker 全部关闭。既有 FEA/standing 修改、实验脚本和历史文件删除不属于本提交。报告与 memory 更新后只提交本任务改动，不合并 master、不推送远端。
