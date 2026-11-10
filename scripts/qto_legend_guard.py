#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qto_legend_guard.py —— 算量"三防"守卫（v4.0.0 新增）

解决三类**会整批虚报工程量**的错误（源自实测：Port City 项目）

  防1 图例页 / 符号示意页被当成安装量
       真实案例：MEP 图 mep002 是 11 类符号示意页，图面 540 个 X-UNIT 图元；
                 把它当安装量 → MEP 虚报 4429，真实安装量只有 3880。
                 正确做法：符号页**不产出安装量**，而是产出"符号↔型号"对照
                 （读懂图上有多少个对应型号的灯/风机/喷头…），单列存档。

  防2 标签块 / 文字标注块被当成构件
       真实案例：A-FLOR-ANNO-TEXT-TAG-200 图层上的 DOOR TAG 块是门标签，
                 按块实例计数 → 虚报 19369 樘门，真实门 1879 樘。

  防3 同代复本被当成多份图纸重复计量
       真实案例：78 组四图号族同内容指纹（同 sig）+ 同批时间戳，
                 按文件数计量 → 4 倍虚报。

用法
  # 1) 分类：把量测明细拆成「安装量 / 图例符号 / 标签块 / 待判定」
  python qto_legend_guard.py classify --file takeoff/register/qty_detail.csv \
      --out takeoff/register

  # 2) 复本：按内容指纹找同代复本
  python qto_legend_guard.py dupes --file takeoff/register/drawing_scan.csv

  # 3) 自证
  python qto_legend_guard.py selftest

退出码
  0 = 通过；1 = 门禁不过（有 FAIL）；2 = 用法/环境错
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict

VERSION = "4.0.0"
TOOL = "qto_legend_guard"

# --------------------------------------------------------------------------
# 受控词表
# --------------------------------------------------------------------------
# kind 三态：install（真实安装量）/ legend（图例·符号示意，不计入安装量）
#            / label（标签块·文字标注块，非构件）/ unk（待人工判定）
KINDS = ("install", "legend", "label", "unk")

# 图例/示意页判定：图号、文件名、图纸标题中出现即视为符号页
# （只作用于"页面级"字段：drawing / item_name / note，不作用于 layer）
LEGEND_PAGE_PATTERNS = [
    r"LEGEND", r"SYMBOL", r"\bKEY\b", r"图例", r"符号", r"示意图", r"示意",
    r"系统样例", r"缩写表", r"ABBREVIATION", r"索引表", r"KEYNOTES",
    r"系统图例", r"标注图例", r"常用符号",
]
# 图例专用图层（只作用于 layer 字段本身）
# 注意：不要把 ^HC-DOCKET 放进来——实测该层是"立管"（HC-DOCKET$0$HC-DOCKET=4），
# 属真实安装量，误隔离会直接少算 4 根立管。宁可漏隔离（进"待判定"）也不可误隔离。
LEGEND_LAYER_PATTERNS = [
    r"^X-UNIT-", r"^HC-L-S&S-W-SYMBOLS", r"^HC-FP-SYMBOL",
    r"^00\s*-\s*L7\b", r"^00\s*-\s*L36\b",
]
# 标签块/文字标注块判定：块名或图层名出现即判为"标签"而非构件
LABEL_PATTERNS = [
    r"\bDOOR\s*TAG\b", r"\bWINDOW\s*TAG\b", r"门标签", r"窗标签",
    r"\bTAG\b", r"ANNO-TEXT", r"ANNO-?TEXT", r"标注文字", r"文字标注",
    r"ID-TAG", r"NAME-TAG", r"房间名标注",
]
# 数量级异常阈值：单张图纸上单类构件计数超过此值先看是不是图例
SUSPICIOUS_COUNT = 2000
# 汇总型口径（图号含 + / 、 / 合计 / 唯一图 / 批次）本身就是多图汇总，
# 合法总量可以远超阈值——只对"单张图"的行启用数量级异常判定。
AGG_MARKERS = ("+", "、", ",", "，", "合计", "唯一图", "批次", "总")

