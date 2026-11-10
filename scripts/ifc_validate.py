#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""IFC 结构校验器（v4.0.0 新增）。

为什么需要：算量模型导出 IFC 后"打开不显示"，绝大多数不是几何错，
而是**结构不合法 / 空间结构不闭合 / 单位与坐标量纲错配**。
宽容解析器（ifcopenshell）能读出来，严格查看器直接整片不渲染——
"文件能打开"和"模型能显示"是两回事，必须有一道专门门禁。

真实案例（2026-10-25/26，Ras El Hekma Ph1A 海岸线模型）：
- ObjectPlacement 写成 IfcAxis2Placement3D（应为 IfcLocalPlacement）：
  ifcopenshell 能读，严格查看器拒收。
- 全部产品无空间结构（无 RelAggregates / RelContainedInSpatialStructure）：
  按 Project→Site→Building→Storey 遍历的查看器一件都看不到。
- Project.RepresentationContexts 指向 IfcSite（应指向几何上下文）。
- 长度单位声明 MILLI.METRE 而几何按米建模 → 模型缩小 1000 倍。

用法：
  python scripts/ifc_validate.py check --file model.ifc [--json out.json]
  python scripts/ifc_validate.py check --file model.ifc --geometry   # 需 ifcopenshell
  python scripts/ifc_validate.py selftest

退出码：0 无 FAIL；1 有 FAIL；2 未运行（文件缺失/解析失败/自证失败）。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import statistics
import sys
import tempfile

RULES = {
    "IFC-01": "文件容器完整（ISO 头 / FILE_SCHEMA / ENDSEC / END-ISO）",
    "IFC-02": "ObjectPlacement 必须是 IfcLocalPlacement/IfcGridPlacement，不得是 IfcAxis2Placement3D",
    "IFC-03": "空间结构闭合：全部带几何的产品必须挂进 Project 空间树（孤儿产品不显示）",
    "IFC-04": "Project.RepresentationContexts 与 ShapeRepresentation 上下文必须指向几何上下文",
    "IFC-05": "产品 Representation 必须是 IfcProductDefinitionShape",
    "IFC-06": "挤出实体 ExtrudedDirection 必填、Depth 必须是实数字面量（带小数点）",
    "IFC-07": "长度单位与坐标/挤出深度量纲合理性（启发式 WARN，不自动判死）",
    "IFC-08": "GUID 不得重复；非 ASCII 文本必须按 STEP \\X2\\ 转义",
    "IFC-09": "文件膨胀预警（轮廓未共用导致的几何重复）",
}

SPATIAL = {"IFCPROJECT", "IFCSITE", "IFCBUILDING", "IFCBUILDINGSTOREY", "IFCSPACE",
           "IFCSPATIALZONE", "IFCFACILITY", "IFCFACILITYPART", "IFCBRIDGE", "IFCRAILWAY",
           "IFCROAD", "IFCMARINEFACILITY", "IFCBUILTSYSTEM"}
REL_TYPES = {"IFCRELAGGREGATES", "IFCRELCONTAINEDINSPATIALSTRUCTURE",
             "IFCRELNESTS", "IFCRELASSIGNSTOGROUP",
             "IFCRELDEFINESBYPROPERTIES", "IFCRELDEFINESBYTYPE",
             "IFCRELVOIDSELEMENT", "IFCRELFILLSELEMENT", "IFCRELCOVERSBLDGELEMENTS",
             "IFCRELREFERENCEDINSPATIALSTRUCTURE", "IFCRELSPACEBOUNDARY",
             "IFCRELASSOCIATESMATERIAL", "IFCRELASSOCIATESCLASSIFICATION",
             "IFCRELASSOCIATESDOCUMENT", "IFCRELASSOCIATESLIBRARY",
             "IFCRELCONNECTSELEMENTS", "IFCRELSEQUENCE"}


