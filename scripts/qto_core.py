#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared deterministic primitives for quantity-takeoff tools (core v3.2.1; package v4.4.0).

This module deliberately does not decide contractual measurement rules. It provides
strict numeric parsing, dimension-aware conversions, consistent issue/status types,
input fingerprints, and crash-safe output primitives shared by calculation tools.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

CORE_VERSION = "3.2.2"
GATE_RESULT_SCHEMA_VERSION = "1.1.0"

class QTOError(Exception):
    """Expected operational error that should be reported as NOT_RUN/ERROR."""

class DataValidationError(QTOError):
    pass

class RuleConflictError(QTOError):
    pass

class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    NOT_RUN = "NOT_RUN"
    ERROR = "ERROR"

@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    message: str
    location: Optional[str] = None
    evidence: Optional[str] = None
    rule_id: Optional[str] = None

@dataclass
class GateResult:
    """Machine-readable gate result with conservative lifecycle semantics.

    ``status`` answers whether the check passed; ``executed`` answers whether it
    actually ran. These are deliberately separate because an unexecuted gate is
    never a pass. Counts may be ``None`` when a legacy gate cannot report them.
    """
    gate_id: str
    status: Status = Status.NOT_RUN
    issues: list[Issue] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    input_hashes: dict[str, str] = field(default_factory=dict)
    rule_version: Optional[str] = None
    tool_version: str = CORE_VERSION
    executed: bool = False
    checked_count: Optional[int] = None
    expected_count: Optional[int] = None
    skipped_count: int = 0

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)
        severity = issue.severity.upper()
        status_by_severity = {
            "WARN": Status.WARN,
            "FAIL": Status.FAIL,
            "ERROR": Status.ERROR,
        }
        candidate = status_by_severity.get(severity)
        priority = {Status.NOT_RUN: 0, Status.PASS: 1, Status.WARN: 2,
                    Status.FAIL: 3, Status.ERROR: 4}
        # Findings can only escalate a status, never accidentally downgrade ERROR
        # to FAIL when another issue is added later.
        if candidate is not None and priority[candidate] > priority[self.status]:
            self.status = candidate

    def finalize(self, executed: bool = False) -> "GateResult":
        self.executed = bool(executed)
        if not self.executed:
            # An execution flag must never erase findings already collected. If the
            # gate truly did not run and has no findings, keep it explicitly NOT_RUN.
            if not self.issues:
                self.status = Status.NOT_RUN
        elif self.status == Status.NOT_RUN:
            self.status = Status.PASS
        if self.skipped_count < 0:
            self.add(Issue("INVALID_SKIPPED_COUNT", "ERROR", "skipped_count cannot be negative"))
            self.skipped_count = 0
        for key in ("checked_count", "expected_count"):
            value = getattr(self, key)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                self.add(Issue("INVALID_" + key.upper(), "ERROR", key + " must be a non-negative integer or null"))
                setattr(self, key, None)
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GATE_RESULT_SCHEMA_VERSION,
            "gate_id": self.gate_id,
            "status": self.status.value,
            "executed": self.executed,
            "checked_count": self.checked_count,
            "expected_count": self.expected_count,
            "skipped_count": self.skipped_count,
            "tool_version": self.tool_version,
            "rule_version": self.rule_version,
            "input_hashes": dict(sorted(self.input_hashes.items())),
            "metrics": _json_safe(self.metrics),
            "issues": [asdict(x) for x in self.issues],
        }

# Canonical factors to SI base units. Conversion is permitted only within a dimension.
_UNIT_MAP: dict[str, tuple[str, Decimal]] = {}
_AMBIGUOUS_UNITS = {
    "cm": "ambiguous legacy/project code: use 'm3'/'mc' for cubic metre or 'centimeter'/'厘米' for centimetre; if CM has a project-specific meaning, declare and approve it in the project unit-alias registry",
}
def _units(dimension: str, factor: str, *names: str) -> None:
    for name in names:
        key = str(name).casefold().replace("³", "3").replace("²", "2").replace("¹", "1")
        key = key.replace(" ", "").replace(".", "").replace("·", "")
        _UNIT_MAP[key] = (dimension, Decimal(factor))

