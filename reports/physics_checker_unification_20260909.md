# 物理 Checker 统一、源码定位与受控修复实施报告

日期：2026-09-09
分支：`physics-checker-unification`

## 1. 结论

计划中的反馈统一、源码索引、候选定位、结构化修复、隔离执行和确定性验收已经接入现有 aDSL agent 流程。旧版 checker JSON 仍可读取；新版每轮保存坐标/单位/配置上下文、类型化 finding、源码候选和完整候选决策。Agent 只提出有证据且有边界的候选，是否接受由控制器根据实际重跑结果决定。

一次真实 StepCode + GPU Eevee + MuJoCo 端到端实验完成并通过：球形底座弧形落地灯的自然沉降峰值倾角从 `97.1569°`（FAIL）降到 `0.3904°`（PASS），最终倾角从 `96.9103°` 降到 `0.2251°`。阈值始终固定为 `25°`，没有通过修改 checker 配置取得通过。

## 2. 实现范围

### 2.1 统一反馈与上下文

- `CheckerResult` 新增 protocol v2 findings，同时保持 v1 兼容。
- finding 明确记录规则、类别、适用性、指标/阈值/单位、区域、证据、repairability 和 source candidates。
- `AnalysisContext` 固定源码、几何、checker 配置 hash，以及单位、坐标变换、使用姿态和打印姿态。
- 基础设施故障、证据不足和语义缺失不再伪装成几何缺陷；required checker 的 `ERROR` fail closed。

### 2.2 SourceIndex 与定位

- 执行时联合 Python AST 与运行时 `Asset` 层级生成 `source_index.json`。
- 索引包含稳定 feature/source ID、类和调用行区间、父子关系与运行时 AABB。
- 自定义 Asset 的运行时实例同时关联外层 `attach_part` 和构造类源码，因此修改内部构造类时不会误判越界。
- 直接 part ID 优先；有显式变换时才能做 checker 坐标到源码几何坐标的匹配。
- AABB 和全局支撑/质量启发式只用于候选召回，并显式保留歧义，不声称物理因果。

### 2.3 候选修复与确定性策略

- Engineering Critic 输出 `RepairProposal`：finding、假设、证据、feature/source IDs、允许范围、参数边界、保持条件和需要重跑的 checker。
- 每个候选从当前基线创建隔离目录；控制器校验 AST 修改范围和不可变输入 hash。
- 所有已注册 required checker 均重跑；此前 PASS 不得退化，目标必须 PASS 或超过容差改善，其他可比规则不得显著恶化，外观/功能评审必须通过。
- 接受后才原子提升源码；拒绝候选留档但不覆盖基线。候选数、总耗时和重复 fingerprint 都受策略控制。

### 2.4 每轮审计产物

每轮保存 `analysis_context.json`、`source_index.json`、`findings.json`、`localization.json`、`repair_proposals.json`；每个候选保存源码、执行产物、全部 checker 证据、图像评审和 `decision.json`。工作区另有 `repair_history.jsonl` 防止重复尝试。

## 3. 真实端到端验证

### 3.1 输入与固定条件

- 输入：已有 `standing_u05` 球形底座弧形落地灯，不是为本次测试临时手写的诊断 fixture。
- 模式：`edit --check-first`，第一轮先逐字节检查原源码。
- LLM：LMS StepCode `gpt-5.6-sol`；每次任务 wrapper 在退出时停止本地代理。
- 渲染：A800 上的 Blender Eevee，8 视角，512×512，64 samples。
- 物理：MuJoCo 刚体凸碰撞自然沉降代理；峰值倾角严格大于 `25°` 判定倾倒。

### 3.2 基线失败证据

| 指标 | 基线 |
|---|---:|
| 状态 | FAIL |
| peak tilt | 97.1569146597° |
| final tilt | 96.9102611159° |
| 阈值 | 25° |
| minimum force / weight | 0.05 |

定位器没有把整体倾倒强行归因到单一代码行，而是返回接触支撑平面的 `base`、`weighted_body` 与有明显水平偏心的 `shade` 等歧义候选。proposal 根据源码读取和支撑面证据选择局部底座修复，并把范围限制到 `SphericalBase`/其外层构造处。

### 3.3 Agent 修改了什么

最终 proposal 在 `SphericalBase` 中保留原球体不变，只增加一个与球体相交、底面位于 z=0 的低矮圆柱脚：

```python
foot = Cylinder(
    radius=0.15,
    height=0.06,
    center=(0.0, 0.0, 0.03),
    axis="z",
    color=DARK_METAL,
)
self.foot = self.attach_part("stability_foot", foot)
```