def is_relationship(typ: str) -> bool:
    """是否为 IfcRelationship 子类型（关系实体，不是构件）。

    ★ 为什么按前缀判而不只按枚举判：
    关系实体**永远不会是 IfcProduct**，拿它们去校验
    GlobalId/OwnerHistory/ObjectPlacement 必然全线报错。
    枚举漏一个，那个关系就会被当成构件；而按 IFC 命名约定，
    `IfcRelationship` 的全部子类型都以 `IFCREL` 开头，前缀判据不会漏。
    本桥 377 条 `IfcRelDefinesByProperties` 曾因此产生 754 条假 FAIL
    （每条 2 项），整张校验报告变成红海，真正的缺陷被淹掉。
    """
    return typ in REL_TYPES or typ.startswith("IFCREL")


# ---------------------------------------------------------------- STEP 解析
def split_args(s: str) -> list[str]:
    """按顶层逗号切分参数，尊重括号与单引号字符串（含 '' 转义）。"""
    out, buf, depth, i, in_str = [], [], 0, 0, False
    while i < len(s):
        c = s[i]
        if in_str:
            buf.append(c)
            if c == "'":
                if i + 1 < len(s) and s[i + 1] == "'":
                    buf.append("'")
                    i += 1
                else:
                    in_str = False
        else:
            if c == "'":
                in_str = True
                buf.append(c)
            elif c == "(":
                depth += 1
                buf.append(c)
            elif c == ")":
                depth -= 1
                buf.append(c)
            elif c == "," and depth == 0:
                out.append("".join(buf).strip())
                buf = []
            else:
                buf.append(c)
        i += 1
    if buf or out:
        out.append("".join(buf).strip())
    return out


