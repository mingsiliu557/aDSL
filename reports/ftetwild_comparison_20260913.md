# fTetWild 独立体网格对照（2026-09-13）

## Material Passport

- Origin Skill: academic-research-suite / experiment-agent
- Origin Mode: run
- Origin Date: 2026-09-13
- Verification Status: ANALYZED — 实际执行；两例超时，不能视为几何验证通过
- Version Label: ftetwild_comparison_v1

## 结论

**SF03 正常对照生成有效体网格，并完成同配置 C3D10/CalculiX 接入；SF27 原始木结版本仍超时；真实间隙对照 SF20 也超时，连接保持性尚未验证。**

因此值得保留为离线候选工具，但目前**不建议接入生产 checker，更不能宣称已解决 SF27 或不会错误连接间隙**。失败只反映本次固定参数、绑定版本与资源预算；不证明 fTetWild 在任何预算下均失败。

| 案例 | 体网格阶段 | 耗时 | 几何/有效性结论 | FEA |
|---|---|---:|---|---|
| SF27/ours 原始版本，保留木结 | TIMEOUT | 300.04 s | 无最终网格，边界、体积、Jacobian、连接变化均未验证 | 未运行 |
| SF03/ours 已通过最终版本 | COMPLETED | 105.12 s | 6,188 四面体有效，仍为 1 个实体；小幅边界变化 | 同配置 C3D10 接入成功；不是完整收敛验证 |
| SF20/adsl 真实间隙对照 | TIMEOUT | 300.05 s | 无最终网格，既不能证明间隙保留，也不能认定发生错误连接 | 未运行 |

SF03 测量另用 0.93 s；条件式 FEA 总用时 13.35 s（自重求解 6.25 s、功能载荷 6.22 s）。三个网格阶段串行执行，无参数重试；两个超时均由父进程终止并回收。没有重跑模型生成、agent 或整批实验。

## 输入、安装和固定预算

原实验：`local_experiment/topology_standing_fea_12_cpu_20260912T100549Z`。
输入均为 `evaluation/{arm}/{case}/attempt_01/asset/render/scene.glb`。SF27 指此前 **ours 分支尚未做局部修复的 RuggedFourCompartmentSpeakerCabinet**，不是另一个 adsl 分支模型，也不是将木结替换为方块后被拒绝的候选。

本地起初无 fTetWild；独立 venv：`local_experiment/ftetwild_20260913/env`，安装官方 `wildmeshing==0.4.1` Python 绑定与 `meshio==5.3.5`。已有 NumPy/SciPy/Trimesh 仅只读复用，生产环境未覆盖。通过 7892 代理下载；绑定二进制 SHA256 与环境版本已保存。wheel 未暴露内嵌 fTetWild 的精确 git revision，不冒称是当前上游 master。

| 参数 | SF27/ours | SF03/ours | SF20/adsl |
|---|---:|---:|---:|
| 原 FEA 高度 | 1.2 m | 0.9 m | 1.6 m |
| m/源码单位 | 0.4210526316 | 0.3862660944 | 0.4076433121 |
| envelope 实际长度 | 0.1 mm | 0.1 mm | 0.1 mm |
| 相对 epsilon | 6.18750020e-5 | 9.51750779e-5 | 4.09505195e-5 |
| 目标边长 | 48 mm | 36 mm | 64 mm |
| 表面三角形数 | 9,396 | 216 | 20,780 |

epsilon = 0.0001 m / 物理包围盒对角线，不是直接把 0.0001 当作无量纲参数。原始 glTF Y-up 坐标经正规旋转恢复为源码 Z-up，再沿用原 FEA 尺度；不以缩放帮助网格成功。日志中的内部工作 epsilon 比用户指定 envelope 更小，两者没有混淆。

各例网格限时 300 s、测量限时 180 s、2 CPU affinity、6 GiB **虚拟地址空间上限**（不是预留内存或实测峰值）。AMIPS stop_quality=10，max_its=80，stage=2；目标边长按原 fine 级名义高度比例 0.04 设置，**不代表离散分辨率等价**。

使用输入 winding number；关闭 open-boundary smoothing、floodfill、强制 manifold 和 orientation correction。未做外部修洞、跨部件焊接或误差递增重试；保留 fTetWild 自身默认简化/优化，因此仍必须测量其几何变化。

## SF27：没有解决本次超时

已通过表面简化、初始四面体与三角形插入，进入网格优化；终止前日志处于 edge collapsing，最后一次统计约 171,639 个**中间**四面体。这些不是过滤后的最终输出，也未做有效性验证。

这与此前 Gmsh `mesh.generate(3)` 内的二维 BSpline 曲面细化卡点不同，但这里只能说明运行到了不同阶段；不能说原始问题已修复。没有为了拿结果提高预算或把木结删掉/替换。没有最终体网格，因此相关几何比较项均为 **UNVERIFIED**。

## SF03：有效网格及几何变化

