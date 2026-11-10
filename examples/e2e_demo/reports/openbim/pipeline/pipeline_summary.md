# OpenBIM QTO 一键工作流报告

- 项目：`SIM-E2E-OPENBIM-001`
- 综合状态：**REVIEW_REQUIRED**
- 可发布：**False**
- 输入文件执行前后哈希一致：**True**

## 阶段结果

- **PASS** `extract_baseline` — `baseline_qto.json`
- **PASS** `extract_current` — `current_qto.json`
- **PASS** `delivery_profile` — `delivery_check.json`
- **PASS** `boq_mapping` — `boq_mapping.json`
- **REVIEW_REQUIRED** `snapshot_diff` — `snapshot_diff.json`

## 范围与限制

IFC STEP explicit quantity pipeline only. It does not infer quantities from geometry, does not implement the full official IDS grammar, does not perform PDF/CAD visual diff, and never auto-approves model changes. Any quantity change requires QS/contract review.