LEGEND_RE = [re.compile(p, re.IGNORECASE) for p in LEGEND_PAGE_PATTERNS]
LEGEND_LAYER_RE = [re.compile(p, re.IGNORECASE) for p in LEGEND_LAYER_PATTERNS]
LABEL_RE = [re.compile(p, re.IGNORECASE) for p in LABEL_PATTERNS]

# 输入列约定（列名可用 --col-* 覆盖）
COL_DRAWING = "drawing"
COL_FAMILY = "family"
COL_SIG = "sig"
COL_BATCH = "batch"
COL_ITEM = "item_name"
COL_COUNT = "count"
COL_UNIT = "unit"
COL_KIND = "kind"
COL_MODEL = "model"
COL_LAYER = "layer"
COL_NOTE = "note"


# --------------------------------------------------------------------------
def _read_csv(path):
    if not os.path.exists(path):
        return None, "文件不存在: %s" % path
    try:
        with io.open(path, encoding="utf-8-sig", newline="") as fp:
            rows = list(csv.DictReader(fp))
    except Exception as exc:
        return None, "读取失败: %s" % exc
    if not rows:
        return [], None
    return rows, None


def _f(row, key, default=""):
    v = row.get(key)
    if v is None:
        return default
    return str(v).strip()


def _num(v, default=0.0):
    try:
        return float(str(v).strip().replace(",", ""))
    except (TypeError, ValueError):
        return default


def _write_csv(path, header, rows):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})
    return path


def _dump_json(path, obj):
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as fp:
        json.dump(obj, fp, ensure_ascii=False, indent=2)
    return path


# --------------------------------------------------------------------------
# 判定器
# --------------------------------------------------------------------------
def judge_kind(row, colmap, suspicious=SUSPICIOUS_COUNT,
               extra_layer_re=None, extra_page_re=None):
    """返回 (kind, 理由)。判定顺序：显式 kind > 标签块 > 图例页 > 图例图层 > 数量异常。

    字段作用域（v4.0.0 修正）：
      页面级正则只查 drawing/item_name/note/family；
      图层级正则**只查 layer 字段本身**。
      早先把所有字段拼成一个大串再匹配，导致 `^X-UNIT-` 这种带锚点的图层
      正则永远匹配不上（串的开头是图号）——534 个符号层里 356 个漏进安装量。
    """
    explicit = _f(row, colmap["kind"]).lower()
    if explicit in KINDS:
        return explicit, "台账显式声明 kind=%s" % explicit
    if explicit:
        return "unk", "kind 取值不在受控词表：%s" % explicit

    page_blob = " | ".join(_f(row, colmap[k]) for k in
                            ("drawing", "family", "item_name", "note"))
    layer = _f(row, colmap["layer"])
    label_blob = " | ".join(_f(row, colmap[k]) for k in
                            ("layer", "item_name", "note", "model"))
    for rx in LABEL_RE:
        if rx.search(label_blob):
            return "label", "命中标签/文字标注块特征：%s" % rx.pattern
    for rx in LEGEND_RE:
        if rx.search(page_blob):
            return "legend", "命中图例/符号页特征：%s" % rx.pattern
    for rx in (extra_page_re or []):
        if rx.search(page_blob):
            return "legend", "命中项目自定义图例页特征：%s" % rx.pattern
    for rx in LEGEND_LAYER_RE:
        if rx.search(layer):
            return "legend", "命中图例专用图层：%s" % rx.pattern
    for rx in (extra_layer_re or []):
        if rx.search(layer):
            return "legend", "命中项目自定义图例图层：%s" % rx.pattern

    cnt = _num(_f(row, colmap["count"]))
    drawing = _f(row, colmap["drawing"])
    is_agg = any(m in drawing for m in AGG_MARKERS)
    if cnt > suspicious and not is_agg:
        return "unk", "单张图纸单类构件计数 %g 超过 %d，疑似图例页或重复计入，需人工判定" % (
            cnt, suspicious)
    return "install", "无异常特征，默认按真实安装量"


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------
CLASSIFY_HEADER = ["drawing", "family", "sig", "batch", "item_name", "model",
                    "layer", "count", "unit", "kind", "reason", "note"]


