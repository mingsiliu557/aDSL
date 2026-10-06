# Benchmark 筛选 v1：按可见结构风险推荐

日期：2026-10-06。状态：调整完成，开发推荐待用户确认。实现提交 `aecc493745e08ef257838e7347f37f21963c5210`，分支 `feat/benchmark-selection-v1`；原实现基线 `736d8d2e8d7843e054d9c6be229169b5b940fffd`。本轮不合并 master 或推送。

本轮将 GT 从推荐的物理门槛改为参考图来源。推荐仅依据来源身份、清楚可辨的输入、明确使用场景/姿态/任务、具体结构风险及类别覆盖。GT 开放边、多材料岛、无法计算体积、未测量或 standing FAIL 均不排除案例。更新后的 20 例全部可用于外观和打印任务，18 例具独立站立任务，另 2 例为壁挂/飞行场景。任务适用不等于生成模型通过检查。

## 修改文件

- `benchmark/scripts/build_review_pack.py`：解除实体、连通性、GT 测量和 standing PASS 对推荐的限制；`task_applicability` 表示任务适用性，兼容字段 `metric_eligibility` 同义保留。GT 测量独立写入 optional diagnostic，区分 PASS/FAIL/INDETERMINATE/NOT_RUN/STALE。
- 同文件：按 standing_sensitive / grouping_tradeoff 的可见风险与类别轮转推荐，优先覆盖不同类别，每来源保留一个较普通的对照；每例必须有风险/对照理由。不读取 G、Dapper 分数或任何方法的成败记录。默认不创建物理短名单；仅显式 `--shortlist-only` / `--measure` 可触发诊断准备/测量。
- 同文件：允许无 VLM 的直接预览确认；辅助 `selection_review` 必须绑定当前输入和已看图片的 SHA。人类字段优先，包括旧 pose 字段别名；辅助审阅不改为 user_confirmed。
- `benchmark/scripts/preflight_candidates.py`：风险提示要求考虑打印朝向、细支撑/偏置上部与多向突出，禁止默认四标签。新标签合约与旧标签缓存区分；可选 GT 诊断按实际测量时冻结的请求、几何、物理配置、代码及产物哈希核验，不因新标签提示改动而丢掉仍可验证的诊断。
- `benchmark/tests/test_selection.py`、`benchmark/tests/test_preflight_cache.py`：定向验证推荐、任务、诊断、普通对照、来源/图像校验、人工优先、缓存及默认不调用 GT checker。
- `benchmark/selection_protocol.md`：同步默认筛选与可选诊断协议，记录后续冻结生成评测的边界。checker 实现、生产 Agent 流程、Dapper 公式、体素参数与物理阈值均未改。

## 实际产物与统计

| 项目 | ABO | Toys4K |
|---|---:|---:|
| 元数据候选 | 30 | 25 |
| 当前源身份有效 | 29 | 25 |
| 已有预览成功 | 29 | 23 |
| 输入审阅后外观任务适用 | 27 | 23 |
| 打印/overhang 任务适用 | 26 | 23 |
| 独立站立任务适用 | 24 | 21 |
| 开发推荐 | 10 | 10 |
| 推荐中外观/打印任务 | 10 | 10 |
| 推荐中站立任务 | 9 | 9 |
| 普通对照 | 1 | 1 |
| 用户最终确认 | 0 | 0 |

全部 20 例有具体一句风险或对照理由，均为 recommended_dev / 待用户确认。原有下载、单独 PNG、原生及中性八视图全部复用，只重新生成清单、Markdown 和两来源联系表；未重跑三维渲染。

## 20 例开发推荐

A=外观，P=打印/overhang/分件取舍，S=独立站立。用途依据参考图任务，而非 GT checker。详细姿态观察、图片 SHA 和四个粗标签子集保存在 JSONL。

