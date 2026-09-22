# Fixed assembly physics 接入：实现及小规模验证

当前 FEA 绑定、位移协调和 CoACD 问题汇总见
[问题清单](assembly_physics_open_issues_20260922.md)。实现接通不代表真实装配已经全部验证。

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-09-22
- Verification Status: PARTIALLY_VERIFIED
- Version Label: assembly_physics_v1_working
- 实际基线：`766db7da00b98e8583adcdffa1970c957119879a`，本轮改动尚未提交。

## 结论与边界

**接入代码已写入工作区，但尚未完成整个计划的真实验收，不能称四工具已全部跑通。**
下文工具验证阶段未启动 API；后续已按用户要求启动单例 SF03，见文末启动记录。
没有人工修改生成模型，没有推送，没有重跑历史批次。
保留原 Image/Code 裁决、单次 Coder 修补入口、共同预算、working/retained 和故障隔离。
旧整件物理工具、EXACT/ASCII STL/float64 NPZ、几何阈值没有修改。

| 项目 | 已有证据 | 当前结论 |
| --- | --- | --- |
| 多工具单循环 | 模拟外观拒绝、多问题合流、单工具超时、方向退步回退 | 通过模拟回归；不能代替真实 agent 修补 |
| 打印方向与过悬 | L 件两方向真实表面测量；现有 T 支架两次真实 Blender 导出 | 已验证旋转/读取链；并非每次旋转都会改善 |
| 自重 MuJoCo | 精确测试凸块下运行完整 5 s；脱离及已知倾倒对照 | 事件判定可工作；不代表 CoACD 自动代理有效 |
| 自重生产路径 | CoACD 严格误差分解超时；正常例末段仍运动 | 尚未验证；不得报稳定 PASS |
| 分件 FEA | 两件独立 C3D10 网格、理想 tie、反力及位移协调、去 tie、解析杆对照 | 指定简单例真实传力成立；不是普遍可求解保证 |
| FEA 数值可靠性 | 细网格位移 +2.98%，峰值应力 +35.34%；现有 T 支架协调残差未过 | 应力峰值未证收敛；T 支架结果 INDETERMINATE |
| 真实 agent 闭环 | 未运行 | 按计划先完成真实工具门槛，不用 API 掩盖工具问题 |

## 修改位置

- `adsl-core/core/assembly.py`：逐件纯打印旋转 API。
- `adsl-core/core/export/export_assembly.py`：两个导出分支使用 `T @ R`；总装姿态及同网格复用不变。
- `adsl-agents/assembly_physics.py`：现有 checker 子进程薄适配、版本匹配的件内实体读取、短失败状态、方向比较。
- `adsl-agents/assembly_standing.py`：独立刚体、均匀实体质量/惯量、CoACD 代理验证、纯重力、接口退出和静置记录。
- `adsl-agents/assembly_overhang.py`：复用既有角度/平台排除/面积不确定度/区域函数，不运行 slicer、不整件缩放。
- `adsl-agents/assembly_fea.py`、`experiments/load_bearing_structural_performance/assembly.py`：分件 Gmsh、C3D10、局部 tie、载荷/支撑、字段/反力解析及可视化。
- `adsl-agents/fixed_assembly.py`、`assembly_topology.py::engineer()`：多路反馈交同一个 Coder；物理未验证不被外观批准覆盖；过悬方向优化单独比较。
- `experiments/fixed_assembly_prompt/run.py`：明确 tools/physics/尺寸入口，冻结配置，真实 ObjectRequest 接收打印方向权限。旧 topology 布尔参数兼容。
- 固定装配提示、optional extras、`examples/fixed_assembly/physics.json`、`tests/test_assembly_physics.py`：必要配置/文档/测试。

## 真实工具证据

根目录：`temp/assembly_physics_smoke_v1/`。旧失败目录均保留；下列结果不是批量模型成绩。

### 自重观察：完整 5 秒，不是 0.04 秒停止

当前 `duration_seconds=5`、`timestep_seconds=.002`，只施加重力。
`assessment_time_seconds`、`simulated_duration_seconds` 均为 5；首个脱离时刻只是历史事件。
输出以第 5 秒最终倾角、接口状态和末段运动为主，同时保留峰值和最早异常。
未取消过程中确实发生的倾倒/脱离，也未放宽静置要求。

`mujoco_five_second_report/test_real_mujoco_with_exact_fi0/`：

