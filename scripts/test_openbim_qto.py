#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for IFC QTO extraction, BOQ mapping, delivery profiles and diffs."""
from __future__ import annotations
import json, tempfile, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ifc_qto_extract import extract_ifc_qto
from boq_mapping import map_ifc_quantities
from ifc_delivery_check import check_delivery_profile
from ifc_qto_diff import compare_snapshots
from openbim_pipeline import run_pipeline
from qto_core import atomic_write_json, convert


def sample_ifc(second_wall=True, unresolved_unit=False):
    volume_unit = "#5" if not unresolved_unit else "#99"
    rows = [
        "ISO-10303-21;", "HEADER;", "FILE_DESCRIPTION((''),'2;1');",
        "FILE_NAME('sample.ifc','2026-10-29T00:00:00',(''),(''),'','','');",
        "FILE_SCHEMA(('IFC4'));", "ENDSEC;", "DATA;",
        "#1=IFCPROJECT('0123456789012345678901',$,'Demo Project',$,$,$,$,$,#2);",
        "#2=IFCUNITASSIGNMENT((#3,#4,#5));",
        "#3=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);",
        "#4=IFCSIUNIT(*,.AREAUNIT.,$,.SQUARE_METRE.);",
        "#5=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);",
        "#10=IFCWALL('1123456789012345678901',$,'Wall A',$,$,$,$,$,.NOTDEFINED.);",
        "#20=IFCQUANTITYVOLUME('NetVolume',$,$,12.5,$);",
        "#21=IFCQUANTITYVOLUME('GrossVolume',$,$,13.0,$);",
        "#22=IFCELEMENTQUANTITY('2123456789012345678901',$,'Qto_WallBaseQuantities',$,'Base quantities',(#20,#21));",
        "#30=IFCRELDEFINESBYPROPERTIES('3123456789012345678901',$,$,$,(#10),#22);",
    ]
    if second_wall:
        rows.extend([
            "#11=IFCWALL('4123456789012345678901',$,'Wall B',$,$,$,$,$,.NOTDEFINED.);",
            "#23=IFCQUANTITYVOLUME('NetVolume',$,$,3.0,$);",
            "#24=IFCELEMENTQUANTITY('5123456789012345678901',$,'Qto_WallBaseQuantities',$,'Base quantities',(#23));",
            "#31=IFCRELDEFINESBYPROPERTIES('6123456789012345678901',$,$,$,(#11),#24);",
        ])
    rows.extend(["ENDSEC;", "END-ISO-10303-21;"])
    if unresolved_unit:
        # Make project volume unit resolvable syntactically but unsupported to force a strict fail.
        rows = [r.replace("#5=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);", "#5=IFCSIUNIT(*,.VOLUMEUNIT.,$,.UNSUPPORTED_UNIT.);") for r in rows]
    return "\n".join(rows) + "\n"


def sample_ifc_imperial():
    text = sample_ifc(second_wall=False)
    text = text.replace("#2=IFCUNITASSIGNMENT((#3,#4,#5));", "#2=IFCUNITASSIGNMENT((#7,#9));")
    text = text.replace("#5=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);", "#5=IFCSIUNIT(*,.VOLUMEUNIT.,$,.CUBIC_METRE.);\n#6=IFCMEASUREWITHUNIT(IFCLENGTHMEASURE(0.3048),#3);\n#7=IFCCONVERSIONBASEDUNIT(*,.LENGTHUNIT.,'foot',#6);\n#8=IFCMEASUREWITHUNIT(IFCVOLUMEMEASURE(0.028316846592),#5);\n#9=IFCCONVERSIONBASEDUNIT(*,.VOLUMEUNIT.,'cubic foot',#8);")
    return text



def sample_ifc_infrastructure():
    text = sample_ifc(second_wall=False)
    extra = """
#12=IFCROAD('7123456789012345678901',$,'Road A',$,$,$,$,$,.ELEMENT.);
#25=IFCQUANTITYLENGTH('Length',$,$,100.0,$);
#26=IFCELEMENTQUANTITY('8123456789012345678901',$,'Qto_RoadBaseQuantities',$,'Base quantities',(#25));
#32=IFCRELDEFINESBYPROPERTIES('9123456789012345678901',$,$,$,(#12),#26);
"""
    head, tail = text.rsplit("ENDSEC;", 1)
    return head + extra + "ENDSEC;" + tail


