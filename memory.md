# aDSL 工作记忆

更新时间：2026-09-20（UTC）

本文是滚动的当前摘要，不是追加式日志。修改项目、环境或实验状态后，应替换过期内容。

## 当前固定装配边界（2026-09-20）

- 用户追加要求将同一SF03单例上限改为5轮，已通过单例入口 `--max-rounds 5` 启动：
  `local_experiment/fixed_assembly_visual5_20260920T043431Z/SF03`，tmux
  `adsl_assembly_visual5_20260920T043431Z`。第1轮检查原候选+最多4次修补；不强制凑轮数，
  判通过/主动停止仍可提前结束，不改Code/Image Critic裁决。仅stepcode，几何及四checker关闭。
  实测已结束：实际只评审第1轮，0次修补，920.56秒、41016tokens；Image拒绝缺损靠背，
  Code以源码存在双立柱和打印件shown记录推翻拒绝，提前批准原稿；几何仍未检查。
  源码未变，不能称修补成功或外观合格；代理已自动停止。详情见新目录result.json。
  本次定向回归18 passed；入口默认仍2轮，原实验保留。任务自启代理结束自停。

- 本轮开发基线为 `fcfdeda7fe78e73969ec206389396bdc1e77c0ec`；视觉模式、诊断循环、
  stepcode单例入口及相关测试随本次Git提交归档，实验资产/API日志不上传。
- 已只读对照官方sig-pku/aDSL及初始提交4d9c1bb：两份Critic核心提示未改，官方也允许Code
  推翻Image并立即结束。当前固定装配复用candidate_preservation，缺少原版plan/checklist、
  max_rounds和历史字段，首轮8图重复成16图；新增complete/shown仅表示可显示，不保证
  子结构语义完整，Code在实测中误用此证据。上述裁决及输入调整仅讨论，尚未实施。
- 用户最新要求优先于之前的几何 gate 方案：先实现原 aDSL + connector + Image/Code Critic，
  通过 `fixed_assembly.validation_mode=visual_only` 关闭闭合、连通、接口体积、穿透及尺寸测量。
  原 `geometry` 模式保留为默认；四个物理 checker 不启用。构造参数合法性与导出一致性仍保留。
- 每个打印件只执行一次实际 CSG，展示模式不做 Manifold 合并或实体判定；同一三角网格复用给
  STL、局部GLB、总装/拆分GLB。导出比较沿用 float32 长度容差，检查三角形序列化一致性，
  不用闭合体差集阻断视觉流程。视觉通过不表示接口或制造通过，几何记 NOT_EVALUATED。
- 有可用网格就尝试渲染，缺失/空/非有限网格明确记遗漏；残缺展示不能通过完整外观审核。
  无图只跳过 Image Critic，Code Critic 仍读源码；修补沿 working 候选推进，retained 独立保存。
  不人工修改生成源码，不新增预算。源代码错误/导出失败记录供 Coder 修复，不伪造几何结论。
- 定向测试 110 passed / 6 skipped；跳过项为显式开启的旧真实几何实验，没有跑历史批次。
- 唯一新实测：`local_experiment/fixed_assembly_visual_20260920T034602Z/SF03`，从历史失败候选副本
  出发，不调用 Planner/初始生成，最多一次修补。仅 stepcode gpt-5.6-sol，实验自启代理结束自停。
  原稿位于 `local_experiment/fixed_assembly_prompt_20260919T074507Z/SF03/generate/rounds/round_02/candidates/01_assembly_or_appearance/source.py`。
  实测已结束：157.60 秒、3次stepcode调用/34249tokens、0次生成/0次修补；6件导出、
  8总装+2拆分图、24项文件一致性通过。自启代理已关闭，tmux回到可交互bash。
  Image Critic拒绝单侧缺损靠背，Code Critic仅据源码含双立柱将它纠正为通过，因而没有
  进入Coder修补；流程记录approved=true仅限visual_code_only，不代表独立确认外观正确。
  多视图仍可见靠背缺损。本轮未改冲突裁决，不追加API/预算；详见
  `reports/fixed_assembly_visual_loop_20260920.md`。启动命令见实验README。

## 历史代码与实验快照（2026-09-19）

- 当前边界：人工只修agent工作流/执行错误处理，不手工改生成source.py，不替agent补几何；
  只有运行内Coder可按既定预算修改模型并重新验收。用户要求持续监督，异常允许暂停修复继续。
- 074507Z新批SF07、SF03已结束且未通过；SF07修补遇空网格IndexError，SF03座面修好、
  靠背仍无效。两例原稿/候选源码哈希均核对未人工改动，最终保留original，不追加预算。
  SF13在Planner阶段挂起原PID2258453，export_assembly.evaluated补6行空数据ValueError检查，
  沿既有PART_GEOMETRY_INVALID返回，不删除空对象或变更几何。85通过/6跳过；07:59:34 UTC
  SIGCONT恢复同一进程，代理与调用未重启。暂停计入原API观察耗时，详见本批MONITORING.md。
- 074507Z三例现已全部结束：SF13修补后5层板有效、主体仍无效，所有候选均拒绝、
  retained=original/approved=false。没有总装图片，视觉/代码审核未达到执行阶段；不报外观PASS。
  共19次API均返回、197251tokens；没有新的流程异常。SF13耗时433.75秒含人工暂停。
  发布与版本源码哈希均匹配；tmux任务rc0且bash可交互。当前没有运行case，不擅自补跑。

- 用户要求沿固定装配全流程审查并修复后启动新实验。已检查输入、生成、导出、审核、
  修补、retained与批次隔离；不是全仓库重构。`fixed_assembly.py`修复缺manifest时反馈
  指向不存在文件的问题，改指真实execution_error.json；保留已完成几何FAIL，不改写成ERROR。
  损坏manifest/空异常信息有界处理，非预期FLOW_ERROR仍停止，未审核外观不当PASS。
  最终相关测试76 passed/6 skipped（4.88秒），新增test_fixed_assembly_recovery.py；不放宽验收。
- SF07单次改序对照2.47秒：按插入顺序UNION仍丢失z24–59.88材料，虽一块闭合也不是正确。
  该export_glb改动已撤回，原solver/排序/容差不变；不继续内核排查。证据与本轮修复详见
  `local_experiment/diagnostics/sf07_operand_order_check_20260919/REPORT.md`。
- 新实验目录`local_experiment/fixed_assembly_prompt_20260919T074507Z`已启动：
  同prompt与冻结参数，SF07→SF03→SF13，各一次初生+最多一次修补，四checker关闭，
  CLIProxy独立tmux。实验tmux为`adsl_assembly_prompt_20260919T074507Z`，提交确认时
  runner PID2257523、SF07实际Planner请求001 RUNNING；SF03/SF13按门禁后续串行。
  新一轮由用户授权，旧源码、结果、预算保留；后续状态以first.log/rest.log及result为准。

- SF07一次限时局部定位已结束：`local_experiment/diagnostics/sf07_local_csg_20260919/REPORT.md`。
  原TaperedStem、原导出路径，120秒预算内实际3.31秒，0API/0checker/0修补、源码未改。
  第10次UNION加入op_18时已有z=43.88–60.12段消失，体积下降3277.47mm³；最终GLB
  写入前已形成z=44.12–59.88缺口。未切solver/排序做对照，不宣称内核根因已证明，不再深挖。
- 仅修改`experiments/fixed_assembly_prompt/run.py`的批次错误分类：明确的无效候选网格
  不再视为全批共同故障，仍拒绝候选；导出不一致、缺文件/ID、未知读取/API/流程错误仍停。
  37项相关mock与代理生命周期测试通过。旧SF07原result与预算不改，通过保存证据复核，
  另存`SF07/continuation_gate.json`解开旧暂停；历史`paused.json`只表示当时的暂停。
- SF03→SF13续跑已经正常结束，旧目录`local_experiment/fixed_assembly_prompt_20260919T063437Z`。
  SF03约219.74秒、66779tokens，SF13约262.60秒、69078tokens，各6次API全部成功，
  各一次初生+一次修补；最终仍有局部无效网格，approved=false/retained=original。
  两例无API/工具/流程错误；因无总装渲染未运行视觉审核，不算外观通过。批次隔离生效，
  个例失败没有阻断后续。旧SF07此次续跑未重跑；四checker始终关闭，独立CLIProxy保留。

- 用户明确确认删除旧展示资料：`presentation.md`和`presentation_assets/`已删除，
  不备份、不自动恢复；后续按新结果重新制作报告。local_experiment、experiments及源码未删除。
  本次未操作历史资产ZIP，但结束核对时根目录两份ZIP已不存在，不假定仍有该备份。
  下文涉及旧presentation路径的条目仅为历史记录，不代表文件仍存在。

- 按用户要求整理实验记录：两组有效研究记录保留原路径，旧502启动及已结束API探针
  原样移入`local_experiment/diagnostics/`，目录索引见`local_experiment/README.md`。
  删除30个项目内`__pycache__`及1个`.pytest_cache`，表观约2.01MiB，可自动再生；
  不删资产/候选/请求/会话证据，不动数据盘、展示ZIP、未提交代码或代理，不做git clean。
- 旧prompt实验SF07约235秒，10次API全部返回、95,869tokens，初生一次、
  修补一次，最终approved=false、retained=original，保留未通过的初稿及匹配渲染/结果。
  初稿pedestal两个连通分量且导出回读无效；修补候选PART_GEOMETRY_INVALID，未接受。
  当时启动器把EXPORTED_*统一视为共性错误而暂停；分类修复后的SF03/SF13续跑已结束，见上。

- 用户明确重新启动后，新 prompt-to-3D 实验已提交至
  `local_experiment/fixed_assembly_prompt_20260919T063437Z`，tmux
  `adsl_assembly_prompt_20260919T063437Z`，初始PID2238093。SF07先运行，实际输入和
  装配API审计通过且无共性错误后，脚本串行继续SF03、SF13；不是旧资产改造。
  沿用原case_manifest中的原始任务prompt，不提供旧源码或旧生成图；出处为原数据集任务，
  不据此额外声称官方逐例生成过这三个模型。独立CLIProxy保持运行；无额外重试/预算。
  每例一次初生、最多一次修补，固定尺度/余量/超时，四checker关闭。提交时SF07 Planner
  请求RUNNING；最终状态以各case/result.json及first.log/rest.log为准，旧502结果完整保留。

- 当前代理使用规则已按用户再次确认改为：**先独立tmux启动CLIProxy，再在另一个tmux使用API**。
  新入口`bash experiments/cliproxy_session.sh adsl_cliproxy_20260919`；当前代理PID2218166，
  `127.0.0.1:8317`监听。代理会话仅跟随日志，Ctrl-C只退出日志，不自动停服务。
  fixed_assembly_prompt/existing的launcher不再自动start/stop或注册退出清理；代理未运行就
  明确退出提示先启动。正常/失败均保留代理；仅用户明确要求时手动cliproxy_stop。
- API已在另一tmux `adsl_api_check_20260919` 实测：gpt-5.6-sol调用read_file后返回OK，
  2请求、1222tokens、6.84秒，结果已整理至`local_experiment/diagnostics/cliproxy_check_20260919/result.json`。
  6项离线生命周期测试通过，检验代理未启动/runner成功/失败均不隐式启停代理。
  上一SF07的502是上游server_is_overloaded，代理当时已成功完成Planner请求，实验退出后
  才被旧trap关闭；分离生命周期修复了自动关代理问题，不保证上游不再偶发过载。
  API修复验证本身未重跑生成；随后用户明确授权的新运行见上，原失败证据保留。

- 用户纠正当前路线为固定装配 **prompt-to-3D**，不是已有资产改造。新增
  `experiments/fixed_assembly_prompt`直接调用原生generate；原始SF07/SF03/SF13 prompt来自
  standing_fea_30/case_manifest，按历史runner无--image，因此不用旧源码/旧生成图。
  原任务与统一制造要求分开保存；1单位=1mm、余量+0.2mm，冻结目标桌120×120×120、
  椅90×80×180、书架100×32×200mm，agent自主分件。一次初生+最多一次修补，四checker关闭。
- 旧启动记录（现为`local_experiment/diagnostics/fixed_assembly_prompt_20260919T051309Z`）：20项mock通过。
  SF07 Planner成功（2件1接口），首次Coder API返回HTTP502/server_is_overloaded，无源码生成。
  实际Planner/Coder输入审计通过（源码起点空、0图片、无旧源码），程序API/几何尚未验证。
  SF03/SF13未启动；不追加预算，不自动重试。2请求，已知5859tokens；失败请求usage未知。
  代理已关闭，tmux adsl_assembly_prompt_first_20260919退出码2但保留bash。
  原改造结果单独保留，不计入此次新生成统计；生产fixed_assembly/checker逻辑未改。

- 用户确认旧 `local_experiment/` 是主动删除的；不恢复、不追索旧目录或账本。
  当前取消跨实验累计 token 记账及一亿 token 累计阻断，不再承接旧累计额度。
  单次实验 usage 统计、已有历史结果与证据保留；单候选预算、API/模型及超时配置不变。
