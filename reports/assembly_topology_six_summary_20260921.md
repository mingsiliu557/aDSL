## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: validate
- Origin Date: 2026-09-21
- Verification Status: ANALYZED（读取已有日志、源码差异、测量及图片；没有重跑）
- Version Label: assembly_six_summary_v1

# 六例固定装配：Image 与 Topology 前后结果

## 1. 完成情况与统计边界

六个实验条目均已结束，最新补跑于 2026-09-21 10:21:18 UTC 收尾。当前没有这批实验的运行进程。
4/6 获得拓扑合格的 retained，另 2/6 保持 INDETERMINATE；不是所有案例都通过。
只启用 assembly_topology，保留 Image/Code；旧整件 topology、standing、FEA、overhang 未执行。

**这里没有严格的 w/o-topology 对照组。**下表的 w/o/w/ 仅指“本轮反馈修补前的输入资产 / 反馈修补后的最终保留资产”。
不能把初稿冒充“关闭 topology、同预算跑完 Image/Code”的实验结果，也不能把共同修补后的变化完全归因于 topology。
初稿中已有资产可能来自更早的修补；正常 SF13 对照本身就是此前通过的版本。

- 3 个已有资产条目：已有 SF07 问题版、已有 SF13 问题版、已有 SF13 正常对照。
- 3 个全新生成条目：新 SF07、新 SF03、新 SF13；API 中断后的续跑仍属于同一案例，不增加样本数。
- 两个已有 SF13 是同一物体的不同版本，不是两个独立物体。
- 新 SF13 首次 Planner 被 API 中断，没有初稿；它的“修补前”使用后来首次完成生成的同一源码及其补齐审核/测量，不把 API 中断算几何失败。
- “修补后”严格使用最新续跑的 retained 和匹配审核；未获批的 working 单独列出，不混入通过率。
- 六例 retained 文件完整性校验、发布 source.py 与 retained 源码哈希、checker 源码哈希均匹配。

## 2. 用户要求的两项总指标

| 指标 | w/o 本轮修补：通过数 | 通过率 | w/ 本轮修补：通过数 | 通过率 | 差值（百分点） | 结果说明 |
|---|---:|---:|---:|---:|---:|---|
| Image Critic 图像审核 | 1/6 | 16.7% | 4/6 | 66.7% | +50.0 | 按相应版本 Image Critic 的 approved；不把 Code 推翻后的批准混入 |
| Assembly Topology 拓扑 | 1/6 | 16.7% | 4/6 | 66.7% | +50.0 | 所有打印件内部连续、所有声明接口配对均 PASS 才算通过；未验证不算 PASS |

这是描述性前后对比，不是严格 w/o/w topology 消融，也不是原版 aDSL 与 Ours 的独立生成对照。
若统计 Image/Code 综合裁决的 appearance_approved，则是 5/6→6/6；它包含 Code 对 Image 拒绝的覆盖，不能替代上面的图像指标。
Image Critic 也会受到已有 Code 纠正历史的影响，上表不是独立人工外观合格率。

| 子组 | 初始拓扑通过 | 最终拓扑通过 | 变化 |
|---|---:|---:|---|
| 已有资产修复/正常对照，3 条目 | 1/3（33.3%） | 3/3（100.0%） | 两个问题条目恢复通过；正常对照保持通过，未改源码 |
| 全新 prompt-to-3D，3 条目 | 0/3（0.0%） | 1/3（33.3%） | 新 SF13 通过；新 SF07、SF03 未验证 |

## 3. 六例逐项结果：最终保留版本

✓=PASS；×=已确认 FAIL；○=INDETERMINATE，不能说已证实不连通。
件内/接口列表示全部相应子项中 PASS 的数量，其余最终子项均为未验证。

| 案例（准确 ID） | Image 初始→最终 | Topology 初始→最终 | 最终件内 PASS | 最终接口 PASS | 累计实际修补 | 结束原因 |
|---|---|---|---:|---:|---:|---|
| 已有 SF07 问题版 `existing_SF07_open` | ×→✓ | ○→✓ | 3/3 | 2/2 | 2 | 审核/导出/拓扑通过 |
| 已有 SF13 问题版 `existing_SF13_open` | ×→✓ | ○→✓ | 6/6 | 5/5 | 1 | 早先修好网格后 API 中断，续跑补齐审核，未再改源码 |
| 已有 SF13 正常对照 `existing_SF13_pass` | ✓→✓ | ✓→✓ | 6/6 | 5/5 | 0 | 保留正常原版，无无谓修补 |
| 新 SF07 `new_SF07` | ×→× | ○→○ | 2/3 | 0/2 | 3 | 工作候选有改善，但接口证据不足、没有可执行反馈；仍余 1 次未用 |
| 新 SF03 `new_SF03` | ×→× | ×→○ | 4/6 | 0/5 | 4 | 工作候选靠背修复；座面不可测，没有合格最终版本；实际修补上限已用完 |
| 新 SF13 `new_SF13` | ×→✓ | ○→✓ | 2/2 | 1/1 | 4 | 第四个候选通过并保留 |

