# action_v2：同步现有评测指标改动

日期：2026-10-24。

## 基准与范围

`action_v1` 的已提交 HEAD 为 `d355518d4dfa7fdae17c96d614b08f57d95262f3`，但最新版评测代码仍在该工作区的未提交修改中。此前 `action_v2` 从上述 HEAD 创建，因此 `d26c9d912c7afe472e378281ce10da2c9779b872` 的 Standing 倾倒定位提交没有包含这些评测变更。

本次将以下 17 个现有文件同步到 `action_v2`，使其同时包含最新版评测协议和 Standing 定位。没有重新设计指标或改写原工作区。唯一格式整理为删除 `tests/test_connectivity_reassessment.py` 的末尾多余空行。

| 范围 | 文件 |
| --- | --- |
| 配置与文档 | `benchmark/configs/physical_main_v1.json`、`benchmark/metrics/README.md` |
| 几何和汇总 | `benchmark/metrics/geometry.py`、`physical.py`、`contact_area.py` |
| 离线入口 | `benchmark/scripts/evaluate_physical_metrics.py`、`curate_measurable_pairs.py` |
| 最终评估接线 | `experiments/benchmark_six/evaluate.py`、`run.py` |
| 八份测试 | `tests/test_contact_area.py`、`test_connectivity_distance_errors.py`、`test_benchmark_geometry_metrics.py`、`test_physical_metrics.py`、`test_connectivity_reassessment.py`、`test_dataset_admission.py`、`test_benchmark_six_evaluation.py`、`test_benchmark_six_runner.py` |

提交另包含本同步报告。历史资产、ZIP、机器配置、实验结果及旧评测档案不属于本次代码同步。

## 同步后的行为

- 连通评测使用 `geometric_facing_contact_connectivity_v1`：节点为最终装配的材料连通块，保留空腔；打印件归属只用于溯源，件内多材料块不再直接否决。
- 距离门槛 `delta=0.005D`、`delta_floor_mm=0`；距离与双向相向表面面积均满足条件才连边。面积门槛为 `1.5e-5 D²`，法向点积严格小于 0，双向面积取 min。
- 面积估计沿用现有面积加权表面采样：每方向 65,536 点、seed=0、批大小 1,024。它估计真实表面积，不按三角面数量计分；保留统计区间和不可测原因，不宣称精确接触面积或连接强度。
- `--connectivity-only` 沿用冻结资产及已有 Standing、NSV24、N 和样本集合；不以空值覆盖其他指标，不静默重算其他指标。
- 最终评估保存连通证据与入库资格；可测但不连通仍有资格，不可测按配对规则排除。资格标记不自动改主库成员，也不改变“aDSL 网格合法才运行 Ours”的条件。
- Standing、NSV24、N 的既定统计口径和在线 Topology/Standing 判定未因这次同步另行修改。倾倒定位的两份生产文件和三份测试均保留 `d26c9d9` 的内容。

这次是将既有修改纳入同一分支，没有重评任何真实输出，也没有更新案例结果或主表数值。

## 实际验证

使用既有环境和 `action_v2` 私有 import overlay，核对八个模块的实际导入全部指向该工作树：Standing、反馈、core assembly、geometry、contact_area、physical、最终 evaluator 和 runner。未更改共享环境安装映射。

定向命令（`<environment-python>` 表示现有项目环境解释器）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
OPENAI_AGENTS_DISABLE_TRACING=1 \
PYTHONPATH="$PWD/local_experiment/standing_localization_v2/import_overlay" \
ADSL_SESSION_ROOT="$PWD/local_experiment/evaluation_sync_v2_20261024/sessions" \
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
<environment-python> -m pytest -q \
  -o cache_dir="$PWD/local_experiment/evaluation_sync_v2_20261024/pytest_cache" \
  tests/test_contact_area.py tests/test_connectivity_distance_errors.py \
  tests/test_benchmark_geometry_metrics.py tests/test_physical_metrics.py \
  tests/test_connectivity_reassessment.py tests/test_dataset_admission.py \
  tests/test_benchmark_six_evaluation.py tests/test_benchmark_six_runner.py \
  tests/test_standing_tipping_localization.py \
  tests/test_assembly_standing_verdict.py::test_tipping_localization_preserves_identity_and_verdict \
  tests/test_agent_feedback.py::test_tipping_localization_at_repair_boundaries_is_idempotent
```

**结果：225 passed in 63.60s，退出码 0，无跳过。** 包含人工 CPU 几何/FCL/面积采样/支撑测量、两类资产适配与 JSON/CSV smoke、冻结证据复用、最终评估接线及原 25 项倾倒定位测试；模型请求和 Standing 模拟采用 fake/stub。未启动真实模型、真实案例、Standing 仿真、渲染或 benchmark。

`git diff --check` 通过。同步后又核对：16 个目标文件与原工作区对应文件逐字节一致，另一测试文件仅删除末尾多余空行，原工作区 git 状态及源文件未改变，Standing 定位的五个文件哈希未改变。导入记录、同步身份/核验与测试日志保存在忽略的 `local_experiment/evaluation_sync_v2_20261024/`。

独立只读审计确认 17 个文件覆盖依赖闭包，与倾倒定位无文本冲突；依赖已在共同基准的 benchmark 可选依赖中声明，无须修改环境。

## 分支使用

后续相关开发应以同时包含本次同步和 `d26c9d9` 的 `action_v2` 为基础。原 `action_v1` 工作区保持原状，没有合并 master，也没有替换任何运行中任务的代码副本或实验环境。具体提交身份与远程状态以该分支 Git 历史及推送核对为准。
