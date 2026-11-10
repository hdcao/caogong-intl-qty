#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract quantities explicitly stored in IFC IfcElementQuantity sets.

This is an IFC STEP quantity reader, not a geometry calculator. It reads the
quantities authored/exported into IFC, preserves their provenance, and fails
closed when units cannot be resolved. It does not infer missing quantities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ifc_validate import parse_step, refs
from qto_core import atomic_write_json, sha256_file, unit_info

TOOL_VERSION = "1.0.0"
QTY_TYPES = {
    "IFCQUANTITYLENGTH": ("length", 3),
    "IFCQUANTITYAREA": ("area", 3),
    "IFCQUANTITYVOLUME": ("volume", 3),
    "IFCQUANTITYCOUNT": ("count", 3),
    "IFCQUANTITYWEIGHT": ("mass", 3),
    "IFCQUANTITYTIME": ("time", 3),
}
UNIT_TOKEN_MAP = {
    ("LENGTHUNIT", "$", "METRE"): "m",
    ("LENGTHUNIT", "MILLI", "METRE"): "mm",
    ("LENGTHUNIT", "CENTI", "METRE"): "centimeter",
    ("LENGTHUNIT", "KILO", "METRE"): "km",
    ("AREAUNIT", "$", "SQUARE_METRE"): "m2",
    ("AREAUNIT", "MILLI", "SQUARE_METRE"): "mm2",
    ("AREAUNIT", "CENTI", "SQUARE_METRE"): "cm2",
    ("VOLUMEUNIT", "$", "CUBIC_METRE"): "m3",
    ("VOLUMEUNIT", "MILLI", "CUBIC_METRE"): "mm3",
    ("VOLUMEUNIT", "CENTI", "CUBIC_METRE"): "cm3",
    ("MASSUNIT", "KILO", "GRAM"): "kg",
    ("MASSUNIT", "$", "GRAM"): "g",
    ("TIMEUNIT", "$", "SECOND"): "s",
}
CONVERSION_NAME_MAP = {
    ("LENGTHUNIT", "foot"): "ft", ("LENGTHUNIT", "feet"): "ft",
    ("LENGTHUNIT", "inch"): "in", ("LENGTHUNIT", "inches"): "in",
    ("LENGTHUNIT", "yard"): "yd", ("LENGTHUNIT", "yards"): "yd",
    ("LENGTHUNIT", "mile"): "mi", ("AREAUNIT", "square foot"): "ft2",
    ("AREAUNIT", "square feet"): "ft2", ("AREAUNIT", "square inch"): "in2",
    ("AREAUNIT", "square inches"): "in2", ("AREAUNIT", "square yard"): "yd2",
    ("AREAUNIT", "square yards"): "yd2", ("AREAUNIT", "acre"): "acre",
    ("VOLUMEUNIT", "cubic foot"): "ft3", ("VOLUMEUNIT", "cubic feet"): "ft3",
    ("VOLUMEUNIT", "cubic inch"): "in3", ("VOLUMEUNIT", "cubic inches"): "in3",
    ("VOLUMEUNIT", "cubic yard"): "yd3", ("VOLUMEUNIT", "cubic yards"): "yd3",
    ("MASSUNIT", "pound"): "lb", ("MASSUNIT", "pounds"): "lb",
    ("MASSUNIT", "ounce"): "oz", ("MASSUNIT", "ounces"): "oz",
    ("TIMEUNIT", "minute"): "min", ("TIMEUNIT", "minutes"): "min",
    ("TIMEUNIT", "hour"): "h", ("TIMEUNIT", "hours"): "h",
    ("TIMEUNIT", "day"): "day", ("TIMEUNIT", "days"): "day",
}