API 中断不计实际修补次数；保留旧预留次数、失败日志及真实费用。新 SF07 上次部分补丁后 API 中断的一次已扣除，不算第 4 次有效修补。

**工作候选不是上述最终资产：**新 SF07 的 working 已有 3/3 件内 PASS、1/2 接口 PASS；
新 SF03 的 working 已有 5/6 件内 PASS、0/5 接口 PASS。两者仍未完整通过，qualified=null，
所以没有用其 Image 批准或局部 PASS 替换 retained 对应的指标。
每次续跑的 original 是该续跑起点，不一定是最早批次的初稿；旧批次初稿始终另存。

## 4. 原因及修补分析

| 案例 | 已有几何证据 / 未通过原因 | 实际源码修补 | 结果与归因边界 |
|---|---|---|---|
| 已有 SF07 问题版 | 底座 z=24 mm 颈部有 41 条边界边；底座网格开口导致下接口不能评估。不是已证实底座与支柱分离。 | 改 FlaredPedestalBase 曲面/内芯尺寸与端面位置，另改上接口 opening_extension 0.5→0.8 mm。 | 件内及接口均恢复 PASS。修改涉及多个参数，仅凭前后结果不能唯一归因于某个 Boolean 退化条件。 |
| 已有 SF13 问题版 | 五块层板各有 14 条边界边，位置在 z=2 mm 的三组顶面木纹区域，而非已证明五条接口断开。 | 在共用 ShelfBoard 中去掉齐平顶面细木纹实体；保留层板本体和接口。 | 五块层板与五个接口均恢复 PASS。边界位置与源码差异支持装饰交界相关；未定位到内核内部具体退化步骤。 |
| 已有 SF13 正常对照 | 初始六件均连续，五条接口均配对。 | 无修改。 | 保持 PASS；该例验证不会因无故障而强行修补，不属于新修复成功案例。 |
| 新 SF07 | 初稿桌面有零面积三角面；前期修补恢复桌面，但工作支柱在 z=29.2 mm 处出现 54 条边界边及 109 条非流形边。 | RoundTabletop/TaperedStem 历次局部调整；最后把支柱主圆柱下端从 z=29 延到 z=24，移除单独下部内芯端盖接缝。 | 最新支柱恢复连续。下接口仍为 INTERFACE_EVIDENCE_INSUFFICIENT：榫头、容纳区和有效插入可测，但 local_wall_evidence=[]，不能确认槽壁配对；不是测出材料断开。 |
| 新 SF03 | 初稿四条椅腿接口各有约 14.4 mm³ 未声明局部干涉，显著大于约 0.06674 mm³ 数值容差；靠背有 14 条边界边。修补后座面出现零面积三角面，使五接口均无法继续评估。 | 腿/座接口 frame 的 z 从 80 改到 80.5 mm；靠背由多构件/union 改为单块减去三个开口。 | 最新靠背 PASS，座面仍 PRINT_MESH_UNMEASURABLE。四腿原干涉没有得到有效复测，不能说已消除；FAIL→未验证不是物理改善证明。零面积面来自哪次具体布尔/三角化操作，本次未新诊断。 |
| 新 SF13 | root_core 报 open/nonmanifold/unoriented/zero-volume shell，错误没有进一步唯一分类；右立柱有 14 条边界边，接口因此未验证。 | 右立柱齐平木纹改成凹槽；根部简化木纹，层板—左立柱交界加 0.60 mm 宽隐藏桥接体，最终改用普通层级组织而非该处多操作数 CSG union。 | 最终两件都形成一个连续实体，唯一接口配对 PASS。是装饰、本体连接及构造方式共同修补，不是只改 connector，也不能把成功唯一归因于桥接或某一个改动。 |

