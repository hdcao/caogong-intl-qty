# -*- coding: utf-8 -*-
"""colmap.py -- 列映射（表头 -> 列号）的单一事实来源。

为什么要有这个模块
------------------
G-DW 与 boq_lint 都要求调用方手写 `--code-col 2 --name-col 3 ...`。
实测代价（VIADUC PK295+300，2026-10-23）：

  * 表头在第 **1** 行，而操作者按第 6 行取表头，读到的是数据行内容；
  * G-DW 的 `--basis-col` 语义是「口径声明」，boq_lint 的 `--basis-col`
    语义却是「计算式」——**同名列不同义**，照抄参数名必然传错；
  * 列映射传错的典型表现是**零发现**，而零发现和「干净」在输出上长得一样。

所以本模块做三件事，且三件事都默认开启、无需 opt-in：

  1. `detect_header_row()`  自动定位表头行（不靠人肉数行号）；
  2. `detect_columns()`     按语义角色推断列号（角色名 ≠ 参数名）；
  3. `audit_columns()`      渲染「角色 -> 列号 -> 实际表头文本」对照表，
                            并把人传值与自动推断的冲突、空表头列指出来。

设计原则：**自动推断只用于「提示」与「交叉验证」，不静默替代人工传值。**
推断与传值不一致时报错退出——因为一个错的自动映射比没有映射更危险，
它会让人以为已经核对过了。
"""

import os
import sys

# --- 控制台编码兜底（v2.1.0）：cp936 控制台下不该因一个符号崩 -----------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
import re

# --------------------------------------------------------------------------
# 语义角色同义词表
#
# 注意：键是**语义角色**，不是任何脚本的命令行参数名。G-DW 与 boq_lint 对
# "basis" 的定义不同，若按参数名建表就会把两个不同的列混为一谈。
#
# 每条 = (精确同义词列表, 包含同义词列表)。精确匹配优先。
# --------------------------------------------------------------------------
ROLE_SYNONYMS = {
    "code": (
        ["cbs子项", "cbs子目", "编码", "项目编码", "子项编码", "cbs code", "code",
         "item no", "item no.", "ref", "poste", "n°"],
        ["cbs", "编码", "code", "item no", "poste"],
    ),
    "name": (
        ["名称", "项目名称", "细目名称", "description", "designation",
         "désignation", "libellé", "libelle", "item"],
        ["名称", "description", "désignation", "libellé", "libelle"],
    ),
    "unit": (
        ["单位", "计量单位", "unit", "unité", "unite", "u"],
        ["单位", "unit", "unité", "unite"],
    ),
    "value": (
        ["工程量", "数量", "工程数量", "quantity", "quantité", "quantite",
         "qté", "qte", "qty"],
        ["工程量", "数量", "quantity", "quantité", "quantite", "qté", "qty"],
    ),
    "formula": (
        ["计算式", "算式", "计算公式", "formule", "calcul", "computation"],
        ["计算式", "算式", "formule", "calcul"],
    ),
    "basis": (
        ["口径声明", "依据说明", "依据", "base", "prestation"],
        ["口径声明", "依据说明"],
    ),
    "draw": (
        ["图号出处", "图纸依据", "图号", "出处", "dessin", "plan", "drawing"],
        ["图号", "图纸", "dessin", "plan", "drawing"],
    ),
    "scope": (
        ["口径标识", "范围口径", "scope"],
        ["口径标识", "范围口径", "scope"],
    ),
    "evidence": (
        ["依据类型", "证据强度", "evidence", "type de prestation"],
        ["依据类型", "证据强度", "evidence"],
    ),
    "conf": (
        ["口径", "可信度", "证据档", "confiance", "confidence", "reliability"],
        ["可信度", "证据档", "confiance", "confidence"],
    ),
    "note": (
        ["说明", "备注", "注释", "notes", "note", "observations", "obs"],
        ["说明", "备注", "注释", "notes", "observations"],
    ),
    "pos": (
        ["图面位置", "位置", "position", "emplacement"],
        ["图面位置", "位置", "position"],
    ),
    "price": (
        ["单价", "材料单价", "unit price", "prix unitaire", "pu"],
        ["单价", "prix", "price"],
    ),
    "amount": (
        ["合价", "金额", "总计", "amount", "montant", "total"],
        ["合价", "金额", "montant"],
    ),
}