_units("length", "1", "m", "metre", "meter", "metres", "meters", "米", "延米", "lm", "ml", "m.l", "m.l.")
_units("length", "0.001", "mm", "毫米")
_units("length", "0.01", "centimeter", "centimetre", "厘米")
_units("length", "1000", "km", "公里")
# International / imperial units commonly encountered in US, UK and mixed-unit drawings.
# Factors are exact definitions converted to the SI base unit for each dimension.
_units("length", "0.3048", "ft", "foot", "feet")
_units("length", "0.0254", "in", "inch", "inches")
_units("length", "0.9144", "yd", "yard", "yards")
_units("length", "1609.344", "mi", "mile", "miles")
_units("area", "1", "m2", "m²", "sqm", "sm", "sq", "平方米")
_units("area", "0.000001", "mm2", "mm²")
_units("area", "0.0001", "cm2", "cm²")
_units("area", "0.09290304", "ft2", "ft²", "sqft", "squarefoot", "squarefeet")
_units("area", "0.00064516", "in2", "in²", "sqin", "squareinch", "squareinches")
_units("area", "0.83612736", "yd2", "yd²", "sqyd", "squareyard", "squareyards")
_units("area", "4046.8564224", "acre", "acres")
_units("volume", "1", "m3", "m³", "cum", "cub", "cbm", "立方米", "mc")
_units("volume", "0.000000001", "mm3", "mm³")
_units("volume", "0.000001", "cm3", "cm³")
_units("volume", "0.028316846592", "ft3", "ft³", "cuft", "cubicfoot", "cubicfeet")
_units("volume", "0.000016387064", "in3", "in³", "cuin", "cubicinch", "cubicinches")
_units("volume", "0.764554857984", "yd3", "yd³", "cuyd", "cubicyard", "cubicyards")
_units("volume", "0.001", "l", "liter", "litre", "liters", "litres", "升")
_units("volume", "0.000001", "ml", "milliliter", "millilitre", "milliliters", "millilitres", "毫升")
_units("mass", "1", "kg", "公斤", "千克")
_units("mass", "1000", "t", "tonne", "tonnes", "metric ton", "吨", "mt")
_units("mass", "0.001", "g", "克")
_units("mass", "0.45359237", "lb", "lbs", "pound", "pounds")
_units("mass", "0.028349523125", "oz", "ounce", "ounces")
_units("time", "1", "s", "sec", "second", "seconds")
_units("time", "60", "min", "minute", "minutes")
_units("time", "3600", "h", "hr", "hrs", "hour", "hours")
_units("time", "86400", "day", "days", "d")
_units("count", "1", "ea", "each", "u", "un", "unit", "units", "unite", "unité", "ens", "ensemble", "no", "nr", "set", "个", "套", "根", "座", "片", "道", "处", "组", "榀", "幅", "跨", "孔", "肢")
_units("item", "1", "item", "ff", "forfait", "项")


