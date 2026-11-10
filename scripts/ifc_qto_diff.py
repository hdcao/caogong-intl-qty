#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare two IFC QTO extraction snapshots; every detected change requires review."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
from decimal import Decimal
from typing import Any
from qto_core import DataValidationError, atomic_write_json, convert, load_unit_alias_registry, sha256_file, parse_decimal


def compare_snapshots(baseline_path: str | Path, current_path: str | Path, *, abs_tol: str = "0.000001", rel_tol: str = "0.0001", unit_registry_path: str | Path | None = None, project_id: str | None = None) -> dict[str, Any]:
    baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8-sig"))
    current = json.loads(Path(current_path).read_text(encoding="utf-8-sig"))
    findings: list[dict[str, str]] = []
    try:
        abs_tol_value = parse_decimal(abs_tol, field_name="abs_tol")
        rel_tol_value = parse_decimal(rel_tol, field_name="rel_tol")
        if abs_tol_value is None or rel_tol_value is None or abs_tol_value < 0 or rel_tol_value < 0:
            raise DataValidationError("absolute and relative tolerances must be finite non-negative decimals")
    except Exception as exc:
        findings.append({"code": "INVALID_TOLERANCE", "severity": "FAIL", "message": str(exc)})
        abs_tol_value = Decimal("0")
        rel_tol_value = Decimal("0")
    unit_aliases = {}
    if unit_registry_path:
        try:
            if not project_id:
                raise ValueError("--project-id is required when --unit-registry is supplied")
            unit_aliases = load_unit_alias_registry(unit_registry_path, project_id=project_id)
        except Exception as exc:
            findings.append({"code": "UNIT_REGISTRY_INVALID", "severity": "FAIL", "message": str(exc)})
    if any(x.get("tool") != "ifc_qto_extract" or x.get("schema_version") != "1.0.0" for x in (baseline, current)):
        findings.append({"code": "INVALID_SNAPSHOT", "severity": "FAIL", "message": "both inputs must be ifc_qto_extract schema 1.0.0 outputs"})
    for label, snapshot in (("baseline", baseline), ("current", current)):
        digest = str(snapshot.get("source_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            findings.append({"code": "SNAPSHOT_SOURCE_HASH_INVALID", "severity": "FAIL", "message": f"{label} snapshot has missing/invalid source_sha256"})
        if snapshot.get("status") not in ("PASS", "WARN", "FAIL"):
            findings.append({"code": "SNAPSHOT_STATUS_INVALID", "severity": "FAIL", "message": f"{label} snapshot has invalid status {snapshot.get('status')!r}"})
        elif snapshot.get("status") == "WARN":
            findings.append({"code": "UPSTREAM_QTO_WARNING", "severity": "WARN", "message": f"{label} extraction has WARN status; verify extraction findings before accepting a no-change result"})
    if baseline.get("status") == "FAIL" or current.get("status") == "FAIL":
        findings.append({"code": "UPSTREAM_QTO_FAILED", "severity": "FAIL", "message": "a snapshot has extraction FAIL status; comparison is incomplete"})
    def key(q: dict[str, Any]) -> tuple[str, str, str, str]:
        return (str(q.get("element_global_id") or ""), str(q.get("quantity_set") or ""), str(q.get("quantity_name") or ""), str(q.get("kind") or ""))
    bq = baseline.get("quantities") if isinstance(baseline.get("quantities"), list) else []
    cq = current.get("quantities") if isinstance(current.get("quantities"), list) else []
    bmap: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    cmap: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for name, rows, target in (("baseline", bq, bmap), ("current", cq, cmap)):
        for row in rows:
            k = key(row)
            if not k[0]: findings.append({"code": "MISSING_GUID", "severity": "FAIL", "message": f"{name} quantity has no element GlobalId: {k!r}"})
            if k in target: findings.append({"code": "DUPLICATE_SNAPSHOT_KEY", "severity": "FAIL", "message": f"{name} snapshot has duplicate quantity key {k!r}"})
            target[k] = row
    rows_out: list[dict[str, Any]] = []
    changed = added = removed = not_comparable = 0
    all_keys = sorted(set(bmap) | set(cmap))
    for k in all_keys:
        b, c = bmap.get(k), cmap.get(k)
        entry: dict[str, Any] = {"element_global_id": k[0], "quantity_set": k[1], "quantity_name": k[2], "kind": k[3]}
        if b is None:
            entry.update({"change": "ADDED", "current_value": c.get("value"), "current_unit": c.get("unit")}); added += 1
        elif c is None:
            entry.update({"change": "REMOVED", "baseline_value": b.get("value"), "baseline_unit": b.get("unit")}); removed += 1
        else:
            bu, cu = b.get("unit"), c.get("unit")
            if not bu or not cu:
                entry.update({"change": "NOT_COMPARABLE", "baseline_value": b.get("value"), "baseline_unit": bu, "current_value": c.get("value"), "current_unit": cu, "reason": "missing resolved unit"}); not_comparable += 1
            else:
                try:
                    old_as_current = convert(b.get("value"), bu, cu, unit_registry=unit_aliases, project_id=project_id)
                    old = Decimal(str(old_as_current)); new = Decimal(str(c.get("value")))
                    diff = abs(new-old)
                    denominator = max(abs(new), abs(old), Decimal("1e-34"))
                    rel = diff / denominator
                    threshold = max(abs_tol_value, rel_tol_value * denominator)
                    status = "UNCHANGED" if diff <= threshold else "CHANGED"
                    entry.update({"change": status, "baseline_value": b.get("value"), "baseline_unit": bu,
                                  "baseline_converted_to_current_unit": old_as_current, "current_value": c.get("value"),
                                  "current_unit": cu, "absolute_delta": diff, "relative_delta": rel})
                    if status == "CHANGED": changed += 1
                except Exception as exc:
                    entry.update({"change": "NOT_COMPARABLE", "baseline_value": b.get("value"), "baseline_unit": bu,
                                  "current_value": c.get("value"), "current_unit": cu, "reason": str(exc)}); not_comparable += 1
        rows_out.append(entry)
    # Detect product additions/deletions even if an element had no QTO quantities.
    def index_elements(label: str, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for element in snapshot.get("elements", []) if isinstance(snapshot.get("elements"), list) else []:
            if not isinstance(element, dict):
                findings.append({"code": "INVALID_ELEMENT_ROW", "severity": "FAIL", "message": f"{label} snapshot contains a non-object element row"})
                continue
            guid = str(element.get("global_id") or "").strip()
            if not guid:
                continue
            if guid in indexed:
                findings.append({"code": "DUPLICATE_ELEMENT_GLOBAL_ID", "severity": "FAIL", "message": f"{label} snapshot has duplicate element GlobalId {guid!r}"})
            else:
                indexed[guid] = element
        return indexed
    be = index_elements("baseline", baseline)
    ce = index_elements("current", current)
    element_added = sorted(set(ce) - set(be)); element_removed = sorted(set(be) - set(ce))
    if added or removed or changed or not_comparable or element_added or element_removed:
        findings.append({"code": "MODEL_QTO_CHANGE_REQUIRES_REVIEW", "severity": "FAIL", "message": "snapshot differences were found; this report does not automatically approve their quantity/BOQ impact"})
    return {"schema_version": "1.0.0", "tool": "ifc_qto_diff", "status": "REVIEW_REQUIRED" if any(f["severity"] in ("FAIL", "WARN") for f in findings) else "NO_CHANGE",
            "input_hashes": {"baseline": sha256_file(baseline_path), "current": sha256_file(current_path), **({"unit_registry": sha256_file(unit_registry_path)} if unit_registry_path else {})},
            "metrics": {"baseline_quantities": len(bq), "current_quantities": len(cq), "quantity_added": added, "quantity_removed": removed,
                        "quantity_changed": changed, "not_comparable": not_comparable, "element_added": len(element_added), "element_removed": len(element_removed)},
            "element_added": element_added, "element_removed": element_removed, "quantity_changes": rows_out, "findings": findings,
            "release_note": "Changes are intentionally not auto-approved. Link every change to BOQ impact review or an evidence-backed no-impact declaration before release."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("compare", help="compare two extracted IFC QTO snapshots")
    p.add_argument("--baseline", required=True); p.add_argument("--current", required=True); p.add_argument("--out", required=True)
    p.add_argument("--abs-tol", default="0.000001"); p.add_argument("--rel-tol", default="0.0001")
    p.add_argument("--unit-registry", help="project-approved unit-alias registry; aliases are never globally inferred")
    p.add_argument("--project-id", help="required with --unit-registry")
    sub.add_parser("selftest", help="check CLI entrypoint")
    a = ap.parse_args(argv)
    if a.cmd == "selftest": print("PASS ifc_qto_diff selftest entrypoint"); return 0
    if a.cmd != "compare": ap.print_help(); return 2
    try:
        result = compare_snapshots(a.baseline, a.current, abs_tol=a.abs_tol, rel_tol=a.rel_tol, unit_registry_path=a.unit_registry, project_id=a.project_id); atomic_write_json(a.out, result)
        print(f"IFC QTO diff: {result['status']} | added={result['metrics']['quantity_added']} removed={result['metrics']['quantity_removed']} changed={result['metrics']['quantity_changed']} not_comparable={result['metrics']['not_comparable']}")
        print(f"report={Path(a.out).resolve()}")
        return 1 if result["status"] == "REVIEW_REQUIRED" else 0
    except Exception as exc:
        print(f"[NOT_RUN] IFC QTO diff failed: {exc}", file=sys.stderr); return 2

if __name__ == "__main__": raise SystemExit(main())
