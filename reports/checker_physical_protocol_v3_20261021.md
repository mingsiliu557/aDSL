# Checker 与物理评测 v3 实施记录

状态：代码、测试文本与文档已修改；尚未执行验证。
计划日期：2026-10-21。实际核对基准：
`action_v1@691a98e1ce1dbae18d5b2f89a89e09d96a7d71f5`。
用户后续已授权将本轮代码、测试文本和报告提交并推送至 `action_v1`；
实际提交号及推送状态以 Git 记录为准。不合并 master，验证执行限制不变。
工作区已有的无关 FEA 修改及其他文件变动未纳入本轮处理。

## 实际修改与接口

以下均为仓库相对路径；行号是本次编辑后的函数入口，后续以函数名定位。

| 文件／入口 | 修改 |
| --- | --- |
| `adsl-agents/assembly_standing.py:20`，`with_protocol` | 为新执行的正常、提前失败及运输层失败结果附协议 ID、观察／接触诊断；不可测字段为 null |
| 同文件 `:35`，`_validate_physical_inputs` | 检查完整唯一刚体、已有刚性变换与 TabSlot 参数合同；不进行接口几何预验收 |
| 同文件 `:136`，`xml_model` | 明确保留正质量、有限质心及有效正惯量要求 |
| 同文件 `:324`，`analyze` / `_analyze` | 移除实体和 CoACD 接口 PASS 门槛；保留代理真实性检查；完整观察独立确定 PASS/FAIL；深度超限只记录 WARNING |
| `adsl-agents/assembly_physics.py:34,:120`，批处理／CLI | 调度顺序不变；仅对新 Standing 错误／超时补协议与未知观察；不修改其他 checker 的异常语义 |
| `adsl-agents/agent_feedback.py:121`，`standing_summary_for_agent` | 白名单短标量摘要；已知 checker_summary 路径投影及预算计量；不复制接触列表或轨迹 |
| `adsl-agents/service.py:148`，`_checker_evidence` | 把短摘要与真实报告 `/metrics` 引用放入模型实际输入的 checker_summary |
| `adsl-agents/fixed_assembly.py:882` | Critic 先于当前物理检查，因而只携带原来源绑定的上一候选摘要，不伪造当前测量 |
| `adsl-agents/prompts.py:29` | 装配 Coder／Engineering／Code Critic 追加独立评估、告警及历史来源说明 |
| `benchmark/metrics/physical.py:33,:108` | `classify_standing_record` / `summarize_standing`，固定分母、协议／来源核对、终止失败和 pending 分开 |
| 同文件 `:124,:153,:171` | `raw_ivr`、逐案例主指标、同清单多方法汇总及共同可测样本列表 |
| `experiments/benchmark_six/evaluate.py:204,:361,:393` | 复用现有支撑测量器，在既有 G 推荐姿态计量；保存实际逐案例指标和统计记录；pending 不执行评测 |
| `experiments/benchmark_six/build_review.py:25` | 主表为 Standing 成功率／Raw IVR／NSV／N，附全部案例、缺失原因、统计集合；拒绝旧协议混算 |
| `experiments/benchmark_six/run.py:228,:737` | 新冻结源指纹包含 metrics；外层评测超时补终止统计，保留此前已完成的 Standing 观察 |
| `docs/standing_evaluation.md` | 协议、指标定义、接口、适用边界及验证状态 |

公共 `CheckerResult`、`CheckerRun`、`run_assembly_checks()` 签名不变。
未增加 finding category、Agent、调度器或配置开关。

### metrics 路径说明

仓库没有计划中示意的 `benchmarks/metrics/`；现有目录名为 `benchmark/`。
因此新增 `benchmark/metrics/__init__.py` 和 `physical.py`，并接入现有
evaluator/report builder，没有新建并行实验框架。

逐案例结果新增 `physical_evaluation_record`、`standing_evaluation`、
`main_table_metrics`。报告入口写 `review/physical_metrics.json`，并把四项
指标放在 README 主表。旧 G、面积和内部 Dapper 数据作为原证据附件保留，
不继续作为本轮物理主表指标。

## 判定变化与保留条件

删除的前置条件：

1. Standing 不再要求原实体 `interface_measurement()` 全 PASS。
2. CoACD 不再重复要求代理接口几何全 PASS。
3. 超出初始 shell-plus-precision 深度参考不再覆盖 verdict、清空 findings。

保留的条件：

- 原来源／manifest／缓存 hash 校验，完整部件清单及单件连通刚体限制。
- 网格可测、质量和惯量有效、变换／连接参数可解析、负间隙弹性过盈限制。
- CoACD 代理质量检查，原碰撞后端、参数、重力、时间步与摩擦。
- 原倾倒阈值、完整观察时间以及“曾退出后回位仍算退出”规则。
- 所有原必需 checker 的最终验收；Standing PASS 不覆盖 Topology FAIL。

