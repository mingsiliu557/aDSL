# 固定装配物理工具：当前问题清单

日期：2026-09-22。整理基线：`766db7da00b98e8583adcdffa1970c957119879a` 加本轮固定装配物理工具改动。
本文区分已观察事实、尚未确认的原因和建议；不表示问题已经修复。
本次只归档代码和讨论，不改运行中的模型、参数、代理或实验进程。

## 1. SF03 的 FEA 接口从属节点没有全部绑定

证据来自全尺寸 SF03 **round_01**，不是最终成绩：

- 6 个打印件的体网格均有效；5 条接口的几何配对检查通过。
- CalculiX 已执行，但报告 3 个节点 `no opposite master face`，未建立对应的 tied MPC。
- 当前 FEA 正确保留 `INDETERMINATE / INTERFACE_BINDING_UNVERIFIED`，不是结构强度 FAIL。

| 从属节点 | 所属打印件 | 接口局部位置（mm） | 到已选主面的最短距离 |
| --- | --- | --- | --- |
| 101299 | leg_front_left | (-19, 9.5, 0) | 约 0.199997 mm |
| 113298 | leg_front_right | (19, 9.5, 0) | 约 0.199997 mm |
| 136718 | leg_rear_right | (19, -9.5, 0) | 约 0.199997 mm |

三者均位于榫头根部、槽口边缘，局部 Z=0；绑定距离容差约 0.200801 mm。
只读测量证实：已选槽壁中存在距离满足配置的面。因此不能直接解释为间隙过大、
腿断开或网格不可生成。求解器警告也区别于“已找到主面但距离过远”。

**尚未确认：**主面三角化、边界邻接搜索或投影步骤中，究竟哪一步使求解器未找到
对应面。位置和距离证据支持优先检查槽口边缘绑定，不足以独自证明某个内核 bug。

最小后续方向：只复用该 deck 和节点/面集合，核对这 3 个边界节点的实际主面查找；
不先修改椅子、不删除节点、不扩大配合间隙或数值容差。

代码：`experiments/load_bearing_structural_performance/assembly.py::tie_regions()`、
`write_assembly_deck()`；`adsl-agents/assembly_fea.py::analyze()`。
本地证据根目录：
`temp/assembly_physics_SF03_fullsize_20260922T085200Z/SF03/generate/rounds/round_01/checkers/assembly_fea/`。
其中 `assembly.stdout.log`、`assembly_WarnNodeMissTiedContact.nam`、`regions_and_ties.json`、
`part_mapping.json` 和 `assembly.inp` 保留完整依据；大模型/求解文件不随此次代码推送。

## 2. T 支架的位移协调复核存在另一项未解问题

这与上面的“求解器明确报告未绑定节点”不是同一个已证实故障。

已有 T 支架离线诊断发现：榫槽角点附近，两张候选主面的距离几乎相同，
分别约 0.19999980927 和 0.20000004768 mm，但位移插值残差明显不同。
后处理选到的最近面，可能不是求解器实际建立 MPC 时采用的面。
**尚未取得真实 MPC 对应关系，不能宣布绑定错误或后处理已经修复。**
不能按“哪个面位移残差更小”回选来制造 PASS；需依据真实绑定关系复核。
证据：`temp/assembly_physics_smoke_v1/fea_short_diagnostic/result.json`。

## 3. Standing 的瓶颈在碰撞代理生成，不是 5 秒重力仿真

- 当前每个打印件是独立刚体；为了保留榫槽凹形，先用 CoACD 分解，再进入 MuJoCo。
- 全尺寸 SF03 首轮在 `seat_base / collision_decomposition` 耗时较长。
  已停止的缩尺 SF03 在此阶段退出码为 -9；仅凭此码不能判定一定是 OOM。
- 当前分解目标误差是 0.02 mm，且关闭自动重网格化等处理；对数百毫米、含槽和装饰的
  零件非常精细。它是性能瓶颈的合理线索，但尚未完成单因素归因。
- CoACD 完成也不等于代理合格：仍需确认没有封住槽、加粗榫头或制造错误接触。

**CoACD 不是必需的算法。**普通 MuJoCo mesh 碰撞使用凸形表示，非凸槽口必须通过
多个可靠凸体、经验证的简单碰撞形状或适用的 SDF 等方式表达。
不能直接用整个带槽零件的凸包代替，也不能把所有部件 weld 后声称验证了接口不脱离。

最小后续方向：在现有座面输入上验证一种能保留接口的碰撞表示及其耗时；
确认本身为凸体的件可以直接使用。任何替代都先做局部对照，不直接全局切换后端。
代码：`adsl-agents/assembly_standing.py::collision_proxies()`、`verify_proxies()`、`xml_model()`。

## 4. 配置已补齐，不等于真实装配的 FEA 数值已可信

最初缩尺 SF03 只配置密度，FEA 为 NEEDS_SPEC，已按用户要求停止。
新配置 `examples/fixed_assembly/physics_sf03_chair.json` 已补齐：

- 450×400×900 mm，座面向下 1000 N、靠背水平 300 N，加自重；明确支脚底面支撑区域。
- 等效 PLA：E=3 GPa，ν=0.35，ρ=1240 kg/m³；不是木材实测参数。
- 允许应力 25 MPa（代理屈服50 MPa/设计因子2）；功能允许位移5 mm，不是材料常数。
- 接口为理想绑定，不验证真实摩擦固定、防脱或外载抗倾倒；单档网格筛查不代表收敛。

已有两件式真实 FEA 冒烟通过反力平衡和位移协调；不能据此推定复杂椅子也能通过。
另一次细网格对照峰值应力变化约 +35.34%，已经记录为未证收敛，不隐藏该限制。
有效网格、可靠绑定及数值检查通过后，才使用应力/位移阈值和热点指导几何修补。

## 5. 修补效果与比较口径仍需保持边界

新 SF03 首轮：Image 通过、Topology 6/6件和5/5接口通过；过悬测量完成，面积
131385.285629 mm²、不确定度1084.350479 mm²。测量 PASS 不表示无过悬。
这些是初稿快照，不能当成 agent 已修好的结果；后续各轮以本地实际账本为准。

过悬只允许改变打印方向，需复测可靠下降；FEA/Standing 不可用不能诱导盲改形状。
所有工具汇总为同一个 Coder 修补机会，保留原预算、working/retained 和版本证据。
新全尺寸案例与旧90×80×180 mm wo对照不可直接相减，声称面积或强度改善。

## 参考与进一步验证的边界

- [CalculiX 官方 tied-MPC 实现](https://github.com/Dhondtguido/CalculiX/blob/master/src/gentiedmpc.f)：
  对应面搜索与垂直距离检查是不同步骤；网页当前源码用于解释机制，不代替本机2.23完整重放。
- [MuJoCo 官方碰撞说明](https://mujoco.readthedocs.io/en/stable/computation/index.html#collision-detection)：
  凸几何、非凸分解及SDF等替代的适用边界。
- [CoACD 官方说明](https://github.com/SarahWeiii/CoACD)：real_metric模式和精度/性能参数。

本次不实施上述后续修复，不修改判定阈值，不追加模型生成或求解实验。

推送前相关无API回归：`test_assembly_chair_profile`、`test_assembly_physics`、
`test_assembly_feedback_recovery`、`test_assembly_topology`、`test_fixed_assembly`
（排除real_boolean）：**80 passed / 5 skipped / 6 deselected，12.57秒**。
跳过的native测试不算真实验证；既有真实工具证据另见 `assembly_physics_v1.md`。
