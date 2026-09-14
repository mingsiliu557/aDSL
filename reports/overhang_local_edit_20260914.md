# 过悬局部编辑：实现与提交前验证

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run (implementation and bounded validation)
- Origin Date: 2026-09-14
- Verification Status: VERIFIED for the tests listed below only; real agent-edit effect UNVERIFIED
- Version Label: overhang_local_edit_v1

## 实际改动

- `experiments/support_requirement_critical_surfaces/analyze.py`：冻结源单位到毫米转换；检测副本通过固定 Blender EXACT 合并获得外表面；失败不回退到拼接网格；保留测量条件、面积误差、局部过悬区域与表面代表点。
- `experiments/overhang_feedback/exterior.py`：独立限时检测进程，不修改原资产。继承 checker 进程组；外层超时清理覆盖 Blender worker。
- `experiments/workflow_checkers/run.py`：取消 1% 支撑接触下降门槛；PASS 仅表示测量完成；缺失字段不补零；记录源坐标到打印坐标变换供现有定位使用。
- `adsl-agents/models.py`、`cli.py`、`service.py`：默认关闭的过悬实验选项；可无 mandatory FAIL 进入 Engineering Critic；复用候选、批评与保存流程；代理自主放弃；两组共享持久化编辑次数上限；保留最佳候选及其测量记录。
- `adsl-agents/overhang_edit.py`、`repair_policy.py`：绝对面积误差比较、手工源码范围、世界坐标保护表面检查；外观或保护不通过不能因面积下降而接受。无可靠改善保留原资产。
- `experiments/overhang_feedback/run_pilot.py`、`paired_assets.json`：已有六个资产的 prepare/edit/evaluate 配对入口。对照组最终选定后才离线测量，不用测量回选模型。准备阶段不调用 API。
- `run_paired.sh`、`submit_paired.sh`：CPU Eevee、现有 StepCode profile；tmux 与文件双日志、最终退出码；只在整批开始/结束操作本批拥有的代理，不持续监控。

生产 OCC → Gmsh → CalculiX、topology、standing 和 fTetWild 实现未改动，也不在本实验中执行。

## 已执行验证

相关轻量单元回归：**112 passed, 2 skipped**。两个 skip 是需显式开启的本地 Blender/PrusaSlicer smoke，不是遗漏的生产检查。

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest \
  tests/test_overhang_local_edit.py tests/test_support_requirement_critical_surfaces.py \
  tests/test_workflow_checkers.py tests/test_checker_fault_isolation.py \
  tests/test_refinement_budget.py tests/test_mesh_failure_feedback.py \
  tests/test_fea_mesh_invalid.py -q