def cmd_classify(a):
    rows, err = _read_csv(a.file)
    if err:
        print("[ERR] %s" % err)
        return 2
    colmap = {
        "drawing": a.col_drawing, "family": a.col_family, "sig": a.col_sig,
        "batch": a.col_batch, "item_name": a.col_item, "count": a.col_count,
        "unit": a.col_unit, "kind": a.col_kind, "model": a.col_model,
        "layer": a.col_layer, "note": a.col_note,
    }
    missing = [k for k, v in colmap.items()
               if v not in (rows[0] or {}) and v not in (COL_DRAWING, COL_FAMILY,
                                                        COL_SIG, COL_BATCH, COL_ITEM,
                                                        COL_COUNT, COL_UNIT, COL_KIND,
                                                        COL_MODEL, COL_LAYER, COL_NOTE)]
    if missing:
        print("[ERR] 输入缺少必需列：%s（可用 --col-* 指定实际列名）" % ",".join(missing))
        return 2

    extra_layer_re = [re.compile(x, re.IGNORECASE)
                      for x in (getattr(a, "legend_layer", None) or [])]
    extra_page_re = [re.compile(x, re.IGNORECASE)
                     for x in (getattr(a, "legend_page", None) or [])]
    out_rows, buckets = [], defaultdict(list)
    for i, r in enumerate(rows, start=2):
        kind, reason = judge_kind(r, colmap, suspicious=a.suspicious_count,
                                  extra_layer_re=extra_layer_re,
                                  extra_page_re=extra_page_re)
        cnt = _num(_f(r, colmap["count"]))
        unit = _f(r, colmap["unit"])
        rec = {
            "drawing": _f(r, colmap["drawing"]),
            "family": _f(r, colmap["family"]),
            "sig": _f(r, colmap["sig"]),
            "batch": _f(r, colmap["batch"]),
            "item_name": _f(r, colmap["item_name"]),
            "model": _f(r, colmap["model"]),
            "layer": _f(r, colmap["layer"]),
            "count": _f(r, colmap["count"]),
            "unit": unit,
            "kind": kind,
            "reason": reason,
            "note": _f(r, colmap["note"]),
        }
        out_rows.append(rec)
        buckets[kind].append((rec, cnt))

    install_rows = [r for r in out_rows if r["kind"] == "install"]
    legend_rows = [r for r in out_rows if r["kind"] == "legend"]
    label_rows = [r for r in out_rows if r["kind"] == "label"]
    unk_rows = [r for r in out_rows if r["kind"] == "unk"]

    def total(rs):
        s = 0.0
        for r in rs:
            s += _num(r["count"])
        return s

    # 型号×符号对照表：图例行的型号列留空则用 item_name 占位
    sym_rows = []
    for r in legend_rows:
        sym_rows.append({
            "model": r["model"] or r["item_name"],
            "symbol_count": r["count"],
            "unit": r["unit"],
            "drawing": r["drawing"],
            "layer": r["layer"],
            "source": "图例/符号页读懂型号数量（不计入安装量）",
        })

    os.makedirs(a.out, exist_ok=True)
    p_install = _write_csv(os.path.join(a.out, "安装量明细.csv"),
                           CLASSIFY_HEADER, install_rows)
    p_legend = _write_csv(os.path.join(a.out, "图例符号明细.csv"),
                           CLASSIFY_HEADER, legend_rows)
    p_label = _write_csv(os.path.join(a.out, "标签块明细.csv"),
                          CLASSIFY_HEADER, label_rows)
    p_unk = _write_csv(os.path.join(a.out, "待判定明细.csv"),
                       CLASSIFY_HEADER, unk_rows)
    p_sym = _write_csv(os.path.join(a.out, "符号型号对照表.csv"),
                       ["model", "symbol_count", "unit", "drawing", "layer", "source"],
                       sym_rows)

    n_all = len(out_rows)
    install_sum = total(install_rows)
    legend_sum = total(legend_rows)
    label_sum = total(label_rows)
    unk_sum = total(unk_rows)

    print("=" * 78)
    print("三防分类  输入=%d 行  →  安装量 %d 行 / 图例符号 %d 行 / 标签块 %d 行 / 待判定 %d 行"
          % (n_all, len(install_rows), len(legend_rows), len(label_rows), len(unk_rows)))
    print("=" * 78)
    print("真实安装量合计 = %g（%d 行）" % (install_sum, len(install_rows)))
    if legend_sum:
        print("[已隔离] 图例/符号页数量合计 %g — 不计入安装量，仅产出符号↔型号对照 %d 条"
              % (legend_sum, len(sym_rows)))
    if label_sum:
        print("[已隔离] 标签块/文字标注块数量合计 %g — 非构件" % label_sum)
    if unk_sum:
        print("[WARN] 待人工判定 %d 行（合计 %g）——未计入安装量，须逐条落口径"
              % (len(unk_rows), unk_sum))
        for r in unk_rows[:20]:
            print("    %s | %s | %s | %s" % (r["drawing"], r["item_name"],
                                              r["count"], r["reason"]))
    if a.fail_on_unk and unk_rows:
        print("[FAIL] 存在 %d 行待判定，不允许带疑问交付" % len(unk_rows))
        return 1

    summary = {
        "tool": TOOL, "version": VERSION, "input": a.file,
        "rows_total": n_all,
        "install_rows": len(install_rows), "install_total": install_sum,
        "legend_rows": len(legend_rows), "legend_total_isolated": legend_sum,
        "label_rows": len(label_rows), "label_total_isolated": label_sum,
        "unknown_rows": len(unk_rows), "unknown_total": unk_sum,
        "symbol_model_pairs": len(sym_rows),
        "outputs": {"install": p_install, "legend": p_legend, "label": p_label,
                    "unknown": p_unk, "symbol_models": p_sym},
    }
    if a.json:
        _dump_json(a.json, summary)
    print("-" * 78)
    print("输出：%s | %s | %s | %s | %s" % (p_install, p_legend, p_label, p_unk, p_sym))
    return 0


