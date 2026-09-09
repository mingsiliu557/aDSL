# aDSL Codex CLI Agent 适配计划

状态：第一版已实施；离线与真实 structured provider 验证通过，完整 3D create 受机器 I/O 阻塞
日期：2026-08-29

## 实施状态（2026-08-29）

第一版 adapter 已按本文核心设计落地：

- 已新增 `adsl.agents.providers.codex_cli` 和 `codex_codec`，并通过共享 `AgentRuntime` 接入 create/edit/resume/chat。
- 已新增 `codex-cli-gpt-5.6-sol.yaml`，默认 `gpt-5.6-sol` + `high`，不要求 API credential。
- 已实现 read-only/ephemeral subprocess、结构化外层 schema、单层工具参数 JSON、typed final output、图片临时附件、JSONL usage、脱敏 diagnostics、prompt 上限和有界 process-group timeout 清理。
- 离线测试 21 项全部通过；真实 structured provider smoke 通过。
- 真实 `max_rounds=1` create 的 planner、coder 和受控 `write_file` 均通过并保存 diagnostics。后续 `asset_executor` 因本机共享盘 Python import 超过原有 300 秒 timeout 而停止，尚未完成 render/critic/publish；不能把该 case 标记为完整通过。
- 当前小型证据位于被忽略的项目 `temp/`。用户已决定在 `/jiigan-hp` 不可访问期间暂用代码盘小 case；数据盘恢复后，正式实验仍应按手册写入数据盘。
- 详细当前状态、路径、踩坑和续跑命令见根目录 `memory.md`。

与初稿相比的已确认调整：

- 命令默认加入 `--ignore-user-config`，认证仍来自 Codex CLI 登录。
- 首版不开放任意 `ADSL_CODEX_CLI_EXTRA_ARGS`。
- preflight 与模型调用对本机共享盘抖动采用有界 timeout；KILL 后不再无限等待继承的 stdio pipe。

## 1. 目标

在 aDSL 中新增一个 `codex-cli` 模型后端，使已经通过 ChatGPT 登录的 Codex CLI
可以替代 OpenAI/OpenRouter API，驱动现有的 3D 生成和聊天 Agent。

目标调用方式：

~~~bash
adsl-run \
  --model-config adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml \
  create "A compact articulated nightstand with one drawer" \
  --articulation \
  --max-rounds 2 \
  --output /jiigan-hp/ttrv-datasets/experiments/adsl/codex-smoke-nightstand
~~~

首版必须保持以下行为不变：

- 继续使用现有的 planner → coder → execute → image critic → code critic → repair 流程。
- 继续由 aDSL 执行 `read_file`、`write_file` 和 `apply_patch`，Codex 不能绕过工具直接修改文件。
- 继续使用现有的角色隔离、SQLite session、checkpoint、resume、usage 和发布逻辑。
- `adsl-run`、`adsl-chat` 和 `adsl-chat-run` 使用同一个后端选择机制。
- 不要求 `OPENAI_API_KEY` 或 OpenRouter key；复用本机 Codex CLI 的已有登录。
- 原有 OpenRouter/OpenAI profile 必须继续工作，不能为了 Codex 改写原流程。

OpenAI 官方文档把 `codex exec` 定义为脚本和 CI 使用的非交互入口；它支持
JSONL 事件、结构化输出、显式 sandbox、图片输入和复用已有 CLI 登录：

- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/cli/reference

## 2. 当前 aDSL 结构

当前的关键调用链是：

~~~text
ObjectWorkflow / ObjectChatService
        ↓
AgentRuntime.agent(...)
        ↓
OpenAI Agents SDK Agent
        ↓
AgentRuntime.run(...)
        ↓
Runner.run(...)
        ↓
OpenAI Responses / Chat Completions model
~~~

现有 runtime 已经承担了最重要的 Agent orchestration：

