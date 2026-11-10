# 安装说明 — 曹工国际工程算量助手（caogong-intl-qty）

版本 **4.6.5** ｜ 覆盖房建 / 市政 / 道桥 / 铁路 / 机电 / 水利 / 工业等多类型工程

> 本版（v4.6.4）**尚未进行**架构 / 软件 / 资深 QS 三视角独立验收，包内无 `ACCEPTANCE_REPORT_v4.6.4.md`；最近一份三方验收报告为 `ACCEPTANCE_REPORT_v4.6.1.md`。
> v4.6.4 已完成的验证：`test_all.py` 默认 34/34、`--extended` 65/65、四组门禁自证 （G-OFF 12/12、G-RUN 7/7、Input 6/6、G-SRC 6/6）；**这些是回归层面的验证，不等于项目级端到端验证，也不等于三方专业验收。**
> **v4.6.4 是独立第三方审核后的"自伤修复"版**：只修 v4.6.3 升级自身造成的 4 处回归
> （冒烟常量未同步、守护用例同义反复、H 编号文档矛盾、验收报告断链）。
> 审核发现的**设计级问题（致命 6 / 高 10）尚未整改**，清单见交付的审核包，需另行决策。

---

## 1. 放到技能目录

把解压出来的整个 `caogong-intl-qty` 文件夹放进 WorkBuddy 的用户级技能目录：

| 系统 | 路径 |
|---|---|
| Windows | `C:\Users\<你的用户名>\.workbuddy\skills\` |
| macOS / Linux | `~/.workbuddy/skills/` |

放好后的结构应当是这样（**注意必须是 `skills/caogong-intl-qty/SKILL.md` 这一层**，不要多套一层目录）：

```
.workbuddy/skills/
└── caogong-intl-qty/
    ├── SKILL.md
    ├── CHANGELOG.md
    ├── INSTALL.md
    ├── references/   34 份参考文档
    ├── scripts/      41 个脚本
    └── assets/       模板、开项清单与 JSON Schema（道桥 + 房建）
```

若只想当前项目用，放项目级目录 `{项目根}/.workbuddy/skills/` 亦可。

---

## 2. 装依赖

需要 **Python ≥ 3.9**。

```bash
# 必需
pip install openpyxl    # Excel 算量表读写
pip install pymupdf     # PDF 矢量提取（读轮廓化图纸、数图元）

# 可选
pip install pillow      # 高倍渲染后裁切局部，目视读图时用
pip install ezdxf       # 直接读 DXF（DWG 需先转档）
```

> IFC 结构校验 `ifc_validate.py` **不依赖 ifcopenshell**（纯文本 STEP 解析），
> 装了 ifcopenshell 也不影响；只有需要真做 3D 几何复核时才另装。

> pymupdf 缺失时仍可开工，但"数图元"（数桩位、数支座这类问题）会降级为
> 渲染 + 目视，**交付时必须标注"数量未经矢量法复核"**。

---

## 3. OpenBIM QTO 工作流（v4.5.0）

本版本提供 IFC STEP 中显式 QTO 数量读取、受控 BOQ 映射、受限交付 profile 检查、数量快照差异和一键流水线。它不根据 IFC 几何自动推算缺失数量，也不等于完整 IDS 解析器。正式项目必须使用经批准的映射注册表、交付 profile 和项目单位规则。

```bash
# 单项工具
python scripts/ifc_qto_extract.py extract --file model.ifc --out out/model_qto.json
python scripts/ifc_delivery_check.py check --qto out/model_qto.json --profile takeoff/register/ifc_delivery_profile.json --out out/delivery.json
python scripts/boq_mapping.py map --qto out/model_qto.json --registry takeoff/register/boq_mapping_registry.json --project-id <项目ID> --out out/boq.json
python scripts/ifc_qto_diff.py compare --baseline out/revA_qto.json --current out/revB_qto.json --out out/diff.json

# 基线/当前版完整一键流程
python scripts/openbim_pipeline.py run --baseline-ifc revA.ifc --current-ifc revB.ifc \
  --registry takeoff/register/boq_mapping_registry.json \
  --profile takeoff/register/ifc_delivery_profile.json --project-id <项目ID> --out-dir out/openbim
```

## 4. 自检

```bash
cd caogong-intl-qty/scripts

# 环境体检：会告诉你 ready / partial / needs_setup，以及缺什么、怎么降级
python check_environment.py

