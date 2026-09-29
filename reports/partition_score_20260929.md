# 分件评分接入实施记录

基线 f264f35，分支 feat/fixed-assembly-partition-score；CPU，暂不合并 master。
证据根目录：temp/partition_score_20260929（主工作区）。

## S1

`python -m pytest -q -p no:cacheprovider tests/test_partition_score.py`：7 passed，5.21s。
真实 Manifold 实体/单元求交，覆盖实心、空腔、格线接触、薄斜体、桥梁空格及24旋转。
相互垂直的纯接触壳体求总体积出现1.39e-17舍入残差，逐壳体检查正体积和非零三维范围后正确排除；没有增加体积过滤阈值。
原式负分、缺件、参考不一致及相等分数均有测试。没有模型调用。

## S2

`ADSL_TEST_FIXED_REAL=1 python -m pytest -q -p no:cacheprovider tests/test_partition_score.py tests/test_partition_exports.py tests/test_assembly_physics.py -k 'not real_ and not fea'`：21 passed，7 deselected，27.97s。
包括真正 Blender 的 geometry/visual_only 导出、2 mm/unit、参考失败不改变 display、推荐 STL 读回评分、既有朝向与过悬调用。

## S3

`python -m pytest -q -p no:cacheprovider tests/test_partition_feedback.py tests/test_assembly_physics.py tests/test_assembly_feedback_recovery.py -k 'not real_ and not fea'`：19 passed，1 skipped，13 deselected，30.39s。
真实实体分件测量进入现有 Engineering 适配器（语言模型 mock）；授权、未知件拒绝、G=0机会、缺件unknown和Topology失败后的执行顺序通过。
旧顺序断言同步为 topology→overhang→standing→fea；skip/deselected未计入通过。

## S4

`python -m pytest -q -p no:cacheprovider tests/test_partition_score.py tests/test_partition_selection.py tests/test_partition_feedback.py tests/test_fixed_assembly.py tests/test_fixed_assembly_recovery.py tests/test_fixed_assembly_plan_revision.py tests/test_assembly_physics.py tests/test_prompts.py -k 'not real_'`：101 passed，1 skipped，14 deselected，36.04s。
候选选择使用真实原式算术、mock模型/物理门槛，验证收益、回退、必要修复、参考切换、优化修复链和resume不重置预算；打印输出逐文件核对选中source。
最后原生复核 `ADSL_TEST_FIXED_REAL=1 python -m pytest -q -p no:cacheprovider tests/test_partition_exports.py tests/test_partition_selection.py`：14 passed，13.47s。

## S5/S6 任务提交

已以 `d87c9d9` 启动 CPU 顺序任务：SF07 P0→P1、SF10 P0→P1，再从首个合格基线执行一次 Engineering/Coder 分组 smoke。
S5 固定源码，按用户确认仅调用真实 Image/Code 评审；S6 最多一次源码修改。基线未合格时不运行该组优化比较。
输入哈希：SF07 `3fe5dd8c176ee79af4dbda6ca93f25f457a6f644c062db85f729a090fd1e48a2`；
SF10 修复基线 `03e6d3f9582cf16951bf090570b88c67fac3e59a09ff4127b9a0995f119514bf`。
脚本 `temp/partition_score_20260929/run_cases.py`，日志 `cases.log`，提交清单 `case_job.json`。
所有实际模型输入/输出/工具调用分别保留在每个版本目录下 actual_model_calls 和 sessions_snapshot.sqlite3。

S4 的 NO_PROPOSAL 停止原因随后补充定向回归：12 passed，6.60s；避免被通用成功文案覆盖。
案例任务已在其进程中加载 d87c9d9 流程，报告以实际保存的 decision/critique 为准；未重启或追加模型预算。