- 原版已有资产固定装配转换（非重新生成）：新目录
  `local_experiment/fixed_assembly_existing_20260919`，固定SF07→SF03→SF13，归档adsl组，
  原源码均当前执行/8视图成功。40mm/源码单位、单侧0.2mm间隙、原尺寸冻结。
  仅新增实验编辑适配器，复用原Planner/Coder、Image/Code Critic与固定装配循环，
  每例一次初始改造+至多一次修补，四物理checker关闭。相关32测试通过/6跳过，
  新适配器13模拟测试通过。未修改普通edit或checker框架。
- 实际结果：SF07两件桌子，初稿误传dict，1次修补为TabSlot对象后接口/外观及8项
  文件一致性通过，保留attempt_0001；SF03六件椅子修补后4腿网格无效、靠背6分量，
  保留原椅子；SF13初稿同样dict错误，修补读不存在manifest触发TOOL_ERROR，保留原书架。
  全部原资产及发布哈希核对通过。原运行粗分类与后验分类分别保留result/post_run_audit。
  SF13是流程缺陷：fixed_assembly.py反馈无条件给出未生成的manifest路径。只补本实验分类，
  未修生产反馈/工具、未追加调用；后续先解决缺失报告引用，不增加本例预算。
- 本轮CLIProxy实际25请求、284,452tokens（SF13中断修补也计入）。历史运行曾因旧local_experiment/
  原API账本缺失，按此前memory最后9,512,660累计charge承接，明细缺失明确标记。
  当时账本 `local_experiment/fixed_assembly_existing_20260919/budget/cliproxy_token_budget.json`，
  累计9,797,112/100,000,000；新增请求无未结项。这是历史记录，不再作为当前累计阻断依据。代理已关闭；tmux
  `adsl_assembly_existing_20260919`已退出任务且保留bash。报告在本轮目录REPORT.md，
  含原图/总装/拆分图、失败分类和成本CSV。未验证实物固定；本轮改动未提交/推送。
- 注意：旧reports/与原local_experiment/目前缺失/被删除，以下历史路径仅是历史记录，
  不应当作当前仍存在的证据。保留用户删除，不恢复或追索旧目录。

- 固定装配导出一致性已在 `95edef0` 上最小修复：只改 export_assembly 的生产逻辑，
  同一打印件的最终局部网格/面材质复用于 STL 和独立/总装/拆分 GLB，展示不重做 CSG。
  最终落盘文件回读、统一坐标及单位，沿用原对称体积差容差；不一致进入原 FAIL gate。
  没有更改 attach_part、connect、余量、四 checker 或候选机制，也无跨候选网格缓存。
- 原三件源码重导出 12 项差全0，原两件回归最大差约0.000022 mm³，均通过；正常
  +0.2mm间隙保留。错移一脚1mm被现有接口/干涉规则拒绝（44.71465mm³），不加gap工具。
  36项相关测试通过、6项历史真实几何变体跳过。原资产/历史失败保留，新证据见
  `reports/fixed_assembly_mesh_reuse_20260919.md`，本轮代码尚未提交/推送。
- 用户追加允许 CLIProxy 单例：`local_experiment/fixed_assembly_mesh_reuse_20260919/cliproxy_smoke`。
  初稿误用connect(id=...)；1/2次修补改两处位置参数后通过，retained=attempt_0001。
  这是调用错误恢复，不是几何修复证明。8次请求、65,359tokens；共用原一亿token台账，
  累计9,512,660、无未结请求。代理已关闭，tmux任务退出码0且交互Bash保留。

- 基于 `95edef0` 修正 tmux 启动层：`experiments/tmux_session.sh` 先创建交互
  Bash，任务在前台子 shell 执行；完成/失败/Ctrl-C 后保留提示符，记录真实退出码。
  不再把 remain-on-exit 留下的 dead pane 当成交互终端。新实验统一使用此入口，
  原日志/pipefail 保留；GPU control 的 wait/status 区分任务结束和 shell 仍存在。
  隔离 tmux 实测 7 passed（正常/失败、Ctrl-C、任务内 exit、参数引用、tee、会话重名）；
  测试 server 均已清理，相关 7 个 shell 脚本语法检查通过。
- 单卡手动入口 `bash experiments/gpu_render_queue/allocate_shell.sh SESSION`：
  分配后进入 `bash -i`，不启动 keeper/实验。远端 shell 退出仍释放卡，登录节点
  shell 保留；不要将登录节点提示符误认为仍持有 GPU。未实际申请卡或改动现有会话。
  keeper 及其提交逻辑未改；本轮代码尚未提交/推送。

- 本轮基于 master `841afc4`，最小修复空源码 resume 的完整 fixed_assembly
  配置传递，规划/导出前拒绝打印件保留名 scene/exploded；77 项测试通过、6 项
  真实几何变体跳过。本摘要随本轮修复提交；实验大文件与无关修改不纳入提交。
- StepCode 三件式顶板+重复支脚已完成：`local_experiment/fixed_assembly_three_parts_20260918`。
  初次生成 1 次、修补 0/2 次，29,738 tokens；接口验收与 Image Critic 通过，
  retained=original，代理已关闭。未验证真实修补能力，不运行四个物理 checker。
- **9月18日历史后验导出一致性失败**：三 STL 均闭合单实体、零退化面、与独立 GLB 一致，
  但两支脚在总装 GLB 的榫头导入斜面各差约 2.8586 mm³（容差 0.8281 mm³）。
  已保存 `saved_export_verification.json`；发布哈希匹配，并非选错 retained。
  当时总装重新求值 CSG，具体 Boolean/三角化根因未定；历史文件不改写，新修复见上。
  不将流程 approved=true 当成完整一致性通过。见 `reports/fixed_assembly_three_parts_20260918.md`。

- 固定装配 v1 已在 `6824a14` 上实现，随本次代码提交同步。公共 `FixedAssembly` /
  `InterfaceFrame` / `TabSlot` 成对生成真实榫槽；显式打印件，冻结毫米换算、配合余量
  和最终尺寸。CLI 为 `--fixed-assembly-config`，不借用联合 checker/过悬模式。
- 固定装配四个物理 checker 均关闭，仅验证接口几何，保留 Image/Code Critic。
  79 项相关测试已通过（含 6 个真实布尔/导出变体）；正间隙不是插接保持力证明。
  本次 StepCode 单例在 `local_experiment/fixed_assembly_v1_20260917/stepcode_smoke`，
  tmux `adsl_fixed_assembly_v1_20260917` 已正常退出；初次生成即通过接口几何与 Image
  Critic，0/2 次修补，4 次模型请求、29,599 token。本次代理已关闭；有两个 STL、
  装配 GLB、8 张装配图与 2 张拆解图，哈希一致性已核对。
  证据以 `reports/fixed_assembly_v1.md` 为准；此次未实际验证 agent 修补效果或实物固定。
- 当前工作分支为 master。本次同步在 d66319c 之上提交联合 checker 的后续修复，
  包括有效 request 传递、独立 checker 与 FEA 依赖隔离、拓扑退步保护、恢复轮次上限、
  未验证工具记录和程序错误停止；不改几何内核、物理阈值或旧过悬专项规则。
- 推送前相关轻量回归为 166 passed；测试不调用真实 API、Blender 或 FEA。
- `local_experiment/prompt8_joint_20260915T072057Z` 已完成 8/8，正常退出。
  本轮 Ours 从 prompt 独立生成，历史 aDSL 不重生成；案例为
  SF01、SF03、SF05、SF07、SF11、SF13、SF21、SF27，不包含 SF20。
- 同案例 aDSL/Ours：topology 为 5/8、6/8，standing 均 8/8，FEA 和三项联合
  通过均为 0/8。SF11 的 approved=true 不代表 FEA 通过：其 FEA 是 NEEDS_SPEC。
- Ours 过悬前后可比较 5/8：最终面积减少 0 例、不变 3 例、增加 2 例；另 3 例未验证。
  SF11、SF27 因拓扑改善接受了过悬增加，这是联合模式硬约束优先的取舍。
- 本轮使用已有新 API 配置与共享一亿 token 上限；不在记忆文件记录密钥。
  实验模型、日志、压缩包留在本地，不随代码推送。
- 下方带日期的实验及进程信息是历史快照；当前状态以上述摘要为准。

## 当前超时、SF04 与反馈精简修复

- 最终离线评估已复用 `adsl-agents/checkers.py` 和修复循环的 checker specs；topology 沿用 900 秒，其他 checker 保留各自原配置，不再逐个继承 case 的 6 小时上限。导出与生成超时未改。
- Checker 父进程使用独立进程组 TERM → KILL 和有界回收，stdout/stderr 直接落文件，避免清理后又无限等待管道。OCC 布尔操作即时记录部件、操作数、开始/完成事件。
- 有明确未完成 OCC 操作的超时记录 `ERROR / GEOMETRY_PREPROCESS_TIMEOUT`，不是“模型断开”；依赖其几何预处理的 FEA 记录 `INDETERMINATE / GEOMETRY_PREPROCESS_UNAVAILABLE`，standing 与后续样本继续。
- SF04 底框没有发现重复实体或零尺寸输入。23 实体混合合并很慢；仅对该底框验证“方块 → 圆柱 → 球”的分阶段 UNION，原语和参数不变。不能将此原因等同 SF01 重复 cap，也不能扩展为全局 CSG 规则。
- 局部候选、限时诊断、前后 STL 与形状验证在 `local_experiment/sf04_fuse_diagnostic/`。原始 SF04 workspace 未覆盖，未宣称整把椅子已通过 topology/standing/FEA。
- 接收方证据分工已收敛：Engineering Critic 默认接收所有 checker 状态和未通过 finding 的关键指标/定位/关联；候选 Coder 只接收当前 RepairProposal.finding_ids 关联证据与源码位置，不再发送全部 baseline model_dump。完整报告留文件，read_file 支持 json_pointer 按字段选取，以及 offset/max_chars 分页（每次最多 12000 正文字符），不自动整份读取。
- SF03 round_02 离线复核：工程反馈从上一版 66362 字符降至 17838；候选 Coder 证据从 125957 降至 12060（约 90.4%），只含该方案关联的 4 条 topology finding。不是实测 API 提速或 token 降幅；历史会话和多次读取仍可能累积上下文，没有新增全局 token 调度/清理系统。
- 本轮不改模型、图片数量、critic、轮数、会话恢复策略、公共缩放或 MuJoCo 参数；未调用 StepCode、未启动或重启批量实验。HTTP 400 的上游具体原因仍未证实。
- 验证和边界说明见 `reports/sf04_timeout_feedback_20260912.md`。

## 当前分支与目标

- 仓库：/vepfs_default/chanxueyan/lhp/lms/aDSL
- 当前分支：master；历史 1337660 是最终实体连通性修复提交，不是当前 HEAD。
- 原始公开代码基线：e1742e3。
- 当前目标：汇总已结束的 8 例 prompt-to-3D 联合 checker 实验，与历史 aDSL 比较；未验证不计 PASS，也不冒充物理不达标。
- 当前批次使用已有新 API 的 gpt-5.6-sol；StepCode 与 Codex CLI transport 仍保留，但不是这批实际模型后端。
- 本轮是 same-source/different-sample targeted subset，不是论文隐藏 200 prompts 的精确复现，也不是工程安全认证。
- 不向远程 push，除非用户明确要求。

## 本机路径与存储规则

- Python 环境：/vepfs_default/chanxueyan/lhp/lms/envs/adsl
- 当前活跃 workspace/SQLite：仓库内 local_experiment/，避免共享盘 SQLite I/O/SIGBUS 问题；仅本地使用，不提交。
- 审计 case 根：temp/audit_20260830/
- 旧 temp 清理前清单：reports/adsl_audit_20260830/prior_temp_inventory.json
- 保留旧证据：temp/prior_evidence/
- GPU 手册：gpu_server_operation_manual.md；仅本地 exclude，禁止提交。
- /jiigan-hp 已恢复并用于不可变大文件归档、GPU render queue 与 keeper 日志；使用前仍必须以 findmnt -T 验证目标为 /jiigan-hp 且 FSTYPE 匹配 hpvs_fs*，不能只凭可 cd 判断健康。
- 当前旧 12-case CPU 批次按用户要求暂停，未恢复。2026-09-12 06:57 UTC 重新提交单 A800 请求：tmux `adsl_gpu_keeper_20260912T065718Z`，日志目录 `/jiigan-hp/lms/aDSL/experiment/gpu_keeper/20260912T065718Z/`；已确认 `Worker pending`，无 allocation timeout，分配后前台运行现有 queue-aware keeper（ttrv PyTorch）。现有 mosalloc 双卡会话未操作。
- 新提交入口将 pending 同时写入 tmux 与 launch.log，正常/可捕获退出写 LAUNCH.exit_code、LAUNCH_FINISHED，并为该窗口启用 remain-on-exit；不自动重提不明状态的申请。旧 `20260911T091815Z` 请求已消失，日志止于 9 月 11 日 17:43:47 UTC，退出原因无记录。GPU 仍通过 tmux 内的 volc ml_devinstance launch 申请，不使用 Slurm。

## 用户级依赖规则

本机是 root，但本项目缺库时不能污染系统环境：

