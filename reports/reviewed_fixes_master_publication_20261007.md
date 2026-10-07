# 已验证修复合入主分支

日期：2026-10-07。用户授权合并近几轮可发布修复。远端默认分支为 `master`，发布前为 `521fe081b57a394c7593ca252fff82578693cf92`。

- 合并 `codex/mesh-validity-v1@d6b52b7`：统一 Mesh64 嵌套求值、制造／显示解耦、实际文件验证、受限 float32 收缩／翻边、空集交集语义、全部面法向保护、毫米预算、翻面进入修复搜索及可定位错误反馈。包含其已验证的六例 runner/API 错误隔离、overhang list 索引兼容和 GPT‑6.1 Sol 配置前置提交，未重写历史。
- 合并 `feat/benchmark-selection-v1@7a0cfdd`：分类、Toys4K 镜像、阶段缓存及输入／结构风险推荐修正。GT 的闭合性和物理 PASS 不作为案例推荐门槛；20 例仍待人工确认。
- constructive／loft、多接口、互穿和分件评分此前已被 master 包含，无须重复合并。两路待合并分支无文件交叉、无合并冲突。合并节点为 `a2376ac`；本报告与验证日志在其后保存，最终发布 SHA 见 Git 历史。

合并 checkout 的实际验证：

| 范围 | 结果 | 时间 |
|---|---|---:|
| 网格、原生 Blender 导出／文件读回、执行器及 fake-runtime 求值反馈 | 184 passed，0 skipped | 57.20 s |
| benchmark 筛选与 six-case runner 离线契约 | 104 passed，2 skipped | 3.15 s |

两项 skip 为明确 opt-in 的 benchmark 原生导入 smoke；本次未启用。没有真实模型请求、下载、后续物理 checker、FEA 或恢复暂停的 benchmark。网格套件包含真实导出与 stub；仅网格有效性检查，不将它表述为完整制造或物理批准。完整自交检测仍为 `NOT_EVALUATED`。

命令及原始日志：

```bash
PY=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python
ADSL_TEST_FIXED_REAL=1 ADSL_TEST_GEOMETRY_REAL=1 \
PYTHONPATH=/tmp/adsl_master_publish_imports:/tmp/adsl_master_publish_20261007 \
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -p no:cacheprovider \
  tests/test_local_precision_repair.py tests/test_mesh_validity.py \
  tests/test_mesh64_evaluation.py tests/test_mesh64_exports.py \
  tests/test_boolean_recovery.py tests/test_boolean_solver.py \
  tests/test_zero_area_tessellation.py tests/test_microcrack_welding.py \
  tests/test_mesh_manufacturing_export.py tests/test_mesh_display_execution.py \
  tests/test_execution_cleanup.py tests/test_mesh_evaluation_feedback.py

PYTHONPATH=/tmp/adsl_publish_audit_imports:/tmp/adsl_master_publish_20261007 \
PYTHONDONTWRITEBYTECODE=1 "$PY" -m pytest -q -p no:cacheprovider \
  benchmark/tests tests/test_benchmark_six_runner.py
```

[网格与反馈日志](reviewed_fixes_master_publication_20261007/mesh.log)、[筛选与 runner 日志](reviewed_fixes_master_publication_20261007/selection.log)、[最新原反例结果](mesh_precision_flip_repair_20261007.md)。原灯臂、象鼻及含 connector 的完整上部离线 GLB／STL 读回已通过；历史失败证据保留，没有重跑整灯 Agent 流程。

主目录的未提交 FEA、GPU、历史删除和其它本地资料不属于本次提交。发布使用独立干净工作树；主目录重叠的七项 tracked 修改及五项 untracked 文件与合并版本逐字节相同，更新前保存原文件／patch／哈希，更新后核对原有内容和删除状态不变。