def parse_step(text: str) -> dict[int, tuple[str, list[str], str]]:
    """返回 {id: (类型, 参数列表, 原始参数串)}。注释与空白行忽略。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    ents = {}
    for m in re.finditer(r"#(\d+)\s*=\s*([A-Za-z0-9_]+)\s*\((.*?)\)\s*;", text, re.S):
        eid, typ, raw = int(m.group(1)), m.group(2).upper(), m.group(3)
        ents[eid] = (typ, split_args(raw), raw)
    return ents


def refs(token: str) -> list[int]:
    return [int(x) for x in re.findall(r"#(\d+)", token or "")]


class Finding:
    def __init__(self, rule, level, msg):
        self.rule, self.level, self.msg = rule, level, msg

    def as_dict(self):
        return {"rule": self.rule, "level": self.level, "message": self.msg}


def validate_text(path: str) -> tuple[list[Finding], dict]:
    raw = io.open(path, "rb").read()
    findings: list[Finding] = []

    def add(rule, level, msg):
        findings.append(Finding(rule, level, msg))

    try:
        text = raw.decode("ascii")
        ascii_clean = True
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        ascii_clean = False

    if not text.lstrip().startswith("ISO-10303-21;"):
        add("IFC-01", "FAIL", "缺少 ISO-10303-21 文件头")
    if "FILE_SCHEMA" not in text:
        add("IFC-01", "FAIL", "缺少 FILE_SCHEMA")
    if "ENDSEC;" not in text or "END-ISO-10303-21;" not in text:
        add("IFC-01", "FAIL", "文件未正常收尾（ENDSEC/END-ISO 缺失，可能被截断）")

    ents = parse_step(text)
    if not ents:
        add("IFC-01", "FAIL", "未解析到任何实体")
        return findings, {"entities": 0}

    types = {eid: v[0] for eid, v in ents.items()}
    products = {eid: v for eid, v in ents.items()
                if v[0].startswith("IFC") and len(v[1]) >= 7
                and not is_relationship(v[0])
                and (v[0] in SPATIAL or types.get(eid) not in {
                    "IFCCARTESIANPOINT", "IFCDIRECTION", "IFCPOLYLINE", "IFCPOLYLOOP",
                    "IFCARBITRARYCLOSEDPROFILEDEF", "IFCRECTANGLEPROFILEDEF",
                    "IFCEXTRUDEDAREASOLID", "IFCSHAPEREPRESENTATION",
                    "IFCPRODUCTDEFINITIONSHAPE", "IFCPROPERTYSINGLEVALUE",
                    "IFCPROPERTYSET", "IFCSIUNIT", "IFCUNITASSIGNMENT",
                    "IFCGEOMETRICREPRESENTATIONCONTEXT",
                    "IFCGEOMETRICREPRESENTATIONSUBCONTEXT"})}

    # IFC-02 / IFC-05：IfcProduct 参数序 GlobalId,OwnerHistory,Name,Description,
    # ObjectType,ObjectPlacement(5),Representation(6)
    geo_products = []
    for eid, (typ, args, _raw) in sorted(products.items()):
        if typ == "IFCPROJECT":
            continue
        if len(args) > 5:
            for r in refs(args[5]):
                rt = types.get(r)
                if rt == "IFCAXIS2PLACEMENT3D":
                    add("IFC-02", "FAIL",
                        f"#{eid} {typ} 的 ObjectPlacement 指向 IfcAxis2Placement3D(#{r})，"
                        "应为 IfcLocalPlacement（宽容解析器能读、严格查看器拒收）")
                elif rt not in (None, "IFCLOCALPLACEMENT", "IFCGRIDPLACEMENT"):
                    add("IFC-02", "FAIL", f"#{eid} {typ} 的 ObjectPlacement 类型异常：{rt}")
        if len(args) > 6 and args[6] != "$":
            geo_products.append(eid)
            for r in refs(args[6]):
                if types.get(r) not in (None, "IFCPRODUCTDEFINITIONSHAPE"):
                    add("IFC-05", "FAIL",
                        f"#{eid} {typ} 的 Representation 是 {types.get(r)}，"
                        "必须是 IfcProductDefinitionShape")

    # IFC-03：空间树闭合。关系参数位按 IFC schema 固定（不按"意图"猜测）：
    #   RelAggregates:                4=RelatingObject  5=RelatedObjects(列表)
    #   RelContainedInSpatialStructure: 4=RelatedElements(列表) 5=RelatingStructure
    # 真实缺陷：Name/Description 后多写一个 $ → RelatingObject 变成 $、
    # 子对象列表被挤到第 6 位，宽容解析器直接丢弃，空间树整棵断掉。
    children, contained = {}, set()
    broken_rels = 0
    for eid, (typ, args, _raw) in ents.items():
        if typ == "IFCRELAGGREGATES":
            if len(args) < 6:
                add("IFC-03", "FAIL", f"#{eid} RelAggregates 参数不足 {len(args)} 个")
                broken_rels += 1
                continue
            relating, related = args[4], args[5]
            if not re.fullmatch(r"#\d+", relating):
                add("IFC-03", "FAIL",
                    f"#{eid} RelAggregates.RelatingObject={relating!r}（不是实体引用，"
                    "疑似 Name/Description 后多写 $ 造成参数错位）")
                broken_rels += 1
                continue
            if not related.startswith("("):
                add("IFC-03", "FAIL",
                    f"#{eid} RelAggregates.RelatedObjects 不是列表：{related[:40]!r}（子对象会被丢弃）")
                broken_rels += 1
                continue
            children.setdefault(int(relating[1:]), []).extend(refs(related))
            if len(args) > 6 and any(t and t != "$" for t in args[6:]):
                add("IFC-03", "WARN", f"#{eid} RelAggregates 列表后有多余参数，可能未被解析")
        elif typ == "IFCRELCONTAINEDINSPATIALSTRUCTURE":
            if len(args) < 6:
                add("IFC-03", "FAIL", f"#{eid} RelContainedInSpatialStructure 参数不足")
                broken_rels += 1
                continue
            related, structure = args[4], args[5]
            if not related.startswith("("):
                add("IFC-03", "FAIL",
                    f"#{eid} RelatedElements 不是列表：{related[:40]!r}（构件包含关系丢失）")
                broken_rels += 1
                continue
            if not re.fullmatch(r"#\d+", structure):
                add("IFC-03", "FAIL", f"#{eid} RelatingStructure={structure!r} 不是实体引用")
                broken_rels += 1
                continue
            contained.update(refs(related))
            children.setdefault(int(structure[1:]), []).extend(refs(related))
    roots = [eid for eid, v in ents.items() if v[0] == "IFCPROJECT"]
    if not roots:
        add("IFC-03", "FAIL", "没有 IfcProject")
    seen, stack = set(roots), list(roots)
    while stack:
        cur = stack.pop()
        for child in children.get(cur, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    orphans = [eid for eid in geo_products if eid not in contained and eid not in seen]
    if geo_products and not any(v[0] == "IFCRELCONTAINEDINSPATIALSTRUCTURE" for v in ents.values()):
        add("IFC-03", "FAIL",
            f"全文件无 IfcRelContainedInSpatialStructure：{len(geo_products)} 个带几何产品"
            "未挂入空间结构，按空间树遍历的查看器将整片不显示")
    if orphans:
        sample = ", ".join(f"#{e}" for e in orphans[:5])
        add("IFC-03", "FAIL",
            f"{len(orphans)}/{len(geo_products)} 个带几何产品是孤儿（未被空间结构包含）：{sample}")

    # IFC-04：几何上下文
    for eid in roots:
        args = ents[eid][1]
        if len(args) > 7:
            for r in refs(args[7]):
                if types.get(r) != "IFCGEOMETRICREPRESENTATIONCONTEXT":
                    add("IFC-04", "FAIL",
                        f"IfcProject(#{eid}) 的 RepresentationContexts 指向 {types.get(r)}(#{r})，"
                        "必须指向 IfcGeometricRepresentationContext")
    declared = set()
    for eid in roots:
        args = ents[eid][1]
        if len(args) > 7:
            declared |= {r for r in refs(args[7])
                         if types.get(r) == "IFCGEOMETRICREPRESENTATIONCONTEXT"}
    for eid, (typ, args, _raw) in ents.items():
        if typ == "IFCGEOMETRICREPRESENTATIONSUBCONTEXT" and len(args) > 6:
            declared |= {eid} if set(refs(args[6])) & declared else set()
    for eid, (typ, args, _raw) in ents.items():
        if typ == "IFCSHAPEREPRESENTATION" and args:
            for r in refs(args[0]):
                if r not in declared:
                    add("IFC-04", "FAIL",
                        f"#{eid} ShapeRepresentation 的上下文 #{r} 未挂入 Project 声明的几何上下文")

    # IFC-06：挤出实体
    depths = []
    for eid, (typ, args, _raw) in ents.items():
        if typ != "IFCEXTRUDEDAREASOLID" or len(args) < 4:
            continue
        if args[2] == "$":
            add("IFC-06", "FAIL", f"#{eid} ExtrudedAreaSolid.ExtrudedDirection 为 $（必填）")
        d = args[3].strip()
        if not re.match(r"^-?\d+\.\d*(E[+-]?\d+)?$", d, re.I):
            add("IFC-06", "FAIL",
                f"#{eid} 挤出深度 {d!r} 不是实数字面量（须写成 2038. 而非 2038，否则被当 INTEGER）")
        else:
            depths.append(abs(float(d.rstrip("E").split("E")[0]) if "E" not in d.upper() else float(d)))

    # IFC-07：单位与量级启发式
    prefix = None
    for eid, (typ, args, _raw) in ents.items():
        if typ == "IFCSIUNIT" and len(args) >= 4 and ".LENGTHUNIT." in args[1].upper():
            prefix = args[2].upper()
    coords = []
    for eid, (typ, args, _raw) in ents.items():
        if typ == "IFCCARTESIANPOINT" and args:
            coords += [abs(float(x)) for x in re.findall(r"-?\d+\.?\d*", args[0])]
    if depths and prefix == ".MILLI.":
        med = statistics.median(depths)
        if med > 5:
            add("IFC-07", "WARN",
                f"长度单位声明为毫米，但挤出深度中位数 {med:g}（疑似按米建模）："
                "若几何确为米，模型在查看器中会缩小 1000 倍，近乎不可见")
    if depths and prefix == "$":
        med = statistics.median(depths)
        if med > 1000:
            add("IFC-07", "WARN",
                f"长度单位声明为米，但挤出深度中位数 {med:g}（疑似按毫米建模）：请核对单位")

    # IFC-08：GUID 与转义
    guids = {}
    for eid in geo_products:
        g = ents[eid][1][0].strip().strip("'") if ents[eid][1] else ""
        guids.setdefault(g, []).append(eid)
    dups = {g: v for g, v in guids.items() if g and len(v) > 1}
    if dups:
        add("IFC-08", "FAIL",
            f"{len(dups)} 组重复 GUID（同名元素哈希撞车，需加序号盐）："
            + "; ".join(f"{g}×{len(v)}" for g, v in list(dups.items())[:3]))
    if not ascii_clean:
        add("IFC-08", "WARN",
            "文件含未转义的非 ASCII 字节：中文应按 STEP \\X2\\XXXX\\X0\\ 转义，"
            "直接写 UTF-8 会被按 latin-1 读取的查看器读坏")

    # IFC-09：膨胀预警
    if geo_products:
        per = len(raw) / len(geo_products)
        if per > 20000:
            add("IFC-09", "WARN",
                f"平均每几何产品 {per/1024:.0f} KB：疑似逐段重建轮廓，"
                "应共用 IfcArbitraryClosedProfileDef（实测曾因此膨胀到 140 MB）")

    stats = {"entities": len(ents), "geometric_products": len(geo_products),
             "orphans": len(orphans), "extrusions": len(depths),
             "length_unit_prefix": prefix, "bytes": len(raw)}
    return findings, stats


def geometry_reread(path: str) -> dict:
    """可选：ifcopenshell 独立反读，散度定理求体积（脱离生成端的第三条路径）。"""
    try:
        import ifcopenshell
        import ifcopenshell.geom
    except ImportError:
        return {"status": "NOT_RUN", "reason": "ifcopenshell 未安装（可选依赖）"}
    f = ifcopenshell.open(path)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    n, ntri, vol, failures = 0, 0, 0.0, []
    for el in f.by_type("IfcProduct"):
        if not getattr(el, "Representation", None):
            continue
        try:
            shp = ifcopenshell.geom.create_shape(settings, el)
            v, fc = shp.geometry.verts, shp.geometry.faces
            s = 0.0
            for i in range(0, len(fc), 3):
                a = v[fc[i] * 3:fc[i] * 3 + 3]
                b = v[fc[i + 1] * 3:fc[i + 1] * 3 + 3]
                c = v[fc[i + 2] * 3:fc[i + 2] * 3 + 3]
                s += (a[0] * (b[1] * c[2] - b[2] * c[1])
                      - a[1] * (b[0] * c[2] - b[2] * c[0])
                      + a[2] * (b[0] * c[1] - b[1] * c[0]))
            vol += abs(s) / 6.0
            n += 1
            ntri += len(fc) // 3
        except Exception as exc:  # 单个构件失败不拖垮整批
            failures.append({"id": el.id(), "name": str(getattr(el, "Name", "")),
                             "error": str(exc)[:200]})
    return {"status": "PASS" if not failures else "FAIL", "elements": n,
            "triangles": ntri, "volume_model_units3": round(vol, 3),
            "failures": failures[:20], "failure_count": len(failures)}


def cmd_check(a):
    if not os.path.isfile(a.file):
        print(f"[ERR] 文件不存在：{a.file}")
        return 2
    findings, stats = validate_text(a.file)
    fails = [f for f in findings if f.level == "FAIL"]
    warns = [f for f in findings if f.level == "WARN"]
    print("=" * 78)
    print(f"IFC 结构校验：{a.file}")
    print(f"实体 {stats['entities']} / 带几何产品 {stats['geometric_products']} / "
          f"挤出实体 {stats['extrusions']} / 长度单位前缀 {stats['length_unit_prefix']}")
    print("=" * 78)
    for f in findings[:12 if len(findings) > 12 else len(findings)]:
        print(f"[{f.level}] {f.rule} {RULES[f.rule]}\n    {f.msg}")
    if not findings:
        print("[PASS] 结构检查项全部通过（不代表几何与合同口径已验证）")
    elif len(findings) > 12:
        # 同一缺陷常在每个构件上重复出现，逐条打印会淹没真正的结构性问题
        print(f"... 共 {len(findings)} 条，按规则汇总：")
        from collections import Counter
        for rule, cnt in sorted(Counter(f.rule for f in findings).items()):
            lv = "FAIL" if any(f.rule == rule and f.level == "FAIL" for f in findings) else "WARN"
            print(f"  {rule} × {cnt}（{lv}）")
    result = {"file": a.file, "stats": stats,
              "findings": [f.as_dict() for f in findings],
              "fail": len(fails), "warn": len(warns)}
    if a.geometry:
        geo = geometry_reread(a.file)
        result["geometry_reread"] = geo
        print("-" * 78)
        print("几何反读：", json.dumps(geo, ensure_ascii=False)[:600])
    if a.json:
        from qto_core import atomic_write_json
        atomic_write_json(a.json, result)
        print("saved", a.json)
    print(f"结论：FAIL {len(fails)} / WARN {len(warns)}（WARN 需项目确认，不自动放行）")
    return 1 if fails else 0


# ---------------------------------------------------------------- selftest
GOOD = """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');
FILE_NAME('good.ifc','2026-10-26T00:00:00',('QS'),(''),'test','test','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPERSON($,'QS',$,$,$,$,$,$);
#2=IFCORGANIZATION($,'QS',$,$,$);
#3=IFCPERSONANDORGANIZATION(#1,#2,$);
#4=IFCAPPLICATION(#2,'1.0','test','T');
#5=IFCOWNERHISTORY(#3,#4,$,.ADDED.,$,$,$,1793000000);
#6=IFCCARTESIANPOINT((0.,0.,0.));
#7=IFCAXIS2PLACEMENT3D(#6,$,$);
#8=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.E-05,#7,$);
#9=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Body','Model',*,*,*,*,#8,$,.MODEL_VIEW.,$);
#10=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);
#11=IFCUNITASSIGNMENT((#10));
#12=IFCLOCALPLACEMENT($,#7);
#13=IFCPROJECT('g0000000000000000000001',#5,'P',$,$,$,$,(#8),#11);
#14=IFCSITE('g0000000000000000000002',#5,'S',$,$,#12,$,$,.ELEMENT.,$,$,$,$,$);
#15=IFCBUILDING('g0000000000000000000003',#5,'B',$,$,#12,$,$,.ELEMENT.,$,$,$);
#16=IFCBUILDINGSTOREY('g0000000000000000000004',#5,'L1',$,$,#12,$,$,.ELEMENT.,0.);
#17=IFCRELAGGREGATES('g0000000000000000000005',#5,$,$,#13,(#14));
#18=IFCRELAGGREGATES('g0000000000000000000006',#5,$,$,#14,(#15));
#19=IFCRELAGGREGATES('g0000000000000000000007',#5,$,$,#15,(#16));
#20=IFCCARTESIANPOINT((0.,0.));
#21=IFCCARTESIANPOINT((2.,0.));
#22=IFCCARTESIANPOINT((2.,1.));
#23=IFCCARTESIANPOINT((0.,1.));
#24=IFCPOLYLOOP((#20,#21,#22,#23));
#25=IFCARBITRARYCLOSEDPROFILEDEF(.AREA.,'sec',#24);
#26=IFCDIRECTION((0.,0.,1.));
#27=IFCEXTRUDEDAREASOLID(#25,#7,#26,3.);
#28=IFCSHAPEREPRESENTATION(#9,'Body','SweptSolid',(#27));
#29=IFCPRODUCTDEFINITIONSHAPE($,$,(#28));
#30=IFCBUILDINGELEMENTPROXY('g0000000000000000000008',#5,'E1',$,$,#12,#29,$,$);
#31=IFCBUILDINGELEMENTPROXY('g0000000000000000000009',#5,'E2',$,$,#12,#29,$,$);
#32=IFCRELCONTAINEDINSPATIALSTRUCTURE('g0000000000000000000010',#5,$,$,(#30,#31),#16);
ENDSEC;
END-ISO-10303-21;
"""

VARIANTS = {
    "IFC-02": ("#12,#29", "#7,#29", "placement 指向 Axis2Placement3D"),
    "IFC-03": ("#32=IFCRELCONTAINEDINSPATIALSTRUCTURE('g0000000000000000000010',#5,$,$,(#30,#31),#16);\n", "", "删除空间包含关系"),
    "IFC-03b": ("#19=IFCRELAGGREGATES('g0000000000000000000007',#5,$,$,#15,(#16));",
                "#19=IFCRELAGGREGATES('g0000000000000000000007',#5,$,$,$,#15,(#16));",
                "关系行多写一个 $（真实文件缺陷：RelatingObject 变 $、子对象被丢弃）"),
    "IFC-04": ("(#8),#11", "(#14),#11", "项目上下文指向 IfcSite"),
    "IFC-05": ("#12,#29,$,$)", "#12,#28,$,$)", "Representation 直接塞 ShapeRepresentation"),
    "IFC-06": ("#7,#26,3.)", "#7,$,3.)", "挤出方向为 $"),
    "IFC-06b": ("#7,#26,3.)", "#7,#26,3)", "深度写成整数"),
    "IFC-07": ("#10=IFCSIUNIT(*,.LENGTHUNIT.,$,.METRE.);",
               "#10=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);\n#40=IFCEXTRUDEDAREASOLID(#25,#7,#26,30.);", "毫米单位+米制深度"),
    "IFC-08": ("#31=IFCBUILDINGELEMENTPROXY('g0000000000000000000009'",
               "#31=IFCBUILDINGELEMENTPROXY('g0000000000000000000008'", "GUID 重复"),
}


def cmd_selftest(a):
    tmp = tempfile.mkdtemp(prefix="ifcval_")
    good_path = os.path.join(tmp, "good.ifc")
    io.open(good_path, "w", encoding="ascii", newline="\n").write(GOOD)
    findings, stats = validate_text(good_path)
    bad = [f for f in findings if f.level in ("FAIL", "WARN")]
    ok = True
    if bad:
        ok = False
        print("[FAIL] 好样本出现误报：", [f.as_dict() for f in bad])
    else:
        print(f"[PASS] 好样本零误报（实体 {stats['entities']}，几何产品 {stats['geometric_products']}）")
    for tag, (old, new, desc) in VARIANTS.items():
        if old not in GOOD:
            print(f"[FAIL] 用例 {tag} 构造失败：锚点不在好样本中")
            ok = False
            continue
        path = os.path.join(tmp, f"bad_{tag}.ifc")
        io.open(path, "w", encoding="ascii", newline="\n").write(GOOD.replace(old, new, 1))
        findings, _ = validate_text(path)
        rule = tag.rstrip("b") if tag.endswith("b") else tag
        hits = [f for f in findings if f.rule == rule and f.level in ("FAIL", "WARN")]
        if hits:
            print(f"[PASS] {tag}（{desc}）被捕获：{hits[0].msg[:70]}")
        else:
            print(f"[FAIL] {tag}（{desc}）未捕获")
            ok = False
    # 非 ASCII 样本
    path = os.path.join(tmp, "bad_utf8.ifc")
    io.open(path, "w", encoding="utf-8", newline="\n").write(GOOD.replace("'E1'", "'潜堤'"))
    findings, _ = validate_text(path)
    if any(f.rule == "IFC-08" for f in findings):
        print("[PASS] 未转义中文被捕获")
    else:
        print("[FAIL] 未转义中文未捕获")
        ok = False
    print("SELFTEST:", "PASS" if ok else "FAIL", "（临时目录：", tmp, "）")
    return 0 if ok else 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ifc_validate",
                                 description="IFC 结构校验：结构合法性/空间闭合/单位量纲/可选几何反读")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check", help="校验一个 IFC 文件")
    p.add_argument("--file", required=True)
    p.add_argument("--json", default="")
    p.add_argument("--geometry", action="store_true", help="用 ifcopenshell 独立反读求体积（可选依赖）")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("selftest", help="内嵌好/坏样本自证")
    p.set_defaults(func=cmd_selftest)
    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