- 用户级 runtime 放在 /vepfs_default/chanxueyan/lhp/lms 下。
- 需要持久环境变量时写 /vepfs_default/chanxueyan/lhp/lms/.bashrc。
- 运行前 source /vepfs_default/chanxueyan/lhp/lms/.bashrc。
- 下载遇到网络问题可使用 127.0.0.1:7892 代理；直连能用时无需强制代理。
- 不把 API key、auth 文件或 credential 输出写入 repo、temp 日志、报告或 Git。

## Native Stepcode API 适配

当前 profile：

- adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml
- model：gpt-5.6-sol
- API：OpenAI-compatible Responses
- credential.stepcode：true
- trust_env：false，防止 localhost gateway 被代理劫持

credential 行为：

- adsl-agents/utils/config.py 以 argv-only subprocess 调用 stepcode config get apiKey。
- adsl-run 未传 --model-config 时默认使用 Stepcode profile；原 OpenRouter profile 仍可显式选择。
- 只在内存提取唯一 ak-/sk- token。
- command 缺失、超时、非零退出或模糊输出全部 fail closed。
- ModelProfile repr 隐藏 api_key。
- runtime_config.json 只保存 credential_source=stepcode 等非秘密字段。
- 当前分支没有 Codex provider/profile/transport。

## 原项目 Eevee 与 CPU 环境

原项目默认渲染就是：

- BLENDER_EEVEE
- 1024×1024
- 256 samples
- 8 views
- 15° elevation

它不要求 GPU。先前失败是本机缺少 libEGL.so.1。

已完成用户级修复：

- EGL/Mesa deb 只解压到 /vepfs_default/chanxueyan/lhp/lms/adsl_runtime/egl
- .bashrc 已加入 ADSL_EGL_ROOT、LD_LIBRARY_PATH、LIBGL_DRIVERS_PATH、__EGL_VENDOR_LIBRARY_DIRS
- 修改前备份：/vepfs_default/chanxueyan/lhp/lms/.bashrc.before-adsl-egl-20260830
- bash -n 已通过
- bpy 4.0.0 使用 Mesa surfaceless EGL 在 CPU 上成功渲染
- 日志先出现 EGL_NOT_INITIALIZED，随后出现 fallback to surfaceless EGL rendering 属于预期 fallback；以最终 exit code 和 PNG 为准

本轮实验使用 env override：

- ADSL_RENDER_ENGINE=BLENDER_EEVEE
- ADSL_RENDER_WIDTH=512
- ADSL_RENDER_HEIGHT=512
- ADSL_RENDER_SAMPLES=16
- ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS=900

项目默认值仍保持 Eevee/1024/256/300 秒。

/tmp/adsl_site_cache/bpy 是本轮加速用的易失缓存，机器或 /tmp 清理后可消失，不能当持久依赖。

## 已实现的运行兼容修复

- Coder prompt 的 import 修正为 from adsl.core import *，与论文和 DSL 文档一致。
- render.py 支持 engine/width/height/samples 环境覆盖，默认不变。
- execution.py 子进程使用独立 process group；timeout/中断时有界 TERM → KILL。
- execution.py 直接执行同目录 asset_executor.py，不再用 python -m adsl.agents.utils.asset_executor，避免资产子进程无意义导入整个 Agent/OpenAI stack。
- 同一 source smoke 在 /tmp bpy cache 下从 308.634 s 降到 3.727 s。
- service.py 发布 scene.joint_states.json 和 render/meta.json，并记录有效 runtime 配置。
- .gitignore 不再忽略整个 tests/。
- tests 覆盖 config、credential 安全、execution cleanup/direct executor、prompt、publish 和 service config。

## 2026-08-30 诊断审计

Manifest：

- experiments/adsl_audit_20260830/case_manifest.json
- 12 个逻辑 case
- 15 次 invocation
- retry_policy=none；每个 invocation 最多启动一次
- Agent 内部 Debugger/repair 是被测流程的一部分

case 范围：

- exact count/contact chair
- curved bookshelf
- 16-spoke radial wheel
- hollow mug
- patterned desk
- pure image table
- pure image articulated nightstand
- two articulated text cases
- localized chair edit
- living-room relational scene
- motorcycle base/edit/scratch memory comparison

总体：

- 15/15 最终生成 GLB/URDF/PNG
- 14/15 被当前 workflow 标记 approved
- 118 model requests
- 836,614 total tokens
- invocation elapsed 求和 4,226.324 s
- 3 次首轮执行失败，全部为同类非法数字词语法，随后被 Debugger 修复
- session DB 合计 53,882,880 bytes，原始内容有 195 次 data:image occurrence
- 未申请 GPU

详细报告：

- reports/adsl_audit_20260830/README.md
- reports/adsl_audit_20260830/paper_claim_matrix.md
- reports/adsl_audit_20260830/code_agent_findings.md
- reports/adsl_audit_20260830/generated_output_findings.md
- reports/adsl_audit_20260830/case_results.json
- reports/adsl_audit_20260830/case_results.md

## 关键正面结果

- T01：4 条等长腿、3 条背横档、无扶手满足；grid/stack 对 repeated structure 有效。
- T03：源码明确用 radial_shapes 生成 16 根辐条并接触 hub/rim，是关系 DSL 的强证据。
- E01：chair 3→5 slats，只 +6/-6 行，其他几何保持，支持 localized edit。
- I02/A01/A02：URDF 都有精确 3 个 movable joints，语义名、轴和 limits 基本合理。
- pose probe 将三组关节设到 85% 行程后重新导出/渲染，抽屉、门、knob/lever 都实际移动且保持连接。
- T02/A02 的真实布局缺陷能在下一轮被 Critic/repair 修复，说明 loop 有实际价值。

pose probe：

- experiments/adsl_audit_20260830/render_pose_probe.py
- temp/audit_20260830/pose_probes/
- I02 drawers：0.2975
- A01 doors：1.632 rad；drawer：0.3825
- A02 knobs：0.455 rad；lever：0.5

## 关键流程/代码缺陷

1. asset_executor.py 用 runpy.run_path 不受限执行模型源码；子进程不是安全 sandbox。当前最高优先级。
2. service.py 最后一轮执行成功后直接 round_limit_after_execution，不运行 Critic；max_rounds=1 恒定 critic_skipped。
3. Image Critic approved 时跳过 Code Critic，与论文“Code Critic final adjudicator”不一致。
4. Planner relations/checklist 只是 list[str]，没有 typed/executable constraints。
5. 没有 deterministic count/contact/alignment/collision/topology/joint-motion gate。
6. articulated 主流程只渲染 Joint.initial；Code Critic prompt 要求非零 pose，但没有对应工具或图。
7. 固定 8×15° object orbit 看不到 mug interior，并让 living-room 约 3/8 视图被墙遮挡。
8. Code Critic prompt 要求 MUST TRUST THE CODE LOGIC，可能把源码意图误当最终几何/视觉事实。
9. 没有 AST/compile preflight；I01、I02、M01 scratch 首轮出现 0. thirty / 0. forty。
10. bounds.py 明确存在 transformed/boolean AABB 近似；T02 已触发 floating books。
11. SQLite selective context 不会物理删除旧 render data URL。
12. 生成代码仍大量使用绝对数字；复杂外观 primitive/flat-color 感明显。
13. 公开主流程没有论文的 SpaceControl/Trellis 高保真阶段。
14. ObjectWorkflow 单体且相机/视图/URDF 硬编码，难做 verifier 注入和 ablation。
15. 缺 Draco 时 Blender 打 ERROR 但 GLB 仍成功，日志容易误判。

## 生成物主要不足

- T02：curved nosing 视觉不明显；四个 nosing CSG 非 watertight，共 41 zero-area faces。
- T04：源码有真实 hollow vessel，但八视图看起来仍接近封顶实心；C-handle 粗糙且 union 非 watertight。
- T05：黑白条纹用 13 个薄 Cube + boolean 模拟，不是真正 texture/material。
- I01/I02：语义可辨识，但比例、腿型、把手、木纹、接缝和曲面细节明显弱于参考。
- A01：原八视图全关闭；motion 是审计 pose probe 才验证的。
- A02：关节正确，但外形更像粗糙工业塔，faucet 识别度一般。
- S01：空间关系大致成立，场景相机严重被墙遮挡。
- M01 edit：保存基体且 wall time 比 scratch 少，但 tokens/requests 更多、源码膨胀、视觉 cyberpunk 更弱。
- 拓扑统计必须先 merge_vertices(merge_tex=True, merge_norm=True)，否则 glTF hard-normal/UV 顶点拆分会把普通 cube 误报为非 watertight。

## 论文复现边界

论文使用：

- Gemini 3 Pro，temperature=1
- R=10
- 1024×1024
- 200 text prompts
- Toys4K 30 image cases
- 120 ablation prompts
- CLIP/VQA/FID/Execution Success
- baselines 和 38 人 user study
- SpaceControl/Trellis high-fidelity application

本轮均未完整复现，因此不能宣称论文 SOTA、100% execution success、CLIP/VQA/FID 或人类偏好已验证。

## 常用复查命令

先加载用户级 Eevee 库：

~~~bash
source /vepfs_default/chanxueyan/lhp/lms/.bashrc
~~~

重新分析既有 case：

~~~bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python   experiments/adsl_audit_20260830/analyze_cases.py
~~~

测试：

~~~bash
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q
~~~

Git 提交前：

- git diff --check
- py_compile 修改过的 Python 文件
- 全量 pytest
- secret scan，不能输出或提交真实 ak-/sk- token
- 确认 temp/ 和 gpu_server_operation_manual.md 不在 staged files
- 只本地 commit，不 push

## 建议的下一阶段修复顺序

1. 生成代码 sandbox + AST allowlist。
2. 修复 round off-by-one；每次成功 execution 都执行固定 verifier chain。
3. 增加 syntax preflight。
4. typed constraints + deterministic count/contact/topology/collision verifier。
5. joint lower/mid/upper pose sampling。
6. container/scene/articulation 的 asset-aware camera policy。
7. session 图片 artifact 外置与 prune/compact。
8. texture/material 或明确接入 high-fidelity generator。
9. 拆分 ObjectWorkflow 并加入 benchmark/ablation runner。

## 2026-08-31 Eevee / GPU 渲染核查

证据边界：

- 论文 Appendix A 只明确 8 个视角、45° 方位间隔、固定 15° 仰角、1024×1024、neutral materials/environment lighting；论文全文没有出现 Eevee 或 GPU，也没有报告 GPU 型号。
- 官方 sig-pku/aDSL 源码在 adsl-core/tools/render.py 中硬编码 init_render_engine("BLENDER_EEVEE", ...)，但没有 CUDA、OptiX 或 CPU/GPU device 选择。
- 因此可以确定官方渲染引擎是 Blender Eevee；“作者实验明确指定 GPU Eevee”不能由论文直接证明。标准 Eevee 的常规硬件路径是图形 GPU，Mesa software surfaceless 是本机登录节点的兼容 fallback。

本机适配与实测：

- /vepfs_default/chanxueyan/lhp/lms/.bashrc 已改为自动分流：检测到真实 NVIDIA GPU 行且存在 10_nvidia.json 时，保留用户态 GLVND libEGL loader 并选择 NVIDIA EGL vendor；否则选择 Mesa DRI/vendor。
- 备份：/vepfs_default/chanxueyan/lhp/lms/.bashrc.before-adsl-gpu-eevee-20260831
- 不能只用 nvidia-smi -L 的退出码判断 GPU：本站登录节点在 0 张 GPU 时也可能返回 0；必须匹配 ^GPU 行。
- 第一次 A800 smoke 误把整个用户态 EGL 路径移除，因 GPU image 缺少 libEGL.so.1 而 abort。正确做法是保留 user-local GLVND loader，只切换 vendor 到 /usr/share/glvnd/egl_vendor.d/10_nvidia.json。
- 修复后单卡 A800（driver 535.129.03）按 BLENDER_EEVEE、8 views、15°、1024×1024、256 samples 成功生成 8/8 PNG 和 meta.json，成功哨兵及 manager RC 均为 0。
- 可复查 worker：experiments/adsl_gpu_eevee_smoke.sh
- 可重建证据：temp/gpu_eevee_smoke_20260831_retry/ 与 temp/gpu_eevee_smoke_20260831_retry.log
- Eevee 单进程使用一张图形 GPU；双卡不会自动加速一个 render job。多个独立 case 并发时才考虑双卡。
- 后续若要求论文常规硬件路径，必须在按手册申请的 volc GPU worker 内 source lms/.bashrc 后运行；直接在登录节点运行仍会走 Mesa CPU fallback。

同条件速度基准（同一 GLB，BLENDER_EEVEE，8 views，15°，1024×1024，256 samples）：

- NVIDIA A800 的 Blender frame time：首视角 8.73 s（含首次 GPU/scene 初始化），后续 0.47–0.85 s；8 视角日志合计 12.44 s。
- Mesa CPU 首视角实测 96.76 s；第二视角进行到 151/256 samples 时为 55.15 s，速率与首视角一致。为避免额外约 11 分钟占用，得到完整首视角后终止其余视角。
- 按实测速率估算 CPU 8 视角约 774 s（12.9 min），与 GPU 12.44 s 相比，纯渲染阶段约 62×；warm-view 约 100–200×。具体倍数会随几何、材质、分辨率和 samples 改变。
- 新申请 volc worker 还有排队、容器和 bpy 初始化开销，所以一次性小任务的端到端加速低于 62×；批量 case 或复用 GPU worker 更划算。
- nvidia-smi pmon 在该容器中没有显示 Eevee 图形进程且产生无效日志/尾部等待，已从 smoke worker 移除；NVIDIA backend 通过强制 10_nvidia.json 且渲染成功来验证。

