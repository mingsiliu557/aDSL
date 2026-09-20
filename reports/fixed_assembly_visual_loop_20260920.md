# Fixed assembly：仅视觉／代码流程验证

日期：2026-09-20 UTC。代码基线 `fcfdeda7fe78e73969ec206389396bdc1e77c0ec`，
本报告随本次视觉流程代码提交归档。实际运行加载文件哈希见实验 `input.json`，原资产未修改。
实验模型、图片及API日志未加入Git；下文实验链接在本机工作区可用，不保证GitHub可打开。

## 范围与修改

按用户最新要求，以 `fixed_assembly.validation_mode=visual_only` 显式关闭装配
闭合性、连通分量、接口体积、穿透和尺寸测量；四个物理 checker 均未执行。
`geometry` 模式仍保留，未放宽其阈值。构造参数／树结构契约仍由原 API 校验。

- `adsl-agents/models.py`：添加验证模式字段，默认保持旧 `geometry`。
- `adsl-core/core/export/export_assembly.py`：视觉模式直接收集每个最终打印件经
  原 Blender CSG 后的三角网格，不调用 Manifold 合并／实体检查，不另跑 body、tab、
  cutter 检查。STL、独立／总装／拆分 GLB 共用本次网格。保留材质和坐标转换。
  文件一致性用原 float32 长度界比较三角形坐标，允许面／顶点重排；不要求闭合体，
  不用实体布尔差当导出检查。这只是序列化一致性，不是几何／制造验收。
- `adsl-agents/utils/asset_executor.py`：有网格即尝试正常或诊断渲染；缺失／遗漏明确
  标注。保留无图原因及错误报告。总装失败不再直接阻断所有 Critic。
- `adsl-agents/service.py`、`fixed_assembly.py`：有图进入 Image 和必要的 Code Critic；
  无图只跳过 Image，仍可读源码诊断。残缺／渲染未完成不允许完整外观通过。
  下一次修补基于 working 版本，retained 单独保存，预算和回退不变。
  `visual_only` 接受仅限视觉／代码审核和导出一致性；几何始终 NOT_EVALUATED。
- `prompt/fixed_assembly.md`：明确视觉模式下“未检查”不等于几何失败，不指导模型
  为未执行的检查做猜测性网格修补。保留原 Code Critic 的视觉纠错职责。
- 单例入口 `experiments/fixed_assembly_prompt/verify_candidate.py` 和
  `verify_visual_stepcode.sh`：仅 stepcode；不调用 Planner／初始生成；最多一次修补。

## 轻量验证

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python \
  -m pytest -q -p no:cacheprovider \
  tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_diagnostics.py \
  tests/test_fixed_assembly.py tests/test_fixed_assembly_recovery.py \
  tests/test_fixed_assembly_exports.py tests/test_fixed_assembly_empty_mesh.py \
  tests/test_fixed_assembly_prompt.py tests/test_fixed_assembly_existing.py
