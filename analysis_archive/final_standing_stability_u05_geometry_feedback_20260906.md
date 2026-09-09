# 01 Final Standing Stability：现有 aDSL 模型验证

## Material Passport

- Material ID: `adsl-final-standing-stability-20260906T075759Z`
- Type: experiment validation report
- Verification Status: ANALYZED
- Generated (UTC): 2026-09-06T07:57:59.439402+00:00
- Input root: `/vepfs_default/chanxueyan/lhp/lms/aDSL/local_experiment/standing_feedback_20260906`
- MuJoCo coverage: 1/1 non-excluded states
- Free-settle falls (>25°): 0/1 non-excluded states

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
| U05_geometry_feedback_edit | INDETERMINATE | low | 0.2110 | 0.2297 | 0.1709 | 17.18 | upright | 0.01 | 0.35 |

## 逐项结果

### U05_geometry_feedback_edit

- URDF SHA256: `2c035bd20a950a32c3049e151c3487a0adc91ad36525b360cbc625989d7c94f3`
- Movable joints: 0
- Initial / worst-state verdict: `INDETERMINATE` / `INDETERMINATE`

**initial** — `INDETERMINATE` (low)

- Joint values: `{}`
- Support: 33 points, rank=2, area=0.2110, mesh-only=True.
- Uniform solid: available=True, margin=0.2297, normalized=0.2658, tip angle=39.01°.
- Uniform shell: available=True, margin=0.1709, normalized=0.1978, tip angle=17.18°.
- Reasons: `MESH_CONTACT_DISCRETIZATION_RISK, MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY`.
- MuJoCo: natural fall (>25°)=False, settle max tilt=0.01°, settle final tilt=0.00°, contacts=3, min observed F/W=0.35, impulse tipped directions=0/16, dropped zero-volume mesh fragments=0.

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
- Command: `experiments/final_standing_stability/analyze.py --input-root /vepfs_default/chanxueyan/lhp/lms/aDSL/local_experiment/standing_feedback_20260906 --output-dir /vepfs_default/chanxueyan/lhp/lms/aDSL/local_experiment/standing_feedback_analysis_20260906 --mujoco-pythonpath /jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310 --physics on`
- 原始 GLB、URDF 和 source.py 未修改；机器可读明细见同目录 `results.json` 和 `summary.csv`。