# A conservative allow-list of common IfcProduct subtypes. Include IFC4.3
# infrastructure products explicitly; STEP has no runtime EXPRESS inheritance
# metadata, so unknown entities are not guessed to be products.
PRODUCT_PREFIXES = (
    "IFCWALL", "IFCSLAB", "IFCBEAM", "IFCCOLUMN", "IFCDOOR", "IFCWINDOW",
    "IFCROOF", "IFCSTAIR", "IFCRAMP", "IFCFOOTING", "IFCPILE", "IFCMEMBER",
    "IFCPLATE", "IFCRAILING", "IFCCURTAINWALL", "IFCBUILDINGELEMENT",
    "IFCFLOW", "IFCDISTRIBUTION", "IFCFURNISHINGELEMENT", "IFCPROXY",
    "IFCSPACE", "IFCOPENINGELEMENT", "IFCTENDON", "IFCREINFORCINGBAR",
    "IFCCIVILELEMENT", "IFCGEOGRAPHICELEMENT", "IFCROAD", "IFCBRIDGE",
    "IFCTUNNEL", "IFCRAILWAY", "IFCFACILITY", "IFCFACILITYPART",
    "IFCSITE", "IFCBUILDINGSTOREY", "IFCBUILDING", "IFCSPATIALZONE",
    "IFCROADPART", "IFCBRIDGEPART", "IFCTUNNELPART", "IFCRAILWAYPART",
    "IFCMARINEFACILITY", "IFCMARINEPART", "IFCBUILTSYSTEM",
    "IFCTRANSPORTELEMENT", "IFCSTRUCTURALMEMBER", "IFCSTRUCTURALCURVE",
    "IFCSTRUCTURALPOINT", "IFCSTRUCTURALSURFACE", "IFCDISTRIBUTIONPORT",
    "IFCPORT", "IFCGRID", "IFCANNOTATION", "IFCVIRTUALELEMENT",
    "IFCENERGYCONVERSIONDEVICE", "IFCFLOWCONTROLLER", "IFCFLOWFITTING",
    "IFCFLOWMOVINGDEVICE", "IFCFLOWSEGMENT", "IFCFLOWSTORAGEDEVICE",
    "IFCFLOWTERMINAL", "IFCFLOWTREATMENTDEVICE", "IFCFASTENER",
    "IFCMECHANICALFASTENER", "IFCELECTRICALELEMENT", "IFCELECTRICDISTRIBUTIONPOINT",
    "IFCFURNITURE", "IFCINTUMESCENTTREATMENT", "IFCPROJECTIONELEMENT",
    "IFCSPACEHEATER",
)

KIND_DIMENSION = {
    "length": "length", "area": "area", "volume": "volume",
    "mass": "mass", "time": "time", "count": "count",
}
UNIT_TYPE_FOR_KIND = {
    "length": "LENGTHUNIT", "area": "AREAUNIT", "volume": "VOLUMEUNIT",
    "mass": "MASSUNIT", "time": "TIMEUNIT",
}
EXPECTED_MEASURE_TYPE = {
    "LENGTHUNIT": "IFCLENGTHMEASURE", "AREAUNIT": "IFCAREAMEASURE",
    "VOLUMEUNIT": "IFCVOLUMEMEASURE", "MASSUNIT": "IFCMASSMEASURE",
    "TIMEUNIT": "IFCTIMEMEASURE",
}


def _token_string(token: str | None) -> str | None:
    if token is None:
        return None
    token = token.strip()
    if token in ("$", "*") or not (len(token) >= 2 and token[0] == "'" and token[-1] == "'"):
        return None
    return token[1:-1].replace("''", "'")


def _enum(token: str | None) -> str:
    return str(token or "").strip().strip(".").upper()


def _number(token: str | None) -> str | None:
    if token is None:
        return None
    raw = token.strip().replace("D", "E").replace("d", "e")
    if raw in ("$", "*"):
        return None
    try:
        value = Decimal(raw)
        if not value.is_finite():
            return None
        return format(value, "f")
    except (InvalidOperation, ValueError):
        return None


