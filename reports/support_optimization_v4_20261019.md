# 支撑体积判定与形状优化：实施记录

## 状态与范围

基准为 `action_v1@ccb19d76da9c5e8e9b7ea76e30d0925c732090f0`。
当前工作在同一分支，只有本任务文件进入本地提交。既有报告删除、FEA、GPU 及其他实验改动未纳入。
未合并、未推送、未启动或恢复实验。

**测试尚未运行；未执行测试收集、编译检查、smoke、真实模型、真实物体、物理仿真或 FEA。
真实模型调用为零。没有实验结论或制造性能验证。**

本记录中的“实现”仅表示源码及测试已编写，不表示行为等价、数值精度或完整工作流已经运行验证。
执行期间只进行源码阅读、文本编辑和 Git 本地提交；编辑脚本没有导入项目模块。

## 实际接口与接线

| 文件／入口 | 实现内容 |
|---|---|
| `adsl-agents/support_volume.py:46`，`support_volume` | 毫米网格副本、现有过悬掩码、投影面积与重心向下射线；后端错误区别于真实无命中 |
| 同文件 `aggregate` | 所有声明打印件必须完成；汇总 ΣS/ΣV，失败不产生成功子集总量 |
| `adsl-agents/actions.py:90`，`regroup_precheck` | 默认 area 原规则；NSV 额外支持已授权的无 grouping_change 形状提案 |
| 同文件 `support_postcheck`、`print_pose_postcheck` | S/N 局部规则及 A 的真实 parts 姿态检查；旧 Action 执行接口未改 |
| `adsl-agents/assembly_overhang.py:195`，`_support_measurements` | 非分组实际姿态／D 的 G 推荐姿态下测量；独立支撑证据文件、测量合同 hash、机会与汇总状态 |
| `adsl-agents/fixed_assembly.py:257` 起 | 检查点保存/恢复、绑定证据、shape-only 起源、声明/root 约束及参考刷新适配 |
| 同文件 `iterate_fixed_assembly` | A/D 采纳、候选内部修补、机会筛选、停止出口及 retained 发布接线 |
| `adsl-agents/assembly_topology.py:487`，`engineer` | 同一配置的预检参数、NSV instructions、显式 support_evidence；不把内部 Dapper 指标作为 Agent 目标 |
| `adsl-agents/prompts.py:23` 起及 `prompt/support_optimization.md` | area 原拼接；NSV 定点替换冲突政策，保留建模 API |
| `adsl-agents/service.py` 的初次／恢复 Coder | 传递提示参数，模型配置视图移除替代评分目标，不修改冻结配置 |
| `adsl-agents/agent_feedback.py:622` 起 | 支撑证据白名单、原反馈预算、可选区域预览压缩、当前与比较基线的明确身份 |
| `docs/support_optimization.md` | 配置、公式、限制、回退与接口说明 |

公共新增配置仅为 `support_metric` 和严格布尔的 `shape_editable`。
缺省值只读，不回填冻结配置。`models.py` 与模型输出 DTO 没有修改。

## τ 的精确定义

`τ = 基线层高 × 基线所有风险面投影面积之和`，单位 mm³。
这是具有层高含义的工程容差，不是打印机精度或算法误差上界，也不是唯一支撑底面积。

- ΔN > 0：ΔS < −τ 才接受支撑收益。
- ΔN = 0：同样要求 ΔS < −τ。
- ΔN < 0：允许 ΔS ≤ τ。
- −τ 边界不算明确下降；合并可接受 +τ 边界；τ=0 合法。
- NSV 比值不参与采纳；增加 V 而 S 不变不能使同件数修改通过。

局部规则不证明全局无循环。既有轮次和修补额度不变。

## 生命周期和回退

每个可选候选固定 support_baseline_version。续修的 assigned source 是最新 working；
S/N 和 τ 仍来自原基线。短证据额外保留 comparison_baseline 的来源和总量，避免把本轮父版本当作比较基线。

