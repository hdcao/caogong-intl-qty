# 行业能力对标与差距路线图（v4.3.0）

> 本文依据截至 2026-10-29 可访问的公开产品/标准页面进行能力映射。它不是第三方性能排名，也不意味着对商业产品的内部技术或私有功能做了测试。只吸收可公开观察的工作流原则，不复制专有代码或未经许可的资料。

## 1. 对标结论

| 行业参考 | 公开可见的成熟做法 | 对本技能的启发 | 当前状态 / 下一步 |
|---|---|---|---|
| Autodesk Forma Construction Takeoff | 云端 2D/3D takeoff；线/面积/计数工具；可复用分类体系；使用项目文档的最新版本 | 统一 2D/3D 数量分类；把当前图纸集与批准基准分开；记录映射而非只报总量 | 已有 2D/模型数量交叉核验与项目清单；新增版本影响链。仍需真实模型映射与专业黄金样本 |
| Bluebeam Revu | 比例校准；长度、面积、计数；Markup/清单导出；批量修订比较与 Slip Sheet；叠图和 Smart Review 等 | 比例/图幅校验、修订差异、标记可追溯、结果导出是一个连续流程 | 已有 PDF/矢量诊断、双尺寸校准流程和图纸建册；新增台账快照差异。未实现像素级叠图或 CAD 语义比较 |
| Togal.AI | 图纸自动命名和组织；图像/文字/图形搜索；AI 检测、测量、计数、标注；协作 | 识别结果必须进入可审计标记与人工确认，跨图检索应与图号/图层上下文绑定 | 现有取证策略覆盖文本、矢量、栅格/OCR 路线及图例/标签防误计；AI 视觉检测仍依赖外部视觉模型/商业软件和标注样本 |
| RICS NRM | 不同用途的估算、成本计划和详细计量规则；支持模板，但提醒用户检查公式和逻辑 | 标准/版本/项目专用修订必须显式配置；模板校验不能代替专业批准 | 已有规则注册表、数量语义和发布门禁；需继续扩充经授权的 NRM/CESMM/POMI/项目规则映射与黄金案例，不能假定任意标准默认适用 |
| buildingSMART IFC | 开放、厂商中立的信息模型标准，支持可解释的数据交换；IFC 4.3 系列有正式标准发布 | 模型属性、实体关系、单位、分类和数量应结构化校验；模型不是自动正确的数量真相 | 已有 IFC 结构校验与 2D/模型差异分析；需增加正式 IFC schema/schema-version 识别和真实导出器兼容测试 |
| OpenAI / Agent Skills 公开指南 | `SKILL.md` 作为可复用入口；渐进式披露；按需读取 references/脚本；保持用途和触发条件清楚 | 主技能文件应是工作流入口，不应塞满所有专业细节；模块要可独立阅读、测试与迭代 | 本版将主 `SKILL.md` 收敛到 500 行以内，新增简洁元数据与专项参考文件 |

## 2. 一定要吸收的共性能力

### A. 版本与基准治理

- 对每个图纸包保留文件 SHA-256、图号、修订号、发布日期/收到日期、批准状态和 supersedes 关系。
- 通过基准与当前快照识别新增、缺失、哈希变化、修订标签变化和元数据变化。
- 将变化映射到工程量行、BOQ 编码、RFI 或“无数量影响”复核记录。
- 不把“文件 hash 变了”解释为“数量一定变化”，也不把“hash 没变”解释为“合同基准已批准”。

### B. 2D/3D 与分类连接

- 统一项目分类映射：原始图号/模型 GUID → 专业/工作包/部位 → 计量规则 → BOQ/CBS。
- 对模型数量保留 IFC schema/version、模型来源、导出日期、坐标/单位和属性来源。
- 对照结果要显式写明可比范围、不适用项、口径差异、独立性证据和未覆盖范围。
- 先识别实体、注释、图例和标签，再计量；AI 找到图形只是一条候选证据，不是自动确认数量。

### C. 规则驱动而非通用猜测

- 项目规则注册表必须记录标准/合同来源、版本、适用范围、生效日期、批准人和测试案例。
- 规则冲突时输出冲突清单，不由模型随意选择某一规范。
- 保持理论量、合同计量量、BOQ 投标量、材料需求量、采购量、现场申报量和认证量等语义分离。
- 计量规则和价格/组价规则拆开管理，避免把数量正确等同于报价正确。

### D. 以证据闭环而非单一分数做质量判断

