# 固定装配四例补跑结果与有限 API 重试

HEAD：`8cc8e4c31165399bbeb50a7c8268583aef40eb21`，保留已有工作区修改。
实验：`temp/assembly_topology_api_retry_20260921T083011Z`。
09-21 08:50 UTC 已结束，tmux 退出码 0 仅表示批处理正常收尾，不表示案例通过。
本报告只核对已有结果、调整下一次运行的请求重试；没有调用真实 API 或重新启动实验。

## 真实错误请求

| 案例 | 调用开始 UTC | 实际阶段 / api_calls 文件 | 错误 / 调用耗时 |
|---|---|---|---|
| new_SF07 | 08:33:24 | code_critic:1 / 003.json | HTTP 500，TLS handshake EOF，1.37 s |
| new_SF03 | 08:50:13 | assembly_repair:4 / 025.json | HTTP 500，TLS handshake EOF，0.66 s |
| new_SF13 | 08:50:23 | image_critic:1 / 001.json | HTTP 500，TLS handshake EOF，2.10 s |

三个错误都与 `easycliproxyapi/auths/logs/main.log` 的上游 TLS 握手 EOF 对应；
日志行 895–896、921–924。未复制含凭据的原始代理请求日志。
SF03 被工作流标为 TOOL_ERROR，但最后候选的工具事件为空、前后源码哈希相同，
根因仍是模型请求 API 500，不是 apply_patch 匹配错误。
不能由这些日志确定是网络节点、中间链路、上游或 TLS 实现中的哪一端导致断开。

## 本轮四例

| 案例 | 本轮新增修补尝试 | 最终 retained / 状态 | 结果与限制 |
|---|---:|---|---|
| 已有 SF13 问题版 | 0 | original / approved=true, topology PASS | 沿用此前已修补资产；6 件连续、5 接口配对及 Image 通过，不是本轮新修好 |
| 新 SF07 | 0 | original / INDETERMINATE | stem_unit 开口；两接口依赖未验证；Code API 500 中断 |
| 新 SF03 | 3 | original / FAIL | 前两次实际改源码并复查；座面因零面积三角面不可测、靠背仍开口；第三次 Coder API 500，未实际修改；没有合格候选替换原版 |
| 新 SF13 | 0 | original / INDETERMINATE | root_core 不可测、右立柱开口、相应接口未验证；Image API 500 中断 |

SF03 的 working=attempt_0002 与 retained=original 分开保存。不能把候选的
INDETERMINATE 写成最终 retained 的结果，也不能因为四条接口不再能测而声称其干涉已解决。
累计修补尝试为 1 / 2 / 4 / 0；SF03 原来的四次预算已耗尽，没有追加。
本轮共约 19.57 分钟、30 次逻辑模型调用（27 成功、3 失败）、已知 514,499 tokens；
3 个失败请求的用量未知，不计成零。另有启动前预检 311 tokens，未计入上述实验调用数。

合并原六例最后结果：已有组 3/3 程序批准，新生成组 0/3 获得合格最终版本。
已有组包含 SF13 问题版与正常对照两个版本，并非三个独立物体。
已有 SF07 的人工外观负例仍保留：拓扑/程序批准不等于视觉效果可靠。
SF03 日志也显示 Image 指出背部横梁缺损，Code 以源码中存在横梁推翻；本次未独立重做看图审核，
不因此声称外观正确，也不在本次修改 Critic 策略。

## 最小配置变更与验证

- 仅把 `adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml` 的 `max_retries: 0` 改为 `3`。
  这是首次请求之后至多三次 SDK 重试（最多四次请求），不重启 Coder 或新增源码候选。
- 复用已安装 OpenAI SDK 2.54.0 的退避机制；不改代理、凭据、模型、900 秒 timeout、checker 或修补预算。
  重试会增加请求耗时；没有 Retry-After 时的短退避不保证覆盖较长的上游/账号冷却。
- 根据 OpenAI Docs 技能核对[官方错误处理说明](https://developers.openai.com/api/docs/guides/error-codes)，
  并以本机 SDK 的 `_should_retry`、`_calculate_retry_timeout` 核实实际行为。
  文档不作为第三方代理故障根因的证据。
- 32 项配置/服务相关测试通过，包括真实 SDK + MockTransport：500/503 后成功、连续 500 最多四次停止、普通 400 不重试。
  测试不访问网络、不消耗 API token；验证的是重试机制，不是上游稳定性。
- 旧实验配置哈希和结果均不回写；新配置只供后续明确启动的任务使用。
  已冻结实验若需续跑，必须显式记录配置修订，不绕过既有哈希检查。
- 本轮 retained 文件仍保留在代码盘，尚未迁移；未重启实验或代理。
