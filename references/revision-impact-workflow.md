# 图纸修订影响分析工作流（v4.3.0）

## 目的与边界

本工作流借鉴成熟算量产品中的图纸版本管理、批量比对、变更标记与测量数据导出实践，补齐“修订登记 → 变化识别 → 数量影响映射 → 人工复核 → 放行/阻断”的闭环。

`scripts/drawing_revision_diff.py` 当前比较两份图纸台账快照的图号、版本字段、SHA-256 和指定元数据，再将发生变化的图号映射到工程量行或经授权的“无数量影响”结论。

**它不是 PDF 像素叠图、DWG 几何语义比较、图纸差异自动解释，也不会自动重算工程量。** 如需几何叠图，请使用经授权的 CAD/PDF 审图软件；工具输出的是变化清单及审查门禁，不代替视觉审查。

## 输入

1. `--baseline`：已审批/曾使用的图纸台账快照 CSV。
2. `--current`：本次收到并准备使用的图纸台账快照 CSV。
3. `--impact-map`：图号到工程量行的映射与审查结果。
4. `--project-id`：项目编号；用于报告识别。

影响映射 CSV 的空白表头模板见 `assets/templates/revision_quantity_impact.template.csv`；机器可读报告的结构约束见 `assets/schemas/drawing-revision-impact.schema.json`。

图纸快照至少需要 `sheet`, `rev`, `sha256` 字段。推荐沿用 `build_input_register.py` 产生的完整表头。每份快照中每个图号只能有一个非 `superseded` 的有效记录；多条有效记录、缺哈希或格式不正确的哈希会被判为歧义，不得视为未变化。

影响映射至少包含：

`sheet, quantity_id, boq_code, item, review_status, reviewer, reviewed_at, evidence_ref, rationale`

- 有数量影响：状态必须为 `RECALCULATED`，填写工程量行 ID、人工复核人、ISO 日期、证据引用。
- 无数量影响：状态必须为 `NO_QUANTITY_IMPACT`，`quantity_id` 留空，并填写理由、复核人、日期和证据引用。
- `PENDING`、`IN_PROGRESS`、漏映射或缺少证据均为未闭合。
- `RECALCULATED` 表示数量已由专业人员重新计算/复核，不代表脚本自动重新算量。

## 运行

```bash
python scripts/drawing_revision_diff.py compare \
  --baseline takeoff/register/drawing_register_baseline.csv \
  --current takeoff/register/drawing_register_current.csv \
  --impact-map takeoff/register/revision_quantity_impact.csv \
  --project-id "PROJECT-001" \
  --out-dir takeoff/revision_review \
  --fail-on-unreviewed
```

生成：

- `drawing_revision_impact.json`：机器可读差异、输入快照哈希、数量映射、阻断项。
- `drawing_revision_impact.md`：可供投标团队审阅的摘要。

退出码：`0` 表示命令成功完成；如启用 `--fail-on-unreviewed`，且所有变化均已有证据支持的闭合处置，则为 0；有待处理/歧义时为 `1`；输入文件或参数无法读取时为 `2`。不启用 `--fail-on-unreviewed` 时会输出报告，但不得把报告存在等同于门禁通过。

## 变化类型

- `ADDED`：新进入当前台账的图号。
- `REMOVED_FROM_CURRENT_REGISTER`：基准中有、当前台账中没有；只能说明台账缺失，不能直接推断设计正式删除。
- `REVISION_AND_CONTENT_CHANGED` / `CONTENT_CHANGED`：哈希证明内容文件有变化。
- `REVISION_LABEL_CHANGED_CONTENT_IDENTICAL`：版本标签变化但登记的文件哈希一致，需要核实修订标签、文件归档与来源。
- `METADATA_CHANGED`：图名、专业、工作包、部位、比例或范围等元数据变化。
- `AMBIGUOUS_*`：无法可靠比较，例如多条有效记录或缺少有效哈希。
- `UNCHANGED`：在已登记哈希与被比较元数据范围内未发现差异；不代表合同基准或图纸本身已获批准。

## 强制复核原则

1. **任何变化都需要处置，变化不等于数量一定变化。**
2. 变更图号必须映射到受影响工程量，或由授权人员给出有依据的 `NO_QUANTITY_IMPACT` 说明。
3. 缺图/图号消失只标记“当前台账缺失”，不能由 AI 自行判断为作废。
4. 哈希仅证明文件字节不同；它不解释变化内容、工程量影响方向或幅度。
5. 必须把变化清单与专业审图软件中的图纸叠加/差异检查结合起来；关键变更必须回到图面复核。
6. 生成新量后，仍需执行项目基准、计量规则、单入口、单位量纲、期望数量、汇总/跨表等既有门禁。
7. 报告、清单和签审记录须一并归档，记录基准快照、当前快照、工具版本与人工处置证据。

## 与已有工具的关系

- `build_input_register.py scan/build`：登记收到什么图、文件哈希、状态、范围及责任人。
- `drawing_revision_diff.py compare`：比较两个已整理好的快照，并建立变更对工程量的影响链。
- `gate_basis_freshness.py`：检查工程量表采用的基准是否仍然可信/新鲜。
- `gate_drawing_basis.py`：将工程量与当前已确认基准比较。
- `drafter_qto.py`、`make_review_html.py`：记录工程量依据并由人按图面位置复核。

以上步骤不能互相替代：先查版本与影响范围，再审图、重算、跑数量门禁，最后由授权人员放行。
