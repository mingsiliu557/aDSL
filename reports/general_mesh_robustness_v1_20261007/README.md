# 网格鲁棒性 v1 离线证据

实现提交：`2c3b75f6899be47887a5abeed8e341ea8c7630da`，分支 `codex/mesh-validity-v1`。

状态：正常夹具导出／checker 通过；原灯臂和象鼻内部 Mesh64 有效，但最终 float32 转换仍失败。passed 的负例测试不代表这两例导出成功。详情见 [实施报告](../general_mesh_robustness_v1_20261007.md)。

持久数据根：`/jiigan-hp/lms/aDSL/experiment/general_mesh_robustness_v1_20261007/release_evidence/`。

| 路径 | 内容 |
|---|---|
| `evidence_summary.json`、`provenance.json` | 源码／实现 SHA、阶段结果、缺陷、时间、源码候选、持久路径 |
| `frozen_geometry_config.json` | 原灯例的固定装配配置；没有模型 profile／token |
| `minimal_operands.json`、`minimal_repro.py` | 保留原几何的三操作数反例与通用重放脚本；不证明全局最小 |
| `logs/` | 实际 pytest、固定源码离线检查及反例日志 |
| 数据根 `lamp/` | 原源码、内部 Mesh64 NPZ、独立诊断 ASCII STL、实际部分资产、topology／求值反馈 |
| 数据根 `elephant_trunk/` | 原象鼻源码、内部与目标精度结果、独立诊断 STL |
| 数据根 `minimal_prefix/` | 首次失败前缀、一次贪心删减及结果 |
| 数据根 `positive_checks/` | 成功导出／材料／joint／空腔、真实多接口、CPU 八视图、stub 修补闭环 |

复现环境（已存在的 Python；导入 overlay 只用于本次隔离测试）：

```bash
cd /tmp/adsl_mesh_validity_v1_20261007
export PYTHONPATH=/tmp/adsl_mesh_validity_v1_imports:/tmp/adsl_mesh_validity_v1_20261007
export PYTHONDONTWRITEBYTECODE=1
TASK_PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python

ADSL_TEST_FIXED_REAL=1 ADSL_TEST_GEOMETRY_REAL=1 "$TASK_PYTHON" -m pytest -q -p no:cacheprovider tests/test_mesh_validity.py tests/test_mesh64_evaluation.py tests/test_mesh64_exports.py tests/test_boolean_recovery.py tests/test_boolean_solver.py tests/test_zero_area_tessellation.py tests/test_microcrack_welding.py tests/test_fixed_assembly_exports.py tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_empty_mesh.py

ADSL_TEST_FIXED_REAL=1 "$TASK_PYTHON" -m pytest -q -p no:cacheprovider tests/test_fixed_assembly_multi_mate.py -k real_

"$TASK_PYTHON" -m pytest -q -p no:cacheprovider tests/test_mesh_evaluation_feedback.py tests/test_assembly_feedback_recovery.py tests/test_fixed_assembly_recovery.py

ADSL_TEST_GEOMETRY_REAL=1 "$TASK_PYTHON" -m pytest -q -p no:cacheprovider tests/test_constructive_geometry.py tests/test_loft_geometry.py tests/test_geometry_demo.py tests/test_prompts.py

ADSL_TEST_FIXED_REAL=1 "$TASK_PYTHON" -m pytest -q -p no:cacheprovider --basetemp=/tmp/adsl_mesh_validity_v1_positive_exports tests/test_mesh64_exports.py tests/test_fixed_assembly_multi_mate.py -k 'not real_ or real_four_part_cycle_geometry_and_topology or real_same_pair_two_interfaces_geometry_and_topology'

"$TASK_PYTHON" reports/general_mesh_robustness_v1_20261007/minimal_repro.py
"$TASK_PYTHON" reports/general_mesh_robustness_v1_20261007/run_offline.py --output /tmp/adsl_mesh_validity_new_evidence
```

最后一条要求输出目录尚不存在；只复制固定输入并离线测量，不请求 API 或启动 benchmark。原生代码运行仍需现有 bpy／Manifold 环境。换 checkout 时先安装对应 packages 或调整 overlay，并核对实际模块来源。

文件读回的 mesh 有效性与自交检测是不同事项：完整三角自交为 NOT_EVALUATED。诊断 STL 单独通过不等于其生产候选已被接受。

实现 diff 与提交状态可复查：

```bash
git log --oneline f78e846b36899b72273f4fb80bd64e99a613db1c..codex/mesh-validity-v1
git diff f78e846b36899b72273f4fb80bd64e99a613db1c..2c3b75f6899be47887a5abeed8e341ea8c7630da
git status --short
```