- `AgentRuntime` 创建 Agent、选择 model，并调用 `Runner.run()`。
- `Runner.run()` 负责多轮模型调用、函数工具、Pydantic structured output 和 session。
- `SessionManager` 按角色把历史保存在 `sessions.sqlite3`。
- `ObjectWorkflow` 负责执行、渲染、critic、修复、checkpoint 和最终发布。
- 文件工具把读写范围限制在当前 asset workspace，并且只允许修改指定的 `source.py`。
- `UsageRecorder` 汇总每个阶段的 token 使用量。

因此 aDSL 不需要复制 Articraft 的完整 harness；只需要把 Codex CLI 适配成
OpenAI Agents SDK 可调用的 `Model`。

## 3. 核心设计决定

### 3.1 推荐方案：实现 SDK Model adapter

新增 `CodexCliModel`，实现当前固定版本 `openai-agents==0.20.0` 所要求的
Model 接口，并把它直接传给现有 `Agent(model=...)`。

~~~text
aDSL Runner
   │
   │ SDK model request：instructions、history、tools、output schema、images
   ▼
CodexCliModel
   │
   │ codex exec --ephemeral --sandbox read-only --output-schema ...
   ▼
Codex CLI
   │
   │ 一个结构化 assistant turn
   ▼
CodexCliModel
   │
   │ 转成 SDK ModelResponse / function calls / usage
   ▼
aDSL Runner 执行原有工具并进入下一轮
~~~

这样可以保留：

- aDSL 的所有角色和 prompt；
- SDK 的工具循环；
- Pydantic 输出模型；
- SQLite session 和 resume；
- tool event 检查；
- execute–critic–refine 轮数；
- chat router 和 object workflow 的共享后端。

### 3.2 不采用的方案

首版不让 Codex CLI 自己直接进入 workspace 修改 `source.py`，也不为 Codex
单独复制一套 `ObjectWorkflow`。否则会出现两套 session、两套工具权限、两套
resume 和两套 usage 逻辑，难以判断到底是谁修改了文件。

首版也不使用 `codex exec resume`。每次模型调用都使用 `--ephemeral`；
aDSL 的 SQLite session 是唯一会话真相，Codex CLI 只是无状态 transport。

## 4. Codex 子进程协议

建议的基础命令：

~~~text
codex exec
  --ephemeral
  --ignore-rules
  --sandbox read-only
  --color never
  --json
  --output-schema <temporary-schema.json>
  --output-last-message <temporary-response.json>
  -C <aDSL-repository-root>
  --model <explicit-model-id>
  -c model_reasoning_effort="<effort>"
  [--image <temporary-image>]...
  -
~~~

约束：

1. prompt 从 stdin 输入，避免 shell 长度和转义问题。
2. `--ephemeral` 不在 Codex 目录中留下 rollout session。
3. `--sandbox read-only` 是第二层保护；真正的文件改动仍由 aDSL 工具完成。
4. `--ignore-rules` 防止 Codex 自己加载另一套项目 rules，干扰 aDSL role prompt。
5. 模型必须显式配置，不能静默继承用户本地默认模型。
6. schema、最终响应和图片附件只放在 `TemporaryDirectory` 中。
7. stdout 的 JSONL 保存为可诊断事件，并从 `turn.completed.usage` 读取
   input、cached、output 和 reasoning token。
8. stderr、退出码、超时和缺失的最终响应都必须转成明确的 provider error。
9. 超时时终止整个 Codex 子进程组，避免遗留后台进程。
10. 持久记录中的命令必须隐藏临时路径、图片路径和任何 credential 信息。

实施前要用当前安装的 Codex CLI 做一个兼容性 smoke，确认 `--json`、
`--output-schema` 和 `--output-last-message` 可以同时使用。如果当前版本不允许，
最终 JSON 仍由 `--output-last-message` 获取，usage 暂时采用兼容解析，但不能从
不稳定的自然语言日志推断业务结果。

## 5. 结构化响应协议

这是整个适配中最重要的部分。

