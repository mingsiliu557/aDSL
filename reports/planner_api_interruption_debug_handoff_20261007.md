# Planner API 断流：给下一位 Agent 的 debug 交接

日期：2026-10-07 UTC。本文件整理已有运行证据，不新增请求或修复。

**直接原因已确认：完整 Responses 响应尚未结束，远端 HTTP/SSE 链路就返回了断流错误；SDK 因此不能交付完整 Plan。具体是网关超时、上游连接异常，还是终止事件转发问题，尚未定位。不能直接写成“客户端等待太短”或“网关固定 90 秒超时”。**

错误原文：

```text
stream error: stream disconnected before completion:
stream closed before response.completed
code: request_timeout
```

## 1. 三次实际请求

全部请求为 `gpt-6-astra`，同一 SSH API、client timeout=900 秒、diagnostic max_retries=0、max_output_tokens=32768。每次只发送一个本地 POST，没有调用 Coder、checker 或整批生成。

| 2026-10-07 UTC | 请求 | 实际结果 | 耗时 |
|---|---|---|---:|
| 02:54:30—02:56:01 | 原版完整图像 Planner，非流式 | 服务端 HTTP408，SDK `APIStatusError` | 91.733s |
| 02:56:53—02:57:42 | 同一原版输入/schema，流式 | HTTP200 + `response.completed`，有效 ObjectPlan，6个语义部件 | 49.823s |
| 03:03:33—03:05:00 | ours 原生装配 Planner，经新 harness 流式调用 | HTTP200 建流，随后 SDK `APIError`/`request_timeout`；无 `response.completed` | 88.075s |

第三次**不是 HTTP408**：HTTP200 只代表流已建立，之后的流错误仍会导致失败。首个 raw event 在8.103秒出现；最后事件时间/逐事件轨迹没有保存，不能据此区分总请求限时与空闲限时。没有保存或接受部分 Plan，源码初生成/修补均为0。

成功请求已知4527tokens；两个失败请求用量未知。成功请求有2944 cached input tokens，单次成功不能证明流式一定更快或已经修好整个服务。

同接口此前的 Sol 完整 Planner 也曾在53.742秒返回408。因此当前证据不足以确定统一的90秒硬限时。此前误用Sol已纠正；换成Astra后仍复现断流，模型误配不是本次Astra断流的充分解释。

## 2. 已核对的边界

- 实际 HTTPX connect/read/write/pool timeout 均900秒；请求携带 `x-stainless-read-timeout: 900.0`。错误提前返回，不是本地 `APITimeoutError`。仅扩大本地timeout不能覆盖服务端已返回的错误。
- 生产profile仍为900秒、max_retries=2；诊断为0以避免混淆单请求耗时。旧生产Planner总耗时可能含SDK重试，原始日志没有逐HTTP尝试记录，不能当作单请求限时。
- 300秒配置属于后续源码执行/几何渲染，不是Planner限时。失败发生在Plan之前，未进入几何、渲染、FEA或其他checker。
- 调用链：`AgentRuntime → OpenAI Responses SDK → 127.0.0.1:28317/v1 → SSH TCP转发 → 远端127.0.0.1:8318 → 上游模型服务`。最后一段的实现/日志尚不可见。
- SSH转发可用；复用SSH master开启只读shell session时，远端返回 `Session open refused by peer`。尚未取得网关服务配置或上游日志。SSH keepalive不是HTTP请求时限。
- 原版system instructions=8881字符，ours=24629字符。没有重复注入：增量恰为Planner模板240 + 新DSL文档6005 + 装配合同/换行9503字符。两份完整指令各自正常注入一次；不能仅凭长度宣称根因。
- 原版输入为一张512×512 PNG，135543字节；请求约192KB。实际ours合同更复杂，schema为FixedAssemblyPlan，不应把它与ObjectPlan的耗时当作同输入对照。

## 3. 优先排查

1. 取得远端8318网关以及它连接的上游日志，按上述UTC时间定位；已保存响应没有request ID。查哪一段首先报错、关闭连接或触发请求总时限/流空闲时限。
2. 核实上游是否真正发送了 `response.completed`，以及网关是否完整接收/转发。错误消息本身不能区分“上游未发送”和“中间层丢失”。检查总请求限时、上游读取限时、空闲限时、连接重置和SSE转发错误；不要预设具体限时值。
3. 若需追加单次诊断，记录HTTP状态、首/末事件时间、终止事件、服务端错误与trace/request ID；保留原输入/schema/model，只改变一个待验证变量。提示词长度和输出长度目前只是潜在工作量因素，不是已确认原因。失败调用usage不得记为0。
4. 客户端仍须拒绝没有完整结束/校验的Plan；不要把缺少completed直接当成功，也不要靠反复启动整批来验证API。

## 4. 文件与复现入口

执行代码：`/tmp/adsl_six_method_comparison_20261006`，分支 `feat/benchmark-six-method-comparison`，本地提交 `e8f6d4f6cb9e1394ea4e113596cda6e08ccb12eb`（未推送/合并）。

修改限定为六例harness的Planner，使用官方SDK `Runner.run_streamed`；完整消费、检查completed后返回和记录usage。其他角色、两套生产源文件、提示词与修复预算保留原调用。断流隔离/SDK边界回归：56 passed。它是已验证的传输与失败保护，不是已完成的服务端修复。

运行根目录：

```text
/jiigan-hp/lms/aDSL/experiment/benchmark_six_20261006T174926Z
```

以下路径均相对运行根目录：

```text
config/astra_20261007/{official_model.yaml,ours_model.yaml,envs.json,model_correction.json}
diagnostics/astra_planner_20261007T025429Z/{summary.json,planner_input.json}
diagnostics/astra_planner_stream_20261007T025652Z/{summary.json,planner_input.json,plan.json}
diagnostics/astra_ours_harness_20261007T030332Z/summary.json
diagnostics/astra_ours_harness_20261007T030332Z/jobs/lamp_planner_smoke/ours/generation/role_calls/001_plan.json
```

最后一份role_call含ours实际完整system instructions、输入、错误与完成标记。成功流式summary含逐事件类型/时间，不含delta/思考文本。三份诊断脚本另存于 `diagnostics/planner_debug_handoff_20261007/`；重新执行会产生真实模型请求，请在独立诊断范围使用。

解释器：

```text
/tmp/adsl_six_envs_20261006T174926Z/official/bin/python
/tmp/adsl_six_envs_20261006T174926Z/ours/bin/python
```

原版两个诊断用official解释器；ours harness诊断用ours解释器。运行时沿用原诊断的代理变量清除方式。密钥由既有profile运行时读取，不在交接文档或脚本中嵌入。

历史ROOT/config下的旧profiles/envs/frozen及首例失败不改写；修正配置在astra_20261007子目录。整批仍暂停，未重放已开始的候选、未重置预算。本次仅整理，未新增API调用。
