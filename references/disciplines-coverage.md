# 适用范围与全专业构件库（Scope & Disciplines）

> 本文回答三个问题：**这个技能能干什么**、**在各专业上成熟度如何**、**明确不干什么**。
>
> 本技能以道桥（公路 / 铁路桥 / 涵洞 / 路基路面）起家并在此完成了实测标定，
> 但**不局限于此**：构件识别库覆盖土木、结构、建筑、机电、措施项目等全部专业。
> 全专业构件库的定位是"**识别与开项**"，不是"项目级验证"——成熟度分级见 §2，不许混淆。

术语沿用本技能既有约定：**口径**（单幅/全桥、单侧/两侧）、**图证**（图号 + 图面位置）、
**红旗**（漏项信号）、**推定**（含钢率等经验推导，须标出）、**敞口**（待确认，须挂 RFI）。

---

## 1. 适用性总表

| 工程类型 | 适用 | 沉淀深度 | 主要计量标准 | 典型构件 | 已知盲区 |
|---|---|---|---|---|---|
| 公路 / 市政道路 | ✅ | **深** | CESMM4 D / POMI | `excavation` `road_base` `asphalt_surface` `kerb` `pavement_marking` | 路面各结构层宽度逐层不同；线性量未扣桥长造成重复计量 |
| 桥梁（公路 / 铁路） | ✅ | **深** | CESMM4 E/F/K/N | `pile` `pile_cap` `pier_column` `pier_cap` `bearing` `girder` `deck_slab` `parapet` | 支座/伸缩缝须直读专用表；盖梁与桥台整项漏；双幅当单幅 |
| 铁路（含接触网 OCL） | ✅ | **中** | CESMM4 N / 项目专用 | `rail_track` `ballast` `sleeper` `switch` `ocl_mast_foundation` | **接触网只有支柱基础开项有沉淀**，架线、承力索、回流线无 |
| 涵洞与排水 | ✅ | **深** | CESMM4 C/G/H | `culvert` `head_wall` `wing_wall` `apron` `cut_off_wall` `drainage_pipe` `manhole` `gully` | 端墙/翼墙/铺砌/隔水墙整组漏；壁厚逐座不同，禁统一参数 |
| 隧道 | ⚠️ 部分 | **浅** | CESMM4（T 类，项目定义） | `tunnel_excavation` `lining` `shotcrete` `rock_bolt` `portal` | 无开项清单；围岩分级、超挖回填、注浆量依赖地勘与工法 |
| 房建 · 结构 | ✅ | **中** | NRM2 / POMI / CESMM4 E-F | `footing_isolated` `raft` `column` `beam` `slab` `rc_wall` `rebar` `formwork` `steelwork` | 钢筋须读 BBS；梁柱节点归属、洞口扣减阈值按项目前章 |
| 房建 · 建筑 | ✅ | **中** | NRM2 / SMM7 | `brick_wall` `curtain_wall` `door` `window` `floor_finish` `suspended_ceiling` `waterproofing` | 装修做法表与材料表常跨图册；门窗五金/框扇是否并入须按前章 |
| 机电 · 给排水 | ✅ | **中** | NRM2 / MasterFormat 22 | `pipe_run` `pipe_fitting` `valve` `sanitary_fixture` `water_tank` `pipe_insulation` | 立管与支管长度来自系统图与平面，**须两图联动**；管件是否计入长度按规则 |
| 机电 · 暖通 | ✅ | **中** | NRM2 / MasterFormat 23 | `duct_run` `duct_fitting` `damper` `grille` `ahu` `fcu` `chilled_water_pipe` | 风管按**展开面积**，周长 = 各边之和；软管段常漏 |
| 机电 · 电气 | ✅ | **中** | NRM2 / MasterFormat 26 | `cable_tray` `conduit` `cable` `db` `luminaire` `switch` `socket` `earthing` | **竖向立管与引下长度最常漏**；电缆长度要按芯数乘；备用回路与预留系数 |
| 机电 · 消防 | ✅ | **中** | NRM2 / 项目前章 | `sprinkler_main` `sprinkler_branch` `sprinkler_head` `hydrant` `hose_reel` `fire_damper` | 主管/支管分列；喷头按间距推算须标注为推定 |
| 机电 · 弱电 ELV | ✅ | **中** | MasterFormat 27/28 | `data_voice` `cctv` `access_control` `fire_alarm` | 点位来自图例，线缆路由常无图 → 挂敞口 |
| 室外照明 / 路灯 | ✅ | **深** | CESMM4 / 项目前章 | `mv_cable` `package_substation` `feeder_pillar` `lv_cable` `ecc_conductor` `conduit` `cable_handhole` `lighting_pole` `luminaire` `pole_foundation` | 灯具数 ≠ 杆数；ECC 与主缆等长常整项漏；不同杆型基础量差一个量级 |
| 水利与水工 | ⚠️ 部分 | **浅** | CESMM4 / 项目定义 | `channel_excavation` `dam_body` `spillway` `gate` `revetment` `marine_works` | 无沉淀；水工金属结构、止水、帷幕注浆未入库 |
| 机场场道 | ⚠️ 部分 | **浅** | 项目定义 / FAA / ICAO 相关 | `pavement_(rigid/flexible)` `shoulder` `airfield_lighting` `marking` | 道面强度分区、助航灯光回路、飞行区标志无沉淀 |
| 港航 | ⚠️ 部分 | **浅** | CESMM4（marine） | `dredging` `quay_wall` `fender` `bollard` `marine_pile` | 潮汐/回淤分层、水下计量口径未验证 |
| 油气与工艺管道 | ⚠️ 部分 | **浅** | 项目定义 / P&ID | `process_equipment` `tank` `pump_set` `valve_(process)` `instrument` `piping_(process)` | 需 P&ID + 轴测图；应力弯、管托、伴热未入库 |
| 电力输变电 | ⚠️ 部分 | **浅** | 项目定义 | `transformer` `switchgear` `busbar` `tower` `conductor` `cable_trench` | 无沉淀；铁塔与架线工程量须读专用杆塔明细表 |
| 临时工程与措施项目 | ✅（须人工确认） | **中** | CESMM4 A/Z / NRM2 Preliminaries | `formwork` `scaffolding` `falsework` `dewatering` `shoring` `traffic_management` `muck_away` | **工法依赖**：模板周转次数、降水工期、脚手架时长图纸上都没有，一律 `source=method` |

