> 以下为首轮历史结果；2026-10-06 增量调整见 [selection_v1_adjustment_results.md](selection_v1_adjustment_results.md)。

# 第一轮 benchmark 筛选实施记录（2026-10-06）

实现分支：`feat/benchmark-selection-v1`；代码提交：`f82a8c9bbe4b0d4b6f6a967a9e03e1dc235efd02`。生产代码基线：`master@2a11b196fb7c09a72747fd8f6977b433f0428bf9`。在隔离 worktree 实施，保留主工作区已有未提交修改。只新增 benchmark 工具、配置、协议和定向测试；没有修改生产 Agent / mesh / physics 判定，没有运行 FEA 或 Planner/Coder。

## 实际取得的材料

- ABO：官方模型索引与 16 个官方商品元数据分片，固定 seed 20261006，抽取灯具 7、椅凳 6、桌子 6、柜架 6。25 个原始 GLB 全部取得，合计 468,680,344 字节；原文件与校验值保留。官方 catalog 缩略图一并保存。
- Toys4K：官方 README 要求申请资产压缩包；本机没有该包，也没有可直接使用的原始模型。复用已有 Toys4k.csv 的 25 个候选标识、类别与预期校验值；该缓存索引的原始发布版本未知，模型校验未验证。没有采用第三方网格补足候选。
- 50 行候选清单：ABO 25 + Toys4K 25。Toys4K 的 mesh/image/use_pose 为 null、needs_review，不表示下载或预览成功。
- 25 个 ABO 均有保留材质和中性材质两组审核八视图，以及独立输入图。150 mm 最长边，512² 透明 PNG，CPU Cycles 32 samples。400 张审核图的逐帧相机、投影与图片校验值核对通过；25 张输入副本另计。独立图片无文字，联系表有编号和类别。

## 实测与选择解释

21 个 ABO 原始参考有开放边、非流形或退化面等未能通过现有保持形状处理的缺陷，不能可靠求实体体积，保留预览待人工复核。没有补洞、加厚或删部件来强行过关。

另 4 个闭合参考完成整件 Dapper 的 24 朝向测量；3 个连接实体完成整体自由站立仿真。两个壁挂架虽然在地面诊断姿态下仿真 PASS，官方商品描述明确依赖墙面安装，因此不推荐用于独立站立开发评价。桌子有两个分离材料岛，未人为绑定为一个刚体，站立为 INDETERMINATE。剩余椅子是本轮唯一推荐，仍需用户确认，不是冻结测试集。

所有 grouping_tradeoff 都是视觉机会标签；本轮没有拆合候选，也没有量化分组收益。整件 G 是竖直空隙代理量，不能当作切片器支撑耗材。r_vox=.1 的粗网格可能掩盖小细节。站立采用统一密度与已有 rigid_flex、5 s、25° 协议，测的是整体自由站立，没有验证 connector 保持力。两个壁挂样本的 PASS 必须按“地面诊断测量”解读。


| Source ID | 类别 | h (mm) | V_ref | 最优 G | G (mm³) | O (N=1) | Standing | 推荐解释 |
|---|---|---:|---:|---:|---:|---:|---|---|
| B07BWJCZJW | chair_stool | 12.720995 | 676 | 115 | 236734.264 | 561.000 | PASS | 推荐，待用户确认 |
| B07GFWF2GM | table | 7.605687 | 1072 | 126 | 55435.234 | 946.000 | INDETERMINATE | 2 个分离材料岛，站立未知 |
| B088HDFTSS | cabinet_shelf | 1.775318 | 36839 | 36 | 201.433 | 36803.000 | PASS | 壁挂搁板，站立使用不适用 |
| B07HSLG6XK | cabinet_shelf | 2.934481 | 6916 | 0 | 0.000 | 6916.000 | PASS | 壁挂镜面搁板，站立使用不适用 |

## 验证

```sh
ADSL_TEST_BENCHMARK_REAL=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q -p no:cacheprovider benchmark/tests/test_selection.py tests/test_partition_score.py
python benchmark/scripts/smoke.py --config benchmark/configs/selection_v1.json
```

定向 pytest：15 passed，0 failed，0 skipped，包含 3 次真实 Blender 导入。独立原生 smoke：7 项通过；稳定箱体 PASS / 峰值倾角 0°，倾倒夹具 FAIL / 74.1214°；实心箱体体素 8、G=0、24 朝向；缓存复用、源/配置变化失效、坏文件隔离均验证。Toys4K 原生导入/预览/测量因无模型未运行，不计为通过。

25 个 ABO 全批预览最终进程退出 0。最初一次在产物写出后退出 139，原因是父进程 fingerprint 的模块查询意外导入 bpy；改为读取源码路径，Blender 仅在子进程运行，已完成全批重跑。checker 的重测保留独立 runs/ 目录，不覆盖历史证据。推荐流程另外验证：壁挂物体即使数值测量 PASS 也不能自动推荐。

## VLM 与证据限制

使用已约定的 StepCode gpt-5.6-sol 配置；25 次尝试，24 次成功，1 次连接不可用，没有追加调用或换 token。自动四标签与人工适用性复核分别保存；user_confirmed_dev 数量为 0。

首批部分 VLM 图片未在调用前复制到专用目录；重渲染后只归档了校验值完全匹配的历史 PNG。175 张历史输入字节可追溯，50 张只有调用时校验值、原始字节缺失（含首次连接失败的 9 张）。19 例完整、1 例部分、5 例未取得原字节。`vlm_conditioning_archive.json` 与各例 `conditioning_archive.json` 明确列出缺失；当前重渲染图片不冒充这些历史输入。实际 system prompt、结构化输出、模型元数据与 usage 均保留。后续工具已改为调用前快照输入。

## 入口

完整原始/派生资产与日志：`/jiigan-hp/lms/aDSL/benchmark/selection_v1/`。

- `review/README.md`：联系表和全部候选入口。
- `review/summary.md` / `summary.json`：实际数量、分布与推荐理由。
- `manifests/candidates.jsonl`：完整来源字段；CSV 方便审核。
- `manifests/recommended_dev20.jsonl`：文件名遵循计划，行数以实际结果为准，不足 20 不补齐。
- `measurements/<case>/reference_measurement.json`：当前结果入口；`runs/` 下旧报告是历史诊断，不能混作当前结果。
- `derived/<case>/use_pose.json`：源文件与标准化变换、节点来源、自动处理操作。
- `selection_v1_review.zip`：轻量预览/测量/来源审核包，不含大型 raw GLB、NPZ/STL；完整 mesh 仍在持久目录。ZIP 内清单的 reference_mesh 相对路径指向完整数据根目录，不表示网格在审核 ZIP 内。

开发集还没有冻结；本轮缺 Toys4K 原始归档，且 ABO 多数仅适合外观复核，无法交付 ABO10 + Toys10。停止在现有可用清单，未扩充样本或开始生成对比。

## 开销记录

记录中的最新导入/预览子进程累计 1748.3 s（并行执行，不能等同于实际墙钟时间，也不包含之前被覆盖的预览重跑日志）；25 次 VLM 尝试累计 1050.7 s，成功请求 usage 共 72390 输入 token + 8037 输出 token。模型凭据没有进入日志或审核包。checker 仿真时间在当前独立报告里。

推荐 source_id：`B07BWJCZJW`（椅子/凳子类），四个自动标签均有；曲面扶手和椅面、窄支撑腿以及相接的可见部件提供初筛依据，整件测量通过。人工尚未确认。
