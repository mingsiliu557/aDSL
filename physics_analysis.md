# aDSL 现有模型物理分析记录

更新时间：2026-09-04（UTC）

本文记录当前样品的物理分析方法、结果与证据边界。它不是 production
checker，也不是打印工程认证；原始 GLB、URDF 与 `source.py` 均保持不变。

## 01 Final Standing Stability

目标是检查成品在 authored URDF Z-up 姿态下能否站立。

- 几何预筛：最低接触点凸包、均匀实体/薄壳质心、带符号支撑裕度。
- 动态复核：MuJoCo 3.12 CPU 刚体沉降、水平力与小冲量。
- 用户判据：运行期间最大倾角严格大于 25°才记为倒下；恰好 25°不算。
- 结果：排除自带整块房间地板的 S01 后，18/18 姿态在自然沉降中均未超过
  25°；最大值为 A01 initial 的 0.639°。
- A01 的最低平面质心在支撑区外，但摇摆后形成额外接触；T03 支撑面积为 0，
  但完全对称初态不会自行打破对称。因此几何失稳与无扰动动态结果必须分开。

证据位于 `analysis_archive/final_standing_stability_analysis.md` 和
`/jiigan-hp/lms/aDSL/experiment/physics_analysis/01_final_standing_stability/`。

## 02 Progressive Build Stability

### 问题与方法

本阶段只回答：从底部向上打印时，每增加一个 Δh 后，当前 partial geometry
在打印平面上沉降 5 秒，最大倾角是否严格大于 25°。不使用 FEA；弯曲、屈曲、
层间开裂和热变形是后续独立问题。

“无粘附自由刚体”不是从空中自由落体。partial 初始位置仅比平面高 0.002
个场景单位以建立接触。它和真实打印的差别是底面没有被热床、胶水或 brim
固定，因此失去支撑后可以倾倒。

- 当前模型没有可信毫米标定，默认 `Δh = 1% × 总高度`，每个模型 100 个固定点。
- 另加入 collision geometry 的出现/完成高度，以免漏掉短暂几何变化。
- 每层与 `z <= h` 半空间求交并优先封口；实体质心不可用时用薄壳代理。
- 每层计算质心—支撑区关系并运行 MuJoCo；严格 `>25°` 才算倒下。
- 底板粘附仅附加输出重力倾覆力矩/自重。没有实测强度，不给粘附 pass/fail。

### 当前结果

- 15 个 case 中排除 S01，分析 14 个 initial-pose 模型。
- 14 × 100 = 1,400 个固定 Δh 全部完成 MuJoCo；超过 25°的点为 0。
- 固定 Δh 外另有 149 个事件点。A02 6 个、I02 1 个、T03 1 个几何刚出生点
  因截面近零厚度无法构造非零质量刚体；明确记为缺失，没有当成稳定。
- 最大倾角：T03 3.356°，A01 0.639°，A02 0.085°，其余小于 0.08°。
- 几何预筛认为 A01 的 114/115 点和 T03 的 101/101 点不稳定；其余模型没有
  质心越出支撑区的点。

| Case | 固定 Δh 覆盖 | >25° 点数 | 峰值倾角 |
|---|---:|---:|---:|
| A01-cabinet | 100/100 | 0 | 0.639° |
| A02-faucet | 100/100 | 0 | 0.085° |
| E01-chair-slat-edit | 100/100 | 0 | 0.008° |
| I01-example-image | 100/100 | 0 | 0.000° |
| I02-example-arti | 100/100 | 0 | 0.049° |
| M01-base-motorcycle | 100/100 | 0 | 0.076° |
| M01-cyberpunk-edit | 100/100 | 0 | 0.069° |
| M01-cyberpunk-scratch | 100/100 | 0 | 0.069° |
| T01-main | 100/100 | 0 | 0.007° |
| T01-one-round-control | 100/100 | 0 | 0.005° |
| T02-bookshelf | 100/100 | 0 | 0.033° |
| T03-radial-wheel | 100/100 | 0 | 3.356° |
| T04-hollow-mug | 100/100 | 0 | 0.052° |
| T05-patterned-desk | 100/100 | 0 | 0.024° |

### 结论边界

严格结论是：`按当前无扰动 >25° 判据未观察到倒下`，不能写成“实际打印
一定稳定”。T03 直立轮是对称不稳定平衡，数值模型不受扰动时不会凭空选择
倒向；真实打印还有平台振动、喷嘴拖曳、翘曲和误差。motorcycle 的平底也可能
来自三角网格/凸包代理。

如果下一步要回答“受到很小现实扰动后会不会倒”，应预先冻结统一的小扰动协议，
仍以是否越过 25°判断，并与自然沉降结果分栏，不能观察结果后再调参。

