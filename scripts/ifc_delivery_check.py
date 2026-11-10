#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IDS-inspired IFC QTO delivery-profile checks (not a full IDS parser)."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from typing import Any
from qto_core import atomic_write_json, unit_info, sha256_file


def check_delivery_profile(qto_path: str | Path, profile_path: str | Path, *, expected_project_id: str | None = None) -> dict[str, Any]:
    qto = json.loads(Path(qto_path).read_text(encoding="utf-8-sig"))
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8-sig"))
    findings: list[dict[str, str]] = []
    def add(code: str, severity: str, message: str, element: str | None = None) -> None:
        f = {"code": code, "severity": severity, "message": message}
        if element: f["element_global_id"] = element
        findings.append(f)
    if qto.get("schema_version") != "1.0.0" or qto.get("tool") != "ifc_qto_extract":
        add("INVALID_QTO_INPUT", "FAIL", "input must be output schema 1.0.0 from ifc_qto_extract")
    upstream_status = qto.get("status")
    if upstream_status == "FAIL":
        add("UPSTREAM_QTO_FAILED", "FAIL", "QTO extraction has FAIL status; delivery profile cannot pass an invalid upstream extract")
    elif upstream_status == "WARN":
        add("UPSTREAM_QTO_WARNING", "WARN", "QTO extraction has WARN status; downstream delivery requires review")
    elif upstream_status not in ("PASS", "WARN", "FAIL"):
        add("QTO_STATUS_INVALID", "FAIL", f"QTO status {upstream_status!r} is not PASS/WARN/FAIL")
    if profile.get("schema_version") != "1.0.0" or not profile.get("profile_id"):
        add("INVALID_PROFILE", "FAIL", "profile needs schema_version 1.0.0 and profile_id")
    if expected_project_id and str(profile.get("project_id") or "").strip() != expected_project_id:
        add("PROJECT_SCOPE_MISMATCH", "FAIL", f"delivery profile project_id {profile.get('project_id')!r} does not match requested project {expected_project_id!r}")
    requirements = profile.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        add("EMPTY_PROFILE", "FAIL", "profile must contain at least one requirement")
        requirements = []
    elements = qto.get("elements") if isinstance(qto.get("elements"), list) else []
    requirement_ids: set[str] = set()
    for idx, req in enumerate(requirements, 1):
        if not isinstance(req, dict):
            add("INVALID_REQUIREMENT", "FAIL", f"requirements[{idx}] must be an object"); continue
        rid = str(req.get("requirement_id") or f"REQ-{idx}").strip()
        if not str(req.get("requirement_id") or "").strip() or rid in requirement_ids:
            add("REQUIREMENT_ID_INVALID", "FAIL", f"requirements[{idx}] has missing/duplicate requirement_id {rid!r}")
        requirement_ids.add(rid)
        types = {str(x).upper() for x in req.get("entity_types", [])}
        if not types:
            add("REQUIREMENT_TYPES_MISSING", "FAIL", f"{rid} must specify entity_types"); continue
        matching = [e for e in elements if str(e.get("entity_type") or "").upper() in types]
        minimum = req.get("min_count", 0)
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
            add("INVALID_MIN_COUNT", "FAIL", f"{rid}.min_count must be a non-negative integer"); minimum = 0
        if len(matching) < minimum:
            add("MIN_ENTITY_COUNT", "FAIL", f"{rid}: found {len(matching)} matching elements; minimum is {minimum}")
        required_quantities = req.get("required_quantities", [])
        if not isinstance(required_quantities, list):
            add("INVALID_REQUIRED_QUANTITIES", "FAIL", f"{rid}.required_quantities must be an array"); required_quantities = []
        malformed_qreqs = [i for i, item in enumerate(required_quantities, 1) if not isinstance(item, dict)]
        for item_index in malformed_qreqs:
            add("INVALID_REQUIRED_QUANTITY", "FAIL", f"{rid}.required_quantities[{item_index}] must be an object")
        required_quantities = [item for item in required_quantities if isinstance(item, dict)]
        raw_required_sets = req.get("required_quantity_sets", [])
        if not isinstance(raw_required_sets, list) or not all(isinstance(x, str) and x.strip() for x in raw_required_sets):
            add("REQUIRED_QUANTITY_SETS_INVALID", "FAIL", f"{rid}.required_quantity_sets must be an array of nonempty strings")
            raw_required_sets = []
        required_sets = set(raw_required_sets)
        for elem in matching:
            guid = elem.get("global_id")
            label = f"{elem.get('entity_type')} {guid or ('STEP #' + str(elem.get('step_id')))}"
            if req.get("require_global_id", True) and not guid:
                add("GLOBAL_ID_REQUIRED", "FAIL", f"{rid}: {label} lacks GlobalId", str(guid or ""))
            if req.get("require_name", True) and not str(elem.get("name") or "").strip():
                add("NAME_REQUIRED", "FAIL", f"{rid}: {label} lacks Name", str(guid or ""))
            actual_sets = set(elem.get("quantity_sets") or [])
            for qset in sorted(required_sets - actual_sets):
                add("QUANTITY_SET_REQUIRED", "FAIL", f"{rid}: {label} lacks required quantity set {qset}", str(guid or ""))
            actual = elem.get("quantities") or []
            for qreq in required_quantities:
                qset = str(qreq.get("quantity_set") or "")
                qname = str(qreq.get("quantity_name") or "")
                matches_q = [q for q in actual if q.get("quantity_set") == qset and q.get("quantity_name") == qname]
                if not matches_q:
                    add("QUANTITY_REQUIRED", "FAIL", f"{rid}: {label} lacks {qset}.{qname}", str(guid or "")); continue
                for q in matches_q:
                    expected_kind = qreq.get("kind")
                    if expected_kind and q.get("kind") != expected_kind:
                        add("QUANTITY_KIND_MISMATCH", "FAIL", f"{rid}: {label} {qset}.{qname} kind={q.get('kind')!r}, expected={expected_kind!r}", str(guid or ""))
                    expected_dimension = qreq.get("unit_dimension")
                    if expected_dimension:
                        if not q.get("unit"):
                            add("QUANTITY_UNIT_MISSING", "FAIL", f"{rid}: {label} {qset}.{qname} has no resolved unit", str(guid or ""))
                        else:
                            try:
                                dimension, _factor = unit_info(q["unit"])
                                if dimension != expected_dimension:
                                    add("QUANTITY_DIMENSION_MISMATCH", "FAIL", f"{rid}: {label} {qset}.{qname} unit {q['unit']!r} has dimension {dimension}, expected {expected_dimension}", str(guid or ""))
                            except Exception as exc:
                                add("QUANTITY_UNIT_UNSUPPORTED", "FAIL", f"{rid}: {label} {qset}.{qname} unit unsupported: {exc}", str(guid or ""))
    fail_count = sum(1 for f in findings if f["severity"] == "FAIL")
    warn_count = sum(1 for f in findings if f["severity"] == "WARN")
    status = "FAIL" if fail_count else ("WARN" if warn_count else "PASS")
    return {"schema_version": "1.0.0", "tool": "ifc_delivery_check", "profile_id": profile.get("profile_id"),
            "project_id": profile.get("project_id"), "status": status, "release_eligible": status == "PASS",
            "source_hashes": {"qto": sha256_file(qto_path), "profile": sha256_file(profile_path)},
            "scope_note": "IDS-inspired subset over extracted IFC QTO fields; this is not a full buildingSMART IDS schema/parser or geometric model validation.",
            "metrics": {"elements": len(elements), "requirements": len(requirements), "failures": fail_count, "warnings": warn_count}, "findings": findings}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check", help="check extracted IFC QTO against project delivery profile")
    p.add_argument("--qto", required=True); p.add_argument("--profile", required=True); p.add_argument("--out", required=True)
    p.add_argument("--project-id", help="optional project scope guard; must match the profile project_id")
    sub.add_parser("selftest", help="check CLI entrypoint")
    a = ap.parse_args(argv)
    if a.cmd == "selftest": print("PASS ifc_delivery_check selftest entrypoint"); return 0
    if a.cmd != "check": ap.print_help(); return 2
    try:
        result = check_delivery_profile(a.qto, a.profile, expected_project_id=a.project_id); atomic_write_json(a.out, result)
        print(f"IFC delivery profile: {result['status']} | requirements={result['metrics']['requirements']} | failures={result['metrics']['failures']}")
        print(f"report={Path(a.out).resolve()}")
        return 1 if result["status"] == "FAIL" else 0
    except Exception as exc:
        print(f"[NOT_RUN] delivery profile check failed: {exc}", file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
