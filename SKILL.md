---
name: caogong-intl-qty
description: "国际工程图纸算量、BOQ核对、图纸版本影响分析与2D/IFC模型复核。适用于建筑/市政/道桥/铁路/机电/水利/工业工程；先确认项目规则与图纸基准，再取量、追溯证据、跑质量门禁并输出复核包。遇到提量、工程量复核、BOQ差异、图纸变更重算、模型对量或数量完整性检查时使用。"
version: 4.6.5
agent_created: true
---

# 曹工国际工程算量助手

读国际工程图纸做工程量测算与三维建模，**并强制执行一套让算错和口径错在交付前自己暴露的检查体系**。

核心立场：**算量本身不难，难的是发现自己算错了。**
本技能的主要产出不是那个数字，而是**证明这个数字可信的过程**。

> **v4.6.2**：21 轮复盘确认**不是知识不足，是三个机制缺口**——① 门禁只验内部自洽；
> ② 无"没跑就出不了交付物"的机械闸；③ 无执行凭证。补强 **G-SRC / G-RUN / 净量原则 / 反自欺规则**。
> **v4.6.3**：吸收分流技能 ccj-qty v3.3.0 的**表外缺席**判据——G-SRC 抓"读错的东西"，
> **G-OFF 抓"根本没读到那一块"**（整册空白/必搭配件/分母疑漏/敞口四态），补上缺口 ① 的另一半。
> 详见 `references/source-semantics-and-receipt.md`、`references/off-table-gaps.md`。

## 何时用

- 给图纸（PDF 图册 / DWG / DXF / 扫描件）要求算量、提量、复核工程量；建三维模型并导工程量
- "这个数看着不对但说不上来哪不对"；判断一份已有算量表能不能信；审某分部完整性；以多方对比报告为纲做分轮修订

不适用：无图纸输入的纯造价估算、纯 CAD 绘图修改、需授权资料。（v2.0.0 起取消"不适用 DWG/DXF"——**图纸格式只决定取证路线**，不构成适用范围限制。）

## 三档渐进

| 档 | 触发 | **输入消化** | 建册 | 范围声明 | 十一岗 | 门禁 |
|---|---|---|---|---|---|---|
| **全量** | 建模型 / 全流程 / 报价 | **强制 12 项** | **强制** | — | 全开 | 核心+交付+注入自证+**凭证** |
| **中档** | 复核一批数量 | **强制 12 项** | **强制**（先锁定"复核的是哪一版"） | — | 全开 | 核心+交付+**凭证** |
| **轻档** | 核对某个数 | 可免册，但 D-02/D-07 必答 | 可免册 | **必填一行** | 只跑相关维度 | 相关门禁+**凭证** |

**轻档两条硬规矩**：① 可**免册**，但须留一行范围声明
`本次只核 <对象>，不保证 <未审范围>；图纸版本 <版本>；未建册。`；② 交付时显式列出"未审维度及原因"。
**不能让人误以为全审过了**——沉默会被读成"都查了"。

---

## 主流程

### 第 -2 步：输入消化（**全量 / 中档强制；本步不产出任何数量**）

> ★ **v4.6.2**：本项目 21 轮迭代中，绝大多数"后来才暴露"的问题，根因不是算错，
> 而是**标书与图纸前期没消化**——拿着图就跑脚本，边算边发现口径与范围。
> **消化 ≠ 读过一遍**，验收标准是能回答四问且**有出处**。

| # | 必答问 | 不合格 | 合格 |
|---|---|---|---|
| 1 | **算哪些**（范围） | "按图纸算" | 分部清单 + **排除项及依据** |
| 2 | **按什么算**（规则） | "按国际惯例" | 计量标准 + 版本 + 项目修订**条款号** |
| 3 | **拿什么算**（输入） | "图纸都在文件夹" | 目录树 + 图号/版本/图种/文字层，缺件单列 |
| 4 | **算到什么程度**（交付） | "出个工程量" | 交付物清单 + 精度 + 验收人 + 敞口上限 |

```bash
python scripts/input_digest.py init   --root . --project "<名>" --drawings <目录> --tender <标书>
python scripts/input_digest.py set    --file takeoff/input_digest.json \
        --id D-07 --answer "..." --source "标书 Vol.2 p.147 §3.4" --by 张三
python scripts/input_digest.py verify --file takeoff/input_digest.json --strict   # 硬闸
```
**四条硬规矩**（H39/H40）：

1. **未消化完不出量**：verify≠0 → 只能出 `DRAFT`；且须过 **G-OFF 图册覆盖率闸门**（页数/空白页**由人逐册登记**，机器只校验自洽性与缺页，**不做 PDF 扫描**；故"目录已列全"仍须人签字，未登记=未核≠通过）。
2. **结论必须带出处**：写"按合同"不合格，须写"标书 Vol.2 p.147 §3.4"。**无出处 = 没消化**。
3. **标书与图纸交叉读**：互指条款（图纸"见规范"↔规范"详图纸"）必须落到一端并登记。
4. **问题立即分流**：口径冲突→OI、图纸缺件→RFI、范围不明→**停下问**。

