# 局部 Boolean：FAST / EXACT / Manifold 对照

2026-09-22。**结论：相同原始输入下，两个异常均由 FAST 重现；仅换 EXACT 即可消除本次开口/退化，Manifold 也有效。但实体正确不等于颜色细节完整，不能称整个模型已修好。生产后端未改。**

## 范围与输入凭据

- 实际 HEAD：`ee712bc4daf347901e750d3c504ee4c4a4490b3d`，未回退。已有无关删除/脚本修改不动。
- 独立脚本：`experiments/fixed_assembly_prompt/compare_boolean_backends.py`。
- 本地全部产物：`temp/boolean_backend_comparison_20260922/`。没有覆盖旧诊断、生成资产或主实验成绩。
- Blender 4.0.0 / manifold3d 3.5.2 / trimesh 5.0.0 / NumPy 2.2.6 / Python 3.10.21；复用现有环境。
- 均为现有源码，1 scene unit = 1 mm。无模型 API、agent、渲染重跑、模型参数修改、FEA 或整批补评。
- 每步复用 `run_checker()` 独立进程组，120 秒上限；CPU affinity 4 核、OMP 4、BLAS 1。没有延长或自动重试。
- 保存 `.blend` 原始对象，包括局部顶点、多边形、父级与变换、材料槽；Manifold 使用这些对象相同世界/mm 坐标的 float64 三角网格，而非损坏的 Boolean 输出。
- 所有输入精确重复顶点合并数为 0；没有近距离焊接、补洞、删面、重网格化或修改容差。
- 运行前保存 `passport.json`；源码 SHA256、输入 NPZ/BLEND 哈希及具体参数见各 `checkers/capture/inputs.json`。

| 对照 | 固定来源与选定运算 | 源码 SHA256 |
| --- | --- | --- |
| SF02/w | 原始 `generate/original/source.py`；`leg_front_left_part` 第 4 次 Boolean，`post ∪ grain_line_1` | `ee48c63b424c6d173dc547e33cb6382e55c7099d63f34d8cf3f7e9c7e3537e7d` |
| SF03/w | 原始 `generate/original/source.py`；`backrest_unit` 第 5 次 Boolean，两立柱的既有并集 ∪ `back_lower_rail` | `f13f130ef733ca39152041b7d0fbe2542427db40066d98f173ba1bd01d500ae3` |
| SF07/wo 正常对照 | `round_04/candidates/03_assembly_or_appearance/source.py`；已通过件内检查的 `top_plate`，圆桌面 ∪ 榫头 | `3af44f64f0f5445faa387e22b17ca14e0b9b6a42c1c013a901cb06410524783b` |

根目录均为 `temp/assembly_topology_paired_fresh_20260921T173631Z/`。SF02/SF03 的输入三角坐标与上一轮保存的 `before_base/before_operand` 逐值相同；此次 FAST 输出也与上一轮指定 Boolean 的 `after/triangles.npz` 逐值相同。不是改参数后另做一个近似案例。

Blender 仅切换 solver；保留现有默认 `double_threshold=9.999999974752427e-07`、`use_self=False`、`use_hole_tolerant=False`、`material_mode=INDEX`。Manifold 使用 Mesh64、默认精度、不覆盖 tolerance；六个输入均返回 `Error.NoError`。

## 几何结果

每格为 **边界边 / 零面积三角面 / 非流形边**。0/0/0 之外，还检查有限坐标、闭合、朝向、体积和表面分量。

| 运算 | FAST | EXACT | Manifold |
| --- | --- | --- | --- |
| SF02 腿 + 木纹 | **4 / 0 / 0，开口** | 0 / 0 / 0，有效 | 0 / 0 / 0，有效 |
| SF03 立柱 + 下横梁 | **0 / 8 / 13，退化且非流形** | 0 / 0 / 0，有效 | 0 / 0 / 0，有效 |
| SF07 正常桌面 + 榫头 | 0 / 0 / 0，有效 | 0 / 0 / 0，有效 | 0 / 0 / 0，有效 |

