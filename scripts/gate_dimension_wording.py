#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gate_dimension_wording.py -- 量纲—单位—口径词 三处自洽门禁 (G-DW)。

=============================================================================
为什么单独做一道门禁
=============================================================================
真实事故（本项目，同一张表里躺了两轮没人发现）：

    CBS-OVA-A02 防冲刷保护
      单位列      = SM            （面积）
      口径声明    = 回图裁决 3252 m³（体积）

两边**各自都有出处**，却互相矛盾。它逃过了当时所有门禁，因为：
  - gate_unit_consistency      只查"单位符号在不在白名单" → SM 合法，PASS
  - gate_scope_declaration     只查"口径词在不在"         → 写了"全桥"，PASS
  - gate_drawing_basis         比"量对不对基准"           → 基准自己也写 3252，PASS

**没有一道门禁跨列比对。** 所以补这一道：跨列看「单位列给的量纲」与
「文本里复述这个量时用的量纲」是不是同一回事。

=============================================================================
口径是两个维度，必须分开登记、分开校验（本次修订的核心）
=============================================================================
  范围口径 scope    —— 这个数覆盖多大范围（全桥 / 单幅 / 两侧 / 每座…）
  证据强度 evidence —— 这个数哪来的（图纸确认 / 推算 / 估算 / 待核）

只写"全桥"不写"推算"，等于只说了量多大、没说准不准。
两个维度在同一个格子里的老写法（"图读｜全桥"）要拆成两列。

=============================================================================
检查项
=============================================================================
  DW-1 FAIL  量纲矛盾：单位列量纲 vs 文本里"数值等于工程量"那个数字的后缀单位
  DW-2 FAIL  单位缺失：数值非 0/空，却无单位
  DW-3 FAIL  范围口径缺失：口径标识列不含任何范围口径词
  DW-4 FAIL  证据强度缺失：口径声明/依据类型列不含证据强度词
  DW-5 FAIL  估算/推定行无证据去处（RFI / 台账 / 业主清单编号 / 规范条款号）
  DW-6 WARN  口径禁用词（已计量 / 已确认 / 复核无误…）
  DW-7 WARN  单位符号带括注未归一（M3（净量）等；CM 仅在项目审批映射后解析）