def _is_product(entity_type: str, args: list[str]) -> bool:
    if not args or _token_string(args[0]) is None:
        return False
    typ = entity_type.upper()
    if typ.startswith(("IFCREL", "IFCPROPERTY", "IFCQUANTITY", "IFCMATERIAL", "IFCSTYLE",
                       "IFCUNIT", "IFCOWNER", "IFCPROJECT", "IFCGEOMETRIC", "IFCAXIS",
                       "IFCCARTESIAN", "IFCDIRECTION", "IFCLOCALPLACEMENT", "IFCPRODUCTDEFINITIONSHAPE")):
        return False
    return typ.startswith(PRODUCT_PREFIXES)


def _unit_for_si(args: list[str]) -> tuple[str | None, str | None]:
    if len(args) < 4:
        return None, None
    unit_type = _enum(args[1])
    prefix = _enum(args[2])
    name = _enum(args[3])
    canonical = UNIT_TOKEN_MAP.get((unit_type, prefix, name))
    return unit_type, canonical


def _conversion_measure(token: str | None) -> tuple[str | None, Decimal | None]:
    """Read a typed IFC measure and retain its EXPRESS type for validation."""
    if token is None:
        return None, None
    match = re.fullmatch(
        r"\s*([A-Za-z][A-Za-z0-9_]*)\s*\(\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?)\s*\)\s*",
        token,
    )
    if not match:
        return None, None
    try:
        value = Decimal(match.group(2).replace("D", "E").replace("d", "e"))
        return match.group(1).upper(), value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return match.group(1).upper(), None


def _normal_unit_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold().replace("_", " "))


