# Standing 倾倒定位增强 v2：实施与离线验收

日期：2026-10-23。分支：`action_v2`。基准提交：`d355518d4dfa7fdae17c96d614b08f57d95262f3`。

本报告随实现保存在独立工作树的 `action_v2` 提交中，具体提交身份以 Git 历史为准。本轮未合并、未推送；没有启动或恢复实验。原 `action_v1` 工作区的状态及核对的 13 份文件哈希均未改变，未将其已有评测改动带入本提交。

## 实际修改

仅修改两个生产文件，另新增一个测试文件、扩展两个现有测试文件和本报告。

| 文件与更新后位置 | 内容 |
| --- | --- |
| `adsl-agents/assembly_standing.py:207` | `_initial_tipping_reference`：首次既有 `mj_forward` 后复制质量、世界质心 `xipos`、根件旋转及有效初始地面接触。识别 floor 的两种接触顺序、geom/flex 所属打印件；排除未激活接触和件间接触。 |
| `adsl-agents/assembly_standing.py:263` | `_localize_tipping`：按首次越过原倾角阈值的方向，计算初始地面支撑边界与逐件重力力矩。严格正贡献排序，平手按 part ID，最多三件；占比分母为所有正贡献之和。 |
| `adsl-agents/assembly_standing.py:346` | `_normalized_tipping_localization`：拒绝非有限、身份错误、目标重复或无效的可选诊断，回退原根件。 |
| `adsl-agents/assembly_standing.py:388` | `simulate` 保留四个位置参数及 `(result, trajectory)` 返回；只在 tipped 时增加可选 `tipping_localization`。不增加求解器调用，不改变采样、阈值或 verdict。 |
| `adsl-agents/assembly_standing.py:583` | 先以原根件创建 `SELF_WEIGHT_TIPPING`，保留原 finding ID、消息、分类和权限；仅合法 RANKED 更新 `region.part_names`。 |
| `adsl-agents/agent_feedback.py:222`、`:345` | 有界白名单投影定位摘要，优先合法 raw domain，其次已有 key_values；不内联逐件完整贡献或接触数组，保留原 domain 的 detail_ref。 |
| `tests/test_standing_tipping_localization.py` | 新增手算、首事件、快照、地面身份与接口退出时间测试。 |
| `tests/test_assembly_standing_verdict.py:199` | verdict、finding 身份、合法/非法定位、旧返回兼容和既有 SourceIndex 关联。 |
| `tests/test_agent_feedback.py:112` | 实际 Engineering / Coder 输入入口的 stub 验证、重复投影、原件不变、完整引用和预算计量。 |

`assembly_physics.py`、`assembly_topology.py`、SourceIndex 匹配与六候选上限、Code Critic 路由、提示、配置和候选采纳/重试逻辑均未修改。

## 接口与回退

定位块使用 `method=initial_directional_gravity_moment_v1`，固定 `causal_claim=false`。初始质量单位 kg、质心与支撑边界单位 m、力矩单位 N·m。全部输出可严格 JSON 序列化，不以舍入或力矩 epsilon 删除微小正贡献；净力矩为负也不会直接否决正贡献候选。

三种 FALLBACK 均返回空候选，沿用原根件：

- `INTERFACE_EXIT_FIRST`：首次接口退出早于或等于首次 tipping 采样。
- `REFERENCE_UNAVAILABLE`：初始接触、质量、质心、方向、事件或诊断目标不可用。
- `NO_POSITIVE_CONTRIBUTION`：计算完成，但没有严格正贡献。

旧结果没有可选定位字段时不补造诊断。定位失败不改变 Standing 的 PASS/FAIL/ERROR/INDETERMINATE，也不掩盖原仿真失败。完整逐件贡献留在报告，Agent 只收到最多三个候选及短摘要；可按原 `detail_ref` 读取明细。贡献排名与 SourceIndex 的源码候选排序含义不同。

## 两个离线例子

**手算排名成功**：方向 `[1,0]`，支撑边界 `0.100 m`，重力 `9.81 m/s²`。head 质量 `0.2 kg`、COM x=`0.140 m`，贡献 `+0.07848 N·m`；body 质量 `0.8 kg`、COM x=`0.095 m`，贡献 `−0.03924 N·m`。净值 `+0.03924 N·m`，候选只有 head，正贡献占比为 1。测试同时核对整体 COM 恒等式与输入不变。

**接口先退出回退**：首次 tipping 为 `0.24 s`，接口在 `0.20 s` 或 `0.24 s` 首次退出，返回 `INTERFACE_EXIT_FIRST` 与空候选；`0.28 s` 才退出时可计算排名。该先后判断不是接口导致倾倒的因果证明。

这两项均为人工夹具，不是真实物体效果结论。

## 实际验证

在独立工作树创建私有 import overlay，并核对 `assembly_standing`、`agent_feedback`、`service` 与 `core.assembly` 的实际导入均解析到该工作树；没有修改共享 Python 环境的安装映射。日志及导入核对保存在未提交的 `local_experiment/standing_localization_v2/`。

使用现有生成环境，执行以下定向集合（`<environment-python>` 表示该环境解释器）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
OPENAI_AGENTS_DISABLE_TRACING=1 \
PYTHONPATH="$PWD/local_experiment/standing_localization_v2/import_overlay" \
ADSL_SESSION_ROOT="$PWD/local_experiment/standing_localization_v2/sessions" \
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 \
<environment-python> -m pytest -q \
  -o cache_dir="$PWD/local_experiment/standing_localization_v2/pytest_cache" \
  tests/test_standing_tipping_localization.py \
  tests/test_assembly_standing_verdict.py::test_tipping_localization_preserves_identity_and_verdict \
  tests/test_agent_feedback.py::test_tipping_localization_at_repair_boundaries_is_idempotent
```

**结果：25 passed in 6.07s，退出码 0。** `git diff --check` 通过。另有独立只读复核，未发现阻塞性问题。

六组合同均覆盖：两件手算；首次方向不被后续峰值覆盖；实际 floor/geom/flex 与世界 COM 快照；接口早于/等于/晚于 tipping；finding 身份与 verdict 保持；Engineering/Coder 实际输入和重复投影稳定。fake simulate 核对仅一次原有 forward、两次原有 step，没有额外求解器调用。现有小型人工几何夹具使用 Manifold 构造，模拟和模型请求均为 stub；未执行原生 MuJoCo 仿真。

## 结论与限制

离线验收支持：根件倾角触发 tipping 时，系统增加基于初始重力力矩的打印件检查候选，接入既有源码关联与 Engineering/Coder 输入，Standing 判定未因定位改变。

定位只覆盖原根件倾角触发，不能通用检测根件不倾倒时上部零件掉落。排名粒度是实际打印件，不能将同一打印件内部的语义头、腿独立归因。初始支撑边界与静力分解不解释全部滑移、冲击、接触变化；SourceIndex 也不保证每个高排名件都有源码匹配。

**测试已执行且通过；真实模型调用为零；真实生成、原生物理仿真、真实案例及 benchmark 均未执行。** 未验证真实仿真中的定位质量、修复成功率或轮次改善，不据此作制造性能结论。