检查点保存基线源码 hash、retained、qualified、partition_reference（含 None）和无候选提案的基线反馈。
统一恢复函数在拒绝、NO_CHANGE、不可比较、错误和预算结束路径恢复 working、retained、qualified、参考、反馈，
清除本次 active_partition_intent；候选源码与诊断仍作为历史保留。
外层异常仅在 NSV 有活动检查点时执行同一恢复，然后沿用原异常传播。

模式 A 从真实 parts 读取旋转，允许贴床平移；首次实际输出和发布前都检查。
源码执行失败尚未产生几何时不推断姿态；第一个真实输出仍须满足单件、直立要求。
初始实际多件或旋转不符合不会自动启动“修复实验模式”的新任务。

模式 D 的 shape-only 续修必须从绑定版本链恢复实际记录的原提案，不凭缺少 intent 放行。
语义归属和 root frame 破坏不被新参考掩盖；允许的主体变化才刷新参考，并使用刷新后的 S/姿态判断。
默认 area 的 Dapper 与主体保持分支保留；内部参考完整性自比较不是 NSV 收益判据。

## 已编写、未执行的测试

| 文件 | 编写的契约 |
|---|---|
| `tests/test_support_volume.py` | τ 三种件数分支及边界；零支撑；体积稀释拒绝；完整加权汇总；配置和姿态；人工立方体/T 形；缩放；向下命中、自命中、后端失败 |
| `tests/test_support_workflow.py` | A 采用/拒绝、独立必要修复和内部修复；检查点/None 参考恢复；D shape-only 与分组；刷新后拒绝；预算/NO_CHANGE/错误/缺测/恢复；原配置 hash；实际输入及提示接线；反馈幂等与预算 |
| `tests/test_support_overhang.py` | area 不新增字段；NSV 缺测不为零；已知异常与程序错误区分；输入不变；机会权限；缺声明不汇总子集 |

没有收集或运行这些测试，故不报告参数化用例总数、通过数、跳过数或耗时。
人工几何和原生依赖只出现在待执行测试中；未安装依赖来验证它们。

用户另行授权后可先运行新测试，再运行已有 Action、反馈、分组和 fixed-assembly 相关回归。
本轮没有执行这些命令，也没有使用历史案例代替接口验证。

## 与原提交顺序的差异

1. 第一提交 `84c85d7` 保存测量、Action、Overhang 接入与基础测试。
2. 第二提交 `2a80415` 同时保存 A/D 共用的基线、恢复、提示和控制器接线；没有将同一循环中的 A/D 状态处理拆成临时不完整提交。
3. 后续收尾提交保存人工源码审阅中补齐的边界、测试和本文。具体 SHA 见本分支 Git 日志。

NSV Planner 同样读取既有 fixed-assembly 提示资源，因此将相同模式参数传到该既有入口，
防止初始计划仍收到旧评分指令；没有新增 Planner、输出 schema 或规划步骤。
初次配置的模型视图使用 partition_objective_enabled 表示该机制启用，隐藏其内部评分参数；
它不是第三个用户配置，也不写回运行配置。

控制器循环多了一层异常恢复保护，因此 diff 中有较多缩进变化。没有新增调度器或独立候选控制器。
报告使用仓库相对路径，不公开服务器用户名或环境目录。

## 未验证事项与后续前提

- 默认 area 的精确等价、所有新分支的语法和运行行为尚未通过测试/编译检查。
- Embree/trimesh 后端可用性、数值自命中容差、重心采样误差、实际推荐 STL 姿态一致性尚未实测。
- 射线代理可能受网格三角化影响；不宣称等于 SEG 完整实现或切片器耗材。
- 保持名义尺寸依赖原任务与现有约束，本轮没有新增尺寸证明器或自动归一化；缺少统一尺度协议时不能宣称同尺寸收益。
- 支撑采用规则不能证明真实物体拆件成功、站立改善、面积下降或制造性能提升。
- 必须在获得运行授权并验证这些测试后，再决定是否用于实验。没有自动恢复 benchmark。
