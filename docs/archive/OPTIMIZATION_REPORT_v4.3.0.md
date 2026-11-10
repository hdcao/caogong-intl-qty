# 曹工国际工程算量助手 v4.3.0：行业能力对标、落地与验收报告

日期：2026-10-29  
版本基线：v4.2.0  
升级版本：v4.3.0

## 1. 本次升级目标

本轮不是追求在一个提示词中“塞进所有能力”，而是把主流工程算量工具中可公开观察、可迁移的工作流优势，转化为本技能可执行、可验证的组件；同时明确本地技能脚本与商业图纸产品的边界。能力对标用于工程路线设计，不是对商业软件内部实现的审计或性能排名。

## 2. 行业经验对标

| 行业参考 | 可借鉴的成熟能力 | 本版采取的行动 | 仍未声称具备的能力 |
|---|---|---|---|
| Autodesk Forma Construction Takeoff | 2D/3D takeoff、分类、使用当前项目文档 | 保留模型与二维数量核对；强化图纸版本基准与清单映射 | 完整原生 2D/3D 图形量取与商用云文档协同 |
| Bluebeam Revu | 比例校准、测量标记、Markups 导出、图纸修订比较/叠图 | 新增修订台账快照差异与工程量影响处置工作流；保留 PDF/矢量诊断入口 | PDF 页面对齐叠图、像素级差异、DWG 语义差异 |
| Togal.AI | 图纸检测、搜索、计数、标注与协作 | 规定视觉识别结果必须作为候选证据并进入人工确认与追溯流程 | 未声称本地脚本具有同等级视觉模型精确率/召回率或协作平台能力 |
| RICS NRM | 估算、成本计划、详细计量的不同规则体系；模板需检查逻辑 | 将项目适用规则、版本、批准和测试条件纳入配置/门禁 | 不默认项目适用 NRM；不打包未经许可的标准全文 |
| buildingSMART IFC | 开放模型结构、实体关系和跨工具数据交换 | 保留 IFC 校验与 2D/模型差异对照，强调模型来源、单位和版本 | 未声称兼容所有建模软件的全部 IFC 导出边界 |
| CSI 分类体系 | MasterFormat、UniFormat、OmniClass 等不同分类用途 | 建议将分类映射显式配置，而非混同不同分类体系 | 未默认任何分类体系适用于所有业主/国家 |
| OpenAI Agent Skills 指南 | 简洁入口、清晰触发条件、渐进式披露、按需读取资源 | 将主 `SKILL.md` 收敛到 500 行以内，并拆出按需参考文档及 `agents/openai.yaml` | 元数据文件不代表在任意宿主/平台已经完成兼容认证 |

公开参考链接：
- OpenAI Build skills: https://learn.chatgpt.com/docs/build-skills
- Autodesk Forma Construction Takeoff: https://construction.autodesk.com/tools/construction-takeoff-software/
- Bluebeam Revu: https://www.bluebeam.com/pricing/
- Togal.AI Features: https://www.togal.ai/features
- RICS New Rules of Measurement: https://www.rics.org/profession-standards/rics-standards-and-guidance/sector-standards/construction-standards/nrm
- buildingSMART IFC: https://www.buildingsmart.org/standards/bsi-standards/industry-foundation-classes/
- CSI Standards overview: https://www.csiresources.org/standards/overview

更完整的逐项分析、已实现/待建设差距与建议路线图见 `references/industry-benchmark-and-gap-plan.md`。

## 3. 本版已落地改动

### 3.1 图纸修订影响闭环

新增 `scripts/drawing_revision_diff.py`、输入模板、合成案例和输出 schema。工具比较基准与当前图纸台账，识别新增、移除、修订号/哈希变化、元数据变化、疑似歧义及未变化图纸；变化图号必须关联到经复核的工程量行，或提供有依据的“无数量影响”处置。

当图号重复、哈希缺失/无效、变化未映射、复核尚未完成或无数量影响缺少证据时，发布模式会阻断。

**边界：**此工具比较的是图纸登记字段和文件哈希，不执行视觉叠图、PDF 页面配准、CAD 实体语义差异识别，也不会自动重算工程量。它提示哪些地方应复核，不推断哈希变化必然导致数量变化。

