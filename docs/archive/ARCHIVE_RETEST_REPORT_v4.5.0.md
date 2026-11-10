# v4.5.0 最终归档独立复测报告

日期：2026-10-29  
归档：`caogong-intl-qty_v4.5.0_optimized.zip`  
复测方式：将 ZIP 解压到独立目录，从解压副本运行测试，不引用工作目录中的 Python 源码。

## 复测结果

| 检查 | 结果 | 证据 |
|---|---:|---|
| ZIP 完整性 | PASS | `unzip -t` 无损坏条目 |
| 核心数值单元测试 | 13/13 | `scripts/test_qto_core.py` |
| 领域对抗回归 | 20/20 | `scripts/test_domain_regressions.py` |
| OpenBIM 专项断言 | 31/31 | `scripts/test_openbim_qto.py` |
| 发布 smoke/regression | 20/20 | `QTO-RELEASE-SMOKE-V10` |
| 合成项目 E2E | 21/21 | `run_e2e.py --skip-smoke` 使用归档内当前源码指纹绑定的 smoke 记录 |
| 故障注入 | 10/10 | 预定义缺陷均被预期门禁捕获 |
| 输入工作簿 / 控制文件 | 哈希不变 | `e2e_summary.json` 前后摘要相同 |
| 源树指纹 | 哈希不变 | E2E 开始与结束 SHA-256 一致 |

用于绑定本次测试证据的源树 SHA-256：

`a289898b2739ad4be40fa09e642f0a1f9e19dc8ae1a31456c39e3cfdd4e2f6f0`

## 复测范围与限制

测试使用包内合成项目和虚构数量、规则及映射。复测证明归档可解压、命令可执行、既定回归与流程断言按预期运行；不证明真实项目合同口径正确、真实 IFC 几何提量完整、正式 IDS 全语法兼容或专业 QS 已签审。正式使用仍需项目规则审批、真实项目黄金样本及授权人员复核。
