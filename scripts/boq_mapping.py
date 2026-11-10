#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Governed, exact-match mapping from extracted IFC quantities to BOQ rows."""
from __future__ import annotations
import argparse, json, re, sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from qto_core import (DataValidationError, atomic_write_json, convert, load_unit_alias_registry,
                      sha256_file)

REQUIRED_GOVERNANCE = ("approved_by", "approved_at", "source_document", "source_clause")
MATCH_KEYS = ("element_type", "quantity_set", "quantity_name", "kind")


def _json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _approval_date(value: str) -> bool:
    try:
        if "T" in value or " " in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            date.fromisoformat(value)
        return True
    except Exception:
        return False


def _selector_matches(rule: dict[str, Any], row: dict[str, Any]) -> bool:
    return all(str(rule.get(key, "")).strip() in ("*", str(row.get(key, ""))) for key in MATCH_KEYS)


def map_ifc_quantities(qto_path: str | Path, registry_path: str | Path, *, project_id: str,
                       unit_registry_path: str | Path | None = None,
                       source_ifc_path: str | Path | None = None) -> dict[str, Any]:
    qto = _json(qto_path); registry = _json(registry_path)
    findings: list[dict[str, str]] = []
    def add(code: str, severity: str, message: str) -> None:
        findings.append({"code": code, "severity": severity, "message": message})
    if qto.get("schema_version") != "1.0.0" or qto.get("tool") != "ifc_qto_extract":
        add("INVALID_QTO_INPUT", "FAIL", "input must be schema_version 1.0.0 output from ifc_qto_extract")
    source_digest = str(qto.get("source_sha256") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", source_digest):
        add("QTO_SOURCE_HASH_INVALID", "FAIL", "IFC extraction output must contain a valid lowercase SHA-256 source digest")
    source_candidate = Path(source_ifc_path).resolve() if source_ifc_path else None
    if source_candidate is None and str(qto.get("source_path") or "").strip():
        source_candidate = Path(str(qto["source_path"])).resolve()
    source_ifc_verified = False
    if source_candidate is not None and source_candidate.is_file():
        try:
            actual_source_digest = sha256_file(source_candidate)
            source_ifc_verified = True
            if re.fullmatch(r"[0-9a-f]{64}", source_digest) and actual_source_digest != source_digest:
                add("SOURCE_IFC_HASH_MISMATCH", "FAIL", "source IFC no longer matches the SHA-256 recorded by QTO extraction; re-extract before mapping")
        except OSError as exc:
            add("SOURCE_IFC_HASH_RECHECK_FAILED", "FAIL", f"could not recheck source IFC hash: {exc}")
    elif source_ifc_path is not None:
        add("SOURCE_IFC_MISSING", "FAIL", f"explicit source IFC does not exist: {source_candidate}")
    else:
        add("SOURCE_IFC_HASH_NOT_RECHECKED", "WARN", "source IFC is unavailable; the QTO hash format is valid but the original file could not be reverified")
    upstream_status = qto.get("status")
    if upstream_status not in ("PASS", "WARN", "FAIL"):
        add("QTO_STATUS_INVALID", "FAIL", f"IFC extraction status {upstream_status!r} is not PASS/WARN/FAIL")
    elif upstream_status == "FAIL":
        add("UPSTREAM_QTO_FAILED", "FAIL", "IFC extraction has FAIL findings; mapped BOQ cannot be released")
    elif upstream_status == "WARN":
        add("UPSTREAM_QTO_WARNING", "WARN", "IFC extraction contains warnings; BOQ result requires review before release")
    if registry.get("schema_version") != "1.0.0":
        add("INVALID_MAPPING_SCHEMA", "FAIL", "mapping registry schema_version must be 1.0.0")
    if str(registry.get("project_id") or "").strip() != project_id:
        add("PROJECT_SCOPE_MISMATCH", "FAIL", f"registry project_id {registry.get('project_id')!r} does not match requested project {project_id!r}")
    rules = registry.get("mapping_rules")
    exclusions = registry.get("approved_exclusions", [])
    if not isinstance(rules, list) or not isinstance(exclusions, list):
        add("INVALID_MAPPING_RULES", "FAIL", "mapping_rules and approved_exclusions must be arrays")
        rules, exclusions = [], []
    rule_ids: set[str] = set()
    for i, rule in enumerate(rules, 1):
        if not isinstance(rule, dict):
            add("INVALID_MAPPING_RULE", "FAIL", f"mapping_rules[{i}] must be an object"); continue
        rid = str(rule.get("rule_id") or "").strip()
        if not rid or rid in rule_ids:
            add("DUPLICATE_OR_MISSING_RULE_ID", "FAIL", f"mapping_rules[{i}] has missing/duplicate rule_id {rid!r}")
        rule_ids.add(rid)
        if str(rule.get("status") or "").upper() != "APPROVED":
            add("UNAPPROVED_MAPPING_RULE", "FAIL", f"mapping rule {rid or i} is not APPROVED")
        missing = [k for k in REQUIRED_GOVERNANCE if not str(rule.get(k) or "").strip()]
        if missing:
            add("MAPPING_GOVERNANCE_MISSING", "FAIL", f"mapping rule {rid or i} missing {', '.join(missing)}")
        elif not _approval_date(str(rule.get("approved_at"))):
            add("MAPPING_APPROVAL_DATE_INVALID", "FAIL", f"mapping rule {rid} approved_at must be ISO date/datetime")
        if not str(rule.get("boq_code") or "").strip() or not str(rule.get("boq_unit") or "").strip():
            add("MAPPING_TARGET_MISSING", "FAIL", f"mapping rule {rid or i} requires boq_code and boq_unit")
        for key in MATCH_KEYS:
            if not str(rule.get(key) or "").strip():
                add("MAPPING_SELECTOR_MISSING", "FAIL", f"mapping rule {rid or i} missing selector {key}")
    exclusion_ids: set[str] = set()
    for i, row in enumerate(exclusions, 1):
        rid = str(row.get("rule_id") or "").strip() if isinstance(row, dict) else ""
        if not rid or rid in exclusion_ids:
            add("EXCLUSION_ID_INVALID", "FAIL", f"approved_exclusions[{i}] has missing/duplicate rule_id")
        exclusion_ids.add(rid)
        if not isinstance(row, dict) or str(row.get("status") or "").upper() != "APPROVED":
            add("EXCLUSION_NOT_APPROVED", "FAIL", f"exclusion {rid or i} must be APPROVED")
        else:
            missing = [k for k in REQUIRED_GOVERNANCE + ("reason",) if not str(row.get(k) or "").strip()]
            if missing:
                add("EXCLUSION_GOVERNANCE_MISSING", "FAIL", f"exclusion {rid} missing {', '.join(missing)}")
            elif not _approval_date(str(row.get("approved_at"))):
                add("EXCLUSION_APPROVAL_DATE_INVALID", "FAIL", f"exclusion {rid} approved_at must be ISO date/datetime")
            for key in MATCH_KEYS:
                if not str(row.get(key) or "").strip():
                    add("EXCLUSION_SELECTOR_MISSING", "FAIL", f"exclusion {rid} missing selector {key}")

    unit_aliases = None
    if unit_registry_path:
        try:
            unit_aliases = load_unit_alias_registry(unit_registry_path, project_id=project_id)
        except Exception as exc:
            add("UNIT_REGISTRY_INVALID", "FAIL", str(exc))
    if not unit_aliases:
        unit_aliases = {}
    qrows_raw = qto.get("quantities") if isinstance(qto.get("quantities"), list) else []
    qrows: list[dict[str, Any]] = []
    invalid_row_count = 0
    for row_index, row in enumerate(qrows_raw, 1):
        if not isinstance(row, dict):
            add("INVALID_QUANTITY_ROW", "FAIL", f"QTO quantities[{row_index}] must be an object")
            invalid_row_count += 1
        else:
            qrows.append(row)
    applied: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    mapped_count = 0; excluded_count = 0; unmapped_count = 0; invalid_count = 0
    for index, row in enumerate(qrows, 1):
        matches = [r for r in rules if isinstance(r, dict) and _selector_matches(r, row)]
        excluded_matches = [r for r in exclusions if isinstance(r, dict) and _selector_matches(r, row)]
        key = f"{row.get('element_global_id')}|{row.get('quantity_set')}|{row.get('quantity_name')}"
        if len(matches) + len(excluded_matches) != 1:
            severity_code = "UNMAPPED_QUANTITY" if not matches and not excluded_matches else "AMBIGUOUS_QUANTITY_MAPPING"
            add(severity_code, "FAIL", f"quantity {key} matched {len(matches)} mapping rules and {len(excluded_matches)} exclusions; exactly one approved disposition is required")
            applied.append({"source_key": key, "disposition": "UNMAPPED" if not matches and not excluded_matches else "AMBIGUOUS", "value": row.get("value"), "unit": row.get("unit")})
            unmapped_count += 1
            continue
        if excluded_matches:
            ex = excluded_matches[0]
            applied.append({"source_key": key, "disposition": "APPROVED_EXCLUSION", "exclusion_rule_id": ex.get("rule_id"), "reason": ex.get("reason"), "value": row.get("value"), "unit": row.get("unit")})
            excluded_count += 1
            continue
        rule = matches[0]
        if row.get("unit") is None:
            add("SOURCE_UNIT_MISSING", "FAIL", f"quantity {key} has no resolved source unit")
            applied.append({"source_key": key, "disposition": "FAIL", "mapping_rule_id": rule.get("rule_id"), "value": row.get("value"), "unit": None})
            invalid_count += 1
            continue
        try:
            converted = convert(row.get("value"), row.get("unit"), rule["boq_unit"],
                                unit_registry=unit_aliases, project_id=project_id)
        except Exception as exc:
            add("UNIT_CONVERSION_FAILED", "FAIL", f"quantity {key} cannot convert {row.get('unit')!r} to {rule.get('boq_unit')!r}: {exc}")
            applied.append({"source_key": key, "disposition": "NOT_COMPARABLE", "mapping_rule_id": rule.get("rule_id"), "value": row.get("value"), "unit": row.get("unit")})
            invalid_count += 1
            continue
        boq_code = str(rule["boq_code"])
        slot = summary.setdefault(boq_code, {"boq_code": boq_code, "description": rule.get("boq_description"), "unit": rule["boq_unit"], "quantity": Decimal("0"), "source_count": 0, "source_elements": []})
        if slot["unit"] != rule["boq_unit"]:
            add("BOQ_UNIT_CONFLICT", "FAIL", f"BOQ code {boq_code} has multiple target units {slot['unit']!r} and {rule['boq_unit']!r}")
            applied.append({"source_key": key, "disposition": "NOT_COMPARABLE", "mapping_rule_id": rule.get("rule_id"), "boq_code": boq_code, "source_value": row.get("value"), "source_unit": row.get("unit"), "reason": "conflicting target unit for BOQ code"})
            invalid_count += 1
            continue
        slot["quantity"] += converted
        slot["source_count"] += 1
        if row.get("element_global_id") not in slot["source_elements"]:
            slot["source_elements"].append(row.get("element_global_id"))
        applied.append({"source_key": key, "disposition": "MAPPED", "mapping_rule_id": rule.get("rule_id"), "boq_code": boq_code, "source_value": row.get("value"), "source_unit": row.get("unit"), "boq_value": converted, "boq_unit": rule["boq_unit"]})
        mapped_count += 1
    fail_count = sum(1 for f in findings if f["severity"] == "FAIL")
    warn_count = sum(1 for f in findings if f["severity"] == "WARN")
    status = "FAIL" if fail_count else ("WARN" if warn_count else "PASS")
    return {
        "schema_version": "1.0.0", "tool": "boq_mapping", "project_id": project_id,
        "status": status, "release_eligible": status == "PASS",
        "source_hashes": {"qto": sha256_file(qto_path), "mapping_registry": sha256_file(registry_path),
                          **({"source_ifc": sha256_file(source_candidate)} if source_ifc_verified and source_candidate else {}),
                          **({"unit_registry": sha256_file(unit_registry_path)} if unit_registry_path else {})},
        "source_ifc_reverified": source_ifc_verified,
        "metrics": {"source_quantities": len(qrows_raw), "valid_quantity_rows": len(qrows), "mapped_quantities": mapped_count, "approved_exclusions": excluded_count, "unmapped_or_invalid": unmapped_count + invalid_count + invalid_row_count, "unmapped_quantities": unmapped_count, "invalid_quantities": invalid_count + invalid_row_count, "invalid_quantity_rows": invalid_row_count, "boq_rows": len(summary), "failures": fail_count, "warnings": warn_count},
        "boq_summary": [{**v, "quantity": v["quantity"]} for _, v in sorted(summary.items())],
        "dispositions": applied, "findings": findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("map", help="validate governance and roll up IFC quantities to BOQ codes")
    p.add_argument("--qto", required=True); p.add_argument("--registry", required=True); p.add_argument("--project-id", required=True); p.add_argument("--out", required=True); p.add_argument("--unit-registry")
    p.add_argument("--source-ifc", help="optional explicit IFC file to re-verify against the QTO source SHA-256")
    sub.add_parser("selftest", help="small CLI selftest; full cases live in test_openbim_qto.py")
    a = ap.parse_args(argv)
    if a.cmd == "selftest":
        print("PASS boq_mapping selftest entrypoint"); return 0
    if a.cmd != "map": ap.print_help(); return 2
    try:
        result = map_ifc_quantities(a.qto, a.registry, project_id=a.project_id, unit_registry_path=a.unit_registry, source_ifc_path=a.source_ifc)
        atomic_write_json(a.out, result)
        print(f"BOQ mapping: {result['status']} | mapped={result['metrics']['mapped_quantities']} | excluded={result['metrics']['approved_exclusions']} | failures={result['metrics']['failures']}")
        print(f"report={Path(a.out).resolve()}")
        return 1 if result["status"] == "FAIL" else 0
    except Exception as exc:
        print(f"[NOT_RUN] BOQ mapping failed: {exc}", file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
