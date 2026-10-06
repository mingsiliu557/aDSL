# 筛选 v1 增量调整（阶段性结果）

记录时间：2026-10-06T08:30:49.585709+00:00。实现位于 `feat/benchmark-selection-v1`，基线 `d8842287e8f2a126879949c68bb47959328c684b`；实际生产模块来自 `master@2a11b196fb7c09a72747fd8f6977b433f0428bf9`，其代码/版本逐项保存在缓存描述符中。保留主工作区已有修改，仅调整 benchmark 工具。本报告是此时快照；Toys 归档下载与后续原生批次尚未完成，不能据此宣布 Toys 接入验收或 20 例推荐完成。

## 实现

- 分类按具体类型、英文标题、末级目录；两条 LADDER 保留原 ID/SHA 并排除范围，B082JGPBLQ 改 cabinet_shelf。HOME_MIRROR/Cart 冲突待复核，人工确认/排除状态保留。首批补 5 个 ABO，现 55 条候选，无重复 ID。
- 固定 Toys ZIP 镜像、revision、大小与 SHA；续传 `.part`，完整校验后才发布，按 CSV 匹配成员并核对单文件 SHA，保留资源层级及 provenance。小 ZIP/缺成员/坏 SHA/空间与路径约束已验证，真实归档仍在下载。
- 预览、测量、VLM 使用独立依赖和 key。旧记录未覆盖；13 份预览、4 份测量经完整 provenance 等价核对迁移，其余只重跑相应阶段。25 份旧 VLM 全部与当前图不匹配，只作历史。
- 当前 VLM 按九张实际图、文本、模型参数、prompt/schema 缓存；失败同 key 不重试，满额仍可读缓存，损坏输入记录不覆写。CLI 限 workers=1，增加跨进程锁保证后续全局串行/额度原子性。
- 外观/过悬/站立分别列资格、原因、测量状态和推荐用途；有效站立 FAIL、执行未知、诊断 PASS 区分。shortlist 从当前源/预检哈希重算，不信任旧派生旗标，最多 24 个可靠实体、每源先 12 个。没有运行 FEA、Planner/Coder 或拆合候选。

## 已运行验证

```sh
python -m pytest -q -p no:cacheprovider benchmark/tests
ADSL_TEST_BENCHMARK_REAL=1 OPENBLAS_NUM_THREADS=1 python -m pytest -q -p no:cacheprovider benchmark/tests tests/test_partition_score.py
python benchmark/scripts/smoke.py --config <独立 smoke-config.json>
```

定向：52 passed、2 skipped；启用原生并含既有 Dapper 回归：63 passed、0 skipped。原生 smoke 7 项通过：稳定 PASS/0°，倾倒 FAIL/74.1214°（预期反例），实心 8 体素/G=0/24 朝向、缓存、坏文件隔离。首次 smoke 因独立目录缺 raw 而执行失败，补 mkdir 后同目录重跑通过；失败日志保留，不计通过。

当前 ZIP CRC 与不包含 GLB/BLEND/NPZ/STL/ZIP 的检查通过。ABO 原生/中性两组逐帧相机已由 worker 核对；Toys 的真实 `.blend`、材质/资源、八视图、物理 smoke 均待归档完成，本次未计通过。

## 当前真实数量

| 来源 | 元数据 | 当前源身份/预览 | 可靠实体 | 当前过悬有效 | 自由站立有效 PASS | 推荐 |
|---|---:|---:|---:|---:|---:|---:|
| ABO | 30 | 29 | 5 | 5 | 2 | 10 |
| Toys4K | 25 | 0 | 0 | 0 | 0 | 0 |

ABO 推荐是用途并集，10 个可做外观、4 个也可过悬、2 个也可自由站立。它们仍待用户确认。壁挂两例的原地面 PASS 只算诊断，不算独立站立。六份测量记录存在，当前可复用五份；另一个历史未知记录单列，未计有效。ABO_B07QCQ1J7M 原 GLB 的当前 SHA 与历史记录不符，原因未知，保留原文件/历史证据并停止其资格，未重新认定来源。

新增 VLM 12 次均成功，累计 37 次（36 成功、1 次历史连接失败），预算上限 50。补充椅子调用与首批最后一条曾有短暂重叠，记录未改写；已补全局进程锁并验证两进程不能突破共用额度。后续 Toys 批次全局串行。自动标签/人工复核分开；分组实测数量 0，没有将 G 或分组标签说成已改善收益。

## 后台批次及结果入口

持久根目录：`/jiigan-hp/lms/aDSL/benchmark/selection_v1`。

后台 tmux：`adsl_selection_v1_20261006_adjustment`。下载 Python/aria2 继续运行；归档完整 size/SHA 验证后，独立有限批次自动执行 cat_057/cow_012 资源盘点及原生八视图 smoke，再处理现有 25 个 Toys、最多 50 次累计 VLM、24 个可靠实体参考测量，重建审核包。两例真实 smoke 不通过则停止并记录原因，不批量冒充成功；每例失败独立保存。未扩到 100 例。

- 最新批次状态：`logs/revision_20261006/remaining_batch_status.json`。
- 原始后续日志/命令：`logs/revision_20261006/remaining_batch.stdout.log`、`remaining_batch.stderr.log`、`run_remaining.py`。
- 归档下载/布局证据：`logs/toys_archive/`；layout 仅证明成员匹配，不能证明完整校验。
- 当前清单/推荐：`manifests/candidates.jsonl`、`recommended_dev20.jsonl`。
- 当前审核：`review/README.md`、`summary.md/json`、两来源联系表；`selection_v1_review.zip`（此时 232,761,378 字节，后续批次完成会更新）。
- 原测量/预览保留；新入口 `preflight_v2.json`、`reference_measurement_v2.json`。
- 完整历史快照：`history/adjustment_20261006`；迁移证据 `logs/revision_20261006/legacy_cache_evidence.json`。
- 独立本轮 smoke：`/jiigan-hp/lms/aDSL/benchmark/selection_v1_smoke_20261006_adjustment/selection_v1/logs/`。

当前 ABO 推荐 source_id：B076V626RZ, B07BWJCZJW, B07F3Y9BSC, B07S6WNZ7Y, B075X2XZDD, B07B4LZP9Q, B07QD6V6MP, B088HDFTSS, B07QZ21DDV, B07GFWF2GM。

最终 Toys 推荐数量和有效测量以后台结束后的 summary 为准。生成 prompt 不会包含风险标签、参考网格或参考测量。本轮结果不证明方法相对 baseline 的优势。