**典型反例**：第 12 轮才发现箱涵表 `DISCHARGES(m3/S)` 是**流量列**非尺寸列（已算错两轮）；
第 18 轮才发现负荷表每张含 2~3 个柜（漏算 69%）。**都是看一眼表头就能避免的。**
完整 12 项清单与评审节点见 `references/source-semantics-and-receipt.md` §1。

---

### 第 -1 步：测量策略卡 + 项目总控（**全量 / 中档强制**）

> 拿图纸就跑脚本是大型项目最常见的失败方式。**先定口径，再动图。**

```bash
# ① 建总控 + 策略卡（card-check 未确认项会 FAIL）
python scripts/project_orchestrator.py init --root . --project "<名>" --discipline 房建
python scripts/project_orchestrator.py card-init / card-check --root .
# ② 排批次（每批须有可判定验收条件）③ 逐批推进（不打断人，疑问挂 OI）④ 断点续算
python scripts/project_orchestrator.py add-batch --root . --id B01 --title "<标题>" \
    --scope "<范围>" --inputs "<输入>" --budget medium --accept "<验收条件>"
python scripts/project_orchestrator.py next|start|oi|done|resume --root . [--id B01 ...]
```
**测量策略卡**（`takeoff/plan/measurement_strategy_card.json`）必须确认五件事，否则**只能出 DRAFT**：

| # | 必确认 | 要点 |
|---|---|---|
| 1 | **计量依据** | 合同测量标准+版本+项目修订（不许默认"国际通用规则"） |
| 2 | **数量分层** | 理论量/合同计量量/材料需求/采购/投标 BOQ **分列**，损耗不进计量面积 |
| 3 | **识图口径** | 图例页不计安装量、标签块不是构件、同内容指纹只计 1 张 |
| 4 | **体系乘数** | 塔楼/栋数/重复区段乘数须登记来源，否则整套 BOQ 等比例错 |
| 5 | **交付与审核** | BOQ 结构、小数位、带标记图纸、复核人与放行权限 |

**不打断协议**：口径不确定 → 挂 OI 继续下一批，不反复问；只有"计量口径本身没定 / 图纸缺失致
整批无法开工 / 交付形式与合同冲突"三种才停（`large-project-protocol.md`）。

### 第 0 步：环境检查 + 建册（**全量 / 中档强制；轻档不建册**）

```bash
python scripts/check_environment.py
```
状态 ready 直接开工；partial 按降级路径走并标注限制；needs_setup 先补齐。
**建册**：打开任何一张图之前，先把"算哪一版、算哪部分、哪里还没到手"锁成一张表。

```bash
# ① 扫目录机械提取图号/哈希（图名、工作包、部位**故意不猜**）；② 人工补 seed 后建册
python scripts/build_input_register.py scan  --dir <图纸目录> --out takeoff/register/
python scripts/build_input_register.py build --register .../drawings_seed.csv --project "<名>" \
        --out takeoff/register/ --scope .../scope.csv --rev-addendum "<图册版本>"
```
★**强制点**：建册状态不是 `CONFIRMED` 时退出码 **1**，成果只能标 `DRAFT`、**不得交付**；
强行推进须 `--allow-draft`。

**建册时必须列图纸目录树**（v2 新增）——见 `appurtenance-pairing.md` §6。
目录里出现而清单里没有的子目录，一律登记为待核，不许默认"属于别的分部"。

---

#### 图纸修订影响分析（招标补遗/新图到达/重算前必须执行）

收到新图或补遗时，冻结旧台账快照、生成当前快照并跑 `drawing_revision_diff.py compare`
（`--baseline`/`--current`/`--impact-map`/`--fail-on-unreviewed`）。未映射、缺哈希、图号歧义或
影响复核未闭合时阻断发布。此工具比较台账/哈希，**不做 PDF 叠图或自动重算**；见 `revision-impact-workflow.md`。

#### 项目配置预检（计量前必须执行）

先跑 `qto_project_validate.py` 校验项目清单、计量规则注册表、单位别名、引用文件与软件版本。
未完成合同规则/图纸基准审批的项目只能出草稿或待确认台账，不能标为可发布。

```bash
python scripts/qto_project_validate.py --manifest takeoff/register/project_manifest.json --project-root .
```
含歧义单位时须由合同条款与授权审批支持；见 `project-config-contract.md`。

### 第 1 步：判型 + 读图（不可跳过）

#### 1.1 先判型，再选技法

