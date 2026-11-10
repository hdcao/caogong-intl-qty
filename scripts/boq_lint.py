#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
boq_lint.py -- structural lint for a BOQ workbook (xlsx/csv).

qa_guards.py checks the engine's own results.json. Real projects also ship
hand-built or previously generated BOQ workbooks; this tool checks those.

Checks (L01-L10):
  L01 FAIL  重复编码          同一 (分组+编码+单位) 出现多次 (P21)
  L02 FAIL  层级断裂          细目编码的直接父级标题行不存在 (P50 实测: 批量插行吃掉了 2.3.1)
  L03 FAIL  孤儿细目          编码为空或无法解析归入任何分部
  L04 FAIL  分段闭合          子细目之和 != 父行值 (P43 实测: 差 182,555)
  L05 FAIL  跨表闭合          A 表汇总值 != B 表明细之和 (P42 实测: 8,884 vs 6,316)
  L06 WARN  零量无理由        数量为 0 而备注为空 (P34)
  L07 WARN  同码双单位        同一编码出现两种单位 (P22)
  L08 WARN  名称算式不自洽    名称里的 "a x b" 与数量列不符 (P38)
  L09 WARN  必填列缺失        计算式 / 图纸依据 / 口径 为空
  L10 WARN  口径禁用词        "已计量/已确认/复核无误" 之类伪闭环词

`selftest` is the meta-gate: it writes a fixture workbook containing one known
defect per check and asserts every one is caught. Capture rate must be 100%;
a check that crashes is a SKIP with a reason, never a pass.