**图例**：✅ 可直接承接｜⚠️ 可承接识别与开项，规则须由项目前章补齐｜空白处不适用。

---

## 2. 成熟度分级（不许混淆）

| 档 | 含义 | 判据 | 当前落在 |
|---|---|---|---|
| **深** | 有开项清单 + 有实测标定过的错误模式 | 存在专用开项清单，且至少一轮项目级实测把漏项/口径错抓出来并回写 | 公路路基路面、桥梁、涵洞与排水、室外照明 |
| **中** | 有构件识别库 + 通用计量规则，无项目级实测 | 能识别 class、能给出计量基础与常见陷阱，但**本项目未在这些专业上跑过完整交付** | 铁路（接触网除外）、房建结构/建筑、MEP 各专项、措施项目 |
| **浅** | 仅有构件识别线索 | 只有 class 名与识别线索，**陷阱列是通论、未经本项目验证** | 隧道、水利水工、机场场道、港航、油气工艺、电力输变电 |

**使用纪律**：
1. 「中/浅」专业出具的成果，交付时必须**显式声明"本专业技能沉淀为识别级，未含项目级实测错误模式"**。
2. 「浅」专业不得引用本技能的红旗表与实测修正幅度——那些数字来自道桥，套过去是伪精确。
3. 「中」专业若要升级为「深」，必须补开项清单 + 跑一轮 `gate_fault_injection.py` 注入自证。

### 2.1 「禁止外推」的两个层级（易被误读，务必分清）

"不要外推"这句话在**两个不同层级上同时成立**，但理由与边界不同。
混为一谈会导致过度保守（同专业内也不敢用通用构造）或过度激进（跨专业乱套指标）：

| 层级 | 规则 | 理由（实测） |
|---|---|---|
| **同专业 · 同类型构件**<br>（桥1 ↔ 桥2；涵 A ↔ 涵 B） | **必须逐座直读，禁止用一个构件的参数外推另一个** | 同为 2500×1500 箱涵：HA035062 墙 400/板 450，HA035063 墙 350/板 400，**壁厚并不相同**；某项目承台厚度 1600（有盖梁体系）与 1500（无盖梁柱式墩）并存，**不是笔误** |
| **跨专业**<br>（房建 ↔ 道桥；MEP ↔ 土建） | **禁止引用**他专业的实测数字或修正幅度 | 指标与结构体系、荷载、施工工法强相关，跨专业外推**没有物理意义**，且会制造伪精确 |