```bash
python scripts/extract_dwg_vector.py <图>.pdf --list    # 判型：text/fonts/图元（毫秒级）
python scripts/vector_forensics.py probe <图>.pdf --out out/probe.json  # --out 是文件名非目录
```

| `text` | `scanned` | 页面性质 | 首选路线 |
|---|---|---|---|
| > 0 | False | 原生矢量 + **完整**文字层 | **文本层解析** |
| > 0 | False | 原生矢量 + **残缺**文字层（部分转曲） | **文本层打底 + 图元/尺寸链补缺口**（不是二选一） |
| > 0 | True | 扫描 + OCR 层 | 原生 dpi 切块；OCR 仅检索辅助 |
| 0 | True | 纯扫描 | 原生 dpi 切块 + 二值化 |
| **0** | **False** | **文字转曲矢量** | **矢量取证 → 尺寸链闭合 → 渲染切块** |

切块走 `render_tiles.py build --pdf <图> --scale 3.0 --overlap 0.15` 再 `check --manifest`（15% 重叠 / 字高 p10 ≥10 px；转曲图量不到字高判 exit 2，改走人工精读）。

**两条对称的错**：`text>0` ≠ 标注都在文字层（本项目 `text=1237/2782/10382`，仍有大量尺寸只能靠图元）；
`text=0` ≠ 扫描件（转曲矢量无字体对象但有上千图元，提倍数或反复 OCR **都救不回来**）。
只看一个指标定路线，两个方向都错。

**图名 ≠ 图种，判据是标注内容**：有 `@`（间距符号）或多行竖排集中标注 → 配筋图；只有编号+截面 → 布置图。
法语 `PLAN DE COFFRAGE`（布置）/ `PLAN DE FERRAILLAGE`（配筋）。
**在布置图上找配筋，是整批漏项的开始**（`drawing-reading-playbook.md` §0.2）。

#### 1.2 比例尺双校准　#### 1.3 方向先判再取值

**比例尺**：单次标定出错无从发现，**必须两个已知尺寸互相印证**，差值 >2% 说明量错对象。
**方向**：**图上有这个数 ≠ 它回答了你要问的问题**。遇 `SKEW`/`MEASURED ALONG THE SKEW`/
`SLOPE %`/`OFFSET` 必须确认量取方向（实测：把纵向梁区总长 25998 当横向桥面宽 → −39.4%）。

#### 1.4 图纸自洽性预检（多图册 / 房建 / 道桥 · 算量前必须跑）

**算量前先验图纸自洽，比算完再改省一个数量级**：说明写 3600、剖面写 3300，你按哪个算？

```bash
python scripts/cross_drawing_check.py init --spec takeoff/cross_check.json --project "<名>"
python scripts/cross_drawing_check.py run  --spec takeoff/cross_check.json --out takeoff/cross_check_report.json
```

| 结论 | 含义 | 处置 |
|---|---|---|
| `PASS`/`N/A` | 同基准同范围且差≤容差 / 不适用 | 无（跨版本另标） |
| `CONFLICT` | 两侧有值且差超容差 | 挂 OI，**下游量不得出交付值**（H33） |
| `UNVERIFIABLE` | 缺一侧/缺溯源/基准或**范围**不同/写法歧义 | 挂 OI，继续算其它 |
| `RULE_CONFLICT` | **同一图号**两个内容（哈希不同） | 先定版 |
| `UNFILLED` | 模板未填 | 补填或 `N/A+理由`；全篇未填判 exit 2 |

矩阵 **4 组 22 项**（平面×立面 5 / 平面×剖面 6 / 立面×剖面 5 / **建筑×结构 6**），判据见
`cross-drawing-consistency.md`，门禁号 **G-XD**，结论在 `chain_check` 之后才采信。
容差：**标高/结构 ±0、派生标高 ±10mm、装修 ±5mm、计数 ±0**——统一 ±5mm 会放过"3600 vs 3300"，
拿 ±0 比"分段累加 vs 总标注"又会把合规高程链全判死，两个方向都是错。
**版本闸只拦"同一图号两个内容"**：跨图册 rev 不同是常态，继续比并标「跨版对照」；`--strict-rev` 才停。

### 第 2 步：绘图员落账取量（v2 核心机制）

> ★ **读数的责任人 = 建模/绘图的人。复核人只能复核，不能代填。** ★

#### 2.1 读数四问（下笔前必须能答）

| 问 | 必须答出 |
|---|---|
| ① **在哪** | 图号 + 图面区域/坐标 |
| ② **什么方向** | 纵向/横向/竖向/斜向 |
| ③ **谁的什么属性** | 构件名 + 属性（宽/高/厚/长/间距/数量） |
| ④ **是不是我问的** | 这个数回答的是不是这一行要问的问题 |

