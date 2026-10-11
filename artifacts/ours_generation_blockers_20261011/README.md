# Ours 两例生成失败：Planning 核对材料

日期：2026-10-11。生成代码：`810f713d00058eeec58ff2f226ad48d12aaa2505`。模型：GPT-5.6-Sol。每例原有预算为初始生成一次、最多四次源码修补，共五轮；FEA 关闭。

本目录先收录当前暂停批次中**实际没有可用最终 GLB 的两例生成失败**。它们与[动物分件排查报告](../../reports/animal_partition_diagnosis_20261011.md)中的“合法模型未分件”不是同一种状态，没有作为成功案例加回 benchmark。

本次只整理已有证据，未运行模型、源码、网格或物理实验，未修补任何生成代码，未恢复 ours。按用户最新要求，本轮不处理网络/API 问题，也不修改转换逻辑。Planning agent 可据此核对原因与提出后续计划；材料上传不意味着已修复或已授权扩大算法。

## 1. 两例区别

| 案例 | 来源与目标 | 实际失败路径 | 重点入口 |
| --- | --- | --- | --- |
| [ABO_B07MBFD87N](ABO_B07MBFD87N/) | ABO 桌灯，圆柱灯罩、细颈、变直径车削底座 | original 与 attempt_0002～0004 的制造、Topology、Standing、Overhang 通过，但 float32 显示转换失败；attempt_0001 引入输入几何非法。五轮后没有可用最终 GLB。 | 各轮源码的旋转轮廓、完整 target_precision attempts、Engineering 提议与 Coder 实际改动；转换算法暂不修改。 |
| [Toys4K_deer_moose_057](Toys4K_deer_moose_057/) | Toys4K 鹿，分叉鹿角、四蹄站立 | original 与 attempt_0001 为 loft positions 非严格递增；attempt_0002～0004 为 `Legs.children` 不存在。没有完成几何求值或有效物理测量。 | 全五轮源码、执行 traceback、Code Critic 反馈与 Coder 输入输出，核对修补为何没有消除错误。 |

两例 `stop_reason` 均为 `round_budget_exhausted`；该停止名称不能代替实际失败原因。灯具的显示 FAIL 不应被改写成制造或站立 FAIL；鹿的物理 INDETERMINATE 表示未有效测量，不是物理不合格结论。已有角色记录没有 ERROR 调用，不能仅凭“生成失败”认定是网络/API 服务故障。

## 2. 目录如何阅读

每个 case 的根目录提供 GitHub 可直接查看的材料：

```text
input.png                    原冻结参考图，字节与原输入一致
case.json                    原需求、尺寸、h、输入 SHA，以及非敏感预算信息
plan.json                    初始 Planner 计划
version_summary.json         全五轮版本、失败原因、状态、source SHA 和账本选择
versions/original/source.py
versions/attempt_0001/source.py
versions/attempt_0002/source.py
versions/attempt_0003/source.py
versions/attempt_0004/source.py
evidence.zip                 完整相关诊断与调用记录（解压阅读）
archive_manifest.json        ZIP 哈希、成员清单及每个成员的 SHA256
```

所有 `source.py` 都按原文件字节复制，没有为了复现通过而修改几何、参数或公共 API 调用。`retained_version` 和 `working_version` 分别保留，最后一个修补版本不被伪称为成功最终版本。

ZIP 中按版本保存：

- `versions/<version>/geometry_report.json`：完整该轮几何/显示报告，保留逐面证据、全部失败行与修复 attempts；没有摘要截断。
- `versions/<version>/assembly_topology.json`、`assembly_overhang.json`、`assembly_standing.json`：原对应版本 checker 结果。
- `versions/<version>/review_decisions.json` 及实际存在的 `execution_error.json`、`proposal.json`、`engineering_critique.json`、`engineering_input.json`、`decision.json` 等。
- `role_calls/*.json`：原角色 instructions、input、output、tool events 和状态，便于对照“要求改什么”与“实际改什么”。内联图像体被移除，原参考图在 `input.png`。
- `role_call_summary.json`：调用状态概览；`original_evidence_provenance.json`：原数据相对路径和哈希，可在持有原实验资料时核对。

完整大账本包含大量重复诊断，未直接复制进仓库；`version_summary.json` 是带原账本 SHA 的导出索引，各轮完整相关报告在 ZIP 中。它不是原生恢复账本，不应用来恢复实验或重置预算。

## 3. 公开范围与证据身份

公开材料删除服务器绝对路径、访问端点和凭据字段；角色记录里的内联图片改为占位。没有上传 token、模型配置、账户信息或 SQLite session。错误中的模块路径替换为逻辑路径；数值诊断不裁剪，源码与参考图不改动。

因此公开 JSON 的文件哈希与原 JSON 的哈希可能不同；两者分别在 archive manifest 与 provenance 中记录。原 `record_hash` 仅标识原记录，不声称修改路径后的 JSON 仍可通过原缓存校验。不要把公开包直接当作生产运行目录。

建议先查看每例 `version_summary.json`，再打开对应源码与 ZIP 中的具体错误文件。完整逐面诊断供文件/工具按需读取，不应整包塞回 Planner、Engineering 或 Coder 的模型输入。

本次验证仅为文件整理检查：核对输入与源码 SHA、离线 AST 解析（语法检查，不执行）、JSON 可读取性、ZIP 完整性、成员哈希与凭据/路径模式扫描。具体检查结果与归档大小随文件实际生成记录；没有新的几何或物理 PASS 结论。