**可以同时沿用的**（不构成外推）：同一项目里设计说明明确写"适用于全桥/全部同类构件"的**通用构造做法**——
前提是它有图号与条文出处，且逐座核对过没有单独的变更。

---

## 2.2 道砟 / 轨道工程：本清单的**排除项**（不是漏项）

道砟（ballast）及轨道上部结构属**轨道工程（06.VOIE / Permanent Way）**，
**不并入桥梁清单**。桥上 0.80 m 为**道床厚度**，它参与高程链闭合，
但道砟本身的量归轨道专业。

> **排除项也要写清"为什么不算"**，否则下次复核的人会把它当成漏项补进来，
> 造成重复计量。



---

## 3. 格式无关原则

**原 description 里"不适用 DWG/DXF"是错的，予以纠正。**
本技能的取证对象是"图纸表达的几何与注记"，不是某种文件格式。区别只在**走哪条取证路线**：

| 输入格式 | 判型特征 | 取证路线 | 说明 |
|---|---|---|---|
| DWG / DXF | 原生矢量，图层名/块名可用 | **首选**：图层/块名 → class 映射，几何直读 | 信息最全，`layer→class` 映射是识别最硬的线索 |
| PDF（原生矢量） | `text > 0`、`scanned = False` | T2 文本层解析 → T3 矢量取证 → T5 尺寸链 | 最快最准 |
| PDF（文字转曲） | `text = 0`、`scanned = False`、`get_drawings()` 上千 | T3/T4 矢量取证 → T5 尺寸链 → T7 渲染 | **别指望 OCR**，转曲后没有字符层 |
| PDF（扫描） | `scanned = True` | T7 原生 dpi 切块目测 | 别信 OCR 的数值，只用它定位 |
| 栅格图片（PNG/JPG/TIF） | 无矢量无文字层 | T7 切块 + 比例尺双校准 | 精度最低，须在证据档降级 |

判型矩阵与技法卡见 `references/drawing-reading-playbook.md` §0。
**任何格式下的读数都必须带证据强度字段**（`method` / `evidence_word`），没有标注的一律按最低档处理。

---

## 4. 规范可插拔与规则优先级

标准不是写死的，按项目装配。**同一条量在不同标准下口径可能不同**，出数前先确认 `project.json.standard`。

| 体系 | 适用 | 要点 |
|---|---|---|
| **NRM2**（RICS） | 房建，英联邦/中东 | 净量；洞口/空腔 < 0.5 m² 或 0.5 m³ **不扣**；描述自解释 |
| **CESMM4** | 土木与基础设施 | Class A–Z 编码（C 土工、D 道路、E 混凝土、G 管、K 桩、N 桥/铁、Z 工法相关） |
| **SMM7** | 旧项目沿用 | CA 工作分部；阈值精神同 NRM2 |
| **POMI** | 国际 / 联合体项目 | 中性国际版，常配 **preambles**，其计量备注须捕获为项目级规则 |
| **MasterFormat** | 北美及美标国际项目 | 按工种结果分类（Div 03/05/08/22/23/26），用于清单编码与描述 |
| **Uniformat** | 同上 | 按元素分类（A 地下 / B 外壳 / C 内装 / D 服务），用于元素级汇总与造价模型 |
| **FIDIC**（红/黄/银皮书） | 合同与支付框架 | 不算计量标准；管暂定金、计日工、变更（Cl.13）、计量与支付（Cl.12） |

**规则优先级链**（高 → 低；冲突时**高者先采信，但低者必须留痕并进复核队列，禁止删除**——完整判据见 `measurement-standards.md` §4.1/§4.2，本文不重复定义）：

```
1 合同专用条件 / FIDIC 专用条款 / 业主要求
2 项目计量规则 / 前章（preambles）/ 项目技术规范
3 图纸设计说明与明细表（门窗表、材料表、钢筋表 BBS）
4 选定标准（NRM2 / CESMM4 / SMM7 / POMI / 项目自定义）
5 地区 / 行业 / 企业标准
6 引擎默认行为
```

