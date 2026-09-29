# aDSL 形状增强与生成实验材料（2026-09-29）

这是本轮四个 `temp/geometry_*_20260929` 目录的归档，包含真实输入、Agent 提示及调用记录、生成源码、GLB、渲染图片、检查报告和失败证据。归档不重新生成候选，也不修改历史结果。全部渲染使用 CPU。

## 先看哪些文件夹

| 文件夹 | 对应实验 | 组别与检查范围 | 已知结果 |
|---|---|---|---|
| [geometry_expression_20260929/](geometry_expression_20260929/) | SF06 扶手椅、SF21 落地灯、T02-bookshelf 曲边书架，三个文本输入的形状表达 A/B | A 增强前；B 新 API + prompt；所有正式 critic/checker 关闭 | 六组均完成八视图；局部形状改善，不能推断制造通过 |
| [geometry_elephant_000_20260929/](geometry_elephant_000_20260929/) | Toys4K `elephant_000`，同一张参考图的 A/B | A 增强前；B 新 API + prompt；所有正式 critic/checker 关闭 | 原 B 导出曾遗漏象鼻；现已用相同源码经确定性 Boolean 回退重新导出，当前展示图已恢复象鼻 |
| [geometry_sf06_no_fea_20260929/](geometry_sf06_no_fea_20260929/) | SF06 使用增强版的完整 Agent 流程，独立重新生成 | Image/Code、Topology、Overhang、Standing、Engineering/Coder；**FEA 关闭** | 已结束，`approved=false`；三项物理 checker 均 `INDETERMINATE`，详见下文 |
| [geometry_sf06_full_20260929/](geometry_sf06_full_20260929/) | SF06 最早提交、随后取消的旧配置 | 输入曾包含 FEA，但用户要求关闭后停止 | 在初始 Coder 返回源码前取消；**FEA 没有执行**。仅保留中断证据，不作为有效实验结果 |

A 指本项目增强前 `master@8943a8306ddcce69ad0c043f0079c0a14f0518f1`，不是未修改的官方上游版本。B 在 `feat/constructive-geometry`：三例文本展示使用 `5ed4209`，SF06 完整流程使用 `7ebfe4b`，大象参考图展示使用 `0efaceb`。精确版本与模型配置以各 `demo_result.json` / `job.json` / `runtime_config.json` 为准。两组使用同一 `gpt-5.6-sol` profile；该展示比较 API 与 prompt 的组合改进。

## 1. 三例文本输入 A/B

对比图均为 **上排 A（增强前），下排 B（增强版）**：

- SF06：[选定视角](geometry_expression_20260929/comparisons/SF06_selected.jpg) / [八视图](geometry_expression_20260929/comparisons/SF06_all.jpg)。
- SF21：[选定视角](geometry_expression_20260929/comparisons/SF21_selected.jpg) / [八视图](geometry_expression_20260929/comparisons/SF21_all.jpg)。
- T02-bookshelf：[选定视角](geometry_expression_20260929/comparisons/T02-bookshelf_selected.jpg) / [八视图](geometry_expression_20260929/comparisons/T02-bookshelf_all.jpg)。

目录为 `geometry_expression_20260929/{A,B}/{SF06,SF21,T02-bookshelf}/`。每个案例内：

| 路径 | 内容 |
|---|---|
| `input.json`、`plan.json` | 固定输入及实际 Planner 输出 |
| `source.py`、`source_attempt_*.py` | 最终源码和执行尝试快照 |
| `planner/`、`coder_initial/`、可选 `coder_execution_patch/` | 实际 system prompt、输入、输出、messages、tool_events 和耗时 |
| `exec_*/render/scene.glb` | 该次执行的三维模型 |
| `exec_*/source_index.json`、`analysis_geometry.json` | 源码索引与实际几何声明 |
| `views/render_0001.png` 至 `render_0008.png` | 六环绕视图及俯视、仰视 |
| `demo_result.json`、`runtime_config.json`、`usage.jsonl` | 状态、最终执行目录、源码 hash、预算与调用记录 |

A/SF06 最终资产来自 `exec_1`；其余五组来自 `exec_0`。`native_smoke/` 是人工小夹具的原生 API/导出/渲染验证，不是额外 Agent 案例；`stage_a.log`、`stage_b.log`、`stage_c.log` 是定向测试日志。详细结果见[实现与展示报告](../../reports/geometry_expression_20260929.md)。

## 2. elephant_000 单图 A/B

- [实际输入图片](geometry_elephant_000_20260929/reference/input.png)。只给模型这一张图。
- [选定视角对比](geometry_elephant_000_20260929/comparison.jpg)：**上参考模型，中 A，下 B**。
- [全部八视图](geometry_elephant_000_20260929/all_views.jpg)：参考、A、B 各占两行，图片已有标签。
- [两组输入一致性和运行数据](geometry_elephant_000_20260929/comparison_data.json)。

