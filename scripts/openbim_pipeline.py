#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the governed IFC QTO -> delivery check -> BOQ mapping -> snapshot review pipeline.

This is an orchestration layer over the package's bounded IFC STEP/QTO readers. It
never computes missing geometry quantities or auto-approves a changed model.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from boq_mapping import map_ifc_quantities
from ifc_delivery_check import check_delivery_profile
from ifc_qto_diff import compare_snapshots
from ifc_qto_extract import extract_ifc_qto
from qto_core import atomic_write_json, sha256_file

PIPELINE_SCHEMA_VERSION = "1.0.0"
PIPELINE_TOOL_VERSION = "1.0.0"


def _failed_extract(path: Path, exc: Exception) -> dict[str, Any]:
    """Emit a schema-shaped FAIL artifact so downstream stages can fail closed."""
    try:
        digest = sha256_file(path)
    except Exception:
        digest = "0" * 64
    return {
        "schema_version": "1.0.0", "tool": "ifc_qto_extract", "tool_version": "1.0.0",
        "status": "FAIL", "source_file": path.name, "source_path": str(path.resolve()),
        "source_sha256": digest, "project": {"global_id": None, "name": None, "schema": None},
        "project_units": {},
        "metrics": {"ifc_entities": 0, "recognized_elements": 0, "quantity_sets": 0,
                    "linked_quantity_sets": 0, "quantities_extracted": 0, "failures": 1,
                    "warnings": 0, "elements_without_quantities": 0},
        "elements": [], "quantities": [],
        "findings": [{"code": "EXTRACTION_EXCEPTION", "severity": "FAIL",
                      "message": f"{type(exc).__name__}: {exc}"}],
    }


