# FixedAssembly 消融开关

实现基准：`action_v2@e5e13fb`。开发分支：`fabrication_ablation`；使用与原仓库并列的独立 worktree。

在请求的 `fixed_assembly.ablation` 中设置一个值；缺省或 null 为完整版。配置冲突直接拒绝，不自动关闭其他配置。

| 值 | 行为 | 必要配置／连带差异 |
|---|---|---|
| review | 不调用 Engineering，关闭指定的三类物理审查；源码／导出错误继续交 Coder | split、visual_only；不能选 topology/standing/overhang，不能 NSV 或 partition_objective；制造几何验证也被跳过 |
| tracing | 保留物理事实，隐藏物理到源码的定位；文件工具仅可读当前源码和脱敏证据 | split；不改变内部检查、定位与提案校验 |
| actions | 不调用 Engineering；保留必要修复的内部 reshape 路径 | split；依赖提案的可选优化停止，记录 optional_optimization_disabled_by_ablation |
| profile_ops | 提示不提供五个截面算子，生成源码执行前拒绝其导入／调用 | split；仅检查生成源码，不修改 core 内部实现 |
| fab_plan | 普通 ObjectPlan → 确定性单打印件计划 → single 流程 | single、require_multiple_parts=false、NSV、shape_editable=true、print_orientation_editable=true；分件编辑关闭，无 partition_objective |

fab_plan 保留同一对象的语义部件描述、关系、checklist 和请求尺度，固定打印件 ID 为 body。转换后保存 plan.json；恢复不再请求 Planner。自动朝向是既有 single 模式的 24 朝向支撑搜索；本次未运行该搜索或 NSV24 评测。

## 接线

- `models.py` 的 FixedAssemblyConfig；`fixed_assembly.py` 的 ablation()、冻结配置、Engineering 调用门及最终记录。
- `service.py` 的请求校验、初始／恢复 Coder、Planner、Image/Code Critic 和修补入口；`actions.py` 的 edit 增加可选 ablation 参数。
- `assembly_topology.py` 的 engineer 入口显式防护，Engineering 反馈和文件上下文从请求接收设置。
- `agent_feedback.py` 的 payload_for_agent 增加可选 ablation/tool_context；`ablation_views.py` 生成 Agent 视图和脱敏证据；`tools/context.py`、`tools/files.py` 实施读取白名单。
- `prompts.py` 仅条件化变换内存中的提示；core 文档和提示资源原件不修改。
- `utils/ablation_source.py`、`utils/asset_executor.py` 在 runpy 前做 AST 检查；`feedback_schema.py` 保留错误原因和算子名。

## tracing 文件边界

原报告、索引、版本记录不移动、不改写。被隐藏的定位按来源版本及角色记录在工作区的 `ablation_private/tracing_*.json`，Agent 无权读取。清理后的证据保存在 `ablation_evidence/sanitized_*.json`，只有本次投影登记的文件和当前 assigned_source 可以读取；路径先 resolve，再检查白名单。JSON pointer 不绕过权限。

原始引用和可回读索引不发送给 Agent；脱敏文件保留物理打印件、空间区域及数值。脱敏仍走原反馈预算，超限保持明确错误。重复投影使用内容标识，不重复写相同文件。此限制针对 Agent 文件工具，不是任意 Python 程序的安全沙箱。

## 默认兼容与执行错误

默认 ablation 不进入 frozen hash；旧 checkpoint 缺少该字段可恢复。manifest 和 assembly_result 始终记录 ablation，默认 null，这是明确新增的元数据。未启用消融时，各角色提示与基准逐字一致。

profile_ops 拒绝 Polygon、linear_extrude、rotate_extrude、loft、hull，覆盖直接导入、导入别名、模块属性和星号导入调用。主错误码保持 GENERATED_SOURCE_EXECUTION_FAILED；详情为 ABLATION_DISABLED_API 与算子名。AST 拒绝没有执行 traceback 时也会保存诊断，下一轮 Coder 仍可修复。它不是通用动态 Python 调用检查。

TabSlot 内部未调用这五个 API；RoundedPad 的内部实现保持不变，不受生成源码 AST 检查影响。

## 验证范围

只运行 stub／纯接口测试；模型、真实生成、仿真及正式评测均未执行。配置开关本身不是消融效果证据。测试命令、结果及排除的原生测试见 `reports/fabrication_ablation_implementation.md`。
