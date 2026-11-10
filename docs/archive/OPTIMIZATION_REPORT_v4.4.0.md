# v4.4.0 优化与验收报告：可落地的 OpenBIM QTO / BOQ 工作流

## 1. 本次目标

本次依据《国际工程算量软件深度功能拆解研究报告》中可直接落地、可在当前 Python 技能包内验证的建议，补齐 IFC 显式 QTO 读取、经审批的构件—BOQ 映射、模型信息交付预检和数量快照差异复核流程。重点不是复刻商业软件，而是形成开放、可审计、可继续扩展的数据工作流。

## 2. 已落地能力

1. **IFC 显式数量读取**：`scripts/ifc_qto_extract.py` 读取 IFC STEP 中显式 `IfcElementQuantity` / `IfcQuantity*` 数据，保留 GlobalId、IFC 实体、数量集、数量名称、单位来源、输入 SHA-256 与解析发现。对支持范围内的项目单位和常见转换单位进行受控换算；不支持或歧义单位不会猜测。
2. **项目审批的 BOQ 映射**：`scripts/boq_mapping.py` 通过项目范围限定、已审批、精确的映射规则，把 IFC 数量映射到 BOQ 编码，检查唯一匹配、量纲/单位兼容、批准依据和被排除对象；支持相同 BOQ 行聚合。未映射、重复映射、未经审批或单位不可换算都会阻断。
3. **IDS-inspired 信息交付预检**：`scripts/ifc_delivery_check.py` 检查指定实体类型、必要标识/名称、Qto 数量集、数量类型和单位等约定。它是本技能限定范围内的信息交付 profile 检查，不是完整的 buildingSMART IDS 解析器。
4. **QTO 快照差异与复核闭环**：`scripts/ifc_qto_diff.py` 比较已提取快照，识别新增、删除、数量变化与不可比较项。任何变化/不可比较均要求人工复核，不自动批准、不自动重算、不直接放行。
5. **单位核心增强**：扩展常见英制/公制长度、面积、体积、质量和时间单位的显式换算，继续禁止不同量纲直接比较；转换规则不明确时采取失败关闭。
6. **发布和 E2E 集成**：新增能力接入默认发布套件 `QTO-RELEASE-SMOKE-V9` 与合成项目 E2E；新增 JSON Schema、模板、参考工作流和合成 IFC 示例。

## 3. 可复现命令

在技能包根目录运行：

```bash
python scripts/test_qto_core.py
python scripts/test_domain_regressions.py
python scripts/test_openbim_qto.py
python scripts/test_all.py
python examples/e2e_demo/run_e2e.py --skip-smoke
```

`--skip-smoke` 只在 `examples/e2e_demo/reports/gate_logs/test_all.log` 存在且完整通过、时间新鲜并且源树指纹与当前源码一致时允许复用。若代码、规则、模板或测试输入发生变化，须重新运行 `python scripts/test_all.py`。

## 4. 验收结果

| 验收项 | 结果 | 说明 |
|---|---:|---|
| 核心单元测试 | 13/13 PASS | 单位、精度、JSON、门禁及安全路径等 |
| 领域对抗回归 | 20/20 PASS | 图纸修订、项目规则、模型/二维比较等 |
| OpenBIM QTO 专项回归 | 15/15 PASS | 提取、单位、映射、profile、快照差异及负例 |
| 默认发布回归 V9 | 19/19 PASS | 源码指纹前后一致 |
| 合成项目 E2E | 21/21 PASS | 包含 6 个 OpenBIM 子检查的集成步骤和 10 项故障注入 |
| E2E 输入工作簿/控制文件 | 哈希未变化 | 测试未修改原始夹具 |

本次发布回归使用完整独立执行的 smoke 日志；E2E 的复用路径会重新核验该日志的套件 ID、计数、失败标记、时间和源码指纹。以上结果证明固定合成夹具的程序行为，不等于专业工程量验收。

## 5. 明确的能力边界

- IFC QTO 当前读取 IFC STEP 文件中显式存储的 `IfcElementQuantity` / `IfcQuantity*`，不通过几何拓扑重新计算缺失数量；不应将“能解析 IFC”理解为“支持 IFC 中所有几何和属性组合”。
- 支持的转换单位是受控子集；其他转换型单位需要在注册表中明确验证，不能由 AI 猜测。
- BOQ 映射必须绑定项目 ID、批准状态、依据和适用范围；示例审批信息为虚构测试数据，不能在真实项目中复用。
- `ifc_delivery_check.py` 是 IDS-inspired profile，不是完整 IDS 标准解析器，也不替代几何质量检查。
- `ifc_qto_diff.py` 比较 QTO 快照，不进行模型几何叠图，不自动重算变化数量；变更进入人工复核流程。
- 本版未声称实现 DWG/PDF 视觉识别、像素/几何级图纸叠图、完整 IFC 几何算量、BCF 生产级交换、完整钢筋下料、5D、ERP 或税费计价模块。
- 真正投标使用前，仍须以项目合同、适用计量标准及其版本、业主补充规定、经批准的映射和经专业人员签认的真实黄金样本进行校验。

## 6. 发布结论

v4.4.0 将可独立验证的 OpenBIM 数据处理和 BOQ 规则治理能力融入现有技能，可作为受控试点和后续开发基线。不得将合成测试 PASS 解释为已取得真实项目专业签审或全部商业竞品能力等价。