| 测试 | 5 s 最终结果 | 说明 |
| --- | --- | --- |
| seated | 倾角约 0.131°、无退出，末段仍运动 | 未倾倒不等于已静置；不能标完整稳定 PASS |
| exit | 接口在 5 s 仍退出，首次约 0.04 s | 记录最终与最早事件，不提前结束 |
| tipping | 最终约 76.05°、峰值约 77.19° | 正确识别超过 25° |

目录含 `results/model.xml`、`trajectory.json/.npz`、`test_result.json`、`simulation_*.png`。
这些是与测试实体一致、经几何校验的解析凸块，**仅用于隔离 MuJoCo 判定测试**；未作为生产 CoACD 的替代路径。
首轮解析凸块在独立中心/半宽计算中产生约 1e-15 mm 接缝，校验正确拒绝；后用共享边界值构造同一测试几何，未放宽容差。

CoACD 1.0.14 的 `threshold_mm=.02`、preprocess off、无 extrusion/decimation：严格分解仍超时。
现有 T 支架 20 s 诊断预算停在 `crossbar`，返回 `INDETERMINATE/CHECKER_TIMEOUT`。
独立 native 双精度对照仍超时，**未采纳为生产修复**。
凸立方体诊断显示初始归一化表面距离成本约 0.006629，大于目标 0.000533；体积成本约 0.000425。
这说明该次过度切分不只由落盘精度导致，不代表 CoACD 永远无法成功，也不证明所有资产都会超时。
没有为成功放宽代理误差、封槽、换整件凸包或修改摩擦/质量。

### 过悬及真实导出

- L 件真实测量：已验证可靠下降至 0 的方向；不是实际支撑用量或可打印证明。
- `export_rotation/test_existing_example_print_ro0/`：复用仓库 T 支架源码；旋转前后使用姿态和局部实体不变，STL 打印旋转落盘。
- 这次 T 支架旋转面积 **增加 7.4399986 mm²**，比较不确定度 0.3857643 mm²，记录 `WORSE`；没有声称成功优化。
- 每件着色 GLB、区域 JSON 和测量结果在 `original/measure`、`rotated/measure`。

### FEA

`fea_complete/test_real_fea_mesh_tie0/results/`：两件传力例，约 16.8 s，位移 0.02117 mm、应力 2.81796 MPa；反力与跨接口位移校验通过，保存 deck、映射、场图。

`fea_controls/test_real_fea_finer_mesh_untie0/validation.json`：

- 2.0 → 1.5 mm 网格：位移 +2.983%，应力 +35.343%，**峰值应力未证明网格收敛**。不能选有利网格声称结构可靠。
- 去掉 tie，求解器仍正常退出但位移约 3.956×10⁷ m；证明程序退出码不代表受约束解有效。
- 解析轴向杆：计算 1.31845×10⁻⁶ m，`FL/EA` 为 1.33333×10⁻⁶ m，约 -1.12%。
- 细网格和对照总约 72.9 s，没有改原物体参数或以所有零件全固定掩盖传力。

`existing_tools/checkers/assembly_fea/`：现有 T 支架的反力平衡通过，但接口位移复核残差约 9.06×10⁻⁹ m 超过当前数值检查界限约 2.73×10⁻¹⁰ m；保留 `NUMERICAL_RESULT_UNVERIFIED`，未放宽界限，也没有说结构强度 FAIL。该残差的成因尚未证实。

FEA 固定声明：`connection_model=ideal_bonded`、`external_load_stability=NOT_EVALUATED`；不验证真实摩擦固定、防拔出或外载抗倾倒。

### 真实独立工具进程

`existing_tools/summary.json`，约 69.9 s：Topology PASS；Standing 超时 INDETERMINATE；Overhang 测量 PASS；FEA 数值未验证。
**Standing 超时后，Overhang 和 FEA 都实际执行并落盘**，不是只靠模拟测试证明隔离。
没有调用 Image/Code/API，因此这不是完整 agent 成功案例。

## 命令与依赖

本轮实际测试：轻量相关回归 **77 passed / 5 skipped / 6 deselected**（skip 为显式 native 开关，不能算真实通过）；真实 Blender 打印旋转 **1 passed / 10.51 s**；真实 MuJoCo 五秒事件语义 **1 passed / 8.36 s**（解析测试代理，不含 CoACD）；最终接口区域修改后的两件 FEA **1 passed / 16.37 s**，路径 `fea_final_roi/`；细网格/去 tie/解析杆 **1 passed / 72.85 s**。CoACD 完整 standing 测试没有通过，早期失败材料保留。

