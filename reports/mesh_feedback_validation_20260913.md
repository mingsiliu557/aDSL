# Mesh feedback 最小改动与定向补测（一次候选验收完成）

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-09-13 UTC
- Verification Status: COMPLETED — 流程容错有效；SF27 候选未通过验收；SF03 回归通过
- Version Label: mesh_feedback_followup_v2

## 最终结论（分开评价）

1. **流程容错有效**：真实 `MESH_TIMEOUT` 未阻断后续 Critic → Coder → 隔离候选 → 独立 checker → 外观验收 → 拒绝并保留原资产。候选出现 `MESH_INVALID` 后仍完成验收及 SF03 回归，程序退出码为 0；没有误报全部物理通过。
2. **SF27 恢复了网格生成，但没有修复成功**：三档网格全部生成，不再发生原先的表面划分超时；粗、中网格仍有无效体单元，整体 FEA 未验证。木结矩形化也未通过外观验收，候选被拒绝。
3. **SF03/ours 无回归**：本次最后重新检查原通过版，topology、standing、FEA 均 PASS，三项 metrics 与原实验完全一致。

仅生成并验收了 **1 个候选**，预算已用完；没有继续修改、重跑整批或扩大几何根因排查。

## 范围与改动

在当前 master 的既有脏工作区上增量修改，保留此前未提交改动；没有提交或推送。
没有修改 OCC 构造、几何容差、整体缩放、材料、载荷、边界条件、MuJoCo 参数或网格后端。
下列公共代码修改来自前一轮；本轮仅恢复既有补测入口、执行一个候选及回归并更新报告，没有新增公共 checker 改动。

- `experiments/topology_connectivity/analyze.py`：在原网格生成边界记录开始/完成/错误及曲面包围盒；打开原生诊断输出。捕获原生网格生成异常，不改变构造规则或 mesher 参数。错误定位仅使用显式报错曲面或无效表面单元警告，不使用最后一个正常进度曲面猜测根因。
- `experiments/load_bearing_structural_performance/analyze.py`：将原生网格生成异常转为可区分的 `MESH_GENERATION_FAILED`；保留既有体网格有效性门槛。
- `adsl-agents/checkers.py`：仍由原父进程执行器终止、回收超时 checker；在确定处于网格生成时返回 `INDETERMINATE / MESH_TIMEOUT`。各取 stdout/stderr 最后 64 KiB 提取简短曲面证据；完整日志仅存文件。没有定位仍返回准确未验证状态。
- `experiments/workflow_checkers/run.py`、`adsl-agents/feedback_schema.py`：网格无效、生成失败和网格超时都是 FEA 未验证，不代表结构不承重。保留已有区域与源码定位接口。
- `adsl-agents/service.py`：只有非歧义、具有源码 ID 的现有 direct/geometric 定位允许网格反馈进入 Critic/Coder；没有可靠定位不修复。复用 RepairController 检查剩余预算。Coder 仅接收相关短证据，明确不得默认删除装饰、填缝或改变评估条件。
- `adsl-agents/repair_policy.py`：有定位的目标网格故障达到真实 FEA PASS 可计为改善；不可用→不可用不计改善。新增实际 FAIL、原 PASS 退化、外观回归仍拒绝；其他有效目标改善可以按原部分验收规则保留未验证工作候选。
- 更新原 Engineering Critic 提示词和 workflow checker README；没有新增 agent、轮次、调度框架或通用网格修复器。

## 验证预算与数据隔离

- 原实验：`local_experiment/topology_standing_fea_12_cpu_20260912T100549Z`，保持不变。
- 补测：[执行目录](../local_experiment/mesh_feedback_validation_20260913/execution/)。所有源码、已有渲染、模型均复制并校验原源码哈希，不重新随机生成。
- SF03：复用最终资产，重新执行 topology、standing、FEA，保留原时限；0 个修复候选。
- SF27：本次明确新增的补测预算为最多 1 个候选、1 轮，不重置原批次预算。原模型和候选的 FEA 外层诊断上限均声明为 300 秒；其余物理条件不变。修复时间预算 7200 秒。
- StepCode 使用原 `stepcode-temperature-zero.yaml`；未改模型。代理由实验外壳开始/结束管理，现已关闭。

## 实际结果