AST 范围校验结果为 `valid=true`，实际唯一 changed symbol 是 `class:SphericalBase`；灯杆、弧形臂、灯罩、灯泡、姿态和 checker 配置均未修改。

### 3.4 重跑与接受结果

| 指标 | 基线 | 候选 |
|---|---:|---:|
| checker status | FAIL | PASS |
| peak tilt | 97.1569° | 0.3904° |
| final tilt | 96.9103° | 0.2251° |
| minimum force / weight | 0.05 | 0.10 |

图像 critic 通过，判断球形底座弧形灯的轮廓和比例得到保留；新增脚居中、低矮、视觉从属，只在球体下方形成窄边。额外的 8 视角像素复核得到：平均归一化 RGB MAE `0.000476`，差异大于 10/255 的像素平均占 `0.246%`。像素指标只描述变化范围，不代替感知或功能证明。

确定性 `decision.json` 最终为 `accepted=true`，原因是目标 standing checker 达到 PASS、无 required regression、外观评审通过且修改范围合法；工作区 `source.py` 与接受候选字节一致。

## 4. 实施中发现并修复的问题

- 自定义 Asset 的源码依赖起初只指向外层 attach 行，合法修改内部构造类被误判越界；SourceIndex 现同时记录构造类依赖。
- Engineering Critic 的自由字典参数与 SDK strict JSON schema 不兼容；该 agent 改为非 strict schema，并保留 Pydantic 解析验证。
- StepCode 曾出现瞬时 502/503；配置改为有限重试 2 次，仍不会无限重试或吞掉最终错误。
- GPU worker 共享盘 heartbeat 单次读取可能抖动；队列增加入队前后 5 秒 grace，并把真实 worker 丢失分类为基础设施错误，禁止交给 Coder“修几何”。
- Code Critic 未读取源码时不再让整个闭环崩溃，但也不会被视为通过；候选会 fail closed。
- 挂载数据盘上的 SQLite session 曾返回 `disk I/O error`。实验 checkpoint 复制到项目盘后通过 `PRAGMA integrity_check` 并成功 resume。建议 session/SQLite/频繁小文件写入项目盘，完成后的大体积 solver/render 证据再归档数据盘。

## 5. 验证与兼容性

- 完整测试：`104 passed`。
- 旧 checker protocol v1 有保守升级测试；缺字段保持缺失，不填假值。
- 覆盖坐标变换、歧义定位、索引失效、构造类依赖、范围保护、配置保护、目标改善、回归拒绝、未知/ERROR、重复候选、预算和 GPU 基础设施路由。
- CLI 对 create/edit/resume 支持 `--repair-policy-config`，未配置时原有非修复工作流仍可使用。

## 6. 产物位置

本地完整实验：

`local_experiment/physics_checker_unification_20260909_u05_local/`

关键证据：

- 基线：`rounds/round_02/checkers/standing/result.json`
- proposal：`rounds/round_02/candidates/01_stability-foot-spherical-base-r2/proposal.json`
- 候选物理结果：`rounds/round_02/candidates/01_stability-foot-spherical-base-r2/checkers/standing/result.json`
- 外观评审：`rounds/round_02/candidates/01_stability-foot-spherical-base-r2/image_critique.json`
- 最终决策：`rounds/round_02/candidates/01_stability-foot-spherical-base-r2/decision.json`
- 最终模型/源码：工作区根下 `scene.glb`、`source.py`、`source_index.json` 和 `render/`

数据盘上保留了较早 checkpoint 和中间证据：

`/jiigan-hp/lms/aDSL/experiment/physics_checker_unification_20260909_u05/`

## 7. 当前边界

- 这次 PASS 是固定 MuJoCo 代理条件下的筛查结果，不是真实产品、打印或安全认证。
- 第一版没有精确 triangle/boolean-to-source provenance；动态 Python、循环生成名称和未知 DSL 写法可能 partial/unresolved。
- AABB 相交、支撑面和质量偏心只是定位证据，不是因果置信度。
- 缺少坐标变换、载荷、材料或关键表面语义时，系统会阻止自动修复，不会猜测。
- 自然语言 preserve 条件中未形式化的部分仍依赖视觉 critic，只能作为辅助证据；严格功能条件应继续转成可测规则。
- FEA、progressive、overhang/support 已统一接入同一协议和控制器，但本次轻量验收只实跑 standing 修复闭环，不据此声称其他分析器获得新的物理实验结论。