# --------------------------------------------------------------------------
# dupes
# --------------------------------------------------------------------------
DUPES_HEADER = ["sig", "family", "batch", "n_replicas", "rep_files", "verdict"]
REP_VERDICT = "同代复本：只按 1 张计量，其余为同一张图纸的转存副本"


def cmd_dupes(a):
    rows, err = _read_csv(a.file)
    if err:
        print("[ERR] %s" % err)
        return 2
    if not rows:
        print("[ERR] 输入为空")
        return 2
    if COL_SIG not in rows[0]:
        print("[ERR] 输入缺少内容指纹列 sig（对每张图算：实体类型|图层|块名/文本 排序后 MD5）")
        return 2

    groups = defaultdict(list)
    for r in rows:
        sig = _f(r, COL_SIG)
        if not sig:
            continue
        groups[sig].append(r)

    out, dup_groups = [], []
    for sig, rs in groups.items():
        fam = Counter(_f(r, COL_FAMILY) for r in rs).most_common(1)
        batch = Counter(_f(r, COL_BATCH) for r in rs).most_common(1)
        files = [ _f(r, "rep_file") or _f(r, "file") or _f(r, "rep_files")
                  for r in rs ]
        n = len(rs)
        if n > 1:
            dup_groups.append({
                "sig": sig,
                "family": fam[0][0] if fam else "",
                "batch": batch[0][0] if batch else "",
                "n_replicas": n,
                "rep_files": " | ".join([f for f in files if f]),
                "verdict": REP_VERDICT,
            })
    dup_groups.sort(key=lambda d: (-d["n_replicas"], d["family"]))

    os.makedirs(a.out, exist_ok=True)
    p = _write_csv(os.path.join(a.out, "复本清单.csv"), DUPES_HEADER, dup_groups)

    n_files = len(rows)
    n_unique = len(groups)
    print("=" * 78)
    print("复本检查  输入 %d 个文件 → 内容指纹唯一 %d 张（同代复本 %d 组）"
          % (n_files, n_unique, len(dup_groups)))
    print("=" * 78)
    if dup_groups:
        multi = sum(1 for d in dup_groups if d["n_replicas"] > 2)
        print("[FAIL] 发现同内容复本：若按文件数计量将虚报。清单前 20 组：")
        for d in dup_groups[:20]:
            print("    %s  %-22s batch=%-10s ×%d" % (d["sig"][:10], d["family"],
                                                      d["batch"], d["n_replicas"]))
        if multi:
            print("    其中 %d 组为 3 张及以上同代复本（图号族特征），风险最高" % multi)
        print("口径：同 sig 只取 1 张计量；确属不同版本则须用文件版本时间（$TDUPDATE）另行定版")
        if a.strict:
            print("[FAIL] strict 模式：存在未处置的复本")
            return 1
    else:
        print("[PASS] 无同内容复本")
    summary = {"tool": TOOL, "version": VERSION, "files": n_files,
               "unique_by_sig": n_unique, "dup_groups": len(dup_groups),
               "multi_replica_groups": sum(1 for d in dup_groups if d["n_replicas"] > 2),
               "output": p}
    if a.json:
        _dump_json(a.json, summary)
    print("输出：%s" % p)
    return 0


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------
SELFTEST_ROWS = [
    # 图例页（符号示意）：不计入安装量
    {"drawing": "MEP-002", "family": "mep", "sig": "s1", "batch": "mep2_x",
     "item_name": "风机盘管符号", "model": "FCU-01", "layer": "X-UNIT-05",
     "count": "180", "unit": "台", "kind": "", "note": "系统图例页"},
    {"drawing": "MEP-002", "family": "mep", "sig": "s1", "batch": "mep2_x",
     "item_name": "喷头符号", "model": "SPH-02", "layer": "X-UNIT-07",
     "count": "220", "unit": "个", "kind": "", "note": "系统图例页"},
    # 真实安装量
    {"drawing": "MEP-101", "family": "mep", "sig": "s2", "batch": "mep_x",
     "item_name": "风机盘管安装量", "model": "FCU-01", "layer": "HC-MECH-FCU",
     "count": "42", "unit": "台", "kind": "", "note": "平面布置图"},
    {"drawing": "MEP-101", "family": "mep", "sig": "s2", "batch": "mep_x",
     "item_name": "喷头安装量", "model": "SPH-02", "layer": "HC-FP-SPRINK",
     "count": "58", "unit": "个", "kind": "", "note": "平面布置图"},
    # 标签块（门标签）：非构件
    {"drawing": "A-100", "family": "arch", "sig": "s3", "batch": "oda_x",
     "item_name": "DOOR TAG 块计数", "model": "", "layer": "A-FLOR-ANNO-TEXT-TAG-200",
     "count": "19369", "unit": "樘", "kind": "", "note": "标签块"},
    {"drawing": "A-100", "family": "arch", "sig": "s3", "batch": "oda_x",
     "item_name": "门（实体块）", "model": "DT-01", "layer": "A-DOOR",
     "count": "1879", "unit": "樘", "kind": "", "note": "实体门块"},
    # 待判定：超大数
    {"drawing": "MEP-999", "family": "mep", "sig": "s4", "batch": "mep_x",
     "item_name": "风管长度", "model": "", "layer": "HC-DUCT",
     "count": "5600", "unit": "米", "kind": "", "note": ""},
]