| 案例 | topology | standing | FEA | 本次候选 | 结论 |
|---|---|---|---|---|---|
| SF03/ours 原通过版 | PASS | PASS | PASS | 0 | 三项 metrics 与原结果完全一致 |
| SF27/ours 原失败版（最终保留） | PASS | PASS | INDETERMINATE / MESH_TIMEOUT | — | 原资产未覆盖，approved=false |
| SF27 唯一候选（拒绝） | PASS | PASS | INDETERMINATE / MESH_INVALID | 1 | 网格生成恢复，但有效性与外观验收未通过 |

SF03 三项复核总耗时 **51.206 秒**。FEA 位移比为 **0.001207341903526211**，名义安全系数 **31.786033354076785**，屈曲系数 **228.1341**；与原结果一致。
源码及渲染沿用原版，没有调用外观修复。
本次在 SF27 候选验收后追加的 SF03 最终回归耗时 **52.161 秒**，三项 metrics 再次精确一致；[最终回归记录](../local_experiment/mesh_feedback_validation_20260913/execution/SF03/post_repair_regression/summary.json)。

SF27 三项基线检查共 **384.059 秒**，其中 FEA 到达声明的 300 秒上限后被终止。真实日志显示 surface 55 的无效表面单元从个位数增长到 **15880**，仍在重试二维表面划分；没有恢复有效体网格，更没有得到承载能力结论。

- [SF03 复核](../local_experiment/mesh_feedback_validation_20260913/execution/SF03/recheck.json)
- [SF27 原生警告](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/checkers/fea/stderr.log)
- [SF27 checker 结果](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/checkers/fea/result.json)
- [SF27 最终未验证状态及剩余预算](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/outcome.json)
- [完整补测终端日志](../local_experiment/mesh_feedback_validation_20260913/terminal.log)

## 定位与修复执行边界

surface 55 的源码坐标包围盒约为 `[1.175,-0.270526,1.7]` 到 `[1.229,0.110526,1.94]`。
这次使用上一轮诊断和源码核对，明确标记为 **人工核对的 WoodKnots/side_knot_1 位置**，不是自动精确归因。
现有索引可以将 `RuggedFourCompartmentSpeakerCabinet/wood_knots` 映射到第 81–114 行的 WoodKnots 类及第 177 行装配调用。
这仅限定可尝试源码范围，不证明装饰必然是根因。

[人工核对记录](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/operator_localization.json)与
[Engineering Critic 短反馈输入](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/engineering_input.json)已保存。

准备阶段曾因验证脚本遗漏正式流程已有的 `strict_json_schema=False`，被 SDK 在发送 API 请求前拦截。该次没有得到 Critic 回复或生成候选。入口修正并通过离线预检后暂停；本轮获得用户明确授权，恢复原来剩余的 **1 次**预算，没有新增次数。

准备过程另有两项已保留记录的问题：首次记录脚本误将 dataclass 当作 Pydantic 序列化，发生在任何检查/API 前；启动外壳运行中被编辑导致基线结束后尾部 shell 命令出错。二者都没有改变原实验，基线结果已保存，代理均正常关闭。不得把这些脚本问题记作模型的物理失败。
首次真实检查加载了旧的 stdout-only 读取逻辑，因此其 stderr 定位从保存日志中补齐，没有重新运行基线检查。

## 唯一候选：实际修改、检查与验收

候选为 `rp1_wood_knots_low_complexity_patch`。Critic 将有定位的超时解释为可检验的局部几何假设，不是承载失败；Coder 只修改 `class:WoodKnots`，作用域校验通过。
实际编辑把 4 个前侧、2 个右侧、1 个顶部的压扁 Sphere 改为 Cube；保持各自中心、颜色、数量及包围尺寸。没有删除装饰、修改壳体或填补其他间隙，没有更改物理检查条件。
位置与包围尺寸不变 **不代表形状不变**：椭圆木结变成矩形斑块，这正是外观验收拒绝的原因。

| 候选网格档位 | 原生 mesh.generate 耗时 | 体单元数 | 有效性/求解 |
|---|---:|---:|---|
| coarse | 0.734 秒 | 9,062 | 7 个无效单元，MESH_INVALID；不进入该档求解 |
| medium | 0.800 秒 | 12,381 | 4 个无效单元，MESH_INVALID；不进入该档求解 |
| fine | 0.910 秒 | 19,503 | 有效，SOLVED，单档自重和功能载荷筛查通过 |