- 输出 2,049 角节点、6,188 个线性四面体；零 Jacobian=0、负 Jacobian=0、非有限 Jacobian=0、非流形面=0，最小 Jacobian 为 `1.13236e-8 m³`。
- Jacobian 指角点矩阵行列式，即六倍有符号四面体体积；小的正值不是零体积，不按大小删除单元。
- 按四面体**共享面**计算实体分量为 1，与原 OCC 装配结果一致；不是仅按共享顶点或原始三角形对象数量判断。
- 暴露输入表面 → 输出边界：3,353 个有效抽样点，最大偏差 **0.03902 mm**；输出边界 → 原输入表面：4,000 点，最大偏差 **0.03722 mm**。均小于固定 0.1 mm envelope。它们是采样值，**不是严格 Hausdorff 上界**。
- GLB 的 18 个物体均为轴对齐长方体。仅为测量，对完全同坐标的顶点重索引，未移动任何位置；18/18 闭合且正向。精确盒子坐标分区求得联合体积 `0.019120881023748 m³`，输出体积 `0.019120668654044 m³`，变化 **−0.00111067%**。
- 初始 30,000 点 Monte Carlo 估计为 `0.01950940 ± 0.00053075 m³`（近似 95% 区间），精度不足以把约 −2% 的估计差异当成真实体积变化；上述精确分区结果取代它用于接入决策。原始测量文件仍保留，不覆盖。

这些证据支持**有限 FEA 接入筛查**，不等同于严格几何等价证明。接入决定及精确体积单独保存在 `SF03_ours/fea/geometry_gate.json`；初始自动测量中的 `geometry_acceptance=UNVERIFIED` 没有被偷偷改成通过。

## SF20：真实间隙存在，但输出保持性未知

原 topology 报告确认 `ClassicStreetLamp/main_post#solid:0` 与 `upper_capital#solid:0` 存在正间隙，不是点/边接触。间隙为源码单位 0.005，换算原 FEA 尺度为 **2.03821656 mm**，远大于本次单侧 0.1 mm envelope。原 OCC 最终装配为 14 个实体分量，所有原始间隙对、端点及源码定位已复制到实验 manifest。

fTetWild 在优化阶段达到 300 s 上限；最后一次统计约 379,282 个中间四面体，没有最终输出。不能进行输出连接数、间隙大小或负 Jacobian 检验，故**连接安全性没有验证完成**。另发现原 GLB 按精确同坐标重索引后仅 73/74 个对象闭合，这是一项输入表示限制，不推断它就是超时根因，也未自动补洞。

脚本已具备输出共享面连通分量、间隙端点最近边界与所属实体分量的测量；但本例没有最终网格，不能把“写了检测代码”当成得到了验证结果。

## CalculiX：仅 SF03 条件式接入

保留全部四面体，新增全局共享的六条边中点，得到 **12,047 节点 / 6,188 C3D10**。中点顺序核对本机 CalculiX 2.23 `shape10tet.f`：12、23、31、14、24、34。所有四个积分点 Jacobian 均为正且与角点行列式一致，保持直边二次单元，不使用 C3D4 直接替代。

材料完全复用原配置（E=3 GPa、ν=0.35、ρ=1240 kg/m³、屈服强度 50 MPa）；功能载荷仍为座面 −1000 N 和靠背方向 +300 N，自重 g=9.81；固定底面三向平移。载荷语义匹配名单、包围盒、总力与原 fine 结果逐项断言一致。**区域选择器仍沿用原逻辑，未修正/放宽。**

| 项目 | 原 SF03 fine | fTetWild 接入 |
|---|---:|---:|
| C3D10 单元数 | 5,353 | 6,188 |
| 固定节点数 | 148 | 48 |
| 座面加载节点数 | 1,885 | 4,546 |
| 靠背加载节点数 | 1,108 | 1,145 |

节点集合数量受网格分布影响；原流程是将总力均分到选定节点，并非严格一致的面压力积分，因此数值差异不能直接解释为后端带来的强度改善。

| fTetWild 单网格求解 | 自重 | 功能载荷 |
|---|---:|---:|
| 状态 | SOLVED | SOLVED |
| 最大位移 | 0.02257 mm | 0.96799 mm |
| 位移/高度 | 2.50760e-5 | 0.00107555 |
| 名义安全系数 | 540.36 | 38.00 |
| 第一正屈曲因子 | 1115.19 | 238.78 |
| 原阈值单级筛查 | SCREEN_PASS | SCREEN_PASS |

未跑 coarse/medium/fine 收敛，也未复算总体 checker 通过率。最终标记 **NOT_FULLY_VERIFIED（仅一次接入验证）**。SF27、SF20 没有进入 CalculiX。

## 文件与验证

- 独立脚本/协议：`experiments/ftetwild_comparison/{run.py,sf03_fea.py,test_metrics.py,README.md}`。
- 独立产物：`local_experiment/ftetwild_20260913/`，总览 `summary.json`。
- 每例：`manifest.json`、`input_m.off`、`input.npz`、`mesh.log`、`mesh_process.json`。
- SF03 另有 `tetra.vtu`、`tetra.npz`、`boundary.ply`、`element_validation.json`、`validation.json`；`fea/` 内有节点/区域映射、C3D10、INP、DAT、FRD 和求解日志。
- 小测试 **3 passed (0.78 s)**：正/负/零 Jacobian、共享面 vs 仅顶点连通、C3D10 共享中点和积分点 Jacobian。
- 三例的源码、GLB、分析 manifest、原 topology 报告和原配置 SHA256 均未变化。未修改生产 checker、agent、模型源码、判定阈值及 OCC 重建逻辑。

本次按实验执行技能将“运行结束”“网格有效”“几何可接受”“FEA 接入”“完整物理通过”分别记录，避免把接口可用当成修复成功。

参考：[fTetWild 官方仓库](https://github.com/wildmeshing/fTetWild)、[官方 Python 参数说明](https://wildmeshing.github.io/python/)、[绑定源码](https://github.com/wildmeshing/wildmeshing-python/blob/master/src/tetrahedralize.cpp)、[CalculiX 官方项目](https://www.dhondt.de/)。