# 四个核心门禁的元自检（验证判据本身没坏）
python gate_dimension_wording.py selftest    # 末行：捕获率 = 4/4 = 100%，另需「合法行 0 误报」
python boq_lint.py selftest                  # 末行：capture rate: 100%  (missed: none)，另需两类误报为「无」
python vector_forensics.py selftest          # JSON 输出里 "failed": 0
python colmap.py selftest                    # 末行：结论: PASS（列映射识别与冲突检测）

# 开项清单与文档编号同步（改过清单或文档后必跑）
python drafter_qto.py verify                 # 期望 未展开=0、PASS

# v4 系列专项自证
python qto_legend_guard.py selftest          # 图例页/标签块/复本三防
python qto_repeat_groups.py selftest         # 重复组展开与乘数管控
python ifc_validate.py selftest              # IFC 结构校验规则可触发性
python project_orchestrator.py selftest      # 批次计划/断点续算/策略卡

# 项目清单 / 计量规则 / 单位别名注册表语义校验（项目根目录）
python scripts/qto_project_validate.py --manifest takeoff/register/project_manifest.json --project-root .
# 真实交付/生产放行前另外执行（模拟项目与草稿应当失败）：
python scripts/qto_project_validate.py --manifest takeoff/register/project_manifest.json --project-root . --release