补充：同优先级两条规则冲突 → 判 `conflict` 进复核队列，**两边都报**；数值阈值冲突**禁止静默取一**；
变更/澄清在其范围内恒优先，且**变更前后的量分列可比**。
标准适配器细则（扣减阈值、单位换算、多语术语表）另见 `references/measurement-standards.md`（随 v2 移植）。

---

## 5. §0 识别方法（各专业通用，六条）

1. **图层 / 块名** —— 最强线索。预处理阶段建立本项目的 `layer → class` 映射表。
2. **图例与明细表** —— 未知符号查图例；数量与属性查门窗表/材料表/钢筋表/支座表。
3. **几何 + 上下文** —— 定距平行线≈墙；沿轴网的长细多段线≈梁/管；嵌套矩形≈柱/桩；沿走向重复的小符号≈管件。
4. **文本锚** —— `KL1(3)` `C30` `DN100` `W12x40` `#4@200` 等标注挂到最近几何上。
5. **跨图联动** —— 构件的标高/断面往往只在剖面图、详图或明细表里出现，**联动完成再定案**。
6. **置信度降级** —— 两条线索打架时**保留两个候选 class 并降级**，禁止强行归类。

---

## 6. 全专业构件库

### 6.1 土木 / 基础设施

| Class | 识别线索 | 关键属性 | 计量基础 | 常见陷阱 |
|---|---|---|---|---|
| `site_clearance` | 范围注记、清表界线 | 面积、障碍物 | m² | 与 `topsoil_strip` 分列 |
| `topsoil_strip` | 深度注记、总平 | 深度、面积 | m³ | 深度常为假定 → 标推定 |
| `excavation` | 横断面、标高、剖面填充 | 深度、平面面积、边坡、松散系数 | m³ | 松散/超挖/工作面各自口径 |
| `disposal` / `fill_import` / `fill_compacted` | 范围注记、土石方表 | 体积、运距、压实层厚 | m³ | 净量 vs 松方；弃运单独计量 |
| `trench_excavation` | 管沟断面 | 宽、深、长 | m³ | 支护与降水另计 |
| `road_subgrade` / `road_subbase` / `road_base` | 路面结构层表、标准断面 | 面积、**逐层宽度**、厚度、材料 | m²（或 m³） | **一个层宽套所有层**是实测红旗 |
| `asphalt_surface` | 铺装表 | 面积、厚度、级配 | m² | 路缘石处扣减 |
| `kerb` / `kerb_edging` | 沿路线线性符号 | 长度、断面型 | m | 交叉口圆弧段 |
| `pavement_marking` | 线型 | 长度/面积、类型 | m / m² | 虚线间歇 |
| `drainage_pipe` | 管符号 + 内底标高 | DN、材质、等级、长度、坡度 | m | 量至井内壁还是中心线 |
| `manhole` / `chamber` / `gully` | 方/圆符号带 y-y、路边小符号 | 深度、尺寸、井盖、类型 | nr + 深度（项/m） | 深度分级；雨水口常漏 |
| `culvert` / `head_wall` / `wing_wall` / `apron` / `cut_off_wall` | 涵洞横断面与详图 | 跨径、长度、壁厚、类型 | m / nr / m³ | **附属整组漏**；壁厚逐座直读 |
| `retaining_wall` | 断面、填充 | 长度、高度、面坡 | m³ / m² | 回填与排水层另计 |
| `piling` / `diaphragm_wall` / `ground_anchor` / `soil_nail` | 桩位图、地连墙平面、锚杆详图 | 桩径、桩长、根数、截面、深度、荷载 | m / nr / m² / m³ | 截桩标高；试桩单列；槽段接缝 |
| `rail_track` / `switch` | 轨道平面 | 长度、轨型、道岔号 | m / 项 | 道岔单列 |
| `pipe_main`（给水/市政管） | 路由平面 + 纵断 | DN、材质、等级、长度 | m | 阀门/管件/包封另计 |
| `marine_works` / `dredging` | 断面、疏浚标高 | 体积、面积、类型 | m³ / m² | 潮汐层与回淤层 |

**基础设施 QC 线索**：桩号/里程连续、排水内底标高单调、层厚对照规范、松散系数有据、检查井深度分级。