def parse_decimal(value: Any, *, field_name: str = "value", allow_blank: bool = False,
                  decimal_comma: bool = False) -> Optional[Decimal]:
    """Parse a finite decimal without silently guessing locale or accepting booleans.

    Thousands separators are intentionally not guessed. With decimal_comma=True,
    only a single comma and no dot is accepted as decimal separator.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        if allow_blank:
            return None
        raise DataValidationError(f"{field_name}: value is blank")
    if isinstance(value, bool):
        raise DataValidationError(f"{field_name}: boolean is not a numeric quantity")
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise DataValidationError(f"{field_name}: NaN/Infinity is forbidden")
        number = Decimal(str(value))
    else:
        raw = str(value).strip().replace("\u00a0", "").replace("\u202f", "")
        if decimal_comma and "," in raw and "." not in raw and raw.count(",") == 1:
            raw = raw.replace(",", ".")
        if "," in raw:
            raise DataValidationError(f"{field_name}: ambiguous comma separator in {value!r}; declare locale explicitly")
        try:
            number = Decimal(raw)
        except (InvalidOperation, ValueError):
            raise DataValidationError(f"{field_name}: invalid decimal {value!r}")
    if not number.is_finite():
        raise DataValidationError(f"{field_name}: NaN/Infinity is forbidden")
    return number


def parse_nonnegative(value: Any, *, field_name: str = "value", allow_zero: bool = True) -> Decimal:
    number = parse_decimal(value, field_name=field_name)
    assert number is not None
    if number < 0 or (not allow_zero and number == 0):
        op = ">= 0" if allow_zero else "> 0"
        raise DataValidationError(f"{field_name}: must be {op}")
    return number


def normalize_unit(unit: Any) -> str:
    raw = str(unit or "").strip().casefold()
    raw = raw.replace("³", "3").replace("²", "2").replace("¹", "1")
    raw = raw.replace("·", "").replace(" ", "").replace(".", "")
    return raw


def load_unit_alias_registry(path: os.PathLike[str] | str, *, project_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Load explicitly approved, project-scoped unit aliases from a JSON registry.

    An alias does not alter global unit meanings. It is active only when it has an
    approval identity/date and source, the registry is correctly scoped, and its
    canonical target is an unambiguous built-in unit. This makes legacy values such
    as ``CM`` usable only when the project has explicitly governed that meaning.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise DataValidationError(f"cannot read unit-alias registry {path!s}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != "1.0.0":
        raise DataValidationError("unit-alias registry must be an object with schema_version '1.0.0'")
    scoped_project = str(data.get("project_id") or "").strip()
    if not scoped_project:
        raise DataValidationError("unit-alias registry must declare project_id; use a project-specific empty registry when no aliases are needed")
    if project_id and project_id != scoped_project:
        raise DataValidationError(f"unit-alias registry belongs to project {scoped_project!r}, not {project_id!r}")
    context = data.get("context")
    if not isinstance(context, dict) or not any(str(context.get(k) or "").strip() for k in ("jurisdiction", "language")):
        raise DataValidationError("unit-alias registry context must declare jurisdiction and/or language")
    rows = data.get("aliases")
    if not isinstance(rows, list):
        raise DataValidationError("unit-alias registry 'aliases' must be an array")
    aliases: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        where = f"aliases[{index}]"
        if not isinstance(row, dict):
            raise DataValidationError(f"{where} must be an object")
        raw_alias = str(row.get("alias") or "").strip()
        key = normalize_unit(raw_alias)
        if not key:
            raise DataValidationError(f"{where}.alias is required")
        if key in aliases:
            raise DataValidationError(f"duplicate unit alias {raw_alias!r}")
        if key in _UNIT_MAP:
            raise DataValidationError(f"unit alias {raw_alias!r} collides with a built-in canonical alias; built-in meanings cannot be overridden")
        if str(row.get("approval_status") or "").upper() != "APPROVED":
            raise DataValidationError(f"{where} alias {raw_alias!r} is not approved")
        required = ("approved_by", "approved_at", "source_document", "source_clause", "applicability")
        missing = [name for name in required if not row.get(name)]
        if missing:
            raise DataValidationError(f"{where} alias {raw_alias!r} missing governance fields: {', '.join(missing)}")
        approved_at = str(row.get("approved_at") or "")
        try:
            if "T" in approved_at or " " in approved_at:
                datetime.fromisoformat(approved_at.replace("Z", "+00:00"))
            else:
                date.fromisoformat(approved_at)
        except ValueError as exc:
            raise DataValidationError(f"{where}.approved_at must be an ISO 8601 date/datetime") from exc
        applicability = row.get("applicability")
        if not isinstance(applicability, dict):
            raise DataValidationError(f"{where}.applicability must be an object")
        if not any(str(applicability.get(k) or "").strip() for k in ("project_id", "jurisdiction", "language")):
            raise DataValidationError(f"{where}.applicability must scope the alias to project_id, jurisdiction or language")
        scoped = str(applicability.get("project_id") or "").strip()
        if scoped and project_id and scoped != project_id:
            raise DataValidationError(f"{where} alias {raw_alias!r} does not apply to project {project_id!r}")
        target = str(row.get("canonical_unit") or "").strip()
        target_key = normalize_unit(target)
        if target_key in _AMBIGUOUS_UNITS:
            raise DataValidationError(f"{where}.canonical_unit {target!r} is ambiguous")
        if target_key not in _UNIT_MAP:
            raise DataValidationError(f"{where}.canonical_unit {target!r} is not a supported built-in unit")
        target_dim, _factor = _UNIT_MAP[target_key]
        declared_dim = str(row.get("dimension") or "").strip().lower()
        if declared_dim != target_dim:
            raise DataValidationError(f"{where}.dimension {declared_dim!r} does not match {target!r} dimension {target_dim!r}")
        aliases[key] = dict(row, _dimension=target_dim, _target=target,
                            _registry_project_id=scoped_project)
    return aliases


def unit_dimensions(*, unit_registry: Optional[Mapping[str, Mapping[str, Any]]] = None) -> dict[str, str]:
    """Return built-in normalized unit aliases and optionally approved project aliases."""
    dimensions = {key: value[0] for key, value in _UNIT_MAP.items()}
    if unit_registry:
        dimensions.update({key: str(value["_dimension"]) for key, value in unit_registry.items()})
    return dimensions


def unit_info(unit: Any, *, unit_registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
              project_id: Optional[str] = None) -> tuple[str, Decimal]:
    key = normalize_unit(unit)
    if unit_registry and key in unit_registry:
        alias = unit_registry[key]
        if not isinstance(alias, Mapping) or str(alias.get("approval_status") or "").upper() != "APPROVED":
            raise DataValidationError(f"unit alias {unit!r} is not a validated approved alias")
        required_governance = ("approved_by", "approved_at", "source_document", "source_clause", "applicability", "_registry_project_id", "_target", "_dimension")
        if any(not alias.get(field) for field in required_governance):
            raise DataValidationError(f"unit alias {unit!r} has incomplete governance metadata")
        scoped = str((alias.get("applicability") or {}).get("project_id") or "").strip()
        registry_project = str(alias.get("_registry_project_id") or "").strip()
        if not project_id:
            raise DataValidationError(f"project_id is required when resolving project unit alias {unit!r}")
        if registry_project and registry_project != project_id:
            raise DataValidationError(f"unit alias registry for {unit!r} belongs to project {registry_project!r}, not {project_id!r}")
        if scoped and scoped != project_id:
            raise DataValidationError(f"unit alias {unit!r} is not approved for project {project_id!r}")
        target_key = normalize_unit(alias.get("_target") or alias.get("canonical_unit"))
        try:
            return _UNIT_MAP[target_key]
        except KeyError as exc:
            raise DataValidationError(f"unit alias {unit!r} points to unsupported target {target_key!r}") from exc
    if key in _AMBIGUOUS_UNITS:
        raise DataValidationError(f"ambiguous unit {unit!r}: {_AMBIGUOUS_UNITS[key]}")
    try:
        return _UNIT_MAP[key]
    except KeyError as exc:
        raise DataValidationError(f"unknown or unsupported unit {unit!r}; no implicit conversion allowed") from exc


def convert(value: Any, from_unit: Any, to_unit: Any, *,
            unit_registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
            project_id: Optional[str] = None) -> Decimal:
    number = parse_decimal(value)
    assert number is not None
    d1, f1 = unit_info(from_unit, unit_registry=unit_registry, project_id=project_id)
    d2, f2 = unit_info(to_unit, unit_registry=unit_registry, project_id=project_id)
    if d1 != d2:
        raise DataValidationError(f"dimension mismatch: {from_unit!r} ({d1}) cannot convert to {to_unit!r} ({d2})")
    with localcontext() as ctx:
        ctx.prec = 34
        return number * f1 / f2


def multiply(values: Iterable[Any]) -> Decimal:
    """Deterministic decimal multiplication; empty factor list is invalid."""
    result = Decimal(1)
    count = 0
    with localcontext() as ctx:
        ctx.prec = 34
        for value in values:
            number = parse_decimal(value)
            assert number is not None
            result *= number
            count += 1
    if not count:
        raise DataValidationError("multiplication requires at least one factor")
    return result


def sum_quantities(values: Iterable[Any]) -> Decimal:
    result = Decimal(0)
    with localcontext() as ctx:
        ctx.prec = 34
        for value in values:
            number = parse_decimal(value)
            assert number is not None
            result += number
    return result


def within_tolerance(actual: Any, expected: Any, *, abs_tol: Any = "0", rel_tol: Any = "0") -> bool:
    a = parse_decimal(actual, field_name="actual")
    e = parse_decimal(expected, field_name="expected")
    at = parse_nonnegative(abs_tol, field_name="abs_tol")
    rt = parse_nonnegative(rel_tol, field_name="rel_tol")
    assert a is not None and e is not None
    return abs(a - e) <= max(at, abs(e) * rt)


def quantize(value: Any, places: int, *, rounding=ROUND_HALF_UP) -> Decimal:
    if not isinstance(places, int) or places < 0 or places > 12:
        raise DataValidationError("places must be an integer in [0, 12]")
    number = parse_decimal(value)
    assert number is not None
    return number.quantize(Decimal(1).scaleb(-places), rounding=rounding)


def sha256_file(path: os.PathLike[str] | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint_inputs(paths: Sequence[os.PathLike[str] | str]) -> dict[str, str]:
    return {str(Path(p)): sha256_file(p) for p in sorted((Path(x) for x in paths), key=lambda p: str(p))}


def release_source_fingerprint(package_root: os.PathLike[str] | str) -> str:
    """Fingerprint executable rules, templates, docs and synthetic E2E inputs.

    Generated reports, caches and build outputs are intentionally excluded. The same
    function is used by the release smoke runner and E2E harness so old test logs cannot
    be reused after any source/rule/template/fixture modification.
    """
    root = Path(package_root).resolve()
    candidates: set[Path] = set()
    for folder in ("scripts", "assets", "references", "agents"):
        base = root / folder
        if base.exists():
            if folder == "scripts":
                candidates.update(p for p in base.rglob("*.py") if p.is_file() and "__pycache__" not in p.parts)
            else:
                candidates.update(p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for name in ("SKILL.md", "INSTALL.md", "VERSION", "CHANGELOG.md"):
        path = root / name
        if path.is_file():
            candidates.add(path)
    demo = root / "examples" / "e2e_demo"
    for name in ("run_e2e.py", "run_openbim_demo.py"):
        path = demo / name
        if path.is_file():
            candidates.add(path)
    for folder in ("input", "reference", "takeoff/register/scan"):
        base = demo / folder
        if base.exists():
            candidates.update(p for p in base.rglob("*") if p.is_file())
    for name in ("takeoff/register/scope.csv",):
        path = demo / name
        if path.is_file():
            candidates.add(path)
    digest = hashlib.sha256()
    for path in sorted(candidates, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
        digest.update(b"\n")
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, set):
        # Set iteration order is process-dependent; reports must be reproducible.
        items = [_json_safe(v) for v in value]
        return sorted(items, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


def atomic_write_text(path: os.PathLike[str] | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write by same-directory temp file + replace; old output survives failed writes."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: os.PathLike[str] | str, obj: Any) -> None:
    atomic_write_text(path, json.dumps(_json_safe(obj), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def atomic_write_csv(path: os.PathLike[str] | str, fieldnames: Sequence[str], rows: Iterable[Mapping[str, Any]], *, excel_safe: bool = False) -> None:
    import io
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(fieldnames), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = {}
        for key in fieldnames:
            value = row.get(key, "")
            if value is None:
                value = ""
            if excel_safe and isinstance(value, str):
                # CSV formula injection may use leading spaces/tabs/newlines before the
                # formula sigil. Preserve genuinely numeric text (including negative
                # values) but prefix other formula-like cells with an apostrophe.
                # Include Unicode spaces/BOM/zero-width separators that spreadsheet
                # importers may ignore before evaluating a formula-like cell.
                probe = value.lstrip(" \t\r\n\v\f\u00a0\u202f\u200b\ufeff")
                if probe and probe[0] in "=+@-":
                    numeric = False
                    try:
                        parse_decimal(probe, field_name="csv_cell")
                        numeric = True
                    except DataValidationError:
                        pass
                    if probe[0] in "=@" or (probe[0] in "+-" and not numeric):
                        value = "'" + value
            clean[key] = value
        writer.writerow(clean)
    atomic_write_text(path, buf.getvalue(), encoding="utf-8-sig")


def safe_relative_path(root: os.PathLike[str] | str, candidate: os.PathLike[str] | str) -> Path:
    base = Path(root).resolve()
    path = (base / candidate).resolve() if not Path(candidate).is_absolute() else Path(candidate).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        raise DataValidationError(f"path escapes project root: {candidate}")
    return path


def gate_exit_code(status: Status) -> int:
    """Common CLI contract: clean=0, findings=1, execution/configuration error=2."""
    if status == Status.PASS:
        return 0
    if status in (Status.FAIL, Status.WARN):
        return 1
    return 2
