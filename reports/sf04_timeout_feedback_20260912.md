# Checker 超时、SF04 底框与工程反馈精简

日期：2026-09-12（UTC）。仅针对性验证，不运行批量实验，不调用模型 API。

## 修改范围

- `adsl-agents/checkers.py`：共用父进程执行器，有界 TERM/KILL 回收进程组；日志直接落盘；根据最后一个未完成 OCC 操作生成几何预处理超时证据。不能对不可中断的内核 I/O 承诺立即杀死，但不再无限等待回收。
- `experiments/standing_fea_30/run_batch.py`：最终评估读取与修复循环相同的 checker specs，不把 21600 秒逐个覆盖给 checker；生成结束立即记录 `EVALUATING`。保留生成和导出原有时间限制。
- `experiments/topology_connectivity/analyze.py`：仅增加 OCC fuse/cut/intersect 的开始、完成和错误日志，未改容差、缩放、实体过滤或连通判定。
- `adsl-agents/service.py`：工程 critic 的重复结果改为状态摘要、单份 typed findings 和完整报告路径；保留指标、关系、源码候选、定位歧义和修复进展。完整数据仍可通过现有 read_file 获取。
- SF04 的改动另存为 `local_experiment/sf04_fuse_diagnostic/repaired_source.py`，只有 `seat_apron` 启用分阶段 UNION。原始生成代码和其他圆角构造不变。

## 超时语义与验收

Topology 沿用现有 900 秒配置；不是把所有 checker 都改成 900 秒，FEA 等继续使用各自原预算。

若最后未完成操作是 OCC 几何预处理，结果为 `ERROR`，violation code 为 `GEOMETRY_PREPROCESS_TIMEOUT`，记录 part、operation、operand_count、started_at 和日志路径。不能按 FAIL 计为模型断开，也不能按 PASS 计入成功。

只有此几何预处理不可用时，依赖它的 FEA 才跳过并记录 `INDETERMINATE / GEOMETRY_PREPROCESS_UNAVAILABLE`。不是把所有 one_piece FAIL 都直接当作 FEA 失败。独立 standing 继续运行，批处理保存异常后继续下一样本。

测试覆盖父进程/子进程忽略 SIGTERM、父进程先退出、最终评估读取共享短超时、连续两个样本保存错误后继续，以及正常面接触 PASS、真实间隙 FAIL 和点接触 FAIL。测试使用短预算，不实际等待 900 秒。

## SF04 限定诊断

对象路径：`IsolatedArmchairDisplay/chair_body/structural_frame/seat_apron`。

原参数：size=(1.62, 0.88, 0.22)，center=(0, -0.02, 0.70)，radius=0.055；沿用原完整场景导出的 scale=0.044444444444444446 m/scene_unit。

| 诊断 | 结果 |
| --- | --- |
| 输入审计 | 3 方块、12 圆柱、8 球；完全重复 0，零尺寸/零长度输入 0 |
| 仅方块 UNION | 约 0.7 秒完成 |
| 仅圆柱 UNION | 约 4.3 秒完成 |
| 仅球 UNION | 约 0.6 秒完成，8 个分离球体符合该子集预期 |
| 方块和圆柱一次性 UNION | 超过 40 秒诊断预算，终止 |
| 分阶段加入方块、圆柱、球 | 首次约 26.6 秒，得到 1 个实体 |
| 修复源码实际导出的嵌套 UNION | 首次复核约 15.2 秒，得到 1 个实体 |

结果支持“该构造的合并关联顺序影响本例求解时间”；仍没有证明某一对具体相切曲面是唯一根因。分组合并不是全项目性能保证。

修复保留相同的 23 个原语及完整参数，只改变 UNION 的关联顺序。前后采用同一 CSG 占据计算、0.005 场景单位网格，占据、顶点、面和包围盒一致；网格和 OCC 最终均为单实体。采样网格一致不是无限精度的表面等价证明，原始 OCC 整次 UNION 未完成，不能声称测得其精确体积用于前后比较。

底框的修复不等于 SF04 整把椅子已通过所有 checker。没有改 SF01、全局 RoundedPad、MuJoCo、公共缩放或小实体过滤。

证据目录：`local_experiment/sf04_fuse_diagnostic/`：

Git 归档另存于 `reports/sf04_timeout_feedback_20260912/`：仅包含修复源码候选、输入审计、几何验证和反馈大小摘要。下列大型网格与完整诊断仍留在本地，不随代码提交。

- `audit.json`、各分组 JSON：原输入与限时诊断；`probe.py` 是小复现。
- `verify.py`、`verification.json`、`verification_progress.jsonl`：实际修复源码的形状与 OCC 复核；最新耗时以 JSON 为准。
- `before_seat_apron.stl`、`after_seat_apron.stl`：原始/修复底框网格，单位为 scene_unit。
- `repaired_source.py`：SF04 完整源码候选，仅底框启用 staged_union；没有覆盖实验基线。

