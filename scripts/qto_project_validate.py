#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate project manifest, measurement rules and project-scoped unit aliases.

Dependency-free, fail-closed release preflight for international quantity take-off.
Schema validity is deliberately distinct from professional approval: a simulation or
DRAFT project can be structurally valid while ``release_eligible`` remains false.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from qto_core import (
    CORE_VERSION, DataValidationError, GateResult, Issue, Status,
    atomic_write_json, fingerprint_inputs, load_unit_alias_registry,
    safe_relative_path, unit_info,
)

TOOL_VERSION = "1.1.0"
SCHEMA_VERSION = "1.0.0"
BASES = {"DESIGN_THEORETICAL", "CONTRACT_MEASURED", "TENDER_BOQ", "SITE_CLAIMED", "CERTIFIED_PAYMENT"}
PROJECT_RELEASE_STATES = {"APPROVED", "READY_FOR_RELEASE", "RELEASED", "ACTIVE"}
RULE_APPROVED_STATES = {"APPROVED", "ACTIVE", "RELEASED"}
PLACEHOLDER_RE = re.compile(r"\b(?:TO_BE_[A-Z_]+|TBD|TBC|PLACEHOLDER|CHANGE_ME)\b", re.I)


def read_json(path: Path, label: str, issues: list[Issue]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(Issue("CONFIG_READ_ERROR", "ERROR", f"Cannot read {label}: {exc}", str(path)))
        return None
    if not isinstance(value, dict):
        issues.append(Issue("CONFIG_ROOT_TYPE", "FAIL", f"{label} root must be a JSON object", str(path)))
        return None
    return value


def required_object(parent: dict[str, Any], key: str, where: str, issues: list[Issue]) -> dict[str, Any] | None:
    value = parent.get(key)
    if not isinstance(value, dict):
        issues.append(Issue("REQUIRED_OBJECT", "FAIL", f"{where}.{key} must be an object", where))
        return None
    return value


def required_text(parent: dict[str, Any], key: str, where: str, issues: list[Issue], *, allow_null: bool = False) -> str | None:
    value = parent.get(key)
    if allow_null and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        issues.append(Issue("REQUIRED_TEXT", "FAIL", f"{where}.{key} must be a non-empty string", where))
        return None
    return value.strip()


def iso_date(value: Any, label: str, issues: list[Issue], *, allow_datetime: bool = True) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        issues.append(Issue("DATE_FORMAT", "FAIL", f"{label} must be an ISO date/datetime string", label))
        return None
    try:
        if "T" in value or " " in value:
            if not allow_datetime:
                raise ValueError("datetime not allowed here")
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        return date.fromisoformat(value)
    except ValueError:
        issues.append(Issue("DATE_FORMAT", "FAIL", f"{label} must use ISO 8601 format, got {value!r}", label))
        return None


def check_ref(root: Path, raw: Any, label: str, issues: list[Issue], *, required: bool) -> Path | None:
    if raw is None or raw == "":
        if required:
            issues.append(Issue("REFERENCE_REQUIRED", "FAIL", f"{label} is required", label))
        return None
    if not isinstance(raw, str) or not raw.strip():
        issues.append(Issue("REFERENCE_TYPE", "FAIL", f"{label} must be a relative path string", label))
        return None
    candidate = Path(raw.strip())
    if candidate.is_absolute():
        issues.append(Issue("ABSOLUTE_REFERENCE", "FAIL", f"{label} must be project-relative for portability", label, raw))
        return None
    try:
        resolved = safe_relative_path(root, candidate)
    except DataValidationError as exc:
        issues.append(Issue("PATH_ESCAPE", "FAIL", f"{label}: {exc}", label, raw))
        return None
    if not resolved.is_file():
        issues.append(Issue("REFERENCE_MISSING", "FAIL", f"Referenced file does not exist: {raw}", label, raw))
        return None
    return resolved


def scan_placeholders(value: Any, path: str = "$", findings: list[str] | None = None) -> list[str]:
    findings = findings if findings is not None else []
    if isinstance(value, dict):
        for key, child in value.items():
            scan_placeholders(child, f"{path}.{key}", findings)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_placeholders(child, f"{path}[{index}]", findings)
    elif isinstance(value, str) and PLACEHOLDER_RE.search(value):
        findings.append(path)
    return findings


def _validate_manifest(manifest: dict[str, Any], project_root: Path, package_root: Path,
                       release: bool, issues: list[Issue]) -> dict[str, Any]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        issues.append(Issue("MANIFEST_SCHEMA_VERSION", "FAIL", f"schema_version must be {SCHEMA_VERSION!r}", "manifest.schema_version"))
    project = required_object(manifest, "project", "manifest", issues)
    scope = required_object(manifest, "scope", "manifest", issues)
    contract = required_object(manifest, "contract_and_measurement", "manifest", issues)
    drawing = required_object(manifest, "drawing_baseline", "manifest", issues)
    policy = required_object(manifest, "quantity_basis_policy", "manifest", issues)
    quality = required_object(manifest, "quality_policy", "manifest", issues)
    software = required_object(manifest, "software_baseline", "manifest", issues)
    sections = {"project": project, "scope": scope, "contract": contract, "drawing": drawing,
                "policy": policy, "quality": quality, "software": software}
    if any(value is None for value in sections.values()):
        return {"release_eligible": False, "project_id": None, "rule_registry": None, "unit_registry": None}

    project_id = required_text(project, "project_id", "manifest.project", issues)
    required_text(project, "project_name", "manifest.project", issues)
    required_text(project, "country_or_jurisdiction", "manifest.project", issues)
    required_text(project, "discipline", "manifest.project", issues)
    prepared_date = iso_date(project.get("prepared_at"), "manifest.project.prepared_at", issues)
    project_status = str(project.get("status") or "").strip().upper()
    if not project_status:
        issues.append(Issue("PROJECT_STATUS_REQUIRED", "FAIL", "manifest.project.status is required", "manifest.project.status"))

    included, excluded = scope.get("included"), scope.get("excluded")
    for key, values in (("included", included), ("excluded", excluded)):
        if not isinstance(values, list) or any(not isinstance(x, str) or not x.strip() for x in values):
            issues.append(Issue("SCOPE_ARRAY", "FAIL", f"manifest.scope.{key} must be an array of non-empty strings", f"manifest.scope.{key}"))
    if isinstance(included, list) and isinstance(excluded, list):
        inc = {x.strip().casefold() for x in included if isinstance(x, str)}
        exc = {x.strip().casefold() for x in excluded if isinstance(x, str)}
        overlap = sorted(inc & exc)
        if overlap:
            issues.append(Issue("SCOPE_OVERLAP", "FAIL", f"Items appear in both included and excluded scope: {overlap}", "manifest.scope"))
    assumptions_path = check_ref(project_root, scope.get("assumptions_register"), "manifest.scope.assumptions_register", issues, required=True)

    required_text(contract, "contract_id", "manifest.contract_and_measurement", issues)
    required_text(contract, "contract_document_revision", "manifest.contract_and_measurement", issues)
    required_text(contract, "measurement_standard", "manifest.contract_and_measurement", issues)
    required_text(contract, "measurement_standard_version", "manifest.contract_and_measurement", issues)
    if not isinstance(contract.get("project_specific_amendments"), list):
        issues.append(Issue("AMENDMENTS_ARRAY", "FAIL", "project_specific_amendments must be an array", "manifest.contract_and_measurement.project_specific_amendments"))
    rule_registry_path = check_ref(project_root, contract.get("rule_registry_file"),
                                   "manifest.contract_and_measurement.rule_registry_file", issues, required=True)
    contract_approval = str(contract.get("approval_status") or "").strip().upper()
    if contract_approval in RULE_APPROVED_STATES:
        required_text(contract, "approved_by", "manifest.contract_and_measurement", issues)
        approved_at = iso_date(contract.get("approved_at"), "manifest.contract_and_measurement.approved_at", issues)
        if not approved_at:
            issues.append(Issue("APPROVAL_TIMESTAMP_REQUIRED", "FAIL", "Approved measurement basis requires approved_at", "manifest.contract_and_measurement.approved_at"))

    check_ref(project_root, drawing.get("register_file"), "manifest.drawing_baseline.register_file", issues, required=True)
    drawing_status = str(drawing.get("register_status") or "").strip().upper()
    if not drawing_status:
        issues.append(Issue("DRAWING_REGISTER_STATUS", "FAIL", "drawing register_status is required", "manifest.drawing_baseline.register_status"))

    allowed_bases = policy.get("allowed_bases")
    if not isinstance(allowed_bases, list) or not allowed_bases or any(not isinstance(x, str) or x not in BASES for x in allowed_bases):
        issues.append(Issue("ALLOWED_BASES", "FAIL", f"allowed_bases must be a non-empty subset of {sorted(BASES)}", "manifest.quantity_basis_policy.allowed_bases"))
    default_basis = policy.get("default_basis")
    if default_basis is not None and (not isinstance(default_basis, str) or default_basis not in (allowed_bases or [])):
        issues.append(Issue("DEFAULT_BASIS", "FAIL", "default_basis must be null or one of allowed_bases", "manifest.quantity_basis_policy.default_basis"))
    if not policy.get("unit_system"):
        issues.append(Issue("UNIT_SYSTEM_REQUIRED", "FAIL", "unit_system must be declared", "manifest.quantity_basis_policy.unit_system"))

    mandatory = quality.get("mandatory_gates")
    if not isinstance(mandatory, list) or any(not isinstance(x, str) or not x.strip() for x in mandatory):
        issues.append(Issue("MANDATORY_GATES_ARRAY", "FAIL", "mandatory_gates must be an array of non-empty gate IDs", "manifest.quality_policy.mandatory_gates"))
    elif len({x.strip() for x in mandatory}) != len(mandatory):
        issues.append(Issue("DUPLICATE_MANDATORY_GATE", "FAIL", "mandatory_gates contains duplicates", "manifest.quality_policy.mandatory_gates"))
    if not isinstance(quality.get("accepted_open_items", []), list):
        issues.append(Issue("OPEN_ITEMS_ARRAY", "FAIL", "accepted_open_items must be an array", "manifest.quality_policy.accepted_open_items"))
    if quality.get("overall_coverage_threshold_is_not_sufficient_for_release") is not True:
        issues.append(Issue("COVERAGE_POLICY_UNSAFE", "FAIL", "overall coverage threshold must not be the sole release criterion", "manifest.quality_policy"))

    version_path = package_root / "VERSION"
    try:
        package_version = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        package_version = ""
        issues.append(Issue("PACKAGE_VERSION_UNREADABLE", "ERROR", str(exc), str(version_path)))
    manifest_version = str(software.get("skill_version") or "").strip()
    if package_version and manifest_version != package_version:
        issues.append(Issue("VERSION_DRIFT", "FAIL", f"project manifest skill_version {manifest_version!r} != package VERSION {package_version!r}", "manifest.software_baseline.skill_version"))
    if not software.get("python_version"):
        issues.append(Issue("PYTHON_VERSION_UNRECORDED", "WARN", "software_baseline.python_version should be recorded at run time", "manifest.software_baseline.python_version"))

    unit_registry_path = check_ref(project_root, manifest.get("unit_alias_registry_file"),
                                   "manifest.unit_alias_registry_file", issues, required=True)
    release_ready = (
        project_status in PROJECT_RELEASE_STATES
        and contract_approval in RULE_APPROVED_STATES
        and drawing_status in PROJECT_RELEASE_STATES
        and bool(quality.get("release_authority"))
        and bool(mandatory)
        and not scan_placeholders(manifest)
    )
    if release:
        if project_status not in PROJECT_RELEASE_STATES:
            issues.append(Issue("PROJECT_NOT_APPROVED", "FAIL", f"Project status {project_status!r} is not an approved release state", "manifest.project.status"))
        if contract_approval not in RULE_APPROVED_STATES:
            issues.append(Issue("MEASUREMENT_BASIS_NOT_APPROVED", "FAIL", f"Measurement approval status {contract_approval!r} is not approved", "manifest.contract_and_measurement.approval_status"))
        if drawing_status not in PROJECT_RELEASE_STATES:
            issues.append(Issue("DRAWING_BASELINE_NOT_APPROVED", "FAIL", f"Drawing register status {drawing_status!r} is not approved", "manifest.drawing_baseline.register_status"))
        if not quality.get("release_authority"):
            issues.append(Issue("RELEASE_AUTHORITY_REQUIRED", "FAIL", "release_authority is required for production release", "manifest.quality_policy.release_authority"))
        if not mandatory:
            issues.append(Issue("MANDATORY_GATES_REQUIRED", "FAIL", "at least one mandatory gate must be declared", "manifest.quality_policy.mandatory_gates"))
        placeholders = scan_placeholders(manifest)
        if placeholders:
            issues.append(Issue("UNRESOLVED_PLACEHOLDERS", "FAIL", f"Unresolved template placeholders at: {', '.join(placeholders[:12])}", "manifest"))

    return {"project_id": project_id, "rule_registry": rule_registry_path,
            "unit_registry": unit_registry_path, "release_eligible": release_ready,
            "project_status": project_status, "contract_approval": contract_approval,
            "drawing_status": drawing_status, "package_version": package_version,
            "manifest_version": manifest_version, "assumptions_register": assumptions_path}


def _validate_rules(rules_doc: dict[str, Any], allowed_bases: set[str], project_id: str | None,
                    unit_aliases: dict[str, dict[str, Any]], issues: list[Issue], *,
                    as_of: date, release: bool) -> int:
    if rules_doc.get("schema_version") != SCHEMA_VERSION:
        issues.append(Issue("RULE_SCHEMA_VERSION", "FAIL", f"rule registry schema_version must be {SCHEMA_VERSION!r}", "rules.schema_version"))
    rows = rules_doc.get("rules")
    if not isinstance(rows, list) or not rows:
        issues.append(Issue("RULES_ARRAY", "FAIL", "rules must be a non-empty array", "rules.rules"))
        return 0
    seen: set[str] = set()
    count = 0
    for index, rule in enumerate(rows, 1):
        count += 1
        where = f"rules.rules[{index}]"
        if not isinstance(rule, dict):
            issues.append(Issue("RULE_TYPE", "FAIL", f"{where} must be an object", where))
            continue
        rule_id = required_text(rule, "rule_id", where, issues)
        if rule_id:
            norm_id = rule_id.casefold()
            if norm_id in seen:
                issues.append(Issue("DUPLICATE_RULE_ID", "FAIL", f"Duplicate rule_id {rule_id!r}", where, rule_id))
            seen.add(norm_id)
        for key in ("title", "discipline", "jurisdiction", "source_document", "source_revision", "measurement_basis", "unit", "rounding_policy", "approval_status"):
            required_text(rule, key, where, issues)
        if rule.get("measurement_basis") not in allowed_bases:
            issues.append(Issue("RULE_BASIS_NOT_ALLOWED", "FAIL", f"Rule measurement_basis {rule.get('measurement_basis')!r} is not allowed by project", where, rule_id))
        for key in ("inclusion_rules", "exclusion_rules", "deduction_rules", "test_case_ids"):
            value = rule.get(key)
            if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
                issues.append(Issue("RULE_LIST_FIELD", "FAIL", f"{where}.{key} must be an array of non-empty strings", where, rule_id))
        start = iso_date(rule.get("effective_from"), f"{where}.effective_from", issues, allow_datetime=False)
        end = iso_date(rule.get("effective_to"), f"{where}.effective_to", issues, allow_datetime=False)
        if start and end and end < start:
            issues.append(Issue("RULE_EFFECTIVE_RANGE", "FAIL", "effective_to precedes effective_from", where, rule_id))
        if start and start > as_of:
            severity = "FAIL" if release else "WARN"
            issues.append(Issue("RULE_NOT_YET_EFFECTIVE", severity, f"Rule does not take effect until {start.isoformat()} (as-of {as_of.isoformat()})", where, rule_id))
        if end and end < as_of:
            severity = "FAIL" if release else "WARN"
            issues.append(Issue("RULE_EXPIRED", severity, f"Rule expired on {end.isoformat()} (as-of {as_of.isoformat()})", where, rule_id))
        rank = rule.get("precedence_rank")
        if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool) or rank < 1):
            issues.append(Issue("RULE_PRECEDENCE", "FAIL", "precedence_rank must be a positive integer", where, rule_id))
        elif rank is None:
            issues.append(Issue("RULE_PRECEDENCE_MISSING", "FAIL", "precedence_rank is required", where, rule_id))
        unit = rule.get("unit")
        if isinstance(unit, str) and unit.strip():
            try:
                unit_info(unit, unit_registry=unit_aliases, project_id=project_id)
            except DataValidationError as exc:
                issues.append(Issue("RULE_UNIT_INVALID", "FAIL", str(exc), where, rule_id))
        approval = str(rule.get("approval_status") or "").strip().upper()
        if approval in RULE_APPROVED_STATES:
            if not rule.get("approved_by"):
                issues.append(Issue("RULE_APPROVER_REQUIRED", "FAIL", "Approved rule requires approved_by", where, rule_id))
            if not iso_date(rule.get("approved_at"), f"{where}.approved_at", issues):
                issues.append(Issue("RULE_APPROVAL_DATE_REQUIRED", "FAIL", "Approved rule requires approved_at", where, rule_id))
        elif approval not in {"DRAFT", "PENDING", "SIMULATION_ONLY_NOT_AUTHORIZED", "NOT_APPROVED", "REJECTED", "SUPERSEDED"}:
            issues.append(Issue("RULE_APPROVAL_STATE", "FAIL", f"Unsupported approval_status {approval!r}", where, rule_id))
    return count