### 3.2 技能结构与渐进式披露

主 `SKILL.md` 从 573 行级别的大型入口整理为 490 行；把硬性规则/停止条件、失败模式与行业对标细节移入 `references/` 按需加载。增加 `agents/openai.yaml`，让技能说明、入口元数据与按需资源更清楚。

### 3.3 测试与发布治理

- 发布烟测套件升级为 `QTO-RELEASE-SMOKE-V7`，43 项。
- 领域对抗回归增加至 15 项，覆盖修订闭环、未复核/未映射阻断、无效哈希、重复有效图号及技能元数据/长度限制。
- 合成项目 E2E 增加图纸修订影响步骤，共 20 项。
- 发布测试和端到端报告绑定当前源树指纹，防止把旧版测试日志当成当前源码的通过证据。

### 3.4 项目规则与证据治理

继续沿用 v4.2.0 的项目配置、计量规则、单位别名、标准化门禁契约和发布审批原则；未知/歧义单位不应靠全局猜测放行。测试 PASS 只说明指定夹具下的软件行为满足预期，不取代项目合同口径确认和专业签审。

## 4. 最终测试记录

| 测试项 | 结果 | 解释 |
|---|---:|---|
| 发布回归 | **43/43 PASS** | 套件 `QTO-RELEASE-SMOKE-V7` |
| 领域对抗回归 | **15/15 PASS** | 包括修订影响阻断/放行规则 |
| 合成项目端到端 | **20/20 PASS** | `SIM-E2E-001` 合成 Access Road & Box Culvert |
| 故障注入 | **10/10 被捕获** | 仅覆盖定义的注入场景，不代表现实错误检出率 |
| 端到端输入工作簿/控制文件哈希 | **前后不变** | `source_inputs_unchanged=True` |
| 端到端期间源树哈希 | **前后不变** | `source_tree_unchanged=True` |
| 主 `SKILL.md` 行数 | **490 行** | 小于 500 行，便于渐进式披露 |

当前发布源码指纹（SHA-256）：

`fd8d0e12ea3059db7e59ae1ef5ad58a91ff1d728928b3d25ee504b1719154a3c`

测试的权威详细记录见 `examples/e2e_demo/reports/E2E_TEST_REPORT.md`、`examples/e2e_demo/reports/e2e_summary.json` 和根目录发布烟测日志。测试输入为合成夹具，数量、规则、费率、审批和项目资料均为演示数据。

## 5. 下一阶段建议（需要真实项目数据或外部能力）

1. **真实黄金样本库：**选择经授权、脱敏且已专业签认的房建、道路、排水、桥涵及机电项目，按图纸版本、合同计量口径及人工复核记录建立基准答案。
2. **视觉差异与语义提量：**如需 PDF 叠图、CAD/BIM 语义差异及视觉识别，应与获授权的 PDF/CAD/BIM 软件或视觉服务集成，并建立精确率、召回率、漏检/误检成本评测；当前修订工具不应冒充这些能力。
3. **标准映射：**根据项目、国家、业主和合同配置 NRM/CESMM/POMI/当地规范等适用规则，登记来源、版本、适用范围、批准人和测试案例；处理版权时保留合法引用和规则摘要。
4. **跨分类映射：**对 BOQ、CBS、WBS、CSI 分类、业主编码和内部成本编码建立项目级映射并做覆盖率及冲突检查。
5. **模型与数据互操作：**为真实 IFC 模型增加 schema 版本、单位、分类属性、导出器和 GUID 追溯测试；不要把模型报量当成无条件真值。
6. **团队治理：**为每个技能维护 owner、维护人、适用范围、数据使用规则、回归集、变更日志、升级/回滚流程和人工放行责任。

## 6. 使用边界与验收声明

v4.3.0 完成的是有证据的技能结构优化、图纸台账变更影响工作流与合成测试。它不构成商业产品等价性声明，不保证自动识别所有图纸对象，不证明真实项目数量正确，也不替代合同/规范确认、QS 复核或正式投标审批。进入生产使用前必须绑定并批准真实项目规则、图纸基准和验收样本。
