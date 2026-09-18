# Fixed assembly v1：两项修复与三打印件验证

日期：2026-09-18。实际起点：master `841afc4363cfe1362c66d4a8882b1b9e0b4344e5`。
未回退代码，保留原有无关修改。本报告随本轮修复提交；实验网格、图片及详细日志
仅保留本地，下面的实验产物链接需在本机访问，不随代码推送。

## 结论

**真实 StepCode 流程完成，但补充的 STL/总装一致性检查未通过。**
初次程序通过现有接口验收与 Image Critic，流程发布 `retained=original`、
`approved=true`。离线逐件对比发现两个支脚的总装 GLB 在榫头导入斜面处
与独立 STL 有约 2.86 mm³ 差异，超过现有容差 0.8281 mm³；因此不能报告
全部交付几何一致。保留实际流程记录，不将后验失败改写成 agent 已修复。
未扩展第三项生产修复、放宽容差、重跑生成或追加预算。

## 最小代码修改与测试

- `adsl-agents/service.py`：resume 的空源码首次 Coder 输入补齐完整
  `fixed_assembly`。测试通过真实请求序列化和 CLI 恢复入口，比对首次/恢复的
  完整 Coder JSON；覆盖正、负余量与非默认尺度。
- `adsl-core/core/assembly.py`：打印件 ID 校验拒绝 `scene` / `exploded`；
  根件、添加部件及导出前 `validate()` 均覆盖。`export_assembly()` 已在写文件前
  调用该校验，因此无需更改导出器。错误明确说明文件名保留原因。
- `adsl-agents/models.py`、`prompt/fixed_assembly.md`：Planner 同样拒绝这两个名字，
  提示词说明限制。`tests/test_fixed_assembly.py` 补上述回归。
- 示例脚本仅增加可选配置、prompt、task ID 参数，默认两件式示例不变；新增本例
  `three_parts_config.json` / `three_parts_prompt.txt`，更新示例 README。

```bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_fixed_assembly.py tests/test_prompts.py tests/test_execution_cleanup.py tests/test_refinement_budget.py tests/test_overhang_candidate_isolation.py tests/test_planned_prompt_generation.py --tb=short
```

结果：**77 passed, 6 skipped，239.93 秒**。六项是未启用的历史真实几何变体，
不能算本次执行通过。`bash -n`、`git diff --check` 通过。
测试启动曾在 Python 依赖导入时等待项目 `temp` 符号链接的文件系统访问；未改变
挂载、链接或系统环境。这与本例几何差异不是同一问题。

## 冻结设置与实际执行

- 仅 StepCode `gpt-5.6-sol`；无其他 API，无四个物理 checker，无历史批次。
- 1 单位 = 1 mm；总尺寸 80 × 32 × 52 mm；顶板 80 × 32 × 12 mm；
  两支脚主体各 16 × 20 × 40 mm，中心 X=−24/+24 mm，Z=0–40 mm。
- 槽单侧余量 +0.2 mm；只作未标定几何演示。生成前配置冻结，真实 Coder 请求
  中确认收到 `mm_per_unit=1`、`fit_offset_mm=0.2` 和 `[80,32,52]`。
- 一次初始生成，最多两次修补；实际 **初始生成 1 次、修补 0 次**。
  Image Critic 通过；Code Critic 保留但按既有条件未触发。不证明真实修复能力。
- 几何上限 120 秒未改；实际几何阶段 1.077 秒，不含启动/渲染。
  CPU Cycles，512²、32 samples；8 张总装图、2 张拆分图。
- 约 284.8 秒从保存输入到最终结果；Planner 约 218 秒、Coder 约 35 秒、
  Image Critic 约 10 秒（会话时间戳，包含模型/工具等待，不是纯推理计时）。
- 4 次模型请求，输入 25,947、输出 3,791，合计 **29,738 tokens**；
  缓存输入 3,840 已包含在输入数内，金额未知。未使用另一 API 的 token。
- tmux `adsl_fixed_three_parts_20260918` 已退出，exit=0。本次创建的代理已关闭。

