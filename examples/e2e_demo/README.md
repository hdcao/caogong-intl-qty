# 模拟项目端到端测试夹具（SIM-E2E-001）

> **仅用于软件验证。** 本目录所有图纸、数量、单价、规则、审核身份、ACK 和报告示例均为虚构数据。PDF 明确标记为 NOT FOR CONSTRUCTION；不得用于投标、合同计量、现场施工或支付认证。

## 项目场景

模拟 100 m 道路及 1 座箱涵。基础方案包含土方、基层、沥青和箱涵底板/侧墙/顶板混凝土；另设 CPB 4.0 护栏 OPTIONS，要求与基础方案分开。虚构成本币种标记为 `SIM-CUR`，不代表任何市场币种或市场单价。

## 输入与证据

- `input/drawings/`：仅供矢量/文字层探测的示意 PDF。
- `input/quantity_takeoff.xlsx`：包含基础算量、成本汇总和独立复核清单。
- `input/summary_control.xlsx`：只为故障注入用例提供外部汇总控制值。
- `takeoff/register/scan/drawings_seed.csv` 与 `takeoff/register/scope.csv`：合成的图纸台账和范围声明。
- `reference/`：合成项目清单、规则注册、图纸基准、时效基准、期望数量、跨表映射、模型/2D 数量、ACK、修订影响映射，以及 `openbim/` 中两版合成 IFC QTO、已批准的合成 BOQ 映射和交付 profile。
- `reports/`：实际运行报告与每个命令的原始输出日志；`reports/openbim/` 记录 IFC QTO 工作流的提取、BOQ 汇总、交付校验和修订数量影响。
- `reference/revisions/`：基准/当前图纸台账快照与工程量影响闭环映射。

## 运行

从技能包根目录执行：

```bash
python scripts/test_qto_core.py
python scripts/test_domain_regressions.py
python scripts/test_all.py
python scripts/test_openbim_qto.py
python examples/e2e_demo/run_openbim_demo.py
# 扩展检查（完整 CLI 入口和历史自检）
python scripts/test_all.py --extended
python examples/e2e_demo/run_e2e.py --skip-smoke
```

`--skip-smoke` 只在最近 24 小时内存在当前套件 `QTO-RELEASE-SMOKE-V12` 的完整通过记录（34/34）、两次源树 SHA-256 均匹配当前版本且无失败记录时才复用；旧套件号、旧分母、部分失败、源树指纹不符及不完整日志均被拒绝。需要单次完整重跑时，执行：

```bash
python examples/e2e_demo/run_e2e.py
```

脚本会写入 `reports/E2E_TEST_REPORT.md` 和 `reports/e2e_summary.json`，并校验输入工作簿/控制工作簿哈希在运行前后不变。

## 验收结论的边界

v4.5.0 延续 21 个端到端工作流步骤，并在 OpenBIM 合成流程内增加一键流水线运行，验证提取、交付 profile、BOQ 映射、快照差异和综合放行状态。独立 smoke/regression 套件为 `QTO-RELEASE-SMOKE-V12`，默认包含 34 项检查（v4.5.1 增尺寸链闸门自证；v4.6.0 增跨图一致性闸门与切块 manifest 自证）。结果仅对这套夹具与这 10 个故障类型成立。

- `gate_cross_sheet.py --allow-hardcoded` 仅核对单元格数值，不证明公式依赖。
- 图纸基准时效检查使用 `--skip-mtime`，不把解压/复制时间当作图纸时效证据。
- 模型数量和 2D 数量是独立的**测试文件**，并未审计其真实来源独立性。
- PDF 探测是诊断，不代表脚本已从图元自动生成工程量。
- 图纸修订工具对台账/哈希做差异识别并检查影响映射；它不做几何叠图、差异解释或自动重算。
- 所有规则均是模拟规则，不代表采用了任何国际或国家计量标准。
- 软件测试 PASS 不替代工程师/QS/授权人对图纸、合同条款、数量、单价、范围和变更影响的专业签批。

## OpenBIM 合成测试边界

`reference/openbim/model_rev_a.ifc` 与 `model_rev_b.ifc` 是仅含示范数量集的合成 IFC STEP 文件，不提供真实几何模型。工作流验证的是数量提取、单位解读、经过审批的 BOQ 映射、受限交付 profile 和变更需人工复核；它不证明几何提量精度、完整 IFC schema 覆盖或官方 IDS 兼容。详细说明见 `references/openbim-qto-workflow.md`。
