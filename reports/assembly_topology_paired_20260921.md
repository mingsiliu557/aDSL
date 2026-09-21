# 六种物体：connector + w/wo assembly_topology

2026-09-21 UTC；实验提交说明，不是已完成结果。

## 设计与复用

用户明确要求六个不同物体的两组独立 prompt-to-3D，均有 connector；
不是上一批“三个已有资产 + 三个新生成”的初末比较。

| 原始任务 ID | wo topology | w topology |
|---|---|---|
| SF07 | 新生成 | 复用已结束 new_SF07（未全部通过） |
| SF03 | 新生成 | 复用已结束 new_SF03（未全部通过） |
| SF13 | 新生成 | 复用已结束 new_SF13 |
| SF02 | 新生成 | 新生成 |
| SF10 | 新生成 | 新生成 |
| SF16 | 新生成 | 新生成 |

共 12 个比较条目，新运行 9 次生成。额外三个原始任务为椅子、会议桌、书架，
在执行前固定，不按成功与否替换。原始 prompt、来源、制造要求及每例尺寸见 input.json。
不使用旧生成源码或渲染作为新生成输入。不使用已有资产改造组冒充新生成组。

复用核验：生产生成、修补、装配、topology、文件工具实现 SHA-256 与历史批次一致；
原始任务、固定装配配置、检测配置和总修补预算一致。复用实际发布 retained，
不选取结果更好的 working；两例未验证也保留。具体路径和哈希记录在 paired_plan.json。

限制：历史组曾因 API 中断续跑，单次续跑重置 Critic 历史；SDK max_retries 曾从 0 调为 3，
实验入口后来改用代码盘 SQLite 并强化归档容错。因此这是含历史复用的初步 w/wo 对比，
不是完全同期、相同执行轨迹的严格消融；不能把单次差异全部归因于 checker。

## 冻结条件与评价

- HEAD：8cc8e4c31165399bbeb50a7c8268583aef40eb21；含工作区现有修复，真实源码快照/哈希另存。
- 两组：CLIProxy gpt-5.6-sol，同一配置；5 轮评价上限（初稿 + 最多 4 次源码修补）。
- API 中断不计有效修补；调用成本和旧记录保留，SDK 有限重试 3 次，不自动无限重跑。
- 两组都使用 visual_only / FixedAssembly / TabSlot、Image/Code Critic。
  w 仅增加 assembly_topology；wo 生成期间无物理反馈。其余四项物理 checker 关闭。
- 1 scene unit = 1 mm，单侧 fit_offset_mm=0.2；每例两组尺寸一致并冻结。
- CPU CYCLES，512×512，32 samples；现有执行上限 300 秒、topology 外部限时 900 秒不变。
- 各例串行、独立进程；个例错误记录后继续。共同导出/输入契约错误才停批。
- 所有组生成/选择结束后，固定各自 retained，再统一离线 topology 与 Image 评价。
  Image 使用原始 prompt 和最终图，不给组别、topology、旧 Critic 意见或源码。
  wo 离线结果不送回编辑、不据此回选资产；评价失败明确记录，分母仍为每组 6 例。
- Image 是模型审核指标，不是真实人工外观真值；topology 不代表承载、实物固定或制造成功。

## 实现与启动

新增薄入口 experiments/fixed_assembly_prompt/run_paired.py，复用原生成及检测；
run.py 仅增加可选每例尺寸表。未改生产 agent 循环或几何实现。

无真实 API/几何的测试：

```sh
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q tests/test_fixed_assembly_paired.py tests/test_fixed_assembly_six.py
```

结果：16 passed。覆盖组间输入、retained 选择、历史不重跑、离线隔离、失败继续与独立 Image 评价。
真实 API 预检：OK，3.73 秒，312 tokens；不代表后续不会再有上游错误。

输出根目录：temp/assembly_topology_paired_20260921T111000Z/（真实代码盘）。
代理复用独立 adsl_cliproxy_20260919，不创建/关闭代理；不操作其他 tmux。

```sh
bash experiments/tmux_session.sh adsl_assembly_paired_20260921T111000Z /vepfs_default/chanxueyan/lhp/lms/aDSL bash /vepfs_default/chanxueyan/lhp/lms/aDSL/temp/assembly_topology_paired_20260921T111000Z/launch.sh
```