主环境：`/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python`。
本轮独立 target `temp/assembly_physics_runtime`，CoACD 1.0.14、matplotlib 3.10.9；未覆盖生产 Python 环境。
另复用 MuJoCo 3.12.0、Gmsh 4.15.2、CalculiX 2.23。
诊断 CoACD fork 在 `temp/assembly_physics_coacd_source`，tag 1.0.14 / `1401ce2a7ae1ed89c65ab958b48d489350c233c7`；只在隔离对照导入，不是默认依赖。

```bash
# 轻量回归；native 测试默认 skip
/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q \
  tests/test_assembly_physics.py tests/test_assembly_feedback_recovery.py \
  tests/test_assembly_topology.py tests/test_fixed_assembly.py -k 'not real_boolean'

# 真实导出/方向读取，无 API；使用新 --basetemp，不覆盖上述证据
ADSL_TEST_FIXED_REAL=1 /vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python -m pytest -q \
  tests/test_assembly_physics.py -k existing_example_print_rotation
```

单工具统一 CLI（输入必须是同源版本的 manifest/source；输出用新目录）：

```bash
python -m adsl.agents.assembly_overhang \
  --manifest <asset/assembly/assembly_manifest.json> --source <source.py> \
  --physics examples/fixed_assembly/physics.json --output <new-output>
```

替换模块名为 `assembly_standing` 或 `assembly_fea` 可独立诊断；正式工作流经 `checker_spec/run_checker` 启动，必须保留外部进程组超时，不直接无限等待。
原实验入口现支持 `--tools assembly_topology assembly_standing assembly_overhang assembly_fea --physics-config FILE --final-size-mm X Y Z --max-rounds 5`。
该工具验证阶段尚未给 SF03 启动 API。后续用户明确要求先验证一个真实 case，
现已启动，限制如下：T 支架 `physics.json` 不直接套用家具；SF03 FEA 缺规格记
NEEDS_SPEC，不作为几何修补目标；standing 碰撞代理失败仍未验证。未扩大批次。

### 单例启动补记（2026-09-22）

- 目录：`temp/assembly_physics_SF03_20260922T082400Z/`；tmux：`adsl_physics_SF03_20260922T082400Z`。
- 真正进入现有 prompt-to-3D Planner，请求记录及空初始源码已核对。CLIProxy/gpt-5.6-sol；5轮评估、最多4次修补。
- 比较参考为已存 wo/SF03，不生成/回选控制组。采用同样90×80×180 mm尺寸和0.2 mm余量。
- FEA尚无适用工况和限值，本例其结果预期NEEDS_SPEC而不是强度FAIL；不声称验证了FEA修补能力。
- `EXPERIMENT.md`记录原始输入来源、旧对照哈希、冻结配置与结果边界；`code_snapshot.tar.gz`保存本次未提交实现。
- 当前仅确认启动；修补效果、候选选择、检查状态与API开销须据完成后的实际记录报告。

### 用户指定的 FEA 补全与全尺寸重启

上述缩尺单例已按用户要求停止，原结果保留。用户选择 450×400×900 mm，
座面1000 N／靠背300 N及自重。新增 `examples/fixed_assembly/physics_sf03_chair.json`，
复用现有语义区域选择、等效PLA参数和25 MPa／5 mm筛查限值，不重构求解器。
6项轻量测试通过，真实两件传力冒烟1passed/16.75s（反力/位移协调通过）。

新单例 `temp/assembly_physics_SF03_fullsize_20260922T085200Z/` 已启动原始prompt生成；
Planner实际输入已核对包含完整FEA条件，未提供旧源码/渲染。最多4次修补，
不是旧记录续跑，不与旧缩尺对照直接比数值。此处仅确认启动，尚无新SF03效果结论。

## 尚待完成

1. CoACD 自动代理须在冻结误差内保留榫槽且预算内完成；当前没有达成。
2. 正常两件落座例的数值静置需确认，不能把未倾倒等同于稳定 PASS。
3. T 支架 FEA 的位移协调复核残差需确定是测量实现还是数值约束问题，当前只能未验证。
4. 真实 Engineering → Coder → 全工具复查/API 冒烟尚未运行。

上述阻点不通过换更宽松阈值、手改生成源码、增加案例或额外轮次绕过。