def validate(manifest_path: Path, project_root: Path, package_root: Path, *, rules_path: Path | None = None,
             release: bool = False, output_path: Path | None = None,
             as_of: date | None = None) -> dict[str, Any]:
    result = GateResult("PROJECT_CONFIG", tool_version=TOOL_VERSION)
    all_issues: list[Issue] = []
    as_of = as_of or date.today()
    manifest = read_json(manifest_path, "project manifest", all_issues)
    if manifest is None:
        result.add(Issue("MANIFEST_UNREADABLE", "ERROR", "Project manifest could not be loaded", str(manifest_path)))
        result.finalize(executed=True)
        payload = result.to_dict()
        if output_path:
            atomic_write_json(output_path, payload)
        return payload

    info = _validate_manifest(manifest, project_root, package_root, release, all_issues)
    selected_rules = rules_path or info.get("rule_registry")
    rule_doc = read_json(selected_rules, "measurement rule registry", all_issues) if isinstance(selected_rules, Path) else None
    alias_map: dict[str, dict[str, Any]] = {}
    unit_registry_doc: dict[str, Any] | None = None
    unit_registry_path = info.get("unit_registry")
    if isinstance(unit_registry_path, Path):
        unit_registry_doc = read_json(unit_registry_path, "unit-alias registry", all_issues)
        try:
            alias_map = load_unit_alias_registry(unit_registry_path, project_id=info.get("project_id"))
        except DataValidationError as exc:
            all_issues.append(Issue("UNIT_ALIAS_REGISTRY_INVALID", "FAIL", str(exc), str(unit_registry_path)))
    elif unit_registry_path is None and manifest.get("unit_alias_registry_file"):
        # A declared but missing alias file was already reported by check_ref.
        alias_map = {}

    policy = manifest.get("quantity_basis_policy") if isinstance(manifest.get("quantity_basis_policy"), dict) else {}
    allowed_bases = set(policy.get("allowed_bases", [])) if isinstance(policy.get("allowed_bases"), list) else set()
    rule_count = 0
    if rule_doc is not None:
        rule_count = _validate_rules(rule_doc, allowed_bases, info.get("project_id"), alias_map, all_issues,
                                     as_of=as_of, release=release)
    elif selected_rules is None:
        all_issues.append(Issue("RULE_REGISTRY_REQUIRED", "FAIL", "A measurement rule registry must be supplied or declared in the manifest", "manifest.contract_and_measurement.rule_registry_file"))

    # Release eligibility is about all control documents together, not the manifest alone.
    config_placeholders = []
    config_placeholders.extend(f"manifest{p[1:]}" for p in scan_placeholders(manifest))
    if rule_doc is not None:
        config_placeholders.extend(f"rules{p[1:]}" for p in scan_placeholders(rule_doc))
    if unit_registry_doc is not None:
        config_placeholders.extend(f"unit_registry{p[1:]}" for p in scan_placeholders(unit_registry_doc))
    if release and config_placeholders:
        all_issues.append(Issue("UNRESOLVED_CONFIG_PLACEHOLDERS", "FAIL",
                                 f"Unresolved placeholders in project control documents: {', '.join(config_placeholders[:20])}", "project_config"))

    # In release mode, even structurally valid but unapproved rules are a hard stop.
    if release and rule_doc is not None:
        unapproved = [str(r.get("rule_id", f"row-{i}")) for i, r in enumerate(rule_doc.get("rules", []), 1)
                      if not isinstance(r, dict) or str(r.get("approval_status") or "").strip().upper() not in RULE_APPROVED_STATES]
        if unapproved:
            all_issues.append(Issue("UNAPPROVED_RULES", "FAIL", f"Rules are not approved: {', '.join(unapproved[:20])}", "rules.rules"))

    # Record reproducible input hashes only for files that were actually read.
    paths = [manifest_path]
    if isinstance(selected_rules, Path) and selected_rules.is_file():
        paths.append(selected_rules)
    if info.get("unit_registry") and info["unit_registry"].is_file():
        paths.append(info["unit_registry"])
    try:
        raw_hashes = fingerprint_inputs(paths)
        portable_hashes = {}
        for path in paths:
            resolved = path.resolve()
            try:
                label = resolved.relative_to(project_root.resolve()).as_posix()
            except ValueError:
                try:
                    label = "package/" + resolved.relative_to(package_root.resolve()).as_posix()
                except ValueError:
                    label = resolved.name
            portable_hashes[label] = raw_hashes[str(path)]
        result.input_hashes = dict(sorted(portable_hashes.items()))
    except OSError as exc:
        all_issues.append(Issue("INPUT_FINGERPRINT_FAILED", "ERROR", str(exc)))
    result.rule_version = str((rule_doc or {}).get("schema_version") or "") or None
    result.checked_count = rule_count
    result.expected_count = rule_count
    result.metrics = {
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_skill_version": info.get("manifest_version"),
        "project_id": info.get("project_id"),
        "package_version": info.get("package_version"),
        "manifest_version_matches_package": info.get("package_version") == info.get("manifest_version"),
        "rules_checked": rule_count,
        "unit_aliases_checked": len(alias_map),
        "release_mode": bool(release),
        "as_of_date": as_of.isoformat(),
        "configuration_placeholders": config_placeholders[:100],
        "release_eligible": bool(info.get("release_eligible")) and not config_placeholders
            and rule_doc is not None
            and all(isinstance(r, dict) and str(r.get("approval_status") or "").strip().upper() in RULE_APPROVED_STATES
                    for r in (rule_doc.get("rules") if isinstance(rule_doc.get("rules"), list) else []))
            and not any(i.severity in {"FAIL", "ERROR"} for i in all_issues),
        "project_status": info.get("project_status"),
        "contract_approval": info.get("contract_approval"),
        "drawing_status": info.get("drawing_status"),
    }
    for issue in all_issues:
        result.add(issue)
    result.finalize(executed=True)
    payload = result.to_dict()
    payload["metrics"]["release_eligible"] = bool(payload["metrics"].get("release_eligible")) and result.status == Status.PASS
    if output_path:
        atomic_write_json(output_path, payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True, help="Project manifest JSON")
    ap.add_argument("--project-root", default=".", help="Project root for declared relative paths")
    ap.add_argument("--package-root", default=str(Path(__file__).resolve().parents[1]), help="Skill package root containing VERSION")
    ap.add_argument("--rules", default="", help="Override the rule registry path (otherwise use the manifest)")
    ap.add_argument("--release", action="store_true", help="Apply production release approval gates")
    ap.add_argument("--json-out", default="", help="Write machine-readable GateResult JSON")
    ap.add_argument("--as-of", default="", help="Evaluation date YYYY-MM-DD; defaults to the local current date")
    ap.add_argument("--selftest", action="store_true", help="Run deterministic positive/negative self-tests")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    try:
        manifest_path = Path(args.manifest).resolve()
        project_root = Path(args.project_root).resolve()
        package_root = Path(args.package_root).resolve()
        rules_path = Path(args.rules).resolve() if args.rules else None
        out_path = Path(args.json_out).resolve() if args.json_out else None
        as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
        payload = validate(manifest_path, project_root, package_root, rules_path=rules_path,
                           release=args.release, output_path=out_path, as_of=as_of)
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    status = payload.get("status")
    if status == "PASS":
        print(f"PROJECT CONFIG RESULT: PASS; release_eligible={payload.get('metrics', {}).get('release_eligible', False)}")
        return 0
    print(f"PROJECT CONFIG RESULT: {status}")
    return 1 if status in {"WARN", "FAIL"} else 2