# 一键全量回归（含配置语义校验、故障注入与核心性质测试）
python scripts/test_all.py
```

全部通过才算基础安装自检完成。该结果不等于通过项目级生产验收；首次使用前还应阅读 `references/production-readiness.md`，并完成项目规则确认与真实样例验收。

**关于元自检**：只看到「捕获率 100%」不算完——那只能证明判据够狠，
证明不了它不乱咬。G-DW 与 boq_lint 现在都带**反向断言**，要同时看到
「合法行 0 误报」「小计/合计行误报: 无」「量纲不相容行误报: 无」。

---

## 5. 怎么用

技能由 SKILL.md 自动触发，无需手动调用。说出这类话就会启动：

> 「按图纸核一下工程量」「复核这些数量有没有问题」
> 「建个三维模型要能导出工程量」「这个数看着不对」
> 「审一下某分部全不全」

若想直接跑单个工具，常用入口：

| 脚本 | 用途 |
|---|---|
| `build_input_register.py` | **第 0 步**：建册（图纸台账 + 范围声明 + 待确认清单） |
| `drafter_qto.py` | 绘图员读数台账：`new` / `add` / `check` / `coverage` / `verify` |
| `colmap.py` | **列映射单一事实来源**：G-DW / boq_lint 靠它从表头自动识别列号；传错列会被拦（退出码 2） |
| `gate_dimension_wording.py` | G-DW 跨列自洽：量纲 / 单位 / 口径词（另附零行守卫） |
| `boq_lint.py` | 审一份**既有** BOQ 清单的结构问题（L01–L10） |
| `vector_forensics.py` | 无文本层矢量图（文字转曲）取证：`probe` / `symbols` / `chain` |
| `xcheck_model_vs_2d.py` | 三维模型 vs 二维算量交叉复核 |
| **（v4）** `project_orchestrator.py` | **大型项目总控**：批次计划 / 断点续算 / OI 挂账 / 测量策略卡 |
| **（v4）** `qto_legend_guard.py` | **识图三防**：图例页、标签块、同代复本（`classify` / `dupes`） |
| **（v4）** `qto_repeat_groups.py` | **重复组展开**：典型区 × 实例数，差异单列、乘数无来源即挂起 |
| **（v4）** `ifc_validate.py` | **IFC 结构校验**："打开不显示"的五种病因 |

各脚本均支持 `--help`。

**列号不用手写**：G-DW 与 boq_lint 会先打印「角色 → 列号 → 实际表头文本」
对照表再开检。手写列号传错时的典型表现是**零发现**，而零发现和干净
在输出上分不出来——所以门禁选择自动识别 + 冲突即拒。

---

## 6. 三条上手前必读

1. **绘图员负责算量** —— 读数的责任人 = 建模 / 绘图的人；复核人只能复核，**不能代填**。
   复核人改了数就必须改签名栏，因为他已经成了新的责任人。
   详见 `references/drafter-qto-role.md`。
2. **口径登记两个维度** —— 每个量同时标**范围口径**（全桥 / 单幅 / 每跨…）
   与**证据强度**（图纸确认 / 推算 / 估算 / 待核）。缺任一维，这一行不许进表。
3. **开项清单反向扫漏项** —— 道桥类 88 项在 `assets/catalog/road-bridge.csv`，
   房建类 41 项在 `assets/catalog/building-unit.csv`，
   交付前跑 `drafter_qto.py coverage --catalog road-bridge|building-unit` 比对。
4. **（v4）识图三防先跑，再落总量** —— 图例页、标签块、同代复本三类错误
   每一行都指向真实图元、合起来就是错的，靠人眼发现不了；必须由
   `qto_legend_guard.py` 拦。
5. **（v4）策略卡没过不出交付版** —— 计量依据、数量分层、识图口径、体系乘数、
   审核责任五项必须先确认，否则只能出 DRAFT。

---

## 7. 常见问题

**Q：技能没被自动触发？**
确认 `SKILL.md` 在 `skills/caogong-intl-qty/` 这一层（不是再深一层），
且 frontmatter 的 `name: caogong-intl-qty` 没被改动。

**Q：跑脚本报 `ModuleNotFoundError`？**
按第 2 节补依赖。注意用哪个 Python 装的包，就要用同一个 Python 跑脚本。

**Q：我能改开项清单吗？**
能，但要两处同步：改 `assets/catalog/road-bridge.csv`（**这是唯一事实来源**），
再改 `references/road-bridge-openitems.md` 的对应说明，最后跑 `drafter_qto.py verify` 确认同步。
只改一边会导致漏项比对按错误编号配对。

**Q：v3.x 的房建开项清单怎么没了？**
v3.1.0 的打包漏掉了 `assets/catalog/building-unit.csv`，v4.0.0 已恢复（39 项），
v4.0.1 补登 B-32（上部结构标注类）/B-33（单模块洁具类型分列）→ 41 项
（B-01~B-44）。房建项目请用 `--catalog building-unit`。

**Q：包里有 `__pycache__` 吗？**
没有，打包时已排除。那是我方运行产生的字节码缓存，绑定具体 Python 版本，对接收方无用。


## 历史：v4.6.0 的三方验收整改（架构 / 软件 / 资深 QS）

> 本节记录的是 **v4.6.0** 那一轮三方验收的整改内容，不是 v4.6.4 的验收记录。

v4.6.0 是一次大改，三方独立验收后修了 30 处问题，其中 6 类属"会用错"级别：

| 类别 | 修了什么 |
|---|---|
| **崩溃语义** | 两个新脚本缺顶层兜底，非法输入带 traceback 退 **1**（读成"查到冲突"）。现统一退 2 + 一行 `[ERR]` |
| **假绿** | `render_tiles.py check` 全篇字高未校验仍判"通过"退 0；现判 **exit 2**——转曲/扫描图上"字高≥10px"这条判据等于没写 |
| **假红** | 台账校验读 `location` 列而表头是 `drawing_location`（正规写法反被判 FAIL）；法语 `3,60` 被当千分位读成 360；只有一侧写单位时两侧同乘系数。三处已修 |
| **版本闸** | 旧口径"rev 不同即 `RULE_CONFLICT`"会让项目中期全部阻塞（不同图册版本不同是常态）。现只拦"同一图号两个内容" |
| **专业口径** | 槽位表"搭接全楼累加数十吨"与计算规则"搭接不另计"正面冲突，已删；ACI 列混用两个版本编号，已统一并标`核对状态` |
| **覆盖缺口** | 跨图矩阵补 D 组建筑×结构 6 项（轴网 / 构件厚度 / 梁底净高 / 踏步数 / 基础埋深 / 层数） |

新增硬规矩 **H35~H38**（比例失真图禁量测 / 跨专业单一 owner / 暂列金额不进净量 / 变更量按原规则重算）。

## v4.3.0 图纸修订影响闭环与技能结构优化（历史）

本版新增图纸台账快照差异分析，以及“变更图号 → 受影响工程量 → 人工复核证据 → 发布门禁”的闭环。工具不会做 PDF 像素叠图、DWG 语义差异解释或自动重算；哈希变化只作为回图复核触发条件。

```bash
python scripts/drawing_revision_diff.py compare \
  --baseline takeoff/register/drawing_register_baseline.csv \
  --current takeoff/register/drawing_register_current.csv \
  --impact-map takeoff/register/revision_quantity_impact.csv \
  --project-id "PROJECT-001" \
  --out-dir takeoff/revision_review \
  --fail-on-unreviewed