每次 Codex 调用只返回一个 assistant turn，概念结构如下：

~~~json
{
  "content": "",
  "thought_summary": "Need to inspect the current source.",
  "tool_calls": [
    {
      "name": "read_file",
      "arguments": {
        "path": "source.py"
      }
    }
  ],
  "final_output": null
}
~~~

### 5.1 工具参数必须是 JSON object

不能把完整参数再次编码成 JSON 字符串。Articraft 早期曾使用：

~~~json
{
  "name": "write_file",
  "arguments": "{\"path\":\"source.py\",\"content\":\"...large code...\"}"
}
~~~

长代码、引号、换行和反斜杠容易使内层 JSON 损坏。aDSL 应直接根据当前可用工具
生成严格的 per-tool schema：

~~~json
{
  "name": "write_file",
  "arguments": {
    "path": "source.py",
    "content": "...large code..."
  }
}
~~~

实现要求：

- `tool_calls.items` 是每个可用工具分支的 `anyOf`。
- 每个分支把工具名称固定为对应名称。
- `arguments` 直接复用该工具的参数 schema。
- `additionalProperties: false`。
- 为满足 Codex strict schema，optional 字段可改为 required + nullable；
  解析后删除值为 null 的 optional 字段。
- 拒绝未知工具、非 object 参数、缺少必需字段和无法通过 schema 的响应。
- 每个 SDK tool call 生成唯一且非空的 call ID，并让 tool output 原样关联该 ID。

### 5.2 Pydantic final output

aDSL 的 planner、debugger、critic 和 chat router 都依赖 typed output，例如：

- `ObjectPlan`
- `EditPlan`
- `DebuggerDecision`
- `ImageCriticDecision`
- `CodeCriticDecision`
- `ChatDecision`

当 SDK 提供 output schema 时，外层 Codex schema 的 `final_output` 应嵌入该
Pydantic JSON Schema，而不是要求模型把 JSON 再放进 `content` 字符串。

语义规则：

- 有 `tool_calls` 时，`final_output` 必须为 null。
- 没有工具调用并且 Agent 有 output type 时，`final_output` 必须存在。
- 普通 coder 的最终说明可以使用 `content`。
- adapter 把 `final_output` 序列化为 SDK 期望的 output-text item，再交给
  Runner 做最终 Pydantic 校验。
- 不信任模型声称的类型；adapter 和 SDK 都要验证。

## 6. 会话、上下文和图片

### 会话

- 保留现有 `SessionManager` 和 `sessions.sqlite3`。
- 每个角色仍使用独立 session ID。
- 每次 `codex exec` 都收到 SDK 提供的当前完整 input/history。
- 不把 Codex thread ID 当作 aDSL session ID。
- transport retry 不得重复执行已经完成的工具调用。

### 上下文

aDSL 默认只有两轮 refinement，大多数角色上下文较短。首版先采用 fail-closed 策略：

- 记录 prompt 字符数和 SHA-256，不记录 credential。
- 设置可配置的 prompt 上限。
- 超过上限时给出明确错误，不静默截断关键执行反馈。
- chat 长会话的 compaction 作为后续独立工作；不能在 model adapter 内用一个
  全局 summary 混合不同角色的 session。

### 图片

现有 `user_input()` 使用 data URL。adapter 需要：

1. 遍历当前 SDK input 中的图片。
2. 解码到本次临时目录。
3. 按内容 SHA-256 去重。
4. 通过重复的 `--image` 参数交给 Codex。
5. prompt 中只写 attachment 编号，不写完整 base64。
6. 本次调用结束后自动删除临时图片。

这必须同时覆盖用户 reference image 和每轮 render image。

## 7. 配置设计

新增 profile：

`adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml`

建议内容：

~~~yaml
provider: codex-cli
api: exec
params:
  model: gpt-5.6-sol
  reasoning_effort: high
  timeout: 900
  sandbox: read-only
  ephemeral: true
  json_events: true