## 2026-08-31 持久化串行 GPU 渲染队列

目标：多个 agent/case 可以同时提交，但 Blender 渲染必须严格串行；不再为每个 case 单独创建/关闭 tmux 和申请 volc worker。

实现：

- 队列核心：adsl-core/tools/gpu_render_queue.py，只依赖 Python 标准库。
- 唯一接入点：adsl-agents/utils/asset_executor.py。text、image、articulated、edit 和每一轮 critic 前的 render 都经过此处。
- 设置 ADSL_GPU_RENDER_QUEUE 后只走队列；worker 不在线或 job 失败会显式失败，不回退到登录节点 Mesa CPU。
- 提交端通过原子 JSON 文件进入 FIFO；worker 用排他锁保证同一 queue 只有一个消费者，固定 concurrency=1。
- 每个 job 都启动一个全新的前台 Blender 子进程；必须等其完整退出后才检查下一项。
- worker 记录启动显存基线。Blender 退出后显存须在 60 s 内回到 baseline + 256 MiB；否则当前 job 失败、worker 状态变为 blocked，并保留后续 pending job，绝不继续提交导致 OOM。
- 单 job 默认 hard timeout 900 s；杀整个进程组；不自动 retry。
- 客户端等待默认 3600 s，asset executor 外层默认 3660 s；执行 manifest 记录 render_backend 和 render_job_id。
- worker restart 会把遗留 running job 标为 failed，不会猜测性重跑。
- graceful stop 只让当前 Blender 完成，pending job 留待下一次 worker；不可把 kill tmux 当正常停止方式。

控制脚本：experiments/gpu_render_queue/

- activate.sh：给 agent/service 注入 queue、Eevee、1024×1024、256 samples 和 timeout。
- control.sh：start / wait / status / stop。
- manager.sh：普通登录节点 tmux 内只申请一次 volc，默认单卡 A800，12 h allocation hard timeout。
- worker.sh：GPU worker 内运行串行 queue daemon；默认空闲 2 h 自动退出释放申请。
- README.md：完整操作与故障 marker。

卡数决策：当前 Eevee 路径单 job 使用一张图形 GPU，且为了 OOM 隔离固定单通道，因此默认 ml.pni2l.3xlarge 单卡 A800。双卡 flavor 不会让同一个 case 更快；除非以后明确实现两个彼此隔离的 queue lane，否则不申请双卡。

验证：

- 修改过的 Python 已通过 py_compile；四个 shell 脚本已通过 bash -n。
- 纯队列测试在 /tmp 隔离副本通过 3/3：第二个 job 在第一个进程退出后才开始、hard timeout 不重试、显存不回落时 worker fail-closed 且第二项保持 pending。

## 2026-09-02 aDSL vs TRELLIS 粗略 CLIP pilot

目的与证据边界：

- 这里只回答“当前 7 个既有 case 的 CLIP 大概效果是否接近 TRELLIS”，不是论文完整复现。
- text/image 两个 track 分开统计，不能混合；±2 CLIP points 只是工程观察线，不是统计等价检验。
- 没有跑论文的 200 text prompts、30 Toys4K、VQA/FID、用户研究或多随机种子。
- 最终 verification_status=ANALYZED 只适用于本地 7-case pilot。

实现入口：

- experiments/clip_trellis_pilot/build_manifest.py
- experiments/clip_trellis_pilot/prepare_toys4k_subset.py
- experiments/clip_trellis_pilot/run_trellis.py
- experiments/clip_trellis_pilot/render_batch.py
- experiments/clip_trellis_pilot/score_clip.py
- experiments/clip_trellis_pilot/gpu_worker.sh
- experiments/clip_trellis_pilot/launch.sh
- tests/test_clip_trellis_pilot.py

数据与环境：

- run root：/jiigan-hp/lms/aDSL/experiment/clip_trellis_pilot_20260902（约 1.3 GiB）。
- manifest：上述目录的 manifest.json；CLIP 结果在 clip/scores.json、scores.csv、report.md。
- 干净 TRELLIS baseline：上述目录 baseline/TRELLIS-6b0d64751ad54d9c32d7b05fec482eb29178f56f。
- TRELLIS commit：6b0d64751ad54d9c32d7b05fec482eb29178f56f；没有使用 /jiigan-hp/TRELLIS 里的工作树源码。
- text 权重：TRELLIS-text-xlarge-original；image 权重：TRELLIS-image-large；CLIP：本地 clip-vit-large-patch14。
- trellis-eval 环境实际是指向 /vepfs_default/chanxueyan/hujingyu/envs/trellis_pami 的 symlink；直接复制 60k+ 文件到 lms 太慢，未完成副本保留为 trellis-eval.incomplete-20260902T0415Z。
- rembg 的 u2net.onnx 缓存在 /jiigan-hp/lms/aDSL/experiment/model_cache/rembg/u2net.onnx，MD5=60024c5c889badc19c04ad937298a77b。

Toys4K 最小下载：

- 通过 127.0.0.1:7892 只下载约 3 MB 的公开 Toys4k.csv 元数据到数据盘。
- 元数据 SHA256=8609e8d3cb9affd85d0d443de59831bf241a716554d8f140e4eb209b2bde26b8。
- seed=260817975 冻结 10 个不同类别：banana、bottle、cells_battery、chair、key、motorcycle、pear、pencil、phone、sandwich。
- 官方 raw Toys4K 需要授权表单，因此没有下载完整 archive，也没有把仅有 metadata 的对象冒充已完成 image-to-3D case。
- 工具已经支持在以后拿到授权 archive 时只抽取这 10 个成员。

锁定协议：

- 5 个 text case（T01-T05）和 2 个 image case（I01-I02），复用原始 prompt/input image。
- TRELLIS 每 case seed=1、一次生成、不做 prompt expansion、不自动 retry。
- 两侧用同一个项目 Eevee renderer：8 views、45° 方位间隔、15° 仰角、1024×1024、256 samples、neutral material、同一 AABB normalization。
- manifest 中 background=white 指 world color 设置；项目 Filmic + exposure=-0.8 后实际角像素是 RGB(134,134,134)，即输出视觉为灰背景。两侧仍公平，但不能描述为纯白像素背景。
- CLIP 指标为 reference 与 8 个 view 的 cosine×100 后取 mean；失败按 0 进入主聚合。

正式运行结果：

- 单卡 A800 worker-hldpl；TRELLIS、Blender、CLIP 三阶段严格串行，三个 exit code 均为 0，结束后 worker 自动释放。
- 7/7 TRELLIS GLB 成功；14/14 aDSL/TRELLIS render group 成功，每组 8 张图。
- text：aDSL 24.171，TRELLIS 25.201，delta=-1.031；按预设 ±2 工程线属于 close_within_margin。
- image：aDSL 66.822，TRELLIS 70.719，delta=-3.897；TRELLIS 更高。
- text 逐 case delta（aDSL−TRELLIS）：T01 -0.714、T02 -1.360、T03 -1.604、T04 +0.125、T05 -1.600。
- image 逐 case delta：I01 -4.385、I02 -3.409；两个 case 方向一致。
- 乱序 reference 对照均值：text aDSL/TRELLIS=15.994/16.652，低于 matched 24.171/25.201；image=60.150/62.437，低于 matched 66.822/70.719。
- image self-control 约 100，说明 pipeline 数值正常；但 image shuffled gap 较小，也说明这两个同属家具的极小样本区分力有限。

视觉复核与结论：

- text 轨可以说“CLIP 粗略接近”，不能说 aDSL 在总体质量上等价 TRELLIS。
- T01/T03 体现 aDSL 的程序化关系优势：四腿/三横档、16 spokes 等精确结构清晰；CLIP 本身并不可靠计数。
- T05 是典型反例：TRELLIS 视觉更像常见办公桌，但出现侧柜/额外结构，违背“exactly one drawer + two side panels”；aDSL 更守结构约束，CLIP 却仍偏向 TRELLIS。
- I01/I02 输入图并排复核中，TRELLIS 的桌腿比例、柜体轮廓、抽屉和接缝更接近参考；aDSL 更 primitive、比例和细节更弱，与 image CLIP 差距一致。
- T04 两边都是可识别 mug，aDSL 略高；但 hollow interior、wall thickness、watertightness 不能由 CLIP 证明。
- 因此当前证据支持：aDSL 在结构明确的 text cases 上能用程序化几何取得接近的 CLIP，同时更易满足 exact constraints；在纯图像外观复原上明显落后 TRELLIS。

本次运行踩坑：

- GPU 容器中 /jiigan-hp 的 fstype 是 hpvs_fs.fsx-tos，不是登录节点显示的 hpvs_fs；preflight 必须接受 hpvs_fs*，否则会误报挂载盘不可用。
- rembg 默认会从 GitHub 下载 u2net 并 30 s timeout；必须提前经 7892 下载到数据盘并设置 U2NET_HOME。
- aria2c 对大单文件用 8 connections 明显比单连接 curl 稳定；下载完成必须校验官方 MD5。
- bash 的 ERR trap 会在预期的 partial stage return code 上提前终止；需用 if command; then rc=0; else rc=$?; fi 捕获，之后再决定是否继续 render/score。
- 数据盘不可靠保留 executable bit；baseline clone 设置 core.fileMode=false，脚本通过 bash 显式运行。
- apply_patch/view_image 的 bwrap 在本机因 unprivileged user namespace 禁用而失败；代码修改只能在明确 diff 审核后临时用 git apply，图片诊断通过 ImageMagick + base64 读取。

## 2026-09-02 实验归档入口

- 总入口：/jiigan-hp/lms/aDSL/experiment/clip_trellis_pilot_20260902/EXPERIMENT.md。
- 仓库副本：reports/adsl_experiment_archive_20260902.md。
- 总入口同时归档第一阶段 12 个逻辑 case/15 次调用的 Agent、代码、生成物缺陷，以及第二阶段 7-case TRELLIS/CLIP 对比。
- 第一阶段详细 Markdown 原文位于数据盘 archive/initial_case_audit_20260830/，包含 overview、agent logic、code findings、generated output findings、paper claim matrix 和 case results。
- 第二阶段原始分数、逐视角路径和对照实验仍以 clip/scores.json 为机器可读真值。
- 数据盘副本已逐文件 cmp 验证与仓库报告一致。

## 2026-09-03 Final Standing Stability（01）验证

- 范围：只验证成品站立稳定性；未实现或接入 checker，未修改任何现有 GLB、URDF 或 source.py，也未进入 progressive build、FEA 或 support 分析。
- 实验入口：experiments/final_standing_stability/analyze.py；说明：experiments/final_standing_stability/README.md；测试：tests/test_final_standing_stability.py。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/01_final_standing_stability/；机器可读真值为 results.json 和 summary.csv；仓库报告副本为 analysis_archive/final_standing_stability_analysis.md。
- 输入：audit_20260830 的 15 个 case、19 个姿态。运行前后 45 个 scene.glb、scene.urdf、source.py 的 SHA256 清单逐字一致。
- 方法：URDF collision geometry 的最低接触凸包；均匀实体与均匀薄壳两种质心；带符号稳定裕度、归一化裕度、最小倾倒角和 16 向临界水平加速度；MuJoCo 自由沉降、16 向阶梯水平力及 0.05 m/s 小冲量交叉复核。
- MuJoCo 3.12.0 使用 CPU，不申请 GPU；因代码盘 pip 安装触发 quota，额外 runtime 位于 /jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310。lms/.bashrc 定义 ADSL_MUJOCO_PYTHONPATH 和 adsl_mujoco_python，不全局覆盖 PYTHONPATH；修改前备份为 .bashrc.before-adsl-mujoco-20260903。
- 覆盖：排除 S01 后，18/18 姿态都有 MuJoCo 结果。初始姿态分布为 2 UNSTABLE、6 STABLE_CANDIDATE、6 INDETERMINATE、1 EXCLUDED。
- 用户倾倒判据（2026-09-03 更新）：MuJoCo 自由沉降、水平力和小冲量均以运行期间最大倾斜角严格大于 25° 为倒下；恰好 25° 不算。自由沉降和冲量观察窗口均为 5 秒。
- 25° 正式结果：18/18 非排除姿态在自由沉降中都未倒下；最大沉降倾角是 A01 initial 的 0.639°。因此结构 verdict 与自然倒下结果必须分别解读。
- 结构筛查失败：A01-cabinet 的最低平面实体/壳体质心均在初始支撑区外，但 MuJoCo 中仅摇摆 0.639° 后形成额外接触，按 25° 规则没有倒下；T03-radial-wheel 支撑面积为 0，在完全对称自由沉降下未自行倒，但约 0.2×自重水平力会使其超过 25°。
- 稳定候选：A02、E01、I01、I02、T01-main、T01-one-round-control；I02 三抽屉同时打开到 85% 上限后壳体裕度降至 0.0257、MuJoCo 最小观测倾倒力降至 0.2 倍自重，应视为最弱稳定候选。
- 不确定：三个 motorcycle 的约 0.44–0.46 平底支撑可能是网格离散化伪影；T02、T04、T05 与 M01 scratch 缺少可靠实体质心。MuJoCo 凸包代理不能消除该不确定性。
- 退化 mesh 处理：少于 4 顶点或共面的零体积分量不送入 MuJoCo，显式丢弃并计数；M01 scratch/T02/T05 分别为 9/14/2。第一次包含三项代理构建失败的输出完整保留为 01_final_standing_stability_attempt_01_degenerate_mesh_failures。
- 旧的“峰值 30° 或最终 15°”正式输出保留为 01_final_standing_stability_threshold30_peak15_final；canonical 01_final_standing_stability 已全部替换为严格 >25° 结果。
- 证据边界：原 URDF 没有 mass、density、inertia；1000 kg/m³ 和摩擦系数 2.0 均为统一实验假设。结果状态为 ANALYZED，不是工程认证或真实材料稳定性 VERIFIED。
- 验证：项目 tests/ 为 39/39 通过（含 5 个 Final Standing Stability 测试，其中 25.0°/刚超过 25.0° 边界已锁定），正式进程 exit code 0，结果报告副本 cmp 一致。
- 测试路径踩坑：裸跑 pytest -q 会沿仓库 temp 数据盘入口收集 runtime 内 MuJoCo、PyOpenGL、NumPy 的第三方自测并在 collection 失败；有效项目测试命令必须限定为 pytest -q tests。本轮未修改既有 pytest 配置。

