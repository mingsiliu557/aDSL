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