def run_pipeline(*, baseline_ifc: str | Path, current_ifc: str | Path,
                 mapping_registry: str | Path, delivery_profile: str | Path,
                 project_id: str, out_dir: str | Path,
                 unit_registry: str | Path | None = None,
                 abs_tol: str = "0.000001", rel_tol: str = "0.0001") -> dict[str, Any]:
    baseline_path = Path(baseline_ifc).resolve()
    current_path = Path(current_ifc).resolve()
    registry_path = Path(mapping_registry).resolve()
    profile_path = Path(delivery_profile).resolve()
    alias_path = Path(unit_registry).resolve() if unit_registry else None
    output_dir = Path(out_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    input_paths = {"baseline_ifc": baseline_path, "current_ifc": current_path,
                   "mapping_registry": registry_path, "delivery_profile": profile_path}
    if alias_path:
        input_paths["unit_registry"] = alias_path
    missing = [f"{name}: {path}" for name, path in input_paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError("required input file(s) missing: " + "; ".join(missing))
    before = {name: sha256_file(path) for name, path in input_paths.items()}

    reports = {
        "baseline_qto": output_dir / "baseline_qto.json",
        "current_qto": output_dir / "current_qto.json",
        "delivery_check": output_dir / "delivery_check.json",
        "boq_mapping": output_dir / "boq_mapping.json",
        "snapshot_diff": output_dir / "snapshot_diff.json",
        "summary": output_dir / "pipeline_summary.json",
        "summary_markdown": output_dir / "pipeline_summary.md",
    }
    steps: list[dict[str, Any]] = []

    def record(name: str, status: str, path: Path, metrics: dict[str, Any] | None = None,
               message: str | None = None) -> None:
        entry: dict[str, Any] = {"step": name, "status": status,
                                 "report": str(path.relative_to(output_dir)).replace("\\", "/")}
        if metrics is not None:
            entry["metrics"] = metrics
        if message:
            entry["message"] = message
        steps.append(entry)

    # Stage 1: independently extract the baseline and current snapshots.
    extracted: dict[str, dict[str, Any]] = {}
    for label, source in (("baseline", baseline_path), ("current", current_path)):
        target = reports[f"{label}_qto"]
        try:
            result = extract_ifc_qto(source)
        except Exception as exc:
            result = _failed_extract(source, exc)
        atomic_write_json(target, result)
        extracted[label] = result
        record(f"extract_{label}", result.get("status", "FAIL"), target,
               result.get("metrics", {}),
               "explicit IFC QTO only; no geometric recomputation is performed")

    # Stage 2: delivery profile and BOQ map are run independently to expose all findings.
    try:
        delivery = check_delivery_profile(reports["current_qto"], profile_path,
                                          expected_project_id=project_id)
    except Exception as exc:
        delivery = {"schema_version": "1.0.0", "tool": "ifc_delivery_check",
                    "status": "FAIL", "release_eligible": False,
                    "metrics": {"failures": 1},
                    "findings": [{"code": "DELIVERY_CHECK_EXCEPTION", "severity": "FAIL",
                                  "message": f"{type(exc).__name__}: {exc}"}]}
    atomic_write_json(reports["delivery_check"], delivery)
    record("delivery_profile", delivery.get("status", "FAIL"), reports["delivery_check"],
           delivery.get("metrics", {}))

    try:
        mapping = map_ifc_quantities(reports["current_qto"], registry_path, project_id=project_id,
                                     unit_registry_path=alias_path, source_ifc_path=current_path)
    except Exception as exc:
        mapping = {"schema_version": "1.0.0", "tool": "boq_mapping", "project_id": project_id,
                   "status": "FAIL", "release_eligible": False,
                   "metrics": {"failures": 1},
                   "findings": [{"code": "BOQ_MAPPING_EXCEPTION", "severity": "FAIL",
                                 "message": f"{type(exc).__name__}: {exc}"}]}
    atomic_write_json(reports["boq_mapping"], mapping)
    record("boq_mapping", mapping.get("status", "FAIL"), reports["boq_mapping"],
           mapping.get("metrics", {}))

    # Stage 3: diff baseline/current QTO; any change remains review-gated.
    try:
        difference = compare_snapshots(reports["baseline_qto"], reports["current_qto"],
                                       abs_tol=abs_tol, rel_tol=rel_tol,
                                       unit_registry_path=alias_path, project_id=project_id if alias_path else None)
    except Exception as exc:
        difference = {"schema_version": "1.0.0", "tool": "ifc_qto_diff", "status": "REVIEW_REQUIRED",
                      "metrics": {"not_comparable": 1},
                      "findings": [{"code": "SNAPSHOT_DIFF_EXCEPTION", "severity": "FAIL",
                                    "message": f"{type(exc).__name__}: {exc}"}]}
    atomic_write_json(reports["snapshot_diff"], difference)
    record("snapshot_diff", difference.get("status", "REVIEW_REQUIRED"), reports["snapshot_diff"],
           difference.get("metrics", {}), difference.get("release_note"))

    after = {name: sha256_file(path) for name, path in input_paths.items()}
    inputs_unchanged = before == after
    hard_failures = []
    if not inputs_unchanged:
        hard_failures.append("one or more source inputs changed during execution")
    if any(extracted[name].get("status") == "FAIL" for name in ("baseline", "current")):
        hard_failures.append("baseline/current IFC QTO extraction failed")
    if delivery.get("status") == "FAIL":
        hard_failures.append("delivery profile gate failed")
    if mapping.get("status") == "FAIL":
        hard_failures.append("BOQ mapping gate failed")
    has_warning = any(extracted[name].get("status") == "WARN" for name in ("baseline", "current")) \
        or delivery.get("status") == "WARN" or mapping.get("status") == "WARN"
    if hard_failures:
        overall = "BLOCKED"
    elif difference.get("status") != "NO_CHANGE" or has_warning:
        overall = "REVIEW_REQUIRED"
    else:
        overall = "PASS"

    summary = {
        "schema_version": PIPELINE_SCHEMA_VERSION, "tool": "openbim_pipeline",
        "tool_version": PIPELINE_TOOL_VERSION, "project_id": project_id,
        "status": overall, "release_eligible": overall == "PASS",
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_hashes_before": before, "input_hashes_after": after,
        "source_inputs_unchanged": inputs_unchanged,
        "stage_statuses": {step["step"]: step["status"] for step in steps},
        "steps": steps, "blocking_reasons": hard_failures,
        "report_paths": {key: path.name for key, path in reports.items()},
        "scope_note": (
            "IFC STEP explicit quantity pipeline only. It does not infer quantities from geometry, "
            "does not implement the full official IDS grammar, does not perform PDF/CAD visual diff, "
            "and never auto-approves model changes. Any quantity change requires QS/contract review."
        ),
    }
    atomic_write_json(reports["summary"], summary)
    md = ["# OpenBIM QTO 一键工作流报告", "", f"- 项目：`{project_id}`",
          f"- 综合状态：**{overall}**", f"- 可发布：**{summary['release_eligible']}**",
          f"- 输入文件执行前后哈希一致：**{inputs_unchanged}**", "", "## 阶段结果", ""]
    md.extend([f"- **{s['status']}** `{s['step']}` — `{s['report']}`" for s in steps])
    if hard_failures:
        md += ["", "## 阻断原因", ""] + [f"- {reason}" for reason in hard_failures]
    md += ["", "## 范围与限制", "", summary["scope_note"], ""]
    reports["summary_markdown"].write_text("\n".join(md), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("run", help="run extraction, delivery profile, BOQ mapping and snapshot diff")
    p.add_argument("--baseline-ifc", required=True)
    p.add_argument("--current-ifc", required=True)
    p.add_argument("--registry", required=True, help="approved BOQ mapping registry")
    p.add_argument("--profile", required=True, help="project delivery profile")
    p.add_argument("--project-id", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--unit-registry", help="optional approved project unit aliases")
    p.add_argument("--abs-tol", default="0.000001")
    p.add_argument("--rel-tol", default="0.0001")
    sub.add_parser("selftest", help="verify orchestration entry point")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        print("PASS openbim_pipeline selftest entrypoint")
        return 0
    if args.cmd != "run":
        ap.print_help()
        return 2
    try:
        result = run_pipeline(baseline_ifc=args.baseline_ifc, current_ifc=args.current_ifc,
                              mapping_registry=args.registry, delivery_profile=args.profile,
                              project_id=args.project_id, out_dir=args.out_dir,
                              unit_registry=args.unit_registry, abs_tol=args.abs_tol,
                              rel_tol=args.rel_tol)
        print(f"OpenBIM pipeline: {result['status']} | release_eligible={result['release_eligible']} | stages={len(result['steps'])}")
        print(f"report={Path(args.out_dir).resolve() / 'pipeline_summary.json'}")
        return 0 if result["status"] == "PASS" else 1
    except Exception as exc:
        print(f"[NOT_RUN] OpenBIM pipeline failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
