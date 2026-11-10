# Synthetic Project End-to-End Test Report

- Skill package version: `4.6.1`
- Project fixture: `SIM-E2E-001 — Synthetic Access Road & Box Culvert`
- Run timestamp (UTC): `2026-11-02T07:07:50+00:00`
- Overall software-test status: **PASS**
- Steps: **21/21 PASS**
- Input workbook/control hashes unchanged: **YES**
- Source tree SHA-256: `a8a8d21f5b2459dc7d3160eee73f088d5c11083128815d043501e12753cf7a58`
- Source tree remained unchanged during E2E: **YES**

## Checks executed

| # | Check | Runner | Gate | Exit | Contract | Log |
|---:|---|---|---|---:|---|---|
| 1 | Environment preflight | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_environment_preflight.json` | `examples/e2e_demo/reports/gate_logs/environment.json` |
| 2 | Compile and release smoke/regression suite | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_compile_and_release_smoke_regression_suite.json` | `examples/e2e_demo/reports/gate_logs/test_all.log` |
| 3 | Project manifest / measurement-rule / unit-alias preflight | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_project_manifest_measurement_rule_unit_alias_preflight.json` | `examples/e2e_demo/reports/gate_logs/project_config_validation.log` |
| 4 | Build drawing/input register | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_build_drawing_input_register.json` | `examples/e2e_demo/reports/gate_logs/input_register.log` |
| 5 | Drawing revision comparison and evidence-backed quantity impact closure | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_drawing_revision_comparison_and_evidence_backed_quantity_impact_closure.json` | `examples/e2e_demo/reports/gate_logs/drawing_revision_diff.log` |
| 6 | Structured scope declaration | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_structured_scope_declaration.json` | `examples/e2e_demo/reports/gate_logs/scope_declaration.log` |
| 7 | Drawing quantity baseline | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_drawing_quantity_baseline.json` | `examples/e2e_demo/reports/gate_logs/drawing_basis.log` |
| 8 | Baseline freshness (mtime intentionally skipped for reproducible archive tests) | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_baseline_freshness_mtime_intentionally_skipped_for_reproducible_archive_tests.json` | `examples/e2e_demo/reports/gate_logs/basis_freshness.log` |
| 9 | Base/optional separation | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_base_optional_separation.json` | `examples/e2e_demo/reports/gate_logs/scope_options.log` |
| 10 | Single physical object / single measurement owner | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_single_physical_object_single_measurement_owner.json` | `examples/e2e_demo/reports/gate_logs/single_owner.log` |
| 11 | Unit/rate outlier check with explicit synthetic ACK and project unit registry | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_unit_rate_outlier_check_with_explicit_synthetic_ack_and_project_unit_registry.json` | `examples/e2e_demo/reports/gate_logs/unit_consistency.log` |
| 12 | Dimension-wording consistency | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_dimension_wording_consistency.json` | `examples/e2e_demo/reports/gate_logs/dimension_wording.log` |
| 13 | Per-item expected quantity closure | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_per_item_expected_quantity_closure.json` | `examples/e2e_demo/reports/gate_logs/expected_total.log` |
| 14 | Spreadsheet formula and subtotal integrity | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_spreadsheet_formula_and_subtotal_integrity.json` | `examples/e2e_demo/reports/gate_logs/sheet_formula.log` |
| 15 | Cross-sheet value reconciliation (hardcoded values explicitly allowed) | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_cross_sheet_value_reconciliation_hardcoded_values_explicitly_allowed.json` | `examples/e2e_demo/reports/gate_logs/cross_sheet.log` |
| 16 | BOQ schema/content lint | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_boq_schema_content_lint.json` | `examples/e2e_demo/reports/gate_logs/boq_lint.log` |
| 17 | Model-vs-2D quantity comparison | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_model_vs_2d_quantity_comparison.json` | `examples/e2e_demo/reports/gate_logs/model_2d.log` |
| 18 | OpenBIM IFC QTO extraction / governed BOQ mapping / delivery profile / snapshot diff | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_openbim_ifc_qto_extraction_governed_boq_mapping_delivery_profile_snapshot_diff.json` | `examples/e2e_demo/reports/gate_logs/openbim_workflow.log` |
| 19 | PDF vector/text layer inventory | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_pdf_vector_text_layer_inventory.json` | `examples/e2e_demo/reports/gate_logs/pdf_vector.log` |
| 20 | PDF vector forensic probe | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_pdf_vector_forensic_probe.json` | `examples/e2e_demo/reports/gate_logs/vector_forensics.log` |
| 21 | Fault injection: ten injected defects must be caught; source files must remain unchanged | **PASS** | **PASS** | 0 | `examples/e2e_demo/reports/gate_contracts/e2e_fault_injection_ten_injected_defects_must_be_caught_source_files_must_remain_unc.json` | `examples/e2e_demo/reports/gate_logs/fault_injection.log` |

## Machine-readable gate contracts

The run wrote 21 standard gate contract artifacts under `examples/e2e_demo/reports/gate_contracts/`. Native checked/expected row counts are retained when available; for legacy scripts that do not expose them, fields remain null instead of inventing coverage. Runner PASS indicates the process completed and no fatal output markers were detected; the separate Gate status retains WARN/FAIL/ERROR findings for review.

## Engineering quantity summary (fixture data only)

| Scope | Quantity / value | Basis / interpretation |
|---|---:|---|
| Base direct-cost control | 23,859 SIM-CUR | Synthetic rate × quantity; excludes optional guardrail |
| Optional guardrail | 17,000 SIM-CUR | Separate CPB 4.0 alternative; must not enter Base |
| Total including optional | 40,859 SIM-CUR | For comparison only; not the Base tender amount |
| Box culvert concrete | 10.8 m³ | 3.6 m³ base + 3.6 m³ walls + 3.6 m³ top slab |
| Roadway excavation | 1,200 m³ | 12.0 m² average area × 100 m, synthetic geometry |
| Granular subbase | 160 m³ | 8.0 m × 100 m × 0.20 m, synthetic geometry |
| Asphalt concrete | 35 m³ | 7.0 m × 100 m × 0.05 m, synthetic geometry |
| Guardrail | 200 m | 2 sides × 100 m; optional |

## What the fault-injection test proves

The harness successfully injected 10 defined defects into temporary copies, reported `CAUGHT` for all 10, and confirmed the source workbook digest was unchanged. This only supports the stated injection set and the test fixture; it is **not** a claim of 100% real-world defect-detection coverage.

## Important limitations

- All drawings, quantities, rules, rates, codes, reviewer names and ACK records are invented test fixtures.
- The 2D/model inputs are separate fixture files but their real-world source independence is not authenticated.
- Cross-sheet comparison used --allow-hardcoded; it validates values, not formula dependency.
- Baseline freshness test skipped file mtime because archive/copy timestamps are not reliable evidence.
- PDF vector probe is diagnostic only; no claim that vector entities were automatically converted into quantities.
- Drawing revision workflow compares register metadata and file hashes; it does not perform geometric overlay or recalculate affected quantities.
- The test does not establish contract compliance, code compliance, market rates, authorized review, or payment certification.

## Release decision

This demo is a reproducible software E2E pass on synthetic data. It is not a professional QS sign-off, not proof that the source schedule is contractually correct, and not production acceptance. Before live use: bind the actual contract and measurement rules, reconcile actual drawing revisions, approve rule registry and evidence, review exceptions, establish a known-good project golden dataset, and obtain authorized professional release.