所有有效输出都是有限、闭合、朝向一致的单个正体积表面分量。SF03 FAST 的表面分裂计数不是可信实体数量，不能据此宣称有 10 个断开实体。

多边形阶段已经有异常：SF02 FAST 有 4 条边界边；SF03 FAST 有 2 个零面积多边形及 6 条非流形边，三角化后成为 8 个零面积面。EXACT 在多边形与三角形阶段均没有这些缺陷。因此这两个局部异常不是 NPZ 序列化新增，也不是单纯的最终三角化问题。

## 形状完整性：独立证据与限制

不以 Manifold 输出作为唯一真值，也不以无效 FAST 的面积/体积作为可靠真值。

| 运算 | 独立参考 | EXACT / Manifold 结果 |
| --- | --- | --- |
| SF02 | 木纹盒完全包含于 22×22×75 mm 腿盒内，只有前平面齐平；预期实体并集就是腿盒，体积 36,300 mm³ | 体积完全相符；包围盒无差；到腿盒表面双向顶点/面心距离 < 8e-15 mm；4,144 个占据探针无缺失/新增 |
| SF03 | 两个 12×12×98 立柱 + 80×10×14 横梁 − 两个 12×10×14 重叠；预期 36,064 mm³ | 两者体积完全相符；包围盒无差，顶点高度仍 180 mm；4,168 个占据探针无缺失/新增，立柱和下横梁主体保留 |
| SF07 | 保存的离散圆桌面和榫头体积之和减去 10×10×1 mm 嵌入根部；预期 90,485.499878655 mm³ | EXACT 体积差 −4.93e-6 mm³，Manifold −3.43e-7 mm³；FAST +9.47e-5 mm³；三者包围盒无差、4,384 个占据探针无缺失/新增 |

占据探针采用固定随机种子 20260922 的 4,096 个空间点，加原输入面心向两侧偏移的局部点；由原始有效输入的 `A.contains(p) OR B.contains(p)` 独立给出预期。排除靠近表面的数值不确定点，长度界沿用现有 `length_bound()`，未改变 checker 阈值。这是有限采样证据，不是所有表面/全部微小特征的形式化证明。

**外观警告：SF02 木纹颜色没有被证明保留。** 原木纹是深色窄盒，但没有突出腿体；前平面与主体齐平。EXACT/Manifold 的实体并集正确，输出中该深色的表面面积却为 0。FAST 在对应区域留下开口，不能把其异常显示当作正确木纹真值。Manifold 本次保存面来源材料线索，但没有接入生产 Blender 材质/渲染链。SF03 EXACT 在当前 `INDEX` 材质策略下也未保留下横梁的独立颜色，Manifold 的来源材料记录不等于渲染保真已通过。

此前 SF02 真实修补后的弧顶丢失属于另一份候选/另一段 CSG。本次只测指定 UNION，没有包含该弧顶运算，**不声称弧顶问题已解决**，也没有偷偷扩展第四个案例。完整后续 CSG、完整部件 topology、Image Critic 均未重跑。

## 耗时

下表仅为本地一次原生 Boolean 操作耗时，不含输入加载、进程启动、测量；Manifold 强制求值后才停止计时。

| 运算 | FAST | EXACT | Manifold |
| --- | ---: | ---: | ---: |
| SF02 | 1.60 ms | 15.37 ms | 0.144 ms |
| SF03 | 2.04 ms | 10.06 ms | 0.499 ms |
| SF07 正常对照 | 1.43 ms | 3.51 ms | 0.446 ms |

每个后端独立进程约 2.7 秒，输入准备约 5–13 ms；九次后端操作均完成，无超时/异常。捕获+三后端+形状分析的进程墙钟累计约 46.21 秒。单次、小几何结果只说明本例成本，不能推断整个生成/渲染流程能按此比例加速，不能推断 EXACT 对所有复杂 CSG 都无超时风险。