Exit codes: 0 clean / 1 findings present / 2 the check could not run.
"""

from __future__ import print_function

import argparse
import csv
import json
import os
import re
import sys
import math
import tempfile

# --- 控制台编码兜底（v2.1.0）-------------------------------------------
# 中文 Windows 控制台默认 cp936，无法编码 ✓/✗/★ 等符号：一条 INFO 输出
# 就会抛 UnicodeEncodeError，让**干净的表**被判成"检查不通过"（实测
# gate_sheet_formula.py 在 ACP=936 下必崩）。这里保留控制台编码，只把
# 不可编码字符替换掉——不换编码，避免中文变乱码。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
# ------------------------------------------------------------------------


try:
    import colmap
except ImportError:
    colmap = None

__version__ = "3.1.0"

FAIL, WARN, INFO = "FAIL", "WARN", "INFO"
LEVEL_ORDER = {FAIL: 0, WARN: 1, INFO: 2}

BANNED_WORDS = ["已计量", "已确认", "复核无误", "确认无误", "无误", "已复核", "已审定"]

HEADER_UNITS = {"", "-", "—", "/", "项", None}

# 小计/合计行：无编码是设计使然（它汇总的是上面若干有编码的明细行），
# 不能按"孤儿细目"报 FAIL。识别放在模型层属性上，判据只问"这是不是小计行"，
# 不在判据里逐行猜名字——那正是 G-DW 第一版误报 5 处的同类错误。
SUBTOTAL_WORDS = ("小计", "合计", "共计", "subtotal", "total", "sum")

# ── L08 量纲相容守卫 ────────────────────────────────────────────────────────
# 名称里 "10 片/跨 × 13 跨 = 130 片" 的乘积是**片数（计数）**，而该行单位列是一个体积单位（建议用 M3；CM 仅在项目审批映射后有效）。
# 名称自己都写明结果是"片"——拿 130 去比 3403.5 是跨量纲比对，必误报。
# 这与 G-DW 的 [R-DW-1]（把含量单位当工程量单位）是同一条根：
#   **不校验量纲就比对数字**。这里是该根因的第三处暴露。
# 规则：名称自带 "= <数> <计数单位>" 且本行单位不是计数单位 → 判为不可比，跳过。
_NAME_COUNT_RESULT = re.compile(
    r"=\s*[\d.,]+\s*(片|根|座|个|套|块|道|处|幅|跨|榀|盏|台|组|层|侧|孔|肢)")
_ROW_COUNT_UNITS = {"ea", "u", "no", "nb", "个", "座", "根", "片", "套", "道", "处",
                    "块", "组", "台", "榀", "幅", "跨", "层", "侧", "孔", "肢"}


def _dims_incompatible(name, row_unit):
    """名称乘积的计数结果 vs 本行的度量单位 —— 量纲不同则不可比。"""
    if _NAME_COUNT_RESULT.search(name or ""):
        if (row_unit or "").strip().lower() not in _ROW_COUNT_UNITS:
            return True
    return False

_MUL_SEP = re.compile(r"[×xX*＊]")
_FIRST_NUM = re.compile(r"(\d+(?:\.\d+)?)")
# codes may carry a letter suffix (2.2.1.03a) or be non-numeric (CT-D, PS-01)
_CODE_RE = re.compile(r"^[0-9A-Za-z]+(?:[.\-][0-9A-Za-z]+)*$")
# unit-scale artefacts: a dimension string in mm vs a value in m / m3 is NOT an inconsistency
_SCALE_ARTEFACTS = (1e3, 1e6, 1e9)
# L08 guard: only treat "a × b" as a quantity formula when every factor carries a
# counting/length unit. "4C×35mm²" / "25m×335W" / "3000×3000×750" are SPECIFICATIONS
# or DIMENSIONS, not formulas -- flagging them is the classic false positive.
_COUNT_UNITS = ("根", "座", "个", "套", "块", "道", "处", "幅", "跨", "榀",
                "盏", "台", "片", "组", "层", "侧", "根", "延米")
# lookbehind (not ^) so "25m 高杆灯" is not read as "25 × m"; the m must stand alone
_STANDALONE_M = re.compile(r"(?<=[\s(（])m(?:$|[\s)）,，/])")


def _seg_has_quantity_unit(seg):
    tail = seg[_FIRST_NUM.search(seg).end():]
    if any(u in tail for u in _COUNT_UNITS):
        return True
    return bool(_STANDALONE_M.search(tail))


def _name_product(name):
    """'灯杆 3 根 × 15 m' -> (45.0, [3.0, 15.0]);  None if not a quantity formula."""
    parts = _MUL_SEP.split(name)
    if len(parts) < 2:
        return None
    nums = []
    for p in parts:
        m = _FIRST_NUM.search(p)
        if not m:
            return None
        if not _seg_has_quantity_unit(p):
            return None          # a spec / dimension string, not a formula
        nums.append(float(m.group(1)))
    prod = 1.0
    for n in nums:
        if not math.isfinite(n):
            return None
        prod *= n
    return prod, nums


# --------------------------------------------------------------------------- io

def _openpyxl():
    try:
        import openpyxl
        return openpyxl
    except ImportError:
        sys.stderr.write(
            "ERROR: boq_lint needs openpyxl for .xlsx input.\n"
            "       pip install openpyxl      (or convert to .csv first)\n")
        raise SystemExit(2)


def load_sheet(path, sheet):
    """Return list of row-lists (header row not stripped)."""
    if path.lower().endswith(".csv"):
        # 中文 Excel 默认导出 GBK（cp936）CSV。只按 utf-8 读会 UnicodeDecodeError，
        # 用户看到的是"脚本崩了"，而真正原因是编码。按 utf-8-sig → gb18030 兜底。
        for enc in ("utf-8-sig", "gb18030"):
            try:
                with open(path, "r", encoding=enc, newline="") as fh:
                    return [r for r in csv.reader(fh)]
            except UnicodeDecodeError:
                continue
        sys.stderr.write("ERROR: CSV 无法按 utf-8 / gb18030 解码（文件可能损坏）：%s\n"
                         % path)
        raise SystemExit(2)
    opx = _openpyxl()
    wb = opx.load_workbook(path, data_only=True)
    if sheet is None:
        ws = wb[wb.sheetnames[0]]
    else:
        if sheet not in wb.sheetnames:
            sys.stderr.write("ERROR: sheet %r not in workbook. available: %s\n"
                             % (sheet, ", ".join(wb.sheetnames)))
            raise SystemExit(2)
        ws = wb[sheet]
    out = []
    for row in ws.iter_rows(values_only=True):
        out.append(["" if v is None else v for v in row])
    wb.close()
    return out


def _cell(row, idx):
    """1-based column index -> value (str)."""
    if idx is None or idx < 1 or idx > len(row):
        return ""
    v = row[idx - 1]
    return "" if v is None else str(v).strip()


def _num(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- model

class Item(object):
    __slots__ = ("r", "code", "name", "unit", "value", "note", "basis", "draw", "conf")

    def __init__(self, r, code, name, unit, value, note, basis, draw, conf):
        self.r = r
        self.code = code
        self.name = name
        self.unit = unit
        self.value = value
        self.note = note
        self.basis = basis
        self.draw = draw
        self.conf = conf

    @property
    def is_header(self):
        """A section title row: has a code but carries no quantity/unit."""
        return bool(self.code) and (self.value is None or self.unit in HEADER_UNITS)

    @property
    def is_subtotal(self):
        """A 小计/合计 row: no code by design, aggregates coded detail rows above."""
        if self.code:
            return False
        low = (self.name or "").strip().lower()
        return any(w in low for w in SUBTOTAL_WORDS)


def build_items(rows, cfg, skip_header_rows=1):
    items = []
    for i, row in enumerate(rows, start=1):
        if i <= skip_header_rows:
            continue
        code = _cell(row, cfg["code"])
        name = _cell(row, cfg["name"])
        if not code and not name:
            continue
        items.append(Item(
            r=i,
            code=code,
            name=name,
            unit=_cell(row, cfg["unit"]) if cfg.get("unit") else "",
            value=_num(_cell(row, cfg["value"])) if cfg.get("value") else None,
            note=_cell(row, cfg["note"]) if cfg.get("note") else "",
            basis=_cell(row, cfg["basis"]) if cfg.get("basis") else "",
            draw=_cell(row, cfg["draw"]) if cfg.get("draw") else "",
            conf=_cell(row, cfg["conf"]) if cfg.get("conf") else "",
        ))
    return items


def parent_of(code):
    if "." not in code:
        return ""
    return code.rsplit(".", 1)[0]


def resolve(items, code, cfg):
    """Value of `code`: if it has children in the same list, sum them; else its own value."""
    kids = [it for it in items
            if it.code != code and it.code.startswith(code + ".") and not it.is_header]
    if kids:
        vals = [it.value for it in kids if it.value is not None]
        return (sum(vals) if vals else None), kids
    for it in items:
        if it.code == code:
            return it.value, [it]
    return None, []


# --------------------------------------------------------------------------- checks

def run_checks(items, cfg, extra_sheets=None):
    """Return list of findings dicts."""
    f = []
    add = lambda lid, lvl, msg, rows=None: f.append(
        {"id": lid, "level": lvl, "msg": msg, "rows": rows or []})

    codes = {}
    for it in items:
        if it.code:
            codes.setdefault(it.code, []).append(it)

    # L01 duplicate (code + unit)
    seen = {}
    for it in items:
        if not it.code or it.is_header:
            continue
        key = (it.code, it.unit)
        seen.setdefault(key, []).append(it)
    for key, group in sorted(seen.items()):
        if len(group) > 1:
            add("L01", FAIL, "重复编码 %s (单位 %s) 出现 %d 次 — 编码需按 (分组+编号+单位) 唯一"
                % (key[0], key[1] or "-", len(group)), [g.r for g in group])

    # L02 broken hierarchy + L03 orphan
    for it in items:
        if not it.code:
            if it.is_subtotal:
                # 小计行无编码属正常；只要求它带量或带名，两者皆无才是真空行
                if it.value is None and not it.name:
                    add("L03", FAIL, "第 %d 行疑似小计/合计但既无名称也无数量（空行）: %r"
                        % (it.r, it.name[:40]), [it.r])
                continue
            if it.value is not None or it.name:
                add("L03", FAIL, "第 %d 行无编码，无法归入任何分部（孤儿细目）: %s"
                    % (it.r, it.name[:40]), [it.r])
            continue
        if not _CODE_RE.match(it.code):
            if it.value is not None:
                add("L03", FAIL, "第 %d 行编码 %r 不是 x.y.z 形式，无法归入分部: %s"
                    % (it.r, it.code, it.name[:40]), [it.r])
            continue
        p = parent_of(it.code)
        # Depth-2 codes (e.g. "2.1") legitimately have no "2" row; only require the
        # parent from depth 3 down -- that is where a missing section title bites.
        if p and it.code.count(".") >= 2 and p not in codes:
            add("L02", FAIL, "层级断裂：%s 的父级标题 %s 不存在（分部标题缺失）— %s"
                % (it.code, p, it.name[:40]), [it.r])

    # L04 in-sheet rollup closure
    for spec in cfg.get("rollup", []):
        code = spec
        parent_val, kids = resolve(items, code, cfg)
        own = [it for it in items if it.code == code and it.value is not None]
        if not own:
            continue
        pv = own[0].value
        kv = parent_val if kids else None
        if kv is None:
            continue
        if abs((pv or 0) - (kv or 0)) > cfg["tol"]:
            add("L04", FAIL, "分段不闭合：%s 合计 %s != 子项之和 %s（差 %s，容差 %s）"
                % (code, pv, kv, round((pv or 0) - (kv or 0), 3), cfg["tol"]),
                [own[0].r] + [k.r for k in kids])

    # L05 cross-sheet closure
    for spec in cfg.get("closure", []):
        if "==" not in spec:
            continue
        left, right = [s.strip() for s in spec.split("==", 1)]
        try:
            lv = _resolve_ref(left, items, cfg, extra_sheets)
            rv = _resolve_ref(right, items, cfg, extra_sheets)
        except Exception as e:
            add("L05", INFO, "跨表闭合 %s 无法执行：%s" % (spec, e))
            continue
        if lv[0] is None or rv[0] is None:
            add("L05", INFO, "跨表闭合 %s 缺值（左=%s 右=%s），未能判定" % (spec, lv[0], rv[0]))
            continue
        if abs(lv[0] - rv[0]) > cfg["tol"]:
            add("L05", FAIL, "跨表矛盾：%s = %s  !=  %s = %s（差 %s）"
                % (left, lv[0], right, rv[0], round(lv[0] - rv[0], 3)),
                [lv[1], rv[1]])

    # L06 zero without reason
    for it in items:
        if it.value == 0 and not it.note:
            add("L06", WARN, "零量无理由：%s %s = 0，备注为空（应写明是漏算还是本项不设）"
                % (it.code, it.name[:30]), [it.r])

    # L07 same code, two units
    byc = {}
    for it in items:
        if it.code and it.unit:
            byc.setdefault(it.code, set()).add(it.unit)
    for code, units in sorted(byc.items()):
        if len(units) > 1:
            add("L07", WARN, "同码双单位：%s 出现 %s — 取单位与描述时必须按单位匹配那一条"
                % (code, "/".join(sorted(units))),
                [i.r for i in items if i.code == code])

    # L08 formula inside the name vs the value column
    for it in items:
        if it.value is None or not it.name:
            continue
        got = _name_product(it.name)
        if not got:
            continue
        prod, nums = got
        if it.value == 0:
            continue
        # 量纲不相容则不可比：名称写"= 130 片"而本行单位是非计数度量单位
        if _dims_incompatible(it.name, it.unit):
            continue
        # "3000×3000×750" is a dimension string in mm, not a quantity formula
        if len(nums) >= 3 and all(n >= 50 for n in nums):
            continue
        # mm -> m / m2 / m3 scale artefacts are not inconsistencies
        ratio = prod / it.value if it.value else 0.0
        if any(ratio and abs(ratio - s) / s < 0.05 for s in _SCALE_ARTEFACTS):
            continue
        if abs(prod - it.value) > max(cfg["tol"], abs(it.value) * 0.01):
            add("L08", WARN, "名称算式与数量不符：%s 名称累乘(%s)=%.3f，数量列却是 %s"
                % (it.code, "×".join(str(int(n)) if n == int(n) else str(n) for n in nums),
                   prod, it.value), [it.r])

    # L09 required columns empty
    # 小计/合计行的必填项与明细行不同：它必须写**计算式**（汇总了哪些行，否则不可审），
    # 但没有单一图纸出处，且行名已含分部口径——故图纸依据与口径对小计行豁免。
    # 这与 L03 是同一条原则：这一行在表里扮演什么角色，决定它该填什么。
    req = [("basis", "计算式"), ("draw", "图纸依据"), ("conf", "口径")]
    for it in items:
        if it.is_header:
            continue
        if it.is_subtotal:
            req_now = [("basis", "计算式")]
        else:
            req_now = req
        missing = [label for key, label in req_now
                   if cfg.get(key) and not getattr(it, key)]
        if missing:
            add("L09", WARN, "必填列缺失：%s %s 缺 %s"
                % (it.code, it.name[:26], "、".join(missing)), [it.r])

    # L10 banned vocabulary
    for it in items:
        txt = " ".join([it.conf or "", it.note or ""])
        hit = [w for w in BANNED_WORDS if w in txt]
        if hit:
            add("L10", WARN, "口径禁用词：%s 用了 %s — 会让人误以为已闭环；"
                             "改用 图纸确认/推算/估算/待核" % (it.code, "、".join(hit)), [it.r])

    f.sort(key=lambda x: (LEVEL_ORDER[x["level"]], x["id"], x["rows"]))
    return f


def _resolve_ref(ref, main_items, cfg, extra_sheets):
    """ref: 'SHEET:CODE' or 'CODE'. Returns (value, row_no)."""
    sheet, code = (ref.split(":", 1) + [None])[:2] if ":" in ref else (None, ref)
    if sheet is None or sheet == cfg.get("sheet"):
        items = main_items
    else:
        items = (extra_sheets or {}).get(sheet)
        if items is None:
            raise ValueError("sheet %r not loaded" % sheet)
    # prefer the headline value (汇总行); fall back to the sum of its children (明细之和)
    own = [it for it in items if it.code == code and it.value is not None]
    if own:
        return own[0].value, own[0].r
    kids = [it for it in items
            if it.code != code and it.code.startswith(code + ".") and not it.is_header
            and it.value is not None]
    if kids:
        return sum(k.value for k in kids), kids[0].r
    return None, None


# --------------------------------------------------------------------------- report

def summarise(findings):
    c = {"FAIL": 0, "WARN": 0, "INFO": 0}
    for f in findings:
        c[f["level"]] = c.get(f["level"], 0) + 1
    return c


def render_text(findings, title):
    lines = ["=" * 78, "BOQ LINT  %s" % title, "=" * 78]
    if not findings:
        lines.append("clean")
    for f in findings:
        lines.append("[%-4s] %-4s %s" % (f["level"], f["id"], f["msg"]))
    c = summarise(findings)
    lines.append("-" * 78)
    lines.append("FAIL=%d  WARN=%d  INFO=%d" % (c["FAIL"], c["WARN"], c["INFO"]))
    return "\n".join(lines)


def write_md(path, findings, title):
    lines = ["# BOQ LINT — %s" % title, ""]
    lines.append("| Level | ID | 说明 | 行 |")
    lines.append("|---|---|---|---|")
    for f in findings:
        lines.append("| %s | %s | %s | %s |" % (
            f["level"], f["id"], f["msg"].replace("|", "\\|"),
            ", ".join(str(r) for r in f["rows"])))
    c = summarise(findings)
    lines.append("")
    lines.append("**FAIL=%d WARN=%d INFO=%d**" % (c["FAIL"], c["WARN"], c["INFO"]))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- cmd check

def cmd_check(args):
    # ---- 列映射：表头定角色 + 数据验角色 ----
    # 注意：boq_lint 的 --basis-col 语义是「计算式」，对应 colmap 的 formula
    # 角色；而 gate_dimension_wording 的 --basis-col 语义是「口径声明」，
    # 对应 basis 角色。**同名列不同义**，照抄参数名必然传错。
    role_of = {"code": "code_col", "name": "name_col", "unit": "unit_col",
               "value": "value_col", "note": "note_col",
               "formula": "basis_col", "draw": "draw_col", "conf": "conf_col"}
    explicit = {}
    for role, attr in role_of.items():
        v = getattr(args, attr, 0)
        if v and v > 0:
            explicit[role] = v
    cols = {"code": args.code_col, "name": args.name_col, "unit": args.unit_col,
            "value": args.value_col, "note": args.note_col,
            "basis": args.basis_col, "draw": args.draw_col, "conf": args.conf_col}

    if colmap is not None and not args.no_auto_map:
        try:
            import openpyxl
            wb0 = openpyxl.load_workbook(args.boq, data_only=True)
            ws0 = wb0[args.sheet] if (args.sheet and args.sheet in wb0.sheetnames) else wb0.active
            roles = ["code", "name", "unit", "value", "note", "formula",
                     "draw", "conf", "basis", "evidence", "pos", "price", "amount"]
            detected, col_headers, hrow = colmap.detect_columns(
                ws0, roles, header_row=args.header_row or None)
            for ln in colmap.render_map(
                    detected, explicit,
                    title="列映射报告（表头行 %d · 表头定角色+数据验角色）" % hrow):
                print(ln)
            errs, warns = colmap.audit_columns(
                detected, explicit, required=("code", "name", "unit", "value"),
                col_headers=col_headers)
            for w in warns:
                print("[WARN] %s" % w)
            if errs:
                for e in errs:
                    print("[ERR] %s" % e)
                print("-" * 78)
                print("列映射未通过核对，门禁拒绝运行（退出码 2）。")
                return 2
            for role in roles:
                v = colmap.resolve(explicit, detected, role)
                if v and not cols.get(role):
                    cols[role] = v
            # boq_lint 的 basis 键语义是「计算式」，必须取自 colmap 的
            # **formula** 角色，不能取 basis 角色（那是口径声明，属 G-DW 的
            # 职责）。接线时按 cfg 键名去拿 role 名，正是「同名列不同义」
            # 的陷阱——此处按语义显式重接一次。
            cols["basis"] = (explicit.get("formula")
                             or (detected.get("formula") or {}).get("col") or 0)
            # 计算式列定位不到时，L09 的「计算式」检查会被静默跳过。
            # 静默跳过与「该项干净」在输出上无法区分，必须显式告知。
            if not cols["basis"]:
                print("[WARN] 未定位到计算式列，L09 的计算式检查已跳过。"
                      "若本表把计算式与口径声明合写在一列，"
                      "请显式传 --basis-col <列号> 让该项生效。")
            # 表头行定位到哪，就从哪一行之后开始读数据
            if args.header_row or args.header_rows:
                args.header_rows = max(args.header_rows, args.header_row or hrow)
            else:
                args.header_rows = max(args.header_rows, hrow)
            print("-" * 78)
        except ImportError:
            print("[WARN] 无 openpyxl，跳过列映射识别")
        except Exception as e:
            print("[WARN] 列映射识别失败，退回显式参数：%s" % e)

    cfg = {
        "code": cols["code"], "name": cols["name"], "unit": cols["unit"],
        "value": cols["value"], "note": cols["note"],
        "basis": cols["basis"], "draw": cols["draw"], "conf": cols["conf"],
        "tol": args.tol, "sheet": args.sheet,
        "rollup": args.rollup or [], "closure": args.closure or [],
    }
    rows = load_sheet(args.boq, args.sheet)
    items = build_items(rows, cfg, skip_header_rows=args.header_rows)

    # ---- 零数据行守卫（v2.1.1）----
    # L01~L12 全部以 items 为分母。items 为空时一条都触发不了，输出
    # "FAIL=0 WARN=0 INFO=0 / clean" 并 exit 0——读者会当成"表通过检查"。
    # 这与 G-UNIT / G-PROP 的空转同源，统一判「未执行」。
    if not items:
        print("-" * 78)
        print("[未执行] 扫描到 **0 行工程量条目**（编码+数量同时可读的行）。")
        print("         L01~L12 一条判据都没触发——FAIL=0 不代表表是对的，"
              "只代表没有东西可判。")
        print("         先核对 --sheet / --header-rows / 列映射，"
              "确认表里确实有数据行。")
        print("[未执行] 退出码 2（检查未能运行）——这不是\"发现问题\"，也不是\"干净\"。")
        raise SystemExit(2)

    extra = {}
    for spec in cfg["closure"]:
        for side in spec.split("==", 1):
            side = side.strip()
            if ":" in side:
                sh = side.split(":", 1)[0].strip()
                if sh and sh != args.sheet and sh not in extra:
                    extra[sh] = build_items(load_sheet(args.boq, sh), cfg,
                                            skip_header_rows=args.header_rows)

    findings = run_checks(items, cfg, extra)
    title = "%s [%s]" % (os.path.basename(args.boq), args.sheet or "sheet#1")
    print(render_text(findings, title))

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump({"tool": "boq_lint.check", "version": __version__, "target": title,
                       "counts": summarise(findings), "findings": findings},
                      fh, ensure_ascii=False, indent=1)
    if args.md:
        os.makedirs(os.path.dirname(os.path.abspath(args.md)) or ".", exist_ok=True)
        write_md(args.md, findings, title)

    c = summarise(findings)
    if c["FAIL"]:
        return 1
    return 1 if (c["WARN"] and args.strict) else 0


# --------------------------------------------------------------------------- selftest

FIXTURE_MAIN = [
    ["编码", "名称", "单位", "数量", "备注", "计算式", "图纸依据", "口径"],
    ["2.1", "路基工程", "-", "", "", "", "", ""],
    ["2.1.1", "挖方", "m3", 1000, "与纵断面对账", "A*L=1000", "HA-035001", "图纸确认"],
    ["2.1.2", "填方", "m3", 2000, "", "", "", ""],                       # L09 必填列缺失
    ["2.2", "桥梁工程", "-", "", "", "", "", ""],
    ["2.3.1.01", "桥1 桩基", "m3", 500, "ok", "24*15*1.77", "HA-035116", "图纸确认"],  # L02 父级 2.3.1 缺失
    ["2.1.1", "挖方(另一范围)", "m3", 1000, "dup", "x", "x", "图纸确认"],  # L01 重复编码
    ["2.4", "照明工程", "-", "", "", "", "", ""],
    ["2.4.1", "LED 灯具", "套", 0, "", "", "", ""],                      # L06 零量无理由 + L09
    ["2.4.2", "灯杆 3 根 × 15 m", "座", 12, "", "3*15=45", "HA-035030", "图纸确认"],  # L08 算式不符
    ["2.4.3", "低压电缆", "m", 100, "", "直读", "HA-035028", "已计量"],   # L10 禁用词
    ["2.4.4", "预应力梁板（10 片/跨 × 13 跨 = 130 片，断面 0.75 m²）", "m3", 3403.5,
     "ok", "0.75×4538", "HA-035116", "图纸确认"],   # L08 量纲不相容：乘积 130 是片数，不得与 m³ 比
    ["2.4.3", "低压电缆(另一范围)", "km", 0.1, "", "直读", "HA-035028", "图纸确认"],  # L07 同码双单位
    ["2.5", "涵洞工程", "m3", 5000, "", "直读", "HA-035060", "图纸确认"],  # L04 分段不闭合
    ["2.5.1", "箱涵洞身", "m3", 3000, "", "逐座直读", "HA-035062", "图纸确认"],
    ["", "零散项（无编码）", "m3", 30, "", "x", "x", "图纸确认"],         # L03 孤儿细目
    ["", "小计 桥梁工程", "m3", 1500, "", "2.3.1.01+2.4.x", "HA-035116", "图纸确认"],  # 小计行：无编码属正常，不得报 L03
    ["", "合计", "m3", 6500, "", "Σ 全部分项", "", ""],                  # 合计行：L03/L09 图纸依据·口径均应豁免，但缺计算式仍须报
]

FIXTURE_CULVERT = [
    ["编码", "名称", "单位", "数量", "备注", "计算式", "图纸依据", "口径"],
    ["2.5", "涵洞工程(明细表合计)", "m3", "", "", "", "", ""],
    ["2.5.1", "箱涵洞身", "m3", 2500, "", "逐座直读", "HA-035062", "图纸确认"],
    ["2.5.2", "箱涵附属", "m3", 447, "", "逐座直读", "HA-035078", "图纸确认"],
    ["2.5.3", "圆管涵", "m3", 1553, "", "逐座直读", "HA-035070", "图纸确认"],
]


def cmd_selftest(args):
    # ★v2.1.1：旧默认 args.out="." 会把 boq_lint_fixture.xlsx 与
    #   boq_lint_selftest.json 写进**当前目录**——在 scripts/ 下跑一次就落到
    #   技能包里，随后被打进分发物（实测 v2.1.1 首次打包多出这 2 个文件）。
    #   fixture 是运行时产物，不是源码；默认改为独立临时目录。
    out_dir = args.out or tempfile.mkdtemp(prefix="boq_lint_selftest_")
    os.makedirs(out_dir, exist_ok=True)
    opx = _openpyxl()
    wb = opx.Workbook()
    ws = wb.active
    ws.title = "T"
    for r in FIXTURE_MAIN:
        ws.append(r)
    ws2 = wb.create_sheet("04_涵洞明细")
    for r in FIXTURE_CULVERT:
        ws2.append(r)
    fx = os.path.join(out_dir, "boq_lint_fixture.xlsx")
    wb.save(fx)
    wb.close()

    cfg = {
        "code": 1, "name": 2, "unit": 3, "value": 4, "note": 5,
        "basis": 6, "draw": 7, "conf": 8, "tol": 1.0, "sheet": "T",
        "rollup": ["2.5"], "closure": ["T:2.5==04_涵洞明细:2.5"],
    }
    items = build_items(load_sheet(fx, "T"), cfg, 1)
    extra = {"04_涵洞明细": build_items(load_sheet(fx, "04_涵洞明细"), cfg, 1)}
    findings = run_checks(items, cfg, extra)
    hit = set(f["id"] for f in findings)

    expect = ["L01", "L02", "L03", "L04", "L05", "L06", "L07", "L08", "L09", "L10"]
    missed = [e for e in expect if e not in hit]

    # 反向断言：小计/合计行无编码是设计使然，绝不许被 L03 指证。
    # 只查"missed 为空"证明不了没误报——必须单独盯住这两行的行号。
    SUBTOTAL_ROWS = [i + 1 for i, r in enumerate(FIXTURE_MAIN)
                     if not r[0] and any(w in str(r[1]).lower() for w in SUBTOTAL_WORDS)]
    false_pos = []
    for f in findings:
        if f["id"] != "L03":
            continue
        for r in f.get("rows") or []:
            if r in SUBTOTAL_ROWS:
                false_pos.append((r, f["msg"]))

    # 反向断言二：量纲不相容的名称算式不得被 L08 指证（片数 vs 体积）
    DIM_ROW = FIXTURE_MAIN.index(
        [r for r in FIXTURE_MAIN
         if str(r[1]).startswith("预应力梁板（")][0]) + 1
    dim_false_pos = [(r, f["msg"]) for f in findings if f["id"] == "L08"
                     for r in (f.get("rows") or []) if r == DIM_ROW]

    rep = {
        "tool": "boq_lint.selftest", "version": __version__,
        "fixture": fx, "expected": len(expect), "caught": len(expect) - len(missed),
        "missed": missed,
        "subtotal_rows": SUBTOTAL_ROWS, "false_positive_L03": false_pos,
        "dim_mismatch_row": DIM_ROW, "false_positive_L08": dim_false_pos,
        "capture_rate": round((len(expect) - len(missed)) / float(len(expect)), 4),
        "findings": [{"id": f["id"], "level": f["level"], "msg": f["msg"]} for f in findings],
    }
    with open(os.path.join(out_dir, "boq_lint_selftest.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)
    print(render_text(findings, "selftest fixture"))
    print("\ncapture rate: %.0f%%  (missed: %s)"
          % (rep["capture_rate"] * 100, missed or "none"))
    print("小计/合计行 %s 被 L03 误报: %s"
          % (SUBTOTAL_ROWS, false_pos or "无"))
    print("量纲不相容行 %d 被 L08 误报: %s"
          % (DIM_ROW, dim_false_pos or "无"))
    return 0 if (not missed and not false_pos and not dim_false_pos) else 1


# REVISION LOG —— 判据被实测推翻的记录（写完觉得"够严"不算数）
# ==============================================================================
#
# [R-BOQ-1] 初版 L03：编码为空即判"孤儿细目"FAIL。
#   实测误报 8 处（VIADUC PK295+300 开项算量表 1_成本取量总表）：
#     第 7/10/15/20/24/27/30 行「小计 基础混凝土 (m³)」「小计 桩根数 (EA)」…
#     第 52 行「小计 暂定钢筋 (t) / 预应力 (t)」
#   全部是**小计行**——它们汇总上方若干有编码的明细行，无编码是设计使然。
#   根因：判据只问"有没有编码"，没问"这一行在表里扮演什么角色"。
#         与之同源的教训：G-DW 第一版只问"出现了哪个量纲词"，没问"是不是工程量单位"。
#   修正：① 模型层新增 is_subtotal 属性（无编码 + 名称含小计/合计/共计/subtotal/total），
#             L03 按属性判定而非在判据里逐行猜名字；② 小计行仍要求带名称或带量，
#             两者皆无按空行报 FAIL；③ selftest 增两条小计/合计用例 + 一条**反向断言**
#             （小计行号绝不许出现在 L03 的 rows 里）——只查 missed 为空证明不了没误报。
#   结果：误报归零，真孤儿（零散项无编码）仍被 L03 捕获，捕获率维持 100%。
#
# [R-BOQ-2] 同源第二处：L09 要求所有行填「图纸依据 / 口径」。
#   实测误报 9 处（同一张表的小计行）——小计行没有单一图纸出处，
#   而行名（"小计 下部混凝土"）本身已含分部口径。
#   修正：小计行必填项收缩为只有**计算式**（汇总了哪些行必须可审，这条不放）；
#        图纸依据与口径对小计行豁免。
#   结果：该表 WARN 39 → 9；9 条中余下的是小计行缺计算式，属真该补的。
#         修正列映射后该表 FAIL=0 / WARN=0。
#
# [R-BOQ-3] 同源第三处，也是最像"真 bug"的一处：L08 名称算式 vs 数量列。
#   实测误报 1 处：CBS-OVA-T01 名称"预应力梁板（10 片/跨 × 13 跨 = **130 片**，断面 0.75 m²）"，
#     体积单位列，数量 3403.5 → L08 拿 10×13=130 去比 3403.5，报"名称算式与数量不符"。
#   名称自己都写明了结果是"片"，乘积的量纲是**计数**，本行单位是**体积**。
#   根因仍是 [R-DW-1] 那条：不校验量纲就比对数字。同一个根因在移植脚本里暴露了三处
#        （G-DW 的含量单位 / boq_lint 的无编码汇总行 / boq_lint 的计数 vs 度量），
#        说明它不是笔误而是**这一类判据的共同盲区**。
#   修正：新增 _dims_incompatible()——名称自带 "= <数> <计数单位>" 且本行单位
#        不是计数单位 → 判不可比，跳过。selftest 增加量纲不相容用例 + 反向断言。
#   结果：该表 clean；灯杆"3 根 × 15 m = 45 vs 12"这类真不自洽仍被捕获。
#
# 留痕原因：移植过来的判据带着原项目的表结构假设。别项目的表只要有"无编码汇总行"，
#           这条判据就会集体误报——而误报比漏报更伤：它会训练人忽略整条门禁。
# ==============================================================================


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="boq_lint.py",
        description="Structural lint for a BOQ workbook (xlsx/csv).")
    sub = ap.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="lint a BOQ workbook")
    c.add_argument("--boq", required=True)
    c.add_argument("--sheet", default=None, help="sheet name (default: first sheet)")
    c.add_argument("--code-col", type=int, default=0,
                   help="编码列（缺省 0 = 由表头自动识别）")
    c.add_argument("--name-col", type=int, default=0)
    c.add_argument("--unit-col", type=int, default=0)
    c.add_argument("--value-col", type=int, default=0)
    c.add_argument("--note-col", type=int, default=0)
    c.add_argument("--basis-col", type=int, default=0,
                   help="计算式列（0 = 自动识别；注意此列语义是计算式，不是口径声明）")
    c.add_argument("--draw-col", type=int, default=0, help="图纸依据 column")
    c.add_argument("--conf-col", type=int, default=0,
                   help="口径/可信度 column（0 = 自动识别）")
    c.add_argument("--header-rows", type=int, default=1, help="rows to skip at the top")
    c.add_argument("--header-row", type=int, default=0,
                   help="header row number (0 = auto-detect); overrides --header-rows")
    c.add_argument("--no-auto-map", action="store_true",
                   help="disable column auto-detection (not recommended)")
    c.add_argument("--rollup", action="append", default=[],
                   help="in-sheet closure: parent code whose children must sum to it (repeatable)")
    c.add_argument("--closure", action="append", default=[],
                   help="cross-sheet closure: 'SHEET:CODE==SHEET:CODE' (repeatable)")
    c.add_argument("--tol", type=float, default=1.0, help="absolute tolerance for closure")
    c.add_argument("--out")
    c.add_argument("--md")
    c.add_argument("--strict", action="store_true", help="WARN also exits 1")
    c.set_defaults(func=cmd_check)

    t = sub.add_parser("selftest", help="meta-gate: verify the lint itself (must be 100 pct)")
    t.add_argument("--out", default="",
                   help="selftest fixture 放哪；默认用独立临时目录，"
                        "不写当前目录（避免产物混进技能包）")
    t.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    try:
        return args.func(args)
    except SystemExit:
        raise
    except Exception as e:  # a crash is a SKIP with a reason, never a silent pass
        sys.stderr.write("ERROR: %s: %s\n" % (type(e).__name__, e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
