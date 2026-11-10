#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drawing register revision comparison and quantity-impact gate.

This tool compares drawing-register snapshots; it does NOT perform pixel-level
PDF/CAD overlay, interpret engineering changes, or recalculate quantities.
Changed drawings must be mapped to affected quantity rows or an approved
no-impact disposition before a project release gate can pass.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

try:
    from qto_core import atomic_write_json
except ImportError:  # pragma: no cover - supports direct import by external tools
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from qto_core import atomic_write_json

TOOL_VERSION = "1.1.0"
CLOSED_DISPOSITIONS = {"RECALCULATED", "NO_QUANTITY_IMPACT"}
METADATA_FIELDS = ("title", "discipline", "work_package", "part", "date", "scale", "source_type", "scope_status", "blocks")
SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"CSV 文件不存在：{path}")
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV 缺少表头：{path}")
        return [{str(k or "").strip(): str(v or "").strip() for k, v in row.items()} for row in reader]


def normalize_sheet(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).upper()


def index_snapshot(rows: list[dict[str, str]], label: str) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Index active rows. Duplicate active sheet IDs are kept as ambiguity markers."""
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for line_no, row in enumerate(rows, start=2):
        sheet = normalize_sheet(row.get("sheet", ""))
        if not sheet:
            raise ValueError(f"{label} 第 {line_no} 行缺少 sheet 图号")
        if not row.get("sha256"):
            row["_hash_missing"] = "true"
        groups[sheet].append(row)

    indexed: dict[str, dict[str, str]] = {}
    ambiguous: dict[str, str] = {}
    for sheet, group in groups.items():
        active = [r for r in group if (r.get("status", "").strip().lower() != "superseded")]
        if not active:
            # All entries are explicitly superseded; this sheet is not active in this snapshot.
            continue
        if len(active) != 1:
            ambiguous[sheet] = f"{label} 中 {sheet} 有 {len(active)} 条非 superseded 记录"
            continue
        row = dict(active[0])
        row["sheet"] = sheet
        indexed[sheet] = row
    return indexed, ambiguous


def parse_review_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def valid_sha256(row: dict[str, str] | None) -> bool:
    """A drawing row is eligible for change classification only with a real SHA-256."""
    if row is None:
        return True
    return bool(SHA_RE.fullmatch(str(row.get("sha256", "")).strip()))


def change_type(old: dict[str, str] | None, new: dict[str, str] | None) -> str:
    # Validate every present row BEFORE Added/Removed short-circuits. Otherwise a
    # newly added drawing with no hash could be marked ADDED and later released if
    # its impact map contains a syntactically complete no-impact disposition.
    if not valid_sha256(old) or not valid_sha256(new):
        return "AMBIGUOUS_MISSING_OR_INVALID_HASH"
    if old is None:
        return "ADDED"
    if new is None:
        return "REMOVED_FROM_CURRENT_REGISTER"
    old_hash, new_hash = old.get("sha256", "").lower(), new.get("sha256", "").lower()
    rev_changed = old.get("rev", "").strip() != new.get("rev", "").strip()
    content_changed = old_hash != new_hash
    if rev_changed and content_changed:
        return "REVISION_AND_CONTENT_CHANGED"
    if content_changed:
        return "CONTENT_CHANGED"
    if rev_changed:
        return "REVISION_LABEL_CHANGED_CONTENT_IDENTICAL"
    if any(old.get(k, "").strip() != new.get(k, "").strip() for k in METADATA_FIELDS):
        return "METADATA_CHANGED"
    return "UNCHANGED"


def is_changed(kind: str) -> bool:
    return kind != "UNCHANGED"


def map_impacts(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    result: dict[str, list[dict[str, str]]] = defaultdict(list)
    for line_no, row in enumerate(rows, start=2):
        sheet = normalize_sheet(row.get("sheet", ""))
        if not sheet:
            raise ValueError(f"impact-map 第 {line_no} 行缺少 sheet")
        row["sheet"] = sheet
        row["review_status"] = row.get("review_status", "PENDING").strip().upper() or "PENDING"
        result[sheet].append(row)
    return result


def assess_impact(sheet: str, rows: list[dict[str, str]]) -> tuple[str, list[str], list[dict[str, str]]]:
    """Return disposition, blockers, and normalized impact rows for a changed sheet."""
    blockers: list[str] = []
    normalized: list[dict[str, str]] = []
    if not rows:
        return "UNMAPPED", [f"{sheet} 变更后没有影响工程量映射或经批准的无影响声明"], []

    for idx, row in enumerate(rows, start=1):
        status = row.get("review_status", "PENDING").upper()
        qty_id = row.get("quantity_id", "").strip()
        reviewer = row.get("reviewer", "").strip()
        reviewed_at = row.get("reviewed_at", "").strip()
        evidence_ref = row.get("evidence_ref", "").strip()
        rationale = row.get("rationale", "").strip()
        if status not in CLOSED_DISPOSITIONS:
            blockers.append(f"{sheet} 影响项 #{idx} 状态未闭合：{status or 'PENDING'}")
        if status == "RECALCULATED" and not qty_id:
            blockers.append(f"{sheet} 已标记 RECALCULATED，但缺 quantity_id")
        if status == "NO_QUANTITY_IMPACT" and qty_id:
            blockers.append(f"{sheet} 标记 NO_QUANTITY_IMPACT 时 quantity_id 必须为空")
        if not reviewer:
            blockers.append(f"{sheet} 影响项 #{idx} 缺人工复核人")
        if not parse_review_date(reviewed_at):
            blockers.append(f"{sheet} 影响项 #{idx} reviewed_at 必须为 YYYY-MM-DD")
        if not evidence_ref:
            blockers.append(f"{sheet} 影响项 #{idx} 缺证据引用（图纸位置/算量行/RFI/复核记录）")
        if status == "NO_QUANTITY_IMPACT" and not rationale:
            blockers.append(f"{sheet} 无工程量影响声明缺少理由")
        normalized.append({
            "quantity_id": qty_id,
            "boq_code": row.get("boq_code", "").strip(),
            "item": row.get("item", "").strip(),
            "review_status": status,
            "reviewer": reviewer,
            "reviewed_at": reviewed_at,
            "evidence_ref": evidence_ref,
            "rationale": rationale,
        })
    if blockers:
        return "OPEN", blockers, normalized
    if all(r["review_status"] == "NO_QUANTITY_IMPACT" for r in normalized):
        return "CLOSED_NO_QUANTITY_IMPACT", [], normalized
    return "CLOSED_RECALCULATED_OR_REVIEWED", [], normalized


def markdown_report(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# Drawing Revision Impact Report",
        "",
        f"- Tool version: `{report['tool_version']}`",
        f"- Project: `{report.get('project_id') or 'NOT_SPECIFIED'}`",
        f"- Baseline snapshot SHA-256: `{report['snapshots']['baseline']['sha256']}`",
        f"- Current snapshot SHA-256: `{report['snapshots']['current']['sha256']}`",
        f"- Release review eligible: **{'YES' if report['release_review']['eligible'] else 'NO'}**",
        "",
        "## Summary",
        "",
        "| Change type | Count |",
        "|---|---:|",
    ]
    for kind, count in s.items():
        lines.append(f"| {kind} | {count} |")
    lines += ["", "## Sheet changes", "", "| Sheet | Change | Baseline rev | Current rev | Impact disposition | Impact rows | Blockers |", "|---|---|---|---|---|---:|---:|"]
    for c in report["changes"]:
        lines.append(
            f"| {c['sheet']} | {c['change_type']} | {c.get('baseline_rev','')} | {c.get('current_rev','')} | "
            f"{c['impact_disposition']} | {len(c['quantity_impacts'])} | {len(c['blockers'])} |"
        )
    if report["release_review"]["blockers"]:
        lines += ["", "## Release blockers", ""]
        lines.extend(f"- {b}" for b in report["release_review"]["blockers"])
    lines += [
        "",
        "## Scope limitations",
        "",
        "- This is a register/hash-based inventory comparison, not pixel-level PDF overlay or CAD semantic differencing.",
        "- A changed file hash does not identify the exact geometric change or prove that quantities changed.",
        "- An unchanged hash does not prove that the drawing baseline is contractually approved.",
        "- A cleared impact row requires an identified human reviewer and evidence reference; software does not sign off engineering quantities.",
        "",
    ]
    return "\n".join(lines)


def compare(args: argparse.Namespace) -> int:
    baseline_path, current_path, impact_path = Path(args.baseline), Path(args.current), Path(args.impact_map)
    base_rows, cur_rows = read_csv(baseline_path), read_csv(current_path)
    impact_rows = read_csv(impact_path)
    base, base_amb = index_snapshot(base_rows, "baseline")
    current, cur_amb = index_snapshot(cur_rows, "current")
    impacts = map_impacts(impact_rows)
    changes: list[dict[str, Any]] = []
    blockers: list[str] = []

    for sheet in sorted(set(base) | set(current) | set(base_amb) | set(cur_amb)):
        old, new = base.get(sheet), current.get(sheet)
        kind = "AMBIGUOUS_DUPLICATE_ACTIVE_ROWS" if sheet in base_amb or sheet in cur_amb else change_type(old, new)
        impact_disposition, sheet_blockers, mapped = "NOT_REQUIRED", [], []
        if is_changed(kind):
            impact_disposition, sheet_blockers, mapped = assess_impact(sheet, impacts.get(sheet, []))
            if kind.startswith("AMBIGUOUS"):
                sheet_blockers = [base_amb.get(sheet) or cur_amb.get(sheet) or f"{sheet} 状态不明确"] + sheet_blockers
            if kind.startswith("AMBIGUOUS_MISSING"):
                sheet_blockers.insert(0, f"{sheet} 缺少有效的 SHA-256，无法证明内容是否变化")
            blockers.extend(sheet_blockers)
        changes.append({
            "sheet": sheet,
            "change_type": kind,
            "baseline_rev": (old or {}).get("rev", ""),
            "current_rev": (new or {}).get("rev", ""),
            "baseline_sha256": (old or {}).get("sha256", ""),
            "current_sha256": (new or {}).get("sha256", ""),
            "baseline_file": (old or {}).get("file", ""),
            "current_file": (new or {}).get("file", ""),
            "impact_disposition": impact_disposition,
            "quantity_impacts": mapped,
            "blockers": sheet_blockers,
        })

    counts = Counter(c["change_type"] for c in changes)
    release_eligible = not blockers and not any(c["change_type"].startswith("AMBIGUOUS") for c in changes)
    report: dict[str, Any] = {
        "schema_version": "1.0.0",
        "tool": "drawing_revision_diff",
        "tool_version": TOOL_VERSION,
        "project_id": args.project_id or "",
        "snapshots": {
            "baseline": {"path": str(baseline_path), "sha256": file_sha256(baseline_path), "rows_read": len(base_rows), "active_sheets": len(base)},
            "current": {"path": str(current_path), "sha256": file_sha256(current_path), "rows_read": len(cur_rows), "active_sheets": len(current)},
            "impact_map": {"path": str(impact_path), "sha256": file_sha256(impact_path), "rows_read": len(impact_rows)},
        },
        "summary": dict(sorted(counts.items())),
        "changes": changes,
        "release_review": {"eligible": release_eligible, "blockers": blockers},
        "limitations": [
            "Register/hash comparison only; no geometric overlay or semantic drawing diff.",
            "Changed drawing does not automatically imply a quantity change; every changed sheet needs a documented disposition.",
            "Software validation is not an authorized professional sign-off.",
        ],
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out / "drawing_revision_impact.json", out / "drawing_revision_impact.md"
    atomic_write_json(json_path, report)
    md_path.write_text(markdown_report(report), encoding="utf-8")
    print(f"Revision comparison: {sum(counts.values())} sheet IDs; changed={sum(n for k,n in counts.items() if k != 'UNCHANGED')}; unchanged={counts.get('UNCHANGED', 0)}")
    print(f"Impact mapping: {'CLOSED' if release_eligible else 'OPEN'}; blockers={len(blockers)}")
    print(f"JSON: {json_path}\nMarkdown: {md_path}")
    if blockers:
        for b in blockers[:100]:
            print(f"[BLOCK] {b}")
    else:
        print("PASS: all changed drawing IDs have a closed, evidence-backed quantity-impact disposition.")
    return 1 if args.fail_on_unreviewed and not release_eligible else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compare drawing-register snapshots and trace changed sheets to quantity review dispositions.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compare", help="Compare baseline/current register CSVs and evaluate impact closure")
    p.add_argument("--baseline", required=True, help="Baseline drawing register snapshot CSV")
    p.add_argument("--current", required=True, help="Current drawing register snapshot CSV")
    p.add_argument("--impact-map", required=True, help="Sheet-to-quantity review mapping CSV")
    p.add_argument("--project-id", default="", help="Project identifier")
    p.add_argument("--out-dir", required=True, help="Output directory for JSON and Markdown reports")
    p.add_argument("--fail-on-unreviewed", action="store_true", help="Return exit 1 if a changed sheet is ambiguous or its impact review is not closed")
    p.set_defaults(func=compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, OSError, csv.Error, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
