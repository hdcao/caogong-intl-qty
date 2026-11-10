# OpenBIM QTO 合成工作流报告

- 状态：**PASS**
- 子命令：7/7 expected-exit checks passed
- 断言：5/5 passed
- 输入哈希未变化：**True**

## Checks

- PASS `delivery_profile`：expected `PASS` / actual `PASS`
- PASS `boq_mapping`：expected `PASS` / actual `PASS`
- PASS `changed_diff`：expected `REVIEW_REQUIRED` / actual `REVIEW_REQUIRED`
- PASS `unchanged_diff`：expected `NO_CHANGE` / actual `NO_CHANGE`
- PASS `integrated_pipeline_review`：expected `REVIEW_REQUIRED` / actual `REVIEW_REQUIRED`

## 限制

Synthetic IFC STEP quantities only. No geometry recomputation, PDF/CAD visual diff, full IDS parser or contract measurement approval is implied. Integrated pipeline keeps changed QTO at REVIEW_REQUIRED and never auto-approves it.