## 2026-09-04 Progressive Build Stability（02）验证

- 新总入口：physics_analysis.md，统一记录 01 成品站立和 02 打印过程稳定性；02 仓库报告副本为 analysis_archive/progressive_build_stability_analysis.md。
- 02 入口：experiments/progressive_build_stability/analyze.py；说明：同目录 README.md；测试：tests/test_progressive_build_stability.py。
- 本阶段按用户确认只判断整体倾倒，不使用 FEA；弯曲、屈曲、层间开裂、热变形和喷嘴载荷均在范围外。
- 方法：authored URDF Z-up，从 global min-z 起以总高度 1% 为 Δh，固定 100 个高度；额外加入 collision geometry 出现/完成事件点。每层与 z<=h 半空间求交并优先封口。
- 每个 partial 用 CPU MuJoCo 沉降 5 秒；运行期间最大倾角严格 >25°才算倒下。所谓 unbonded free rigid body 不是高空自由落体，只用 0.002 场景单位间隙建立地面接触。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/02_progressive_build_stability/。15 个 case 中排除带整块房间地板的 S01；14 个 initial-pose 模型的 1400/1400 固定 Δh 均完成动态仿真，0 个点超过 25°。
- 固定 Δh 外共 149 个几何事件点；A02 6 个、I02 1 个、T03 1 个近零厚度 birth event 无法构造非零质量 MuJoCo body，明确保留为 unavailable，未计成稳定。
- 峰值倾角为 T03 3.356°、A01 0.639°、A02 0.085°，其余小于 0.08°。几何预筛仍将 A01 的 114/115 点和 T03 的 101/101 点判为不稳定。
- 关键边界：无扰动数值模型可能停在完全对称的不稳定平衡；因此只能说“按当前无扰动 >25° 判据未观察到倒下”，不能宣称实际打印稳定。下一步若测现实扰动，应预先冻结统一小冲量协议并与自然沉降分栏。
- 底板粘附只输出重力倾覆力矩/自重作为附加解释；无实测粘附强度时不作 pass/fail，也不改变逐 Δh 主结论。
- 依赖与资源：复用 01 的 Trimesh/Manifold 与数据盘 MuJoCo 3.12 runtime；全部 CPU 完成，不申请 GPU。partial STL 只在 /tmp 临时生成并在每 case 后清理。
- 验证：02 与 01 限定测试合计 9/9 通过；正式运行 exit code 0；1400 个固定 Δh 动态覆盖完整。


## 2026-09-04 Load-Bearing Structural Performance（03）验证

- 入口：experiments/load_bearing_structural_performance/analyze.py；配置：case_config.json；说明：README.md；测试：tests/test_load_bearing_structural_performance.py。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/03_load_bearing_structural_performance/；仓库报告副本：analysis_archive/load_bearing_structural_performance_analysis.md。
- 工具链：官方 Gmsh 4.15.2 SDK + 官方 CalculiX 2.23 源码。官方 ccx 预编译包依赖本机没有的 libgfortran.so.4，因此使用 GCC/GFortran 11、用户级 Ubuntu SPOOLES/ARPACK/BLAS/LAPACK 在 /vepfs_default/chanxueyan/lhp/lms/fea_runtime 编译 2.23；没有系统安装。
- lms/.bashrc 修改前备份为 .bashrc.before-adsl-fea-20260904；它 source experiments/load_bearing_structural_performance/activate.sh，提供 ADSL_CCX_BIN、adsl_gmsh、adsl_fea_python。wrapper 局部注入 LD_LIBRARY_PATH/PYTHONPATH，不全局污染现有环境。
- 官方 archive SHA256：ccx_2.23.tar.bz2=35d426fed5eb164fbbaaafa20819d13d22e30bc2b9bc9c6c4ed957e9468355dd；gmsh SDK=2dbd68d033f99b05789554bf4db6d47f4108dd36b0b36d4a50935df0ceb9e772。
- 解析基准：C3D10 悬臂梁位移误差 0.276%；固定—自由 Euler 柱临界载荷误差 0.340%，benchmark status=PASSED。
- 14-case 结果：4 SOLVED、7 INVALID_LOAD_PATH、3 NOT_MESHABLE；solver failure=0。4 个 SOLVED 为 E01、I01、T01-main、T01-control，全部 coarse/medium/fine 和 self-weight/functional 完成，4/4 通过 medium→fine 收敛门槛。
- 细网格 8/8 条载荷轨通过本轮代理阈值 U/L≤1%、名义屈服 FoS≥2、线性屈曲因子≥2；最柔的是 E01 functional 5.848 mm（0.650% 高度），其次 T01-main 5.587 mm（0.621%）。这是各向同性 PLA、全固定底面、线弹性代理，不是打印安全认证。
- 载荷路径缺陷：A01/A02/I02 的运动件虽有 URDF joint，但无 FEA 连接刚度/tie/contact；motorcycle、T03、T05 有 18–85 个断开体；M01 scratch/T02/T04 不能形成封闭实体。pipeline fail closed，没有体素补洞或静默粘合。
- 共享盘踩坑：CalculiX 会 delete/reopen DAT；直接以 jiigan-hp 为 cwd 报 openfile could not delete file。attempt-01 完整归档；正式 run_ccx 对每个 job 用 /tmp TemporaryDirectory，退出后解析、复制全部产物到数据盘并立即清理，避免共享盘语义和 /tmp 累积占满。
- 资源：全部 CPU，无 GPU。canonical 输出约 564 MiB，含 26 个成功 CalculiX job（24 case load/mesh + 2 benchmarks）。

## 2026-09-04 Support Requirement & Critical-Surface-Aware Support（04）验证

- 入口：experiments/support_requirement_critical_surfaces/analyze.py；配置：case_config.json 与 fff_profile.ini；说明：README.md；测试：tests/test_support_requirement_critical_surfaces.py。
- 正式结果：/jiigan-hp/lms/aDSL/experiment/physics_analysis/04_support_requirement_critical_surfaces/；仓库报告副本：analysis_archive/support_requirement_critical_surfaces_analysis.md。
- 方法：45°向下三角面 overhang 预筛 + PrusaSlicer G-code bridge/support/interface；interface footprint 与 support 起长 footprint 映射回原 collision face，输出 face IDs、面积、质心、bounds 和彩色 PLY。
- contact 是 0.2 mm top/bottom Z gap 内的名义承托区，不是严格 mesh 相交；关键面按 collision regex + face selector 显式配置，可靠表面重叠 >0.01 mm² 即 VIOLATION。
- profile：0.4 mm nozzle、0.2 mm layer、PLA、45°、everywhere rectilinear support、3 interface layers；原始 Z-up/initial state。第03项语义尺度保留元数据，实际切片最长边封顶 180 mm。
- 合成基准 PASSED；14/14 案例 ANALYZED，14/14 都生成 support。关键面为 3 VIOLATION、4 PASS、7 INDETERMINATE。
- 违规定位：A01 拉手 109.200 mm²；A02 aerator/knobs/lever 523.541 mm²；I02 三层抽屉拉手 253.287 mm²。E01/I01/T01-main/T01-control PASS。
- T02 候选关键面重叠 1204.717 mm²，但其与其余6个单一 GLB 案例没有可靠三角面语义来源，因此保持 INDETERMINATE。
- 网格缺陷：M01 scratch、T02、T04、T05 至少一个 non-watertight collision part；T02 有41个退化面，M01 scratch 有2个。PrusaSlicer 隐式 repair 不等于源模型通过质量检查。
- 源完整性：127个 URDF/GLB/STL/OBJ/source.py 运行前后 SHA256 一致。全部 CPU，无 GPU。
- runtime：本机无 Flatpak且官方 2.8.1 AppImage不兼容，使用 lms/tools/prusaslicer/2.4.0 下用户态解包的 Ubuntu Jammy 包；未系统安装。.bashrc 已备份为 .bashrc.before-adsl-support-20260904，并提供 adsl_prusaslicer/adsl_support_python。
- 编辑踩坑：本机 unprivileged user namespace 禁用导致 apply_patch 的 bwrap 间歇失败；一次 Python 行清理误将真实换行写成字面 `\n`，已恢复并重跑7/7单测。修正版14-case JSON完成后 Markdown 定位行曾有键引用错误，仅报告生成失败；已修复并从 canonical results.json 无需重切片地重建报告。

## 2026-09-04 Codex CLI 恢复与 01 易倒样例扩展

- 当前分支重新接回 `adsl-agents/providers/codex_cli.py` 和 `codex_codec.py`，默认
  profile 为 `codex-cli-gpt-5.6-sol.yaml`。Stepcode 不再是默认路径；OpenAI-compatible
  profile 只在显式指定时保留兼容。
- transport 使用已登录的 `codex exec --ephemeral --ignore-user-config --ignore-rules
  --sandbox read-only --json --output-schema`；aDSL 仍负责 Agents SDK session、受限工具、
  source 执行、critic/refinement 与渲染。真实结构化 smoke 成功，完整测试 76/76 通过。
- `pyproject.toml` 增加 `testpaths=["tests"]`，避免 `temp` 数据盘软链接使 pytest
  误收集 `runtime/mujoco-py310` 内第三方自测。
- CAP3D/MARVEL 只下载 caption CSV，正式位于
  `/jiigan-hp/lms/aDSL/datasets/prompt_sources/`，按数据集与不可变 revision 分层；
  未下载完整 ShapeNet/ABO 网格。selector 对四个 CSV 强制校验 SHA256。
- 实验根：
  `/jiigan-hp/lms/aDSL/experiment/physics_analysis/01_unstable_case_validation_20260904/`。
  16 个 prompt 已冻结为 8 initial + 8 reserve，使用 CAP3D 与 MARVEL level-2 原始 caption，
  不做稳定性 prompt engineering。论文未公开 200 条 prompt IDs，本实验只能标
  same-source/different-sample。
- 生成前发现横向 rocket 不适合 standing test；在任何模型生成和结果观察前改为
  ShapeNet standing/tower loudspeaker，并在 manifest 记录 pre-run amendment。
- 01 controls：稳定组 2/2 自由沉降最大倾角 0°；易倒组 3/3 严格超过 25°，峰值
  133.891°、179.990°、179.974°。control 不计入 aDSL failure rate。
- GPU queue 现直接固定到
  `/jiigan-hp/lms/aDSL/experiment/gpu_render_queue`；manager/worker 要求精确目录、
  mount target=/jiigan-hp、fstype=`hpvs_fs*`。第一次申请因旧 realpath 白名单在申请前
  退出；修复后的单卡 run `20260904T154103Z` 仍 pending。
- 无人值守 tmux `adsl_standing_cases` 已提交：等待 `adsl_gpu_renderer` live 后严格
  串行生成 U01–U08，每 case 两轮，不自动跑 reserve。日志与 marker 位于实验根：
  `generation_manager.log`、`generation_status_initial.json`、
  `INITIAL_GENERATION_SUCCESS` / `INITIAL_GENERATION_FAILED.exit_code`。
- 本账号另有两个从 2026-08-19 起停在 root 提示符的旧 GPU 会话：
  `gpu:4.0` 单卡、`gpualloc:1.0` 双卡。未获授权，未退出、未复用；可能占用配额。
- 后续：先检查生成 marker；完成后运行 01 analyzer 到 `generated_analysis/`。只有
  initial 8 个自然沉降全部不超过 25° 时才生成已冻结 U09–U16 reserve；最后运行
  `report_unstable_cases.py` 并归档。

### 2026-09-06 重提交 attempt_02