四问答不全 → **不许落账**。落账后必须跑 `drafter_qto.py check`，**exit 0 且无
`DRAFTER-Q`/`DRAFTER-SIGN`/`DRAFTER-DATE` FAIL** 才算成功；`add` **先自检再写盘**——
位置写"见图"、构件写"构件"、属性写"宽"这类空话，或日期 `2026/13/45`、单位越界，**该行不入库**；
位置须能落回图上：坐标（`x642~654`/`(592,391)`）或具名图元（`表/立面/剖面/COUPE/PLAN/DETAIL`）。

#### 2.2 口径登记三个词表（别混，混了就查不出）

"证据"在本技能里有三套词，**回答三个不同问题**，同一行要分别登记：

| 词表 | 回答什么 | 取值 | 落在哪 |
|---|---|---|---|
| **范围口径** | 覆盖多大范围 | 全桥/单幅/双幅(两幅合计)/单侧/**两侧(同一幅的两条边,≠双幅)**/每座/每跨/每根/每延米；汇总:总计/合计 | 口径声明列 |
| **数量证据强度** | 这个量哪来的 | `图读·标注`/`图读·量表`/`图读·尺寸链`/`规范推导`/`惯例外推`/`业主清单反算`/`待核` | 依据类型列 + `evidence_word` |
| **取证方法** | 用什么手段取的 | **六档** method（见 2.3） | `method` 列 |
| **价格成熟度** | **单价**哪来的 | A 已回价 / B 待询价占位 / C 经验推导 | 价格档列（evidence-grading.md） |

**`图读` ≠ `图纸确认`**：图上"有标注"只说明读到一个数，不说明设计已确认。旧写法 `图读` 一律按
`图读·尺寸链` 计（最低档）。**证据强度最终值 = min（来源强度, 图册成熟度上限）**：

| 图册状态 | 允许的最高强度 |
|---|---|
| EXE / 施工图，已批准（Rev ≥ C） | 图读·标注 / 图读·量表 |
| APD / 招标图，Rev A/B，未批准 | 图读·尺寸链（标注须写 `图读·标注-待设计确认`） |
| **APS / 方案图 / V0 首版** | **`图读·尺寸链` 封顶**——写"图纸确认"等于把"我量到了"说成"设计确认了" |

#### 2.3 取证方法六档（method 字段）

| method | 强度 | 硬性要求 |
|---|---|---|
| `schedule_direct` 专用表直读 / `text_layer` 文本层解析 | strong | — |
| `chain_derived` 尺寸链闭合 / `vector_cluster` 矢量聚类 | medium | 必须有计算式 |
| `render_read_text` **切块读字** | **medium** | 必须记 tile 坐标 + 字高 |
| `render_read_est` 渲染估读 | **weak → 必须挂 RFI** | 必须写证据去处 |

★**v4.6.0 拆档**：旧版把切块读数与缩略图目测并作 weak，会把人逼向"宁可推尺寸链也不读图"，
而尺寸链的前提（关系读对）本身才是弱项。**读到字的算 medium，没字可读的才是 weak。**
历史台账 `render_read` 一律按 `render_read_est` 计（从低不从高），不批量改历史 CSV。

#### 2.4 落账工具

```bash
python scripts/drafter_qto.py new --out takeoff/register/ --package 桥梁
python scripts/drafter_qto.py add --file <台账>.csv --template A-2-3 --code CBS-OVA-I03 \\
  --item "桥台台身混凝土" --sheet "<图号>" --location "C.1 台区立面 x642~654" --direction 纵向 \\
  --object "桥台台身" --attr "楔形 顶1.00/底5.50/高6.00" --method chain_derived --span 全桥 \\
  --evidence 推算 --value 503.10 --unit M3 --formula "2×(1.00+5.50)/2×12.90×6.00" --drafter 曹工 --date 2026-10-21
python scripts/drafter_qto.py check --file <台账>.csv && python scripts/drafter_qto.py coverage --file <台账>.csv --catalog road-bridge
```
详见 `references/drafter-qto-role.md`。

#### 2.5 识图三防（v4.0.0，**落账前必须跑**）

这三类错会**整批虚报**，表面看每行都有出处，靠人眼难发现，必须用工具拦。

```bash
python scripts/qto_legend_guard.py classify --file <明细>.csv --out takeoff/register/ --fail-on-unk
python scripts/qto_legend_guard.py dupes    --file <扫描清单>.csv --strict
python scripts/qto_repeat_groups.py expand  --file <重复组>.csv --out takeoff/register/ --fail-on-pending
```

| 防 | 错误 | 正确做法 |
|---|---|---|
| **防1 图例页** | 符号示意页 540 符号计成 540 台设备（虚报 4429→真实 3880） | 图例页**不出安装量**；"型号↔符号"对照单列存档 |
| **防2 标签块** | `DOOR TAG` 19369 个当门（真实 1879 樘） | 标签块只作型号回证，不参与计数 |
| **防3 同代复本** | 78 组四图号族同 sig，按文件数计量 4 倍虚报 | 同 sig 只计 1 张；`$TDUPDATE` 裁决版本 |