代码入口是 `experiments/progressive_build_stability/analyze.py`；仓库报告副本是
`analysis_archive/progressive_build_stability_analysis.md`。完整逐层数据位于
`/jiigan-hp/lms/aDSL/experiment/physics_analysis/02_progressive_build_stability/`
的 `results.json` 和 `summary.csv`。本阶段 CPU 即可，不需要 GPU。


## 03 Load-Bearing Structural Performance

### 可执行方法

本阶段验证了可执行的开源链路，而不是只列工具名称：Gmsh 4.15.2/OpenCASCADE
负责实体 Boolean 与 C3D10 二次四面体网格，CalculiX 2.23（SPOOLES + ARPACK）
负责线性静力和线性特征屈曲。求解为 CPU 工作，不需要 GPU。CGX/ParaView 可用于
后续人工场查看；当前脚本直接解析 DAT，并把最大位移、最大 von Mises、名义安全
系数、屈曲因子和薄弱单元质心写成 JSON/Markdown。

必须提供的工程输入分为三类：

- 材料：至少需要 E、Poisson ratio、density、强度；真实打印件还必须有打印方向、
  XY/Z 拉伸/剪切数据、层间强度、填充与壳厚。当前只有统一各向同性 PLA 代理：
  E=3.0 GPa、nu=0.35、rho=1240 kg/m3、名义屈服 50 MPa。
- 载荷：大小、方向、分布区域和组合。当前冻结 self-weight 与逐类 functional load；
  chair 为 seat 1000 N + back 300 N，table top 为 1000 N，其他 case 的 intended load
  也固化在 case_config.json，但只有载荷路径有效时才求解。
- 边界/连接：支承面、joint 锁定状态、tie/contact、连接刚度、摩擦与预紧。当前可解
  单体统一固定最低 Z 面；运动学 URDF joint 不能代替结构连接定义。

### 数值链路验证

- C3D10 悬臂梁：端位移相对 Euler-Bernoulli 解析解误差 0.276%。
- 固定—自由 Euler 柱：特征屈曲临界载荷误差 0.340%。
- 4 个可解模型均跑 coarse/medium/fine；medium→fine 以位移≤10%、应力≤20%、
  屈曲因子≤10% 为门槛，4/4 通过。

### 当前 14 个模型的判断

- 4/14 `SOLVED`：E01 chair、I01 table、T01 main chair、T01 control chair。
- 7/14 `INVALID_LOAD_PATH`：A01、A02、I02、两个可封闭 motorcycle、T03 wheel、
  T05 desk。它们精确 union 后仍有 4、5、4、54、85、18、18 个独立承载体，且缺少
  可用于 FEA 的 tie/contact/连接刚度；没有将它们静默粘成一体。
- 3/14 `NOT_MESHABLE`：M01 scratch、T02 bookshelf、T04 mug 的 collision mesh
  不能形成封闭实体/Boolean 结果；没有用体素补洞伪造原模型。

细网格的 8 条已求解载荷轨在统一代理门槛下均为 `SCREEN_PASS`（U/特征长度≤1%、
名义屈服 FoS≥2、线性屈曲因子≥2），但这只是理想化筛查：

| Case | Load | max U | max von Mises | nominal FoS | buckling factor |
|---|---|---:|---:|---:|---:|
| E01 chair | self / functional | 0.0159 / 5.848 mm | 0.072 / 3.963 MPa | 692 / 12.6 | 633 / 120 |
| I01 table | self / functional | 0.0259 / 0.0578 mm | 0.092 / 0.199 MPa | 542 / 251 | 338 / 168 |
| T01 main | self / functional | 0.0163 / 5.587 mm | 0.069 / 3.655 MPa | 729 / 13.7 | 818 / 122 |
| T01 control | self / functional | 0.0151 / 1.793 mm | 0.073 / 1.782 MPa | 686 / 28.1 | 808 / 168 |

E01/T01 main 的 functional 位移约为高度的 0.65%/0.62%，是已求解样品中最值得
继续关注的柔度；仍低于本次 1% 代理门槛。最大应力对应单元的四角点质心已输出，
因此可以定位具体薄弱区域；完整应力/位移场保留在 DAT/FRD。尖角、全固定边界和
点/节点载荷附近的局部峰值可能有奇异性，不能只凭单个 max 值下工程结论。

### 证据边界与结果位置

当前没有打印方向、材料 coupon、填充/壳厚、制造缺陷、蠕变、疲劳、几何非线性
或真实接触，所以不能宣称模型“承载安全”。线性特征屈曲通常高估含缺陷真实件；
名义 FoS=50 MPa/max von Mises 也不是认证安全系数。

