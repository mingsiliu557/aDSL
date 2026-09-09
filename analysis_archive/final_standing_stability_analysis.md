# 01 Final Standing Stability：现有 aDSL 模型验证

## Material Passport

- Material ID: `adsl-final-standing-stability-20260903T094827Z`
- Type: experiment validation report
- Verification Status: ANALYZED
- Generated (UTC): 2026-09-03T09:48:27.621289+00:00
- Input root: `/jiigan-hp/lms/aDSL/experiment/audit_20260830`
- MuJoCo coverage: 18/18 non-excluded states
- Free-settle falls (>25°): 0/18 non-excluded states

## 结论边界

本报告是均匀密度假设下的几何预筛与 MuJoCo 刚体代理实验，不是工程认证。原始 URDF 没有质量、密度和惯性；未建模材料分布、装配间隙、柔性、地面不平、制造误差或锚固。`STABLE_CANDIDATE` 仅表示在这些假设下未发现致命站立问题。
`Verdict` 是支撑区、质心与 MuJoCo 的综合结构筛查；是否在仿真中实际倒下单独看 `Natural fall >25°`，两者不能混写。

## 方法与规则

- 坐标：URDF 使用 Z-up；GLB 的 Y-up→Z-up 旋转由 URDF collision origin 保留。
- 支撑区：取最低点以上 `max(1e-8, bbox_diagonal×1e-6)` 的接触顶点，投影到 XY 后求凸包。少于二维或面积近零即为退化支撑。
- 质心：分别计算均匀实体体积质心与均匀薄壳面积质心。任何 collision 非 watertight 时，实体质心不发布。多部件相交可能重复计质量，保留为限制。
- 分析对象：稳定性计算采用 URDF collision geometry；`scene.glb` 仅冻结并记录哈希。碰撞代理与视觉网格不同的 case，结论只适用于代理。
- 裕度：质心投影到支撑凸包边界的带符号最短距离；正数在内、负数在外。归一化裕度除以模型水平包围盒对角线。
- 倾倒角：`atan(max(0, margin) / COM_height)`；16 个水平角度另算静态临界加速度。
- MuJoCo：密度 1000 kg/m³、摩擦系数 2.0；自由沉降和小冲量各观察 5 秒，并进行 16 向水平力阶梯；任一时刻最大倾斜角严格大于 25° 即记为倒下。primitive collision 保留；每个有体积的 mesh 连通分量由 MuJoCo 凸包碰撞代理；少于 4 顶点或共面的零体积碎片被丢弃并计数。
- 判定：退化支撑或两种质心均在外→`UNSTABLE`；质量模型冲突/实体质心不可得/纯 mesh 接触→`INDETERMINATE`；归一化裕度 <1% 或 ≤0.1 倍自重力倾倒→`MARGINAL`。
- 是否倒下：只采用用户指定规则——5 秒自由沉降期间最大倾斜角严格大于 25°；恰好 25° 不算倒下。水平力结果单独报告达到 >25° 的最小 F/W。

## 工具依据