def extract_ifc_qto(path: str | Path) -> dict[str, Any]:
    file_path = Path(path).resolve()
    raw = file_path.read_bytes()
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        # STEP files with raw non-ASCII text are not silently accepted as clean;
        # parser still reads identifiers but records a warning for provenance.
        text = raw.decode("utf-8", errors="replace")
    entities = parse_step(text)
    if not entities:
        raise ValueError("no IFC STEP entities parsed; file may be truncated or unsupported")

    findings: list[dict[str, str]] = []
    def issue(code: str, severity: str, message: str, location: str | None = None) -> None:
        row = {"code": code, "severity": severity, "message": message}
        if location:
            row["location"] = location
        findings.append(row)

    if not text.lstrip().startswith("ISO-10303-21;") or "FILE_SCHEMA" not in text or "END-ISO-10303-21;" not in text:
        issue("IFC_CONTAINER", "FAIL", "IFC STEP container header/schema/footer is incomplete")
    if "\ufffd" in text:
        issue("IFC_ENCODING", "WARN", "file contains non-ASCII/invalid-decoding text; review IFC STEP encoding")

    # Resolve project units from IfcProject.UnitsInContext -> IfcUnitAssignment.
    assigned_unit_ids: set[int] = set()
    project_meta = {"global_id": None, "name": None, "schema": None}
    schema_match = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text, re.I)
    if schema_match:
        project_meta["schema"] = schema_match.group(1)
    for _eid, (typ, args, _raw) in entities.items():
        if typ == "IFCPROJECT":
            if args:
                project_meta["global_id"] = _token_string(args[0])
            if len(args) > 2:
                project_meta["name"] = _token_string(args[2])
            # IFC2x3/IFC4 IfcProject UnitsInContext is the last attribute.
            if args:
                for ref_id in refs(args[-1]):
                    unit_entity = entities.get(ref_id)
                    if unit_entity and unit_entity[0] == "IFCUNITASSIGNMENT" and unit_entity[1]:
                        assigned_unit_ids.update(refs(unit_entity[1][0]))

    unit_by_id: dict[int, dict[str, str | None]] = {}
    for eid, (typ, args, _raw) in entities.items():
        if typ == "IFCSIUNIT":
            unit_type, canonical = _unit_for_si(args)
            unit_by_id[eid] = {"unit_type": unit_type, "canonical": canonical}
            if canonical is None:
                issue("UNSUPPORTED_SI_UNIT", "WARN",
                      f"IfcSIUnit #{eid} is not in the supported unit mapping; quantities using it will not be guessed",
                      f"#{eid}")
    # Support a controlled subset of IfcConversionBasedUnit definitions (imperial units).
    # A unit is accepted only when its named unit and numeric conversion factor agree.
    expected_dimension = {"LENGTHUNIT": "length", "AREAUNIT": "area", "VOLUMEUNIT": "volume", "MASSUNIT": "mass", "TIMEUNIT": "time"}
    conversion_rows = [(eid, typ, args) for eid, (typ, args, _raw) in entities.items()
                       if typ in ("IFCCONVERSIONBASEDUNIT", "IFCCONVERSIONBASEDUNITWITHOFFSET")]
    for eid, typ, args in conversion_rows:
        unit_type = _enum(args[1]) if len(args) > 1 else None
        name = _normal_unit_name(_token_string(args[2]) if len(args) > 2 else None)
        canonical = CONVERSION_NAME_MAP.get((unit_type or "", name))
        # Offset-based units are deliberately unsupported here: ignoring an
        # offset can silently yield a numerically plausible but physically wrong result.
        if typ == "IFCCONVERSIONBASEDUNITWITHOFFSET":
            unit_by_id[eid] = {"unit_type": unit_type, "canonical": None}
            issue("UNSUPPORTED_OFFSET_UNIT", "WARN",
                  f"IfcConversionBasedUnitWithOffset #{eid} ({name or 'unnamed'}) is not converted by this tool",
                  f"#{eid}")
            continue
        factor_id = refs(args[3])[0] if len(args) > 3 and refs(args[3]) else None
        factor_entity = entities.get(factor_id) if factor_id is not None else None
        measure_type, conversion_value = (
            _conversion_measure(factor_entity[1][0])
            if factor_entity and factor_entity[0] == "IFCMEASUREWITHUNIT" and factor_entity[1]
            else (None, None)
        )
        base_unit_id = refs(factor_entity[1][1])[0] if factor_entity and factor_entity[0] == "IFCMEASUREWITHUNIT" and len(factor_entity[1]) > 1 and refs(factor_entity[1][1]) else None
        base_unit = unit_by_id.get(base_unit_id) if base_unit_id is not None else None
        accepted = False
        if (canonical and conversion_value is not None and conversion_value > 0 and base_unit
                and base_unit.get("canonical") and measure_type == EXPECTED_MEASURE_TYPE.get(unit_type)):
            try:
                base_dim, base_factor = unit_info(str(base_unit["canonical"]))
                target_dim, target_factor = unit_info(canonical)
                actual_factor = conversion_value * base_factor
                tolerance = max(abs(target_factor) * Decimal("1e-9"), Decimal("1e-12"))
                accepted = (base_dim == target_dim == expected_dimension.get(unit_type)
                            and abs(actual_factor - target_factor) <= tolerance)
            except Exception:
                accepted = False
        unit_by_id[eid] = {"unit_type": unit_type, "canonical": canonical if accepted else None}
        if not accepted:
            issue("UNSUPPORTED_CONVERSION_UNIT", "WARN",
                  f"IfcConversionBasedUnit #{eid} ({name or 'unnamed'}) is unsupported or its typed conversion factor failed verification; no implicit conversion will be attempted",
                  f"#{eid}")
    project_units: dict[str, str] = {}
    unit_ambiguous: set[str] = set()
    for uid in sorted(assigned_unit_ids):
        u = unit_by_id.get(uid)
        if not u or not u.get("unit_type") or not u.get("canonical"):
            continue
        dim = str(u["unit_type"])
        canonical = str(u["canonical"])
        if dim in project_units and project_units[dim] != canonical:
            unit_ambiguous.add(dim)
        else:
            project_units[dim] = canonical
    for dim in unit_ambiguous:
        project_units.pop(dim, None)
        issue("AMBIGUOUS_PROJECT_UNIT", "FAIL", f"multiple assigned project units found for {dim}")

    # Index product-like elements by STEP id, keeping absent names explicit.
    elements: dict[int, dict[str, Any]] = {}
    for eid, (typ, args, _raw) in entities.items():
        if not _is_product(typ, args):
            continue
        guid = _token_string(args[0]) if args else None
        name = _token_string(args[2]) if len(args) > 2 else None
        elements[eid] = {
            "step_id": eid, "global_id": guid, "entity_type": typ,
            "name": name, "quantity_sets": [], "quantities": [],
        }
        if not guid:
            issue("MISSING_GLOBAL_ID", "FAIL", f"product entity #{eid} has no GlobalId", f"#{eid}")
        if not name:
            issue("MISSING_ELEMENT_NAME", "WARN", f"product entity #{eid} has no Name", f"#{eid}")

    guid_entities: dict[str, list[int]] = defaultdict(list)
    for entity_id, element in elements.items():
        if element.get("global_id"):
            guid_entities[str(element["global_id"])].append(entity_id)
    for guid, entity_ids in sorted(guid_entities.items()):
        if len(entity_ids) > 1:
            issue("DUPLICATE_ELEMENT_GLOBAL_ID", "FAIL",
                  f"GlobalId {guid!r} is used by multiple recognized product entities: {entity_ids}")

    # Index IfcElementQuantity sets.
    quantity_sets: dict[int, dict[str, Any]] = {}
    for eid, (typ, args, _raw) in entities.items():
        if typ != "IFCELEMENTQUANTITY":
            continue
        qset_name = _token_string(args[2]) if len(args) > 2 else None
        description = _token_string(args[3]) if len(args) > 3 else None
        method = _token_string(args[4]) if len(args) > 4 else None
        quantity_sets[eid] = {
            "step_id": eid, "name": qset_name or "UNNAMED_QUANTITY_SET",
            "description": description, "method_of_measurement": method,
            "quantities": refs(args[5]) if len(args) > 5 else [],
        }
        if len(args) <= 5 or not args[5].strip().startswith("("):
            issue("EMPTY_QUANTITY_SET", "WARN", f"IfcElementQuantity #{eid} has no readable Quantities list", f"#{eid}")

    def parse_quantity(qid: int, set_name: str, owner: dict[str, Any], seen: set[int]) -> list[dict[str, Any]]:
        if qid in seen:
            issue("QUANTITY_CYCLE", "FAIL", f"quantity reference cycle at #{qid}", f"#{qid}")
            return []
        seen.add(qid)
        entry = entities.get(qid)
        if not entry:
            issue("BROKEN_QUANTITY_REFERENCE", "FAIL", f"quantity set references missing entity #{qid}", f"#{qid}")
            return []
        typ, args, _raw = entry
        if typ == "IFCPHYSICALCOMPLEXQUANTITY":
            children = refs(args[2]) if len(args) > 2 else []
            out: list[dict[str, Any]] = []
            for child_id in children:
                out.extend(parse_quantity(child_id, set_name, owner, seen.copy()))
            return out
        if typ not in QTY_TYPES:
            issue("UNSUPPORTED_QUANTITY_TYPE", "WARN", f"unsupported quantity entity {typ} #{qid}; skipped", f"#{qid}")
            return []
        kind, value_idx = QTY_TYPES[typ]
        name = _token_string(args[0]) if args else None
        raw_value = _number(args[value_idx]) if len(args) > value_idx else None
        if not name:
            issue("MISSING_QUANTITY_NAME", "FAIL", f"quantity #{qid} has no Name", f"#{qid}")
            return []
        if raw_value is None:
            issue("INVALID_QUANTITY_VALUE", "FAIL", f"quantity {name!r} #{qid} has no finite numeric value", f"#{qid}")
            return []
        unit = None
        explicit_unit_id = refs(args[2])[0] if len(args) > 2 and refs(args[2]) else None
        if explicit_unit_id is not None:
            explicit = unit_by_id.get(explicit_unit_id)
            unit = explicit.get("canonical") if explicit else None
            if not unit:
                issue("UNRESOLVED_EXPLICIT_UNIT", "FAIL",
                      f"quantity {name!r} references unsupported/unknown unit #{explicit_unit_id}", f"#{qid}")
            else:
                try:
                    actual_dimension, _factor = unit_info(unit)
                    expected_dimension_for_quantity = KIND_DIMENSION.get(kind)
                    if expected_dimension_for_quantity is None or actual_dimension != expected_dimension_for_quantity:
                        issue("EXPLICIT_UNIT_DIMENSION_MISMATCH", "FAIL",
                              f"quantity {name!r} has kind {kind!r} but explicit unit {unit!r} has dimension {actual_dimension!r}",
                              f"#{qid}")
                        unit = None
                except Exception as exc:
                    issue("UNRESOLVED_EXPLICIT_UNIT", "FAIL",
                          f"quantity {name!r} references unsupported unit #{explicit_unit_id}: {exc}", f"#{qid}")
                    unit = None
        elif kind == "count":
            unit = "ea"
        else:
            expected_unit_type = {
                "length": "LENGTHUNIT", "area": "AREAUNIT", "volume": "VOLUMEUNIT",
                "mass": "MASSUNIT", "time": "TIMEUNIT",
            }[kind]
            unit = project_units.get(expected_unit_type)
            if not unit:
                issue("UNRESOLVED_PROJECT_UNIT", "FAIL",
                      f"quantity {name!r} ({kind}) has no unique supported project unit; value will be preserved but not guessed",
                      f"#{qid}")
        row = {
            "quantity_step_id": qid,
            "element_global_id": owner.get("global_id"),
            "element_step_id": owner.get("step_id"),
            "element_type": owner.get("entity_type"),
            "element_name": owner.get("name"),
            "quantity_set": set_name,
            "quantity_name": name,
            "quantity_type": typ,
            "kind": kind,
            "value": raw_value,
            "unit": unit,
            "unit_source": "explicit_unit" if explicit_unit_id is not None else ("project_unit_assignment" if kind != "count" else "IFC_COUNT_SEMANTICS"),
            "source": "IFC_IfcElementQuantity",
        }
        owner["quantities"].append(row)
        return [row]

    # Link quantity sets to products using IfcRelDefinesByProperties.RelatedObjects.
    flat_rows: list[dict[str, Any]] = []
    linked_sets = 0
    for eid, (typ, args, _raw) in entities.items():
        if typ != "IFCRELDEFINESBYPROPERTIES" or len(args) < 6:
            continue
        object_ids = refs(args[4])
        definition_ids = refs(args[5])
        for set_id in definition_ids:
            qset = quantity_sets.get(set_id)
            if not qset:
                continue  # e.g. a regular IfcPropertySet, not a quantity set
            linked_sets += 1
            for object_id in object_ids:
                owner = elements.get(object_id)
                if not owner:
                    issue("QTO_RELATION_NON_PRODUCT", "WARN",
                          f"quantity set #{set_id} is related to entity #{object_id}, not a recognized product", f"#{eid}")
                    continue
                if qset["name"] not in owner["quantity_sets"]:
                    owner["quantity_sets"].append(qset["name"])
                for qid in qset["quantities"]:
                    flat_rows.extend(parse_quantity(qid, qset["name"], owner, set()))

    if not quantity_sets:
        issue("NO_IFC_ELEMENT_QUANTITY", "WARN", "no IfcElementQuantity sets were found; IFC may contain geometry but no authored QTO quantities")
    elif linked_sets == 0:
        issue("UNLINKED_IFC_ELEMENT_QUANTITY", "FAIL", "IfcElementQuantity sets exist but none are linked to products via IfcRelDefinesByProperties")
    if not flat_rows:
        issue("NO_QUANTITIES_EXTRACTED", "FAIL", "no supported numeric quantities were extracted")

    # Duplicate same-named quantities on the same object/set are ambiguous for downstream BOQ mapping.
    seen_keys: set[tuple[str | None, str, str]] = set()
    for row in flat_rows:
        key = (row.get("element_global_id"), row["quantity_set"], row["quantity_name"])
        if key in seen_keys:
            issue("DUPLICATE_QUANTITY_KEY", "FAIL",
                  f"duplicate quantity key {key!r}; downstream mapping must not silently merge duplicate names")
        seen_keys.add(key)

    fail_count = sum(1 for x in findings if x["severity"] == "FAIL")
    warn_count = sum(1 for x in findings if x["severity"] == "WARN")
    return {
        "schema_version": "1.0.0",
        "tool": "ifc_qto_extract",
        "tool_version": TOOL_VERSION,
        "status": "FAIL" if fail_count else ("WARN" if warn_count else "PASS"),
        "source_file": file_path.name,
        "source_path": str(file_path),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "project": project_meta,
        "project_units": project_units,
        "metrics": {
            "ifc_entities": len(entities), "recognized_elements": len(elements),
            "quantity_sets": len(quantity_sets), "linked_quantity_sets": linked_sets,
            "quantities_extracted": len(flat_rows), "failures": fail_count, "warnings": warn_count,
            "elements_without_quantities": sum(1 for e in elements.values() if not e["quantities"]),
        },
        "elements": sorted(elements.values(), key=lambda e: (str(e.get("global_id") or ""), e["step_id"])),
        "quantities": sorted(flat_rows, key=lambda r: (str(r.get("element_global_id") or ""), r["quantity_set"], r["quantity_name"], r["quantity_step_id"])),
        "findings": findings,
    }