| 来源 | source_id | 类别 | 任务 | 一句具体风险 / 普通对照理由 |
|---|---|---|---|---|
| ABO | B076V626RZ | 灯具 | A/P/S | 弯曲细灯杆承托偏置灯头；整件换向时杆、开口灯罩与底座的打印支撑需求可能冲突。 |
| ABO | B075X4YDN6 | 椅/凳 | A/P/S | 较大的曲面靠背和座体经中央立柱承托在细放射脚上；脚架尺寸影响站立，座体与脚架分开可能减少打印方向冲突。 |
| ABO | B07F3Y9BSC | 桌 | A/P/S | 半圆长桌面与下层板由多根弯曲细腿连接；长宽比例和前后腿位置影响站立，层板与腿分开存在打印朝向权衡。 |
| ABO | B07S6WNZ7Y | 柜/架 | A/P/S | 高而浅的柜体仅靠两侧长支板落地，较大的上部与薄接地范围对比例敏感；上柜、层板与长侧板分组可能改变打印支撑。 |
| ABO | B075X2XZDD | 灯具 | A/P/S | 高而细的立杆配较小圆底和横向伸出的灯头，对生成比例及自由站立敏感，分开灯头与杆可能改变打印朝向取舍。 |
| ABO | B0814T6G5M | 椅/凳 | A/P/S | 外扩曲面象耳靠背、向下伸出的背部和细腿朝不同方向延伸，主体比例影响站立，整体旋转不能直接消除所有局部支撑取舍。 |
| ABO | B072ZK887F | 桌 | A/P/S | 圆桌面和悬挂圆篮由多根细长杆连接，细杆支撑范围与上部比例影响站立，圆篮与杆分开可能改变打印方向。 |
| ABO | B088HDFTSS | 柜/架 | A/P | 薄三角墙角层板和边缘装饰为规则结构，可比较平放整件与分开装饰的件数取舍；壁挂使用不适用自由站立。 |
| ABO | B075X2LKNZ | 灯具 | A/P/S | 宽圆锥灯罩位于窄矩形灯体之上，主体比例影响支撑范围，灯罩与底部的打印姿态存在不同选择。 |
| ABO | B082JGPBLQ | 柜/架 | A/P/S | 【普通对照】普通对照：宽底箱形柜体和两抽屉的布局较规则，用于观察方法是否在低结构风险案例上保持完整形状。 |
| Toys4K | cat_057 | cat | A/P/S | 直立躯干落在两只小脚上，外伸手臂和后伸长尾分布于不同方向；应比较整件旋转与躯干/突出部位分组，而非直接把使用姿态悬空视为支撑需求。 |
| Toys4K | dinosaur_020 | dinosaur | A/P/S | 头部前伸与细长尾部后伸形成双向布局，双脚支撑较小；侧置整件与拆开头尾/躯干存在朝向和件数取舍，风险不是已测量失败。 |
| Toys4K | dog_053 | dog | A/P/S | 大而前突的头身由细腿和小脚支承，弯耳向左右伸展；单一打印朝向未必同时利于耳、口鼻和身体，值得比较局部分组。 |
| Toys4K | giraffe_005 | giraffe | A/P/S | 长颈与较高躯干位于细长四腿之上，头部前置；平放可能改善打印支撑却改变突出部位方向，腿/颈/躯干的拆合收益需比较。 |
| Toys4K | monkey_005 | monkey | A/P/S | 较大的躯干、头和帽集中在极细双腿上，口鼻前突而手臂/尾部向后；站立接触敏感，整件旋转和局部分组的打印收益需验证。 |
| Toys4K | robot_050 | robot | A/P/S | 大球头位于小身体与小脚之上，手臂和天线方向不同；头身连接及拆合会影响打印支撑/件数，是否站稳不能由外观或GT网格判定。 |
| Toys4K | lion_013 | lion | A/P/S | 巨大头和鬃毛高于小坐姿躯干，水平尾和两侧脚形成多向突出；坐姿接触及局部分组有风险，但未测量站立或支撑收益。 |
| Toys4K | cow_012 | cow | A/P/S | 宽厚躯干跨在四条较细腿上，头、角和腹部细节有不同突出方向；倒置或侧置可能减少部分空隙，分组是否有收益仍需测量。 |
| Toys4K | dragon_007 | dragon | A/P | 展翼、下垂腿和长尾朝多个方向突出，旋转整件与分离翼/躯干可能改变支撑和件数；图中飞行姿态的独立站立不适用。 |
| Toys4K | bunny_004 | bunny | A/P/S | 【普通对照】作为普通对照保留低矮坐姿和较宽后躯，主要需还原长耳与前肢；先搜索整件打印朝向，不预设必须拆分或具有站立失败。 |

