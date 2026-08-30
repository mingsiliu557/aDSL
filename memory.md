# aDSL 工作记忆

更新时间：2026-08-30（UTC）

本文是滚动的当前摘要，不是追加式日志。后续修改时应更新/替换过期内容。

## 当前目标与状态

- 目标：让现有 aDSL Agents SDK workflow 可在本机可靠运行。当前同时支持 Stepcode OpenAI-compatible HTTP API 与 Codex CLI；后者保留为已验证 fallback。
- Stepcode HTTP API profile 已实现并通过真实 chat/create；新开发优先使用 `adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml`，无需再为每个 model turn 启动 `codex exec`。
- Codex CLI adapter 仍接入共享 `AgentRuntime`。`adsl-run create/edit/resume`、`adsl-chat` 和 `adsl-chat-run` 可通过 `--model-config` 在两个后端之间切换。
- 第一阶段端到端复现已完成：文本 create、reference-image create、articulated create、edit、受控 interrupted resume、两类 Critic 和 chat 均已有真实 Codex CLI 证据；本机资产执行与多视角渲染全部使用 CPU。
- Stepcode profile：`adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml`，使用本机 `http://127.0.0.1:44949/v1` 的 Responses API 与 `gpt-5.6-sol`；Codex fallback profile 为 `adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml`。
- 原 OpenAI/OpenRouter profile 分支保留，仍要求原 credential；Codex profile 明确禁止 credential。

## 本机路径与资源规则

- 代码仓库：`/vepfs_default/chanxueyan/lhp/lms/aDSL`。
- 项目环境：`/vepfs_default/chanxueyan/lhp/lms/envs/adsl`（Python 3.10，已安装 editable aDSL、Agents SDK 0.20.0、mesh/render 依赖）。
- 当前小 case/smoke：仓库内被忽略的 `temp/`。Codex 证据保存在各自 `temp/codex-*` workspace；Stepcode HTTP 证据在 `temp/stepcode-api-chat-smoke/` 和 `temp/stepcode-api-create-smoke/`。已发布资产目录包含 `source.py`、`scene.glb`、`scene.urdf`、8 张 PNG、sidecar 和运行记录。这里只适合当前前期小数据，不作为长期正式实验目录。
- 正式持久数据根目录按手册应为 `/jiigan-hp/ttrv-datasets`，aDSL 正式实验建议放 `/jiigan-hp/ttrv-datasets/experiments/adsl/`。但 2026-08-29 `/jiigan-hp` 不可访问/会挂起，因此暂时不要写该路径。
- 数据盘恢复后，提交前必须用 `findmnt -T` 确认 target 是 `/jiigan-hp`、FSTYPE 是 `hpvs_fs`，再检查可写性；不能只看目录是否存在。
- 本阶段 Codex CLI、Blender CPU smoke 不需要 GPU。
- 若后续需要 GPU：先在普通登录节点开 `tmux`，再由 tmux 中的 manager 使用 `volc ml_devinstance launch` 申请，不能用 Slurm。队列 `q-20250901110548-6w2bl`；单卡 A800 flavor `ml.pni2l.3xlarge`，双卡 A800 flavor `ml.pni2l.7xlarge`。无人值守优先 direct launch `volc ... bash worker.sh`，worker 返回即释放申请。

## 已实现的 Stepcode HTTP API 协议

- 本机 Stepcode gateway 在 `127.0.0.1:44949` 监听；带认证的 `/v1/models` 返回 `claude-opus-4-8`、`claude-opus-4-8-jigan`、`claude-opus-5` 和 `gpt-5.6-sol`。`/v1/chat/completions` 与 `/v1/responses` 的最小 `gpt-5.6-sol` 请求均返回 200；项目 profile 选择 Responses API。
- `credential.stepcode: true` 会用 argv-only subprocess 调用 `stepcode config get apiKey`，从带格式的输出中只在内存提取唯一 `ak-*`/`sk-*` token。key 不写 YAML、runtime metadata、README、日志或 Git；`ModelProfile` 的 `repr` 也隐藏 `api_key`。
- Stepcode credential lookup 的非零退出、超时、binary 缺失和模糊输出均 fail closed，错误消息不拼接 stdout/stderr。覆盖变量为 `ADSL_STEPCODE_BIN` 与 `ADSL_STEPCODE_TIMEOUT_SECONDS`。
- 本机环境代理会把未绕过的 localhost HTTP 请求导向代理并返回 502；Stepcode profile 显式 `trust_env: false`，而现有 OpenRouter profile 的默认 `trust_env: true` 保持不变。
- runtime 使用现有 `OpenAIResponsesModel`、Agents SDK sessions、Pydantic structured output 和 aDSL 文件工具，不需要自定义 codec 或 `codex_cli/call_*` 诊断目录。