- [Trimesh mass properties](https://trimesh.org/trimesh.base.html) 用于 watertight mesh 的体积质量属性；[SciPy spatial](https://docs.scipy.org/doc/scipy/reference/spatial.html) 提供二维 ConvexHull。
- [MuJoCo XML Reference](https://mujoco.readthedocs.io/en/stable/XMLreference.html)、[Computation](https://mujoco.readthedocs.io/en/latest/computation/) 与 [Python API](https://mujoco.readthedocs.io/en/stable/python.html) 是物理代理、接触和执行接口依据。

## 初始姿态汇总

| Case | Verdict | Confidence | Support area | Solid margin | Shell margin | Geometric tip angle | Natural fall >25° | Settle max tilt | Min F/W to >25° |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|
| A01-cabinet | UNSTABLE | high | 0.1776 | -0.2462 | -0.3013 | 0.00 | upright | 0.64 | 0.35 |
| A02-faucet | STABLE_CANDIDATE | medium | 0.3626 | 0.3385 | 0.3216 | 26.18 | upright | 0.00 | 0.75 |
| E01-chair-slat-edit | STABLE_CANDIDATE | medium | 1.1236 | 0.4160 | 0.3533 | 17.16 | upright | 0.01 | 0.50 |
| I01-example-image | STABLE_CANDIDATE | medium | 2.1168 | 0.4900 | 0.4900 | 17.83 | upright | 0.00 | 0.50 |
| I02-example-arti | STABLE_CANDIDATE | medium | 0.3071 | 0.1697 | 0.1461 | 12.50 | upright | 0.02 | 0.35 |
| M01-base-motorcycle | INDETERMINATE | low | 0.4400 | 0.0943 | 0.0883 | 6.28 | upright | 0.02 | 0.20 |
| M01-cyberpunk-edit | INDETERMINATE | low | 0.4400 | 0.0944 | 0.0891 | 6.12 | upright | 0.02 | 0.20 |
| M01-cyberpunk-scratch | INDETERMINATE | low | 0.4600 | — | 0.0996 | 7.34 | upright | 0.01 | 0.20 |
| S01-living-room | EXCLUDED | not_applicable | 21.8400 | 1.4872 | 1.6016 | 61.03 | — | — | — |
| T01-main | STABLE_CANDIDATE | medium | 1.1236 | 0.4364 | 0.3819 | 19.44 | upright | 0.00 | 0.50 |
| T01-one-round-control | STABLE_CANDIDATE | medium | 1.1000 | 0.4262 | 0.3773 | 20.67 | upright | 0.00 | 0.50 |
| T02-bookshelf | INDETERMINATE | low | 1.2400 | — | 0.2835 | 10.28 | upright | 0.01 | 0.35 |
| T03-radial-wheel | UNSTABLE | high | 0.0000 | — | — | 0.00 | upright | 0.00 | 0.20 |
| T04-hollow-mug | INDETERMINATE | low | 1.3188 | — | 0.5648 | 39.41 | upright | 0.01 | 1.00 |
| T05-patterned-desk | INDETERMINATE | low | 1.5912 | — | 0.3663 | 16.14 | upright | 0.01 | 0.50 |

## 逐项结果

### A01-cabinet

- URDF SHA256: `536a345ff3cb9cc64bba8c5670516daa0e9164493a005c196c67991e79a50792`
- Movable joints: 3
- Initial / worst-state verdict: `UNSTABLE` / `UNSTABLE`

**initial** — `UNSTABLE` (high)

- Joint values: `{"left_door_hinge": 0.0, "right_door_hinge": 0.0, "upper_drawer_slide": 0.0}`
- Support: 4 points, rank=2, area=0.1776, mesh-only=True.
- Uniform solid: available=True, margin=-0.2462, normalized=-0.0946, tip angle=0.00°.
- Uniform shell: available=True, margin=-0.3013, normalized=-0.1157, tip angle=0.00°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, COM_PROJECTION_OUTSIDE_SUPPORT, MUJOCO_DID_NOT_OVERRIDE_GEOMETRIC_FAILURE`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.64°, settle final tilt=0.42°, contacts=4, min observed F/W=0.35, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.

**positive_85pct_limits** — `UNSTABLE` (high)

- Joint values: `{"left_door_hinge": 1.632, "right_door_hinge": 1.632, "upper_drawer_slide": 0.3825}`
- Support: 4 points, rank=2, area=0.1776, mesh-only=True.
- Uniform solid: available=True, margin=-0.0562, normalized=-0.0172, tip angle=0.00°.
- Uniform shell: available=True, margin=-0.1177, normalized=-0.0360, tip angle=0.00°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, COM_PROJECTION_OUTSIDE_SUPPORT, MUJOCO_DID_NOT_OVERRIDE_GEOMETRIC_FAILURE`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.57°, settle final tilt=0.41°, contacts=4, min observed F/W=0.50, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### A02-faucet

- URDF SHA256: `c46ae1e06ffd47cb409ea63b21aad8d442382ac93af14984abba579e6c9dc060`
- Movable joints: 3
- Initial / worst-state verdict: `STABLE_CANDIDATE` / `STABLE_CANDIDATE`

**initial** — `STABLE_CANDIDATE` (medium)

- Joint values: `{"central_lever_hinge": 0.0, "left_knob_rotation": 0.0, "right_knob_rotation": 0.0}`
- Support: 65 points, rank=2, area=0.3626, mesh-only=False.
- Uniform solid: available=True, margin=0.3385, normalized=0.2480, tip angle=30.71°.
- Uniform shell: available=True, margin=0.3216, normalized=0.2356, tip angle=26.18°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.00°, settle final tilt=0.00°, contacts=3, min observed F/W=0.75, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.

**positive_85pct_limits** — `STABLE_CANDIDATE` (medium)

- Joint values: `{"central_lever_hinge": 0.5525, "left_knob_rotation": 0.5525, "right_knob_rotation": 0.5525}`
- Support: 65 points, rank=2, area=0.3626, mesh-only=False.
- Uniform solid: available=True, margin=0.3369, normalized=0.2360, tip angle=30.65°.
- Uniform shell: available=True, margin=0.3230, normalized=0.2262, tip angle=26.38°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.00°, settle final tilt=0.00°, contacts=3, min observed F/W=0.75, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.

**negative_85pct_limits** — `STABLE_CANDIDATE` (medium)

- Joint values: `{"central_lever_hinge": -0.2975, "left_knob_rotation": -0.5525, "right_knob_rotation": -0.5525}`
- Support: 65 points, rank=2, area=0.3626, mesh-only=False.
- Uniform solid: available=True, margin=0.3371, normalized=0.2361, tip angle=30.57°.
- Uniform shell: available=True, margin=0.3225, normalized=0.2259, tip angle=26.18°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.00°, settle final tilt=0.00°, contacts=3, min observed F/W=0.75, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### E01-chair-slat-edit

- URDF SHA256: `fbd8d5331b3dd5edc1729df703a6bb3894b76ae6857a618c7b178fc2ea6b2938`
- Movable joints: 0
- Initial / worst-state verdict: `STABLE_CANDIDATE` / `STABLE_CANDIDATE`

**initial** — `STABLE_CANDIDATE` (medium)

- Joint values: `{}`
- Support: 16 points, rank=2, area=1.1236, mesh-only=False.
- Uniform solid: available=True, margin=0.4160, normalized=0.2452, tip angle=21.18°.
- Uniform shell: available=True, margin=0.3533, normalized=0.2082, tip angle=17.16°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.01°, settle final tilt=0.01°, contacts=16, min observed F/W=0.50, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### I01-example-image

- URDF SHA256: `b5ea05d2b8644137ef39af0af337de6930c58f5010251dac477eb54bbd33feb0`
- Movable joints: 0
- Initial / worst-state verdict: `STABLE_CANDIDATE` / `STABLE_CANDIDATE`

**initial** — `STABLE_CANDIDATE` (medium)

- Joint values: `{}`
- Support: 16 points, rank=2, area=2.1168, mesh-only=False.
- Uniform solid: available=True, margin=0.4900, normalized=0.1856, tip angle=17.83°.
- Uniform shell: available=True, margin=0.4900, normalized=0.1856, tip angle=18.65°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.00°, settle final tilt=0.00°, contacts=16, min observed F/W=0.50, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### I02-example-arti

- URDF SHA256: `5c26658e249332651a3ab97075aca0ac73a928711dd3dcb5fa3b23e115fc5c2d`
- Movable joints: 3
- Initial / worst-state verdict: `STABLE_CANDIDATE` / `STABLE_CANDIDATE`

**initial** — `STABLE_CANDIDATE` (medium)

- Joint values: `{"bottom_drawer_slide": 0.0, "middle_drawer_slide": 0.0, "top_drawer_slide": 0.0}`
- Support: 4 points, rank=2, area=0.3071, mesh-only=False.
- Uniform solid: available=True, margin=0.1697, normalized=0.1357, tip angle=14.07°.
- Uniform shell: available=True, margin=0.1461, normalized=0.1168, tip angle=12.50°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.02°, settle final tilt=0.01°, contacts=4, min observed F/W=0.35, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.

**positive_85pct_limits** — `STABLE_CANDIDATE` (medium)

- Joint values: `{"bottom_drawer_slide": 0.2975, "middle_drawer_slide": 0.2975, "top_drawer_slide": 0.2975}`
- Support: 4 points, rank=2, area=0.3071, mesh-only=False.
- Uniform solid: available=True, margin=0.1001, normalized=0.0700, tip angle=8.41°.
- Uniform shell: available=True, margin=0.0257, normalized=0.0180, tip angle=2.23°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.08°, settle final tilt=0.05°, contacts=4, min observed F/W=0.20, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### M01-base-motorcycle

- URDF SHA256: `3192304b3b3ed2fe6c201cf9e7acd982569454be4871271bbb33f6334e9b6907`
- Movable joints: 0
- Initial / worst-state verdict: `INDETERMINATE` / `INDETERMINATE`

**initial** — `INDETERMINATE` (low)

- Joint values: `{}`
- Support: 4 points, rank=2, area=0.4400, mesh-only=True.
- Uniform solid: available=True, margin=0.0943, normalized=0.0286, tip angle=6.76°.
- Uniform shell: available=True, margin=0.0883, normalized=0.0267, tip angle=6.28°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.02°, settle final tilt=0.01°, contacts=4, min observed F/W=0.20, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### M01-cyberpunk-edit

- URDF SHA256: `d4117d7d204e87c56a5497f6aa6935b43acf692de685be4dde8bb43247b8ff34`
- Movable joints: 0
- Initial / worst-state verdict: `INDETERMINATE` / `INDETERMINATE`

**initial** — `INDETERMINATE` (low)

- Joint values: `{}`
- Support: 4 points, rank=2, area=0.4400, mesh-only=True.
- Uniform solid: available=True, margin=0.0944, normalized=0.0286, tip angle=6.67°.
- Uniform shell: available=True, margin=0.0891, normalized=0.0270, tip angle=6.12°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.02°, settle final tilt=0.01°, contacts=4, min observed F/W=0.20, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### M01-cyberpunk-scratch

- URDF SHA256: `44b2ca6d7f2bb00bf65ed3fcdb872d558d3f73e339bee548282b6854a8c7cd7c`
- Movable joints: 0
- Initial / worst-state verdict: `INDETERMINATE` / `INDETERMINATE`

**initial** — `INDETERMINATE` (low)

- Joint values: `{}`
- Support: 4 points, rank=2, area=0.4600, mesh-only=True.
- Uniform solid: available=False, margin=—, normalized=—, tip angle=—°.
- Uniform shell: available=True, margin=0.0996, normalized=0.0299, tip angle=7.34°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, SOLID_COM_UNAVAILABLE_NON_WATERTIGHT, MUJOCO_DROPPED_DEGENERATE_MESH_COMPONENTS, MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.01°, settle final tilt=0.01°, contacts=4, min observed F/W=0.20, impulse tipped directions=0/16, dropped zero-volume mesh fragments=9.
### S01-living-room

- URDF SHA256: `3dfe628b62268cbc55242b7f864221b758d2d07d994d3917af9e2aada6359ee9`
- Movable joints: 0
- Initial / worst-state verdict: `EXCLUDED` / `EXCLUDED`

**initial** — `EXCLUDED` (not_applicable)

- Joint values: `{}`
- Support: 4 points, rank=2, area=21.8400, mesh-only=False.
- Uniform solid: available=True, margin=1.4872, normalized=0.2215, tip angle=61.03°.
- Uniform shell: available=True, margin=1.6016, normalized=0.2385, tip angle=65.20°.
- Reasons: `EXCLUDED_TRIVIAL_FLOOR_SUPPORT`.
### T01-main

- URDF SHA256: `d09662a3084e8aa46686b2ce9666eb5bf34fa6b25962437fdcbc2c5112f5a452`
- Movable joints: 0
- Initial / worst-state verdict: `STABLE_CANDIDATE` / `STABLE_CANDIDATE`

**initial** — `STABLE_CANDIDATE` (medium)

- Joint values: `{}`
- Support: 16 points, rank=2, area=1.1236, mesh-only=False.
- Uniform solid: available=True, margin=0.4364, normalized=0.2571, tip angle=22.89°.
- Uniform shell: available=True, margin=0.3819, normalized=0.2250, tip angle=19.44°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.00°, settle final tilt=0.00°, contacts=16, min observed F/W=0.50, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### T01-one-round-control

- URDF SHA256: `019235e414ea1575f4745ac082c8c91bb8bf4db874de3d2904273be47c69db71`
- Movable joints: 0
- Initial / worst-state verdict: `STABLE_CANDIDATE` / `STABLE_CANDIDATE`

**initial** — `STABLE_CANDIDATE` (medium)

- Joint values: `{}`
- Support: 16 points, rank=2, area=1.1000, mesh-only=False.
- Uniform solid: available=True, margin=0.4262, normalized=0.2618, tip angle=23.24°.
- Uniform shell: available=True, margin=0.3773, normalized=0.2318, tip angle=20.67°.
- Reasons: `MUJOCO_SETTLE_CONFIRMED`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.00°, settle final tilt=0.00°, contacts=16, min observed F/W=0.50, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### T02-bookshelf

- URDF SHA256: `5781d2d6111e05f4805228ae41365daa5981c65a7f8f340966928dec84d6f749`
- Movable joints: 0
- Initial / worst-state verdict: `INDETERMINATE` / `INDETERMINATE`

**initial** — `INDETERMINATE` (low)

- Joint values: `{}`
- Support: 8 points, rank=2, area=1.2400, mesh-only=True.
- Uniform solid: available=False, margin=—, normalized=—, tip angle=—°.
- Uniform shell: available=True, margin=0.2835, normalized=0.1354, tip angle=10.28°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, SOLID_COM_UNAVAILABLE_NON_WATERTIGHT, MUJOCO_DROPPED_DEGENERATE_MESH_COMPONENTS, MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.01°, settle final tilt=0.01°, contacts=5, min observed F/W=0.35, impulse tipped directions=0/16, dropped zero-volume mesh fragments=14.
### T03-radial-wheel

- URDF SHA256: `e92b54dd0ae6c237dc32fdd7f2809fd4abdfe037f918f24ab18449ed0d93cce3`
- Movable joints: 0
- Initial / worst-state verdict: `UNSTABLE` / `UNSTABLE`

**initial** — `UNSTABLE` (high)

- Joint values: `{}`
- Support: 2 points, rank=1, area=0.0000, mesh-only=True.
- Uniform solid: available=True, margin=—, normalized=—, tip angle=0.00°.
- Uniform shell: available=True, margin=—, normalized=—, tip angle=0.00°.
- Reasons: `DEGENERATE_SUPPORT_REGION, MUJOCO_DID_NOT_OVERRIDE_GEOMETRIC_FAILURE`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.00°, settle final tilt=0.00°, contacts=1, min observed F/W=0.20, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### T04-hollow-mug

- URDF SHA256: `382f2cf5c584cfc1bb9a13c142f9b0fa4ed14d52518fed917f376cc9ef85c8e4`
- Movable joints: 0
- Initial / worst-state verdict: `INDETERMINATE` / `INDETERMINATE`

**initial** — `INDETERMINATE` (low)

- Joint values: `{}`
- Support: 32 points, rank=2, area=1.3188, mesh-only=True.
- Uniform solid: available=False, margin=—, normalized=—, tip angle=—°.
- Uniform shell: available=True, margin=0.5648, normalized=0.2432, tip angle=39.41°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, SOLID_COM_UNAVAILABLE_NON_WATERTIGHT, MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.01°, settle final tilt=0.00°, contacts=2, min observed F/W=1.00, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.
### T05-patterned-desk

- URDF SHA256: `ab7ca4967faaad38262df81265e3b67595e40f7a473126972542412e3a4c0a4c`
- Movable joints: 0
- Initial / worst-state verdict: `INDETERMINATE` / `INDETERMINATE`

**initial** — `INDETERMINATE` (low)

- Joint values: `{}`
- Support: 8 points, rank=2, area=1.5912, mesh-only=True.
- Uniform solid: available=False, margin=—, normalized=—, tip angle=—°.
- Uniform shell: available=True, margin=0.3663, normalized=0.1436, tip angle=16.14°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, SOLID_COM_UNAVAILABLE_NON_WATERTIGHT, MUJOCO_DROPPED_DEGENERATE_MESH_COMPONENTS, MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.01°, settle final tilt=0.00°, contacts=3, min observed F/W=0.50, impulse tipped directions=0/16, dropped zero-volume mesh fragments=2.

## 关键解释

- `T03-radial-wheel` 的支撑面积为 0，但完全对称初态在 5 秒自由沉降中最大倾角仅 0.002°；它没有“自行倒下”，但约 0.2×自重水平力会使其超过 25°。
- `A01-cabinet` 的最低平面几何质心落在初始支撑区外，但 5 秒自由沉降最大倾角仅 0.639°，并在轻微摇摆后形成额外接触；按 >25° 规则它没有倒下，结构 verdict 因几何/接触分歧仍保持保守。
- motorcycle 的有限平底可能来自三角网格离散化或轮胎代理，不足以证明真实两轮车能无支架站立，因此纯 mesh 接触保持 `INDETERMINATE`。
- M01 scratch、T02、T05 的 MuJoCo 代理分别丢弃 9、14、2 个零体积碎片；三者因此继续保持 `INDETERMINATE`。
- `S01-living-room` 自带整块房间地板，整体支撑测试会得到平凡答案，故排除；物件级拆分属于另一项实验。
- MuJoCo mesh 碰撞使用凸包代理，不能消除 concavity、质量分布与真实接触面的不确定性；物理结果只用于交叉复核。

## 可复现性

- Python: `3.10.21`
- Trimesh: `5.0.0`
- SciPy: `1.15.3`
- Shapely: `2.1.2`
- MuJoCo: `3.12.0`
- Command: `experiments/final_standing_stability/analyze.py --input-root /jiigan-hp/lms/aDSL/experiment/audit_20260830 --output-dir /jiigan-hp/lms/aDSL/experiment/physics_analysis/01_final_standing_stability --mujoco-pythonpath /jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310 --physics on`
- 原始 GLB、URDF 和 source.py 未修改；机器可读明细见同目录 `results.json` 和 `summary.csv`。