def sample_delivery_profile(project_id="P-DEMO"):
    return {
        "schema_version": "1.0.0", "profile_id": "DEMO-IFC-QTO", "project_id": project_id,
        "requirements": [{
            "requirement_id": "WALL-BASE-QTO", "entity_types": ["IFCWALL"], "min_count": 2,
            "require_global_id": True, "require_name": True,
            "required_quantity_sets": ["Qto_WallBaseQuantities"],
            "required_quantities": [{"quantity_set": "Qto_WallBaseQuantities", "quantity_name": "NetVolume", "kind": "volume", "unit_dimension": "volume"}]
        }]
    }

def approved_registry(project_id="P-DEMO", valid=True):
    rule_status = "APPROVED" if valid else "DRAFT"
    return {
        "schema_version": "1.0.0", "project_id": project_id,
        "mapping_rules": [{
            "rule_id": "MAP-WALL-NET", "status": rule_status,
            "approved_by": "QS Lead", "approved_at": "2026-10-29",
            "source_document": "Tender Measurement Rules.pdf", "source_clause": "Section 2.1",
            "element_type": "IFCWALL", "quantity_set": "Qto_WallBaseQuantities",
            "quantity_name": "NetVolume", "kind": "volume", "boq_code": "C-001",
            "boq_description": "Concrete wall volume", "boq_unit": "l"
        }],
        "approved_exclusions": [{
            "rule_id": "EX-WALL-GROSS", "status": "APPROVED", "approved_by": "QS Lead",
            "approved_at": "2026-10-29", "source_document": "Tender Measurement Rules.pdf",
            "source_clause": "Section 2.2", "reason": "Gross volume is retained for reference but is not a billable quantity in this BOQ",
            "element_type": "IFCWALL", "quantity_set": "Qto_WallBaseQuantities",
            "quantity_name": "GrossVolume", "kind": "volume"
        }]
    }