- attempt_01 在等待 43000 秒后先于 GPU 分配完成而退出，未生成任何 case；原日志和
  marker 已保留为 `generation_manager_attempt_01.log` 与
  `INITIAL_GENERATION_attempt_01_FAILED.exit_code`。
- 新 GPU run：`gpu_render_queue/runs/20260906T044309Z`；tmux：
  `adsl_gpu_renderer`。单卡 A800，allocation timeout=172800 秒（48 小时）。
- 新生成 tmux：`adsl_standing_cases`；日志：
  `generation_manager_attempt_02.log`；等待 timeout=200000 秒，长于 GPU allocation
  timeout，GPU manager 若提前失败则 control.sh 仍会立即 fail closed。
- attempt_02 成功/失败 marker：
  `INITIAL_GENERATION_attempt_02_SUCCESS` /
  `INITIAL_GENERATION_attempt_02_FAILED.exit_code`。提交时两个 tmux 均存活，GPU 状态
  为 Worker pending。

## 2026-09-06 阶段实验 presentation 归档

- 新增根目录 `presentation.md`：Marp-compatible 中文阶段汇报，覆盖 Agent/代码审计、
  7-case aDSL–TRELLIS CLIP pilot、01/02 稳定性、03 CalculiX FEA、04 PrusaSlicer
  support/critical-surface 分析，以及 U01–U08 attempt_02 pending 状态。
- 新增 `presentation_assets/`（约 14 MiB）：15 个 aDSL GLB、7 个 TRELLIS GLB、
  5 个稳定/失稳 control GLB、3 个 articulated URDF/关节状态、15 份审计渲染、5 张
  对比图和3张定量图。资源均从数据盘 canonical experiment 复制，原始 JSON/CSV/log/
  G-code/FRD 仍只保留在数据盘。
- 图表由 `presentation_assets/generate_figures.py` 生成，使用 aDSL 环境已有 Pillow，
  未安装新依赖。presentation 共 67 个本地链接，检查结果 missing=0；图表脚本通过
  `py_compile`，三张 PNG 均为 1600×900。
- 结论边界在 presentation 中单独标注：14/15 是 Agent 自审批而非真实成功率；CLIP
  pilot 不是统计等价检验；01/02 为无扰动刚体代理；03/04 不是工程或制造认证；
  U01–U08 尚未生成，不能提前写入 failure rate。
- 根据用户要求补充物理检测环境图：
  `presentation_assets/renders/simulation/` 下新增 01 MuJoCo control 初态/5秒终态、
  02 T03 四个 partial-build 高度、03 E01 实际 C3D10 fine mesh + FRD 位移/应力场、
  04 A02 `surface_classes.ply` support/contact 分类共4张 PNG，并分别插入 presentation。
- 可复现入口为 `presentation_assets/generate_simulation_snapshots.py`。脚本只读数据盘
  冻结的 XML/URDF/INP/FRD/PLY，输出展示 PNG，不修改原模型或分析结果；MuJoCo 使用
  项目 `.bashrc` 的 EGL 配置，CalculiX 图直接解析 actual fine deck/FRD，PrusaSlicer 图
  保留正式 PLY 的面颜色。03 图明确区分 FRD nodal-extrapolated 4.29 MPa 与 canonical
  DAT 3.963 MPa 的后处理口径。

## 2026-09-06 CPU stability pilot and geometry-feedback loop

- GPU worker remained pending, so U01 and U05 were generated with local CPU Blender EEVEE at 512x512, 64 samples, max_rounds=1. Both produced GLB, URDF, and eight renders.
- The unchanged Final Standing Stability checker found U01 upright (peak 0.002 degrees, verdict INDETERMINATE) and U05 naturally tipped (peak 97.140 degrees, final 96.504 degrees, support area 0, 16/16 impulse directions tipped, verdict UNSTABLE/high).
- U07 failed on the shared data disk with sqlite3.OperationalError: no such table: main.agent_messages; run.json remained status=running. This exposes both shared-filesystem SQLite fragility and incomplete exception-path state finalization.
- Important path pitfall: project temp is a symlink to /jiigan-hp/lms/aDSL/experiment, so it is not suitable when the goal is to avoid shared-disk SQLite. The first U05 feedback edit there received the intended plinth patch but exited 135 (SIGBUS) before render/critic.
- With explicit user authorization, the U05 edit was rerun in the real project-local local_experiment directory. The checker output was supplied verbatim as edit feedback. The agent added a concentric Cylinder plinth (radius 0.26, height 0.04).
- The local edit used the original model profile and critic settings: gpt-5.6-sol/high, max_rounds=2, 8 views, 1024x1024, 256 samples, local Blender EEVEE. Image Critic approved after round 1; Code Critic did not run because the current workflow short-circuits on Image Critic approval.
- Unchanged MuJoCo recheck: support area 0.2110, peak tilt 0.008346 degrees, final tilt 0.002314 degrees, 0/16 impulse directions tipped, minimum observed F/W 0.35. Natural fall changed true to false, but composite verdict remains INDETERMINATE/low due mesh-contact and mass-model uncertainty.
- This is an externally orchestrated checker-to-text-feedback-to-edit-to-checker feasibility result, not an automatic ObjectWorkflow gate and not a success-rate estimate.
- presentation.md, simulation figures, model assets, machine-readable evidence, and adsl_presentation_bundle_20260906.zip were updated. The ZIP now also includes analysis_archive, reports, and physics_analysis.md so report links are self-contained.
- The presentation now documents the exact external feedback handoff, ObjectWorkflow planner/coder/critic sequence, and byte-matching source diff. The portable evidence directory includes baseline/revised source.py, plan.json, runtime_config.json, and the coder response containing its single apply_patch call.
- Added an eight-view appearance audit. The upper lamp geometry is unchanged, but the radius-0.26 plinth is locally conspicuous: its diameter is 44.4 percent greater than the radius-0.18 sphere's diameter, although its 0.04 height is only about 2.4 percent of the 1.65 scene height. Conclusion: object identity retained, non-invisible base-fidelity tradeoff.


## 2026-09-06 通用 Engineering checker 与 FEA 自动反馈闭环

- `ObjectWorkflow` 新增通用 `CheckerSpec` / `CheckerResult` 协议、外部进程执行器和
  `object-engineering-critic`。checker 可重复注册；每轮结果固定写入
  `rounds/round_XX/checkers/<name>/result.json`，原始求解证据保留在同目录。
- required checker 的 `ERROR` 按基础设施故障停止，不把 solver/slicer 缺失伪装成几何
  缺陷；`FAIL/INDETERMINATE` 才交 Engineering Critic。配置 checker 后，只有外观和
  所有 required checker 均 PASS 才发布，最后一轮不再无审查发布。
- CLI 新增可重复 `--checker-config` 和 edit-only `--check-first`。`--check-first`
  跳过 initial edit patch，使 round 1 字节一致地检查输入源码；resume 会从
  `runtime_config.json` 恢复 checker specs。
- `experiments/workflow_checkers/run.py` 将现有 MuJoCo standing、CalculiX/Gmsh FEA、
  PrusaSlicer support 分析适配到统一协议；spec 可声明用户态
  PYTHONPATH/LD_LIBRARY_PATH prepend，无需 root 安装或 shell wrapper。
- 新增 `tests/test_workflow_checkers.py`；aDSL 环境完整回归 84/84 通过。一次误用
  ttrv 环境导致 collection 缺包，改用明确的
  `/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python` 后通过。
- FEA baseline：0.9 m 四腿薄椅，isotropic PLA proxy，底面全固定，座面 -1000 N、
  椅背 +300 N。模型 exact union 后 watertight/单 component，coarse/medium/fine 均
  求解并收敛；不是断连或 solver failure。
- Round 1 为真实 FAIL：U=39.728 mm，U/H=4.414%，17.571 MPa，FoS=2.846，
  buckling=9.061，hotspot=[0.26675,0.23675,0.48121] m。Image Critic approved。
- Engineering Critic 把热点映射到后立柱/座面连接；Coder 唯一 patch 是将两根后立柱
  从 22×22 mm 改为 22×40 mm，并将 y-center 0.245→0.236 以保持外缘不变。
- Round 2 仍 FAIL 但显著改善：U=19.522 mm，U/H=2.169%，9.613 MPa，FoS=5.201，
  buckling=9.107，mesh convergence 继续 PASS。位移下降 50.86%，应力下降 45.29%。
- 用户确认“有改善即可”，在 round 3 FEA 完成前主动停止。冻结结果严格选择 round 2；
  round 3 仅有第二次 patch/渲染、没有完整 FEA，不能用于性能结论。原始 run.json 因
  KeyboardInterrupt 仍显示 running，这是另一个 exception/cancellation 状态收尾缺陷。
- 同一 round 2 椅子上另外实跑通 standing spec（PASS，1/1 state 未超过25°）与 support spec（PASS，关键面无 nominal contact overlap）；证明三份适配器均通过通用 runner，而非只做 schema 单测。
- 两轮 Image Critic 虽均 approved，却都在 observations 中误称 seat thickened；源码 byte diff 证明座面没改，实际是后立柱。此结果继续证明 visual critic 不能描述/验证精确结构修改。
- 八个匹配 Eevee 视图 RGB MAE 平均 0.1416%，差值>10/255 像素平均 0.600%；源码和
  包围盒均证明变化局部，但像素差不是感知质量指标。
- 完整三网格 INP/DAT/FRD 保存在
  `local_experiment/fea_checker_loop_20260906/selected_improvement/evidence/`
  （约248 MiB）。portable presentation 只复制标准 JSON、源码、Critic 记录、16 张
  渲染、两份 GLB 和实际 FRD 场对比 PNG。


## 2026-09-06 Progressive-build checker 自动反馈闭环

- 通用 checker adapter 新增 `progressive` 模式和固定 spec/config：authored Z-up、
  Δh=总高1%并加入 geometry birth/complete events、密度1000 kg/m³、摩擦2.0、
  无底板粘附、每层 MuJoCo 自然沉降5秒，峰值倾角严格>25°为 FAIL。
- adapter fail closed：非空 partial 没有 MuJoCo 结果记 ERROR，clip failure 记
  INDETERMINATE；Agent feedback 只保留首次和最坏失败层，同时附 COM、signed/
  normalized margin、support area 和 active geometry names。
- 新增2项 adapter 回归测试；progressive/workflow 定向测试 14/14 通过。
- 三个手写 aDSL 诊断 fixture 都满足 final standing PASS / progressive FAIL：
  C01 39/107（first 47%，peak34.574°），C02 46/107（first36%，peak30.968°），
  C03 45/107（first39%，peak31.944°）。它们用于隔离机制，不计入自然prompt失败率。
- C01 进入真实 Codex CLI Engineering checker loop。Round1 checker 将首次/最坏高度、
  COM越界与 `narrow_base/central_stem/right_display_shelf` 传给 Engineering Critic；
  Coder唯一patch是在base后新增
  `stability_foot = Cube((0.44,0.28,0.02), center=(0.08,0,0.01))`。
- Round2同协议0/108失稳，peak0.0908°，signed margin +0.1345，support area0.1232，
  progressive PASS；独立成品standing peak0.0337°也PASS。流程在round2以
  `appearance_and_required_checkers_approved` 正常完成。
- 独立 PrusaSlicer复核前后均 `support_required=true`，overhang area同为
  3147.968 mm²；低foot只解决整体倾覆，不解决右悬臂过悬。Slicer是逐层G-code/
  support规划，不是逐道沉积热力学仿真。
- 八视图RGB MAE 0.169%，差值>10/255像素占0.682%；修改局部但底部foot可见。
- 新的Image Critic可证伪错误：baseline round1源码没有foot，它却声称已看到
  low-profile support extension。结合FEA椅子误称seat thickened，说明Image Critic
  不能审计精确结构修改；必须以source diff和checker为准。
- 完整记录：
  `presentation_assets/results/progressive_checker_loop/EXPERIMENT.md`；
  仿真图：`presentation_assets/renders/simulation/02b_mujoco_progressive_checker_loop.png`。

- 方法修正：真实FFF progressive主判据应由PrusaSlicer逐层G-code驱动，累计model+support+brim质量、COM、底板连通footprint与所需粘附强度；当前无粘附MuJoCo Δh降级为脱粘极端stress test，不再解释为真实打印过程主模拟。

## 2026-09-06 Standing GPU 正式批次终止

- `adsl_gpu_renderer` 与 `adsl_standing_cases` 从 04:43 UTC 起持续约 6 小时仅报告
  `Worker pending`，没有生成新的 U01–U08 正式样本；已有结论来自 CPU pilot U01/U05、
  U05 standing checker 闭环、3 个手写 unstable controls 和 progressive C01 闭环。
- 经用户确认 standing 阶段已经完成后，主动停止这两个 tmux 会话，并终止 tmux 退出后
  遗留的精确 pending allocation 进程。GPU run
  `/jiigan-hp/lms/aDSL/experiment/gpu_render_queue/runs/20260906T044309Z/`
  标记为 `MANAGER_ABORTED`；standing attempt_02 记录为失败/取消，不计入统计。
- `presentation.md` 已将“仍在 GPU 队列中”改为“长期 pending 后主动终止”；没有新模型或
  新 checker 结果可加入。后续 overhang prompt pilot 使用 CPU，不再新申请 GPU。