新 SF07 下接口细节：missing_tab≈0.000054 mm³、occupied_cavity≈0.000049 mm³，均低于 0.121467 mm³ 容差；局部干涉为 0，有效插入区间 0–14 mm，root_connection 已确认。
其停止的直接原因是局部槽壁证据为空，不是超时或 API 错误。最终网格底座包围盒仅 z=17–24 mm 的颈部；
最新渲染也未显示要求的大底座。这支持“导出几何/显示未体现设计底座”的问题，不证明具体哪一步生成丢失，未进行额外内核排查。

这六例所核对的关键初始/最终/工作结果中，没有用 INTERNAL_PART_DISCONNECTED 来证明上述未验证项断开。
主要障碍是网格开口/退化导致不可测、接口干涉，以及局部配对证据不足；它们必须分别汇报。

## 5. 图像指标的已知限制

- 已有 SF07 最终 Image approved=true，但保存图上仍有穿出桌面的椭圆凸起。日志称其为“诊断显示伪影”，不能据此否认图上缺陷。本例只能算自动图像审核通过，不能直接称人工视觉合格。
- 新 SF07 工作候选的图缺少要求的大底座；Code 曾以源码存在底座推翻视觉质疑，后续 Image 继承该说法。其自动批准不足以证明外观正常。
- 新 SF13 最终首视角确实呈现完整五层书架；此处仅作已有图片核对，不替代全视角人工测量或实物制造验证。

图片证据：

- [已有 SF07 最终图：桌面凸起](/jiigan-hp/lms/aDSL/experiment/local_experiment/assembly_topology_six_20260921T060500Z/existing/existing_SF07_open/rounds/round_03/candidates/02_repair_top_stem_opening_extension_01/asset/render/render_0001.png)
- [新 SF07 工作候选：未显示大底座](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/assembly_topology_remaining_20260921T100200Z/continued/new_SF07/rounds/round_02/candidates/01_repair_stem_lower_transition_z29p2/asset/render/render_0001.png)
- [新 SF13 最终图](/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/assembly_topology_remaining_20260921T100200Z/continued/new_SF13/rounds/round_05/candidates/04_assembly_or_appearance/asset/render/render_0001.png)

## 6. 可追溯记录

以下目录内 `assembly_versions.json` 保存每个版本的 Image/Code、assembly_topology 子项和相应源码哈希；源码差异由这些实际文件直接对比，未修改生成源码。

| 案例 | 最新工作目录（相对项目） | retained / working |
|---|---|---|
| existing_SF07_open | `local_experiment/assembly_topology_six_20260921T060500Z/existing/existing_SF07_open` | attempt_0002 / attempt_0002 |
| existing_SF13_open | `temp/assembly_topology_api_retry_20260921T083011Z/continued/existing_SF13_open` | original / original（沿用已修复源码） |
| existing_SF13_pass | `local_experiment/assembly_topology_six_20260921T060500Z/existing/existing_SF13_pass` | original / original |
| new_SF07 | `temp/assembly_topology_remaining_20260921T100200Z/continued/new_SF07` | original / attempt_0001 |
| new_SF03 | `temp/assembly_topology_SF03_resume_20260921T091930Z/continued/new_SF03` | original / attempt_0002 |
| new_SF13 | `temp/assembly_topology_remaining_20260921T100200Z/continued/new_SF13` | attempt_0004 / attempt_0004 |

原批次清单与初始结果：`local_experiment/assembly_topology_six_20260921T060500Z/{six_cases,case_results}.json`。
新 SF13 首次成功生成记录：`local_experiment/assembly_topology_api_retry_20260921T071034Z/fresh/SF13/generate/assembly_versions.json`；其初稿源码与最新续跑 original 一致。
本轮没有调用 API、执行 checker、重启实验、修模型或改变阈值。通过率仅覆盖当前 assembly_topology 的定义，不包含站立、承重、真实固定或可打印保证。

## 7. 结果解释检查（11/11）

仅描述性计数，无 p 值、显著性检验或总体推断。辛普森悖论：分组另列；生态谬误：不从六条目外推物体总体；
选择偏差/Berkson：便利选例且含历史正常对照；碰撞偏差：没有回归控制推断；基率忽略：所有分母显式列出；
均值回归：问题例前后变化不是随机对照的因果效应；幸存者偏差：全部六例保留，未验证不剔除；
多重寻找：不只报告成功案例；分叉路径：记录 API 补跑和计次更正，未按结果换案例；
相关与因果混淆：共同 Image/Code/topology 修补不能单独归因于 topology；反向因果：有版本先后但不据此宣称组件级因果。
此外，SF13 问题版/正常版非独立，不能把 6 当成 6 个独立物体估计泛化率。