# 表头里出现这些词的列，通常是结构性列（序号/分部标题/签字人），
# 不参与语义角色竞争，避免把「一、基础」这类分部行误判成名称列。
STRUCTURAL_HINTS = (
    "分部", "章节", "section", "chapter", "章", "节",
    "签字", "假设人", "校核", "审核", "编制", "日期", "date", "by",
    "序号", "no.",
)

# --------------------------------------------------------------------------
# 数据探测式：表头定角色，**数据验角色**。
#
# 为什么需要：VIADUC PK295+300 表有 col1「编码」（装 2.3.1 分部序号）和
# col2「CBS子项」（装 CBS-OVA-F02 真编码），两列表头都是 code 的同义词。
# 只按表头识别会取到最左的 col1，而 G-DW 按 col1 扫 `startswith("CBS-")`
# 将匹配 0 行 —— **零发现，看起来干净，实则一项未检**。
# 所以在候选列之间，用数据内容模式再排一次序。
#
# 取值规则：某列前 probe_rows 个非空单元格中，匹配比例 ≥ 0.6 者胜出。
# 无数据可探时（如表头-only 的 selftest 表）自动退回表头匹配。
# --------------------------------------------------------------------------
DATA_PROBES = {
    # 真编码通常以 2 个以上字母开头再连字符（CBS-OVA-F02）；分部序号 2.3.1 不匹配
    "code": r"^[A-Za-z]{2,}[-–—]\S*",
    # 工程量：可带千分位与小数
    "value": r"^-?[\d][\d,\.]*$",
    # 单位：短符号（CM/SM/LM/MT/EA/m/m²/m³/U）
    "unit": r"^[A-Za-zµ°²³/]{1,6}$",
    # 依据类型/证据强度：中文短词
    "evidence": r"^[\u4e00-\u9fffA-Za-z（）()]{2,20}$",
}
PROBE_MIN_RATIO = 0.6
PROBE_ROWS = 8


def _norm(text):
    """表头文本归一化：去空白/零宽字符，小写，去首尾标点。"""
    if text is None:
        return ""
    s = str(text)
    s = s.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    s = re.sub(r"\s+", "", s)
    return s.strip(" \t:：|/／-_—–.").lower()


def _score(cell_text, role):
    """返回 (分值, 匹配到的同义词)。精确=100，包含=10，不匹配=0。"""
    t = _norm(cell_text)
    if not t:
        return 0, None
    exact, partial = ROLE_SYNONYMS[role]
    for w in exact:
        if t == _norm(w):
            return 100, w
    for w in partial:
        if _norm(w) and _norm(w) in t:
            return 10, w
    return 0, None


def detect_header_row(ws, roles, max_scan=15, max_col=40):
    """在前 max_scan 行里找出最像表头的那一行。

    返回 (row_index, score)。score = 该行命中的角色数。
    并列时取更靠上的行（表头通常在数据之上）。
    """
    best_row, best_score = 1, 0
    hi = min(ws.max_row, max_scan)
    wc = min(ws.max_column, max_col)
    for r in range(1, hi + 1):
        score = 0
        for role in roles:
            for c in range(1, wc + 1):
                s, _ = _score(ws.cell(r, c).value, role)
                if s:
                    score += 1
                    break
        if score > best_score:
            best_row, best_score = r, score
    return best_row, best_score


def _probe_ratio(ws, col, header_row, pattern, rows=PROBE_ROWS):
    """该列在表头以下数据行中，匹配 pattern 的比例。无数据返回 None。"""
    if not pattern:
        return None
    rx = re.compile(pattern)
    hit = tot = 0
    hi = min(ws.max_row, header_row + rows)
    for r in range(header_row + 1, hi + 1):
        v = ws.cell(r, col).value
        if v is None or str(v).strip() == "":
            continue
        tot += 1
        if rx.match(str(v).strip()):
            hit += 1
    if tot == 0:
        return None
    return float(hit) / tot