判据与真实案例见 `quantity-semantics.md` §2。

### 第 3 步：跑门禁（分三级）

```bash
python scripts/gate_basis_freshness.py --init takeoff/basis.json   # 首次：生成基准模板，不许手写 JSON

# ① 必跑组（顺序不能反）
python scripts/chain_check.py selftest                                 # 闸门自己在不在工作
python scripts/chain_check.py run --spec <尺寸链>.json --out out/       # 模型里尺寸关系成不成立
python scripts/gate_basis_freshness.py <表>.xlsx --basis <基准.json>   # 基线可不可信
python scripts/gate_drawing_basis.py   <表>.xlsx --basis <基准.json>   # 量 vs 基线
python scripts/gate_sheet_formula.py   <表>.xlsx                       # 多小计逐组覆盖

# ② 交付组（8 条）——参数：表.xlsx 为主；G-DW 需 --sheet；G-XS 需 --map；G-EXP 需 --expect
python scripts/gate_scope_declaration.py / gate_single_owner.py / gate_unit_consistency.py <表>.xlsx
python scripts/gate_dimension_wording.py <表>.xlsx --sheet <页签>      # ★跨列自洽（列号自动识别）
python scripts/gate_cross_sheet.py <汇总表>.xlsx --map <跨表映射.json>
python scripts/gate_expected_total.py <表>.xlsx --expect <期望值.json>  # ★业主清单闭环
python scripts/gate_source_semantics.py table --file <外部清单>.xlsx --out out/src_check/   # ★G-SRC
python scripts/gate_off_table.py check --file takeoff/off_table.json --strict --out out/g_off.json  # ★G-OFF
python scripts/gate_execution_receipt.py verify --file takeoff/gate_receipt.json --strict   # ★G-RUN

# ③ 专项组
python scripts/gate_scope_options.py <表>.xlsx --summary <汇总表>.xlsx --summary-cell C7
python scripts/gate_fault_injection.py <表>.xlsx --allow-subprocess --xs-map <绝对路径>
python scripts/boq_lint.py check --boq <既有清单>.xlsx --sheet <页签>   # 列号自动识别
python scripts/colmap.py selftest / ack_util.py --help                  # 列映射自证 / WARN 台账
```

★**必跑组的顺序不能反**：`input_digest verify` → **G-OFF（表外缺席）** → `chain_check` →
**G-XD（图与图之间）** → **G-SRC（数据源语义）** → `gate_basis_freshness` →
`gate_drawing_basis` → **G-RUN（凭证收口）**。
**基线过期时"数值全部相符"是假通过；尺寸链不闭合时"数值与图纸一致"也是假通过**——
图纸那一侧同样是被读错的那一版（`references/dimension-chain-gate.md`）。

**列号不要手写**：G-DW 与 boq_lint 由 `colmap.py` 从表头自动识别（表头定角色 + **数据验角色**），
并打印「角色 → 列号 → 实际表头文本」对照。手写列号传错时典型表现是**零发现**——
而零发现和干净在输出上无法区分。显式传入的列若与识别结果冲突，门禁拒绝运行（exit 2）。

**退出码统一**：`0` 通过 ／ `1` 不通过 ／ `2` 门禁自身无法运行（含崩溃、**演练模式**、**列映射未核**——
那不是"通过"，也不是"抓到工程量问题"）。调用方必须区分 2 和 1。

**★ 汇总只能写三栏**：`通过 N 项 / 未运行 K 项 / 待确认 M 项`。

- **分母为 0 一律计入「未运行」**（实测：APS 表单价列全空时 G-UNIT 检 0 行、G-PROP 算 0.00 元，
  两条都 exit 0，汇总写"13/13 通过"）。**零数据行同样算未运行**（v2.1.1 起 10 道门禁 0 行一律 exit 2；
  修复前 9 道里 7 道打印「门禁通过」——**没有一道说实话**）。
- 只有 `未运行==0 且 待确认==0` 才允许写"全部门禁通过"。每门禁须报「实检行数/应检行数」。
- **假红同样要修**：空表上"表比基准新"必然成立致 G-FRESH 假红；出现过一次整条门禁就被跳过。

判据库见 `gate-library.md`。**注意**：项目实例里的 `tools/` 不属于本技能，不要照抄该路径；
按上面 3 组命令逐条跑，或按项目自建运行器。

### 第 4 步：门禁自证（不可跳过）