入口为 `experiments/load_bearing_structural_performance/analyze.py`；配置为同目录
`case_config.json`；正式结果位于
`/jiigan-hp/lms/aDSL/experiment/physics_analysis/03_load_bearing_structural_performance/`。
机器可读真值是 `results.json`，报告是 `load_bearing_structural_performance.md`，
逐级 deck、DAT、FRD 与 stdout 日志均保留。第一次直接在共享盘求解触发 CalculiX
delete/reopen 不兼容，完整保留为 `03_load_bearing_structural_performance_attempt_01_shared_fs_openfile_failure`；
canonical 运行改为每个 job 在 `/tmp` 独立求解、解析后复制到数据盘并立即清理。

## 04 Support Requirement & Critical-Surface-Aware Support

### 可执行方法

本阶段把几何候选与真实切片结果分开：Trimesh 按向下三角面法向做 45°
（从水平面计）overhang 预筛；PrusaSlicer 实际生成并在 G-code 中标记 bridge
infill、support material 与 support material interface。interface extrusion 按 0.45 mm
挤出宽度缓冲后映射回原 collision 三角面，得到 top-contact；support 从已有模型
顶面起长的区域另作 bottom-contact。这里的 contact 是 0.2 mm Z gap 内的名义承托区，
不是两个实体严格零距离相交。

关键表面使用显式 `collision_regex + face selector` 配置。用户确认采用功能面与主展示面，
有效重叠面积超过 0.01 mm² 即为 VIOLATION。输出包含 global/local face IDs、collision、
面积、质心、bounds 和彩色 PLY；单一合并 GLB 缺少可靠 face provenance 时 fail closed，
不根据外观猜语义。

固定 profile 为 0.4 mm nozzle、0.2 mm layer、通用 PLA、45° overhang、everywhere
rectilinear support、3 层 interface。保持 authored Z-up 和 initial joint state。第 03 项
1.8 m 等语义实物尺度只保留为元数据；实际桌面 FDM 网格等比限制为最长边 180 mm，
否则会产生数千至上万层，且超出 220 mm 虚拟打印床。

### 当前结果

- 合成 cone/cantilever/two-anchor bridge 基准通过；14/14 当前案例完成 CPU 切片与映射。
- 14/14 都由 PrusaSlicer 生成 support，说明当前 agent 输出普遍没有按免支撑打印方向设计。
- 关键表面：3 VIOLATION、4 PASS、7 INDETERMINATE。
- VIOLATION：A01 cabinet 109.200 mm²，集中在左右门拉手和上抽屉拉手；A02 faucet
  523.541 mm²，集中在 aerator、左右旋钮和中央 lever；I02 nightstand 253.287 mm²，
  集中在三层抽屉拉手。
- PASS：E01 chair、I01 table、T01-main chair、T01-control chair；它们仍需要 support，
  只是当前配置下名义接触没有落在已标注关键面。
- 7 个单一合并 GLB 为 INDETERMINATE。T02 bookshelf 检出 1204.717 mm² 候选重叠，
  但无法证明候选就是语义架板顶面，因此没有升级为 VIOLATION。
- M01 scratch、T02、T04、T05 含 non-watertight collision part；T02 另有 41 个退化面，
  M01 scratch 有 2 个。切片器隐式 repair 后成功不代表源网格正确。
- 127 个输入 URDF/GLB/STL/OBJ/source.py 的运行前后 SHA256 完全一致。

### 工具与证据边界

本机没有 Flatpak；PrusaSlicer 2.9.5 官方 Linux 只通过 Flatpak 发布。官方 2.8.1
old-distro AppImage 缺本机桌面库，new-distro build 又要求 GLIBC 2.36，因此正式实验使用
Ubuntu Jammy 用户态解包的 PrusaSlicer 2.4.0；它具备本实验所需 FFF support/interface/
bridge 功能，没有系统安装。runtime 位于
`/vepfs_default/chanxueyan/lhp/lms/tools/prusaslicer/2.4.0/`，`.bashrc` 提供
`adsl_prusaslicer` 与 `adsl_support_python`。

bridge role 只说明切片器采用桥接路径，不证明真实 PLA 不下垂；support 痕迹还受温度、
冷却、速度、Z gap、界面密度和拆除影响。结果状态为 ANALYZED，不是制造认证。

入口为 `experiments/support_requirement_critical_surfaces/analyze.py`，配置和 README 在同目录；
仓库报告副本为 `analysis_archive/support_requirement_critical_surfaces_analysis.md`。完整结果、
G-code、STL、PLY 和日志位于
`/jiigan-hp/lms/aDSL/experiment/physics_analysis/04_support_requirement_critical_surfaces/`。

