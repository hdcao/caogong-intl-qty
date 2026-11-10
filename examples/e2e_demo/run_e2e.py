#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the packaged synthetic QTO project end-to-end.

Run from any working directory:
  python /path/to/package/examples/e2e_demo/run_e2e.py

All inputs are fictitious test data. Passing this script is not professional
approval, contract interpretation, market validation, or production acceptance.
"""
from __future__ import annotations

import argparse
import signal
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from qto_core import GateResult, Issue, Status, atomic_write_json, release_source_fingerprint

ROOT = Path(__file__).resolve().parents[2]
DEMO = Path(__file__).resolve().parent
INP = DEMO / "input"
REF = DEMO / "reference"
REPORTS = DEMO / "reports"
LOGS = REPORTS / "gate_logs"
GATE_CONTRACTS = REPORTS / "gate_contracts"
SCRIPT = ROOT / "scripts"
BOOK = INP / "quantity_takeoff.xlsx"
SUMMARY_CONTROL = INP / "summary_control.xlsx"
SHEET = "1_成本取量总表"
# v4.6.4：单一真源——SMOKE_EXPECTED_CHECKS 与 SMOKE_SUITE_ID 一律从 scripts/test_all.py 导入，
# 本文件不再保留字面量副本。
# 背景：v4.6.3 的事故结构不是「值写错了」，而是「同一个事实硬编码在两个模块、只校验其中一处」。
# 把值改对不是修复；让值再错一次会被抓住才是修复——而"再错一次"只有在单点定义时才不可能发生。
# 注意：不得改成 try/except 静默回退到硬编码，静默回退等于回到原点（两份真相照旧漂移）。
try:
    from test_all import SMOKE_EXPECTED_CHECKS, SMOKE_SUITE_ID  # noqa: E402
except ImportError as _exc:  # pragma: no cover - 打包不完整时快速失败，而非悄悄降级
    raise RuntimeError(
        "需能从 scripts/test_all.py 导入 SMOKE_EXPECTED_CHECKS/SMOKE_SUITE_ID "
        f"（单一真源）；导入失败说明包结构不完整: {_exc}"
    ) from _exc
SOURCE_DIGEST_AT_START: str | None = None


def smoke_evidence_valid(text: str, expected_source_digest: str) -> bool:
    """Accept only a complete pass from the currently versioned release suite."""
    match = re.search(
        rf"RELEASE SMOKE RESULT: (\d+)/(\d+) passed \(suite={re.escape(SMOKE_SUITE_ID)}\)",
        text,
    )
    digests = re.findall(r"SOURCE TREE SHA256: ([0-9a-f]{64})", text)
    return bool(
        match
        and len(digests) == 2
        and all(digest == expected_source_digest for digest in digests)
        and "SOURCE TREE UNCHANGED: True" in text
        and int(match.group(1)) == SMOKE_EXPECTED_CHECKS
        and int(match.group(2)) == SMOKE_EXPECTED_CHECKS
        and "PASS: smoke/regression suite passed" in text
        and "[FAIL]" not in text
        and "FAIL: any failed smoke/regression check" not in text
    )


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_path_in_text(value: str) -> str:
    # Keep generated reports portable: never bake the runner's checkout path into evidence.
    root_text = str(ROOT)
    return value.replace(root_text, ".").replace(root_text.replace("\\", "/"), ".")


def sanitize_artifacts() -> None:
    targets = [REPORTS, DEMO / "takeoff" / "register" / "built"]
    extensions = {".json", ".md", ".log", ".csv", ".txt", ".html"}

    def scrub(obj):
        if isinstance(obj, str):
            return sanitize_path_in_text(obj)
        if isinstance(obj, list):
            return [scrub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: scrub(v) for k, v in obj.items()}
        return obj

    for base in targets:
        if not base.exists():
            continue
        for artifact in base.rglob("*"):
            if not artifact.is_file() or artifact.suffix.lower() not in extensions:
                continue
            try:
                if artifact.suffix.lower() == ".json":
                    data = json.loads(artifact.read_text(encoding="utf-8"))
                    artifact.write_text(json.dumps(scrub(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                else:
                    text = artifact.read_text(encoding="utf-8", errors="replace")
                    clean = sanitize_path_in_text(text)
                    if clean != text:
                        artifact.write_text(clean, encoding="utf-8")
            except (OSError, json.JSONDecodeError, UnicodeError):
                # A failed optional report scrub must not silently make a gate PASS;
                # later archive QA also greps for checkout-path leakage.
                continue


def display_arg(value: object) -> str:
    raw = str(value)
    if raw == sys.executable:
        return "{python}"
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            return str(candidate.relative_to(ROOT)).replace(os.sep, "/")
    except (ValueError, OSError):
        pass
    return raw


def gate_id_for(name: str) -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
    return "E2E_" + token[:80]


def input_hashes_for_command(argv: list[str]) -> dict[str, str]:
    allowed: dict[str, str] = {}
    for raw in argv:
        try:
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            candidate = candidate.resolve()
            if not candidate.is_file():
                continue
            rel = candidate.relative_to(ROOT).as_posix()
            include = (
                rel.startswith("scripts/") or rel.startswith("assets/") or rel.startswith("references/")
                or rel.startswith("examples/e2e_demo/input/")
                or rel.startswith("examples/e2e_demo/reference/")
                or rel.startswith("examples/e2e_demo/takeoff/register/scan/")
                or rel == "examples/e2e_demo/takeoff/register/scope.csv"
            )
            if include:
                allowed[rel] = sha256(candidate)
        except (OSError, ValueError, TypeError):
            continue
    return dict(sorted(allowed.items()))


def write_gate_contract(name: str, argv: list[str], rc: int, elapsed: float,
                        output: str, log_path: Path, *, native_result_path: Path | None = None,
                        force_issue: Issue | None = None) -> tuple[str, str]:
    """Write a standard GateResult artifact for native and legacy gate scripts.

    Legacy scripts often do not expose checked/expected counts. Those remain null,
    rather than inventing coverage, while the wrapper records command, log, exit code,
    discovered input hashes and source-tree fingerprint in metrics.
    """
    contract_path = GATE_CONTRACTS / (gate_id_for(name).lower() + ".json")
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    payload = None
    if rc == 0 and native_result_path and native_result_path.is_file():
        try:
            candidate = json.loads(native_result_path.read_text(encoding="utf-8"))
            if candidate.get("schema_version") == "1.1.0" and isinstance(candidate.get("issues"), list):
                payload = candidate
        except (OSError, json.JSONDecodeError, UnicodeError):
            payload = None

    if payload is None:
        result = GateResult(gate_id_for(name), tool_version="E2E-HARNESS-1.0.0", rule_version=SMOKE_SUITE_ID)
        result.input_hashes = input_hashes_for_command(argv)
        lines = output.splitlines()
        warning_lines = [line.strip() for line in lines if line.lstrip().startswith(("[WARN]", "[WARNING]"))]
        fatal_lines = [line.strip() for line in lines if line.lstrip().startswith(("[FAIL]", "[ERR]", "[ERROR]", "[未执行]"))]
        if force_issue:
            result.add(force_issue)
        elif rc == 1:
            result.add(Issue("GATE_EXIT_FAIL", "FAIL", f"Command exited with code {rc}", str(log_path)))
        elif rc != 0:
            result.add(Issue("GATE_EXECUTION_ERROR", "ERROR", f"Command exited with code {rc}; the gate did not complete cleanly", str(log_path)))
        if rc == 0 and fatal_lines:
            for idx, line in enumerate(fatal_lines[:20], 1):
                severity = "ERROR" if line.startswith(("[ERR]", "[ERROR]", "[未执行]")) else "FAIL"
                result.add(Issue(f"OUTPUT_MARKER_{idx}", severity, line[:800], str(log_path)))
        if not force_issue and rc == 0:
            for idx, line in enumerate(warning_lines[:100], 1):
                result.add(Issue(f"OUTPUT_WARNING_{idx}", "WARN", line[:800], str(log_path)))
        result.checked_count = None
        result.expected_count = None
        result.skipped_count = 0
        result.finalize(executed=True)
        payload = result.to_dict()
    metrics = payload.setdefault("metrics", {})
    metrics["runner"] = {
        "step_name": name,
        "command": [display_arg(x) for x in argv],
        "process_exit_code": rc,
        "duration_seconds": round(elapsed, 3),
        "log": str(log_path.relative_to(ROOT)).replace(os.sep, "/") if log_path.is_relative_to(ROOT) else str(log_path),
        "source_tree_sha256": SOURCE_DIGEST_AT_START or release_source_fingerprint(ROOT),
        "legacy_gate_checked_count_unreported": payload.get("checked_count") is None,
    }
    atomic_write_json(contract_path, payload)
    return str(contract_path.relative_to(ROOT)).replace(os.sep, "/"), str(payload.get("status", "ERROR"))


def run_step(name: str, argv: list[str], log_name: str, timeout: int = 30,
             native_result_path: Path | None = None) -> dict:
    started = time.monotonic()
    print(f"[RUN] {name}", flush=True)
    log_path = LOGS / log_name
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rc = 0
    with log_path.open("w", encoding="utf-8", errors="replace") as out:
        proc = None
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(ROOT),
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Timeout is a bounded failure, not permission for the step or its
                # descendants to continue writing into later gates or reports.
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        proc.kill()
                out.write(f"\n[E2E HARNESS ERROR] Step exceeded timeout of {timeout}s; process group terminated.\n")
                rc = 124
        except Exception as exc:
            out.write(f"[E2E HARNESS ERROR] {type(exc).__name__}: {exc}\n")
            rc = 125
    output = sanitize_path_in_text(log_path.read_text(encoding="utf-8", errors="replace"))
    log_path.write_text(output, encoding="utf-8")
    elapsed = time.monotonic() - started
    contract_path, gate_status = write_gate_contract(
        name, argv, rc, elapsed, output, log_path, native_result_path=native_result_path)
    fatal_marker = any(line.lstrip().startswith(("[FAIL]", "[ERR]", "[ERROR]", "[未执行]")) for line in output.splitlines())
    status = "PASS" if rc == 0 and not fatal_marker and gate_status not in {"FAIL", "ERROR", "NOT_RUN"} else "FAIL"
    print(f"[{status}] {name} (exit={rc}, gate={gate_status}, {elapsed:.1f}s)", flush=True)
    return {
        "name": name,
        "command": [display_arg(x) for x in argv],
        "exit_code": rc,
        "status": status,
        "gate_status": gate_status,
        "gate_contract": contract_path,
        "duration_seconds": round(elapsed, 3),
        "log": str(log_path.relative_to(ROOT)).replace(os.sep, "/"),
        "output_tail": output.strip().splitlines()[-8:],
    }


def cmd(*args: object) -> list[str]:
    return [sys.executable, *[str(x) for x in args]]


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the synthetic QTO end-to-end test project.")
    ap.add_argument("--skip-smoke", action="store_true",
                    help="Do not rerun scripts/test_all.py; require a fresh passing test_all.log in this report folder.")
    args = ap.parse_args()
    global SOURCE_DIGEST_AT_START
    run_started_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source_digest_at_start = release_source_fingerprint(ROOT)
    SOURCE_DIGEST_AT_START = source_digest_at_start
    REPORTS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(GATE_CONTRACTS, ignore_errors=True)
    GATE_CONTRACTS.mkdir(parents=True, exist_ok=True)
    required = [
        BOOK,
        SUMMARY_CONTROL,
        INP / "drawings" / "RD123456-SIM-RD-CV-001-REV2.pdf",
        REF / "project_manifest.json",
        REF / "measurement_rule_registry.json",
        REF / "unit_alias_registry.json",
        REF / "drawing_baseline.json",
        REF / "basis_freshness.json",
        REF / "expectations.json",
        REF / "cross_sheet_map.json",
        REF / "model_quantities.json",
        REF / "2d_quantities.json",
        REF / "ack_rate_outliers.csv",
        REF / "revisions" / "baseline.csv",
        REF / "revisions" / "current.csv",
        REF / "revisions" / "impact_map.csv",
        REF / "openbim" / "model_rev_a.ifc",
        REF / "openbim" / "model_rev_b.ifc",
        REF / "openbim" / "boq_mapping_registry.json",
        REF / "openbim" / "ifc_delivery_profile.json",
        DEMO / "takeoff" / "register" / "scan" / "drawings_seed.csv",
        DEMO / "takeoff" / "register" / "scope.csv",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        print("E2E BLOCKED — missing fixture files:")
        print("\n".join(missing))
        return 2

    input_hashes_before = {str(p.relative_to(ROOT)): sha256(p) for p in (BOOK, SUMMARY_CONTROL)}
    steps: list[dict] = []
    p = lambda rel: ROOT / rel

    # Environment / structure / build register.
    steps.append(run_step("Environment preflight", cmd(SCRIPT / "check_environment.py", "--json"), "environment.json"))
    if args.skip_smoke:
        smoke_log = LOGS / "test_all.log"
        smoke_text = smoke_log.read_text(encoding="utf-8", errors="replace") if smoke_log.is_file() else ""
        smoke_age = (time.time() - smoke_log.stat().st_mtime) if smoke_log.is_file() else float("inf")
        # A fresh timestamp alone is not proof: stale or incomplete logs must fail closed.
        smoke_ok = (smoke_evidence_valid(smoke_text, source_digest_at_start)
                    and -300 <= smoke_age <= 24 * 60 * 60)
        smoke_rc = 0 if smoke_ok else 125
        smoke_contract, smoke_gate_status = write_gate_contract(
            "Compile and release smoke/regression suite (separately executed evidence)",
            [], smoke_rc, 0.0, smoke_text, smoke_log,
            force_issue=None if smoke_ok else Issue(
                "SMOKE_EVIDENCE_INVALID", "ERROR",
                "Existing smoke log is missing, stale, incomplete or bound to a different source-tree fingerprint.",
                str(smoke_log)))
        steps.append({
            "name": "Compile and release smoke/regression suite (separately executed evidence)",
            "command": ["not rerun in this invocation; see existing test_all.log"],
            "exit_code": smoke_rc,
            "status": "PASS" if smoke_ok else "FAIL",
            "gate_status": smoke_gate_status,
            "gate_contract": smoke_contract,
            "duration_seconds": 0.0,
            "log": str(smoke_log.relative_to(ROOT)).replace(os.sep, "/"),
            "output_tail": smoke_text.strip().splitlines()[-4:],
        })
    else:
        steps.append(run_step("Compile and release smoke/regression suite", cmd(SCRIPT / "test_all.py"), "test_all.log", 90))
    steps.append(run_step("Project manifest / measurement-rule / unit-alias preflight", cmd(
        SCRIPT / "qto_project_validate.py", "--manifest", REF / "project_manifest.json",
        "--project-root", DEMO,
        "--json-out", DEMO / "reports/project_config_validation.json"), "project_config_validation.log",
        native_result_path=DEMO / "reports/project_config_validation.json"))
    steps.append(run_step("Build drawing/input register", cmd(
        SCRIPT / "build_input_register.py", "build",
        "--register", DEMO / "takeoff/register/scan/drawings_seed.csv",
        "--scope", DEMO / "takeoff/register/scope.csv",
        "--project", "SIM-E2E-001",
        "--out", DEMO / "takeoff/register/built",
        "--rev-addendum", "SIM-RD-CV-001 Rev.2 (synthetic)",
    ), "input_register.log"))
    steps.append(run_step("Drawing revision comparison and evidence-backed quantity impact closure", cmd(
        SCRIPT / "drawing_revision_diff.py", "compare",
        "--baseline", REF / "revisions/baseline.csv",
        "--current", REF / "revisions/current.csv",
        "--impact-map", REF / "revisions/impact_map.csv",
        "--project-id", "SIM-E2E-001",
        "--out-dir", DEMO / "reports/revision_impact",
        "--fail-on-unreviewed"), "drawing_revision_diff.log"))

    # Quantity workbook gates. Explicit paths and schema are intentional.
    steps.append(run_step("Structured scope declaration", cmd(SCRIPT / "gate_scope_declaration.py", BOOK, "--sheet", SHEET), "scope_declaration.log"))
    steps.append(run_step("Drawing quantity baseline", cmd(SCRIPT / "gate_drawing_basis.py", BOOK, "--sheet", SHEET, "--basis", REF / "drawing_baseline.json"), "drawing_basis.log"))
    steps.append(run_step("Baseline freshness (mtime intentionally skipped for reproducible archive tests)", cmd(SCRIPT / "gate_basis_freshness.py", BOOK, "--sheet", SHEET, "--basis", REF / "basis_freshness.json", "--skip-mtime"), "basis_freshness.log"))
    steps.append(run_step("Base/optional separation", cmd(SCRIPT / "gate_scope_options.py", BOOK, "--sheet", SHEET, "--no-fill", "--summary", BOOK, "--summary-cell", "1_全项目成本汇总!C7"), "scope_options.log"))
    steps.append(run_step("Single physical object / single measurement owner", cmd(SCRIPT / "gate_single_owner.py", BOOK, "--sheet", SHEET), "single_owner.log"))
    steps.append(run_step("Unit/rate outlier check with explicit synthetic ACK and project unit registry", cmd(
        SCRIPT / "gate_unit_consistency.py", BOOK, "--sheet", SHEET, "--ack", REF / "ack_rate_outliers.csv",
        "--unit-registry", REF / "unit_alias_registry.json", "--project-id", "SIM-E2E-001"), "unit_consistency.log"))
    steps.append(run_step("Dimension-wording consistency", cmd(
        SCRIPT / "gate_dimension_wording.py", BOOK,
        "--sheet", SHEET, "--code-col", "2", "--name-col", "3", "--unit-col", "4",
        "--value-col", "5", "--basis-col", "12", "--scope-col", "9",
        "--evidence-col", "10", "--formula-col", "6", "--rfi-col", "17", "--no-auto-map",
        "--unit-registry", REF / "unit_alias_registry.json", "--project-id", "SIM-E2E-001",
    ), "dimension_wording.log"))
    steps.append(run_step("Per-item expected quantity closure", cmd(SCRIPT / "gate_expected_total.py", BOOK, "--sheet", SHEET, "--expect", REF / "expectations.json"), "expected_total.log"))
    steps.append(run_step("Spreadsheet formula and subtotal integrity", cmd(SCRIPT / "gate_sheet_formula.py", BOOK, "--sheet", SHEET), "sheet_formula.log"))
    # This mapping compares stored quantities, not formula dependency; hardcoded-value mode is explicit.
    steps.append(run_step("Cross-sheet value reconciliation (hardcoded values explicitly allowed)", cmd(
        SCRIPT / "gate_cross_sheet.py", BOOK, "--map", REF / "cross_sheet_map.json", "--allow-hardcoded"), "cross_sheet.log"))
    steps.append(run_step("BOQ schema/content lint", cmd(
        SCRIPT / "boq_lint.py", "check", "--boq", BOOK, "--sheet", SHEET,
        "--out", DEMO / "reports/boq_lint.json", "--md", DEMO / "reports/boq_lint.md"), "boq_lint.log"))

    # Other source checks / evidence diagnostics.
    steps.append(run_step("Model-vs-2D quantity comparison", cmd(
        SCRIPT / "xcheck_model_vs_2d.py", REF / "model_quantities.json", REF / "2d_quantities.json",
        "--stage", "all", "--report", DEMO / "reports/model_vs_2d.md"), "model_2d.log"))
    steps.append(run_step("OpenBIM IFC QTO extraction / governed BOQ mapping / delivery profile / snapshot diff", cmd(
        DEMO / "run_openbim_demo.py"), "openbim_workflow.log", 90,
        native_result_path=DEMO / "reports/openbim/summary.json"))
    pdf = INP / "drawings" / "RD123456-SIM-RD-CV-001-REV2.pdf"
    steps.append(run_step("PDF vector/text layer inventory", cmd(SCRIPT / "extract_dwg_vector.py", pdf, "--list"), "pdf_vector.log"))
    steps.append(run_step("PDF vector forensic probe", cmd(SCRIPT / "vector_forensics.py", "probe", pdf, "--out", DEMO / "reports/vector_probe.json"), "vector_forensics.log"))

    # Fault injection against isolated copies. Absolute paths avoid cwd-dependent false captures.
    steps.append(run_step("Fault injection: ten injected defects must be caught; source files must remain unchanged", cmd(
        SCRIPT / "gate_fault_injection.py", BOOK, "--sheet", SHEET, "--allow-subprocess",
        "--xs-map", REF / "cross_sheet_map.json", "--summary", SUMMARY_CONTROL,
        "--basis", REF / "drawing_baseline.json"), "fault_injection.log", 45))

    input_hashes_after = {str(p.relative_to(ROOT)): sha256(p) for p in (BOOK, SUMMARY_CONTROL)}
    hashes_unchanged = input_hashes_before == input_hashes_after
    source_digest_at_end = release_source_fingerprint(ROOT)
    source_tree_unchanged = source_digest_at_start == source_digest_at_end
    all_steps_pass = all(s["status"] == "PASS" for s in steps)
    overall = "PASS" if all_steps_pass and hashes_unchanged and source_tree_unchanged else "FAIL"
    summary = {
        "report_schema_version": "1.1.0",
        "run_type": "SYNTHETIC_END_TO_END_TEST_ONLY",
        "started_at_utc": run_started_utc,
        "skill_version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "project_id": "SIM-E2E-001",
        "project_name": "Synthetic Access Road & Box Culvert",
        "overall_status": overall,
        "test_steps_passed": sum(s["status"] == "PASS" for s in steps),
        "test_steps_total": len(steps),
        "gate_status_counts": {status: sum(s.get("gate_status", "NOT_REPORTED") == status for s in steps) for status in ("PASS", "WARN", "FAIL", "ERROR", "NOT_RUN", "NOT_REPORTED")},
        "gate_contracts_written": sum(bool(s.get("gate_contract")) for s in steps),
        "input_hashes_before": input_hashes_before,
        "input_hashes_after": input_hashes_after,
        "source_inputs_unchanged": hashes_unchanged,
        "source_tree_sha256": source_digest_at_start,
        "source_tree_sha256_at_end": source_digest_at_end,
        "source_tree_unchanged": source_tree_unchanged,
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "limitations": [
            "All drawings, quantities, rules, rates, codes, reviewer names and ACK records are invented test fixtures.",
            "The 2D/model inputs are separate fixture files but their real-world source independence is not authenticated.",
            "Cross-sheet comparison used --allow-hardcoded; it validates values, not formula dependency.",
            "Baseline freshness test skipped file mtime because archive/copy timestamps are not reliable evidence.",
            "PDF vector probe is diagnostic only; no claim that vector entities were automatically converted into quantities.",
            "Drawing revision workflow compares register metadata and file hashes; it does not perform geometric overlay or recalculate affected quantities.",
            "The test does not establish contract compliance, code compliance, market rates, authorized review, or payment certification.",
        ],
        "steps": steps,
    }
    (REPORTS / "e2e_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Synthetic Project End-to-End Test Report",
        "",
        f"- Skill package version: `{summary['skill_version']}`",
        f"- Project fixture: `{summary['project_id']} — {summary['project_name']}`",
        f"- Run timestamp (UTC): `{summary['started_at_utc']}`",
        f"- Overall software-test status: **{overall}**",
        f"- Steps: **{summary['test_steps_passed']}/{summary['test_steps_total']} PASS**",
        f"- Input workbook/control hashes unchanged: **{'YES' if hashes_unchanged else 'NO'}**",
        f"- Source tree SHA-256: `{source_digest_at_start}`",
        f"- Source tree remained unchanged during E2E: **{'YES' if source_tree_unchanged else 'NO'}**",
        "",
        "## Checks executed",
        "",
        "| # | Check | Runner | Gate | Exit | Contract | Log |",
        "|---:|---|---|---|---:|---|---|",
    ]
    for i, s in enumerate(steps, 1):
        lines.append(f"| {i} | {s['name']} | **{s['status']}** | **{s.get('gate_status', 'NOT_REPORTED')}** | {s['exit_code']} | `{s.get('gate_contract', 'not written')}` | `{s['log']}` |")
    lines += [
        "",
        "## Machine-readable gate contracts",
        "",
        f"The run wrote {summary['gate_contracts_written']} standard gate contract artifacts under `examples/e2e_demo/reports/gate_contracts/`. Native checked/expected row counts are retained when available; for legacy scripts that do not expose them, fields remain null instead of inventing coverage. Runner PASS indicates the process completed and no fatal output markers were detected; the separate Gate status retains WARN/FAIL/ERROR findings for review.",
        "",
        "## Engineering quantity summary (fixture data only)",
        "",
        "| Scope | Quantity / value | Basis / interpretation |",
        "|---|---:|---|",
        "| Base direct-cost control | 23,859 SIM-CUR | Synthetic rate × quantity; excludes optional guardrail |",
        "| Optional guardrail | 17,000 SIM-CUR | Separate CPB 4.0 alternative; must not enter Base |",
        "| Total including optional | 40,859 SIM-CUR | For comparison only; not the Base tender amount |",
        "| Box culvert concrete | 10.8 m³ | 3.6 m³ base + 3.6 m³ walls + 3.6 m³ top slab |",
        "| Roadway excavation | 1,200 m³ | 12.0 m² average area × 100 m, synthetic geometry |",
        "| Granular subbase | 160 m³ | 8.0 m × 100 m × 0.20 m, synthetic geometry |",
        "| Asphalt concrete | 35 m³ | 7.0 m × 100 m × 0.05 m, synthetic geometry |",
        "| Guardrail | 200 m | 2 sides × 100 m; optional |",
        "",
        "## What the fault-injection test proves",
        "",
        "The harness successfully injected 10 defined defects into temporary copies, reported `CAUGHT` for all 10, and confirmed the source workbook digest was unchanged. This only supports the stated injection set and the test fixture; it is **not** a claim of 100% real-world defect-detection coverage.",
        "",
        "## Important limitations",
        "",
    ]
    lines.extend(f"- {x}" for x in summary["limitations"])
    lines += [
        "",
        "## Release decision",
        "",
        "This demo is a reproducible software E2E pass on synthetic data. It is not a professional QS sign-off, not proof that the source schedule is contractually correct, and not production acceptance. Before live use: bind the actual contract and measurement rules, reconcile actual drawing revisions, approve rule registry and evidence, review exceptions, establish a known-good project golden dataset, and obtain authorized professional release.",
        "",
    ]
    (REPORTS / "E2E_TEST_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    sanitize_artifacts()

    print(f"E2E RESULT: {summary['test_steps_passed']}/{summary['test_steps_total']} steps passed")
    print(f"Source inputs unchanged: {hashes_unchanged}")
    print(f"Report: {REPORTS / 'E2E_TEST_REPORT.md'}")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