```bash
python scripts/gate_fault_injection.py <表>.xlsx
python scripts/gate_fault_injection.py <汇总表>.xlsx --allow-subprocess --xs-map <跨表映射.json> --sheet <页签>
python scripts/gate_dimension_wording.py selftest --out out/   # 以下四条为各判据自证
python scripts/boq_lint.py selftest --out out/ && python scripts/vector_forensics.py selftest && python scripts/colmap.py selftest
python scripts/cross_drawing_check.py selftest && python scripts/render_tiles.py selftest
```

**对已定义且成功注入的必测缺陷，用例必须全部符合预期**（以 `gate_fault_injection.py` 捕获结果为准），
同时报告覆盖率、跳过项、反向误报断言与原文件前后摘要。此结论仅适用于当前测试集，
**不得表述为真实工程缺陷捕获率 100%**。退出码非 0 或 2（演练/未真跑）均不算完成验证。
另看 `用例覆盖 x/y`——分母缩水说明剩余判据**并未被验证**。

**元门禁必须同时断言「没漏报」和「没误报」。** 只查 `missed 为空` 证明不了判据不乱咬——
误报比漏报更伤：漏报只漏一个，误报会训练人忽略整条门禁（R-DW-1 实测误报 5 处后被跳过）。
G-DW 与 boq_lint 均配反向断言（合法行/小计行/量纲不相容行号绝不许出现在 finding 里）。
四条细节：① 分母只算"成功注入"（SKIP≠抓到）；② WARN 型按**输出标识**判非退出码；
③ `--xs-map` 须绝对路径；④ 原表自证前后摘要须一致（改过原表则结论作废）。

---

### 第 5 步：模型 × 二维交叉复核（建了模型就必须做）

```bash
python scripts/xcheck_model_vs_2d.py model.json t2d.json --stage export
python scripts/xcheck_model_vs_2d.py model.json t2d.json --stage all --skip-lot 钢筋 模板 支座 \
        --report 交叉复核报告.md
```

三条铁律：**两侧不同源、可比范围先声明、第一次见差异先怀疑导出。**

**★ 零差异告警（风险信号，非独立性判定）**：两侧若 ≥90% 项相对差恰为 0.000% 且
`param_source` 同源，标"疑似共模风险"，须查参数来源/导出链/计算路径。
零差异既不能证明共模也不能证明独立；有小差异同样不能证明独立——独立性靠来源记录、
参数依赖、工具链与人工抽样判断。几何离散误差应记为误差来源，不得人为修平。

#### 5.1 IFC 结构、QTO 与模型交付校验（v4.5.0）

```bash
# 一键：结构校验 → 基线/当前取量 → 交付预检 → BOQ 映射 → 快照差异
python scripts/openbim_pipeline.py run --baseline-ifc revA.ifc --current-ifc revB.ifc \
  --registry takeoff/register/boq_mapping_registry.json --project-id <ID> --out-dir out/openbim
# 分步：ifc_validate / ifc_qto_extract / ifc_delivery_check / boq_mapping / ifc_qto_diff
```

先做 IFC 结构校验，再读模型中**明确存储的** `IfcElementQuantity`；按已批准映射汇总 BOQ、
检查交付要求、比较快照。新增/删除/数量变更/单位不可比必须人工复核，不能自动放行。
`PASS` 才可放行，`REVIEW_REQUIRED` 须复核，`BLOCKED` 表示硬门禁失败。提取器不重算几何，
IDS 仅为受限子集。**校验通过 ≠ 合同数量正确**：IFC QTO 只是模型当前记录，须与二维图纸、
计量规则及已批准映射交叉核对（`openbim-qto-workflow.md`、`ifc-structure.md`）。

#### 5.2 模型 × 二维交叉：必须走脚本，禁止自建对照表（v4.5.3 强化）

```bash
python scripts/xcheck_model_vs_2d.py --model out/model_qto.json --sheet <二维算量.xlsx> --stage all --out out/xcheck
```

