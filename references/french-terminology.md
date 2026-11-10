# 法语图册构件词对照（DZ / MA / TN 等法语区）

> **开工前必读。** 法语图册上"看得懂每个字母"和"知道这个词指哪个构件"是两件事，
> 而读错构件词的后果不是误差，是**数量级错误**——本项目实测：图上的 `P-1~P-12`
> 是 12 个**墩**（PILE），按"桩位编号"去数会得到 12 根桩，而实际桩数是 168 根（−93%）。

## 一、同形异义：三个词先分清再落账

| 图上写的 | 法语实义 | 中文 | 常见误读 | 识别特征 |
|---|---|---|---|---|
| `CULÉE`（CULEE） | abutment | **桥台** | 当成"承台"或通用"台" | 只在桥两端，常配 `MUR EN RETOUR`（翼墙） |
| `PILE` | pier | **墩**（下部支承） | **当成"桩"** | 编号连号（P-1…P-12），位于两桥台之间 |
| `PIEU`（PIEUX） | pile (deep foundation) | **桩** | — | 只在基础平面/桩详图出现，常配 `Ø`、`L=`、`BETON DE PIEUX` |
| `RADIER` | raft / pile cap | **承台（筏板式）** | 与 `SEMELLE` 混 | 承台下有 PIEUX 时才是桩基承台 |
| `SEMELLE` | footing | **独立基础底板** | 当成承台 | 无桩，直接落地 |
| `CHEVÊTRE` | cap beam / crosshead | **盖梁** | 当成承台（两者都是横向大体积） | 位于墩柱**顶**，承托支座/梁 |

★ **一句话规则**：图名/图号里 `SUR PILES` = 墩处剖面，`SUR CULÉE` = 台处剖面。
看到 `P1~Pn` 先问一句"这是墩号还是桩号"——答不出就停下查桩位图。

## 二、构件词对照（按分部）

| FR 图面词 | 中文 | EN | 易混 / 识别特征 |
|---|---|---|---|
| `OUVRAGE D'ART` | 桥涵构造物 | structure | 图册总称 |
| `TABLIER` | 上部结构（桥面系） | superstructure/deck | 与 `INFRASTRUCTURE` 相对 |
| `INFRASTRUCTURE` | 下部结构 | substructure | 含墩台与基础 |
| `FONDATION` | 基础 | foundation | 含 `PIEUX`/`SEMELLE`/`RADIER` |
| `CULÉE` | 桥台 | abutment | 见 §一 |
| `PILE` | 墩 | pier | 见 §一 |
| `PIEU` / `PIEUX` | 桩 | pile | 见 §一 |
| `RADIER` | 承台（筏板） | pile cap | 见 §一 |
| `SEMELLE` | 独立基础 | footing | 见 §一 |
| `FÛT` | 墩身/柱身 | pier shaft | 与 `CHEVÊTRE` 区分 |
| `CHEVÊTRE` | 盖梁 | cap beam | 墩顶横梁 |
| `MUR EN RETOUR` | 翼墙 | wing wall | 台两侧，常按重量或面积计 |
| `MUR PARAFLU` / `MUR GARDE-GRÈVE` | 前墙 / 挡土前墙 | front wall | 常被误并进台身，**单价不同** |
| `POUTRE` | 梁 | beam/girder | `POUTRE PRÉFABRIQUÉE` 预制梁 |
| `POUTRELLES` | 小梁/纵梁 | joist | 数量多，按延米或根 |
| `DALLE` | 板 | slab | — |
| `HOURDIS` | 现浇桥面板（梁间板） | deck slab | 与 `DALLE` 常同图，厚度不同 |
| `ENTRETOISE` | 横隔梁/横撑 | cross girder | 梁间连接，易整项漏 |
| `APPAREIL D'APPUI` | 支座 | bearing | 必查专用表，禁止按片×端推算 |
| `NICHE` / `FEUILLURE` | 支座槽 / 支座垫石 | bearing plinth | 单价与支座分开 |
| `JOINT DE CHAUSSÉE` | 伸缩缝 | expansion joint | 法语清单常按**延米**，不是"道" |
| `GOUJON` | 锚栓 | anchor bolt | 通常成套计入支座/缝 |
| `TIGE` | 螺杆 | rod | `TIGE M20×250` 类 |
| `GARDE-CORPS` | 护栏/栏杆 | parapet/railing | 按延米 |
| `TROTTOIR` | 人行道 | sidewalk | 与 `CORNICHE`（檐口）区分 |
| `CORNICHE` | 檐口/挑檐 | cornice | 常为预制小构件，按延米 |
| `ÉTANCHÉITÉ` | 防水层 | waterproofing | 范围通常**扣人行道**，不是全桥宽 |
| `CHAPE DE PROTECTION` | 保护层/铺装 | protection screed | 厚度常写 `ep. min 4cm` |
| `BALLAST` / `GRAVE TRAITÉE` | 道砟 / 级配碎石 | ballast / treated base | 不属桥梁混凝土量 |
| `VOUSSOIR` | 拱石/缝体块件 | voussoir | 板式伸缩缝的缝体 |
| `DÉNOMINATION` | 构件明细表 | item schedule | **不是**材料表，是按构件列数量 |
| `NOMENCLATURE` | 材料表 | bill of materials | 与上者用途不同 |
| `COUPE` | 剖面 | section | `COUPE A-A` |
| `ÉLÉVATION` | 立面 | elevation | — |
| `PROFIL EN LONG` | 纵断面 | longitudinal profile | 标高/里程来源 |
| `PLAN` | 平面 | plan | `PLAN DE COFFRAGE` 模板图 |
| `COFFRAGE` | 模板 | formwork | 是否单列取决于计量标准 |
| `BÉTON DE PROPRETÉ` | 垫层混凝土 | blinding | 单独开项，易漏 |
| `ACIER POUR BÉTON ARMÉ` | 钢筋 | rebar | 常按 `MT`（吨） |
| `PRÉCONTRRAINTE` / `TORON` | 预应力 / 钢绞线 | prestressing / strand | 锚具、孔道、压浆须分别开项 |
| `GAINE` / `CONDUIT` | 波纹管/孔道 | duct | 按延米 |
| `INJECTION` | 压浆 | grouting | 按孔道体积或延米 |
| `ANCRAGE` | 锚具/锚固 | anchorage | 按套 |
| `ESSEC` / `ESSAI DE CHARGEMENT` | 静载试验 | load test | 措施项，易漏 |
| `ÉPROUVETTE` | 试块 | test cube | 措施项 |