### 6.1A 桥梁细分构件（漏项与误判最高发，逐层核对）

| 层 | Class | 识别线索 | 关键属性 | 计量基础 | 常见陷阱 |
|---|---|---|---|---|---|
| 基础 | `pile` / `pile_cap` / `spread_footing` | **桩位图**（P1~Pn 编号）、承台平面、桥台剖面 | 桩径、桩长、根数、承台尺寸/厚 | m / m³ / nr | 桥台可能是**扩大基础无桩**；桩长未注 → 挂 RFI，不猜 |
| 下部 | `pier_column` / `pier_cap`（盖梁）/ `abutment`（台身/台帽/耳墙）/ `bearing_pedestal` / `shear_key` | 墩台剖面 + 配筋图 | 截面、高度、混凝土等级 | m³ | **盖梁与桥台整项漏**；盖梁宽 ≠ 桥宽；台身常为楔形要分块 |
| 支座 | `bearing`（板式 / 盆式） | **支座表**（AT ABUTMENTS / AT PIERS） | 尺寸、型号、数量 | nr | **必须直读支座表**，禁止"幅×跨×片×端"推算 |
| 上部 | `girder`（T / I / U / 箱梁 / 预制板组合） | **梁断面详图 1:30~1:10**、girder layout 的 "N EQUAL SPACES" | 梁型、梁高、腹板厚、马蹄/翼缘宽、梁长、榀数 | m³ / nr | **梁型误判代价最大**；梁间距 ≠ 梁宽；榀数用尺寸链闭合反算 |
| 上部 | `diaphragm`（横隔板）/ `wet_joint` / `end_diaphragm` | layout 图、梁部详图 | 数量、尺寸 | m³ / nr | 常整项漏 |
| 桥面 | `deck_slab`（预制 plank 与现浇层**分列**）/ `deck_waterproofing` / `asphalt_wearing` | 板座详图、NOTES | 厚度、面积、预制/现浇 | m³ / m² | 预制与现浇计价口径不同；铺装厚度常误读 |
| 桥面系 | `parapet`（NJ 护栏）/ `expansion_joint` / `drainage_spout` / `approach_slab` / `bridge_road_marking` | 桥面系详图、伸缩缝大样 | 长度、型号、数量 | m / nr | 伸缩缝长按**横桥向单幅宽**，不是路线宽；护栏常整项漏 |
| 附属 | `underbridge_lighting` / `bridge_lighting` / `inspection_walkway` / `ocl_mast_foundation` | 照明图册**子目录**、OCL 图 | 灯具、盘柜、端子箱 | nr / m | 整分部漏高发 |

**双幅桥**：平面出现 `SOUTH/NORTH CARRIAGEWAY`（或 `LEFT/RIGHT`）且同名结构两处标高不同 →
逐项问"这个数是单幅还是两幅合计"。支座表总数 ÷（跨数 × 每榀支座数）÷ 幅数 = 每幅榀数，双向闭合。
**斜交桥**：记录斜交角，声明桥长取沿桥轴结构长还是沿路线路线长，两者差 1/cos θ。

### 6.2 结构（混凝土 / 钢 / 模板 / 钢筋）

| Class | 识别线索 | 关键属性 | 计量基础 | 常见陷阱 |
|---|---|---|---|---|
| `footing_isolated` / `footing_strip` / `raft` | 填充方块/长条/大面 | 长宽厚、混凝土等级 | m³ | 厚度变化；工作面 excavation 另计 |
| `pile_cap` / `column` / `beam` / `slab` / `rc_wall` | 平面图元 + `KL…` 等标注 | 截面、高度、厚度、等级 | m³ | 柱高 = 层高减梁高；梁算不算到柱边；洞口与降板 |
| `stair_waist` / `stair_landing` | 楼梯平面/剖面 | 宽、板厚、长 | m³ | 梯段斜长与投影长混用 |
| `formwork`（按构件派生） | 由构件几何派生 | 接触面积 | 展开面积 m² | 洞口超阈值才扣；底面 vs 侧面 |
| `rebar` | **钢筋表 BBS** / 标注 | 直径、等级、形状、间距、保护层 | kg / t | 无表时按含钢率**推定**并挂敞口 |
| `precast_*` | 预制构件表 + 标注 | 类型、尺寸、数量 | nr / m³ | 接缝、吊点、运输另计 |
| `steel_column` / `steel_beam` / `steel_brace` / `roof_truss` / `purlin` | 构件标注（`UB` `W12x40`） | 截面、单位重、长度、数量 | kg / t | 拼接板、节点板、螺栓、防火涂料另计 |
| `steel_decking` / `bolts` / `welds` | 压型板符号、明细表 | 面积、板型、类型、数量 | m² / nr / m | 端部搭接；螺栓有时并入综合项 |