def _selftest() -> int:
    sample = """ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nFILE_NAME('sample.ifc','2026-10-29T00:00:00',(''),(''),'','','');\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n#1=IFCPROJECT('0123456789012345678901',$,'Demo Project',$,$,$,$,$,#2);\n#2=IFCUNITASSIGNMENT((#3,#4,#5));\n#3=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);\n#4=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);\n#5=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);\n#10=IFCWALL('1123456789012345678901',$,'Wall A',$,$,$,$,$,.NOTDEFINED.);\n#20=IFCQUANTITYVOLUME('NetVolume',$,$,12.5,$);\n#21=IFCQUANTITYVOLUME('GrossVolume',$,$,13.0,$);\n#22=IFCELEMENTQUANTITY('2123456789012345678901',$,'Qto_WallBaseQuantities',$,'Base quantities',(#20,#21));\n#30=IFCRELDEFINESBYPROPERTIES('3123456789012345678901',$,$,$,(#10),#22);\nENDSEC;\nEND-ISO-10303-21;\n"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sample.ifc"
        out = Path(td) / "qto.json"
        src.write_text(sample, encoding="ascii")
        result = extract_ifc_qto(src)
        atomic_write_json(out, result)
        reread = json.loads(out.read_text(encoding="utf-8"))
        assert reread["metrics"]["quantities_extracted"] == 2, reread["metrics"]
        assert reread["quantities"][0]["unit"] == "m3", reread["quantities"]
        assert not [f for f in reread["findings"] if f["severity"] == "FAIL"], reread["findings"]
    print("PASS ifc_qto_extract selftest: IFC ElementQuantity, project units, provenance, JSON output")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("extract", help="extract quantities explicitly stored in IfcElementQuantity")
    p.add_argument("--file", required=True, help="input IFC STEP file")
    p.add_argument("--out", required=True, help="output JSON")
    p.set_defaults(func="extract")
    sub.add_parser("selftest", help="run synthetic IFC quantity extraction selftest")
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return _selftest()
    if args.cmd != "extract":
        ap.print_help(); return 2
    try:
        result = extract_ifc_qto(args.file)
        atomic_write_json(args.out, result)
        print(f"IFC QTO extraction: {result['status']} | elements={result['metrics']['recognized_elements']} | quantities={result['metrics']['quantities_extracted']} | fail={result['metrics']['failures']} warn={result['metrics']['warnings']}")
        print(f"source_sha256={result['source_sha256']}")
        print(f"report={Path(args.out).resolve()}")
        return 1 if result["status"] == "FAIL" else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[NOT_RUN] IFC QTO extraction failed: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