## 三、单位写法（法语清单与英文清单不同）

| 法语清单写法 | 含义 | 本技能规范符号 |
|---|---|---|
| `m³` / `MC` / `M3` | 立方米 | `M3`（推荐）。`CM` 仅是可能出现的历史/项目代码，不作全局等价别名。 |
| `m²` / `M2` | 平方米 | `M2` |
| `ml` / `m.l` / `ML` | 延米 | `ML` |
| `U` / `un` / `ens.` / `ENS` | 个 / 套 | `U` |
| `t` / `T` / `MT` | 吨 | `MT` |
| `ff` / `forfait` | 包干项 | `FF` |
| `kg` | 千克 | `KG` |

**词表外或歧义单位 = 未校验，不是通过。** 若项目 BOQ 确实将 `CM` 定义为立方米，必须以该项目批准的单位别名注册表明确绑定合同/BOQ来源、条款、适用范围、批准人和日期；参见 `project-config-contract.md`。 门禁遇到不认识的单位会打 `DW-0` 并计入"未校验行数"，
报告里不得写"量纲已核"。

## 四、图种词与轴线编号（v4.6.0）

### 4.1 图种词（判图种时先查这张表，但仍要回图看标注）

| 法语图名 | 中文 | 陷阱 |
|---|---|---|
| `PLAN DE COFFRAGE` | 模板/布置图 | 只有编号与断面，**没有配筋**；在这里找钢筋 = 整批漏项 |
| `PLAN DE FERRAILLAGE` | 配筋图 | 常有 `@` 间距、`HA/FE/TS` 钢筋代号；构件外形常画不全 |
| `PLAN DE COFFRAGE ET FERRAILLAGE` | 布置+配筋合图 | 须分区判读，不能整张按一种处理 |
| `COUPE` / `COUPE TRANSVERSALE` | 剖面（横剖） | 图名常与立面混用，**按投影方向判，不按名字** |
| `ÉLÉVATION` | 立面 | 同上 |
| `PLAN DE NIVEAU` / `PLAN D'ÉTAGE` | 楼层平面 | 层号写在图名里，落账要带层号 |
| `NOTICE` / `NOTE DE CALCUL` | 说明 / 计算书 | 数值常与图形不一致 → 进跨图校验（`cross-drawing-consistency.md`） |
| `DÉTAIL` / `DÉTAIL TYPE` | 详图 / 标准详图 | 标准详图不是一处，须乘重复次数并留重复次数图证 |
| `PLAN DE REPÉRAGE` | 定位/编号图 | 只给编号不给尺寸，配钢筋表使用 |
| `PLAN DE FONDATION` | 基础平面 | 常与桩位图分开画，两处都要核 |

### 4.2 轴线编号的三种写法（欧洲/北非常见）

按大写字母与数字交叉编号的老规矩之外，法语图册还会出现：

| 写法 | 含义 | 读法 |
|---|---|---|
| `A, B, C…` + `1, 2, 3…` | 常规双序列 | 字母一般**不跳 I/O**（法式常全用 26 个字母），与 GB 不同 |
| `A'`、`A"`、`A₁` | 插入轴（后加的轴线） | **不是 A 的重复**，是独立轴线，计量要单列 |
| `E-a`、`F-b`、`Z1/Z2` | 扇形/曲线区段编号 | 一个"轴号"可能对应一段弧，**长度要按弧长算，不能按两轴直线距离** |
| 轴线带 `+` 偏移 | 轴线不居中（贴柱边） | 用偏移量定位，不能默认居中 |

**落账要求**：`location` 里的轴号必须与图面圆圈内字样完全一致（含撇号、下标、大小写）。
把 `A'` 写成 `A` 会让两条轴线合成一条，计量直接少一半——这是**看得见却最容易写错**的一类。

## 五、开工前动作（强制）

1. 拿到法语图册，**先做一页"图面词 → 标准构件名"映射**，作为建册附件；
   只列本册实际出现的词，不要求穷举。
2. 映射里必须回答三件事：`PILE/PIEU` 各指什么、承台叫 `RADIER` 还是 `SEMELLE`、
   伸缩缝单位是"道"还是"米"。
3. 映射未经确认前，不得落账任何墩/桩/台相关读数（体系未定，见 SKILL.md 停止条件 6）。