### 6.3 建筑

| Class | 识别线索 | 关键属性 | 计量基础 | 常见陷阱 |
|---|---|---|---|---|
| `brick_wall` / `block_wall` / `insulated_wall` | 墙线 + 材料注记 / 剖面 | 厚度、长度、高度、层次 | m² | 洞口扣减；不同材料分列；分层做法 |
| `curtain_wall` / `cladding` | 立面网格 | 面积、面板类型 | m² | 骨架 vs 玻璃面板 |
| `door` / `window` / `roof_light` | `M1021` `C…` 标注 + 门窗表 | 宽高、类型、扇数、玻璃 | nr（/ m²） | 框 / 扇 / 五金是否并入 |
| `internal_partition` | 细双线 | 类型、厚度 | m² | 玻璃隔断另算 |
| `suspended_ceiling` | 天花平面 + 标高 | 面积、板材、龙骨 | m² | 灯槽、检修口、凹槽边缘 |
| `floor_finish` / `skirting` / `wall_finish` / `paint` | 装修做法表 | 面积/长度、做法、遍数 | m² / m | 洞口阈值；踢脚与门洞交接 |
| `roof_covering` / `waterproofing` / `insulation` | 屋面平面 + 大样 | 面积、类型、坡度、厚度 | m² | 泛水上翻按周长计；搭接 |
| `tiling` / `stone` / `stair_finish` / `balustrade` / `handrail` | 做法表、详图 | 面积、类型、长度 | m² / m / nr | 切割损耗 |
| `external_works`（`kerb` `paving` `fence` `drain`） | 总平 | 各异 | 各异 | 与市政分部界面重叠，须定 owner |

### 6.4–6.8 机电（MEP）

| 专业 | Class | 识别线索 | 关键属性 | 计量基础 | 常见陷阱 |
|---|---|---|---|---|---|
| 给排水 | `pipe_run`（给水/排水/通气） | 单/双线 + `DN…` | DN、材质、等级、系统 | m（中心线） | 除非规则允许，**管件不从管长中扣** |
| 给排水 | `pipe_fitting` / `valve` / `pipe_support` / `pipe_sleeve` | 符号、明细表、剖面 | 类型、尺寸、间距 | nr / kg | 支架间距常为假定 → 标推定；防火封堵另计 |
| 给排水 | `manhole` / `floor_trap` / `gully` / `sanitary_fixture` | 图例符号 | 尺寸、深度、类型 | nr | 洁具配件是否并入 |
| 给排水 | `water_tank` / `pump` / `water_heater` / `pipe_insulation` | 设备标注、注记 | 类型、容量、厚度 | nr / m / m² / m³ | 保温按表面积规则 |
| 消防 | `sprinkler_main` / `sprinkler_branch` / `sprinkler_head` | 红色管线 + `DN`、喷头符号 | DN、材质、长度、K 值 | m / nr | 主管与支管**必须分列**；按间距推算喷头须标推定 |
| 消防 | `fire_alarm_device` / `fire_damper` / `hose_reel` / `hydrant` / `extinguisher` / `gas_suppression` | 设备符号、系统图 | 类型、尺寸 | nr / 项 | 气体灭火按系统计 |
| 暖通 | `duct_run` | 双线 + `WxH` | 宽、高、材质、板厚 | **展开面积 m²** | 周长 = 各边之和 |
| 暖通 | `duct_fitting` / `damper` / `grille` / `diffuser` | 明细表、符号 | 类型、尺寸 | nr / m² | 管件按展开面积 |
| 暖通 | `fan` / `ahu` / `fcu` / `pau` / `chilled_water_pipe` / `condensate_pipe` | 设备标注、管线 | 类型、容量、DN | nr / m | 设备与配管分列 |
| 暖通 | `duct_insulation` / `duct_support` / `flexible_duct` | 注记、明细表、末端虚线 | 面积、厚度、长度 | m² / nr / m | **软管段常漏** |
| 电气 | `cable_tray` / `ladder` / `trunking` / `conduit` | 双线、细线 + 尺寸 | 宽、直径、类型 | m | **竖向立管与引下段最常漏**；暗埋管在结构里 |
| 电气 | `cable` / `wire` | 系统图、竖井图 | 型号、截面、芯数、回路 | m | **长度须按芯数乘**；预留与波形系数按前章 |
| 电气 | `db` / `panel_board` / `mcc` / `luminaire` / `switch` / `socket` | 设备与图例符号 | 类型、额定值 | nr | 备用回路是否计入 |
| 电气 | `earthing` / `lightning_protection` / `bonding` / `gland` / `termination` | 屋面/竖井平面、详图 | 类型、长度、规格 | m / nr | 暗藏在结构里的接地体 |
| 弱电 | `data_voice` / `cctv` / `access_control` / `fire_alarm` | 图例符号 | 类型、点位 | nr / m | 线缆路由常无图 → 挂敞口 |