已提交并确认：tmux `adsl_assembly_paired_20260921T111000Z`，pane_dead=0，
首例 SF07_wo/generate RUNNING，真实 API 001 stage=plan，source_empty_before_call=true；
11:10:24 UTC 开始 Planner 请求。未等待完成，不持续监督。
窗口保留交互 shell；console.log 与 jobs/*/*.log 保存进度。
最终读取 case_results.csv / metrics.json；详细测量与独立看图结果在 offline/。

### 用户回查：已暂停，不是全部完成

- 首例 SF07/wo 约 240 秒后结束，初始生成一次，源码修补零次。其余 8 个新生成任务及统一离线评价尚未启动。
- STL 序列化比较通过；top_plate、pedestal 的独立/总装/拆分 GLB 三角面对应比较分别报
  4.7658256848、8.0156097709 mm，`EXPORTED_FILE_GEOMETRY_MISMATCH`。
  这是导出检查报告的偏差，尚未区分真实导出改变与比较方法误判，不等于已经证明模型几何偏移。
  run_paired.py 根据该错误暂停整批；不是 topology 拒绝（本组不调用 topology）。
- Image 已返回拒绝（表面分层/粗糙）。Code Critic 后续请求发生实际 HTTP 503：
  `auth_unavailable`，内含经 127.0.0.1:7892 出站 TLS 握手被重置。
  当前审核捕获异常写为 FLOW_ERROR；不是本轮 Python 类型错误，也不是已证实额度不足。
  有限重试没有救回本次调用。已知 41686 tokens，另一次失败调用用量未知。
- 批次脚本正常 return，tmux 的 task_exit=0 只表示进程返回零，不表示实验成功；以 paused.json 为准。
- 按用户要求关闭 5 个 aDSL 实验空会话（含本批），均现场确认仅 bash、task_exit=0、无子进程。
  保留 adsl_cliproxy_20260919、mosalloc、rec、s1、s2；没有删除模型、图片、日志或诊断。
  本次仅诊断/清理，未修改生产代码、未重新调用 API、未重启实验。

### 已授权修复与续跑

- 已定位导出误判：两件 STL 与 GLB 顶点集合完全相同，GLB 缺少的独有三角面全为零面积面；
  同时去掉了重复面。原检查把编码数量不同当成几何不一致。现在仅在序列化比较的临时数组中
  按精确零面积/重复几何规范化，保留原始网格，报告两侧计数；微小但非零的面不删除，原容差不变。
  topology、CSG、模型源码与材料导出未改，网格退化不因此变成 topology PASS。
- 同一 SF07 源码在新目录真实重新导出：8 项一致性检查 PASS、比较偏差均为 0，源码 SHA-256 未变。
  证据：`temp/SF07_export_serialization_recheck_20260921T113500Z/verification.json`。
- CLIProxy SDK max_retries 从 3 增到 6（最多 7 次请求）；没有增加候选预算或外层无限重试。
  按 OpenAI Docs 与本地 openai 2.54.0 核对现有有限退避重试，
  [官方错误恢复说明](https://developers.openai.com/api/docs/guides/error-codes)。
  实际 HTTP 503 尚不能宣称根治；最新真实小请求成功，3.56 秒、312 tokens。
- 验证：重试 Mock HTTP 4 项通过；导出/visual-only 16 项通过（含真实 Blender 导出）；
  配对/续跑/原生成相关 63 项通过。覆盖第 7 次成功、持续 503 封顶、400 不重试，
  零面积/重复面不造成序列化误报、真实面缺失/位移仍拒绝，以及续跑不重生成/不重置已用编辑预算。
- 新目录 `temp/assembly_topology_paired_resume_20260921T114000Z/`，原目录不可变。
  SF07/wo 从已生成的相同源码、相同 plan 进入原有 resume；此前 0 次编辑，仍最多 4 次。
  本次不再调用该例 Planner/初始 Coder，初稿检查会重新执行；旧 API 成本并入该例汇总。
  其余 8 次新生成照常；原 3 个 w 历史资产仍保留。新的导出比较代码差异与重试修订
  显式记录在 paired_plan.json 的 amendments，不能再称新旧代码逐字相同。
- 同一模型、尺度、余量、打印/使用假设、阈值和修补上限不变；对照仍不接收 topology。
  没有修改代理、系统环境或模型源码，也没有额外启用任何物理 checker。
- 已启动并核实：`adsl_assembly_paired_resume_20260921T114000Z`，pane_dead=0、task_exit=running；
  SF07_wo 的 started.json route=ObjectWorkflow.resume、max_rounds=5/max_repairs=4，
  初始源码来自旧本轮 SF07，未新启动 Planner/初始 Coder。只确认启动，不持续监督。

### SF02 停批诊断（下节记录后续代码修复）

114000Z 续跑于 12:22 UTC 再次暂停：SF07/wo 三次修补后生成审核通过，
SF03/wo、SF13/wo 初稿通过；SF02/wo 四次修补已完成，但最后 Code Critic 请求
遇 HTTP 503 / server_is_overloaded。SF02/w、SF10 两组、SF16 两组尚未开始，统一离线评价未执行。

SF02 仅初稿的 backrest_panel.stl 报一致性错误，后四候选导出均 PASS。
只读复核：STL 和 GLB 各126面、包围盒一致、双向顶点最近距离最大1.9073486328125e-6mm；
STL落地平移为Z+5.5mm，一张GLB面积约1.4066696166992188e-5mm²的极薄面，
经float32保存和恢复后成为零面积。两边分别排除零面积面后留下119/120面，
错误对应导致12.2976189921mm的比较值。并非已证实模型位移12mm，旧SF07修复未覆盖此精度边界。

另一个控制流问题：run.py汇总所有轮次导出失败，run_paired.py因此被初稿旧错误暂停，
没有区分后续候选已经导出成功。用户要求先推送讨论，本次只记录诊断，
**当时尚未实现修复，未改模型、阈值，也未再次启动实验。**

### 逐面比较退出生成强制验收（本地代码，未重跑）

基线仍为 `8cc8e4c31165399bbeb50a7c8268583aef40eb21`，保留已有工作区修改。
按用户新要求做最小调整，不再扩大三角面匹配算法：

- `export_assembly.py`：visual_only 的正式导出仅检查文件读取、打印件 ID/齐全性、
  有限且非空的网格、恢复局部毫米坐标后的包围盒、GLB 装配/拆分变换；沿用原长度容差。
  逐面比较仅在测试显式指定 `compare_triangles=True` 时运行；正式 manifest 标
  `triangle_comparison=NOT_EXECUTED`，基础检查不声称精确表面等价。
  最终网格复用、颜色/材质与单位处理不变；旧 geometry 模式的实体差集比较未改。
- `run.py` / `run_paired.py`：批次导出门槛读取当前 working 的版本绑定报告，
  不再用所有历史失败的并集阻断后续正常版本；历史失败仍保留，retained 不变。
  对旧记录只识别含 `triangle_coordinate_deviation_mm` 的逐面错误为非阻断；
  真实缺文件/部件、尺度/摆放错误及未明确分类的 EXPORTED 错误仍报告并阻断。
- 未修改 Image/Code Critic、assembly_topology、候选预算、发布逻辑或生成源码。
  未改旧实验结果、未请求 API、未恢复批次；SF02 最终审核的 HTTP503 仍是独立未解决问题。

针对性测试 **39 passed in 6.58s**：同表面不同三角化不再触发导出拒绝；显式逐面回归仍有效；
缺文件、缺部件、错误尺度/摆放仍失败；旧错误不污染当前版本；Topology 失败仍到达修补，
失败候选不覆盖 retained。测试只使用模拟流程及小网格序列化，不重新生成已有资产。

实际执行命令：

```sh
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q \
  tests/test_fixed_assembly_visual_only.py tests/test_fixed_assembly_exports.py \
  tests/test_fixed_assembly_prompt.py::test_candidate_geometry_rejection_does_not_imply_shared_failure \
  tests/test_fixed_assembly_prompt.py::test_current_report_not_historical_export_error_controls_batch \
  tests/test_fixed_assembly_prompt.py::test_saved_sf07_geometry_pause_can_continue_without_replay_or_result_overwrite \
  tests/test_fixed_assembly_prompt.py::test_saved_pause_cannot_bypass_shared_error_or_missing_evidence \
  tests/test_fixed_assembly_paired.py::test_batch_uses_latest_export_not_rejected_history \
  tests/test_fixed_assembly_paired.py::test_generation_finishes_before_offline_and_reused_not_regenerated \
  tests/test_assembly_topology.py::test_batched_engineer_source_fallback_one_budget_and_publication \
  tests/test_assembly_topology.py::test_rejected_candidate_cannot_publish_its_checker_result
```

## Material Passport

- 类型：实验设计、复用审计、提交记录；测试结果已核验，批次效果待运行。
- 来源：本地原始 task manifest、历史请求/版本账本、当前代码哈希；全部路径在冻结计划。
- 研究技能影响：先核对复用可比性、保留失败与历史差异，不把修改前后冒充 w/wo。
- 验证边界：无几何阈值调整、无手工修改模型、无新增 checker、无成功率结论。