~~~

与 OpenAI/OpenRouter profile 不同，Codex profile 不应要求 `credential.file` 或
`credential.env`。身份验证由 Codex CLI 自己管理。

建议环境变量：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `ADSL_CODEX_CLI_BIN` | `codex` | Codex 可执行文件或经过验证的代理 wrapper |
| `ADSL_CODEX_CLI_TIMEOUT_SECONDS` | profile 中的 900 | 单次模型调用超时 |
| `ADSL_CODEX_CLI_EXTRA_ARGS` | 空 | 受控追加 CLI 参数 |
| `ADSL_CODEX_CLI_MAX_PROMPT_CHARS` | 待 smoke 后确定 | fail-closed 上下文上限 |

代理设置只继承当前进程环境，不能在代码中硬编码 `127.0.0.1:7892`。如果本机必须
经过代理，可以把已经验证的 wrapper 配给 `ADSL_CODEX_CLI_BIN`，或在启动命令中
显式设置大小写两套 proxy 环境变量。

`ModelProfile.load()` 应改为按 `provider` 分支验证：

- `provider: openai`：保持当前 credential 和 api 规则。
- `provider: codex-cli`：要求 `api: exec` 和显式 model；禁止 credential。
- 未知字段仍然 fail closed。

## 8. 预计文件改动

| 文件 | 计划修改 |
| --- | --- |
| `adsl-agents/providers/__init__.py` | 新增 provider package 导出 |
| `adsl-agents/providers/codex_cli.py` | 子进程、临时文件、命令、超时、JSONL usage、错误与路径脱敏 |
| `adsl-agents/providers/codex_codec.py` | SDK input、图片、工具 schema、typed output 和 ModelResponse 转换 |
| `adsl-agents/utils/config.py` | provider-discriminated profile；创建 `CodexCliModel` |
| `adsl-agents/utils/runner.py` | 接受 Codex model；不改变现有 Agent/Runner orchestration |
| `adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml` | 官方示例 profile |
| `pyproject.toml` | 注册 `adsl.agents.providers` package；不增加运行时 API 依赖 |
| `README.md` | 增加登录、配置、运行、代理和数据盘示例 |
| `tests/test_codex_cli_transport.py` | 命令、超时、错误、usage 和脱敏测试 |
| `tests/test_codex_cli_model.py` | tool/typed-output/image/SDK contract 测试 |
| `tests/test_codex_cli_workflow.py` | fake transport 下的 planner→coder→tool→critic 集成测试 |

预计不需要改动：

- `adsl-agents/service.py`
- `adsl-agents/tools/*`
- `adsl-chat/service.py`
- 3D executor、renderer 和 publisher

如果实现时必须修改这些文件，应先说明为什么现有 runtime boundary 不够。

## 9. 实施顺序

### Phase 0：冻结基线

- 记录 aDSL commit、Python、Codex CLI 和 `openai-agents` 版本。
- 在项目专用 Python 3.10 环境中安装当前依赖。
- 运行已有 smoke；若仓库没有测试，至少验证 imports、CLI help 和一个不调用模型的
  config test。
- 不修改用户现有的未跟踪文件 `gpu_server_operation_manual.md`。

### Phase 1：SDK 接口 spike

- 读取安装后的 `openai-agents==0.20.0` Model ABC 和 ModelResponse 类型。
- 写一个只返回固定文本的 fake `Model`，证明现有 `Runner.run()` 可以使用自定义模型。
- 再证明一个 function call 和一个 Pydantic output 能通过。
- 只有这三个 spike 通过后才写正式 transport。

### Phase 2：Codex transport

- 实现命令构造、stdin、临时 schema、output-last-message、JSONL events、超时和清理。
- 所有 subprocess 调用使用 argv，不经过 `shell=True`。
- 写 fake-runner unit tests，不消耗模型额度。
- 加 binary/version/login preflight，但不能读取或打印 `auth.json`。

