# 固定装配补跑：API 与 SQLite I/O 诊断

基线 HEAD：`8cc8e4c31165399bbeb50a7c8268583aef40eb21`，保留工作区已有补丁。
本轮只诊断 API 并修复实验落盘/收尾，不重跑模型、不改 checker 或代理配置。

## API：已确认与尚未确认

- CLIProxy 进程存活，实际错误日志在 LMS 的 `easycliproxyapi/auths/logs/`，
  不是启动器跟随的 `logs/service.log`；未复制含敏感请求头的代理原始日志。
- 当前链路：aDSL → localhost:8317 CLIProxy → localhost:7892 Clash → 上游 Codex。
  Clash 当前仅配置一个可选出口；CLIProxy 日志确认只加载一个账号。
- 07:12:23：上游 TLS 握手 EOF，2.918 秒后返回 HTTP 500。
- 07:13:53：上游 HTTP/2 流 PROTOCOL_ERROR，17.695 秒后返回 HTTP 500。
- 07:14:44：51 秒后请求返回 503 / auth_unavailable，耗时 97 毫秒；日志没有 API REQUEST 段。
- 之后再次有成功请求：07:16:05 的请求耗时约 70 秒，即约 07:14:55 开始。
  本机发行包配置说明写明临时错误的默认冷却为 60 秒，未显式覆盖该值。
  **上述时间线强烈支持“唯一账号暂时冷却 → 无可用账号”的解释**，
  但未取得当时内存冷却状态快照，不将推断写成直接观测。
- 未看到这批错误是 token 上限、checker 超时、请求字段 400 或永久凭据失效的证据。
  应用配置 `max_retries: 0`，首个错误直接进入现有 FLOW_ERROR/TOOL_ERROR 收尾。
- 握手/协议中断究竟来自网络出口、中间链路或上游，现有日志不能唯一归因。
  未重启代理、切换节点、修改凭据或增加无限重试；不能宣称 API 已修好。

## I/O：位置与最小修复

- 新 SF13 已生成源码/资产、保存了拓扑状态；随后 API 失败。
  二次异常发生于 `run_case()` 末尾的 `db.backup(out)`：目的地在
  `/jiigan-hp/.../sessions_snapshot.sqlite3`，导致其后的 `result.json` 没写出来。
- 原始运行数据库位于 `/tmp`，只读 `PRAGMA quick_check` 返回 `ok`，
  文件大小 1,179,648 字节。不是“所有实验文件都丢了”。
- `local_experiment` 是数据盘软链接，不能用它代表代码盘。
  新补跑入口默认使用真实 `aDSL/temp/assembly_topology_api_retry_<时间>/`。
- 新运行会话数据库在 `aDSL/temp/assembly_sessions/`。SQLite 先在代码盘生成并关闭
  完整快照，再以普通文件复制到输出目录；保留代码盘快照用于归档失败恢复。
- 实验汇总先落盘。快照失败单独记 `session_snapshot.status=ERROR` 和失败阶段，
  不替换 API 原因，不改变 approved 或物理状态，不因此阻断后续独立案例。
- 运行结束后再校验并迁移到数据盘；本轮尚未启动新任务，没有移动旧实验资产。

## 验证

- 52 项针对性测试通过：固定装配 prompt 入口、会话存储、快照失败容错。
- 实际代码盘探针 `temp/sqlite_io_probe_sw4vbyli/`：SQLite 创建/提交、备份、普通复制、
  读回均通过，`quick_check=ok`，数据行数正确。
- 未在数据盘反复重做写入以“证明”间歇性 I/O 的底层原因；未声称所有存储故障已消除。
- 未调用真实 API，未生成/修补任何 3D 源码，也未放宽验收。