## 01 扩展：高倾倒风险原始 caption 与 controls（2026-09-04）

本扩展不修改既有模型和严格 >25° 判据。CAP3D/MARVEL caption CSV 位于
`/jiigan-hp/lms/aDSL/datasets/prompt_sources/`，没有下载完整 ShapeNet/ABO 网格。
四类对象是 ShapeNet floor/street lamp、ShapeNet standing/tower loudspeaker、
ABO floor lamp 与 ABO bar stool；每个对象分别使用 CAP3D 和 MARVEL level-2 原始
caption，不加入稳定性提示。类别过滤后按 SHA256(dataset:object_id) 排序，rank 0 为
initial、rank 1 为 reserve。论文未公开其 200 条 prompt ID，因此范围是
same-source/different-sample。

| Control 组 | 数量 | 自由沉降 >25° | 峰值倾角 |
|---|---:|---:|---|
| 稳定（宽箱体、低圆柱） | 2 | 0/2 | 0°、0° |
| 易倒（线支撑、偏置悬挑、偏置顶重） | 3 | 3/3 | 133.891°、179.990°、179.974° |

易倒 control 的几何预筛检出退化支撑或质心投影在支撑区外，MuJoCo 自由沉降也全部
越过阈值；因此当前规则能检测明确失稳。control 不进入 aDSL failure rate。

Codex CLI 已恢复为默认 agent transport，真实结构化 smoke 与完整 76 项测试通过。
单卡 A800 持久 Eevee worker 仍 pending；tmux `adsl_standing_cases` 已提交，会在
worker live 后严格串行生成 U01–U08，每 case 两轮。实验根为
`/jiigan-hp/lms/aDSL/experiment/physics_analysis/01_unstable_case_validation_20260904/`。
生成后再运行 MuJoCo；仅当 initial 8 个全部未自然倒下时触发冻结的 reserve。

## 2026-09-06：03 FEA checker 自动反馈样本

- 已把 standing、FEA、support 三类 analyzer 接入通用 required-checker 发布门。
- 薄弱椅子在相同材料、载荷、边界、网格规则下完成两次三档网格求解；两次
  mesh convergence 均 PASSED。
- 一次 agent 局部修复把 functional max U 从 39.728 mm 降至 19.522 mm
  （−50.86%），U/H 从 4.414% 降至 2.169%；von Mises 从 17.571 MPa 降至
  9.613 MPa，FoS 从 2.846 升至 5.201。
- 修复只把两根后立柱由 22×22 mm 改成 22×40 mm，并保持后缘位置不变；八视图
  变化像素占比约 0.600%。外观 critic 两轮均批准。
- Round 2 仍超过 U/H≤1% 的固定门槛，因此归档为“方向正确的改善”，不是 PASS。
  用户在确认改善后停止后续 round。
- 证据入口：
  `presentation_assets/results/fea_checker_loop/EXPERIMENT.md`；求解场图：
  `presentation_assets/renders/simulation/03b_calculix_fea_checker_loop.png`。


## 2026-09-06：02 Progressive checker 自动反馈样本

- 新增通用 `progressive` checker adapter。固定协议为1%总高Δh + geometry事件，
  每个 `z<=h` partial 用同一MuJoCo无粘附刚体代理沉降5秒，严格>25°判倒。
- 三个受控fixture的完整成品峰值均<0.12°，但打印中间态分别有39、46、45个高度
  超过25°，证明检测器可以捕获“成品稳定、中途失稳”。
- C01 baseline首次在47%高度失败，最坏52%高度peak34.574°；当时右悬臂已出现、
  左上配重未完成，solid COM x=0.1776，signed support margin=-0.0876。
- Engineering Critic据此要求唯一低层修复：新增0.44×0.28×0.02、x中心0.08的
  `stability_foot`。Agent只增加这一行；复测0/108高度失败，最坏peak0.0908°，
  margin转为+0.1345，checker PASS，成品standing也PASS。
- 物理含义要严格限制：该PASS只回答无底板粘附条件下的整体刚体倾覆。PrusaSlicer
  前后都要求support，overhang area均3147.968 mm²；横向shelf仍不能据此称为
  无支撑可打印。弯曲、层间粘附、喷嘴拖曳和热翘曲继续不在02 checker内。
- 证据入口：
  `presentation_assets/results/progressive_checker_loop/EXPERIMENT.md`。

- 方法修正：真实FFF progressive主判据应由PrusaSlicer逐层G-code驱动，累计model+support+brim质量、COM、底板连通footprint与所需粘附强度；当前无粘附MuJoCo Δh降级为脱粘极端stress test，不再解释为真实打印过程主模拟。