## 已实现的 Codex 协议

- `CodexCliModel` 实现固定版本 `openai-agents==0.20.0` 的 `Model` 接口，现有 SDK Runner 继续负责角色 session、工具循环、Pydantic output 和 usage 聚合。
- 每个 turn 使用 `codex exec --ephemeral --ignore-user-config --ignore-rules --sandbox read-only --json --output-schema ... --output-last-message ...`；prompt 从 stdin 输入，不使用 shell。
- SQLite session 是唯一会话真相，不使用 `codex exec resume`。
- Codex 不直接写 asset workspace。`read_file`、`write_file`、`apply_patch` 仍由 aDSL 工具执行并受 `source.py` 范围限制。
- 工具参数在 Codex schema 中是直接 JSON object；进入 SDK `ResponseFunctionToolCall.arguments` 时只序列化一次，避免长 Python/引号/换行/反斜杠的二次 JSON 损坏。
- 每个 model turn 最多一个工具调用；unknown tool、非 object 参数、缺字段、typed final/tool 混用、坏 JSON 等均 fail closed。
- Pydantic output 嵌入外层 `final_output` schema，并由 adapter 和 SDK 双重校验。
- Responses data-URL 图片落到每次调用的临时文件，按 SHA-256 去重，通过重复 `--image` 传入；prompt 不含 base64，调用结束清理。
- 从最后一个 `turn.completed.usage` 解析 input、cached、output、reasoning token。
- 子进程新建 process group；timeout 后 TERM、KILL，再有界关闭 pipe transport，避免启动器后代继承 stdio 导致无限等待。
- 每次调用把脱敏 metadata、JSONL events、最终 response 和 stderr 保存到 `<asset-workspace>/codex_cli/call_*`。metadata 只记录 prompt 长度与 SHA-256，不保存 prompt、图片 base64 或认证内容。
- 模型环境变量：`ADSL_CODEX_CLI_BIN`、`ADSL_CODEX_CLI_TIMEOUT_SECONDS`、`ADSL_CODEX_CLI_MAX_PROMPT_CHARS`。执行/渲染覆盖：`ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS`、`ADSL_RENDER_ENGINE`、`ADSL_RENDER_WIDTH`、`ADSL_RENDER_HEIGHT`、`ADSL_RENDER_SAMPLES`。默认仍为 300 秒、Eevee、1024×1024、256 samples；本机 CPU smoke 使用 900 秒、Cycles、512×512、16 samples。有效配置写入每个 workspace 的 `runtime_config.json`。

## 验证结果

