# SF13 修复后 standing：case、运动图与静止诊断

本目录是最终 case 的副本：原源码、修复后的 STL/GLB、8 件 24 接口 topology 结果、5 秒 standing 轨迹与原始报告均保留。
旧 temp case 未删除。所有新增渲染使用 CPU，来自已保存的真实 qpos；没有放大运动、重新生成模型或修改物理配置。

最新的运动原因对照见 [接触与时间步诊断](analysis/motion_cause_20260928/README.md)：初始位姿非静力平衡；末段运动对步长/接触设置敏感，0.5 ms 未延续 1 ms 的改善，尚未收敛。原结果仍为 INDETERMINATE。

## 新判据复核（2026-09-28）

用户已确认取消静止速度硬门槛，保留全观察期倾角和接口检测。新 CPU 复核为 **PASS**，
`settled=false` 继续作为诊断；物理配置与轨迹均未改变。新报告见
[standing_criterion_20260928/report.json](standing_criterion_20260928/report.json)，
[逐项比较](standing_criterion_20260928/comparison.json)。下文原 INDETERMINATE 属于旧判据记录，保留不改写。

## 看图

- [运动 GIF](renders/motion.gif)：0–5 秒，真实幅度，末帧停留 1 秒。
- [四张关键帧](renders/keyframes.png)：0 秒、最大根件倾角时刻 0.12 秒、末段最大速度时刻 4.56 秒、5 秒。
- [运动与阈值曲线](renders/motion_metrics.png)（[SVG](renders/motion_metrics.svg)）。
- [最终静态图](renders/frame_0250.png)。

模型运动很小，GIF 肉眼不明显是正常的；速度曲线更容易看出超限时刻。图中根件倾角不代表每块层板的角速度。

## 为什么没有满足静止

原规则要求末尾 0.5 秒内，**每个采样点、所有 8 件**同时满足线速度 ≤1 mm/s、角速度 ≤0.01 rad/s。
按当前代码的浮点时间比较，该窗口实际包含 4.52–5.00 秒共 25 个采样。
线速度有 14 个采样超限，角速度有 11 个；两类时间可重叠。最终一帧虽已低于阈值，不能覆盖前面的超限。

主要来源是 **第五层板 shelf_level_5**，第二、第三层板也有少量角速度超限；顶盖、两侧板和第四层板不是主要来源。

| 部件 | 末段最大原点线速度 mm/s | 末段最大角速度 rad/s | 超线速度次数 | 超角速度次数 |
| --- | ---: | ---: | ---: | ---: |
| side_left | 0.079017 | 0.001794 | 0 | 0 |
| side_right | 0.096654 | 0.002211 | 0 | 0 |
| shelf_bottom | 0.050483 | 0.001733 | 0 | 0 |
| shelf_level_2 | 0.510329 | 0.013041 | 0 | 1 |
| shelf_level_3 | 1.108457 | 0.013573 | 2 | 3 |
| shelf_level_4 | 0.191282 | 0.001306 | 0 | 0 |
| shelf_level_5 | 2.854361 | 0.018059 | 13 | 7 |
| cap_top | 0.433300 | 0.002270 | 0 | 0 |

### 已确认的测量问题：线速度取点依赖部件原点

当前 `assembly_standing.py::simulate` 直接取 freejoint 的前三个 qvel 分量作为“部件线速度”。
它是部件坐标原点的速度。本模型的层板局部几何保留了高度坐标，原点在层板之外；第五层板质心局部 z≈158.2 mm。
于是很小的转动也能让远处原点产生较大的线速度。该线速度指标会受部件局部坐标原点选择影响。

在 **4.56 秒同一时刻**：

- 第五层板原点线速度 **2.854361 mm/s**；
- 用 `mj_jacBodyCom @ qvel` 计算的质心速度只有 **0.164690 mm/s**；
- 角速度 **0.018059 rad/s**；158.2 mm 的偏距对应约 2.86 mm/s 的转动速度量级，与原点读数相符。

末段所有部件的质心线速度均小于 1 mm/s，第五层板质心速度的整个末段峰值为 **0.313924 mm/s**。
这能解释线速度超限为何被放大，但**不能直接判 PASS**：第五层板及第二、第三层板的角速度仍有实际仿真超限。
目前没有修改生产 checker 的取点或判据，也没有改写原始结果。

MuJoCo 的自由关节速度约定见[官方说明](https://mujoco.readthedocs.io/en/3.2.2/overview.html#floating-objects)；
质心 Jacobian 和所需运动学步骤见[官方 API](https://mujoco.readthedocs.io/en/3.13.0/APIreference/APIfunctions.html#mj-jacbodycom)。
本次按保存的 qpos 与回放恢复的 qvel 做运动学后处理，调用 `mj_kinematics`、`mj_comPos`、`mj_jacBodyCom`，没有新增积分步骤。

### 剩余角运动：观察到了接触变化，根因尚未完全分离

第五层板在 4.54→4.56→4.58 秒时，左侧板接触点数为 **12→6→12**，同时出现角速度尖峰。
其末段质心各轴范围约 **0.01474 × 0.00355 × 0.00472 mm**，属于很小的运动。
全程求解器最多使用 7 次迭代，上限为 100，没有观察到迭代次数耗尽。

还需注意：4.56 秒第五层板与右侧板的 MuJoCo 有符号接触距离最小约 **−0.1724 mm**，
而配置的两侧表面半径合计 0.02 mm。这是仿真中软接触的穿入读数，不能当成网格裂缝尺寸。
现有 standing 只做初始接触深度验证；初始验证通过不能证明后续动态接触误差很小。

后续已完成同一模型的有限步长/接触对照，见上方最新诊断。运动对设置高度敏感且未收敛；数值接触与真实摩擦滑动/晃动的贡献仍未完全分开。未据此让 Agent 改物体设计、延长仿真或放宽阈值。

## 已完成的验证与文件

- 原状态仍为 INDETERMINATE：未倾倒、无接口脱离；251 个轨迹采样均检测全部 24 条接口。
- 一次完全相同 XML/配置的 CPU 诊断回放：qpos 与原记录最大差 **0**；最大速度误差约 **4.34e−19**。
- `asset/assembly/`：最终 manifest、STL/GLB；`source.py`、`plan.json`、`physics.json`。
- `standing/`：原 checker 报告、XML、轨迹、接触记录、各件测量实体。
- `topology/`：8 件、24 接口 PASS 的原始证据。
- [诊断汇总](analysis/summary.json)、[逐件速度](analysis/settling_diagnosis.json)、[质心/接触](analysis/com_diagnosis.json)。
- `analysis/replay_diagnosis.py`、`center_of_mass_diagnosis.py`、`plot_motion.py`：新增诊断脚本；`renders/` 保存 CPU 渲染脚本、图和元数据。
- [来源](provenance.json)：复制自原运行的位置；历史 JSON 内原路径未改写，当前目录的相对路径如上。

原 standing 环境没有 matplotlib，所以最初未自动出图。本次复用已有 trellis-eval 的 matplotlib 生成曲线，
使用 adsl 环境 Blender/Cycles CPU 渲染 28 张实际姿态图，未安装依赖。

线速度取点的生产实现仍未修改；有限接触/步长对照已经完成，不能当作收敛验证通过。