## 2026-09-06 Overhang checker-feedback pilot

- Standing/progressive experiments are complete; the six-hour pending GPU standing batch was terminated with no new U01–U08 outputs.
- Overhang pilot uses CPU only and paper-aligned CAP3D/MARVEL captions. Exact paper prompt IDs are not public.
- O01 mug feedback added two local 50-degree underside chamfers at the handle mounts. PrusaSlicer nominal support contact fell from 278.361 to 225.243 mm² (-19.08%) while print AABB stayed fixed; geometric overhang rose from 3087.424 to 3119.069 mm² (+1.02%).
- User selected an improvement-oriented stopping rule for this demonstration, so O01 is archived as measurable improvement but strict FAIL, not as a complete solution.
- O02–O04 baselines completed and all required support; O05–O08 expansion was stopped to avoid unnecessary CPU/API use.
- Canonical presentation evidence: `presentation_assets/results/overhang_checker_loop/EXPERIMENT.md`, paired GLBs, and `presentation_assets/renders/comparison/overhang-O01-four-view-before-after.png`.
- Flow pitfall: raw support-region triangle ID lists inflate the agent context; preserve them in raw artifacts but summarize object, face count, area, centroid, and bounds in future feedback payloads.

## 2026-09-06：StepCode LMS 版本隔离与 `lms_proxy`

- StepCode 上游从 2026-09-06 起拒绝 `1.2.48`，最低要求为 `1.2.70`；已验证 `1.2.79` 的模型列表和 `/v1/responses` 均返回 HTTP 200，`gpt-5.6-sol` 可用。
- 升级首次失败不是网络问题，而是 `/tmp` 的 6 GiB tmpfs 已满；将 `TMPDIR` 指到项目盘后升级成功。
- 全局 `/root/.stepcode/current` 已恢复指向 `1.2.48`；root 下的 `1.2.79` 副本已删除。
- LMS 新版位于 `/vepfs_default/chanxueyan/lhp/lms/stepcode/versions/1.2.79`，凭据仍从 `/root/.stepcode/config.json` 内存读取，没有复制到 LMS 或仓库。
- 未 source 的干净 shell：`stepcode --version` 为 `1.2.48`，且没有 `lms_proxy`。
- 使用新版前执行 `source /vepfs_default/chanxueyan/lhp/lms/.bashrc`；此后 `stepcode --version` 为 `1.2.79`，且 `ADSL_STEPCODE_BIN` 指向 LMS wrapper。
- 代理流程：`lms_proxy start`、`lms_proxy health`、任务结束后 `lms_proxy stop`。LMS 私有代理监听 `127.0.0.1:44949`，启动时会停止全局旧 Supervisor 代理以释放端口。
- localhost 健康检查必须绕过环境代理（`curl --noproxy "*" ...`）。直接 kill 全局代理会因 Supervisor 的 `autorestart=true` 被重新拉起，应使用 Supervisor stop；LMS 私有代理则由 `lms_proxy` 管理。
- 当前验收结束后，全局代理和 LMS 私有代理均保持停止，`44949` 无监听。

## 2026-09-09：物理 checker 代码同步主分支

- 将站立稳定性、渐进打印稳定性、CalculiX FEA 和 PrusaSlicer overhang/support
  checker 统一接入 aDSL 的 execute–critic–refine 工作流；required checker 参与最终
  发布判定，`FAIL`/`INDETERMINATE` 进入 Engineering Critic，`ERROR` 按基础设施故障
  fail closed。
- 保留 `--check-first` 原始 round-1 基线、结构化反馈、checker 配置不可被 Coder 修改、
  GPU Eevee 串行队列及 Codex CLI 默认传输；StepCode HTTP 后端仍可显式选择。
- 物理 checker 主提交为 `fbf8915`。合并 `origin/master` 时保留其
  `codex_api_plan.md` 与 `temp/` 忽略规则，并合并 README 的 StepCode、GPU 和 checker
  使用说明。
- 合并后在 aDSL Python 3.10 环境运行完整测试：`89 passed`。测试时清除机器级
  `ADSL_STEPCODE_BIN`，避免主机私有 wrapper 改变配置单测的假设。
- PDF、ZIP、`local_experiment/`、`presentation_assets/`、`presentation.md`、`temp`
  和未接入正式队列的 `vram_guard.py` 均保留为本地实验材料，不进入 Git 提交。

## 2026-09-09：Checker v2、源码定位与确定性候选修复

- 按 `Physics_Checker_Unification_and_Source_Repair_Plan.md` 完成 protocol-v2
  findings、AnalysisContext、AST+运行时 SourceIndex、坐标感知定位、结构化
  RepairProposal、隔离候选执行和确定性接受/回退策略；旧 protocol-v1 保持兼容。
- 每轮新增 `analysis_context.json`、`source_index.json`、`findings.json`、
  `localization.json`、`repair_proposals.json`；候选保存全部 checker、图像评审和
  `decision.json`，`repair_history.jsonl` 防止重复尝试。
- 真实 `standing_u05` StepCode+GPU+MuJoCo 闭环完成：基线 peak/final tilt 为
  97.1569°/96.9103°（FAIL）；Agent 只在 `SphericalBase` 增加半径0.15、高0.06、
  底面位于z=0的居中低矮圆柱脚；候选降至0.3904°/0.2251°（PASS）。
- 确定性策略确认 AST 范围合法、required checker 无回归、图像 critic approved，
  因此接受候选。8视角平均归一化RGB MAE为0.000476，变化像素约0.246%；变化局部，
  但这些视觉指标不等于功能证明。
- 完整实验在 `local_experiment/physics_checker_unification_20260909_u05_local/`；
  实施报告为 `reports/physics_checker_unification_20260909.md`。完整回归104项通过。
- 踩坑：SourceIndex 必须把自定义 Asset 实例关联到构造类源码；Engineering Critic
  的自由字典不能使用 SDK strict schema；StepCode瞬时502/503需要有限重试；GPU共享盘
  heartbeat需短grace并将真实丢卡标成基础设施错误；Code Critic不读源码不能算通过。
- 挂载盘上的 `sessions.sqlite3` 曾出现 `disk I/O error`；复制到项目盘后
  `PRAGMA integrity_check=ok` 并成功resume。后续活跃workspace/SQLite/小文件放项目盘，
  完成后的大体积render/solver证据再归档到 `/jiigan-hp/lms/aDSL/experiment/`。
- 本轮结束后 LMS StepCode proxy 已停止；A800 GPU renderer worker 保持运行，避免卡被
  回收。MuJoCo结果仍是固定代理条件下筛查，不是真实打印或安全认证。

## 2026-09-09：30-object standing + FEA 配对实验协议

- 新增 `experiments/standing_fea_30/`：固定 30 个唯一 CAP3D/MARVEL prompt，覆盖
  chair/stool、table/desk、bookshelf、floor lamp、tower speaker 各 6 个；对 caption
  做类别语义复核，排除 table lamp、desk-with-shelves、pendant light 和空 speaker
  stand 等标签/描述冲突。论文未公开其精确 200 prompt ID，因此本实验明确标为
  same-source/different-sample targeted subset，不声称精确复现。
- 两臂均独立 prompt-to-3D、同一代码与 StepCode profile、temperature=0、最多四轮、
  同一 512×512 Eevee GPU 队列；API 没有正式 seed，所以只固定 prompt selection、
  arm order、Python hash seed 和所有 checker/render 配置，不声称 bitwise reproducible。
- vanilla aDSL 不配置物理 checker；Ours 同时配置 required standing + category-specific
  FEA，并允许统一 Engineering Critic/source repair。两臂最终源码都离线重跑同一
  standing/FEA evaluator，避免只评 Ours；standing 严格 >25° 才失败。
- FEA 的 `INDETERMINATE` 原样保留并按 NOT_MESHABLE、INVALID_LOAD_PATH 等原因统计，
  不伪装成 FAIL/PASS，也不因 indeterminate 重采样。联合通过只定义为两个 checker
  均 PASS；报告输出 paired bootstrap 95% interval 和 exact McNemar。
- 五类合成连通夹具已跑真实 export→MuJoCo→Gmsh/CalculiX 预检，10/10 checker 都
  产出有效判定：standing 五类均 PASS；FEA chair/table/lamp PASS，bookshelf/speaker
  收敛后 FAIL，验证了成功和结构失败两条真实路径。证据在
  `local_experiment/standing_fea_30_preflight_20260909/`。
- StepCode 1.2.79 最小 Responses 请求验证 `temperature=0` 可用并返回 `OK`，代理随后
  由 trap 关闭。完整仓库回归 109/109 通过。
- 批处理启动前强制校验 GPU worker heartbeat；每次显式 resume 使用新日志文件，避免
  覆盖历史；额度、GPU、基础配置等全局错误仍单独标记并可停批；单个 arm 的 checker/generation 错误记录到 batch_terminal.json 后继续下一个 arm/case。活跃 SQLite/workspace
  留在项目盘，完成后才将不可变大证据归档数据盘。


## 2026-09-10：Topology 连接性与双端源码定位

- 新增执行期 `analysis_geometry.json`：在 GLB/URDF 扁平化前保存 aDSL
  primitive、Boolean 层级、semantic path、feature/source ID 与源码行号；重复 DSL
  调用的 source ID 通过同 scope 内 occurrence 区分。
- 新增 OpenCASCADE topology checker：
  `load_path` 检查指定载荷部件到最低支撑部件的实体连接；
  `one_piece` 检查非 joint 顶层部件是否为一个实体。面接触或体积重叠才算连接，
  点/边接触与正间隙均失败；AABB 只选候选，不证明连接。
- 断裂 finding 记录最近的两个语义端点、最近点、距离和源码位置；定位器另外给出
  两端最低共同语义父级 `bridge_parent`。resize/reshape/relayout 只能针对端点，
  新增局部连接件只能针对 bridge scope；候选仍必须重跑全部 configured checkers。
- FEA 在 manifest 可用时从同一解析几何构建 Gmsh C3D10 网格；topology FAIL 由
  topology checker 作为可编辑几何失败报告，而 FEA 保持 INDETERMINATE，不再把
  GLB Boolean/export 故障误报成真实结构断裂。旧 URDF collision 路径保留为兼容回退。
- SF01 真实回归：从旧 `BOOLEAN_UNION_EMPTY` 改为明确的两个问题：
  segmented backrest 含 3 个断开实体；seat/back 承载组件与 pedestal 最近间隙
  `0.0201189931 m`。定位到 seat `L158/L9-70`、pedestal `L168/L112-147`
  和 bridge parent `L150-168`，没有 unresolved finding。
- 已有改进椅子回归：10 个语义部件形成 1 个连通分量，topology PASS；
  analytic geometry 路径的 coarse/medium/fine 三层 CalculiX 均 SOLVED。最终仍因
  位移比约 `2.169%` 超过 1% 门槛而 FEA FAIL，与历史物理结论一致。
- `load_path` 只检查并保留载荷—支撑连通分量，完全无关的断开装饰件不阻塞 FEA；
  `one_piece` 仍检查全部非 joint 顶层部件。
- topology 与统一定位针对性测试共 22 项通过，全量回归 120 项通过。临时验证证据位于
  `/tmp/adsl_topology_sf01_*` 与 `/tmp/adsl_topology_connected_*`，不作为长期归档。
- 按 `AGENTS.md` 控制范围：本轮不实现 adaptive registry、依赖调度器、新状态机、
  30-case/5-case 实验或其他非必要重构。

## 2026-09-11：最终实体连通性语义与 12-case CPU 配对实验

- topology 最小修复已合入 master/origin/master，提交为 1337660。one_piece
  现在以最终装配中的 OCC 实体分量为准：不会只因一个语义部件内部包含多个实体就失败，
  因为这些实体可能经其他部件连通；同时也不会把同一语义部件中的断开实体错误折叠为
  一个图节点。
- load_path 作为 FEA 前置检查，逐项确认每个必需载荷都成功匹配并连接到有效支撑；
  网格保留完整承载分量，不再只取最短路径。仍保留 OCC 与双端源码定位；未支持或证据
  不足的情况明确返回 INDETERMINATE，没有扩展通用支撑推断、连接契约或调度系统。
- 12-case 子集为 SF01 SF03 SF05 SF06 SF07 SF11 SF13 SF16 SF20 SF21 SF25 SF27，
  包含4把椅子、2张桌子、2个书架、2盏落地灯和2个塔式音箱。vanilla aDSL 不接收
  checker feedback；Ours 同时使用 required topology + standing + category-specific
  FEA feedback；两臂最终都用相同三个 checker 评价。
- GPU 等待不能阻塞实验，因此新增 --local-render 最小开关及
  experiments/topology_standing_fea_12/run_cpu.sh、submit_cpu.sh。它只移除
  ADSL_GPU_RENDER_QUEUE，Agent/checker/StepCode 流程保持不变；渲染为本地 CPU
  BLENDER_EEVEE、512×512、64 samples、8 views。
- 当前有效 CPU run：
  /vepfs_default/chanxueyan/lhp/lms/aDSL/local_experiment/topology_standing_fea_12_cpu_20260911T091921Z/；
  tmux 为 adsl_tsf12_cpu_20260911T091921Z。活跃 SQLite 和小文件留在代码盘，完成后
  再考虑将不可变大证据归档至数据盘。