- 离线测试：2026-08-30 最新全量 `41 passed in 234.38s`。除原 Codex/config/workflow/executor/render/publish 覆盖外，新增 Stepcode credential 提取、literal true、secret-safe repr/error 和 OpenRouter/Codex 回归。
- Stepcode HTTP 最小协议探针：认证 `/v1/models` 成功；Chat Completions 返回精确短文本，Responses 返回 `status=completed`。
- Stepcode one-shot chat：`adsl-chat-run` 经真实 Responses API/Pydantic router 返回 `action=chat`，没有 `codex exec` 或 `codex_cli/` 目录；证据在 `temp/stepcode-api-chat-smoke/`。
- Stepcode side table create：Planner typed output、Coder 文件工具调用与 typed final 全部走 HTTP API；CPU 发布 10,368-byte GLB、2,927-byte URDF 与 8 张 PNG。3 requests、13,491 tokens，`runtime_config.json` 只记录 `credential_source=stepcode`，无 key。联系图：`temp/stepcode-api-create-smoke/contact_sheet.jpg`。
- 真实最小 structured provider smoke：通过。返回 `{"ok":true,"message":"Structured output works."}`；usage 为 1 request、16,541 input、40 output tokens。证据在 `temp/codex-provider-smoke/`。
- 木凳 `max_rounds=1`：端到端成功，发布 16,668-byte GLB、2,977-byte URDF、8 张 512×512 PNG。历史累计 3 个 Codex requests、58,720 tokens。联系图：`temp/codex-create-smoke/contact_sheet.jpg`。
- 床头柜 `max_rounds=1`：端到端成功，包含顶板、浅抽屉、把手、开放搁板和侧板；发布 41,368-byte GLB、782-byte URDF、8 张 PNG。3 requests、64,692 tokens。联系图：`temp/codex-case-bedside-table/contact_sheet.jpg`。
- 餐椅 `max_rounds=1`：端到端成功，包含四腿、座面、双后立柱和三条横档；发布 16,928-byte GLB、4,712-byte URDF、8 张 PNG。3 requests、59,680 tokens。联系图：`temp/codex-case-chair/contact_sheet.jpg`。
- 三个 case 均使用 CPU Cycles；未申请或使用 GPU。`approved=false` 是 `max_rounds=1` 在首次成功 execution 后按设计以 `round_limit_after_execution` 发布、跳过 critic，不是失败。
- Articulated 双门柜 `max_rounds=2`：第 1 轮 execution 成功，Image Critic 查看 8 张 PNG 后直接 `approved=true`，因此按设计不再调用 Code Critic/repair。发布 107,196-byte GLB、10,406-byte URDF、两个 door mesh、8 张 PNG 和两个初始值均为 0.4363 rad（约 25°）的 `scene.joint_states.json`。4 requests、88,922 tokens。联系图：`temp/codex-case-articulated-cabinet/contact_sheet.jpg`。
- 餐椅 extend edit：基于 `temp/codex-case-chair/source.py` 增加左右直扶手，完整经过 Edit Planner `read_file` → typed EditPlan、Coder `read_file` → `apply_patch` → final，再次 CPU 执行/发布成功。原 source SHA-256 保持 `479ef5...a3964`，新 source 为 6,099 字节；5 requests、103,525 tokens。联系图：`temp/codex-edit-chair-armrests/contact_sheet.jpg`。
- Reference-image 床头柜 `max_rounds=2`：以既有床头柜首张 512×512 render 为参考，Planner/Coder 均收到 1 张图片，Image Critic 与 Code Critic 均收到参考图加 8 个新视图。发布 53,012-byte GLB、11,027-byte URDF 和 8 张 PNG。Image Critic 认为抽屉缝隙过大而拒绝；Code Critic 读取源码后以对称的 0.05-unit 水平 reveal 和 0.0275-unit 垂直 reveal 纠正视觉误判，最终 `code_critic_approved`，未做无必要 repair。6 requests、137,122 tokens。对照图：`temp/codex-reference-nightstand/reference_contact_sheet.jpg`。
- 受控 interrupted resume 茶几：在 Planner/Coder 完成、`checkpoint.stage=refining`、执行器尚未产出资产时发送 `Ctrl-C`；源码 2,164 字节与 checkpoint/usage 完整保留。确认旧 `D` 状态 executor 退出后，`adsl-run resume` 从 `next_round=1` 直接执行并发布 10,436-byte GLB、2,968-byte URDF 和 8 张 PNG；`run.json` 含 `resume=true`、`resumed=true`，请求总数恢复前后均为 3，证明没有重跑 Planner/Coder/Critic。联系图：`temp/codex-resume-controlled/contact_sheet.jpg`。
- 进程组清理修复后的真实无渲染执行 smoke：通过 `execute_asset_source()` 正常发布 10,436-byte GLB 与 2,968-byte URDF，render 数为 0；证据在 `temp/execution-cleanup-smoke/`。
- 复杂书桌 `max_rounds=2`：为自然触发 Repair Coder，要求矩形台面、四方腿、居中浅抽屉/把手、双层开放 hutch 和背部 X 支撑。首轮发布 51,544-byte GLB、798-byte URDF 与 8 张 PNG；Image Critic 确认所有结构清晰存在并 `approved=true`，八视图人工复核一致，因此没有为了覆盖率伪造 repair。4 requests、88,004 tokens。联系图：`temp/codex-case-repair-desk/contact_sheet.jpg`。
- one-shot chat：`adsl-chat-run` 使用 Codex profile 成功返回 `action=chat` 和简短确认文本，`asset=null`，证据在 `temp/codex-chat-smoke/`。
- 第一次触发旧 pipe 清理缺陷的 workspace 保留在 `temp/codex-create-smoke-preflight-timeout-20260829/`；缺陷已修复。

