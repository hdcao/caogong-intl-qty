# 项目配置契约与发布前校验

## 1. 目的与范围

本配置层把“项目依据是什么、何时生效、谁批准、引用文件是否存在、软件包与项目配置是否同版”从人工阅读要求变为可自动执行的前置检查。它不替代 QS/工程师对合同原文、当地计量标准、图纸、数量或付款资格的专业判断。

## 2. 三份主配置

- `project_manifest.json`：项目身份、国家/司法辖区、范围纳入/排除、合同/计量标准版本、图纸基准、数量口径策略、强制门禁和软件版本。
- `measurement_rule_registry.json`：每条计量规则需有唯一 `rule_id`、来源文件/修订/条款、适用口径、单位、加项/扣减/包含规则、舍入政策、生效区间、优先级、批准状态与测试用例。
- `unit_alias_registry.json`：只管理项目/地区/语言语境下的非标准单位别名。即使暂时没有别名，也要保留属于该项目的空注册表，以便绑定 `project_id` 和上下文。

对应 JSON Schema 位于 `assets/schemas/`，可用于编辑器提示和结构验证。真正发布前仍须运行下方语义校验器，因为 JSON Schema 不能独自验证文件是否真实存在、符号的物理量纲、项目范围、批准日期或发布状态。

## 3. 单位治理：`CM` 不再隐式等于立方米

`CM` 在不同市场/清单中可能是项目定义的旧代码，也可能被理解为厘米，不能全局认定为体积。默认使用标准且清晰的 `m3`/`m³`、`m2`/`m²`、`m`、`LM`、`t`、`kg`、`EA` 等单位。

如合同或 BOQ 明确把 `CM` 定义为立方米，需在 `unit_alias_registry.json` 中按项目逐条记录，例如：

```json
{
  "alias": "CM",
  "canonical_unit": "m3",
  "dimension": "volume",
  "approval_status": "APPROVED",
  "approved_by": "实际有授权的审批人",
  "approved_at": "2026-10-27",
  "source_document": "合同或 BOQ 的准确文件名及修订",
  "source_clause": "准确条款/页码/清单说明编号",
  "applicability": {"project_id": "实际项目编号"}
}
```

示例中的审批人、日期、文件和条款必须替换为真实且可追溯的信息，不能直接复制作为审批证据。禁止全局写入 `CM` 到代码白名单，禁止通过普通备注绕过项目审批。

使用项目别名时，相关门禁和算量台账必须显式传入注册表和项目编号：

```bash
python scripts/qto_project_validate.py \
  --manifest takeoff/register/project_manifest.json \
  --project-root . --package-root /path/to/caogong-intl-qty

python scripts/gate_unit_consistency.py takeoff/quantity_takeoff.xlsx \
  --unit-registry takeoff/register/unit_alias_registry.json \
  --project-id PROJECT-ID

python scripts/gate_dimension_wording.py takeoff/quantity_takeoff.xlsx \
  --unit-registry takeoff/register/unit_alias_registry.json \
  --project-id PROJECT-ID
```

若不使用项目单位别名，门禁无需传 `--unit-registry`，但必须使用标准明确的单位；出现歧义单位应停止计量或退回澄清，不能靠自动猜测通过。

## 4. 结构校验与发布校验的区别

常规校验：

```bash
python scripts/qto_project_validate.py \
  --manifest takeoff/register/project_manifest.json \
  --project-root . --package-root /path/to/caogong-intl-qty \
  --json-out reports/project_config_validation.json
```

`--release` 会进一步要求项目、计量规则和图纸基准处于已批准状态；批准人/日期、发布授权、强制门禁和未解决占位符必须满足要求。合成项目、草稿和待确认项目可通过“结构有效”校验，但 `release_eligible` 必须为 `false`。不能将结构有效理解为可用于报价、结算或付款。

## 5. 门禁结果契约

核心门禁结果采用 `assets/schemas/gate-result.schema.json` 中的 `schema_version=1.1.0` 契约，关键字段包括 `status`、`executed`、`checked_count`、`expected_count`、`skipped_count`、输入哈希、规则版本和问题明细。`NOT_RUN`、`ERROR` 或无法说明检查数量的结果不能被汇总器改写成 PASS。兼容旧脚本的端到端包装器会标出退出码、日志和当前源树哈希；这类封装结果不能伪装成底层脚本已经提供的检查行数。

## 6. 生产放行最低证据

1. 合同、技术规范和计量标准具体到版次/修订；存在优先级冲突时留有书面裁决。
2. 图纸清单有图号、修订号、接收/批准状态及替代关系；取消图和 superseded 图不会混入当前基准。
3. 计量规则逐条有依据、包含/排除/扣减、单位、舍入规则、测试用例和授权批准记录。
4. 清单行的数量依据、读数位置、计算式、图纸证据、责任人和复核状态可追溯。
5. 所有强制门禁执行且通过；未执行、未覆盖、冲突待核和重大例外不得靠总体覆盖率抵消。
6. 真实项目黄金样本由有资质/授权的专业人员签认，输入文件及发布包有哈希、版本和可复现的运行报告。

参见 `references/production-readiness.md` 与 `INSTALL.md`。