```

单独启用的两个有效几何 smoke：**2 passed，5.31 秒**。

- 两个重叠立方体：并集体积 1500、外面积 800，低于拼接面积 1200，验证内部重叠面没有计入外表面。
- 显式三角化的单体 L 棱柱：Blender 导出及 PrusaSlicer 完成；检测到过悬与实际支撑。
- 成功 smoke 产物：`/tmp/adsl_overhang_smoke_valid_20260914/`。

保留一次失败：最初“支柱与横梁仅面接触”的合成 fixture，经 Blender 合并后表面有效性不合格，被拒绝测量。日志：`/tmp/adsl_overhang_smoke_20260914/test_real_exterior_and_slicer_0/`。未放宽阈值、删面或修改该输入获得成功；随后单体 L 棱柱只用于独立验证切片器接线。**这不代表原失败 fixture 已修复。**

验证还覆盖：床面排除、倾斜面、桥接/支撑分报、缺失/超时不填零、固定尺度、无 FAIL 的显式入口、候选接受后继续、预算持久化、外观/保护拒绝、控制组读不到离线报告及旧模式默认行为。

## 明确限制

- 尚未证明真实 agent 编辑能够改善；提交任务后必须看实际结果，不能由入口接通推断成功。
- O01–O04 的旧导出缺少可靠逐部件语义。已配置保护上表面与可见特征，但未认证把手孔洞等未给定精确尺寸。保护三角对应过于严格时，重三角化也可能被拒绝。
- Blender EXACT 对闭合输入并不保证总能输出可接受表面。失败资产正常保留且标未评估，不自动填洞、改容差或换后端。
- 面积容差来自浮点坐标误差假设、分类不确定面及基线重复差异，不是 Blender 布尔近似或真实打印误差的数学认证。
- 六个资产版本并非六个独立数据集对象：O01/O02 同对象，O03/O04 同对象。原始 prompt 来源保存在配对清单引用中。

## 运行与查看

```bash
bash experiments/overhang_feedback/submit_paired.sh
```

提交输出给出 tmux 名称和项目下独立目录。查看 `batch.log`、`edit_results.json`、`evaluate_results.json`、每个案例的候选 `decision.json`、`protection.json`、原始测量及渲染。`exit_code.txt` 在整批退出时写入。失败案例也保留；没有改善不会覆盖原资产。按用户要求，提交确认后不持续监控。

## 本次实际提交结果（不是修复成功）

- 用户随后授权代码验证后直接提交、不持续监控。
- tmux：`adsl_overhang_20260914T033000Z`。
- 目录：`local_experiment/overhang_paired_20260914T033000Z/`。
- 已提交并启动，但预检全部未能进入编辑；整批以退出码 **1** 结束。没有调用编辑 agent API，没有生成修复候选。
- O01–O04、SF03：`EXTERIOR_UNVERIFIED`。O01 与 SF03 明确在 detector union 输出的闭合/定向/退化面有效性门禁被拦截；此记录不把不同案例归因于同一种几何原因。
- SF05：人工保护规则未匹配其现有导出，准备阶段未就绪；后续阶段因此没有 prepared.json。这是当前实验配置/导出兼容性未完成，不是模型物理不合格。
- 原资产及失败结果均保留。没有自动重试、放宽条件、修改源模型或继续扩大几何排查。**现有资产上的真实编辑效果仍完全未验证，不能宣称实现已在六例上跑通。**
- 验证产物另存项目内 `local_experiment/overhang_validation_20260914/`，不只依赖 /tmp。
- 最后一次针对 workflow/overhang/fault-isolation 的回归：70 passed、2 skipped；前述完整相关集合为112 passed、2 skipped。

## 后续 bug 修复与两例预检

用户要求修复，并在可行时提交少量案例。修改限于实验适配：

1. 布尔输出用 bmesh EAR_CLIP 显式三角化，替代 loop-triangle 缓存导出。相同 SF03 输入：520 个三角形保持不变，零面积面 7→0，顶点集合完全一致，总表面积差 0，体积差约 −2.91×10⁻¹¹ mm³。不是删面、删碎片或放宽阈值。
2. 分组件显式 `repair=False`，避免 trimesh 默认的隐式小孔修补。仍不接受真实开边/非流形或零体积输入。
3. SF05 使用与原源码哈希匹配的现有 source_index，固定原始部件世界坐标区域，再比较区域内的整片三角面。不按候选重算区域，不把包围盒一致等同于表面一致；保留严格相等规则。
4. 准备失败保存原因，后续记录 NOT_READY，不再产生误导性的 prepared.json 缺失错误；评估阶段也不自动重试基线失败。

验证：相关轻量回归 **114 passed, 3 skipped**；显式开启 Blender smoke 的局部集合 **32 passed**。

原资产预检结果（无 API 编辑）：

| 案例 | 保护检查 | 过悬测量 | 耗时 | 过悬面积 mm² |
|---|---|---|---:|---:|
| O03 | PASS | ANALYZED | 16.94 s | 18438.226 |
| SF03 | PASS | ANALYZED | 7.09 s | 6119.804 |
| SF05 | PASS | EXTERIOR_UNVERIFIED：输入存在零体积分量 | 0.05 s | 未评估 |

产物：`local_experiment/overhang_bugfix_20260914/`。O01、O02、O04 的其他输入/边连接问题未宣称解决。SF05 未删分量，不参与本次编辑。依据这些预检，仅提交 O03、SF03 两例，每例 control/feedback 两组、每组最多2个编辑候选；StepCode、CPU Eevee。真实编辑改善仍须看后续结果。
