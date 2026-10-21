# 主表物理指标接口 v2：实施与验证

日期：2026-10-21。基准及当前 HEAD：`action_v1@c07b6ea0e601619d42f2cb05f7d2bc257b5dab74`。
本报告记录实现阶段；相关代码与报告随本次 `action_v1` 提交发布，具体提交号以 Git 历史为准。其他无关修改和删除未纳入。

## 结果

主表接口现在返回 `method / standing_success_rate / structural_connectivity_rate / nsv / N`。
连通率采用“每个预定打印件内部材料连续 AND 最终装配位置的件间距离图连通”；
NSV 使用新离线 `support_volume_min24_v1` 协议，逐件完整搜索原有 24 朝向的最小 S。
N 与 NSV 在同一跨方法共同可测案例集合上取均值。

**159 passed，0 failed，0 skipped（23.80 秒）**。
真实 python-fcl、Manifold、射线后端用于人工 CPU 几何；没有使用全 stub 结果代替距离后端验证。
Standing 测试使用模拟观察夹具，未启动真实 Standing 仿真。

另对明确清单中的 **33 案例 × 两方法 = 66 份已有最终资产**完成只读尺寸／间隙审计，缺失 0。
这不是重跑这些案例的连通性或支撑测量，也不产生新方法性能结论。

## 实际修改与接口

| 文件／函数 | 内容 |
|---|---|
| `benchmark/metrics/geometry.py:74` `load_evaluation_parts` | fixed-assembly 与 single-print-mesh 两种输入；源码／清单／STL hash 核验；逆打印矩阵、毫米变换；GLB 全节点；独立资产身份 |
| `benchmark/metrics/geometry.py:242` `measure_connectivity` | 复用完整材料 union／分量计数；Manifold 交集与 FCL 三角形距离；联合判定、双失败原因、不可测状态及详细证据 |
| `benchmark/metrics/geometry.py:311` `measure_support24` | 原 rotations24/support_volume；24 个朝向全部成功才选 min；确定性平手；sum(S)/sum(V)；保存每次实际姿态与结果 |
| `benchmark/metrics/physical.py:197` `physical_table_row` | 只消费绑定的新 connectivity/support_evaluation；校验协议、配置、资产、件数与数值；原 support 不覆盖、不升级 |
| `benchmark/metrics/physical.py:226` `summarize_physical_methods` | 固定分母连通率与独立原因计数；pending 为未完成；NSV/N 同样本；旧 IVR 不参与主表筛选 |
| `benchmark/scripts/evaluate_physical_metrics.py:26` `evaluate` | 显式冻结 records → 审计或离线计量 → 新目录证据、JSON/CSV、表注；不自动扫描实验目录 |
| `benchmark/configs/physical_main_v1.json` | δ=0.005D，floor=0；沿用已有 45°、床面排除 0.2 mm；三个独立协议 ID |
| `pyproject.toml` | 按既有 optional-dependencies 风格增加 benchmark 依赖，包括 python-fcl |
| `experiments/benchmark_six/build_review.py` | 主表字段和解释同步；没有增加几何测量调度 |
| `benchmark/metrics/README.md`、`docs/standing_evaluation.md` | 定义、来源边界、两种输入示例、Standing 刚体路径静态核对、N 样本表注 |
| 两份 metrics/geometry 测试及现有评价测试 | 新合同覆盖；已有 report builder 断言改读结构连通列 |

`classify_standing_record`、`summarize_standing`、`raw_ivr` 的函数体未改。
没有修改在线 Agent、Action、checker 门槛、候选采纳／回退或发布规则。
当前已有在线 S 朝向搜索是此前工作区改动，本轮仅引用原测量函数，不将其列为本轮新增能力。

### 身份与不可测

- 资产 hash 绑定文件、单位、变换和打印分组；不是 bare manifest hash。
- 配置 hash 包含协议、数值约定、后端版本和有序旋转集，不含 case/D/资产 hash。
- 连通测量 `status=PASS` 不等于结构通过；通过看 `overall_pass`。
- 件内真实缝隙不使用 δ；A1—B—A2 绕路不能把 A 的内部断裂变成成功。
- 空腔按完整材料处理；没有将正壳填实。负距离不盲目归零；FCL／实体求交失败没有采样后备。
- UNAVAILABLE 保留已知的内部失败，但未知项为 null；终止不可测在统计中未成功，不伪称观察到断开。
- 原 support 没有新协议标记时为 `SUPPORT24_NOT_MEASURED`。旧报告的主表可能因此缺少新 NSV／连通值，这是明确缺测，不是将旧结果重新解释。

## 验证

使用现有项目 Python 环境及项目内隔离依赖目录，不改共享 Python 环境。
服务器原环境没有 python-fcl；本轮只下载该可选 wheel 到本任务目录后运行原生人工测试。

后端：trimesh 5.0.0、python-fcl 0.7.0.11、manifold3d 3.5.2、embreex 4.4.0、rtree 1.4.1。

最终命令（环境已加入仓库及隔离依赖到 PYTHONPATH）：

