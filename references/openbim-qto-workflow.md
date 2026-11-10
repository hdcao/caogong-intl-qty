# IFC QTO、BOQ 映射与模型交付检查（v4.5.0）

## 目标与边界

本模块把可落地的 openBIM 工作流拆成四个独立工具：

1. `ifc_qto_extract.py`：读取 IFC STEP 文件中明确存储的 `IfcElementQuantity` / `IfcQuantity*`，保留构件 GlobalId、实体类型、数量集、单位来源和输入 SHA-256。
2. `boq_mapping.py`：通过项目专属、已批准的精确匹配规则，将 IFC 数量映射至 BOQ 编码、转换到清单单位并汇总；每一数量都必须恰好映射一次，或匹配一条有审批依据的非计价排除规则。
3. `ifc_delivery_check.py`：按项目定义的 IDS-inspired 子集检查指定实体类型、数量集、数量名、量纲、Name 和 GlobalId。它不是 buildingSMART IDS 完整解析器。
4. `ifc_qto_diff.py`：比较两个提取快照中的构件数量变化，输出新增、删除、变化及不可比较项。只比较已提取的 QTO 值，不执行几何重新计算、不判断合同变更的应计量金额。
5. `openbim_pipeline.py`：一键编排基线/当前模型提取、交付 profile 检查、BOQ 映射、快照差异和源文件哈希前后校验，输出 `PASS` / `REVIEW_REQUIRED` / `BLOCKED` 综合状态。

以上工具使用 Python 标准库和包内 `qto_core.py`，不要求安装 IfcOpenShell；这不代表它们可以替代完整 IFC 几何引擎。提取器只读 IFC 已记录的数量，不会从几何形状重新计算缺失数量。若 IFC 文件只含几何、不含 QTO，必须走几何引擎或二维独立量算流程，不得将空结果当成零工程量。

## 标准工作流

```bash
# 1. 先做 IFC 结构校验，再读取已存储数量
python scripts/ifc_validate.py check --file model.ifc
python scripts/ifc_qto_extract.py extract --file model.ifc --out out/model_qto.json

# 2. 按项目已批准的信息交付要求检查
python scripts/ifc_delivery_check.py check \
  --qto out/model_qto.json \
  --profile assets/templates/ifc-delivery-profile.template.json \
  --out out/ifc_delivery_check.json

# 3. 按项目已批准的 BOQ 映射注册表汇总数量
python scripts/boq_mapping.py map \
  --qto out/model_qto.json \
  --registry takeoff/register/boq_mapping_registry.json \
  --project-id PROJECT-ID \
  --out out/boq_mapping.json

# 4. 比较已提取的两个模型/QTO 快照
python scripts/ifc_qto_diff.py compare \
  --baseline out/revA_qto.json --current out/revB_qto.json \
  --out out/model_qto_diff.json

# 5. 一键编排（推荐；任意上游硬失败会使综合状态 BLOCKED）
python scripts/openbim_pipeline.py run \
  --baseline-ifc model_rev_a.ifc --current-ifc model_rev_b.ifc \
  --registry takeoff/register/boq_mapping_registry.json \
  --profile takeoff/register/ifc_delivery_profile.json \
  --project-id PROJECT-ID --out-dir out/openbim_pipeline
```

如果项目单位别名注册表存在并已按项目审批，可在 BOQ 映射和快照比较命令中添加 `--unit-registry <registry.json> --project-id <PROJECT-ID>`。别名只能由 `qto_core.load_unit_alias_registry()` 按适用项目、批准人、日期和来源条款验证；不得在代码中为 `CM` 等歧义缩写加全局默认含义。

## BOQ 映射规则的强制要求

复制 `assets/templates/boq-mapping-registry.template.json` 后，为项目逐项配置：

- `project_id` 与合同/清单版本；
- 精确选择条件：`element_type`、`quantity_set`、`quantity_name`、`kind`；
- 清单编码、描述和目标单位；
- `status=APPROVED`、批准人、批准日期、来源文件和条款；
- 对不进入可支付/投标 BOQ 的 QTO，配置 `approved_exclusions`，写明排除原因与同等审批依据。

当前实现仅支持明确的精确匹配及显式 `*` 通配，不做未审计的模糊匹配或根据名称猜编码。一个源数量匹配零条或多条映射/排除规则均 FAIL。若一个 BOQ 编码的不同规则指向不同目标单位，也 FAIL。净量和毛量必须按目标规则分别映射；绝不因名字相近而自动任选一个。

## 结果语义

- `ifc_qto_extract`：数量成功读取且单位可确定才可 PASS；单位不明、引用损坏、重复键等关键问题 FAIL。工具同时保存 findings 和源文件哈希。
- `boq_mapping`：每个抽取数量恰好有一项已批准去向才可 PASS；有未映射、重复匹配、未批准规则、单位不可转换、缺失/非法来源哈希、源 IFC 哈希不匹配或上游提取 FAIL 时阻断。若源 IFC 不可访问且未显式传入 `--source-ifc`，结果为 WARN、不得视为可发布。WARN 不会被压成 PASS。
- `ifc_delivery_check`：只检查 profile 声明的字段子集；会传播上游 QTO FAIL/WARN，并可校验预期 project_id。PASS 不等于全量 IDS 合规，也不证明模型几何正确。
- `ifc_qto_diff`：发现新增/删除/数量变化/不可比较项、重复 GlobalId、无效来源哈希或非法容差时返回 `REVIEW_REQUIRED` 并以非零退出码阻断自动放行。必须将变化连到 BOQ 影响复核，或形成有依据的“无工程量影响”声明。
- `openbim_pipeline`：只有各阶段 PASS、数量快照无变化、源输入前后哈希一致时才返回 `PASS` 和 `release_eligible=true`；变化返回 `REVIEW_REQUIRED`，提取/映射/交付硬失败或输入文件执行中变化返回 `BLOCKED`。

## 已知边界

- 当前 IFC 读取基于 STEP 文本；产品识别使用受控实体类型清单（已补充常见 IFC4.3 道桥/基础设施类型），不支持所有 IFC 容器、所有版本扩展实体、完整属性集继承解析或几何运算。
- 显式单位必须与 `IfcQuantity*` 的物理量纲匹配；带偏移转换单位暂不转换，转换因子的 EXPRESS 测量类型及数值会被校验。
- 仅支持明确识别的常见 `IfcQuantityLength/Area/Volume/Count/Weight/Time`，不认识的数量类型会告警并跳过，不会猜测。
- `ifc_qto_diff.py` 比较提取出的数量，不做 PDF/DWG 图像叠图、几何差分或自动增量重算。
- IDS 检查是本包定义的受限 profile，不宣称兼容官方 IDS XSD 全部语法。
- IFC 数量是“模型中记录的数量”，不等于合同应计量数量。所有 BOQ 映射、量纲和计量口径必须由项目 QS/商务负责人确认；正式放行须遵守项目签审流程。

## 测试

```bash
python scripts/ifc_qto_extract.py selftest
python scripts/test_openbim_qto.py
```

领域回归覆盖：QTO 数量读取、单位归属/量纲校验、IFC4.3 道桥实体、重复 GlobalId、转换单位类型/偏移校验、升与立方米换算、BOQ 映射唯一性与来源哈希、上游失败状态传播、IDS-inspired 交付要求、一键流水线 PASS/REVIEW_REQUIRED、快照容差与重复标识异常，以及未知单位不得被默认为已知单位。