def detect_columns(ws, roles, header_row=None, probes=None):
    """按语义角色推断列号（表头定角色 + 数据验角色）。

    返回 (detected, col_headers, header_row)：
      detected   {role: {"col", "header", "matched", "ambiguous", "probe"}}
      col_headers {col: 表头原文}  供 audit_columns 核对人工传值
    一个角色匹配到多列时记入 ambiguous，col 取最左一列并在报告中明示。
    """
    if probes is None:
        probes = DATA_PROBES
    if header_row is None:
        header_row, _ = detect_header_row(ws, roles)
    wc = min(ws.max_column, 40)
    col_headers = {}
    for c in range(1, wc + 1):
        v = ws.cell(header_row, c).value
        if v is not None and str(v).strip():
            col_headers[c] = str(v).strip()

    out = {}
    for role in roles:
        hits = []
        for c in range(1, wc + 1):
            v = ws.cell(header_row, c).value
            s, w = _score(v, role)
            if s:
                hits.append({"col": c, "header": str(v), "matched": w,
                             "score": s})
        if not hits:
            out[role] = {"col": None, "header": "", "matched": None,
                         "ambiguous": [], "probe": None}
            continue
        # 数据验证：在候选列间按「数据匹配比例」重排，探测胜出者优先。
        pr = probes.get(role)
        if pr and len(hits) > 1:
            for h in hits:
                h["probe"] = _probe_ratio(ws, h["col"], header_row, pr)
            provable = [h for h in hits
                        if h["probe"] is not None and h["probe"] >= PROBE_MIN_RATIO]
            if provable:
                best = max(h["probe"] for h in provable)
                hits = [h for h in provable if h["probe"] == best]
        out[role] = {
            "col": hits[0]["col"],
            "header": hits[0]["header"],
            "matched": hits[0]["matched"],
            "ambiguous": [h["col"] for h in hits[1:]],
            "probe": hits[0].get("probe"),
        }
    return out, col_headers, header_row


def render_map(detected, explicit=None, title="列映射"):
    """生成「角色 -> 列号 -> 实际表头文本」对照表，返回文本行列表。

    explicit 为人工传入的 {role: col}（可为 None）。两处都给出时，
    文本上能直接看出传值与表头是否对得上。
    """
    lines = [title]
    lines.append("  %-10s %-6s %-34s %s" % ("角色", "列号", "表头实际文本", "命中同义词"))
    lines.append("  " + "-" * 74)
    for role in sorted(detected):
        d = detected[role]
        col = d["col"]
        head = (d["header"] or "")[:32]
        hit = d["matched"] or "-"
        flag = ""
        if explicit and role in explicit:
            ev = explicit[role]
            if col is None:
                flag = "  <- 传 %s，表头未识别" % ev
            elif ev != col:
                flag = "  <- 传 %s，与识别结果不一致!" % ev
            else:
                flag = "  <- 与传入一致"
        if d["ambiguous"]:
            flag += "  [歧义: 也匹配列 %s]" % d["ambiguous"]
        lines.append("  %-10s %-6s %-34s %s%s"
                     % (role, col if col else "-", head, hit, flag))
    return lines


def audit_columns(detected, explicit, required=(), col_headers=None,
                  allow_multi_role=True):
    """核对人工传值与自动识别结果。

    返回 (errors, warns)。errors 非空时调用方应退出（退出码 2）：
    静默采用任一侧都会让「门禁通过」失去意义。

    三种冲突要报 ERROR：
      a) 覆盖分支：该角色已在表头中识别到列，人工却传另一列 —— 一律报错；
      c) 必需角色既识别不到也未传入。

    一种情况按 allow_multi_role 决定拦不拦：
      b) 补充分支：该角色压根没识别到，人工指定某列承担它，而该列表头
         与角色同义词不符。这是**一列两用**的现实（如 VIADUC 把「口径声明」
         与「计算式」合写在 H 列）。默认放行（WARN），allow_multi_role=False
         时报错；放行不等于看不见 —— 列映射报告会标出该列承担几个角色。
    """
    col_headers = col_headers or {}
    errors, warns = [], []
    # 统计每列承担的角色数，供一列两用标记
    role_by_col = {}
    for role, d in detected.items():
        if d.get("col"):
            role_by_col.setdefault(d["col"], []).append(role)
    for role, ev in sorted((explicit or {}).items()):
        if not ev or ev <= 0:
            continue
        d = detected.get(role)
        if d is None:
            continue
        ev_head = col_headers.get(ev, "")
        if d["col"] is None:
            if ev_head:
                s, w = _score(ev_head, role)
                if not s:
                    if allow_multi_role:
                        others = [x for x in role_by_col.get(ev, []) if x != role]
                        warns.append(
                            "角色 %r 采用传入列 %s（表头 %r 与其同义词不符）——"
                            "按人工指定的一列两用处理；该列另承担 %s"
                            % (role, ev, ev_head, others or "无其他角色"))
                    else:
                        errors.append(
                            "列映射冲突：角色 %r 传入列 %s，但该列表头为 %r，"
                            "与角色语义不符（也未在表头中识别到 %r）。"
                            % (role, ev, ev_head, role))
                else:
                    warns.append(
                        "角色 %r 采用传入列 %s（表头 %r，弱匹配 %r）；"
                        "表头自动识别未覆盖该角色"
                        % (role, ev, ev_head, w))
            else:
                warns.append("角色 %r 传入列 %s，但该列无表头文本（可能是空列）"
                             % (role, ev))
            continue
        if ev != d["col"]:
            # 覆盖分支：该角色**已经**在表头中识别到列，人工却传了另一列。
            # 这是覆盖识别结果，不是补充——一律报 ERROR。
            # （放行只属于下面的「补充」分支：该角色压根没识别到。）
            errors.append(
                "列映射冲突：角色 %r 传入列 %s，但表头识别为列 %s（表头文本 %r）。"
                "请核对后重传，或省略该参数采用识别结果。"
                % (role, ev, d["col"], d["header"]))
        if d["ambiguous"]:
            warns.append("角色 %r 的列 %s 存在歧义，同样匹配列 %s；已按数据探测取最左一列"
                         % (role, ev or d["col"], d["ambiguous"]))
    for role in required:
        d = detected.get(role) or {}
        # 判定「最终能否拿到列号」而非「是否识别到」——调用方显式传入也算数。
        final = (explicit or {}).get(role) or d.get("col")
        if not final:
            errors.append("必需角色 %r 未能在表头中定位，也未从参数传入" % role)
    return errors, warns