def selftest() -> int:
    import tempfile
    from copy import deepcopy
    base = Path(__file__).resolve().parents[1] / "examples" / "e2e_demo"
    manifest_path = base / "reference" / "project_manifest.json"
    if not manifest_path.is_file():
        print("SELFTEST BLOCKED: synthetic fixture not found")
        return 2
    positive = validate(manifest_path, base, Path(__file__).resolve().parents[1])
    if positive.get("status") != "PASS" or positive.get("metrics", {}).get("rules_checked", 0) < 1:
        print("SELFTEST FAIL: valid synthetic fixture rejected")
        print(json.dumps(positive, ensure_ascii=False, indent=2))
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        bad_manifest = deepcopy(json.loads(manifest_path.read_text(encoding="utf-8")))
        bad_manifest["software_baseline"]["skill_version"] = "0.0.0-stale"
        bad_path = tmp_root / "manifest.json"
        bad_path.write_text(json.dumps(bad_manifest, ensure_ascii=False), encoding="utf-8")
        bad = validate(bad_path, base, Path(__file__).resolve().parents[1])
        if not any(i.get("code") == "VERSION_DRIFT" for i in bad.get("issues", [])):
            print("SELFTEST FAIL: stale package version not caught")
            return 1
        bad_rules = json.loads((base / "reference" / "measurement_rule_registry.json").read_text(encoding="utf-8"))
        bad_rules["rules"].append(deepcopy(bad_rules["rules"][0]))
        rules_path = tmp_root / "rules.json"
        rules_path.write_text(json.dumps(bad_rules, ensure_ascii=False), encoding="utf-8")
        dup = validate(manifest_path, base, Path(__file__).resolve().parents[1], rules_path=rules_path)
        if not any(i.get("code") == "DUPLICATE_RULE_ID" for i in dup.get("issues", [])):
            print("SELFTEST FAIL: duplicate rule ID not caught")
            return 1
        bad_rule = json.loads((base / "reference" / "measurement_rule_registry.json").read_text(encoding="utf-8"))
        bad_rule["rules"][0]["unit"] = "CM"
        rules_path.write_text(json.dumps(bad_rule, ensure_ascii=False), encoding="utf-8")
        amb = validate(manifest_path, base, Path(__file__).resolve().parents[1], rules_path=rules_path)
        if not any(i.get("code") == "RULE_UNIT_INVALID" for i in amb.get("issues", [])):
            print("SELFTEST FAIL: ambiguous unit not caught")
            return 1
    print("SELFTEST: PASS (manifest/rule validation, version drift, duplicate IDs, ambiguous units)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
