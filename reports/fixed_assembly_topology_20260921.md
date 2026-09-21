# 固定装配 topology：第一版接入与 SF13 测量

基线：`fa1e9d4ec2c48ec295dcc3fcd8e977809bf4636e`，2026-09-21。
本次基于该版本工作区修改；无关的既有删除和 tmux/GPU 脚本修改均保留。
验证依据 academic-research-suite 的执行/证据规范；不把流程验证写成制造成功。

## 实现范围

- `adsl-core/core/assembly_topology.py`：仅读取已导出打印网格；逆向打印平移，件内 Manifold 合并、实体分量与 TabSlot 局部查询。不重新执行源程序，不跨打印件 union。
- `adsl-core/core/assembly.py`：共享 `geometry_recipe()`，生成和测量使用同一尺寸/导入斜面定义；`add_part/connect` 语义不变。
- `adsl-core/core/export/export_assembly.py`：增加最终三角面所属原对象的面段元数据，避免共享面被 STL 展平后无法还原。没有另一次 CSG，也未改网格、材质或单位。旧资产缺少此信息时保守处理，不推断/补洞。
- `adsl-agents/assembly_topology.py`：复用 `CheckerSpec/run_checker`、类型化 findings 和现有 Engineering Critic。主 checker 默认 900 秒，每个原生查询最多 120 秒并受剩余总时间约束；异常/超时保存已有结果，其他可测项继续。
- `adsl-agents/fixed_assembly.py`：显式选中此 checker 才调用。Image/Code 顺序保留；有明确可修复 FAIL 才将所有当前问题交 Engineer，至多一个协调提案进入下一次已有修补机会。支持源码推断、空方案停止；独立 working/retained 及发布哈希一致性保持。
- `experiments/fixed_assembly_prompt/verify_topology.py`：现有资产先测量，满足条件才在同一份初始几何上做最多一次修补。复用保存的第一次结果，不为了检查再跑初稿 CSG。真实模型仍为 StepCode `gpt-5.6-sol`。

默认固定装配/普通 aDSL 不变。旧 topology、standing、overhang、FEA 不执行。
不强制至少两件；单连续件、零接口合法。

## 判定边界

件内确认多个实体才 FAIL。空网格、不闭合、退化、不能解释的壳体或超时均为未验证，不是已确认断开。
空腔负向内壁不计成第二实体；点/边接触不构成实体连接。

接口依据最终实际材料与预期局部查询体：榫头存在、插入区间、槽区域无阻塞、局部容纳边界、根部属于同一材料分量。
正余量允许零材料交集；负余量报告名义过盈，并单列未声明的局部干涉。
局部壁面证据来自有误差界的薄带查询，不是精确接触面积，不要求完整槽底或四面包围。
槽壁或根部证据不足为 INDETERMINATE。PASS 不证明插入路径、保持力、承载、可制造或可生成 FEA 网格。

源码索引必须匹配当前源码哈希。语义匹配但没有源码 ID 的候选仍不可靠；Engineer 可自行读源码，记录 `model_inferred`，不能伪装工具确认。

## 本地验证

真实 Manifold 原语检查：连续/相交/分离方块、空腔、点/边/面接触、正余量、名义过盈、实际移动网格、缺榫头/槽、通槽、非轴对齐毫米坐标、远处接收件不能当槽壁。
真实短超时和坏网格子进程：保留未验证状态；其他正常件继续。
模拟流程：多 findings 一次交 Engineer、无索引源码推断、一次预算、空方案、预算耗尽、拒绝候选不污染发布、初始测量复用和旧流程兼容。

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest \
  tests/test_assembly_topology.py tests/test_fixed_assembly.py \
  tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_plan_revision.py \
  tests/test_fixed_assembly_exports.py tests/test_generation_review_contract.py \
  tests/test_fixed_assembly_recovery.py -q
```

另显式执行原两件式真实导出回归：

```bash
ADSL_TEST_FIXED_REAL=1 /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest \
  'tests/test_fixed_assembly.py::test_real_boolean_export[normal]' -q