**硬规矩**：
- 两侧必须**不同源**（`model-2d-crosscheck.md` §一）。同名项 ≥90% 相对差恰为 `0.000%` 判 `X0-ZERO`/**FAIL**
  ——这是"假通过"不是"高度一致"。
- **不许自建脚本或手搓对照表替代**：自建对照只验证"自己两处算法一致"，必然"全部一致"。
  实测某海岸项目 13 层全 0.000%，写成"交叉检核通过（独立代码路径）"，实为同源复算，
  本会直接 FAIL，因未走脚本而漏网。
- 同源时**只能**表述「同源复算一致（仅验证算术，不验证断面读法）」，不得计入独立校验，
  也不得据此写"无需回图复核"。

**模型几何两项硬对账**（`ifc_validate` 只查签名/关系/挤出，查不出这两项，必须自己做）：
① **模型长度 = 算量长度**——逐结构算 `Σ|Depth|` 与沿轴投影长，须相等且等于算量 L；
分段结构各段以中心放置会互相嵌套，长度退化为 `max(L_i)`（**IFC-15**）。
② **布置重叠量化**——OBB 采样重叠率 +「可用净距 vs 所需总长」，缺口为正即布置不成立，
须标"示意布置"并降级用途（**IFC-16**）。详见 `ifc-structure.md` §7.5/§7.6。

---

### 第 6 步：十维 × 十一岗审阅

按 `quality-system.md` 逐维出意见：十维答"**看什么**"（D1~D10，每维唯一视角）；
十一岗答"**谁有权拦**"（PG-01~PG-11，含 **PG-11 绘图员**）。
**每维有且只有一个 accountable（A）**，R ≠ A。**AI 出意见+闭合状态，不代签**，签发栏输出 `＿＿＿＿＿＿`。
非 PASS 项须有去向，三态之一：已闭合/阻断/挂账。**禁止写"已关注""持续跟踪"**——等于没有责任人。

**审阅必须是独立信号**：出量的人不审自己的量（同一上下文的自校验拦不住自己，
见 `quality-system.md` §七）。读图错误只有人能抓——用 `make_review_html.py` 出审阅稿，
按图面位置回图复核，见 `human-review-loop.md`。

---

### 第 7 步：交付

必须包含：

1. **算量表**：每行带出处 + 口径 + 证据档（`assets/qty-sheet-template.csv`）
2. **读数台账**：绘图员落账与签字原始记录。`drafter_qto.py coverage` 出总体覆盖率（仅作筛查，
   不替代关键项证据检查）；强制项缺证据不得标 `DELIVERED`；0.8 阈值不是正确性的充分条件。
3. **门禁报告**：哪几项过/报红/原因/**是否注入自证**；G-XD 须报
   `通过/冲突/无法校验/版本分裂/未填` 五数，**"无法校验"不得折算为通过**。
4. **审阅意见**：十维×十一岗结论 + 本次未审维度及原因。
5. **假设与敞口台账**：依据类型、假设人、确认日期、**若被推翻的处置**。
6. **交叉复核报告**：含未纳入比对项（覆盖边界）。
7. **（v4）测量策略卡**：`card-check` 通过（无 TO_BE_CONFIRMED、approval_status=APPROVED）。
8. **（v4）符号型号对照表**：图例页读出的"型号↔符号"，**单列存档，不进 BOQ**。
9. **（v4）复本清单与重复组展开表**：同代复本处置记录、乘数来源与差异单列
10. **（v4.6.2）输入消化清单**：`input_digest.py verify` exit 0（12 项均有答案+出处+签署）
11. **（v4.6.2）门禁执行凭证**：`gate_execution_receipt.py verify --strict` exit 0。
    **无凭证不得写"全部门禁通过"**；汇总只能写"通过 N / 未运行 K / 待确认 M"
12. **（v4.6.2）数据源语义声明**：`gate_source_semantics.py verify` exit 0
13. **（v4.6.4）表外缺席报告**：`gate_off_table.py check --strict` exit 0（COV/PAIR/RATIO/OI/LEDGER）；无基准带须明写"未校验"。**`init` 后必须填 `systems`（bridge/culvert/road/lighting/building/mep，可多选）与 `structure_system`**——缺 systems 会按 component_classes 反推（INFO，须补登）；反推不出则 `PAIR-4` exit 2。见 `references/off-table-gaps.md` §1

---

## 交付状态与规则版本

项目必须登记批准的图纸基准、合同计量规则/版本、范围、责任人、规则来源；规则缺失或冲突标
`DRAFT`/`RULE_CONFLICT`。理论量/合同计量量/投标 BOQ 量/材料需求量/采购量/申报量必须分开，
每条可追溯。发布边界见 `production-readiness.md`；图纸哈希变化按 `revision-impact-workflow.md`。
**AI 不代替合同解释、设计确认、支付认证或授权签发。**

## 硬规矩与停止条件（摘要）

完整条款 **H01~H48** 及道桥八项闭合检查见 `references/hard-rules-and-stop-conditions.md`
（引用直接写编号如 `H12`）。执行中至少守住：

- **H01/H02** 绝不编造图纸未提供的尺寸/数量/价格；推定须与图读证据分层。
- **H05** 计量规则或单位有歧义时先记录冲突与依据，不得默认"国际通用规则"。
- **H19/H28** 建册未 `CONFIRMED`、体系乘数未登记来源时，不得出完整交付版或对外量差结论。
- **H14~H16** 未执行/零数据/exit 2 与真正 PASS 必须区分；不得以旧日志替代当前测试。
- **H10/H24~H27** 选项项、图例页、标签块、同代复本、损耗不得计入合同净量。
- **H32** 可数构件禁止"约 XX"——数不准写 `❓` + tile 坐标 + 未定点数。
- **H33** 跨图参数冲突未裁决前，不得用任一值驱动下游量。
- **H34** 切块读数须能唯一复原到图面（tile 坐标或图号+轴网+截图+像素坐标）。
- **H35~H38** 比例失真图不得量测；跨专业同一构件只能一个计量 owner；暂列金额不进净量；变更量按原规则重算。
- **H39~H43** 输入未消化不出量；结论带出处且 AI 不代签；数据源先登记语义；净量原则；无凭证不得称"全部门禁通过"。
- **H44~H48（v4.6.3）** 表外缺席须过 G-OFF；未核≠通过；比率带外只指路、禁内置惯例带；敞口四态（"挂账"即未判）；图注>实测>推算且注记须落地；工法未定不出措施量。
- 每个数量应能追溯至图纸/模型、位置、方向、参数、公式、单位、规则版本、责任人、复核记录；
  **AI 不代替 QS、设计、合同授权人或最终签发人**，无证据项标 DRAFT/OPEN。

## 参考文件

| 文件 | 什么时候读 |
|---|---|
| `references/drawing-reading-playbook.md` | **读图技法总库**（判型/图种/技法卡/T7 切块纪律/失效模式） |
| **（v4.6.2/3）** `references/source-semantics-and-receipt.md` · `off-table-gaps.md` | **开工前+交付前必读**：输入消化 / G-SRC / G-RUN / **G-OFF 表外缺席** / 净量原则 / 反自欺 / 效果验证 |
| `references/cross-drawing-consistency.md` | 第 1.4 步：跨图一致性（4组22项/容差/版本闸/G-XD） |
| `references/rebar-check-slots.md` | 钢筋 R1~R10+P1~P3 检查槽位与 EC2/BAEL/ACI 条款号 |
| `assets/prompts/tile_read_{zh,en,fr}.md` | 切块精读指令（正/负向清单 + JSON schema） |
| `examples/acceptance_cases.md` | 人工验收用例（结构三条带硬回归值） |
| `references/vector-forensics.md` | 无文本层/`partial_vectorized` 图的取证路线 |
| `references/drafter-qto-role.md` | 第 2 步：绘图员负责算量（读数四问/台账/自检） |
| `references/quantity-semantics.md` | 数量六层分层 + 识图三防 + 何时拒绝出数 |
| `references/quality-system.md` | 第 6 步：十维 × 十一岗 + RACI |
| `references/gate-library.md` / `dimension-chain-gate.md` | 门禁判据库 + 尺寸链闸门（落数前读）；**发布冒烟总闸 `scripts/test_all.py`**（默认闸一键收口，须 `RELEASE SMOKE RESULT: N/N` 且两次 `SOURCE TREE SHA256` 相等），内含三个整改自证 `test_source_semantics.py` / `test_input_digest.py` / `test_execution_receipt.py` |
| `references/failure-modes.md` | 取量前读 B/C 组，交付前读 E 组，新错先来归因 |
| `references/model-2d-crosscheck.md` | 第 5 步：建了模型必读（两侧必须不同源） |
| `references/input-register.md` / `appurtenance-pairing.md` | 第 0 步：建册格式 + 图纸目录树反查分部 |
| `references/measurement-standards.md` / `calculation-rules.md` / `project-config-contract.md` | 计量标准/扣减加项规则/项目配置契约 |
| `references/road-bridge-reading.md` / `road-bridge-openitems.md` / `french-terminology.md` | 道桥读图要诀/开项清单/法语构件词 |
| `references/human-review-loop.md` | 人工审阅回环（门禁查不了读法） |
| `references/production-readiness.md` / `revision-impact-workflow.md` / `revision-rounds.md` | 交付状态/版本影响/分轮修订 |
| `references/openbim-qto-workflow.md` / `ifc-structure.md` | IFC QTO 提取映射 / 结构校验与 IFC-15/16 |
| `references/hard-rules-and-stop-conditions.md` | 完整硬规矩 **H01~H48**、停止条件 |
| `references/evidence-grading.md` / `building-quantity-practice.md` / `large-project-protocol.md` / `disciplines-coverage.md` | 询价可信度 / 房建实战 / 大型项目协议 / 专业覆盖 |
| **历史与对标**（归档） | `qto-failure-patterns-summary.md` / `engine-architecture-v3.md` / `script-migration-matrix-v3.md` / `industry-benchmark-and-gap-plan.md` |

---

## 一句话提醒

大量算量事故不是四则运算错，而是**量错对象、口径错位、范围漏项、图纸复本虚增或基线过期**——
根源多是**输入未被消化**。典型模式见 `qto-failure-patterns-summary.md`、`failure-modes.md` 与各门禁回归。
新问题必须沉淀为**可重复的失败案例**，不能只在当前项目里临时修正。