本次真实清单中 17 个推荐没有可靠 GT 材料实体，17 个推荐没有可验证的当前 GT 物理报告，仍正常入选。`ABO_B088HDFTSS` 为墙角壁挂搁板、`Toys4K_dragon_007` 为飞行姿态：standing NOT_APPLICABLE，A/P 适用。Toys 覆盖 10 个不同类别；ABO 覆盖灯具、椅/凳、桌与柜/架。

## GT 测量仅作历史辅助诊断

本轮新增 GT 仿真/测量 **0**，新增 VLM/API 调用 **0**，新增下载 **0**。VLM 历史累计仍为 47 次尝试 / 43 次成功。此次风险初阅由 Coding Agent 看现有输入与中性图完成，保存实际已看图片与 SHA；不是人类确认。旧 VLM 输出保留在历史证据中，不当作新提示下的标签。

当前仍能校验的 GT 诊断有 10 个 overhang PASS（两来源各 5），4 个 standing PASS（均 ABO，其中 2 个是壁挂物的地面诊断）；另有 1 份 ABO 历史失效报告。standing 诊断计数由旧“自然使用合格”2改为“全部有效诊断”4，是统计范围变化，不是新增站立通过。GT 结果不进入推荐排序，也不能证明生成物体站稳或分件收益。

开放边/退化面等不自动解释为可见外观缺失；参考图完整可辨时，生成方法可以构造自己的有效模型。源 SHA 冲突、缺输入或真正不可辨的目标仍待复核：`ABO_B07QCQ1J7M` 原源文件冲突保留，两个范围外梯子继续 excluded；Toys dinosaur_055 渲染失败、dinosaur_059 导入失败未冒充物理失败。

## 实际验证

```sh
OPENBLAS_NUM_THREADS=1 /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q -p no:cacheprovider benchmark/tests
```

结果：**62 passed，2 skipped**。跳过的是既有 opt-in 原生几何用例，本轮未改导入、几何内核或 checker，没有将历史原生通过数计为本次执行。

定向用例覆盖清楚输入+开放/多岛 GT、无任何 GT 测量、GT standing FAIL、壁挂/飞行仅站立不适用、无 VLM 的直接人工确认、图像/源 SHA 失效、具体风险与普通对照/类别覆盖、GT 诊断与任务独立统计、默认不运行 GT checker，以及诊断配置/几何变化仍使缓存失效。

实际审核整合首次发现部分联系表审阅未绑定独立输入：补阅有效壁挂样本并保存真实图片路径，两个范围外梯子保持未确认；没有伪造已查看图片。清单/统计更新前备份到 `history/structural_risk_20261006T155331Z/`。重建期间中断过一份中间审核包，最终包以最后完整重建及校验记录为准。

## 输出位置与待确认

数据根：`/jiigan-hp/lms/aDSL/benchmark/selection_v1/`。

- [全部候选 JSONL](/jiigan-hp/lms/aDSL/benchmark/selection_v1/manifests/candidates.jsonl) / [CSV](/jiigan-hp/lms/aDSL/benchmark/selection_v1/manifests/candidates.csv)
- [20 例开发推荐](/jiigan-hp/lms/aDSL/benchmark/selection_v1/manifests/recommended_dev20.jsonl)
- [ABO 联系图](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/ABO_contactsheet.jpg) / [Toys4K 联系图](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/Toys4K_contactsheet.jpg)
- [当前统计](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/summary.json) / [逐例审核](/jiigan-hp/lms/aDSL/benchmark/selection_v1/review/README.md)
- 实际辅助审阅：`review/structural_risk_20261006/abo_visual_review.json`、`toys_visual_review.json`；整合与最终验证：`logs/structural_risk_20261006/`。旧 `closeout.*` 与早期报告只作历史，不作为本轮数量依据。

仍待用户确认 20 例、输入完整性和使用姿态（特别是双足/坐姿玩具、壁挂/飞行任务定义，以及 B07F3Y9BSC 的历史附属组件疑点）。风险是候选假设，分组实测为 0；不保证我们的方法会改善。正式名单先冻结，再做方法对比，选样不能用“aDSL 已失败”或“我们已成功”。

后续生成评测复用现有 topology 检查输出网格、连通性与装配有效性；整体 baseline 无接口时，接口专项为不适用。生成失败、无效网格或物理不可测均保留在冻结案例分母中。本轮只写明该协议，不运行生成评测、不推断 GT 连接图。跨方法共同尺度、体素设置与 Dapper 参考量另定；现有 150mm 与历史诊断参数不在本轮变成正式比较协议，也不改公式。