- 09:41 UTC 状态快照：SF01/adsl 已完成，SF01/ours 正在第2轮，其他 case 未开始。
  baseline SF01 的 topology/standing/FEA 均 PASS：5个语义部件构成1个最终实体分量，
  MuJoCo peak/final tilt 为3.543°/3.408°，FEA 最大位移3.774 mm、位移/特征长度
  0.419%、名义屈服安全系数18.332、首个正屈曲因子243.237。
- Ours SF01 第一轮生成了非法 0. seventeen Python 语法，Debugger 已自动修正；
  第2轮8张 CPU Eevee 图于09:41 UTC全部生成。这再次说明缺少生成源码 AST/compile
  preflight 会浪费一轮，但本次不扩大范围修复。
- 第一次 CPU 提交
  local_experiment/topology_standing_fea_12_cpu_20260911T091759Z/ 因封装脚本误写
  不存在的 cases.json 而在生成前安全退出；已改为真实的 case_manifest.json。
  该失败目录仅作审计，不计入实验结果。
- 单 A800 keeper 请求由 experiments/gpu_render_queue/submit_keeper.sh 提交，tmux 为
  adsl_gpu_keeper_20260911T091815Z，记录目录为
  /jiigan-hp/lms/aDSL/experiment/gpu_keeper/20260911T091815Z/。该入口直接调用
  volc ml_devinstance launch，明确不设 allocation timeout；无卡时持续 pending，
  分配后前台运行 queue-aware keeper。keeper 在 render queue 空闲时用 ttrv PyTorch
  占卡，出现 pending/running render job 时释放 burn 进程，队列空闲后重新启动。
  只有用户手动停止 tmux 才终止本次占卡请求。
- 本轮新增脚本均通过 bash -n/py_compile；topology、checker unification 与
  standing/FEA runner 的针对性回归为 21 passed, 1 skipped。
## 2026-09-11：checker 无反馈与批处理容错

- `adsl-agents/service.py` 不再因 required checker `ERROR`（进程失败、超时、缺少
  `result.json` 或结果无效）直接终止 ObjectWorkflow。该结果仍写入 checker history，
  作为 `checker_feedback_unavailable` 审计记录，不交给 Engineering Critic 伪造几何反馈；
  外观通过时可完成为 `approved=false` 的明确未验证执行。候选中的 checker ERROR 只拒绝
  当前候选并继续尝试后续候选。
- `experiments/standing_fea_30/run_batch.py` 将每个 arm 的非完成状态和未捕获异常写入
  state/events，继续后续 arm 和 case，最终用 `COMPLETE_WITH_ERRORS` 汇总；全局预检错误
  仍然中止。CPU/GPU wrapper 都会继续运行 summarizer。
- SF01 Ours 的 MuJoCo 错误不是整件太小：整件包围盒约 `1.06×1.06×1.575`，而
  `mesh_0_1988` 是两三角形、体积约 `2.09e-17` 的孤立浮点残片。生成源码
  `RoundedPad` 将较短边的半径设为 `min(width, depth)/2`，使四个 cap 中出现重合球；
  Blender Boolean UNION 后产生大量碎片（原始 mesh split 2361 个组件）。临时去重 cap
  中心的对照导出降为 10 个组件且无小碎片，支持“重复 Boolean 输入导致残片”的判断。
- MuJoCo checker 目前只增加少于 4 个三角面的低拓扑碎片过滤，保留原有秩判断；对真实
  SF01 重新检查为 `available=true`、`PASS`，98 个可用 mesh component、2263 个低拓扑
  碎片被丢弃。随后将已验证的 `RoundedPad` 半径分支正式放入 `adsl.core` 公共实现：
  严格小于边界时保留四 cap，尺度相关 epsilon 内切换两端 capsule，超出 epsilon 才
  抛出 `ValueError`；当前 SF01 源码改用该公共 helper，历史 round 快照保持不变。

## 2026-09-12：无效 FEA 网格反馈与 SF03 收尾

- FEA 在进入 CalculiX 前拦截零/负体积和非正/非有限 Jacobian，返回
  `INDETERMINATE / MESH_INVALID`，不是结构强度 FAIL。复用 minSJ；低正质量
  不直接判无效。未修改 OCC、容差、缩放、网格生成参数或模型。
- MESH_INVALID 是不可用状态中可提出局部几何假设的有限例外：提供数量、局部
  坐标、已有源码定位和报告指针，明确成因未确定、强度未验证；基础设施故障
  不作几何目标。禁止通过删单元、放宽阈值或修改 checker/求解设置过关。
- 未验证到未验证不算改善；其他有效检查改善可以保留未验证工作候选。
  目标网格修复后真实 PASS 可算改善，新发现真实物理 FAIL 则拒绝并保留报告。
- SF03 原候选已补完真实 stepcode 视觉验收，工作候选 accepted=true，最终
  topology=PASS、standing=PASS、FEA=INDETERMINATE/MESH_INVALID、approved=false。
  原 1 round / 1 candidate 已用完，不重置预算；本次没调用 Coder 或重跑 FEA。
  保存细网格确认退化元素 10463、10464、10471；历史 minSJ 缺逐元素 IDs，数量
  明确标为已确认下界。几何成因没有继续深挖，不能声称网格已修好。
- 报告：`reports/fea_mesh_invalid_sf03_completion_20260912.md`。结果：
  `local_experiment/checker_diagnostic_20260912T0637Z/SF03_outcome.json`。
  13 个新增轻量回归，相关测试合计 91 passed。SF04 与旧 12-case 未恢复。

## 2026-09-12：十轮单候选与 SF03 新验证

- 用户要求保留我们的 Coding Agent / stepcode，不改 Gemini。新建运行默认上限
  10 轮，工程修复每轮 1 候选、全程 10 候选；7200 秒安全预算保持。CLI resume
  仍沿用已有“额外轮数”语义，不自动重置历史候选预算。
- 正式新批次配置、prompt manifest 元数据同步为 10 轮、1024×1024/8 views。
  历史四轮批次不可直接混用新冻结配置；历史 workspace/protocol 未改写。
- 多 checker 仍统一汇总给 Engineering Critic，仅 topology 非 PASS 阻断依赖的
  FEA；独立 standing 继续。每轮一个方案可以覆盖兼容的多个问题，不引入调度框架。
- 新单例 `local_experiment/sf03_mesh_repair_10round_20260912/` 从 SF03 当前工作
  源码开始，显式新授权预算，不重新 prompt-to-3D 生成。模型保持
  gpt-5.6-sol / temperature=0；CPU Eevee 1024×1024，保留旧结果。
- 论文给出十轮上限，但没有多候选超参数；单候选是顺序修订对应，不能声称
  Gemini 等全部实验条件复现。相关小测试 72 passed；详见该目录 EXPERIMENT.md。
- 用户随后要求停止 SF03、保留证据待统一规划。新实验到第 2 轮：第 1 候选
  下横梁深度 0.13→0.15 仍 3 个坏单元，视觉 PASS、正式拒绝；第 2 候选后立柱
  深度 0.14→0.12 仍 4 个坏单元，topology/standing PASS，候选视觉请求中被人工
  停止，验收未完成。进程组 1126357 已 TERM 退出（143），未删除文件，未恢复
  旧批次。`STOPPED.json` 和 `run.json` 标记 stopped_by_user；工作源码未改动、
  approved=false、FEA=INDETERMINATE/MESH_INVALID。不自动继续，不宣称网格已解决。

### 2026-09-12 新批次：五轮上限，暂停网格无效修复

- 用户要求新任务最多 5 轮，工程修复每轮 1 候选、总候选上限 5；保留既有超时。
- 暂停 294c683 中 MESH_INVALID 的几何修复入口，保留 CalculiX 前网格有效性检测、
  原始详细报告与 FEA=INDETERMINATE/MESH_INVALID。给 agent 仅简短未验证摘要。
- 只剩网格无效等不可用检查时提前正常保存并结束，approved=false，不凑满五轮。
  其他有效 checker 可继续修复；已有通过项回归保护和拓扑到 FEA 的依赖不变。
- 本次使用既有 12 case CPU 提交脚本、StepCode，两组 vanilla/ours；使用新目录，
  不恢复已暂停批次和 SF03 诊断预算。只确认启动，按用户要求不持续监督。
- 已提交：tmux `adsl_tsf12_cpu_20260912T085057Z`，输出
  `local_experiment/topology_standing_fea_12_cpu_20260912T085057Z/`；102 项相关测试通过。
  提交记录见该目录 EXPERIMENT.md；提交不代表已完成生成或物理验证。
- 启动确认发现上述新批次 KeyboardInterrupt，tmux 已退出；配置已落盘但不在运行。
  原因未确认，保留文件、不自动重试。不得把本次提交报告为持续运行中。
- 用户随后明确授权重新提交：新 tmux `adsl_tsf12_cpu_20260912T085927Z`，目录
  `local_experiment/topology_standing_fea_12_cpu_20260912T085927Z/`。
  启动检查 pane_dead=0、批次 PID 1154361、SF01/adsl PID 1154363，StepCode health=ok。
  已设置新实验窗口 remain-on-exit，保留退出现场；旧会话不动。只确认启动，不持续监督。

### 2026-09-12 批次代理与可见日志修复

- 085927Z 批次于 09:22 结束：COMPLETE_WITH_ERRORS，24 个 arm 均发生连接错误，
  完整完成 0。SF01 在 09:17 前曾多次 API 成功；代理消失的直接触发者未证实。
- CPU submitter 不再预先反复 start/stop proxy。整批 runner 独占锁，拒绝接管
  现有存活代理；只在开始启动一次、退出清理一次，并核对 PID 避免误关替代代理。
  这是合作式保护，不能阻止手动 stop 或未更新旧脚本直接关闭全局代理。
- CPU tmux 使用 pipefail + tee：控制台输出同时保存到 batch.log；逐 case/arm
  和子进程开始结束记录立即显示，详细子任务输出仍在对应 stdout/stderr 文件。
- COMPLETE_WITH_ERRORS 返回非零，外层保留失败标记，不再写 SUCCESS。
- 29 项相关测试通过；未修改 checker、模型或代理本体，也未自动扩大实验。
- 用户要求预检成功才提交。真实小请求：healthz=ok，但 Responses 上游 HTTP 503；
  预检代理已清理。因此未再次提交批次，需要 API 恢复后再做预检。
- 后续排查：本地代理对上游 HTTP 状态透传，网络错误合成为 502，故之前 503 来自上游。
  未改配置复测：本地路径 HTTP 200/5.31 秒、直连上游 HTTP 200/2 秒；暂时恢复，
  上游内部原因未确定。没有以重试成功冒充永久修复。
- 预检成功后已按用户授权提交 `adsl_tsf12_cpu_20260912T100549Z`，目录
  `local_experiment/topology_standing_fea_12_cpu_20260912T100549Z/`；pane_dead=0，
  SF01/adsl 已启动，控制台/文件双路进度输出。代理只在正式批次开始/结束操作。
- 2026-09-14 planned-checks 恢复：SF05 规划遇到 StepCode HTTP 502，上次批次
  停在规划阶段，未执行 checker；不是物理不达标。现将 API/模型输出/请求超时
  隔离到单例，保留有效部分计划，其他独立案例继续；编程和冻结资产错误仍停止。
  StepCode 未知用量保留 RESERVED，不计为零，也不阻断另一案例；新 API 严格
  预算规则不变。恢复不重试 SF05，不重置原 8 小时截止时间或单例预算。
  本轮仍是 plan + baseline only，混合编辑未接通；97 项相关测试通过。
  后续 agent 循环上限按用户约定设为 4 轮，不增加每例 2 次编辑预算；本轮不改。
- 2026-09-15：按用户要求停止 `prompt8_fixed_20260915`，仅终止本批调度及
  SF27 worker，保留资产、独立代理和 GPU keeper。SF01 四轮补丁后的 TypeError
  源于 handoff 写顶层 overhang_experiment，而 _repair 读 request 下该字段，
  误走普通流程返回 None。最小修复统一写入嵌套字段；98 项相关模拟测试通过，
  新增真实 handoff → _repair 的 CHANGED/NO_CHANGE/TOOL_ERROR 覆盖。
  此前候选未完成验收，不能作为修复无效的物理证据。SF03 另有上游 500/TLS EOF，
  与此 bug 不同；未声称已修复 API。实验未重启、预算未重置、代码尚未推送。
- 2026-09-15 后续审查修复：选中的 FEA 不再因 topology 缺失被删除，改为依赖
  阻塞/INDETERMINATE，独立工具继续；计划无效/NEEDS_SPEC 保留在最终记录，
  必需项目未验证不报联合通过。one_piece 分量数增加不能被过悬下降抵消。
  handoff 完整保存有效 request，恢复不默认增加原 4 轮上限。程序 TypeError 等
  保存诊断并上报 FLOW_ERROR 停批，不再重复消耗轮次；单例 API 错误仍隔离。
  166 项轻量测试通过，包括完整 mock 修复闭环与 retained 发布一致性。
  未修改几何内核/阈值，未启动实验或调用 API，历史失败记录未改，尚未推送。