退出码：0 通过 / 1 存在 FAIL / 2 门禁自身无法运行
"""

from __future__ import print_function

import argparse
import io
import json
import os
import re
import math
import sys
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
    import openpyxl
except ImportError:
    openpyxl = None

try:
    import colmap
except ImportError:
    colmap = None

# --------------------------------------------------------------------------
# 口径词表：两个维度分开
# --------------------------------------------------------------------------
# 维度一：范围口径（覆盖多大范围）
SPAN_WORDS = ("全桥", "单幅", "双幅", "两侧", "单侧", "每座", "总计",
              "单件", "合计", "每跨", "每延米", "全段", "单座", "每根")

# 维度二：证据强度（这个数哪来的）
SCOPE_WORDS = ("图纸确认", "推算", "估算", "待核")
# 兼容本项目既有写法
EVIDENCE_WORDS = ("图纸确认", "图读", "推算", "推定", "估算", "待核", "敞口")
ESTIMATE_WORDS = ("估算", "推定")

# 伪闭环词：在交付物里读起来像已完成，实则掩盖未解决问题
BANNED_WORDS = ("已计量", "已确认", "复核无误", "无误", "已核实", "已复核", "无问题")

# --------------------------------------------------------------------------
# 单位量纲表：**唯一事实来源是 qto_vocab**（v2.1.0 起）
# 此前本文件有一份逐字复制的副本，两份已经开始漂移——同一类判据两张表，
# 改一处不改另一处不会报错，正是本技能自己反复警告的"基线不随表更新"。
try:
    from qto_vocab import (UNIT_DIM, DIM_LABEL,
                           norm_unit as _norm_unit,
                           dim_of_token as _dim_of_token)
except ImportError:                              # 单文件旁置时退化为最小表
    UNIT_DIM = {"M3": "volume", "SM": "area", "M2": "area",
                "LM": "length", "MT": "mass", "EA": "count"}
    DIM_LABEL = {"volume": "体积", "area": "面积", "length": "长度",
                 "mass": "重量", "count": "数量", "item": "包干项"}

    def _norm_unit(raw):
        return re.sub(r"[（(\[][^）)\]]*[）)\]]", "", str(raw or "")).strip().upper()

    def _dim_of_token(tok):
        return {"m³": "volume", "m3": "volume", "m²": "area", "m2": "area",
                "延米": "length", "kg": "mass", "t": "mass",
                "座": "count", "根": "count", "片": "count",
                "道": "count", "套": "count", "个": "count"}.get(
                    str(tok or "").strip().lower())


UNIT_TOKENS = ("m³", "m3", "立方米", "m²", "m2", "平方米",
               "延米", "kg", "t", "座", "根", "片", "道", "套", "个")
NUM_UNIT_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(" + "|".join(re.escape(u) for u in UNIT_TOKENS) + r")",
    re.IGNORECASE)
# 含量单位（kg/m³、t/m³…）—— 描述的是"每方的含量"，不是这个工程量本身的量纲
CONTENT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:kg|t)\s*/\s*(?:m³|m3|立方米)", re.IGNORECASE)
PAREN_RE = re.compile(r"[（(\[][^）)\]]*[）)\]]")
# 工程量计算式的特征：基数 × 含量（乘号链）
MUL_CHAIN_RE = re.compile(r"\d\s*[×*]\s*\d")

# 证据去处：出现任一类即视为"有可核查来源"
SOURCE_PATTERNS = (
    ("图号", re.compile(r"图\s*[:：]?\s*\d|\d{4,}\s*[-–]\s*[A-Z0-9]{2,}|[A-Z]{2,4}\d{2,}\s*[-–]")),
    ("图纸编号", re.compile(r"23041|23040|\bS-\d|\bA-\d|\bB-\d|V0")),
    ("规范/标准", re.compile(r"BAEL|BPEL|RPOA|EUROCODE|Fascicule|AASHTO|BS\s?EN|EN\s?\d|ACI|ASTM|UIC")),
    ("业主清单编号", re.compile(r"业主清单\s*[\d.]+|\b\d+\.\d{2}\s*=")),
    ("RFI 编号", re.compile(r"RFI\s*[-#]?\s*\w+")),
    ("台账编号", re.compile(r"台账\s*#?\d+")),
)


# --------------------------------------------------------------------------
# _norm_unit / _dim_of_token 一律来自 qto_vocab（见上）。此处不再保留副本。


def _parse_num(s):
    try:
        value = float(str(s).strip())
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return "%d" % int(round(f)) if abs(f - round(f)) < 1e-9 else "%.4f" % f


# --------------------------------------------------------------------------
def check_row(r, code, name, unit_raw, value, basis, scope, formula,
              rfi_text, evidence=""):
    """返回该行的 findings [(level, code, msg), …]。"""
    out = []
    where = "第%d行(%s)" % (r, code)
    unit = _norm_unit(unit_raw)
    raw = str(unit_raw or "").strip()

    # --- DW-7 单位写法带括注 ---
    if raw and unit != raw.replace(" ", "").upper():
        out.append(("WARN", "DW-7",
                    "%s 单位列写法 %r 含括注/空白 — 已归一为 %r；"
                    "归一必须做在判据侧，不能只在告警侧提醒"
                    % (where, raw, unit)))

    # --- DW-2 有值无单位 ---
    v = _parse_num(value)
    if v not in (None, 0.0) and not raw:
        out.append(("FAIL", "DW-2", "%s 工程量=%s 却无单位列" % (where, value)))

    # --- DW-0 单位不在词表：这是"未校验"，不是"通过" ---
    # 词表外单位（法语 m³ / U / MC、手打错字）此前直接跳过 DW-1，
    # 输出上与"查过没问题"无法区分。现在必须留下计数痕迹。
    if unit and v is not None and unit != "EA" and UNIT_DIM.get(unit) is None:
        out.append(("WARN", "DW-0",
                    "%s 单位 %r 不在量纲词表，本行**未做**量纲校验"
                    "（词表外 = 未校验，不是通过）" % (where, unit)))

    # --- DW-1 量纲矛盾 ---
    # 判据：只在"文本中的数字 == 工程量列数值"时才比对其后缀单位。
    # 这样既抓得住 A02 那种"单位列 SM 而口径写 m³"的真矛盾，
    # 又不会被 "1.05m 高的道砟挡墙" 这类描述性尺寸误伤。
    dim_unit = UNIT_DIM.get(unit)
    if dim_unit and dim_unit not in ("EA", "item") and v is not None:
        blob = "%s %s %s" % (name or "", basis or "", formula or "")
        blob = CONTENT_RE.sub(" ", blob)          # 先剥含量单位 kg/m³
        for m in NUM_UNIT_RE.finditer(blob):
            try:
                num = float(m.group(1))
            except ValueError:
                continue
            if abs(num - v) > max(1e-6, abs(v) * 1e-6):
                continue
            d = _dim_of_token(m.group(2))
            if d and d != dim_unit:
                out.append(("FAIL", "DW-1",
                            "%s 单位列=%s（%s），但文本里 %s 后面写的是 %s（%s）— "
                            "单位与口径矛盾，须先定案再出量（A02 同类）"
                            % (where, unit, DIM_LABEL[dim_unit],
                               _fmt_num(num), m.group(2), DIM_LABEL[d])))
                break

    # --- DW-3 范围口径词 ---
    scope_txt = str(scope or "")
    if not any(w in scope_txt for w in SPAN_WORDS):
        out.append(("FAIL", "DW-3",
                    "%s 口径标识缺范围口径词（全桥/单幅/两侧/每座…），写了 %r"
                    % (where, scope_txt[:40])))

    # --- DW-4 证据强度词（口径声明列 或 依据类型列） ---
    basis_txt = str(basis or "")
    ev_txt = basis_txt + " " + str(evidence or "")
    ev = next((w for w in EVIDENCE_WORDS if w in ev_txt), None)
    if not ev:
        out.append(("FAIL", "DW-4",
                    "%s 口径声明/依据类型缺证据强度词"
                    "（图纸确认 / 推算 / 估算 / 待核）" % where))

    # --- DW-5 估算/推定必须有可核查去处 ---
    if ev in ESTIMATE_WORDS:
        alltxt = "%s %s %s %s" % (name or "", basis or "", scope or "",
                                  rfi_text or "")
        hits = [lbl for lbl, rx in SOURCE_PATTERNS if rx.search(alltxt)]
        if not hits:
            out.append(("FAIL", "DW-5",
                        "%s 证据强度为「%s」却找不到可核查去处"
                        "（RFI 编号 / 台账编号 / 业主清单编号 / 规范条款号 / 图号）"
                        % (where, ev)))

    # --- DW-6 口径禁用词 ---
    for w in BANNED_WORDS:
        if w in scope_txt or w in basis_txt:
            out.append(("WARN", "DW-6",
                        "%s 用了伪闭环词「%s」— 改用 图纸确认 / 推算 / 估算 / 待核"
                        % (where, w)))
    return out


# --------------------------------------------------------------------------
def run(book, sheet, code_col, name_col, unit_col, value_col, basis_col,
        scope_col, formula_col, rfi_col, evidence_col=0, code_prefix="CBS-"):
    if openpyxl is None:
        raise RuntimeError("缺少 openpyxl，门禁无法运行")
    wb = openpyxl.load_workbook(book, data_only=True)
    if sheet and sheet in wb.sheetnames:
        ws = wb[sheet]
        used = sheet
    else:
        ws = wb.active
        used = ws.title
        if sheet:
            print("[WARN] 未找到页签 %r，已退回活动页签 %r" % (sheet, used))

    def g(r, c):
        if not c or c <= 0:
            return ""
        v = ws.cell(r, c).value
        return "" if v is None else str(v)

    findings = []
    n = 0
    prefix = code_prefix or "CBS-"
    for r in range(1, ws.max_row + 1):
        code = g(r, code_col).strip()
        if not code.startswith(prefix):
            continue
        n += 1
        findings += check_row(r, code, g(r, name_col), g(r, unit_col),
                              g(r, value_col), g(r, basis_col),
                          g(r, scope_col), g(r, formula_col),
                          g(r, rfi_col), g(r, evidence_col))
    return findings, n


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="gate_dimension_wording",
        description="量纲—单位—口径词 自洽门禁 (G-DW)")
    ap.add_argument("book", help="工作簿路径 (.xlsx)")
    ap.add_argument("--sheet", default="", help="页签名（缺省取活动页签）")
    ap.add_argument("--code-col", type=int, default=0,
                    help="编码列（缺省 0 = 由表头自动识别）")
    ap.add_argument("--name-col", type=int, default=0)
    ap.add_argument("--unit-col", type=int, default=0)
    ap.add_argument("--value-col", type=int, default=0)
    ap.add_argument("--basis-col", type=int, default=0,
                    help="口径声明列（依据说明/计算式）")
    ap.add_argument("--scope-col", type=int, default=0,
                    help="口径标识列（图号｜范围口径词）")
    ap.add_argument("--evidence-col", type=int, default=0,
                    help="依据类型列（证据强度词）")
    ap.add_argument("--formula-col", type=int, default=0)
    ap.add_argument("--rfi-col", type=int, default=0)
    ap.add_argument("--header-row", type=int, default=0,
                    help="表头行号（缺省 0 = 自动定位）")
    ap.add_argument("--no-auto-map", action="store_true",
                    help="关闭列映射报告（不推荐：传错列时的典型表现是零发现）")
    ap.add_argument("--code-prefix", default="CBS-",
                    help="编码前缀，只有该前缀的行参与检查（缺省 CBS-）")
    ap.add_argument("--json", default="")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--unit-registry", default="",
                    help="已批准的项目单位别名注册表 JSON；用于显式解析 CM 等歧义代码")
    ap.add_argument("--project-id", default="",
                    help="项目编号；传入后会校验单位别名适用范围")
    a = ap.parse_args(argv)

    if a.unit_registry:
        try:
            from qto_core import load_unit_alias_registry
            aliases = load_unit_alias_registry(a.unit_registry, project_id=a.project_id or None)
            if aliases and not a.project_id:
                raise ValueError("项目别名非空时必须同时传 --project-id")
            for alias_key, entry in aliases.items():
                UNIT_DIM[alias_key.upper()] = entry["_dimension"]
        except Exception as exc:
            print("[ERR] 项目单位别名注册表无效，拒绝运行：%s" % exc)
            return 2

    if not os.path.exists(a.book):
        print("[ERR] 工作簿不存在: %s" % a.book)
        return 2

    # ---- 列映射：表头定角色 + 数据验角色 ----
    # 传入列缺省为 0（未指定），由 colmap 从表头识别；显式传入时与识别结果
    # 交叉核对，冲突即退出——静默采用任一侧都会让「门禁通过」失去意义。
    explicit = {}
    for role, arg in (("code", a.code_col), ("name", a.name_col),
                      ("unit", a.unit_col), ("value", a.value_col),
                      ("basis", a.basis_col), ("scope", a.scope_col),
                      ("evidence", a.evidence_col),
                      ("formula", a.formula_col)):
        if arg and arg > 0:
            explicit[role] = arg
    roles = ["code", "name", "unit", "value", "basis", "draw", "scope",
             "evidence", "conf", "note", "pos", "formula"]

    cols = dict(code=a.code_col, name=a.name_col, unit=a.unit_col,
                value=a.value_col, basis=a.basis_col, scope=a.scope_col,
                evidence=a.evidence_col, formula=a.formula_col)
    if colmap is not None and not a.no_auto_map:
        try:
            wb0 = openpyxl.load_workbook(a.book, data_only=True)
            ws0 = wb0[a.sheet] if (a.sheet and a.sheet in wb0.sheetnames) else wb0.active
            detected, col_headers, hrow = colmap.detect_columns(
                ws0, roles, header_row=a.header_row or None)
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
                print("零发现与干净在输出上无法区分——列传错时必须先拦住。")
                return 2
            # 采用识别结果（显式传入且一致的不变）
            for role in roles:
                v = colmap.resolve(explicit, detected, role)
                if v and not cols.get(role):
                    cols[role] = v
            print("-" * 78)
        except Exception as e:
            print("[WARN] 列映射识别失败，退回显式参数：%s" % e)

    try:
        findings, n = run(a.book, a.sheet, cols["code"], cols["name"],
                          cols["unit"], cols["value"], cols["basis"],
                          cols["scope"], cols["formula"], a.rfi_col,
                          cols["evidence"], a.code_prefix)
    except Exception as e:
        print("[ERR] 门禁执行失败: %s" % e)
        return 2

    nf = sum(1 for s, _, _ in findings if s == "FAIL")
    nw = sum(1 for s, _, _ in findings if s == "WARN")
    print("=" * 78)
    print("G-DW 量纲—单位—口径词自洽门禁")
    print("=" * 78)
    for s, code, msg in findings:
        print("[%s] %-6s %s" % (s, code, msg))
    # 零行时不许打印「无问题」——那正是假 PASS 的样子。先报零行，再谈结论。
    if not findings and n > 0:
        print("[OK] 无问题")
    print("-" * 78)
    print("扫描 %s 行 = %d   FAIL = %d   WARN = %d" % (a.code_prefix, n, nf, nw))
    # ---- 量纲覆盖计数：未校验行数必须显式报出 ----
    nun = sum(1 for s, c, _ in findings if c == "DW-0")
    print("量纲校验覆盖：已校验 %d 行 / 未校验 %d 行"
          % (max(n - nun, 0), nun))
    if nun:
        print("[WARN] 有 %d 行单位不在词表，这些行**没有**做量纲比对——"
              "报告中不得写「量纲已核」。" % nun)
    # ---- 零行守卫（v2.1.1：WARN 升级为「未运行」）----
    # 「零发现」与「干净」在输出上无法区分。若一行也没扫到，上面的 FAIL=0
    # 会看起来像通过——这正是列映射传错时的典型表现（本项目实测：
    # 把编码列取成分部序号列 2.3.1，startswith("CBS-") 匹配 0 行）。
    # v2.1.0 只给 WARN 且默认仍 exit 0，等于把假绿降级成"小声的假绿"；
    # 现统一按「未运行 = 2」处理，与其余门禁同语义。
    if n == 0:
        print("[未执行] 扫描到 **0 行数据行**（编码前缀 %r）。" % a.code_prefix)
        print("         一行都没扫到，「FAIL=0」不说明表是干净的。")
        print("         先核对上面的列映射报告——code 列很可能定位错了"
              "（把分部序号 2.3.1 当成编码是常见成因）。")
        print("[未执行] 退出码 2（门禁无法运行）——这不是\"检查不通过\"，也不是\"通过\"。")
        return 2
    if a.json:
        d = os.path.dirname(os.path.abspath(a.json))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(a.json, "w", encoding="utf-8") as fp:
            fp.write(json.dumps({
                "tool": "gate_dimension_wording", "version": "2.0.0",
                "book": a.book, "sheet": a.sheet or "",
                "rows": n, "fail": nf, "warn": nw,
                "findings": [{"level": s, "code": c, "msg": m}
                             for s, c, m in findings],
            }, ensure_ascii=False, indent=2))
        print("[OK] 已写出: %s" % a.json)
    if nf:
        return 1
    if a.strict and nw:
        return 1
    return 0


# --------------------------------------------------------------------------
# selftest —— 元门禁：构造含已知缺陷的样例表，断言每一条都被抓到。
# 捕获率必须 100%；门禁崩了是 SKIP（带原因），绝不是 PASS。
# --------------------------------------------------------------------------
SELFTEST_FIXTURE = [
    # (code, name, unit, value, basis, scope, 期望被抓的码)
    ("CBS-OVA-A02", "防冲刷保护（回图裁决 3252 m³）", "SM", "3252",
     "回图裁决 3252 m³", "图号｜全桥", "DW-1"),          # 单位 SM vs 文本 m³
    ("CBS-OVA-X01", "孤立项", "M3", "100", "推", "图号｜", "DW-3"),   # 无范围口径词
    ("CBS-OVA-X02", "无单位项", "", "88", "图读", "图号｜全桥", "DW-2"),
    ("CBS-OVA-X03", "估算项无去处", "M3", "50", "估算", "图号｜全桥", "DW-5"),
]

# --------------------------------------------------------------------------
# 合法行：**期望零发现**。这些正是 [R-DW-1] 的误报来源——判据第一版把它们
# 当成缺陷报了 5 处。只断言「漏报为零」证明不了「没误报」，必须单独盯住
# 这些行的行号：它们绝不许出现在任何 finding 里。
# --------------------------------------------------------------------------
LEGIT_FIXTURE = [
    # (code, name, unit, value, basis, scope)
    # R-DW-1 误报源 ①：钢筋行单位 MT，文本写含量 150 kg/m³。
    #   kg/m³ 是含量单位不是工程量单位，剥掉后才许比对量纲。
    ("CBS-OVA-T10", "横隔梁钢筋 HRB400（含钢率约 150 kg/m³）", "MT", "37.8",
     "图纸确认", "图号｜全桥"),
    # R-DW-1 误报源 ②：体积行文本里出现长度词 1.05m。
    #   判据只指证「数值 == 工程量列值」的那个数字，1.05 ≠ 235.50 故不指证。
    ("CBS-OVA-A05", "桥面铺装（摊铺厚度 1.05 m）", "M3", "235.50",
     "图纸确认", "图号｜全桥"),
    # 正当复述：文本数值与工程量列相同且量纲一致——最正常的情形，绝不许报。
    ("CBS-OVA-F02", "钻孔灌注桩身混凝土", "M3", "3800.07",
     "图纸确认", "图号｜全桥"),
]


def _selftest(a):
    # 默认落到系统临时目录：旧默认 "out/" 是相对 CWD 的，从技能包内跑一次
    # selftest 就会把 _dw_selftest.xlsx 写进 scripts/out/，随后被打包进分发
    # 物（实测 v2.1.0.zip 里真带了这个 5500 B 的测试残留）。fixture 文件是
    # 运行时产物，不属于源码，绝不能混进安装包。
    out = a.out or tempfile.gettempdir()
    # 目录还是文件名的判定不能只看「目录已存在」：--out ./tmpdir 这种**尚未
    # 创建**的目录会被当成文件名，写出一个没有扩展名的 tmpdir，openpyxl 直接
    # 拒绝（"does not support  file format"）。带斜杠 / 已存在 / **无扩展名**
    # 三种情形都按目录处理；只有显式给了 .xlsx/.xlsm 才当文件名。
    _ext = os.path.splitext(out)[1].lower()
    if (os.path.isdir(out) or out.endswith(("/", "\\"))
            or _ext not in (".xlsx", ".xlsm")):
        path = os.path.join(out, "_dw_selftest.xlsx")
    else:
        path = out
    if openpyxl is None:
        print("[ERR] 缺少 openpyxl，selftest 无法运行")
        return 2
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "01_清单"
    head = ["编码", "CBS子项", "名称", "单位", "工程量", "", "",
            "口径声明", "图号出处/口径标识", "", "", "依据类型"]
    for i, h in enumerate(head, start=1):
        ws.cell(1, i, h)
    r = 2
    legit_rows = []
    for code, name, unit, value, basis, scope, _exp in SELFTEST_FIXTURE:
        ws.cell(r, 2, code)
        ws.cell(r, 3, name)
        ws.cell(r, 4, unit)
        ws.cell(r, 5, value)
        ws.cell(r, 8, basis)
        ws.cell(r, 9, scope)
        ws.cell(r, 12, basis)
        r += 1
    for code, name, unit, value, basis, scope in LEGIT_FIXTURE:
        ws.cell(r, 2, code)
        ws.cell(r, 3, name)
        ws.cell(r, 4, unit)
        ws.cell(r, 5, value)
        ws.cell(r, 8, basis)
        ws.cell(r, 9, scope)
        ws.cell(r, 12, basis)
        legit_rows.append(r)
        r += 1
    wb.save(path)

    try:
        findings, n = run(path, "01_清单", 2, 3, 4, 5, 8, 9, 0, 0, 12)
    except Exception as e:
        print("[SKIP] selftest 执行失败（不算通过）: %s" % e)
        return 2

    hit = {}
    for s, code, msg in findings:
        hit.setdefault(code, []).append(msg)
    caught, missed = [], []
    for row in SELFTEST_FIXTURE:
        exp = row[6]
        if exp in hit:
            caught.append((exp, hit[exp][0]))
        else:
            missed.append(exp)

    # ---- 反向断言：合法行绝不许被指证 ----
    # 漏报为零只说明判据够狠，说明不了它不乱咬。误报比漏报更伤：
    # 它会训练人忽略整条门禁（[R-DW-1] 实测误报 5 处后被人跳过）。
    false_pos = []
    for s, dcode, msg in findings:
        for lr in legit_rows:
            if ("第%d行" % lr) in msg:
                false_pos.append((lr, dcode, msg))

    print("=" * 78)
    print("G-DW selftest 元门禁（正向捕获 + 反向误报）")
    print("=" * 78)
    for exp, msg in caught:
        print("[CAUGHT] %-6s %s" % (exp, msg[:88]))
    for exp in missed:
        print("[MISSED] %-6s 注入了缺陷却没被抓到" % exp)
    print("-" * 78)
    print("捕获率 = %d/%d = %s" % (
        len(caught), len(SELFTEST_FIXTURE),
        "100%" if not missed else "%.0f%% — 判据太弱，不许上线" % (
            100.0 * len(caught) / len(SELFTEST_FIXTURE))))
    print("合法行（应零发现）= %d 行 %s" % (len(legit_rows), legit_rows))
    if false_pos:
        for lr, dcode, msg in false_pos:
            print("[FALSE-POS] 第%d行 被 %s 误报: %s" % (lr, dcode, msg[:70]))
    else:
        print("[OK] 合法行 0 误报（含量单位 kg/m³、文本长度词 1.05m、"
              "正当复述均未被指证）")
    if missed:
        return 1
    if false_pos:
        print("结论: FAIL（存在误报，判据乱咬）")
        return 1
    print("结论: PASS")
    return 0


def main2(argv=None):
    """支持子命令：check（默认，位置参数 book）与 selftest。"""
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "selftest":
        ap = argparse.ArgumentParser(prog="gate_dimension_wording selftest")
        ap.add_argument("--out", default="",
                        help="selftest fixture 写到哪（目录或文件名）。"
                             "默认写系统临时目录，不写技能包内。")
        return _selftest(ap.parse_args(argv[1:]))
    return main(argv)


if __name__ == "__main__":
    sys.exit(main2())


# ==============================================================================
# REVISION LOG —— 判据被实测推翻的记录（写完觉得"够严"不算数）
# ==============================================================================
#
# [R-DW-1] 初版 DW-1：整行文本扫描，只要出现"别的量纲的词"就 FAIL。
#   实测误报 5 处（VIADUC_PK295+300 表），典型：
#     CBS-OVA-T08 单位 MT，文本 "≈150 kg/m³" → 被判"出现体积量纲"
#     CBS-OVA-I05 单位 CM，文本 "…1.05m…"   → 被判"出现长度量纲"
#   根因：文本里的单位分两类——**工程量单位**与**含量/描述单位**，初版没区分。
#   修正：① 先剥含量单位（kg/m³）；② 只指证"数值 == 工程量列数值"的那个数字。
#   结果：DW-1 误报归零，A02 型真矛盾仍可捕获（已注入回测，见 §故障注入）。
#
# [R-DW-2] 初版把"证据强度"与"范围口径"混在同一列检查。
#   实测：本表口径标识列只写"全桥"（范围），证据强度写在"依据类型"列 → 全行报红。
#   修正：两个维度拆成独立参数 --scope-col / --evidence-col，分别出 DW-3 / DW-4。
#
# 留痕原因：这道门禁是为"跨列矛盾"而生的，它自己第一版就犯了
#           "不区分语境就比对字面量"的错——正是它要抓的错误类型。
# ==============================================================================