def resolve(explicit, detected, role):
    """取最终列号：显式传入优先，其次识别结果，最后 0（表示该列不存在）。"""
    ev = (explicit or {}).get(role)
    if ev:
        return ev
    d = (detected or {}).get(role) or {}
    return d.get("col") or 0


def print_map(lines):
    for ln in lines:
        print(ln)


# --------------------------------------------------------------------------
# 自检：用一张已知表头断言识别结果
# --------------------------------------------------------------------------
def selftest():
    header = {
        1: "编码", 2: "CBS子项", 3: "名称", 4: "单位", 5: "工程量",
        6: "材料单价DZD", 7: "合价DZD", 8: "口径声明", 9: "图号出处/口径标识",
        10: "图面位置", 11: "证据档", 12: "依据类型", 13: "假设人",
    }
    # 数据行必须仿真：col1 装分部序号 2.3.1，col2 装真编码 CBS-OVA-F02。
    # 若仿真表只有表头行，数据探测无从生效，「表头定角色+数据验角色」
    # 这条核心机制就等于没测。
    data = {
        2: {1: "2.3.1", 2: "CBS-OVA-F02", 3: "钻孔灌注桩身混凝土", 4: "CM",
            5: "3800.07", 6: "", 7: "", 8: "168根×…", 9: "23041-…-1/3｜全桥",
            10: "桩位/桩细部", 11: "B", 12: "图读值"},
        3: {1: "2.3.1", 2: "CBS-OVA-F03", 3: "中墩承台", 4: "CM",
            5: "3345.408", 6: "", 7: "", 8: "12个中墩逐个计", 9: "23041-…-1/3｜全桥",
            10: "承台平面", 11: "B", 12: "图读值"},
        4: {1: "2.3.1", 2: "CBS-OVA-F04", 3: "桥台承台", 4: "CM",
            5: "582.912", 6: "", 7: "", 8: "C.1/C.2 各1个", 9: "23041-…-1/3｜全桥",
            10: "承台平面", 11: "B", 12: "图读值"},
        5: {1: "2.3.2", 2: "CBS-OVA-T01", 3: "预制梁板", 4: "CM",
            5: "3403.5", 6: "", 7: "", 8: "0.75×(20×34.40+…)", 9: "23041-…-2/3｜全桥",
            10: "DEMI COUPE", 11: "A", 12: "图读值"},
        6: {1: "2.3.2", 2: "CBS-OVA-T04", 3: "支座", 4: "EA",
            5: "260", 6: "", 7: "", 8: "10片×26梁端", 9: "23041-…-2/3｜全桥",
            10: "支座表", 11: "B", 12: "图读值"},
    }
    roles = ["code", "name", "unit", "value", "basis", "draw", "scope",
             "evidence", "conf", "note", "pos", "formula", "price", "amount"]
    expect = {"code": 2, "name": 3, "unit": 4, "value": 5, "basis": 8,
              "draw": 9, "scope": 9, "evidence": 12, "conf": 11,
              "note": None, "pos": 10, "formula": None, "price": 6,
              "amount": 7}

    class _WS(object):
        max_row = 8
        max_column = 13

        def cell(self, r, c):
            class _C(object):
                def __init__(self, v):
                    self.value = v
            if r == 1:
                return _C(header.get(c))
            return _C(data.get(r, {}).get(c))

    ws = _WS()
    detected, col_headers, hrow = detect_columns(ws, roles)
    bad = []
    for role, col in expect.items():
        got = detected[role]["col"]
        if got != col:
            bad.append("%s: 期望 %s 实得 %s" % (role, col, got))
    if hrow != 1:
        bad.append("表头行: 期望 1 实得 %s" % hrow)
    # 数据探测必须真的生效：code 若被 col1（分部序号）抢走，说明探测失效
    if detected["code"]["col"] == 1:
        bad.append("数据探测失效: code 被分部序号列 col1 抢走")
    # 冲突 a) 覆盖分支：code 已识别为列 2，却传 5（工程量列）→ 必须报错
    errs, _ = audit_columns(detected, {"code": 5}, col_headers=col_headers)
    if not any("code" in e for e in errs):
        bad.append("冲突 a) 失效: code 传 5（覆盖已识别列）未报错")
    # 覆盖分支另一例：code 传 1（分部序号列，表头同义歧义）也必须报错
    errs1b, _ = audit_columns(detected, {"code": 1}, col_headers=col_headers)
    if not any("code" in e for e in errs1b):
        bad.append("冲突 a) 失效: code 传 1（分部序号列）未报错")
    # 补充分支：note 未识别，传 6（材料单价列）——默认放行但必须 WARN 说明
    errs2, warns2 = audit_columns(detected, {"note": 6}, col_headers=col_headers)
    if errs2:
        bad.append("误拦: note 传 6（一列两用补充）被报 error %s" % errs2[:1])
    if not any("note" in w for w in warns2):
        bad.append("一列两用未提示: note 传 6 无 WARN 说明")
    # 补充分支严格模式：allow_multi_role=False 时应报错
    errs2b, _ = audit_columns(detected, {"note": 6}, col_headers=col_headers,
                              allow_multi_role=False)
    if not any("note" in e for e in errs2b):
        bad.append("严格模式失效: allow_multi_role=False 时 note 传 6 未报错")
    # 不得误报：evidence 传 12（与识别一致）绝不许报错
    errs3, _ = audit_columns(detected, {"evidence": 12}, col_headers=col_headers)
    if errs3:
        bad.append("误报: evidence 传 12（与识别一致）却报错 %s" % errs3[:1])
    # 必需角色缺失必须报错（formula 在 fixture 中既未识别也未传）
    errs4, _ = audit_columns(detected, {}, required=("formula",),
                             col_headers=col_headers)
    if not any("formula" in e for e in errs4):
        bad.append("必需角色校验失效: formula 缺失未报错")
    # 反向：unit 能识别到，列为 required 也不许报错
    errs5, _ = audit_columns(detected, {}, required=("unit",),
                             col_headers=col_headers)
    if errs5:
        bad.append("误报: unit 已识别却因 required 报错 %s" % errs5[:1])

    print("=" * 78)
    print("colmap selftest（表头定角色 + 数据验角色 + 冲突检测）")
    print("=" * 78)
    for ln in render_map(detected, {"code": 5}, title="识别结果（fixture 表头+数据）"):
        print(ln)
    print("-" * 78)
    print("code 数据探测比例 = %s（col1 分部序号应不匹配，col2 真编码应匹配）"
          % detected["code"]["probe"])
    if bad:
        for b in bad:
            print("[FAIL] %s" % b)
        print("结论: FAIL（%d 项）" % len(bad))
        return 1
    print("[OK] 14 个角色全部识别正确（表头+数据双验）")
    print("[OK] 覆盖分支 2/2 报错；补充分支默认放行+WARN、严格模式报错")
    print("[OK] 一致传值 0 误报；必需角色缺失 1/1 报错")
    print("结论: PASS")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        sys.exit(selftest())