`reference/` 保存用户同意使用的公开同名 PLY、CPU 渲染、上游 README 及 [provenance.json](geometry_elephant_000_20260929/reference/provenance.json)。来源为 [Yang2001/toys4k_meshes](https://huggingface.co/datasets/Yang2001/toys4k_meshes)，原始 `.blend` 与原 aDSL 对应运行没有取得，不能称为原论文样本的精确复现。除输入图外，其余参考视图只用于展示。参考模型与生成模型的朝向不同，同编号相机不等于语义对齐的侧面。

`A/`、`B/` 的文件意义同上一节。A 展示资产为 `A/exec_0/render/scene.glb`；B 当前展示资产为 `B_reexport_recovery/exec_final/render/scene.glb`。`B/exec_1/render/scene.glb` 保留为原始缺失象鼻的失败记录。两组各一次初始生成；A 无执行修补，B 一次执行修补。

### B 象鼻恢复与原始失败证据

当前对照图的 B 已替换为修复后八视图。没有重新调用 Agent，也没有修改其源码；公共导出路径在 Boolean 无效时用原操作数重算，并验证 float32 网格和 GLB 部件完整性。见 [重新导出记录](geometry_elephant_000_20260929/B_reexport_recovery/reexport_result.json) 与 [实际 GLB](geometry_elephant_000_20260929/B_reexport_recovery/exec_final/render/scene.glb)。旧对照图保存在 `diagnosis/before_boolean_recovery/`，以下仍为原始失败证据。

源码包含 `CurvedTrunk`。首次执行遇到退化面错误，Coder 将局部 hull 链改成渐缩拉伸段与球体的 Boolean 合并。修补后 glTF 导出仍出现网格无效及数组长度不匹配，并明确跳过该 mesh：

- [首次执行错误](geometry_elephant_000_20260929/B/execution_error_0.txt)。
- [修补前源码](geometry_elephant_000_20260929/B/source_attempt_0.py) / [修补后源码](geometry_elephant_000_20260929/B/source_attempt_1.py)。
- [当次导出 stdout](geometry_elephant_000_20260929/B/exec_1/stdout.log)：`Mesh ... is not valid`、`Array length mismatch`、`has no primitives and will be omitted`。
- [后续完整场景诊断](geometry_elephant_000_20260929/diagnosis/full_scene_boolean_trace.json)：Boolean 后仍有象鼻网格；[复现 GLB](geometry_elephant_000_20260929/diagnosis/full_scene_reproduction.glb) 再次遗漏象鼻。`diagnosis/` 为运行结束后的人工诊断，不是模型当时收到的证据，也不是修复候选。

历史 `demo_result.json` 的 `rendered` 只表示渲染文件生成，不表示形状完整。保持原记录不改写，故本 README 明确标记此失败。更低的面数不能作为此例增强成功的证据。

## 3. SF06 增强版完整流程：不含 FEA

这是独立于文本展示的一个新生成任务，不是把上节 SF06/B 源码直接送检。冻结尺度为 900 × 800 × 900 mm，开启分件/连接和既有修复循环。

优先阅读：

- [完成摘要](geometry_sf06_no_fea_20260929/completion.json)：运行约 764 秒，最终 `approved=false`，没有 qualified 版本；停止原因 `export_unassessed_no_geometry_repair`。
- [最终选择与评审](geometry_sf06_no_fea_20260929/generate/assembly_result.json)：选中 `original`，working 为 `attempt_0001`。
- [版本记录](geometry_sf06_no_fea_20260929/generate/assembly_versions.json)、[检查结果](geometry_sf06_no_fea_20260929/generate/checker_results.json)。
- [源码](geometry_sf06_no_fea_20260929/generate/source.py)、[初始计划](geometry_sf06_no_fea_20260929/generate/plan.json)、[实际装配资产](geometry_sf06_no_fea_20260929/generate/assembly/)、[最终显示图](geometry_sf06_no_fea_20260929/generate/render/)。
- `generate/rounds/round_01/`：初始评审、Engineering 输入输出、checker 报告和导出资产。
- `generate/rounds/round_02/`：后续源码候选、执行及评审证据。
- `generate/api_calls/`、`generate/stage_inputs/`：实际模型调用和阶段输入；`generate/repair_history.jsonl` 记录修补历史。

选中版本的 chair_body 因退化网格未能完整显示；Topology、Overhang、Standing 均为 `INDETERMINATE`。检查被配置启用不等于成功完成测量，更不等于物理通过。没有执行 FEA。

## 4. 已停止的 SF06 旧配置

`geometry_sf06_full_20260929/STOPPED_BY_USER.json` 和 `status.json` 说明中断原因。该目录用于解释为什么历史输入中存在 FEA 字段；不能将其与无 FEA 任务拼成一次运行，也不要按旧脚本恢复。正式复查以 `geometry_sf06_no_fea_20260929/` 为准。

## 归档完整性与读取约定

[archive_manifest.json](archive_manifest.json) 列出原始材料相对路径、字节数及 SHA256，以及排除项。本次复制 757 个材料文件，约 70.3 MiB；本 README 与清单是新增索引。排除会话数据库、缓存与本地 Python 路径软链接；实际角色提示、输入输出、工具调用 JSON/日志保留。发布前凭据字段与 token 模式扫描无命中。

原始 JSON/日志中的绝对路径、历史 README 中的本机链接保持不变，以保留证据。下载后，将 `/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/` 前缀替换理解为本 README 所在目录即可找到归档文件；其他原运行环境依赖路径并未打包。启动脚本保存的是原运行命令，需配置本地环境与凭据，不能保证下载后直接启动。

本归档没有新增候选、没有修复象鼻，也没有将运行结束或渲染成功改写为检查通过。