SELFTEST_SCAN = [
    {"sig": "sA", "family": "CD-700", "batch": "oda_x", "rep_file": "CD-700_A.dxf"},
    {"sig": "sA", "family": "CD-700", "batch": "oda_x", "rep_file": "CD-700_B.dxf"},
    {"sig": "sA", "family": "CD-700", "batch": "oda_x", "rep_file": "CD-700_C.dxf"},
    {"sig": "sA", "family": "CD-700", "batch": "oda_x", "rep_file": "CD-700_D.dxf"},
    {"sig": "sB", "family": "CD-400", "batch": "oda_x", "rep_file": "CD-400_A.dxf"},
]


def cmd_selftest(a):
    base = a.tmpdir or tempfile.mkdtemp(prefix="qto_guard_")
    tmp = os.path.join(base, "guard_selftest")
    os.makedirs(tmp, exist_ok=True)
    fails = []

    detail = os.path.join(tmp, "qty_detail.csv")
    _write_csv(detail, ["drawing", "family", "sig", "batch", "item_name",
                        "model", "layer", "count", "unit", "kind", "note"],
               SELFTEST_ROWS)
    scan = os.path.join(tmp, "drawing_scan.csv")
    _write_csv(scan, ["sig", "family", "batch", "rep_file"], SELFTEST_SCAN)

    # --- 用例1：图例页隔离 ---
    class A1:
        file = detail; out = os.path.join(tmp, "o1"); json = ""
        col_drawing = COL_DRAWING; col_family = COL_FAMILY; col_sig = COL_SIG
        col_batch = COL_BATCH; col_item = COL_ITEM; col_count = COL_COUNT
        col_unit = COL_UNIT; col_kind = COL_KIND; col_model = COL_MODEL
        col_layer = COL_LAYER; col_note = COL_NOTE
        fail_on_unk = False; suspicious_count = SUSPICIOUS_COUNT
        legend_layer = []; legend_page = []
    rc = cmd_classify(A1)
    if rc != 0:
        fails.append("用例1 classify 返回 %d（应 0）" % rc)
    inst = _read_csv(os.path.join(tmp, "o1", "安装量明细.csv"))[0] or []
    leg = _read_csv(os.path.join(tmp, "o1", "图例符号明细.csv"))[0] or []
    lab = _read_csv(os.path.join(tmp, "o1", "标签块明细.csv"))[0] or []
    sym = _read_csv(os.path.join(tmp, "o1", "符号型号对照表.csv"))[0] or []

    inst_total = sum(_num(r.get("count")) for r in inst)
    if inst_total != 42 + 58 + 1879:
        fails.append("用例1 安装量合计 %g，应为 1979（图例 400 与标签 19369 均须排除）" % inst_total)
    if len(leg) != 2:
        fails.append("用例1 图例行数 %d，应为 2" % len(leg))
    if len(lab) != 1:
        fails.append("用例1 标签行数 %d，应为 1" % len(lab))
    if len(sym) != 2:
        fails.append("用例1 符号型号对照 %d 条，应为 2（读懂型号数量而非列清单）" % len(sym))
    if any(r.get("kind") == "install" for r in leg + lab):
        fails.append("用例1 图例/标签行混入安装量")

    # --- 用例2：显式 kind 优先 ---
    class A2(A1):
        file = detail
    rows2 = [dict(r) for r in SELFTEST_ROWS]
    rows2[0]["kind"] = "install"      # 人为改判
    d2 = os.path.join(tmp, "qty_detail2.csv")
    _write_csv(d2, list(rows2[0].keys()), rows2)
    A2.file = d2
    A2.out = os.path.join(tmp, "o2")
    cmd_classify(A2)
    inst2 = _read_csv(os.path.join(tmp, "o2", "安装量明细.csv"))[0] or []
    if not any(r.get("model") == "FCU-01" and r.get("drawing") == "MEP-002" for r in inst2):
        fails.append("用例2 显式 kind=install 未被尊重")

    # --- 用例3：复本识别 ---
    class A3:
        file = scan; out = tmp; json = ""; strict = False
    rc = cmd_dupes(A3)
    if rc != 0:
        fails.append("用例3 dupes 返回 %d（应 0）" % rc)
    dup = _read_csv(os.path.join(tmp, "复本清单.csv"))[0] or []
    if len(dup) != 1 or int(_num(dup[0].get("n_replicas"))) != 4:
        fails.append("用例3 复本组识别错误：%s" % dup)
    if dup and "CD-700" not in dup[0].get("family", ""):
        fails.append("用例3 复本组 family 不符")

    # --- 用例4：strict 模式拦复本 ---
    class A4(A3):
        strict = True
    if cmd_dupes(A4) != 1:
        fails.append("用例4 strict 模式未拦住复本（应返回 1）")

    # --- 用例6：汇总型大数不误判 / 单张大数仍被拦 ---
    rows6 = [dict(r) for r in SELFTEST_ROWS]
    rows6.append({"drawing": "全楼汇总", "family": "mep", "sig": "", "batch": "",
                  "item_name": "洁具全楼合计", "model": "", "layer": "HC-L-SWS-FITTINGS",
                  "count": "3534", "unit": "个", "kind": "", "note": "39 张图汇总"})
    rows6.append({"drawing": "A-200", "family": "arch", "sig": "s9", "batch": "oda_x",
                  "item_name": "单图构件数", "model": "", "layer": "HC-X",
                  "count": "3534", "unit": "个", "kind": "", "note": ""})
    d6 = os.path.join(tmp, "qty_detail6.csv")
    _write_csv(d6, list(rows6[0].keys()), rows6)
    A6 = A1
    A6.file = d6
    A6.out = os.path.join(tmp, "o6")
    cmd_classify(A6)
    unk6 = _read_csv(os.path.join(tmp, "o6", "待判定明细.csv"))[0] or []
    drawings = {r.get("drawing") for r in unk6}
    if "全楼汇总" in drawings:
        fails.append("用例6 汇总型大数被误判为待判定")
    if "A-200" not in drawings:
        fails.append("用例6 单张大数未被拦为待判定")

    # --- 用例5：列名缺失须报错 ---
    class A5(A1):
        file = os.path.join(tmp, "nope.csv")
    if cmd_classify(A5) != 2:
        fails.append("用例5 文件不存在应返回 2")

    print("=" * 78)
    if fails:
        print("[FAIL] qto_legend_guard 自证 %d 项不通过：" % len(fails))
        for f in fails:
            print("   - %s" % f)
        print("=" * 78)
        return 1
    print("[PASS] qto_legend_guard 自证全部通过（6 组用例）")
    print("  用例1 图例页隔离 + 标签块剔除 + 符号型号对照")
    print("  用例2 显式 kind 优先于模式判定")
    print("  用例3 同内容复本识别（4 张同 sig → 1 张）")
    print("  用例4 strict 模式拦截")
    print("  用例5 输入缺失返回 2")
    print("  用例6 汇总型大数不误判 / 单张大数仍被拦")
    print("=" * 78)
    return 0


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description="算量三防守卫：图例页 / 标签块 / 同代复本")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("classify", help="把量测明细拆成安装量/图例/标签/待判定")
    p1.add_argument("--file", required=True)
    p1.add_argument("--out", required=True)
    p1.add_argument("--json", default="")
    p1.add_argument("--col-drawing", default=COL_DRAWING)
    p1.add_argument("--col-family", default=COL_FAMILY)
    p1.add_argument("--col-sig", default=COL_SIG)
    p1.add_argument("--col-batch", default=COL_BATCH)
    p1.add_argument("--col-item", default=COL_ITEM)
    p1.add_argument("--col-count", default=COL_COUNT)
    p1.add_argument("--col-unit", default=COL_UNIT)
    p1.add_argument("--col-kind", default=COL_KIND)
    p1.add_argument("--col-model", default=COL_MODEL)
    p1.add_argument("--col-layer", default=COL_LAYER)
    p1.add_argument("--col-note", default=COL_NOTE)
    p1.add_argument("--legend-layer", action="append", default=[],
                    help="项目自定义图例图层正则（可重复），如 '^HC-FP-SYMBOL'")
    p1.add_argument("--legend-page", action="append", default=[],
                    help="项目自定义图例页正则（可重复），作用于图号/图名/备注")
    p1.add_argument("--suspicious-count", type=int, default=SUSPICIOUS_COUNT,
                    help="单张图纸单类构件计数超过此值判为待判定（默认 %d）" % SUSPICIOUS_COUNT)
    p1.add_argument("--fail-on-unk", action="store_true",
                    help="存在待判定行即返回 1（交付前用）")
    p1.set_defaults(func=cmd_classify)

    p2 = sub.add_parser("dupes", help="按内容指纹找同代复本")
    p2.add_argument("--file", required=True)
    p2.add_argument("--out", default=".")
    p2.add_argument("--json", default="")
    p2.add_argument("--strict", action="store_true", help="存在复本即返回 1")
    p2.set_defaults(func=cmd_dupes)

    p3 = sub.add_parser("selftest", help="自证")
    p3.add_argument("--tmpdir", default="", help="自证临时目录（默认系统临时目录）")
    p3.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