### Phase 3：codec

- 转换 SDK history、function call output、图片和 instructions。
- 生成严格的 per-tool arguments schema。
- 支持 typed `final_output`。
- 转换为 SDK ModelResponse、usage 和唯一 call ID。
- 对 malformed JSON、未知工具、无 final output、双重 final/tool output 做负面测试。

### Phase 4：配置和 runtime 接线

- 新增 Codex YAML profile。
- 保持现有 OpenAI profile 完全兼容。
- 让 `AgentRuntime.agent()` 得到同一实例的 `CodexCliModel`。
- 确认 `UsageRecorder` 能从 SDK RunResult 得到 Codex usage。
- 确认 create、edit、resume 和 chat 都通过同一个 profile 生效。

### Phase 5：无网络集成测试

使用 fake Codex runner 完成：

1. planner 返回 `ObjectPlan`；
2. coder 调用 `write_file`；
3. debugger 在执行失败后调用 `read_file` 和 `apply_patch`；
4. image critic 接收多张 render；
5. code critic 返回 typed decision；
6. workflow 保存 checkpoint、usage 和最终结果；
7. resume 不重复执行已完成工具。

特别增加一个包含长 Python、引号、换行和反斜杠的 `write_file.content`，
证明不会再次出现 “tool arguments were not valid JSON”。

### Phase 6：真实 smoke

正式结果必须写入数据盘：

`/jiigan-hp/ttrv-datasets/experiments/adsl/`

依次执行：

1. 只返回结构化对象的最小 provider smoke。
2. 静态简单物体，`max_rounds=1`。
3. 带一个 reference image 的静态物体。
4. 一个简单 articulated asset，使用官方默认 `max_rounds=2`。
5. 一次 `adsl-run resume`。
6. 一次 `adsl-chat-run` router smoke。

每次保存：

- 完整启动命令和非秘密环境摘要；
- runtime config；
- Codex JSONL events；
- usage；
- source、render、GLB/URDF；
- checkpoint 和 run manifest；
- exit code 与错误；
- git commit 和版本信息。

运行结束后检查没有遗留 Codex、renderer 或 executor 进程。

### Phase 7：文档和交付

- README 写清楚“Codex CLI 后端不是 OpenAI API”。
- 写清楚先完成 CLI 登录，不能复制或提交 `auth.json`。
- 给出 OpenRouter 与 Codex 两套并列命令。
- 写清楚 data disk 输出规则和代理配置方式。
- 在实际验证完成前，不把 Codex profile 标记为 production-ready。

## 10. 测试矩阵

| 范围 | 必测内容 |
| --- | --- |
| 配置 | Codex 无 credential、显式 model、未知字段拒绝、OpenRouter regression |
| 命令 | argv、cwd、read-only、ephemeral、reasoning effort、图片、额外参数 |
| 安全 | 不使用 shell、路径脱敏、不输出 auth、workspace 只能由 aDSL 工具写 |
| schema | 每个工具独立 arguments object、optional null、unknown tool、长代码 |
| typed output | 六个现有 Pydantic decision/plan 类型 |
| 图片 | data URL 解码、去重、格式错误、reference + render 多图 |
| session | role 隔离、chat 历史、resume、call ID 关联 |
| usage | input/cached/output/reasoning/total token 和缺失 usage |
| 错误 | binary 不存在、非零退出、timeout、空响应、坏 JSON、schema mismatch |
| workflow | create、edit、execute failure repair、critic repair、round limit、publish |

## 11. 验收标准

实现只有同时满足以下条件才算完成：

