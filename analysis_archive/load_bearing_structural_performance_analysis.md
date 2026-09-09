# 03 Load-Bearing Structural Performance：当前案例验证

## Material Passport

- Material ID: `adsl-physics-03-load-bearing-20260904`
- Type: experiment execution and analysis report
- Origin Skill: `academic-research-suite/experiment-agent`
- Origin Mode: `run`
- Verification Status: UNVERIFIED
- Generated (UTC): 2026-09-04T09:00:25.083944+00:00

## 结论摘要

- 14 个模型中：SOLVED=4，INVALID_LOAD_PATH=7，NOT_MESHABLE=3，LOAD_REGION_AMBIGUOUS=0，求解失败/不收敛=0。
- 三网格 medium→fine 收敛门槛（位移≤10%、应力≤20%、屈曲因子≤10%）：4/4 个已求解模型通过。
- 细网格代理门槛（U/L≤1%、名义屈服 FoS≥2、线性屈曲因子≥2）：8/8 条已求解载荷轨通过；这不等于真实打印认证。
- `INVALID_LOAD_PATH` 表示模型由多个承载体组成，但没有可供 FEA 使用的 tie/contact/连接刚度；部分模型虽有 URDF 运动学 joint，也不能据此推导结构刚度。
- `NOT_MESHABLE` 表示当前 collision surface 不能形成经验证的封闭实体；不采用体素补洞来伪造可解模型。
- SOLVED 仍只是各向同性 PLA、全固定底面、线弹性、小变形和理想载荷下的筛查。打印方向、层间强度、缺陷、蠕变和非线性接触均未建模。

## 逐案例

| Case | Status | union components | watertight | coarse/medium/fine | convergence |
|---|---|---:|---|---|---|
| A01-cabinet | INVALID_LOAD_PATH | 4 | True | — | — |
| A02-faucet | INVALID_LOAD_PATH | 5 | True | — | — |
| E01-chair-slat-edit | SOLVED | 1 | True | SOLVED/SOLVED/SOLVED | PASSED |
| I01-example-image | SOLVED | 1 | True | SOLVED/SOLVED/SOLVED | PASSED |
| I02-example-arti | INVALID_LOAD_PATH | 4 | True | — | — |
| M01-base-motorcycle | INVALID_LOAD_PATH | 54 | True | — | — |
| M01-cyberpunk-edit | INVALID_LOAD_PATH | 85 | True | — | — |
| M01-cyberpunk-scratch | NOT_MESHABLE | — | — | — | — |
| T01-main | SOLVED | 1 | True | SOLVED/SOLVED/SOLVED | PASSED |
| T01-one-round-control | SOLVED | 1 | True | SOLVED/SOLVED/SOLVED | PASSED |
| T02-bookshelf | NOT_MESHABLE | — | — | — | — |
| T03-radial-wheel | INVALID_LOAD_PATH | 18 | True | — | — |
| T04-hollow-mug | NOT_MESHABLE | — | — | — | — |
| T05-patterned-desk | INVALID_LOAD_PATH | 18 | True | — | — |

## 已求解结果（fine mesh）

| Case | Load | max U (mm) | U/L (%) | max von Mises (MPa) | nominal FoS | first buckling factor | screen | hotspot centroid (m) |
|---|---|---:|---:|---:|---:|---:|---|---|
| E01-chair-slat-edit | self_weight | 0.015892 | 0.001766 | 0.0722464 | 692.076 | 633.0026 | SCREEN_PASS | `[-0.1694680851063825, 0.1694680851063825, 0.326010638297875]` |
| E01-chair-slat-edit | functional | 5.84812 | 0.6498 | 3.96289 | 12.617 | 120.2526 | SCREEN_PASS | `[0.1694680851063825, 0.1694680851063825, 0.4090319148936175]` |
| I01-example-image | self_weight | 0.0258754 | 0.00345 | 0.0922878 | 541.783 | 337.5952 | SCREEN_PASS | `[0.4077141162666175, 0.1958798882681225, 0.57530466522363]` |
| I01-example-image | functional | 0.0578411 | 0.007712 | 0.199461 | 250.676 | 167.9212 | SCREEN_PASS | `[0.406511619394855, 0.16656348201815002, 0.5730181913319226]` |
| T01-main | self_weight | 0.0163212 | 0.001813 | 0.0685661 | 729.224 | 817.9162 | SCREEN_PASS | `[0.1694680851063825, 0.1694680851063825, 0.32601063829787497]` |
| T01-main | functional | 5.58664 | 0.6207 | 3.65459 | 13.6814 | 121.6178 | SCREEN_PASS | `[0.1694680851063825, 0.1694680851063825, 0.4090319148936175]` |
| T01-one-round-control | self_weight | 0.015113 | 0.001679 | 0.0728799 | 686.06 | 808.1439 | SCREEN_PASS | `[0.208125, 0.18562499999999998, 0.40286706198437]` |
| T01-one-round-control | functional | 1.79343 | 0.1993 | 1.78237 | 28.0525 | 167.66 | SCREEN_PASS | `[-0.208125, 0.18341078125, 0.4959120730380925]` |

## 当前模型的阻断性问题

- A01/A02/I02：运动件与主体分离；运动学 URDF joint 没有定义 FEA 锁定状态、连接刚度、接触或 tie。
- 三个 motorcycle 分别得到 54、85 个独立体或空 Boolean 结果；需要封闭实体、焊接/接触定义和车轮—地面支承。
- T02 bookshelf 与 T04 mug 不能形成封闭 collision 实体；必须验证 CAD/打印网格修复后才能做 FEA。
- T03 wheel 精确 union 后仍有 18 个独立体；缺少 spoke/rim/hub 连续性及 hub 约束。
- T05 desk 有 18 个独立体且无语义子部件区域；需要桌面—桌腿连接及载荷表面标注。

## 工具、输入与边界

- Gmsh 4.15.2/OpenCASCADE：primitive collision 的 Boolean fuse 与 C3D10 二次四面体网格。
- CalculiX 2.23/SPOOLES/ARPACK：线性静力与线性特征屈曲；CPU 求解，不需要 GPU。
- 应力为积分点 von Mises 最大值；位移为节点向量模最大值；薄弱区输出对应单元四角点质心。
- nominal FoS = 50 MPa / max von Mises，只是名义屈服裕度；不包含层间、疲劳、蠕变、冲击或统计材料散差。
- 屈曲 factor 乘以相应参考载荷才是线性特征屈曲临界载荷；存在接触、几何缺陷或大变形时通常会高估真实能力。
- functional load 的语义区域由原 URDF collision 名称定位，再映射到网格表面节点；映射失败必须 fail closed。

## 复现信息

- Input root: `/jiigan-hp/lms/aDSL/experiment/audit_20260830`
- Output root: `/jiigan-hp/lms/aDSL/experiment/physics_analysis/03_load_bearing_structural_performance`
- CalculiX: `This is Version 2.23`
- Gmsh: `4.15.2`
- Command: `experiments/load_bearing_structural_performance/analyze.py --output-dir /jiigan-hp/lms/aDSL/experiment/physics_analysis/03_load_bearing_structural_performance --solver-timeout 900`
- 原始模型未修改；逐级 deck、日志、FRD/DAT 和机器可读结果均在 output root。