def run() -> int:
    count = 0
    def check(label, condition):
        nonlocal count
        if not condition:
            raise AssertionError("FAIL: " + label)
        count += 1; print("PASS", label)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ifc = root / "sample.ifc"; ifc.write_text(sample_ifc(), encoding="ascii")
        qto = extract_ifc_qto(ifc)
        check("extracts explicit IfcElementQuantity rows", qto["metrics"]["quantities_extracted"] == 3)
        check("resolves project volume unit to m3", all(x["unit"] == "m3" for x in qto["quantities"] if x["kind"] == "volume"))
        check("preserves element GlobalId and quantity-set provenance", all(x.get("element_global_id") and x.get("quantity_set") == "Qto_WallBaseQuantities" for x in qto["quantities"]))
        check("does not pretend STEP data is geometric recomputation", all(x["source"] == "IFC_IfcElementQuantity" for x in qto["quantities"]))
        qto_path = root / "qto.json"; atomic_write_json(qto_path, qto)

        registry_path = root / "mapping.json"; atomic_write_json(registry_path, approved_registry())
        mapped = map_ifc_quantities(qto_path, registry_path, project_id="P-DEMO")
        check("maps governed quantities and aggregates to one BOQ row", mapped["status"] == "PASS" and mapped["metrics"]["boq_rows"] == 1)
        check("converts 15.5 m3 to 15500 litres", str(mapped["boq_summary"][0]["quantity"]) == "15500.0" or str(mapped["boq_summary"][0]["quantity"]) == "15500")
        bad_registry_path = root / "mapping_bad.json"; atomic_write_json(bad_registry_path, approved_registry(valid=False))
        bad_map = map_ifc_quantities(qto_path, bad_registry_path, project_id="P-DEMO")
        check("blocks unapproved BOQ mapping rules", bad_map["status"] == "FAIL" and any(x["code"] == "UNAPPROVED_MAPPING_RULE" for x in bad_map["findings"]))

        profile = sample_delivery_profile()
        profile_path = root / "profile.json"; atomic_write_json(profile_path, profile)
        checked = check_delivery_profile(qto_path, profile_path)
        check("passes IDS-inspired QTO delivery profile", checked["status"] == "PASS")
        profile["requirements"][0]["required_quantities"][0]["quantity_name"] = "NonexistentQuantity"
        atomic_write_json(profile_path, profile)
        checked_bad = check_delivery_profile(qto_path, profile_path)
        check("blocks missing required IFC quantity", checked_bad["status"] == "FAIL" and any(x["code"] == "QUANTITY_REQUIRED" for x in checked_bad["findings"]))

        same_diff = compare_snapshots(qto_path, qto_path)
        check("unchanged snapshots return NO_CHANGE", same_diff["status"] == "NO_CHANGE")
        current = json.loads(qto_path.read_text(encoding="utf-8"))
        current["quantities"][0]["value"] = "99.0"
        current_path = root / "current.json"; atomic_write_json(current_path, current)
        diff = compare_snapshots(qto_path, current_path)
        check("quantity changes require manual review", diff["status"] == "REVIEW_REQUIRED" and diff["metrics"]["quantity_changed"] >= 1)
        incomparable = json.loads(qto_path.read_text(encoding="utf-8"))
        incomparable["quantities"][0]["unit"] = "kg"
        incomparable_path = root / "incomparable.json"; atomic_write_json(incomparable_path, incomparable)
        diff_bad = compare_snapshots(qto_path, incomparable_path)
        check("dimensionally incompatible snapshots are NOT_COMPARABLE and review-blocked", diff_bad["status"] == "REVIEW_REQUIRED" and diff_bad["metrics"]["not_comparable"] >= 1)

        bad_ifc = root / "bad_units.ifc"; bad_ifc.write_text(sample_ifc(unresolved_unit=True), encoding="ascii")
        bad_qto = extract_ifc_qto(bad_ifc)
        check("unsupported IFC project unit is never silently guessed", bad_qto["status"] == "FAIL" and any(x["code"] == "UNRESOLVED_PROJECT_UNIT" for x in bad_qto["findings"]))

        imperial_ifc = root / "imperial.ifc"; imperial_ifc.write_text(sample_ifc_imperial(), encoding="ascii")
        imperial_qto = extract_ifc_qto(imperial_ifc)
        volume_row = next(x for x in imperial_qto["quantities"] if x["quantity_name"] == "NetVolume")
        check("validates common IFC conversion-based imperial volume unit", volume_row["unit"] == "ft3" and imperial_qto["status"] != "FAIL")
        check("converts extracted cubic-foot QTO to cubic metres", convert(volume_row["value"], volume_row["unit"], "m3") == convert("12.5", "ft3", "m3"))

        # Explicit-unit dimensions are checked against the quantity entity's kind.
        bad_explicit = root / "bad_explicit_unit.ifc"
        bad_explicit.write_text(sample_ifc(second_wall=False).replace(
            "#20=IFCQUANTITYVOLUME('NetVolume',$,$,12.5,$);",
            "#20=IFCQUANTITYVOLUME('NetVolume',$,#3,12.5,$);"), encoding="ascii")
        bad_explicit_qto = extract_ifc_qto(bad_explicit)
        check("rejects an explicitly assigned length unit on an IFC volume quantity",
              bad_explicit_qto["status"] == "FAIL" and any(x["code"] == "EXPLICIT_UNIT_DIMENSION_MISMATCH" for x in bad_explicit_qto["findings"]))

        bad_count = root / "bad_count_unit.ifc"
        count_text = sample_ifc(second_wall=False)
        count_extra = """
#25=IFCQUANTITYCOUNT('ElementCount',$,#3,2,$);
#26=IFCELEMENTQUANTITY('8123456789012345678901',$,'Qto_Counts',$,'Counts',(#25));
#32=IFCRELDEFINESBYPROPERTIES('9123456789012345678901',$,$,$,(#10),#26);
"""
        head, tail = count_text.rsplit("ENDSEC;", 1)
        bad_count.write_text(head + count_extra + "ENDSEC;" + tail, encoding="ascii")
        bad_count_qto = extract_ifc_qto(bad_count)
        check("rejects a dimensional unit attached to IfcQuantityCount",
              bad_count_qto["status"] == "FAIL" and any(x["code"] == "EXPLICIT_UNIT_DIMENSION_MISMATCH" for x in bad_count_qto["findings"]))

        infrastructure = root / "road.ifc"; infrastructure.write_text(sample_ifc_infrastructure(), encoding="ascii")
        infra_qto = extract_ifc_qto(infrastructure)
        check("recognizes IFC4.3 infrastructure products such as IfcRoad",
              any(e["entity_type"] == "IFCROAD" and e["name"] == "Road A" for e in infra_qto["elements"])
              and any(q["quantity_name"] == "Length" and q["unit"] == "m" for q in infra_qto["quantities"]))

        duplicate_guid = root / "duplicate_guid.ifc"
        duplicate_guid.write_text(sample_ifc_infrastructure().replace(
            "'7123456789012345678901'", "'1123456789012345678901'"), encoding="ascii")
        duplicate_qto = extract_ifc_qto(duplicate_guid)
        check("blocks duplicate GlobalIds across IFC infrastructure/building elements",
              duplicate_qto["status"] == "FAIL" and any(x["code"] == "DUPLICATE_ELEMENT_GLOBAL_ID" for x in duplicate_qto["findings"]))

        wrong_measure = root / "wrong_measure_type.ifc"
        wrong_measure.write_text(sample_ifc_imperial().replace(
            "IFCVOLUMEMEASURE(0.028316846592)", "IFCLENGTHMEASURE(0.028316846592)"), encoding="ascii")
        wrong_measure_qto = extract_ifc_qto(wrong_measure)
        check("rejects a typed conversion factor whose IFC measure type mismatches its unit dimension",
              wrong_measure_qto["status"] == "FAIL" and any(x["code"] == "UNSUPPORTED_CONVERSION_UNIT" for x in wrong_measure_qto["findings"]))

        offset_unit = root / "offset_unit.ifc"
        offset_unit.write_text(sample_ifc_imperial().replace(
            "IFCCONVERSIONBASEDUNIT(*,.VOLUMEUNIT.,'cubic foot',#8)",
            "IFCCONVERSIONBASEDUNITWITHOFFSET(*,.VOLUMEUNIT.,'cubic foot',#8,1.0)"), encoding="ascii")
        offset_qto = extract_ifc_qto(offset_unit)
        check("never ignores offsets in conversion-based units",
              offset_qto["status"] == "FAIL" and any(x["code"] == "UNSUPPORTED_OFFSET_UNIT" for x in offset_qto["findings"]))

        missing_hash_qto = dict(qto); missing_hash_qto.pop("source_sha256", None)
        missing_hash_path = root / "missing_hash_qto.json"; atomic_write_json(missing_hash_path, missing_hash_qto)
        missing_hash_map = map_ifc_quantities(missing_hash_path, registry_path, project_id="P-DEMO")
        check("blocks BOQ mapping when QTO provenance hash is missing",
              missing_hash_map["status"] == "FAIL" and any(x["code"] == "QTO_SOURCE_HASH_INVALID" for x in missing_hash_map["findings"]))
        stale_hash_qto = dict(qto); stale_hash_qto["source_sha256"] = "0" * 64
        stale_hash_path = root / "stale_hash_qto.json"; atomic_write_json(stale_hash_path, stale_hash_qto)
        stale_hash_map = map_ifc_quantities(stale_hash_path, registry_path, project_id="P-DEMO")
        check("rechecks original IFC bytes before BOQ mapping and rejects stale QTO snapshots",
              stale_hash_map["status"] == "FAIL" and any(x["code"] == "SOURCE_IFC_HASH_MISMATCH" for x in stale_hash_map["findings"]))
        malformed_rows_qto = dict(qto); malformed_rows_qto["quantities"] = list(qto["quantities"]) + ["not-an-object"]
        malformed_rows_path = root / "malformed_quantity_row.json"; atomic_write_json(malformed_rows_path, malformed_rows_qto)
        malformed_rows_map = map_ifc_quantities(malformed_rows_path, registry_path, project_id="P-DEMO")
        check("counts malformed QTO row as invalid and blocks mapping",
              malformed_rows_map["status"] == "FAIL" and malformed_rows_map["metrics"]["invalid_quantity_rows"] == 1
              and malformed_rows_map["metrics"]["source_quantities"] == len(qto["quantities"]) + 1)

        bad_upstream = dict(qto); bad_upstream["status"] = "FAIL"
        bad_upstream_path = root / "failed_upstream_qto.json"; atomic_write_json(bad_upstream_path, bad_upstream)
        failed_profile_check = check_delivery_profile(bad_upstream_path, profile_path)
        check("delivery profile cannot pass when upstream QTO extraction failed",
              failed_profile_check["status"] == "FAIL" and any(x["code"] == "UPSTREAM_QTO_FAILED" for x in failed_profile_check["findings"]))
        wrong_project_check = check_delivery_profile(qto_path, profile_path, expected_project_id="OTHER-PROJECT")
        check("delivery profile enforces requested project scope",
              wrong_project_check["status"] == "FAIL" and any(x["code"] == "PROJECT_SCOPE_MISMATCH" for x in wrong_project_check["findings"]))

        invalid_tol = compare_snapshots(qto_path, qto_path, abs_tol="-1", rel_tol="NaN")
        check("snapshot diff rejects invalid/negative tolerances instead of silently using them",
              invalid_tol["status"] == "REVIEW_REQUIRED" and any(x["code"] == "INVALID_TOLERANCE" for x in invalid_tol["findings"]))
        duplicate_elements = json.loads(qto_path.read_text(encoding="utf-8")); duplicate_elements["elements"].append(duplicate_elements["elements"][0])
        duplicate_elements_path = root / "duplicate_elements.json"; atomic_write_json(duplicate_elements_path, duplicate_elements)
        duplicate_diff = compare_snapshots(qto_path, duplicate_elements_path)
        check("snapshot diff blocks duplicate element GlobalIds",
              duplicate_diff["status"] == "REVIEW_REQUIRED" and any(x["code"] == "DUPLICATE_ELEMENT_GLOBAL_ID" for x in duplicate_diff["findings"]))

        # Restore a valid profile after the deliberate negative profile test above.
        atomic_write_json(profile_path, sample_delivery_profile())
        pipeline_dir = root / "pipeline_same"
        pipeline_same = run_pipeline(baseline_ifc=ifc, current_ifc=ifc, mapping_registry=registry_path,
                                     delivery_profile=profile_path, project_id="P-DEMO", out_dir=pipeline_dir)
        check("one-command OpenBIM pipeline passes unchanged, fully governed synthetic model",
              pipeline_same["status"] == "PASS" and pipeline_same["release_eligible"] and pipeline_same["source_inputs_unchanged"])
        # jsonschema 是**可选**依赖。未安装时应 SKIP，而不是判 FAIL——
        # 旧写法把 ImportError 当成失败，导致任何没装 jsonschema 的机器上
        # 发布冒烟固定 19/20，且失败项与真实缺陷无关。假红比假绿更耗人：
        # 出现过一次，整条测试套件就会被跳过（v4.5.1 修正）。
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema is None:
            print("SKIP validates integrated pipeline output against its JSON Schema "
                  "(jsonschema not installed; pip install jsonschema to enable)")
        else:
            pipeline_schema = json.loads((ROOT / "assets/schemas/openbim-pipeline-result.schema.json").read_text(encoding="utf-8"))
            # 校验失败（SchemaValidationError）必须照常抛出——那是真缺陷，不能吞。
            jsonschema.Draft202012Validator(pipeline_schema).validate(pipeline_same)
            check("validates integrated pipeline output against its JSON Schema", True)
        changed_ifc = root / "changed.ifc"; changed_ifc.write_text(sample_ifc().replace("12.5", "14.0"), encoding="ascii")
        pipeline_changed = run_pipeline(baseline_ifc=ifc, current_ifc=changed_ifc, mapping_registry=registry_path,
                                        delivery_profile=profile_path, project_id="P-DEMO", out_dir=root / "pipeline_changed")
        check("one-command OpenBIM pipeline requires review when model quantities change",
              pipeline_changed["status"] == "REVIEW_REQUIRED" and not pipeline_changed["release_eligible"])
    print(f"OPENBIM QTO REGRESSION RESULT: {count}/{count} passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(run())