- 登录过 Codex CLI 的机器上无需 API key 即可运行 aDSL Agent。
- 未登录或 binary 不存在时，在调用模型前给出清楚错误。
- 原有 OpenRouter 配置和路径不回归。
- planner、coder、debugger、两类 critic 和 chat router 均使用 Codex 成功运行。
- 复杂 `write_file`/ `apply_patch` 参数不会发生二次 JSON 损坏。
- Codex 不直接修改文件；每次 source 改动都有对应 aDSL tool event。
- reference 和 render 图片真正传入 Codex。
- `run.json`、`usage.jsonl` 和 checkpoint 保持有效。
- create、edit、resume 和 chat 至少各有一个自动化或真实 smoke。
- 正式输出全部位于数据盘，代码仓库只保存代码、配置和少量测试 fixture。
- 运行结束没有遗留子进程。

## 12. 已知风险与处理

| 风险 | 处理 |
| --- | --- |
| OpenAI Agents SDK Model 接口随版本变化 | 固定 `openai-agents==0.20.0`，先做接口 spike 和 contract tests |
| Codex CLI flags/version变化 | 启动时记录版本；命令构造集中在一个模块；不散落 shell 字符串 |
| 工具参数含长代码导致 JSON 损坏 | per-tool arguments object，不使用 JSON-in-string |
| typed output 与 tool call 同轮混淆 | 外层 schema + 解析后语义校验 |
| Codex 绕过 harness 自己改文件 | read-only sandbox + 明确 transport prompt + 工具事件断言 |
| 图片导致 prompt 巨大 | 图片落临时文件并使用 `--image`，正文不携带 base64 |
| 长 chat 超出上下文 | 首版明确上限并报错；后续按 role/session 做 compaction |
| 代理不稳定 | 继承环境或配置 wrapper；不硬编码端口 |
| token usage 格式变化 | 优先解析官方 JSONL `turn.completed.usage`，兼容层单独测试 |
| 结果误写代码盘 | smoke 命令使用数据盘绝对路径并做 mount preflight |

## 13. 首版明确不做

- 不改用 Codex App Server、MCP server 或另一个 Agent SDK。
- 不让 Codex 原生 shell/write 工具直接操作 asset workspace。
- 不在 aDSL 内复制 Codex 登录文件。
- 不硬编码本机代理地址或认证信息。
- 不同时改变 3D executor、renderer、critic prompt 或默认 round 数。
- 不做并行多 Agent；先保证现有串行工作流正确。
- 不把运行结果、日志、模型或缓存长期放在代码盘。

## 14. 实施后的推荐启动检查

~~~bash
cd /vepfs_default/chanxueyan/lhp/lms/aDSL

/vepfs_default/chanxueyan/lhp/lms/npm-global/bin/codex login status

adsl-run \
  --model-config adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml \
  create "A simple wooden stool" \
  --max-rounds 1 \
  --output /jiigan-hp/ttrv-datasets/experiments/adsl/codex-smoke-stool
~~~

开始真实生成前，还必须按 `gpu_server_operation_manual.md` 检查
`/jiigan-hp/ttrv-datasets` 确实挂载为 `hpvs_fs` 且可写。

## 15. 实施状态与实际偏差（2026-08-30）

首阶段 Codex CLI 适配已经实现，详细滚动记录见根目录 `memory.md`。

已完成：

- 自定义 Codex CLI `Model`、codec、配置分支、usage、诊断、超时清理和图片临时文件传递；
- 真实 structured provider smoke；
- 三个静态 text-to-3D case 的 source、GLB、URDF 与 CPU 多视角渲染；
- 一个 articulated 双门柜的 `max_rounds=2` 完整 execution → Image Critic 闭环，首轮获批；
- 一个真实 extend edit，覆盖 Edit Planner/Coder 的 `read_file` 与 `apply_patch` 工具循环；
- 一个真实 reference-image create；参考图实际进入 Planner、Coder、Image Critic 和 Code Critic，Code Critic 纠正 Image Critic 的视觉误判后批准发布；
- 一个受控 interrupted resume；在 `refining`/首轮执行器冷启动阶段中断，确认旧进程退出后直接从保存源码恢复，且没有新增 Codex 请求；
- 一个为触发 repair 专门设计的复杂书桌 case；首轮资产完整包含全部指定结构，Image Critic 合理批准，人工八视图复核一致，因此未人为制造 repair；
- 一个 `adsl-chat-run` 普通 chat router smoke；
- 最新全量测试 `36 passed`，无遗留 Codex、executor、renderer 或 pytest 进程。