## 公共路径判断

1. **已证实**：生产 `_apply_boolean()` 的 FAST 正常返回即接受，没有检查此次开口/退化；这两个具体案例的坏几何确实来自该 FAST 路径。不是只能让 agent 分别改木纹才有办法。
2. **本轮支持的较小下一步**：优先验证公共 EXACT 求值策略，或在已确认无效的 FAST 结果之后从未污染的原始操作数重新 EXACT 求值。EXACT 已足够解决两次局部实体异常，没有证据要求立即全面替换为 Manifold。
3. **仍需单独确认**：材质/齐平细节和完整后续 CSG 保留；不能仅把坏面计数归零就自动发布为正确模型。Manifold 作为部件内部 Boolean 候选值得保留，但材质传播、混合开口输入及完整递归 CSG 尚未验证。
4. **本轮未实施公共策略修改**。没有改变 agent/checker/connector、阈值、原模型或远端。没有新增模型修补机会，也没有把检测改进计为 agent 收益。

官方文档说明 FAST 对重叠几何支持有限，EXACT 支持重叠但计算成本可能更高；这只是实验动机，上述结论来自本机同输入对照。[Blender Boolean 文档](https://docs.blender.org/manual/en/2.91/modeling/modifiers/generate/booleans.html)；Manifold 的输入须满足其流形条件，它不是坏输入自动修复器。[Manifold 官方说明](https://github.com/elalish/manifold)

## 可复核文件与命令

- 总设置：`temp/boolean_backend_comparison_20260922/passport.json`。
- 每例：`<case>/comparison.json` 包含质量、体积/占据、材料、耗时。
- 原输入：`<case>/checkers/capture/operands.blend`、`base.npz`、`operand.npz`、材料与 SHA256。
- 三后端：`<case>/checkers/{FAST,EXACT,MANIFOLD}/mesh.npz`、`measurement.json`、`materials.json`；Blender 另存多边形及三角化诊断。
- 各步 `invocation.json`、stdout/stderr、`*_process.json` 保存调用、限时与状态。这里 process/result 的 PASS **只表示诊断完成，不是物体通过**。

实际执行：

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m py_compile \
  experiments/fixed_assembly_prompt/compare_boolean_backends.py
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python \
  experiments/fixed_assembly_prompt/compare_boolean_backends.py \
  --output temp/boolean_backend_comparison_20260922
```

复现需换一个不存在的新输出目录。此次真实完成 3 次输入捕获、9 次后端重放、3 次离线形状比较，输入/源码哈希断言与两次历史 FAST 对照断言通过。未运行整套物理测试或真实 API。使用 academic-research-suite 的执行记录约定冻结输入与设置、保存失败并区分实体有效和外观保留；未扩展研究范围。

## 后续实施：用户授权默认 EXACT（2026-09-22）

以上是 `ee712bc` 上的历史对照，不回写实验成绩。用户随后明确要求替换并推送。
公共 `export_glb.py::_apply_boolean()` 现直接选 EXACT，适用于 UNION、DIFFERENCE、INTERSECT；
保留原有参数/材质设置、异常清理与传播，不回退 FAST、不改变超时和 checker 阈值。
不代表完整物体/齐平木纹/弧顶已通过复核。历史对照的重新捕获命令应在 `ee712bc` 上运行，
或直接复用已保存原始 `.blend` 操作数；不能用新策略的前序运算冒充旧 FAST 输入。

**后续回归发现阻断项，尚未提交或推送该替换。** 正常支架从旧策略 PASS 变成 EXACT 导出后 STL 无效；
SF02/SF03 整件 topology 也未全部通过。详见 [公共 EXACT 拓扑验证](exact_public_topology_validation_20260922.md)。