诊断脚本遇到当前 Trimesh 缺少旧 `remove_degenerate_faces` 方法，改为直接使用现有 CSG occupancy 和 marching cubes 比较；未修改公共导出代码。

## API 输入离线比较

固定输入：SF03 / Ours / round_02 的已有结果。只比较工程反馈字段，不包含图片、工具定义、共同 prompt 字段及会话历史。

- 原反馈：327308 字符。
- 精简后：66362 字符，减少约 79.7%。
- Finding：5 → 5；指标、源码候选等由针对性测试确认保留。
- 原始报告与修复历史仍在文件中，模型需要细节时按路径读取。

没有调用 StepCode，因此没有实测网络请求耗时、token 账单或修复质量增益；不能把字符缩减等同 API 提速。模型、图片、critic、最大轮数未改。会话恢复仍使用既有角色会话，本轮未改协议级重试/恢复语义；HTTP 400 的确切上游原因尚不确定。

## 回归命令

### 接收方证据收敛补充（2026-09-12）

补齐候选 Coder 原先通过 `checker_evidence=[run.result.model_dump() ...]` 重发完整 baseline 的遗漏：

| 接收方 | 默认输入 |
| --- | --- |
| Engineering Critic | 所有 checker 状态摘要；未通过 finding 的关键指标、源码候选、区域和关联；不直接携带嵌套原始报告 |
| 候选 Coder | 当前 RepairProposal、相关源码位置，以及该方案 finding_ids 对应的证据；不含无关 checker/finding 或整份历史 |
| 详细报告 | 保存在原文件；按 json_pointer 选字段，或 offset/max_chars 取文本片段 |

`read_file` 单次最多返回 12000 正文字符（另加少量截断元信息）。小文件仍兼容只传 path 的调用；大文件明确返回 truncated 与 next_offset。JSON 字段读取先在本地解析，只有选中字段的有界文本进入工具输出。Unicode 字符分页和超长单行同样受限，工作区路径约束不变。

例如读取一条 finding 的数值：

```json
{"path":"rounds/round_02/checkers/topology/result.json","json_pointer":"/findings/0/metric","offset":0,"max_chars":2000}
```

通过明确的工具参数说明告诉 agent 优先按字段获取所需证据；函数工具参数说明参考 [OpenAI 官方函数调用文档](https://developers.openai.com/api/docs/guides/function-calling)。不是要求模型主动把全部分页读回来，也没有新增会话累计 token 限额或修改恢复协议。

固定 SF03 / Ours / round_02、原 `round2_high_backrest_grain_overlap_resize` 方案离线结果：

- Engineering Critic 反馈字段：66362 → 17838 字符。
- Coder checker_evidence：125957 → 12060 字符（减少约 90.4%）。
- Coder 恰好收到方案指定的 4 条 topology finding，未携带 standing/FEA 的无关结果。
- 仅比较反馈字段，不含图片、共同提示词、工具定义和既有会话历史；未调用 API，不能解释为实测延迟或 token 账单降幅。

证据：`local_experiment/sf04_fuse_diagnostic/receiver_feedback_comparison.json`，小型摘要另存至 `reports/sf04_timeout_feedback_20260912/receiver_feedback_comparison.json` 随代码归档。新增 `tests/test_bounded_checker_inputs.py` 覆盖实际候选请求、关联筛选、字段读取、默认/显式读取上限、中文分页、小文件兼容和路径约束。

本次补充回归：`test_bounded_checker_inputs`、`test_checker_timeout_feedback`、`test_checker_unification`、`test_workflow_checkers`、`test_codex_runner_contract`、`test_service_config`，共 58 passed（9.78 秒）；`git diff --check` 通过。测试阶段未调用真实模型 API、未重新运行几何实验；代码归档随后按用户要求提交至 master。

### 原超时与几何回归

在现有 aDSL 环境、Gmsh Python 路径及 FEA 动态库路径下运行：

```bash
python -m pytest tests/test_checker_timeout_feedback.py tests/test_workflow_checkers.py tests/test_standing_fea_30.py tests/test_topology_connectivity.py tests/test_checker_unification.py -q
```

最终结果：50 passed（12.02 秒），`git diff --check` 通过。底框脚本由父进程施加 90 秒上限运行，最后一次 OCC 合并 15.04 秒，1 个实体，前后网格体积均为 0.3227761875 scene_unit³。

未启动或重启批量实验、未推送 GitHub。已加载旧代码的运行进程不会自动热更新。