```

详见 `references/revision-impact-workflow.md`。另新增 `agents/openai.yaml` 元数据、行业能力对标与差距路线图，并把 `SKILL.md` 控制在 500 行以内，细节按任务渐进式加载。

v4.3.0 的历史结果为 `QTO-RELEASE-SMOKE-V7` 及其 43 项检查。v4.3.1 历史发布门禁为 `QTO-RELEASE-SMOKE-V8`（13 项）；v4.4.0 当时的发布门禁为 `QTO-RELEASE-SMOKE-V9`（19 项）；`python scripts/test_all.py --extended` 可额外执行完整 CLI 入口和历史自检。合成项目 E2E 仍需单独运行并以其生成的报告为准。测试仅证明固定合成夹具行为，不代替真实项目专业签审。

---

## v4.2.0 历史：项目配置治理、发布证据与端到端测试

安装依赖后，从技能包根目录运行：

```bash
python scripts/test_qto_core.py
python scripts/test_domain_regressions.py
python scripts/test_all.py
python examples/e2e_demo/run_e2e.py
```

本段命令和 42/42、`QTO-RELEASE-SMOKE-V6` 数字仅对应 v4.2.0 历史版本。v4.5.0 当时的套件编号/分母与新增整改以 `CHANGELOG.md` 和 `examples/e2e_demo/README.md` 为准。

v4.2.0 的历史复用机制要求 `QTO-RELEASE-SMOKE-V6` 和 42/42；从 v4.3.0 起包含图纸修订影响分析入口；v4.3.1 将发布契约升级为 `QTO-RELEASE-SMOKE-V8` 并修复相关门禁问题；v4.4.0 新增 OpenBIM QTO、BOQ 映射、交付 profile 和数量快照差异工作流；v4.5.0 强化量纲与来源校验、增加 IFC4.3 基础设施实体识别、综合流水线与上游状态传播。

注意：模拟项目的所有图纸、数量、价格、规则和审核身份均为虚构；软件 PASS 不等于合同合规、工程量正确、市场价合理或支付认证。生产发布仍需按 `references/engine-architecture-v3.md` 和 `references/production-readiness.md` 完成真实项目验证及授权签审。



## v4.5.0：OpenBIM 流水线与可信度加固

新增 `openbim_pipeline.py`，一条命令完成基线/当前 IFC 显式 QTO 提取、交付 profile 检查、BOQ 映射、数量快照比较及综合结果。只有所有硬门禁通过、快照无变化、输入哈希不变时才返回 `PASS`；有变化需 `REVIEW_REQUIRED`，硬失败返回 `BLOCKED`。

同步修复显式单位量纲不匹配、转换单位类型/偏移风险、重复 GlobalId、无效比较容差、QTO 源哈希缺失、BOQ 无效数量计数和交付检查不传播上游 FAIL 等问题。当前套件为 `QTO-RELEASE-SMOKE-V12`，默认 34 项检查；OpenBIM 专项回归包含 IFC4.3 基础设施实体和失败状态传播等反例。

```bash
python scripts/test_openbim_qto.py
python scripts/openbim_pipeline.py --help
python scripts/test_all.py
python examples/e2e_demo/run_openbim_demo.py
python examples/e2e_demo/run_e2e.py
```

## v4.4.0：OpenBIM QTO 与 BOQ 工作流

新增四个可本地运行的模块：`ifc_qto_extract.py`（读取 IFC 中已存储的数量集）、`boq_mapping.py`（项目批准的精确 BOQ 映射和单位转换）、`ifc_delivery_check.py`（IDS-inspired 受限交付 profile 校验）、`ifc_qto_diff.py`（比较 QTO 快照并强制人工复核）。详细命令与已知限制见 `references/openbim-qto-workflow.md`。

```bash
python scripts/ifc_qto_extract.py selftest
python scripts/test_openbim_qto.py
python examples/e2e_demo/run_openbim_demo.py
python scripts/test_all.py
python examples/e2e_demo/run_e2e.py
```

当前发布套件为 `QTO-RELEASE-SMOKE-V12`，默认 34 项检查；合成项目 E2E 目标为 21 个工作流步骤。OpenBIM 演练使用虚构的两版 IFC QTO 数据，演示提取、单位、清单映射、交付 profile 和“变更必须复核”门禁，不代表真实设计模型几何验证、全量 IDS 合规、CAD 视觉差分或合同计量签审。

## v4.3.1 专家审核整改

- 修复新增/删除图纸在 SHA-256 校验前被分类的问题；哈希缺失或非法始终阻断。
- 模型/二维对量使用共享 `qto_core.convert`，先验证单位并转换到同一单位；不同量纲、未知单位、缺失单位或未审批歧义单位标记 `NOT_COMPARABLE` 并阻断。
- 发布回归采用有界进程组清理，减少子进程/管道继承导致的超时死锁；E2E 超时也会终止整个进程组。
- 默认发布套件为 13 个发布检查；扩展模式 `python scripts/test_all.py --extended` 会运行全量 CLI `--help` 与 11 项扩展自检。
