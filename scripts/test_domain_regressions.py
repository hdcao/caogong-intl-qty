#!/usr/bin/env python3
"""Adversarial regression tests for domain-level numeric and input-integrity guards."""
import contextlib, importlib.util, io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"

class DomainRegressionTests(unittest.TestCase):
    def run_cmd(self, *args):
        return subprocess.run([sys.executable, *map(str, args)], cwd=str(ROOT),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", timeout=60)

    def test_vector_chain_rejects_nan(self):
        p = self.run_cmd(SCRIPTS / "vector_forensics.py", "chain", "--total", "nan", "--parts", "1")
        self.assertEqual(p.returncode, 2, p.stdout)
        self.assertIn("数值参数无效", p.stdout)

    def test_vector_chain_rejects_zero_intervals(self):
        p = self.run_cmd(SCRIPTS / "vector_forensics.py", "chain", "--total", "10", "--equal", "0")
        self.assertEqual(p.returncode, 2, p.stdout)

    def test_xcheck_rejects_duplicate_names_instead_of_overwriting(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "model.json", Path(d) / "2d.json"
            rows = [{"name":"Beam A", "value":1, "unit":"m3", "param_source":"sheet A"},
                    {"name":"Beam A", "value":2, "unit":"m3", "param_source":"sheet B"}]
            a.write_text(json.dumps(rows), encoding="utf-8")
            b.write_text(json.dumps([{"name":"Beam A", "value":1, "unit":"m3", "param_source":"sheet C"}]), encoding="utf-8")
            p = self.run_cmd(SCRIPTS / "xcheck_model_vs_2d.py", a, b)
            self.assertEqual(p.returncode, 2, p.stdout)
            self.assertIn("name 重复", p.stdout)

    def test_xcheck_rejects_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "model.json", Path(d) / "2d.json"
            a.write_text('[{"name":"Beam A","value":NaN,"unit":"m3","param_source":"sheet A"}]', encoding="utf-8")
            b.write_text('[{"name":"Beam A","value":1,"unit":"m3","param_source":"sheet B"}]', encoding="utf-8")
            p = self.run_cmd(SCRIPTS / "xcheck_model_vs_2d.py", a, b)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("G-NUM", p.stdout)
    def test_skip_smoke_rejects_stale_or_incomplete_release_evidence(self):
        runner_path = ROOT / "examples" / "e2e_demo" / "run_e2e.py"
        spec = importlib.util.spec_from_file_location("qto_e2e_runner_contract_test", runner_path)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        digest = module.release_source_fingerprint(ROOT)
        current = (f"SOURCE TREE SHA256: {digest}\n"
                   "SOURCE TREE UNCHANGED: True\n"
                   f"RELEASE SMOKE RESULT: {module.SMOKE_EXPECTED_CHECKS}/{module.SMOKE_EXPECTED_CHECKS} passed (suite={module.SMOKE_SUITE_ID})\n"
                   f"SOURCE TREE SHA256: {digest}\n"
                   "PASS: smoke/regression suite passed.")
        # v4.6.4：原断言 `assertTrue(module.smoke_evidence_valid(current, digest))` 用
        # {module.SMOKE_EXPECTED_CHECKS}/{module.SMOKE_EXPECTED_CHECKS} **自己构造日志**，
        # 再喂给同一模块的函数校验 → 常量取任何值（24/23/7/999）都恒真，永远发现不了漂移，
        # 制造"这条证据链有测试保护"的错觉。故删除该恒真断言，改判下面两件事：
        #   ① `smoke_evidence_valid` 对**格式正确**的日志仍返回 True（函数契约，正向基线）；
        #   ② `run_e2e` 实际生效的常量与 `test_all` 导出的同名常量**同源同值**（真实对账）。
        # 真正有效的是 ②：它比的是"两个模块里的同一事实"，而不是"函数与它自己"。
        # 下面 6 条 assertFalse 检验的是拒绝路径（缺指纹/缺 PASS 行/指纹不符/UNCHANGED=False/
        # 含 [FAIL]/旧套件号），全部保留，不得削弱。
        self.assertTrue(module.smoke_evidence_valid(current, digest),
                        "格式完整的当前日志应被接受（函数契约基线）")
        sys.path.insert(0, str(SCRIPTS))
        from test_all import SMOKE_EXPECTED_CHECKS as _TA_EXPECTED  # noqa: E402
        self.assertEqual(module.SMOKE_EXPECTED_CHECKS, _TA_EXPECTED,
                         "run_e2e 与 test_all 的 SMOKE_EXPECTED_CHECKS 必须同源同值")
        self.assertFalse(module.smoke_evidence_valid(current, "0" * 64))
        self.assertFalse(module.smoke_evidence_valid(current.replace("PASS: smoke/regression suite passed.", ""), digest))
        self.assertFalse(module.smoke_evidence_valid(current.replace(f"SOURCE TREE SHA256: {digest}\n", "", 1), digest))
        self.assertFalse(module.smoke_evidence_valid(current.replace(f"SOURCE TREE SHA256: {digest}\n", f"SOURCE TREE SHA256: {'0' * 64}\n", 1), digest))
        self.assertFalse(module.smoke_evidence_valid(current.replace("SOURCE TREE UNCHANGED: True", "SOURCE TREE UNCHANGED: False"), digest))
        self.assertFalse(module.smoke_evidence_valid(current + "\n[FAIL] an injected failure\n", digest))
        self.assertFalse(module.smoke_evidence_valid("RELEASE SMOKE RESULT: 40/40 passed (suite=QTO-RELEASE-SMOKE-V4)", digest))

    def test_drawing_revision_diff_closes_reviewed_change_impact(self):
        baseline = ROOT / "examples/e2e_demo/reference/revisions/baseline.csv"
        current = ROOT / "examples/e2e_demo/reference/revisions/current.csv"
        impact = ROOT / "examples/e2e_demo/reference/revisions/impact_map.csv"
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            p = self.run_cmd(SCRIPTS / "drawing_revision_diff.py", "compare",
                             "--baseline", baseline, "--current", current,
                             "--impact-map", impact, "--project-id", "SIM-E2E-001",
                             "--out-dir", out, "--fail-on-unreviewed")
            self.assertEqual(p.returncode, 0, p.stdout)
            report = json.loads((out / "drawing_revision_impact.json").read_text(encoding="utf-8"))
            self.assertTrue(report["release_review"]["eligible"])
            kinds = {row["sheet"]: row["change_type"] for row in report["changes"]}
            self.assertEqual(kinds["RD-001"], "REVISION_AND_CONTENT_CHANGED")
            self.assertEqual(kinds["RD-004"], "ADDED")
            self.assertEqual(kinds["RD-002"], "UNCHANGED")
            try:
                import jsonschema
                schema = json.loads((ROOT / "assets/schemas/drawing-revision-impact.schema.json").read_text(encoding="utf-8"))
                jsonschema.validate(report, schema)
            except ImportError:
                pass

    def test_drawing_revision_diff_blocks_pending_or_unmapped_impact(self):
        baseline = ROOT / "examples/e2e_demo/reference/revisions/baseline.csv"
        current = ROOT / "examples/e2e_demo/reference/revisions/current.csv"
        original_map = ROOT / "examples/e2e_demo/reference/revisions/impact_map.csv"
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            text = original_map.read_text(encoding="utf-8").replace("RECALCULATED,Synthetic QS", "PENDING,Synthetic QS")
            # Remove the no-impact disposition for a new sheet: missing mapping must block.
            text = "\n".join(line for line in text.splitlines() if not line.startswith("RD-004,")) + "\n"
            impact = tmp / "impact_map.csv"
            impact.write_text(text, encoding="utf-8")
            out = tmp / "out"
            p = self.run_cmd(SCRIPTS / "drawing_revision_diff.py", "compare",
                             "--baseline", baseline, "--current", current,
                             "--impact-map", impact, "--project-id", "SIM-E2E-001",
                             "--out-dir", out, "--fail-on-unreviewed")
            self.assertEqual(p.returncode, 1, p.stdout)
            report = json.loads((out / "drawing_revision_impact.json").read_text(encoding="utf-8"))
            self.assertFalse(report["release_review"]["eligible"])
            self.assertGreaterEqual(len(report["release_review"]["blockers"]), 2)

    def test_drawing_revision_diff_missing_hash_is_ambiguous_not_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            baseline = tmp / "baseline.csv"
            current = tmp / "current.csv"
            headers = "sheet,rev,sha256,status,title\n"
            baseline.write_text(headers + "S-1,A,,confirmed,Drawing\n", encoding="utf-8")
            current.write_text(headers + "S-1,A,,confirmed,Drawing\n", encoding="utf-8")
            impact = tmp / "impact.csv"
            impact.write_text("sheet,quantity_id,item,review_status,reviewer,reviewed_at,evidence_ref,rationale\nS-1,Q-1,Item,RECALCULATED,QS,2026-10-28,Review-1,Rechecked\n", encoding="utf-8")
            out = tmp / "out"
            p = self.run_cmd(SCRIPTS / "drawing_revision_diff.py", "compare",
                             "--baseline", baseline, "--current", current,
                             "--impact-map", impact, "--out-dir", out, "--fail-on-unreviewed")
            self.assertEqual(p.returncode, 1, p.stdout)
            report = json.loads((out / "drawing_revision_impact.json").read_text(encoding="utf-8"))
            self.assertEqual(report["changes"][0]["change_type"], "AMBIGUOUS_MISSING_OR_INVALID_HASH")
            self.assertFalse(report["release_review"]["eligible"])


    def test_drawing_revision_diff_blocks_added_drawing_with_missing_hash_even_if_impact_closed(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            baseline = tmp / "baseline.csv"
            current = tmp / "current.csv"
            headers = "sheet,rev,sha256,status,title\n"
            baseline.write_text(headers, encoding="utf-8")
            current.write_text(headers + "S-NEW,A,,confirmed,New drawing\n", encoding="utf-8")
            impact = tmp / "impact.csv"
            impact.write_text(
                "sheet,quantity_id,item,review_status,reviewer,reviewed_at,evidence_ref,rationale\n"
                "S-NEW,,New drawing,NO_QUANTITY_IMPACT,Authorized QS,2026-10-29,REV-01,Reviewed against scope\n",
                encoding="utf-8")
            out = tmp / "out"
            p = self.run_cmd(SCRIPTS / "drawing_revision_diff.py", "compare",
                             "--baseline", baseline, "--current", current,
                             "--impact-map", impact, "--out-dir", out, "--fail-on-unreviewed")
            self.assertEqual(p.returncode, 1, p.stdout)
            report = json.loads((out / "drawing_revision_impact.json").read_text(encoding="utf-8"))
            self.assertEqual(report["changes"][0]["change_type"], "AMBIGUOUS_MISSING_OR_INVALID_HASH")
            self.assertFalse(report["release_review"]["eligible"])
            self.assertTrue(any("SHA-256" in item for item in report["release_review"]["blockers"]))

    def test_xcheck_converts_compatible_volume_units_before_comparison(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            model, two_d = tmp / "model.json", tmp / "2d.json"
            model.write_text(json.dumps([{
                "name": "Concrete", "value": 1.0, "unit": "m3",
                "kind": "quantity", "param_source": "Model geometry"
            }]), encoding="utf-8")
            two_d.write_text(json.dumps([{
                "name": "Concrete", "value": 1000.0, "unit": "L",
                "kind": "quantity", "param_source": "Drawing measurement"
            }]), encoding="utf-8")
            report = tmp / "report.md"
            p = self.run_cmd(SCRIPTS / "xcheck_model_vs_2d.py", model, two_d, "--report", report)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertIn("EXACT 1", p.stdout)
            self.assertIn("NOT_COMPARABLE 0", p.stdout)
            self.assertNotIn("两侧存在真实差异", p.stdout)
            self.assertIn("G-X0 未发现", p.stdout)
            self.assertIn("单位必须经注册表解析并转换到同一单位后比较", report.read_text(encoding="utf-8"))


    def test_xcheck_uses_approved_project_alias_registry(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            model, two_d, registry = tmp / "model.json", tmp / "2d.json", tmp / "units.json"
            model.write_text(json.dumps([{
                "name": "Concrete", "value": 1.0, "unit": "CM",
                "kind": "quantity", "param_source": "Model geometry"
            }]), encoding="utf-8")
            two_d.write_text(json.dumps([{
                "name": "Concrete", "value": 1.0, "unit": "m3",
                "kind": "quantity", "param_source": "Drawing measurement"
            }]), encoding="utf-8")
            registry.write_text(json.dumps({
                "schema_version": "1.0.0", "project_id": "P-1",
                "context": {"jurisdiction": "Contract-defined test", "language": "en"},
                "aliases": [{
                    "alias": "CM", "canonical_unit": "m3", "dimension": "volume",
                    "approval_status": "APPROVED", "approved_by": "Authorized QS",
                    "approved_at": "2026-10-29", "source_document": "Synthetic Contract Appendix",
                    "source_clause": "BOQ preamble 1.2", "applicability": {"project_id": "P-1"}
                }]
            }), encoding="utf-8")
            p = self.run_cmd(SCRIPTS / "xcheck_model_vs_2d.py", model, two_d,
                             "--unit-registry", registry, "--project-id", "P-1")
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertIn("EXACT 1", p.stdout)
            self.assertIn("NOT_COMPARABLE 0", p.stdout)

    def test_xcheck_blocks_incompatible_units_as_not_comparable(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            model, two_d = tmp / "model.json", tmp / "2d.json"
            model.write_text(json.dumps([{
                "name": "Concrete", "value": 1.0, "unit": "m3",
                "kind": "quantity", "param_source": "Model geometry"
            }]), encoding="utf-8")
            two_d.write_text(json.dumps([{
                "name": "Concrete", "value": 1000.0, "unit": "kg",
                "kind": "quantity", "param_source": "Drawing measurement"
            }]), encoding="utf-8")
            p = self.run_cmd(SCRIPTS / "xcheck_model_vs_2d.py", model, two_d)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("NOT_COMPARABLE", p.stdout)
            self.assertIn("dimension mismatch", p.stdout)

    def test_xcheck_blocks_ambiguous_unit_without_project_approval(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            model, two_d = tmp / "model.json", tmp / "2d.json"
            rows_m = [{"name":"Concrete", "value":1.0, "unit":"CM", "kind":"quantity", "param_source":"Model geometry"}]
            rows_t = [{"name":"Concrete", "value":1.0, "unit":"CM", "kind":"quantity", "param_source":"Drawing measurement"}]
            model.write_text(json.dumps(rows_m), encoding="utf-8")
            two_d.write_text(json.dumps(rows_t), encoding="utf-8")
            p = self.run_cmd(SCRIPTS / "xcheck_model_vs_2d.py", model, two_d)
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("NOT_COMPARABLE", p.stdout)
            self.assertIn("ambiguous unit", p.stdout)

    def test_drawing_revision_diff_duplicate_active_sheet_rows_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            baseline = tmp / "baseline.csv"
            current = tmp / "current.csv"
            headers = "sheet,rev,sha256,status,title\n"
            baseline.write_text(headers +
                                "S-1,A," + "a" * 64 + ",confirmed,Drawing\n" +
                                "S-1,B," + "b" * 64 + ",confirmed,Drawing\n", encoding="utf-8")
            current.write_text(headers + "S-1,B," + "b" * 64 + ",confirmed,Drawing\n", encoding="utf-8")
            impact = tmp / "impact.csv"
            impact.write_text("sheet,quantity_id,item,review_status,reviewer,reviewed_at,evidence_ref,rationale\nS-1,Q-1,Item,RECALCULATED,QS,2026-10-28,Review-1,Rechecked\n", encoding="utf-8")
            out = tmp / "out"
            p = self.run_cmd(SCRIPTS / "drawing_revision_diff.py", "compare",
                             "--baseline", baseline, "--current", current,
                             "--impact-map", impact, "--out-dir", out, "--fail-on-unreviewed")
            self.assertEqual(p.returncode, 1, p.stdout)
            report = json.loads((out / "drawing_revision_impact.json").read_text(encoding="utf-8"))
            self.assertEqual(report["changes"][0]["change_type"], "AMBIGUOUS_DUPLICATE_ACTIVE_ROWS")
            self.assertFalse(report["release_review"]["eligible"])

    def test_agent_skill_metadata_and_compact_manifest_are_present(self):
        manifest = (ROOT / "agents/openai.yaml").read_text(encoding="utf-8")
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("display_name:", manifest)
        self.assertIn("short_description:", manifest)
        self.assertIn("default_prompt:", manifest)
        self.assertIn("allow_implicit_invocation: true", manifest)
        self.assertLessEqual(len(skill.splitlines()), 500)
        # ★不写死版本号：从 VERSION 读，断的是"front-matter 与包版本一致"。
        #   旧写法 assertIn("version: 4.5.0") 让每次升版都必须同步改测试，
        #   漏改就报一个与被测行为无关的错（v4.5.1 撞到过）。
        pkg_version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertIn("version: %s" % pkg_version, skill)

    def test_unit_gate_does_not_assume_cm_is_volume(self):
        text = (SCRIPTS / "gate_unit_consistency.py").read_text(encoding="utf-8")
        self.assertNotIn("VOL_UNITS = {'CM'", text)
        core = importlib.util.spec_from_file_location("qto_core_gate_unit_test", SCRIPTS / "qto_core.py")
        self.assertIsNotNone(core)
        module = importlib.util.module_from_spec(core)
        sys.modules[core.name] = module
        core.loader.exec_module(module)
        self.assertNotIn("cm", module.unit_dimensions())


    def test_current_json_schemas_and_project_fixtures(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is an optional authoring-time dependency; runtime validator is dependency-free")
        schemas = ROOT / "assets" / "schemas"
        for path in sorted(schemas.glob("*.schema.json")):
            schema = json.loads(path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator.check_schema(schema)
        fixtures = [
            ("project-manifest.schema.json", ROOT / "examples/e2e_demo/reference/project_manifest.json"),
            ("measurement-rule-registry.schema.json", ROOT / "examples/e2e_demo/reference/measurement_rule_registry.json"),
            ("unit-alias-registry.schema.json", ROOT / "examples/e2e_demo/reference/unit_alias_registry.json"),
            ("boq-mapping-registry.schema.json", ROOT / "examples/e2e_demo/reference/openbim/boq_mapping_registry.json"),
            ("ifc-delivery-profile.schema.json", ROOT / "examples/e2e_demo/reference/openbim/ifc_delivery_profile.json"),
        ]
        for schema_name, fixture_path in fixtures:
            schema = json.loads((schemas / schema_name).read_text(encoding="utf-8"))
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            jsonschema.Draft202012Validator(schema).validate(fixture)

    def test_project_config_release_mode_fails_closed_on_unresolved_control_placeholders(self):
        import shutil
        sys.path.insert(0, str(SCRIPTS))
        import qto_project_validate
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            shutil.copytree(ROOT / "examples/e2e_demo/reference", tmp / "reference")
            shutil.copytree(ROOT / "examples/e2e_demo/takeoff/register/scan", tmp / "takeoff/register/scan")
            shutil.copy2(ROOT / "examples/e2e_demo/takeoff/register/scope.csv", tmp / "takeoff/register/scope.csv")
            alias_path = tmp / "reference/unit_alias_registry.json"
            alias_doc = json.loads(alias_path.read_text(encoding="utf-8"))
            alias_doc["context"]["jurisdiction"] = "TO_BE_CONFIRMED"
            alias_path.write_text(json.dumps(alias_doc, ensure_ascii=False), encoding="utf-8")
            result = qto_project_validate.validate(
                tmp / "reference/project_manifest.json", tmp, ROOT,
                release=True, as_of=__import__("datetime").date(2026, 10, 27))
            codes = {issue.get("code") for issue in result.get("issues", [])}
            self.assertIn("UNRESOLVED_CONFIG_PLACEHOLDERS", codes)
            self.assertFalse(result.get("metrics", {}).get("release_eligible"))

    def test_g_unit_requires_project_approval_for_ambiguous_cm(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl is required by the package runtime")
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            book = tmp / "quantity.xlsx"
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "1_成本取量总表"
            ws.cell(3, 3, "Test concrete")
            ws.cell(3, 4, "CM")
            ws.cell(3, 5, 12.0)
            ws.cell(3, 7, 100.0)
            wb.save(book)
            no_registry = self.run_cmd(SCRIPTS / "gate_unit_consistency.py", book, "--sheet", "1_成本取量总表")
            self.assertEqual(no_registry.returncode, 2, no_registry.stdout)
            self.assertIn("未发现体积项", no_registry.stdout)
            registry = tmp / "unit_alias_registry.json"
            registry.write_text(json.dumps({
                "schema_version": "1.0.0", "project_id": "P-1",
                "context": {"jurisdiction": "Contract-defined test", "language": "en"},
                "aliases": [{
                    "alias": "CM", "canonical_unit": "m3", "dimension": "volume",
                    "approval_status": "APPROVED", "approved_by": "Authorized QS",
                    "approved_at": "2026-10-27", "source_document": "Synthetic Contract Appendix",
                    "source_clause": "BOQ preamble 1.2", "applicability": {"project_id": "P-1"}
                }]
            }), encoding="utf-8")
            approved = self.run_cmd(SCRIPTS / "gate_unit_consistency.py", book, "--sheet", "1_成本取量总表",
                                   "--unit-registry", registry, "--project-id", "P-1")
            self.assertEqual(approved.returncode, 0, approved.stdout)
            self.assertIn("体积类行 1 条", approved.stdout)
            wrong_project = self.run_cmd(SCRIPTS / "gate_unit_consistency.py", book, "--sheet", "1_成本取量总表",
                                         "--unit-registry", registry, "--project-id", "P-2")
            self.assertEqual(wrong_project.returncode, 2, wrong_project.stdout)

    def test_catalogue_entry_missing_from_document_is_blocking(self):
        # Temporarily replace the verifier's inputs with a tiny controlled fixture.
        import drafter_qto
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d:
            doc = Path(d) / "catalog.md"
            doc.write_text("Only A-1-1 is documented.\n", encoding="utf-8")
            old_catalogs = drafter_qto.CATALOGS
            old_docs = drafter_qto.DOC_OF_CATALOG
            old_patterns = drafter_qto.CATALOG_ID_RE
            try:
                drafter_qto.CATALOGS = {"road-bridge": [("A-1-1", "documented", ""), ("A-1-2", "missing doc", "")]}
                drafter_qto.DOC_OF_CATALOG = {"road-bridge": str(doc)}
                drafter_qto.CATALOG_ID_RE = {"road-bridge": __import__("re").compile(r"\b([A-FX]-\d+-\d+[a-z]?)\b")}
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    rc = drafter_qto.cmd_verify(SimpleNamespace())
                self.assertEqual(rc, 1)
                self.assertIn("[FAIL] 清单里有 1 项文档未展开说明", stream.getvalue())
            finally:
                drafter_qto.CATALOGS = old_catalogs
                drafter_qto.DOC_OF_CATALOG = old_docs
                drafter_qto.CATALOG_ID_RE = old_patterns

if __name__ == "__main__":
    unittest.main(verbosity=2)