## 几何结果与失败证据

`SupportLeg` 实例化为一个原型，然后 `.copy()` 两次，独立实例 ID 为
`leg_left` / `leg_right`。两个 `connect()` 都引用同一个 `shared_leg_tab_fit`
对象及同名参数；两份支脚 STL 字节相同。根为 `top_plate`，两接口/槽口 ID 不同。

共享参数：榫头 10 × 10 mm、插入 6 mm、槽深 6.5 mm、根部嵌入 0.8 mm、
导入斜面 0.8 mm。每接口实际外露新增榫体 590.7414 mm³、切除槽体
703.0401 mm³、根部嵌入 80.0003 mm³。接口止挡面在 (±24,0,40)，配对变换一致。

| 检查 | 结果 |
| --- | --- |
| 三份 STL 闭合、连通、零面积面 | 均闭合、各 1 实体分量、0 零面积面 |
| 总装尺寸与配对框架 | 80 × 32 × 52 mm；两个接口位置/方向符合声明 |
| 非预期体积穿透 | STL 按装配变换放置后及总装 GLB 中，各对交叠体积均 0 |
| STL 对独立部件 GLB | 三件对称体积差均 0 |
| STL 对总装 GLB：顶板 | 差 0，通过 |
| STL 对总装 GLB：左脚/右脚 | 差 2.858655 / 2.858644 mm³，均超过 0.828052 mm³，失败 |
| retained 发布记录 | 原始版本；源码/审核记录/图片/导出哈希均匹配；不是错版本发布 |

差异位于两个榫头顶端的导入斜面带：Z≈45.2–46 mm，左侧 X=−29..−19、
右侧 X=19..29、Y=−5..5 mm。独立支脚有 40 个三角面，总装对应件为 36 个；
总装实体主要少了上述体积。离线比较只读现有文件，精确重复顶点去重，不进行近邻
焊接/补洞/重网格化；按毫米、Z-up 和 manifest 变换逐件匹配，不全局 union。

证据足以定位到**独立导出与总装重新求值之间的不一致**：当前总装通过
`export_glb(assembly.scene())` 重新导出变换后的 CSG，而非直接实例化已验收网格。
尚未证明具体 Boolean/三角化内部根因；本轮不继续深入内核。现有验收验证了独立件
与声明几何，但未拦截这项总装重新导出的差异。以上是后验检查结论，不静默改写
原始 `approved=true`，也不将退出码 0 等同于完整几何验收成功。

## 产物与复现命令

本轮目录：`local_experiment/fixed_assembly_three_parts_20260918/`，旧结果未覆盖。

- [顶板 STL](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly/top_plate.stl)
- [左支脚 STL](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly/leg_left.stl)
- [右支脚 STL](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly/leg_right.stl)
- [总装 GLB](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/scene.glb)
- [拆分 GLB](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly/exploded.glb)
- [源码](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/source.py)
- [接口 manifest](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly/assembly_manifest.json)
- [最终版本与审核](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly_result.json)
- [后验导出差异与失败状态](../local_experiment/fixed_assembly_three_parts_20260918/saved_export_verification.json)
- [实际 API 用量](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/usage.jsonl)

```bash
# 本次已执行；如需未来重现必须另用新目录，不覆盖现有结果。
bash examples/fixed_assembly/run_smoke.sh local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke examples/fixed_assembly/three_parts_config.json examples/fixed_assembly/three_parts_prompt.txt fixed_assembly_three_parts
# 只读已生成网格与记录，结果保存在本例目录，不调用 API/Blender。
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python local_experiment/fixed_assembly_three_parts_20260918/verify_saved_exports.py
```

![总装](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/render/render_0001.png)
![拆分视图 1](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly/exploded_render/render_0001.png)
![拆分视图 2](../local_experiment/fixed_assembly_three_parts_20260918/stepcode_smoke/assembly/exploded_render/render_0002.png)

实物插入路径、保持力、承重、过悬、打印成功均未验证；间隙配合不等于实际固定。