**MEP 通用纪律**：长度来自平面/剖面，**系统归属来自系统图（rise-and-fall / schematic）**，两张图必须联动。

### 6.8A 室外照明 / 路灯（看起来简单，实测错得最多）

**按系统树自电源侧向末端清点**，不要从灯具倒推。

| 层 | Class | 识别线索 | 关键属性 | 计量基础 | 常见陷阱 |
|---|---|---|---|---|---|
| 电源 | `mv_cable` / `package_substation` | 图例 `SUBSTATION (100kVA)`、回路表 `FEED FROM PS-xx` | 容量、座数、围栏/基础/接地 | m / nr | **只算馈线柜不算箱变**；中压电缆常无图 → 挂 RFI |
| 配电 | `feeder_pillar` / `lighting_panel` | 回路表标题、进/出线/备用回路 | 回路数、规格 | nr | **一页图可能排两个盘**，回路表要按盘归属 |
| 回路 | `lv_cable`（4C×N mm²）/ `ecc_conductor` | 回路表逐回路列出**长度** | 芯数截面、长度、ECC 规格 | m | **ECC 与主缆等长**，整项漏高发 |
| 敷设 | `conduit` / `cable_handhole` / `direct_buried` | 标准详图、图例管径 | 管径、材质、长度、井型 | m / nr | 直埋与穿管口径混淆；手孔按 TYPE 分列 |
| 末端 | `lighting_pole`（12/14/25m 高杆）/ `luminaire` | 图例 `25M HIGH MAST 6×335W` | 杆高、杆型、**每杆灯具数**、功率 | nr | **灯具数 ≠ 杆数**；杆型 6~7 种，别合并 |
| 配套 | `pole_foundation` / `anchor_bolt` / `earth_electrode` / `earth_grid` | 基础详图 + **BAR BENDING SCHEDULE**、接地详图 | 基础尺寸、**单座钢筋量**、接地极规格 | m³ / t / nr | 高杆基础与 12m 杆基础**差一个量级**，一个 m³/根 打天下会错数倍 |
| 附属 | `underbridge_lighting` / `retrofit_existing` / `remove_existing` | 图册子目录、图例 `EXISTING` 类 | 灯具、端子箱、导管 | nr / m | 整组漏高发 |

**两条硬规矩**：① 杆数由**布置间距**或**矢量聚类**得出，再用尺寸链/图例闭合；
灯具数 = Σ（杆数 × 该杆型灯具头数）+ 桥下灯具。② 每种杆型对应**自己的基础型式与钢筋量表**。

### 6.9 临时工程与措施项目

**这一类依赖工法与工期，不依赖图纸。** 每条必须 `source = method` 并要用户明确确认，
**禁止静默推导工期、周转次数、台班数**。