```bash
python -m pytest -q -p no:cacheprovider \
  tests/test_physical_metrics.py \
  tests/test_benchmark_geometry_metrics.py \
  tests/test_benchmark_six_evaluation.py \
  tests/test_benchmark_six_runner.py \
  tests/test_assembly_standing_verdict.py \
  tests/test_support_volume.py \
  --basetemp=temp/physical_main_v2_20261021/pytest_regression
```

结果：159 passed。此前的 37 项小集合已包含在最终回归中，不相加。

发布前另外运行 `tests/test_support_overhang.py tests/test_agent_feedback.py`：
154 passed，60.35 秒。独立测试运行不与上述数量相加；日志见
[publication_tests.txt](physical_main_v2_20261021/publication_tests.txt)。
日志副本：[test_results.txt](physical_main_v2_20261021/test_results.txt)。

验证包括：

- 内部小缝隙与预先声明为两件的不同判定；桥接不能掩盖断裂；双失败；链式连通；不引入地面节点。
- δ 下／等／上边界、floor、包含／交叠／表面接触、完整空腔、边—边内部最近点、AABB 重叠但实体分离。
- 非单位尺度、逆打印矩阵、装配毫米平移、GLB 多节点及显式辅助节点排除；非法单位／姿态／hash 拒绝。
- 真实非对称人工 T 形 24 朝向射线测量，选中矩阵重新计量一致；与 G 分离、平手固定、失败方向不填零；非等体积加权总量。
- 两种 adapter 人工文件 → 测量 → 带 hash 的 JSON 证据 → 主表 CSV；输入未修改；已存在输出目录拒绝覆盖。
- 单件 baseline 与多件 ours 不受 IVR N/A 影响；Standing 不适用但连通适用；双标签计数、不可测和 pending；协议／配置／资产混用拒绝。
- 原 Standing 分类、反馈之外的运行器接口与人工支撑回归。没有真实模型服务、渲染或 Standing 求解调用。

## 尺寸／间隙只读审计

从当前明确的 33-case 对照清单选择资产：official 使用已有 common evaluator 的冻结最终清单；
ours 使用最近完成批次的 retained selected 输出，雪人使用已保存的 `attempt_0003` 隔离资产。
不把工作候选或拒绝版本按新指标选成“更好最终结果”。

| 方法 | 资产数 | D 范围 mm | 0.005D 范围 mm | 已声明 fit_offset 范围 mm |
|---|---:|---:|---:|---:|
| official | 33 | 152.545017–244.641589 | 0.762725–1.223208 | N/A：单件，无接口声明 |
| ours | 33 | 152.545007–245.734988 | 0.762725–1.228675 | 0.2–0.2（有连接声明的资产） |

没有发现已声明 fit_offset 大于该资产 δ；这不证明实际接触、装配成功或尺度协议完全一致。
本轮没有测真实件间距离，floor 保持 0，没有按通过率调参。
official 文件已经采用历史评估器的 150-mm 尺度；ours 读取 retained 原生单位。
审计只记录实际尺寸，不能据此宣布各案例同尺寸公平比较已经被自动证明。

- [size_clearance_audit.csv](physical_main_v2_20261021/size_clearance_audit.csv)：66 行单位、D、δ、间隙、资产身份。
- [audit_summary.json](physical_main_v2_20261021/audit_summary.json)：范围、清单／配置 hash；66 份绑定再次核对通过。
- [asset_bindings.json](physical_main_v2_20261021/asset_bindings.json)：逐例 manifest/source/STL hash。
- [asset_selection.json](physical_main_v2_20261021/asset_selection.json)：逐例最终资产选择依据，不含服务器路径。
- [missing_assets.json](physical_main_v2_20261021/missing_assets.json)：空列表。

本地精确路径输入位于任务目录 `temp/physical_main_v2_20261021/size_audit_records.json`，不上传服务器路径。
审计后重新核对全部选中 manifest、source 和 STL 与其冻结 hash 相符。没有覆盖历史资产、账本或旧表格。

## 后续执行记录

实现阶段之后，用户授权的当前 33 例补评已经完成，见[结果与证据](physical_main_v2_current33_20261021/report.md)。以下“本轮未执行”描述仅指本报告的人工验证／只读审计阶段，不代表后续补评未执行。

## 边界和后续使用

已完成的是离线接口、人工后端验证和只读尺寸审计。没有重测 33 例的新主表，没有真实案例连通率／NSV24 改善结论。
未来评测须在明确冻结且同尺度／同协议的最终资产上另行运行，不自动启动。

当前公共评估器的官方单件结果经过与装配相同的 Standing 输入限制：必须为一个连通材料刚体；
其他旧原版 aDSL 仿真路径未确认，不能推断它们全部具有相同行为。本文没有修改刚体划分。

主表不单列覆盖率，但输出保存缺失、终止失败、pending、各原因、精确 NSV/N 子集和逐例证据。
连通成功不证明无互穿、接口强度或插入可行；Standing PASS 不证明材料连续或最终制造正确。

**真实模型调用为零；真实案例生成、修补、Standing 仿真及 benchmark 实验均未执行。**