```

完整相关回归：**96 passed, 6 skipped**（14.49 秒）；六项为默认关闭的真实几何参数变体。
其中正常两件式已另行显式执行：**1 passed**（5.47 秒）。
包含外层总超时终止内层独立进程组的真实短超时测试；确认子进程已回收。

## SF13 实际结果：仅测量，未调用 API

输入：`local_experiment/fixed_assembly_prompt_regroup_SF13_20260920T160523Z/SF13/generate/`。
源码哈希：`2ae975928cf4dd73ce31bfffb8314cbe55571a3ef0ca98025253d96c4815de75`。
六打印件、五接口；不是早期两打印件的 shelf_set 版本。

产物：`local_experiment/assembly_topology_SF13_20260921_v1/`。
主报告：`rounds/round_01/checkers/assembly_topology/report.json`；类型化结果另存 `baseline_result.json`。
输入冻结、实现文件哈希、来源记录在 `input.json`。报告保留测量当时的实现哈希，不追溯改写为后续测试完善版本。

| 项目 | 结果 | 依据 |
|---|---|---|
| root_frame | PASS | 件内合并后 1 个实体 |
| shelf_1_part … shelf_5_part | INDETERMINATE | 每件现有网格不闭合，不能可靠构造实体 |
| 五条 connect_shelf_*_to_frame | INDETERMINATE | 层板网格不可测，依赖阻塞，未执行局部配对查询 |
| 汇总 | INDETERMINATE | 没有确认的几何 FAIL；不能报告 topology 合格 |

总测量墙钟 **18.14 秒**；六个件子进程各约 3 秒（包括解释器启动）。没有超时。

### 不闭合阶段的只读诊断

五块层板均为 70 个三角面，零面积面 0，非流形边 0，**边界边 14**。
各件局部毫米坐标中的边界范围：

`[-37, -23.5799999237, 2] → [40, -7.9200000763, 2]`。

独立 GLB 经精确重复顶点归并后同样各有 14 条边界边。这证明保存的输入网格已经不闭合，不是 checker 新增 Manifold 并集后才产生；但缺少此前各阶段数据，**不能进一步确定是哪个 Boolean、三角化或序列化步骤造成**。
新 checker 没有自动补洞、删除小结构或放宽容差，也没有重跑源码来追查内核。

当前 source_index 与源码哈希一致，但六个直接部件名称匹配项没有 source_ids，因此不能声称已可靠定位至源码。

### 预算与资产

- 初始生成 **0** 次、Engineering/Coder API **0** 次、源码修补 **0** 次、token **0**。
- 仅 INDETERMINATE：按计划停止，不让 agent 盲目改形状；没有启动代理。
- 原源码和旧结果未覆盖。新目录保留输入源码、六 STL、总装/拆分 GLB 与已有渲染；没有候选被接受/发布为合格。
- 原始图：`rounds/round_01/asset/render/`；拆分图：`rounds/round_01/asset/assembly/exploded_render/`。本轮复用原图，不声称新增视觉审核。

## 可运行入口与结论

先测量（输出必须是不存在的新目录）：

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python \
  experiments/fixed_assembly_prompt/verify_topology.py \
  --saved-case local_experiment/fixed_assembly_prompt_regroup_SF13_20260920T160523Z/SF13 \
  --output local_experiment/assembly_topology_SF13_NEW
```

只有该目录记录明确可修复 FAIL 才可使用 `--repair --output <同目录>`，使用已有 StepCode 配置；输入全为未验证时入口会在调用 API 前停止。修补需要按机器手册启动 LMS 代理，仅关闭本次自行启动的代理。禁止重放已有修补目录以重置预算。

**已验证**：新增检测基本行为、短反馈进入模拟真实请求链路、独立失败状态和版本发布一致性。
**SF13 已验证**：框架件连通；层板现有输入不闭合，接口保持未验证。
**尚未验证**：SF13 的真实 Engineer→Coder 修补是否有效。不能用模拟通过或反馈入口存在代替真实修复成功。
