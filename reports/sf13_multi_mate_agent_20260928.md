# SF13 Agent 多接口验证（2026-09-28）

结果：Agent 主动规划了 8 件、24 接口（17 条补充连接），两个修复候选均通过
visual_only 导出并保留全部接口；本次未通过外观与实体网格检查，未进入重力求解。

仅 topology + standing，FEA/overhang 关闭，全程 CPU；一个新初稿、两次 Agent 修复。
侧板包含零面积三角形，上层板和顶盖为开放网格。topology 虽枚举全部 24 个接口，
但均因端件不可测而 INDETERMINATE；standing 同样在网格前置条件处停止。
不能宣称 24 接口已完成重力检测。保留版本仍为 original，最新候选未获接受。

实际使用主分支 `7e7a4eb` 加工作区已有的未提交 rigid_flex standing 实现，
精确源码/哈希/diff 随实验保存；没有修改产品代码或人工修复生成模型。
MuJoCo CPU 控制预检通过；该控制结果不等于 SF13 动态仿真成功。

运行 824.77 s、21 次 API、437,245 tokens；approved=false，round_budget_exhausted。
详细说明、最新候选预览、全部产物和逐接口证据：
[实验摘要](../local_experiment/sf13_multi_mate_20260928T111227Z/SUMMARY.md)。

## 网格原因与旧修复复核

- topology 已检出第五层板 12 条、顶盖 36 条开放边，实际送入 Engineering；本轮
  Engineer 猜测木纹复杂度，Coder 仅改木纹后仍失败。两侧板的零面积面仍走
  PRINT_MESH_UNMEASURABLE，不在现有 localized_mesh_feedback 的自动几何修补入口内。
- CPU 内存对照：去木纹无效；去掉 0.35 mm 榫头倒角，顶盖通过但侧板仍退化。
  顶盖板体与单独榫头各自通过，二者第一次 UNION 就有 6 条开放边；最终顶盖 36 条。
- 缺陷在没有 GLB/STL 写出的 _build_shape + loop triangles 阶段已存在。
  顶盖倒角接缝顶点差约 0.000004～0.000008 mm；侧板槽口结果多边形有三个共线点，
  三角化产生零面积面。已定位 CSG/三角化阶段，未追踪 Blender 内核的具体舍入指令。
- 旧 8cc8e4c 开口反馈、ee712bc float64 NPZ、766db7d EXACT/ASCII 均仍生效；
  旧修复不保证所有新 CSG 闭合，也不修复上游已有坏网格。旧 SF13 木纹边界不能套用到此新实例。
- 原 source/导出文件哈希与既存记录一致、产品源码与运行快照一致；本轮没有新 API 或修复。

[对照表、精确坐标与证据](../local_experiment/sf13_multi_mate_20260928T111227Z/mesh_diagnosis/README.md)。

## 后续确定性代码修复（独立结果）

同一 SF13 源码通过公共局部重三角化重新导出后，两侧板零面积面均由 1 降为 0，
部件 topology 为 6/8 PASS，接口配对为 16/24 PASS；原 Agent 结果与资产保持不变。
第五层板/顶盖仍为开放网格，未运行 standing。详见
[零面积面修复与回归](zero_area_tessellation_20260928.md)。