## 重要踩坑

- 本机 `CODEX_HOME=/vepfs_default/chanxueyan/lhp/lms/.codex` 位于共享盘。2026-08-29 共享盘严重抖动时，Python/Agents SDK import 约需 3–5 分钟，`codex --version`/`login status` 也可能需数分钟。adapter 的一次性 preflight 上限为 300 秒，model call 使用 profile 的 900 秒。
- Linux `D` 状态是不可中断 I/O 等待，不是模型或 GPU 错误。代码和 Python 环境都位于 `/vepfs_default`，首次 import 仍可能耗时数分钟；Stepcode HTTP 消除每轮 Codex CLI/preflight 开销，但不能消除进程首次读取共享盘依赖。后续可用常驻服务或本地盘环境缓存优化。
- `adsl-agents/prompt/coder.md` 原先错误地强制 `from adsl import *`，而 bundled DSL 文档/示例要求 `from adsl.core import *`。editable 安装时 `adsl-agents` 的 namespace finder 会先占用顶层 `adsl`，导致前者没有 `Asset` 并报 `NameError`。提示词已统一为 `from adsl.core import *`，并有回归测试。
- 本机没有可用的 `libEGL.so.1`，固定 Eevee 会在 GLB 成功后报 EGL 错误。Cycles 可纯 CPU 无头渲染，因此增加 `ADSL_RENDER_ENGINE=CYCLES` 及 preview 尺寸/采样覆盖；默认不变。
- 每个新 asset runtime 都会重新做 Codex `--version` 和 `login status` preflight；同一进程跑多个 case 时目前仍重复，当前共享盘上每次可能浪费约 5 分钟。后续应做安全的进程内 preflight cache。
- `asset_executor` 为隔离生成代码而每轮启动新 Python/Blender 子进程；共享盘上每次 import 可能约 3–5 分钟。900 秒覆盖只用于本机运行，项目默认保持 300 秒。
- 受控中断验证发现旧 `execute_asset_source()` 使用普通 `subprocess.run`：父进程收到 `Ctrl-C` 后，正处于 Linux 不可中断磁盘睡眠的 executor 会短暂成为 PID 1 的子进程。现改为独立 process group，并在 timeout、`KeyboardInterrupt` 或其他异常时有界 TERM → KILL 整个组；但内核 `D` 状态仍只能等 I/O 返回后兑现 KILL。恢复前必须确认旧 executor PID 已消失，不能并发写同一 `round_01`。
- 原 `_publish()` 会遗漏 executor 生成的 `scene.joint_states.json` 和 `render/meta.json`，导致 articulated 发布目录丢失初始关节状态与相机元数据。现已复制，新发布会在 `run.json` 记录路径；四个修复前的既有 case 也已回填 sidecar。修复后的餐椅 edit 已验证两个 manifest 字段。
- Codex launcher/native 子进程退出后，分离后代可能暂时保持 stdout/stderr pipe。绝不能在 KILL 后无限 `await communicate()`；当前实现会有界关闭 transport 并取消任务。
- `--ignore-rules` 指 Codex exec-policy rules；aDSL 的业务约束仍必须写进 transport prompt，不能依赖该 flag 代替 prompt。
- `temp/` 已加入 `.gitignore`；原来忽略整个 `tests/` 的规则已移除，使 adapter 单元测试可以被版本控制。
- 不读取、打印、复制或提交 Codex `auth.json`。CLI preflight 只调用 `--version` 和 `login status`。

## 下一步建议

1. 建议在远程新建 `stepcode-api-backend` 分支并保留 Codex adapter 作为 fallback；不需要重新 clone。若后续确实要求完全移除 Codex，再从远程基线新建第二分支并只 cherry-pick Stepcode 改动。
2. 若要补齐最后一个自然分支，可专门运行一个确实需要修改源码的 Critic repair case；真实 Image Critic 与 Code Critic 已覆盖，但 Repair Coder 未自然触发。
3. 可考虑常驻 agent 服务或本地盘 Python 环境缓存，减少 `/vepfs_default` 首次 import 的数分钟等待。
4. `/jiigan-hp` 恢复后按 mount fail-closed 预检，把正式实验改到数据盘；当前 `temp/` 结果只作为小型开发证据。