细网格功能载荷位移比为 **0.0000703473**、名义安全系数 **129.8398**、屈曲系数 **3180.515**。这些只是单档求解值；中网格无有效解，`mesh_convergence=UNAVAILABLE`，**不能据此宣称整体结构验证通过**。
候选 FEA checker 总耗时按 invocation/result 文件时戳估计约 **134.75 秒**，不同于上述原生生成调用耗时；前者还包含几何前处理、有效性检查和求解。

topology 前后均为 8 部件、1 个连通分量、0 个弱接触。standing 峰值倾角约 **0.01129° → 0.01112°**，均远低于 25°；不将这种微小变化解释为实质增益。

Image Critic 和 Code Critic 都判定矩形斑块更像贴附牌片，未保留自然木结外观。Code Critic 还指出顶部木结埋在顶板中；这在原源码中已存在，并非此次编辑新增的缺陷，本轮没有继续修它。
最终决策为 **accepted=false**，记录原因为 `candidate introduced a required regression`，外观/功能保留未通过，`target_improvements=[]`、`unavailable_checks=["fea"]`。即使不考虑外观，未验证→未验证本身也不满足当前目标改善规则。

- [候选源码](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/candidates/01_rp1_wood_knots_low_complexity_patch/source.py)
- [候选 FEA 原始报告](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/candidates/01_rp1_wood_knots_low_complexity_patch/checkers/fea/raw/result.json)
- [Image Critic](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/candidates/01_rp1_wood_knots_low_complexity_patch/image_critique.json)、[Code Critic](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/candidates/01_rp1_wood_knots_low_complexity_patch/code_critique.json)
- [验收决策](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/candidates/01_rp1_wood_knots_low_complexity_patch/decision.json)

原版正视图：

![原版椭圆木结](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline_renders/render_0001.png)

候选正视图（仅供分析，已拒绝）：

![候选矩形木结](../local_experiment/mesh_feedback_validation_20260913/execution/SF27/baseline/candidates/01_rp1_wood_knots_low_complexity_patch/execution/render/render_0001.png)

## API、耗时与预算

本次授权运行始于 **08:37:01 UTC**，SF27 验收于 **08:56:31 UTC**结束，SF03 回归于 **08:57:23 UTC**结束，合计约 **20 分 22 秒**。

| 阶段 | 耗时 | 实际输入 token | 输出 token | API requests |
|---|---:|---:|---:|---:|
| Engineering Critic | 241.29 秒 | 65,366 | 1,735 | 2 |
| Coder | 90.65 秒 | 70,999 | 3,058 | 3 |
| Image Critic | 137.25 秒 | 20,864 | 255 | 1 |
| Code Critic | 269.43 秒 | 52,465 | 954 | 2 |

合计输入 **209,694**、输出 **6,002**、总 token **215,696**，含工具往返的累计用量，不是短反馈正文的 token 数。保留原 StepCode 模型与 API 重试配置，没有人工增加修复候选。
候选执行/渲染及三项 checker 位于 Coder 完成与 Image Critic 开始之间，合计约 **430.84 秒**。
最终只有 1 份 proposal 和 1 份 decision；原选中源码与 GLB 的 SHA256 与原实验一致。候选及失败证据仍保留在隔离目录。

## 轻量测试

执行命令：

```sh
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q \
  tests/test_mesh_failure_feedback.py tests/test_fea_mesh_invalid.py \
  tests/test_checker_fault_isolation.py tests/test_checker_timeout_feedback.py \
  tests/test_refinement_budget.py
```

覆盖：stderr 网格超时证据、原生网格异常在求解前被拦截、无定位/歧义定位不修复、短反馈不含完整日志、不可用→不可用不算改善、恢复 PASS 的接受及回归拒绝、预算耗尽保存资产、独立 checker 继续执行与子进程清理。

最终结果：**76 passed in 11.18s**；Engineering Critic schema 离线预检通过；`git diff --check` 通过。
收尾确认无本次验证/checker 子进程存活，`lms_proxy is stopped`。两例源码与 GLB 的 SHA256 均与原实验对应文件一致。

## 仍未解决

1. SF27 候选的粗、中网格无效以及木结外观退化仍未解决。网格生成恢复不等于有效性或整体 FEA 通过；预算用完后已停止。
2. 自动 AABB 定位可能匹配多个部件；本次没有扩展定位框架，歧义时自动流程应正常结束。人工核对不能统计成自动定位增益。
3. 底层曲面为什么让网格器反复细化仍未确定；本次未深入 OCC 内核。