- 每行数量要连接至来源文件、图号、位置、尺寸/实体、公式、单位、规则版本、责任人和复核证据。
- 识别置信度可用于排序复核优先级，但不允许用模型自评 confidence 直接替代专业审批。
- 重要项目应使用独立来源或独立抽样做复核；相同模型/相同参数重复计算不能被当作独立验证。
- 失败注入既要验证漏报，也要验证误报；每次缺陷都要转成回归案例。

### E. 产品化技能而不是巨型提示词

- `SKILL.md` 负责触发条件、关键门禁、工作流和资源路由；细节放 `references/`。
- 脚本只承担确定性计算、文件校验和可重复的数据变换；需语义理解的任务必须输出候选项及证据。
- 每项能力有最小输入契约、结构化输出、退出码、示例、反例和已知限制。
- 变更后重跑回归；测试证据绑定版本和源码指纹；发布物必须可解压复测。

## 3. 本版落地与未落地范围

### 已落地

- 主 `SKILL.md` 渐进式拆分，保留关键工作流及风险边界。
- 新增图纸台账快照比较与工程量影响审查工具，报告输入快照 hash 和变化处置状态。
- 对缺失/重复有效图号、无效/缺失 hash、未复核影响项实施阻断。
- 将修订影响检查接入模拟项目端到端和发布回归。
- 新增 OpenAI-compatible `agents/openai.yaml` 元数据。
- 增加行业能力映射与参考来源，区分已有、计划中和不具备的功能。

### 仍需要真实项目和外部工具

- 逐图页视觉差异/自动叠图、DWG 语义 diff、云端多用户冲突合并。
- 生产级 PDF 页面配准、矢量实体到构件对象的可靠自动识别。
- AI 自动检测模型的精确率/召回率评估、训练/标注数据治理及跨专业泛化评测。
- 经许可的真实合同、已签认数量、独立抽样和真实 IFC 模型黄金数据集。
- 标准许可/版权约束下的完整规则文本维护。标准条文应保存引用与规则摘要，不应未经许可打包复制受版权保护的全文。

## 4. 公开参考资料

1. OpenAI, **Build skills**（技能目录、简洁触发描述、渐进式披露、`SKILL.md` 与资源组织）：https://learn.chatgpt.com/docs/build-skills
2. Autodesk Forma, **Construction Takeoff**（二维/三维算量、分类系统与项目文档）：https://construction.autodesk.com/tools/construction-takeoff-software/
3. Bluebeam, **Revu product features**（测量、校准、Markup 清单、批量版本比较及审图功能）：https://www.bluebeam.com/pricing/
4. Togal.AI, **Features**（图纸管理、识别、搜索、计数、标注和协作）：https://www.togal.ai/features
5. RICS, **New Rules of Measurement**（估算/成本计划/详细计量的规则体系）：https://www.rics.org/profession-standards/rics-standards-and-guidance/sector-standards/construction-standards/nrm
6. buildingSMART International, **Industry Foundation Classes**（开放 IFC 标准及版本）：https://www.buildingsmart.org/standards/bsi-standards/industry-foundation-classes/
7. CSI, **Standards overview**（MasterFormat、UniFormat、OmniClass 分类体系）：https://www.csiresources.org/standards/overview


## v4.4.0 落地状态更新（2026-10-29）

本轮把可在本技能内完成并可用合成样例验证的四项能力写入代码：

- `scripts/ifc_qto_extract.py`：读取 STEP 中显式 `IfcElementQuantity` / 常见 `IfcQuantity*`，输出 GlobalId/数量集/单位来源/哈希。
- `scripts/boq_mapping.py`：强制项目 scope、规则审批元数据、精确选择器和恰好一次的映射/排除处置。
- `scripts/ifc_delivery_check.py`：执行声明式 IDS-inspired 交付 profile（并非官方 IDS 完整解析）。
- `scripts/ifc_qto_diff.py`：比较两份已提取 QTO 快照并在变更/不可比较时阻断自动放行。

合成验证脚本 `scripts/test_openbim_qto.py` 与 `examples/e2e_demo/run_openbim_demo.py` 覆盖主要正反例。仍未实现的行业软件能力包括：DWG/PDF 视觉叠图、CV/OCR 自动构件识别、IFC 几何重新计算、完整 IDS 语法/BCF 交换、钢筋节点下料和 5D/ERP 集成。这些需要额外几何/视觉依赖、数据规范或专业系统接口，不得描述为本版已具备。
