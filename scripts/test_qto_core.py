#!/usr/bin/env python3
"""Dependency-free regression tests for qto_core; run: python scripts/test_qto_core.py"""
from decimal import Decimal
import csv, json, os, random, tempfile, unittest
from pathlib import Path
from qto_core import (DataValidationError, GateResult, Issue, Status, atomic_write_csv, atomic_write_json,
                      convert, load_unit_alias_registry, multiply, parse_decimal, parse_nonnegative,
                      safe_relative_path, sum_quantities, within_tolerance)

class CoreTests(unittest.TestCase):
    def test_decimal_no_binary_drift(self):
        self.assertEqual(sum_quantities(["0.1", "0.2"]), Decimal("0.3"))
        self.assertEqual(multiply(["0.1", "0.2"]), Decimal("0.02"))
    def test_reject_nonfinite_bool_and_ambiguous_locale(self):
        for x in (float("nan"), float("inf"), True, "1,234.56"):
            with self.assertRaises(DataValidationError): parse_decimal(x)
        self.assertEqual(parse_decimal("1,25", decimal_comma=True), Decimal("1.25"))
    def test_unit_conversion_and_dimension_rejection(self):
        self.assertEqual(convert("1250", "mm", "m"), Decimal("1.250"))
        self.assertEqual(convert("2", "m3", "mc"), Decimal("2"))
        self.assertEqual(convert("10", "ft", "m"), Decimal("3.048"))
        self.assertEqual(convert("100", "sqft", "m2"), Decimal("9.290304"))
        self.assertEqual(convert("1", "cuyd", "m3"), Decimal("0.764554857984"))
        self.assertEqual(convert("1", "h", "min"), Decimal("60"))
        with self.assertRaises(DataValidationError): convert(1, "m", "m3")
        with self.assertRaises(DataValidationError): convert(1, "widgets", "ea")
        with self.assertRaises(DataValidationError): convert(1, "CM", "m3")
    def test_tolerance(self):
        self.assertTrue(within_tolerance("100.01", "100", abs_tol="0.02"))
        self.assertFalse(within_tolerance("100.03", "100", abs_tol="0.02"))
    def test_gate_states_fail_closed_when_execution_is_not_confirmed(self):
        result = GateResult("TEST")
        self.assertEqual(result.status, Status.NOT_RUN)
        self.assertEqual(result.finalize().status, Status.NOT_RUN)
        self.assertEqual(result.finalize(executed=True).status, Status.PASS)
        result = GateResult("TEST"); result.add(Issue("X", "FAIL", "bad"))
        self.assertEqual(result.finalize(executed=True).status, Status.FAIL)
        self.assertEqual(result.finalize(executed=False).status, Status.FAIL,
                         "finalize must not erase a previously detected failure")
        self.assertEqual(GateResult("TEST").finalize(executed=False).status, Status.NOT_RUN)
    def test_csv_formula_safety_covers_whitespace_prefix_but_preserves_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "export.csv")
            rows = [
                {"text": "=1+1", "quantity": "-1.25"},
                {"text": "  =HYPERLINK(\"https://example.invalid\")", "quantity": "2"},
                {"text": "\t@SUM(A1:A2)", "quantity": "3"},
                {"text": "\u00a0=HYPERLINK(\"https://example.invalid\")", "quantity": "4"},
                {"text": "ordinary item", "quantity": "5"},
            ]
            atomic_write_csv(path, ["text", "quantity"], rows, excel_safe=True)
            with open(path, encoding="utf-8-sig", newline="") as stream:
                got = list(csv.DictReader(stream))
            self.assertTrue(got[0]["text"].startswith("'=1+1"))
            self.assertTrue(got[1]["text"].startswith("'  =HYPERLINK"))
            self.assertTrue(got[2]["text"].startswith("'\t@SUM"))
            self.assertTrue(got[3]["text"].startswith("'\u00a0=HYPERLINK"))
            self.assertEqual(got[0]["quantity"], "-1.25")
            self.assertEqual(got[4]["text"], "ordinary item")
    def test_set_json_serialization_is_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.json"), os.path.join(d, "b.json")
            atomic_write_json(a, {"items": {"gamma", "alpha", "beta"}})
            atomic_write_json(b, {"items": {"beta", "gamma", "alpha"}})
            with open(a, encoding="utf-8") as fa, open(b, encoding="utf-8") as fb:
                self.assertEqual(fa.read(), fb.read())
    def test_atomic_json_and_safe_path(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "nested", "result.json")
            atomic_write_json(p, {"n": Decimal("1.20")})
            with open(p, encoding="utf-8") as stream:
                self.assertEqual(json.load(stream)["n"], "1.20")
            with self.assertRaises(DataValidationError): safe_relative_path(d, "../escape")

    def test_gate_result_severity_never_downgrades_and_contract_counts_validate(self):
        result = GateResult("TEST", checked_count=5, expected_count=5)
        result.add(Issue("E", "ERROR", "tool error"))
        result.add(Issue("F", "FAIL", "bad data"))
        self.assertEqual(result.status, Status.ERROR)
        result.finalize(executed=True)
        payload = result.to_dict()
        self.assertTrue(payload["executed"])
        self.assertEqual(payload["checked_count"], 5)
        self.assertEqual(payload["schema_version"], "1.1.0")
        broken = GateResult("BAD", checked_count=-1).finalize(executed=True)
        self.assertEqual(broken.status, Status.ERROR)
        self.assertIsNone(broken.checked_count)

    def test_project_scoped_unit_alias_registry(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "units.json"
            path.write_text(json.dumps({
                "schema_version": "1.0.0", "project_id": "P-1",
                "context": {"jurisdiction": "Test", "language": "en"},
                "aliases": [{
                    "alias": "CM", "canonical_unit": "m3", "dimension": "volume",
                    "approval_status": "APPROVED", "approved_by": "QS-01",
                    "approved_at": "2026-01-01", "source_document": "Contract Appendix A",
                    "source_clause": "A.3", "applicability": {"project_id": "P-1"}
                }]
            }), encoding="utf-8")
            aliases = load_unit_alias_registry(path, project_id="P-1")
            self.assertEqual(convert("2.5", "CM", "m3", unit_registry=aliases, project_id="P-1"), Decimal("2.5"))
            with self.assertRaises(DataValidationError):
                load_unit_alias_registry(path, project_id="P-2")
            with self.assertRaises(DataValidationError):
                convert("2.5", "CM", "m3")

    def test_ambiguous_alias_cannot_be_approved_without_governance(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "units.json"
            path.write_text(json.dumps({
                "schema_version": "1.0.0", "project_id": "P-1",
                "context": {"jurisdiction": "Test", "language": "en"},
                "aliases": [{"alias": "CM", "canonical_unit": "m3", "dimension": "volume",
                             "approval_status": "DRAFT", "applicability": {"project_id": "P-1"}}]
            }), encoding="utf-8")
            with self.assertRaises(DataValidationError):
                load_unit_alias_registry(path, project_id="P-1")

    def test_deterministic_decimal_properties(self):
        rng = random.Random(20261027)
        for _ in range(500):
            integer = rng.randint(0, 10**9)
            scale = rng.randint(0, 6)
            value = Decimal(integer).scaleb(-scale)
            metres = convert(value, "mm", "m")
            restored = convert(metres, "m", "mm")
            self.assertEqual(restored, value)
            delta = Decimal(rng.randint(0, 1000)).scaleb(-6)
            self.assertEqual(within_tolerance(value + delta, value, abs_tol=delta),
                             within_tolerance(value, value + delta, abs_tol=delta))

    def test_safe_path_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as outside:
            root = Path(d)
            out = Path(outside) / "secret.txt"
            out.write_text("x", encoding="utf-8")
            link = root / "linked.txt"
            try:
                link.symlink_to(out)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks not supported in this environment")
            with self.assertRaises(DataValidationError):
                safe_relative_path(root, "linked.txt")

if __name__ == "__main__":
    unittest.main(verbosity=2)