| Class | 取决于 | 计量基础 | 常见陷阱 |
|---|---|---|---|
| `formwork` | 构件几何 | 展开面积 m² | 若要计价则需周转次数（工法） |
| `scaffolding` / `falsework` / `propping` | 立面/高度/跨径/荷载 | m² 或 m³ | 内外架、使用时长 |
| `dewatering` | 地下水位、工期 | 项 / 周 | 工期须人工确认 |
| `shoring` / `temporary_support` | 开挖深度 | m² / m | 与支护桩界面 |
| `crane` / `vertical_transport` | 建筑高度、工期 | 项 / 时间 | 台班与工期须人工确认 |
| `hoarding` / `site_establishment` | 总平 | m / m² / 项 | 围挡范围 |
| `traffic_management` | 与道路的界面 | 项 | 导改方案依赖 |
| `muck_away` | 弃方体积、运距 | m³ | 运距须人工确认 |

---

## 7. Class 名速查

统一使用下列 `snake_case` 名，保证规则匹配确定性，**不要自造同义词**：

```
【土木/道桥】excavation, fill_compacted, trench_excavation, road_base, asphalt_surface, kerb, pavement_marking, drainage_pipe, manhole, gully, culvert, head_wall, wing_wall, apron, cut_off_wall, retaining_wall, piling, pile_cap, diaphragm_wall, ground_anchor, soil_nail, rail_track, switch, pipe_main, marine_works, dredging
【桥梁】pile, spread_footing, pier_column, pier_cap, abutment, bearing, bearing_pedestal, shear_key, girder, diaphragm, wet_joint, end_diaphragm, deck_slab, deck_waterproofing, asphalt_wearing, parapet, expansion_joint, drainage_spout, approach_slab, bridge_road_marking, bridge_lighting, underbridge_lighting, inspection_walkway, ocl_mast_foundation
【结构】footing_isolated, footing_strip, raft, column, beam, slab, rc_wall, stair_waist, stair_landing, formwork, rebar, precast_*, steel_column, steel_beam, steel_brace, roof_truss, purlin, steel_decking, bolts, welds
【建筑】brick_wall, block_wall, insulated_wall, curtain_wall, door, window, roof_light, internal_partition, suspended_ceiling, floor_finish, skirting, wall_finish, paint, roof_covering, waterproofing, insulation, tiling, stone, stair_finish, balustrade, handrail, external_works
【机电】pipe_run, pipe_fitting, valve, pipe_support, pipe_sleeve, pipe_insulation, sanitary_fixture, water_tank, pump, floor_trap, sprinkler_main, sprinkler_branch, sprinkler_head, fire_alarm_device, fire_damper, hose_reel, hydrant, extinguisher, duct_run, duct_fitting, damper, grille, diffuser, fan, ahu, fcu, pau, chilled_water_pipe, duct_insulation, flexible_duct, cable_tray, ladder, trunking, conduit, cable, wire, db, panel_board, mcc, luminaire, switch, socket, gland, termination, earthing, lightning_protection, bonding, data_voice, cctv, access_control
【路灯】mv_cable, lv_cable, ecc_conductor, package_substation, feeder_pillar, lighting_panel, lighting_pole, luminaire, lamp_head, pole_foundation, anchor_bolt, earth_electrode, earth_grid, cable_handhole, direct_buried, retrofit_existing, remove_existing
【工艺与措施】process_equipment, tank, vessel, pump_set, instrument, piping_(process), scaffolding, falsework, propping, dewatering, shoring, crane, vertical_transport, hoarding, site_establishment, traffic_management, muck_away
```

---

## 8. 明确的不适用范围（诚实边界）

| 不做 | 原因 | 遇到怎么办 |
|---|---|---|
| **无图纸输入的纯造价估算 / 指标测算** | 本技能的产出是"带图证的量"，不是"经验价"；没有图就没有出处 | 走造价指标路径，本技能不接管 |
| **纯 CAD 绘图与图面修改** | 不产出设计成果，只读取 | 用绘图/设计工具 |
| **需登录、付费或授权才能获取的资料** | 停止条件之一，不绕过 | 停下并说明，要用户提供 |
| **工法依赖的量未经确认就出数** | 模板周转、降水工期、台班数图纸上没有 | 标 `source=method`，进敞口台账要用户确认 |
| **把"有构件库"当"有项目级验证"** | 见 §2，中/浅专业没有实测错误模式 | 交付时显式声明沉淀档位 |
| **市场询价与单价调研** | 量的归量，价的归价 | 见 `references/evidence-grading.md` |
| **无图可依的关键参数硬猜** | 禁止编造图纸上没有的数字 | 挂 RFI 或按规范推导并标"推导链"，不写"待澄清"了事 |