实施中确实修改了原计划标记为“预计不需要改动”的边界，原因均由真实运行暴露：

- `adsl-agents/service.py`：共享盘冷启动超过原固定 300 秒，因此增加默认不变的 executor timeout 覆盖并记录有效值；同时修复 publisher 遗漏 `scene.joint_states.json` 与 `render/meta.json` 的问题。
- `adsl-core/tools/render.py`：本机缺少 `libEGL.so.1`，固定 Eevee 无法无头渲染；增加默认不变的 Cycles/尺寸/samples 环境覆盖，CPU smoke 使用 Cycles。
- `adsl-agents/prompt/coder.md`：原强制 `from adsl import *` 与 bundled 文档的 `from adsl.core import *` 冲突，在 editable namespace 下导致 `Asset` 未定义；现已统一并回归测试。
- `adsl-agents/utils/execution.py`：受控中断发现普通 `subprocess.run` 不能显式管理 executor/Blender 后代；现使用独立进程组，并在 timeout/中断/异常时做有界 TERM → KILL。Linux `D` 状态仍需等待内核 I/O 返回，但信号会保持 pending，恢复前必须确认旧 PID 消失。

暂未满足、不能标记 production-ready 的项目：

- `/jiigan-hp` 当前探测仍无响应；经用户允许，小 case 暂存仓库忽略的 `temp/`，尚未迁移到正式数据盘。
- Image Critic 与 Code Critic 均已有真实覆盖；reference case 的 Code Critic 判定首轮源码尺寸正确并纠正视觉误判，专门设计的复杂书桌又被 Image Critic 合理首轮批准，因此没有自然触发 Repair Coder。repair 分支仍以 contract/offline 覆盖为主，不通过篡改 critique 或资产伪造覆盖。

因此当前结论是：Codex CLI backend 与 text/reference create、articulation、edit、受控 resume、chat、CPU executor/render 及两类 Critic 主链路均可用；正式数据盘与自然触发的 Repair Coder 是剩余验收项。

## 16. Stepcode HTTP API 转向（2026-08-30）

本机后续发现 Stepcode 已配置 API credential 和本地 gateway，因此新增一条无需 `codex exec` 的主路径。真实 key 只用于内存验证，未写入仓库或本文档。

已确认：

- `stepcode system status` 显示 credential 已配置，本机 gateway 为 `127.0.0.1:44949`；
- 带认证 `/v1/models` 返回 4 个模型，包括 `gpt-5.6-sol`；
- `gpt-5.6-sol` 的 `/v1/chat/completions` 与 `/v1/responses` 均返回 200；
- 新 profile `stepcode-gpt-5.6-sol.yaml` 使用 `provider: openai`、`api: responses` 和 `credential.stepcode: true`；
- key 由 `stepcode config get apiKey` 以 argv-only subprocess 获取并仅在内存解析；repr、错误、runtime metadata 和 Git 均不含 key；
- profile 设置 `trust_env: false`，避免机器代理把 localhost 请求转发后返回 502；
- 真实 `adsl-chat-run` structured router 成功；
- 真实 `adsl-run create` 完成 Planner、Coder tool loop、CPU GLB/URDF/8-view publish，全程无 `codex exec`；
- 最新全量测试为 `41 passed in 234.38s`。

分支策略：当前工作区位于 `master`，远程为 `origin`。推荐把现状提交到新的 `stepcode-api-backend` 分支，同时保留 Codex adapter 作为 fallback；不需要重新 clone。任何 push 前必须排除 `temp/`、真实 key 和用户未跟踪的 `gpu_server_operation_manual.md`，并由用户明确授权远程写入。