```

110 passed / 6 skipped，5.22 秒。跳过为需显式开启的旧真实几何实验。
覆盖几何 FAIL 仍评图、无图源码诊断、working 连续修补／恢复预算、保留原资产、
纯视觉不调用实体检查、输出不一致被拒、缺图不通过、旧模式行为保留。
测试不调用真实 API，不做模型 CSG；导出一致性测试使用已有小网格序列化。

## 唯一真实单例

- 输入为此前失败源码的副本：
  `local_experiment/fixed_assembly_prompt_20260919T074507Z/SF03/generate/rounds/round_02/candidates/01_assembly_or_appearance/source.py`。
- 新结果：[SF03 目录](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03)。
- 固定 1 mm/scene unit、90 × 80 × 180 mm 目标尺寸、单侧 +0.2 mm 间隙；不修改原源码。
  本轮未测量目标尺寸达标与否。
- 实际初始生成 **0** 次；允许修补 1 次，实际 **0** 次。
- API 为 stepcode `gpt-5.6-sol`，3 次底层调用均成功，共 **34,249 token**。
  本次自启 `lms_proxy`，结束后已自动关闭；不影响原 CLIProxy。
- 单例总耗时 **157.60 秒**；部件网格导出／文件比较 **1.63 秒**。
  API 共 **136.71 秒**；其余约 20.89 秒包含导出、渲染、记录和发布，并非纯渲染计时。
- 6 个打印件，8 张总装图、2 张拆分图；STL／局部 GLB／总装／拆分对应文件
  **24 项序列化比较通过**。未运行连接性、接口体积或其他物理检查。

| 实际阶段 | 结果 |
| --- | --- |
| 原失败源码 → connector 总装／导出 | 完成，未因非闭合实体检查阻断 |
| 总装／拆分渲染 | 完成 |
| Image Critic | 执行，拒绝：靠背只有单侧立柱，横梁悬空 |
| Code Critic | 执行并读取源码，随后批准，否定了 Image Critic 的意见 |
| Coder 修改／修改后再检查 | **未执行**：沿用现有 Code Critic 纠错机制后提前接受 |
| 最终 retained | `original`；源码哈希与输入一致 |
| 流程结果 | `approved=true`，范围仅 `visual_code_only`；不是几何通过 |
| 几何恢复／实体连接性 | **未评估**，没有修补成功证据 |

实际 API：Image Critic 7,874 输入 + 246 输出（24.95 秒）；Code Critic 读取调用
11,530 + 48（41.67 秒），继续回答 13,740 + 811（70.09 秒）。没有重新生成或批次。

## 关键剩余问题：源码声明不能证明渲染结果正确

Image Critic 正确记录了不完整靠背的视觉现象。Code Critic 则认为源码已经包含
`backrest_left_post`、`backrest_right_post` 及横梁，因此将图像问题解释为误判。
然而本次保存的多个角度和拆分图仍显示靠背缺损；**源码存在不等于 CSG 之后的显示几何存在**。

本轮保留了原视觉纠错机制，没有为得到一次修补而追加预算、重跑或手改模型。
因此结论必须拆开：**候选导出 → 两个 Critic 的流程已经可执行；视觉目标未被可靠确认，
真实修补能力本次未验证。** 软件的 accepted 记录保留原样，不能把它解释为独立核验
后的外观正确或装配成功。后续若调整 Critic 的冲突裁决，应另行确定最小范围。

## 图片与文件

总装正面（实际候选，靠背缺损仍可见）：

![总装正面](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/render/render_0001.png)

总装另一角度：

![总装斜视](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/render/render_0004.png)

拆分展示（现有相机配置使间距较大、零件较小，未另改渲染或模型）：

![拆分图](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/assembly/exploded_render/render_0001.png)

- [完整实际结果与调用开销](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/result.json)
- [最终总装 GLB](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/assembly/scene.glb)
- [拆分 GLB](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/assembly/exploded.glb)
- [接口／导出记录](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/assembly/assembly_manifest.json)
- [Image Critic](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/rounds/round_01/image_critique.json)
- [Code Critic](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/rounds/round_01/code_critique.json)
- [原始保留源码](../local_experiment/fixed_assembly_visual_20260920T034602Z/SF03/source.py)

源哈希：`ffe74e385038844e3f63c51ba28c44c5317d0a3698d674bc4f0a2f33d6aff42d`。
本次没有几何恢复结论，不声称真实插接固定、承载或制造成功。

## 后续5轮上限复测

用户随后授权同起点新开最多5轮（首轮检查+最多4次修补），目录
`local_experiment/fixed_assembly_visual5_20260920T043431Z/SF03`。
实际仍在第1轮结束、0次修补：Image指出缺损靠背，Code根据源码声明及部件shown记录
推翻拒绝，最终保留original。源码哈希未变；总耗时920.56秒，3次API共41016tokens。
几何与四checker仍未执行，stepcode代理已结束。增加上限未改变提前接受问题。
只读对照确认官方亦有Code覆盖Image的规则；本地还存在候选审核输入不完整、重复图片和
展示元数据含义易被误用的差异。目前只记录问题，未实施新的Critic裁决策略。