过去仅因接口几何预验收或初始深度超限而 INDETERMINATE 的输入，在碰撞
表示可建立、完整观察结束后可能得到 PASS 或 FAIL。未完成观察仍不可测；
不把 ERROR 改成在线物理 FAIL。`initial_contact_check.verified` 只解释为
满足旧深度参考，已不用于最终 verdict。没有新增告警修补 finding。

原接口退出仍使用声明的 TabSlot 查询几何；无法据此证明真实榫头存在。
接触互穿可能导致异常卡住，必须同时阅读 Topology、Raw IVR 和接触告警。

## 统计口径

- Standing：完整 PASS 才成功；适用且终止的生成／输入／仿真失败计未成功。
  失败原因保留多标签，模拟错误不计作实际观察到倾倒。
- pending：保持 PENDING；存在 pending 时汇总 INCOMPLETE、完整成功率为 null。
- Raw IVR：全部最终含接口打印件的件对原始交集总和 / 各件体积总和，不扣除
  允许的过盈。单件 N/A；缺失 pair 或体积时 null。仅按生产者已有数值容差
  处理有界负零误差，不改变原交集记录或 Topology 阈值。
- NSV：复用现有射线 S/V，在既有 24 朝向 G 推荐姿态上测量，未添加搜索。
  单件不可测使总量不可测；不以 G、面积或零替代 S。
- N：实际完整输出件数；不能因为 baseline 名义为单件，就给生成失败填 N=1。
- 各方法清单与适用性必须一致；IVR/NSV/N 的均值分别使用明确列出的共同可测
  集合，NSV 同时核对测量配置 hash。主表不加覆盖率列，但附件有数量与清单。
- 评测仍用已有统一 150-mm 拷贝及原朝向协议，不修改在线 S/S-D³/τ、
  朝向搜索、候选采用或打印布局发布逻辑。

旧协议结果不自动升级；本轮没有重算雪人，也没有把已有雪人
INDETERMINATE 改写为 PASS。新统计记录只在后续获授权执行时产生。

## 已编写、未运行的验证

| 文件 | 新增／调整的合同 |
| --- | --- |
| `tests/test_assembly_standing.py` | 深度 WARNING 下的 PASS、倾倒、退出及两者同时发生；保留 findings |
| `tests/test_assembly_standing_verdict.py` | 两后端解除接口预验收、代理质量失败仍停止、完整／部分观察、无效惯量、超时和调度独立性 |
| `tests/test_agent_feedback.py` | 有界标量、预算、重复投影、无告警修补权限；Engineering／Coder／Code Critic 实际 stub 输入及 read_file 指针 |
| `tests/test_partition_selection.py` | Topology FAIL＋Standing PASS 的候选仍不能通过最终验收 |
| `tests/test_support_workflow.py` | 原提示之外仅增加明确的协议说明 |
| `tests/test_physical_metrics.py` | 固定分母、pending、多重原因、未知观察、来源／协议冲突、Raw IVR／NSV 空值、共同可测集合 |
| `tests/test_benchmark_six_evaluation.py` | 原朝向与单次支撑测量接线、部分总量、终止／pending、主表生成和旧报告拒绝 |
| `tests/test_benchmark_six_runner.py` | 外层评测超时留存旧证据，同时写 FAIL/TIMEOUT 而不虚构倾倒 |

现有 source/manifest/cache 保护未修改；相应既有回归仍须后续运行。
没有执行 pytest（包括 collect）、编译检查、导入探针、离线脚本或几何夹具。
静态文本核对不能替代上述测试，也不提供物理结论。

## 尚待验证及边界

1. 实际 runtime、依赖版本、序列化、冻结 harness 的模块导入均待授权后验证。
2. 实际接触模型下的独立 Standing 输出，以及完整角色输入的可达性，尚无
   本轮运行证据；当前交付为实现和测试文本。
3. 现有评测的飞行 dragon 适用性排除规则保持并在汇总入口对齐，不依据
   方法成败重新选样本。
4. 新 metrics 文件纳入新批次源指纹；不迁移现有批次或改写旧账本。
5. Topology 容差、mesh 修复、Boolean、FEA、NSV 算法与支撑采纳公式未改。
   工作区的其他 FEA 变动属于之前已有内容，不是本任务交付。

**测试已编写，尚未运行；仿真、模型、诊断及 benchmark 均未执行。
新协议输出的是规定仿真条件下的站立观察，不证明接触模型或实际制造完全正确。**
