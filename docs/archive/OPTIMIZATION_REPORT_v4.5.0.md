# v4.5.0 优化与验收报告

日期：2026-10-29  
版本：`caogong-intl-qty 4.5.0`  
主题：OpenBIM 工作流工程化、IFC QTO 可信度加固、单命令流程编排

## 1. 本轮交付

在 v4.4.0 的显式 IFC QTO 读取、项目批准的 BOQ 映射、IDS-inspired 信息交付检查和 QTO 快照差异基础上，完成以下加固：

- 新增 `scripts/openbim_pipeline.py`，统一编排基线/当前 IFC 提取、交付 profile、当前版 BOQ 映射、数量快照差异及输入源哈希复核；输出 `PASS`、`REVIEW_REQUIRED` 或 `BLOCKED`。
- 新增 `assets/schemas/openbim-pipeline-result.schema.json`，固定流水线摘要的数据结构。
- 强化显式单位量纲检查，拒绝 `IfcQuantityVolume` 被错误长度单位覆盖、`IfcQuantityCount` 绑定长度单位等情况。
- 校验 IFC 转换单位的 EXPRESS measure type、转换因子有限性与正值；带 offset 的转换单位暂不转换，避免错误忽略偏移。
- 扩展常见 IFC4.3 道路、桥梁、隧道、铁路和基础设施对象识别范围；重复 GlobalId 作为阻断项。
- BOQ 映射核验来源 QTO SHA-256，并在提供/可访问原始 IFC 时重算文件哈希；传播上游 FAIL/WARN，不将异常压成 PASS。
- 交付 profile 检查传播 QTO 状态、校验项目范围、必需字段、重复/缺失 requirement ID 和异常数量要求。
- 快照差异拒绝负数、NaN/Infinity 等无效容差，并验证状态、哈希和 GlobalId。
- 更新安装说明、版本清单、示例项目夹具、测试门禁和 E2E 流程。

## 2. 当前验证结果

| 验证层 | 结果 | 说明 |
|---|---:|---|
| 核心数值单元测试 | 13/13 | `scripts/test_qto_core.py` |
| 领域对抗回归 | 20/20 | `scripts/test_domain_regressions.py` |
| OpenBIM 专项断言 | 31/31 | `scripts/test_openbim_qto.py`；覆盖 13 个测试方法及一键流程正反例 |
| 发布 smoke/regression | 20/20 | `QTO-RELEASE-SMOKE-V10`，含关键 CLI 入口、回归与项目配置自检 |
| 合成项目 E2E | 21/21 | 包含 OpenBIM 流程、图纸修订影响、工作簿门禁和故障注入 |
| 故障注入 | 10/10 | 定义的 10 种合成缺陷均命中目标门禁 |
| 输入工作簿哈希 | 一致 | E2E 前后未变化 |
| 源树 SHA-256 | 一致 | 测试开始与结束时相同 |

最终发布使用的源树指纹：

`a289898b2739ad4be40fa09e642f0a1f9e19dc8ae1a31456c39e3cfdd4e2f6f0`

机器可读的端到端摘要保存在 `examples/e2e_demo/reports/e2e_summary.json`；逐步结果与门禁契约见 `examples/e2e_demo/reports/E2E_TEST_REPORT.md`、`reports/gate_logs/` 和 `reports/gate_contracts/`。

## 3. OpenBIM 一键用法

```bash
python scripts/openbim_pipeline.py run \
  --baseline-ifc examples/e2e_demo/reference/openbim/model_rev_a.ifc \
  --current-ifc examples/e2e_demo/reference/openbim/model_rev_b.ifc \
  --registry examples/e2e_demo/reference/openbim/boq_mapping_registry.json \
  --profile examples/e2e_demo/reference/openbim/ifc_delivery_profile.json \
  --project-id SIM-E2E-001 \
  --out-dir out/openbim
```

示例项目中的 Rev A/Rev B 数量存在差异，因此预期结果是 `REVIEW_REQUIRED`，并且 `release_eligible=false`。这属于设计行为，不应将有变化的模型自动放行。相同且规则批准的合成模型可得到 `PASS`。

## 4. 必须保留的能力边界

- 当前 IFC 输入主要针对 IFC STEP 文本中显式存储的 `IfcElementQuantity` / 常见 `IfcQuantity*`；不根据几何推算缺失 QTO。
- 产品类型识别使用受控实体类型清单，并非完整 IFC4.3 schema/所有扩展实体的继承解析器。
- IDS 检查为本包定义的 IDS-inspired 子集，不是 buildingSMART 官方完整 IDS 解析器或 XSD 验证器。
- QTO 快照差异不等于 PDF/DWG 像素叠图、CAD/BIM 几何差分或自动增量重算。
- 自动 BOQ 映射仅接受项目审批的精确条件/明示通配规则；不做未审计模糊匹配。
- 全部测试基于合成夹具。它们证明预设程序行为，不证明真实项目合同计量合规、识图准确率、数量误差、市场价格或专业签审。

## 5. 发布建议

v4.5.0 可作为 OpenBIM 数量处理的开发基线，并用于经授权、经脱敏的受控试点。进入正式投标流程前，必须批准项目规则包与 BOQ 映射，用真实 IFC 导出器样本做黄金集验证，并由 QS/商务责任人复核所有数量变化、不可比较项和规则例外。
