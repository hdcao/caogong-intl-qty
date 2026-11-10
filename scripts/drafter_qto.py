#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
drafter_qto.py -- 绘图员负责算量（Drafter-owns-QTO）机制的落账与自检工具。

=============================================================================
机制要点（这套脚本存在的理由）
=============================================================================
算量的错误里，**算术错误是少数，"读数错误"才是多数**：
  - 图上有这个数，但它量的是纵向，你要的是横向（−39.4%）；
  - 图上是梁间距 1.28，你当成了梁宽（+131%）；
  - 表头写"承台顶"，实际给的是承台底高程（整条高程链错位）；
  - 双幅桥只数了一排（少算一半）。
这些**算术全对**，错的是"这个数回答的是不是你问的问题"。

而唯一有资格判定这一点的人，是**亲手在图元上定位过那个数的人**——即绘图员。
所以本机制的第一原则：

    ★ 读数的责任人 = 建模/绘图的人。复核人只能复核，不能代填。★

配套四条硬纪律：
  1. 一图一签    每张图的每条读数都要落账（图号 + 图面位置 + 方向 + 口径）
  2. 读数四问    下笔前必须能答出"在哪 / 什么方向 / 谁的什么属性 / 是不是我问的"
  3. 方法分级    method 六档记录取证强度（v4.6.0 起 render_read 拆为 text/est 两档），
                 弱证据自动降置信并挂 RFI
  4. 改数回图    任何数值变更必须回图重新取证，不接受"上一版是这么定的"

=============================================================================
口径是两个维度（与 gate_dimension_wording.py 共用 qto_vocab.py）
=============================================================================
  范围口径 span      —— 全桥 / 单幅 / 两侧 / 每座 …
  证据强度 evidence  —— 图纸确认 / 推算 / 估算 / 待核

台账同时登记这两列；任一缺失即报警。

=============================================================================
用法
=============================================================================
  # ① 新建读数台账（按分部建册）
  python drafter_qto.py new --out takeoff/register/ --package 桥梁

  # ② 登记一条读数（四问齐全 + method + 两类口径词 + 签名）
  python drafter_qto.py add --file takeoff/register/readings_桥梁.csv \
        --code CBS-OVA-I03 --item "桥台台身混凝土" \
        --sheet "23041-OVA-29-V0/3-3" --location "C.1 台区立面 x642~654" \
        --direction 纵向 --object "桥台台身" --attr "楔形 顶1.00/底5.50/高6.00" \
        --method chain_derived --span 全桥 --evidence 推算 \
        --value 503.10 --unit M3 --formula "2×(1.00+5.50)/2×12.90×6.00" \
        --drafter 曹工 --date 2026-10-21

  # 若本项目确有批准的 CM=立方米约定，必须明确绑定项目单位别名注册表：
  # 在 add/check 命令上传 --unit-registry reference/unit_alias_registry.json --project-id <项目编号>

  # ③ 自检台账
  python drafter_qto.py check --file takeoff/register/readings_桥梁.csv

  # ④ 与道桥开项清单比对，报漏项
  python drafter_qto.py coverage --file takeoff/register/readings_桥梁.csv

退出码：0 通过 / 1 存在 FAIL / 2 工具自身无法运行
"""

from __future__ import print_function

import argparse
import csv
import io
import json
import os
import re
import sys
import math

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


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qto_vocab import (METHODS, METHOD_ALIAS, TILE_LOCATOR_RE, WEAK_METHODS,
                       SPAN_WORDS, EVIDENCE_WORDS,
                       ESTIMATE_WORDS, BANNED_WORDS, DIM_LABEL,
                       norm_unit, dim_of_unit, validate_unit,
                       find_span_words, find_evidence_words,
                       find_source_labels, find_banned,
                       find_dimension_conflict)

# v2.1.0：四问的机器判据。
# 此前 sheet/location/direction/object/attribute 只判"strip() 非空"，
# 于是 `--sheet 见图 --location 见图 --object 构件 --attr 宽` 能拿满分——
# 这正是 SKILL.md 硬规矩里禁止的"用字符串存在性检查冒充防线"。
DIRECTIONS = ("纵向", "横向", "竖向", "斜向", "未知")
GENERIC_FILLER = ("见图", "见附图", "见详图", "同上", "略", "tbd", "待定", "构件", "宽", "高", "长")
# 图面位置必须能落回图上：坐标（x642 / (592,391) / pt 数值）或具名图表
LOC_ANCHOR_RE = re.compile(
    r"[xyXY]\s*[=:：]?\s*\d{2,5}|[（(]\s*\d{2,5}\s*[,，]\s*\d{2,5}|"
    r"\d{3,5}\s*[~-]\s*\d{3,5}|"
    r"表|图|立面|剖面|断面|详图|大样|COUPE|PLAN|DETAIL|ELEVATION|SECTION",
    re.IGNORECASE)
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

HEADER = [
    "reading_id", "template_id", "cbs_code", "item_name", "sheet",
    "drawing_location", "direction", "object_name", "attribute",
    "method", "span_word", "evidence_word", "value", "unit",
    "formula", "source_ref", "drafter", "date", "note",
]


# --------------------------------------------------------------------------
def _read_rows(path):
    if not os.path.exists(path):
        return None, "台账文件不存在: %s" % path
    if path.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError:
            return None, "缺少 openpyxl，无法读取 xlsx"
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        raw = [[c.value for c in row] for row in ws.iter_rows()]
        if not raw:
            return [], None
        head = [str(h).strip() if h is not None else "" for h in raw[0]]
        out = []
        for r in raw[1:]:
            if all(v in (None, "") for v in r):
                continue
            out.append(dict(zip(head, r)))
        return out, None
    with io.open(path, encoding="utf-8-sig") as fp:
        return list(csv.DictReader(fp)), None


def _write_csv(path, rows):
    # v2.1.0：读支持 xlsx、写只支持 CSV——此前 add 一个 .xlsx 台账会把它
    # 覆盖成 284 字节的 CSV 文本，然后 check 崩在 BadZipFile。
    if str(path).lower().endswith((".xlsx", ".xlsm", ".xls")):
        print("[ERR] 本工具只写 CSV；%s 是 Excel 文件，拒绝覆盖。"
              "请改用 .csv 台账（或在 Excel 里另存为 CSV 后再落账）。" % path)
        return False
    try:
        from qto_core import atomic_write_csv
        atomic_write_csv(path, HEADER, ({k: r.get(k, "") for k in HEADER} for r in rows), excel_safe=True)
        return True
    except Exception as exc:
        print("[ERR] 台账原子写入失败，原文件保持不变：%s" % exc)
        return False


def _valid_date(s):
    """YYYY-MM-DD 且真实存在（挡住 2026-02-30 / 2026-13-45）。"""
    try:
        y, m, d = [int(x) for x in s.split("-")]
    except (ValueError, AttributeError):
        return False
    if not (1900 <= y <= 2200 and 1 <= m <= 12 and 1 <= d <= 31):
        return False
    import datetime
    try:
        datetime.date(y, m, d)
    except ValueError:
        return False
    return True


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _all_template_ids():
    """全部 catalog 的模板编号（供 argparse choices 收紧 --template）。"""
    ids = []
    for rows in CATALOGS.values():
        ids.extend(r[0] for r in rows)
    return sorted(set(ids))


def _fmt_num(v):
    try:
        f = float(v)
        if not math.isfinite(f):
            return str(v)
    except (TypeError, ValueError, OverflowError):
        return str(v)
    return "%d" % int(round(f)) if abs(f - round(f)) < 1e-9 else "%.4f" % f


def _dump_json(path, obj):
    try:
        from qto_core import atomic_write_json
        atomic_write_json(path, obj)
    except Exception as exc:
        print("[ERR] JSON 原子写入失败：%s" % exc)
        return
    print("[OK] 已写出: %s" % path)


# --------------------------------------------------------------------------
def cmd_new(a):
    out = a.out or "takeoff/register/"
    if os.path.isdir(out) or out.endswith(("/", "\\")):
        path = os.path.join(out, "readings_%s.csv" % (a.package or "ALL"))
    else:
        path = out
    if os.path.exists(path) and not a.force:
        print("[WARN] 已存在，未覆盖: %s (加 --force 覆盖)" % path)
        return 0
    _write_csv(path, [])
    print("[OK] 已建读数台账: %s" % path)
    print("     method   ∈ " + "/".join(sorted(METHODS)))
    print("     span     ∈ " + "/".join(SPAN_WORDS))
    print("     evidence ∈ " + "/".join(("图纸确认", "推算", "估算", "待核")))
    return 0


def cmd_add(a):
    rows, err = _read_rows(a.file) if os.path.exists(a.file) else ([], None)
    if err:
        print("[ERR] %s" % err)
        return 2
    # v4.6.0：旧写法 `render_read` 不再作为新行的合法取值，但给出明确的迁移提示，
    # 而不是一句"未知 method"让人猜。转换方向固定为 est（从低不从高）。
    if a.method in METHOD_ALIAS:
        new_m = METHOD_ALIAS[a.method]
        print("[WARN] method=%s 是 v4.5.x 旧写法，本行按 %s 登记（从低不从高）。\n"
              "       若确实是切块读到了图面数字，请改用 render_read_text 并记 tile 坐标+字高。"
              % (a.method, new_m))
        a.method = new_m
    if a.method not in METHODS:
        print("[FAIL] 未知 method=%s，只能取 %s" % (a.method, "/".join(sorted(METHODS))))
        return 1
    rid = a.id or ("R%03d" % (len(rows) + 1))
    if any(str(r.get("reading_id")) == rid for r in rows):
        print("[FAIL] reading_id=%s 已存在 — 编号不许重排/复用" % rid)
        return 1
    row = {
        "reading_id": rid, "template_id": a.template or "",
        "cbs_code": a.code or "", "item_name": a.item or "",
        "sheet": a.sheet or "", "drawing_location": a.location or "",
        "direction": a.direction or "", "object_name": a.object or "",
        "attribute": a.attr or "", "method": a.method,
        "span_word": a.span or "", "evidence_word": a.evidence or "",
        "value": _fmt_num(a.value), "unit": norm_unit(a.unit),
        "formula": a.formula or "", "source_ref": a.source or "",
        "drafter": a.drafter or "", "date": a.date or "", "note": a.note or "",
    }
    # ★ 先自检，再写盘。此前 add 无条件打印 [OK]，空字段照样"登记成功"，
    #   等于工具替违规背书（四问只剩 "strip() 非空"）。
    fails = [f for f in check_row(len(rows) + 2, row,
                                 unit_registry=getattr(a, "unit_registry", "") or None,
                                 project_id=getattr(a, "project_id", "") or None) if f[0] == "FAIL"]
    if fails:
        print("[FAIL] 本行**未写入**——以下内容不合规：")
        for _, code, msg in fails:
            print("  %-13s %s" % (code, msg))
        print("补齐后重试（reading_id=%s 未被占用）。落账四问见 "
              "references/drafter-qto-role.md §2。" % rid)
        return 1
    rows.append(row)
    if not _write_csv(a.file, rows):
        return 2
    print("[OK] 已登记 %s  %s = %s %s（%s / %s）" % (
        rid, a.item or "", _fmt_num(a.value), norm_unit(a.unit),
        METHODS[a.method][0], a.evidence or "—"))
    return 0


# --------------------------------------------------------------------------
def check_row(r, row, unit_registry=None, project_id=None):
    """对一条读数做自检，返回 [(level, code, msg), …]。"""
    out = []
    rid = str(row.get("reading_id") or "").strip()
    where = "第%d行(%s)" % (r, rid or "无编号")
    code = str(row.get("cbs_code") or "").strip()
    unit = str(row.get("unit") or "").strip()
    value = str(row.get("value") or "").strip()
    basis = str(row.get("formula") or "") + " " + str(row.get("note") or "")
    span = str(row.get("span_word") or "")
    ev_txt = str(row.get("evidence_word") or "")
    src = str(row.get("source_ref") or "")

    # --- 读数四问：位置 / 方向 / 构件 / 属性 ---
    for col, label in (("sheet", "图号"), ("drawing_location", "图面位置"),
                       ("direction", "方向或维度"), ("object_name", "所属构件"),
                       ("attribute", "属性名")):
        if not str(row.get(col) or "").strip():
            out.append(("FAIL", "DRAFTER-Q",
                        "%s 缺「%s」— 读数四问答不全，不许落账" % (where, label)))
    if not code:
        out.append(("FAIL", "DRAFTER-Q", "%s 缺 CBS 编码" % where))

    # --- 四问的内容判据（v2.1.0）---
    # 非空 ≠ 答了。位置必须能落回图上，构件/属性不许是泛词。
    loc = str(row.get("drawing_location") or "").strip()
    obj = str(row.get("object_name") or "").strip()
    attr = str(row.get("attribute") or "").strip()
    _squeeze = lambda s: re.sub(r"\s+", "", s).lower()
    GENERIC_LOC = ("见图", "见附图", "见详图", "同上", "略", "tbd", "待定", "-", "—")
    if loc and (_squeeze(loc) in GENERIC_LOC or not LOC_ANCHOR_RE.search(loc)):
        out.append(("FAIL", "DRAFTER-Q",
                    "%s 图面位置 %r 不可复现——须写坐标（x642~654 / (592,391)）"
                    "或具名图元（表/立面/剖面/COUPE/PLAN/DETAIL）" % (where, loc)))
    if obj and _squeeze(obj) in ("构件", "部位", "结构", "thing"):
        out.append(("FAIL", "DRAFTER-Q",
                    "%s 所属构件写成泛词 %r，须写具体构件名（如 桥台台身）" % (where, obj)))
    if attr and _squeeze(attr) in ("宽", "高", "长", "厚", "尺寸", "数量", "n"):
        out.append(("FAIL", "DRAFTER-Q",
                    "%s 属性写成泛词 %r，须写清是哪个属性（如 楔形台身顶厚1.00/底厚5.50）"
                    % (where, attr)))

    # --- 方向：本项目头号事故类型（把纵向当横向 −39.4%）的唯一落点 ---
    d = str(row.get("direction") or "").strip()
    if d and d not in DIRECTIONS:
        out.append(("FAIL", "DRAFTER-DIR",
                    "%s 方向 %r 非法，只能取 %s" % (where, d, " / ".join(DIRECTIONS))))

    # --- 日期：签名没有可信时间戳，等于没有签名 ---
    dt = str(row.get("date") or "").strip()
    if not dt:
        out.append(("FAIL", "DRAFTER-DATE", "%s 缺日期——落账时间不可信" % where))
    elif not ISO_DATE_RE.match(dt) or not _valid_date(dt):
        out.append(("FAIL", "DRAFTER-DATE",
                    "%s 日期 %r 非 YYYY-MM-DD 或不存在（如 2026-02-30）" % (where, dt)))

    # --- 单位：词表外 = 未校验，不许静默入库 ---
    if not unit:
        out.append(("FAIL", "DRAFTER-UNIT", "%s 缺单位——量纲无法校验" % where))
    else:
        _, uerr = validate_unit(unit, unit_registry=unit_registry, project_id=project_id)
        if uerr:
            out.append(("FAIL", "DRAFTER-UNIT", "%s %s" % (where, uerr)))

    # --- method 合法 + 弱证据必须挂 RFI ---
    m_raw = str(row.get("method") or "").strip()
    # v4.6.0：历史 `render_read` 归一化为 `render_read_est`（从低不从高），
    # 不改历史 CSV 的值——改历史记录等于篡改证据链。
    m = METHOD_ALIAS.get(m_raw, m_raw)
    if m not in METHODS:
        out.append(("FAIL", "DRAFTER-M", "%s method=%r 非法" % (where, m_raw)))
    elif m_raw in METHOD_ALIAS:
        out.append(("WARN", "DRAFTER-M",
                    "%s method=%r 是 v4.5.x 旧写法，按「%s」计（从低不从高）；"
                    "新行请写 %s" % (where, m_raw, METHODS[m][0], m)))
    if m in WEAK_METHODS:
        # v2.1.0：从 WARN 升为 FAIL。SKILL.md 写的是"弱证据必须挂 RFI"，
        # WARN 不阻断 = 这句话没有约束力（实测原样例 render_read+value=0 全绿）。
        if not src:
            out.append(("FAIL", "DRAFTER-M",
                        "%s 取证方法为「%s」却无证据去处 — 弱证据必须挂 RFI"
                        % (where, METHODS[m][0])))
        elif not re.search(r"RFI|台账|清单|规范|图", src, re.I):
            out.append(("FAIL", "DRAFTER-M",
                        "%s 「%s」的证据去处 %r 不像 RFI/台账/清单/图号"
                        % (where, METHODS[m][0], src)))
    elif m == "render_read_text":
        # v4.6.0：读到字才配 medium。没有 tile 坐标/字高，读法就不可复现，
        # 这一行退化成"我记得我看到了"——按估读处理，而不是按读字放行。
        # 表头是 drawing_location（见 HEADER）；读 location 会永远取到空串，
        # 于是把 tile 坐标写在"图面位置"的正规做法被判 FAIL，只有写进 note 才过——
        # 证据被扭到非设计字段上，等于这条判据反向工作。
        loc_txt = (str(row.get("drawing_location") or "") + " "
                   + str(row.get("note") or ""))
        if not TILE_LOCATOR_RE.search(loc_txt):
            out.append(("FAIL", "DRAFTER-M",
                        "%s method=render_read_text 却无 tile 坐标/字高记录 — "
                        "切块读数必须可复现，否则按 render_read_est 处理" % where))
    # --- 强证据必须可复算 ---
    if m in ("chain_derived", "vector_cluster") and not str(
            row.get("formula") or "").strip():
        out.append(("FAIL", "DRAFTER-FORM",
                    "%s method=%s 却无计算式——尺寸链/聚类必须能复算" % (where, m)))

    # --- 范围口径词 ---
    if not find_span_words(span):
        out.append(("FAIL", "DRAFTER-SPAN",
                    "%s 缺范围口径词（全桥/单幅/两侧/每座…）" % where))

    # --- 证据强度词 ---
    evs = find_evidence_words(ev_txt)
    if not evs:
        out.append(("FAIL", "DRAFTER-EV",
                    "%s 缺证据强度词（图纸确认/推算/估算/待核）" % where))
    elif any(w in ESTIMATE_WORDS for w in evs) and not find_source_labels(
            src + " " + basis):
        out.append(("FAIL", "DRAFTER-EV",
                    "%s 证据强度为「%s」却无证据去处（RFI/台账/业主清单编号/规范条款号）"
                    % (where, evs[0])))

    # --- 量纲矛盾 ---
    c = find_dimension_conflict(unit, value,
                                (row.get("item_name"), row.get("formula"),
                                 row.get("note")),
                                unit_registry=unit_registry, project_id=project_id)
    if c:
        num, tok, d_unit, d_txt = c
        out.append(("FAIL", "DRAFTER-DIM",
                    "%s 单位列=%s（%s），文本里 %s 后面写的是 %s（%s）— "
                    "单位与口径矛盾，须先定案再出量" % (
                        where, norm_unit(unit), DIM_LABEL.get(dim_of_unit(unit, unit_registry=unit_registry, project_id=project_id), d_unit),
                        _fmt_num(num), tok, DIM_LABEL.get(d_txt, d_txt))))

    # --- 禁用词 ---
    for w in find_banned(span + " " + basis):
        out.append(("WARN", "DRAFTER-BAN", "%s 用了伪闭环词「%s」" % (where, w)))

    # --- 签名（绘图员负责算量的落点）---
    if not str(row.get("drafter") or "").strip():
        out.append(("FAIL", "DRAFTER-SIGN", "%s 缺绘图员签名 — 读数无人担责" % where))
    return out


def cmd_check(a):
    rows, err = _read_rows(a.file)
    if err:
        print("[ERR] %s" % err)
        return 2
    findings = []
    if not rows:
        findings.append(("FAIL", "DRAFTER-EMPTY",
                         "台账为空 — 没有一条落账读数，绘图员未履行读数责任"))
    ids = set()
    for i, row in enumerate(rows, start=2):
        rid = str(row.get("reading_id") or "").strip()
        if rid in ids:
            findings.append(("FAIL", "DRAFTER-ID", "第%d行 reading_id=%s 重复" % (i, rid)))
        ids.add(rid)
        findings += check_row(i, row,
                              unit_registry=getattr(a, "unit_registry", "") or None,
                              project_id=getattr(a, "project_id", "") or None)

    nf = sum(1 for s, _, _ in findings if s == "FAIL")
    nw = sum(1 for s, _, _ in findings if s == "WARN")
    print("=" * 78)
    print("绘图员读数台账自检  %s" % os.path.basename(a.file))
    print("=" * 78)
    for s, code, msg in findings:
        print("[%s] %-12s %s" % (s, code, msg))
    if not findings:
        print("[OK] 无问题")
    print("-" * 78)
    print("读数=%d  FAIL=%d  WARN=%d" % (len(rows), nf, nw))
    from collections import Counter
    # v4.6.0：旧写法归一化后再统计，否则历史台账全部显示成 "?"（看不出构成）
    c = Counter(METHOD_ALIAS.get(str(r.get("method") or "?"), str(r.get("method") or "?"))
                for r in rows)
    print("取证方法构成: " + ", ".join(
        "%s(%s)=%d" % (k, METHODS.get(k, ("?", "?", "?"))[1], v)
        for k, v in sorted(c.items())))
    # 低置信行数 = 复核队列长度，不是正确率。交付时须报出来。
    low_conf = sum(v for k, v in c.items()
                   if k in WEAK_METHODS or METHODS.get(k, ("", "?", ""))[1] == "weak")
    print("低置信行（复核队列）: %d / %d" % (low_conf, len(rows)))
    e = Counter(str(r.get("evidence_word") or "?") for r in rows)
    print("证据强度构成: " + ", ".join("%s=%d" % kv for kv in sorted(e.items())))
    if a.json:
        _dump_json(a.json, {
            "tool": "drafter_qto.check", "version": "2.0.0",
            "file": a.file, "rows": len(rows), "fail": nf, "warn": nw,
            "findings": [{"level": s, "code": c, "msg": m} for s, c, m in findings],
        })
    return 1 if nf else 0


# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 开项清单（catalog）—— 现阶段道桥类沉淀，供漏项复核
#
# ★★ 单一事实来源：assets/catalog/<name>.csv ★★
#   脚本不自带清单，一律从 CSV 读。
#   文档 references/road-bridge-openitems.md 引用同一套编号并提供详细说明。
#   两侧若漂移，用 `drafter_qto.py verify` 比对。
#
# ★ 编号是**模板编号，与具体项目的 CBS 编码无关**。
#   格式 {分部}-{层}-{序号}，如 A-2-3 = 桥梁(A) 下部第 2 层第 3 项。
#   项目落地时：台账 template_id 列抄模板编号，
#               cbs_code 列登记该项目自己的 CBS 编码，两者并排。
# --------------------------------------------------------------------------
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATALOG_DIR = os.path.join(SKILL_DIR, "assets", "catalog")
# 每个 catalog 的配套说明文档（verify 时用它反查编号是否漂移）
DOC_OF_CATALOG = {
    "road-bridge": os.path.join(SKILL_DIR, "references",
                                "road-bridge-openitems.md"),
    # v4.0.0：恢复房建开项清单（原 v3.x 丢失 building-unit.csv）
    "building-unit": os.path.join(SKILL_DIR, "references",
                                  "building-quantity-practice.md"),
}
# v4.0.0：不同 catalog 的编号格式不同，必须按 catalog 各自取号。
# 房建用 B-01 式短号，道桥用 A-1-1 式三段号；用同一个正则会串号——
# 房建文档里的 "B-01" 会被道桥 catalog 当成"文档引用了不存在的编号"。
CATALOG_ID_RE = {
    "road-bridge": re.compile(r"\b([A-FX]-\d+-\d+[a-z]?)\b"),
    "building-unit": re.compile(r"\b(B-\d{1,3}[A-Z]?)\b"),
}


def load_catalogs():
    """从 assets/catalog/*.csv 加载开项清单。

    返回 {catalog_name: [(template_id, item_name, drawing_evidence), ...]}
    CSV 列：template_id, part, layer, item_name, drawing_evidence, note
    """
    cats = {}
    if not os.path.isdir(CATALOG_DIR):
        return cats
    for fn in sorted(os.listdir(CATALOG_DIR)):
        if not fn.lower().endswith(".csv"):
            continue
        name = os.path.splitext(fn)[0]
        rows = []
        with io.open(os.path.join(CATALOG_DIR, fn), encoding="utf-8-sig") as fp:
            for rec in csv.DictReader(fp):
                tid = str(rec.get("template_id") or "").strip()
                if not tid:
                    continue
                rows.append((tid,
                             str(rec.get("item_name") or "").strip(),
                             str(rec.get("drawing_evidence") or "").strip()))
        if rows:
            cats[name] = rows
    return cats


CATALOGS = load_catalogs()


def cmd_coverage(a):
    rows, err = _read_rows(a.file)
    if err:
        print("[ERR] %s" % err)
        return 2
    catalog = CATALOGS.get(a.catalog)
    if not catalog:
        print("[FAIL] 未知 catalog=%s，可用: %s" % (a.catalog, "/".join(CATALOGS)))
        return 2
    # 三态登记（v2.1.0）：
    #   已计量 = 命中模板编号且该行有值
    #   已登记未计量 = 命中编号但值为 0/空/敞口（"本项不设"也要留痕）
    #   未登记 = 编号与名称都没命中
    # 此前只把任意非空 cbs_code 塞进 have，一行没量的空壳也算"已覆盖"。
    tids, valued, codes, names = set(), set(), set(), []
    for r in rows:
        t = str(r.get("template_id") or "").strip()
        c = str(r.get("cbs_code") or "").strip()
        nm = str(r.get("item_name") or "").strip()
        v = str(r.get("value") or "").strip()
        has_val = v not in ("", "0", "0.0", "0.0000", "敞口", "—", "-")
        if t:
            tids.add(t)
            if has_val:
                valued.add(t)
        if c:
            codes.add(c)
        if nm:
            names.append((nm, has_val))

    def _core(s):
        return re.split(r"[（(]", s)[0].strip()

    counted, registered_only, miss, by_name = [], [], [], []
    for code, name, src in catalog:
        if code in valued:
            counted.append((code, name))
        elif code in tids:
            registered_only.append((code, name))
        elif any(_core(name) and _core(name) in nm for nm, _ in names):
            by_name.append((code, name))
        else:
            miss.append((code, name, src))

    print("=" * 78)
    print("开项覆盖检查  catalog=%s  台账行=%d  清单条目=%d"
          % (a.catalog, len(rows), len(catalog)))
    print("=" * 78)
    if by_name:
        print("[WARN] 以下 %d 项按**名称**配对命中，建议补 template_id："
              % len(by_name))
        for code, name in by_name:
            print("  %-16s %s" % (code, name))
    if registered_only:
        print("[WARN] 已登记但未计量 %d 项（值为 0/空/敞口，须说明「本项不设」或补量）："
              % len(registered_only))
        for code, name in registered_only:
            print("  %-16s %s" % (code, name))
    if miss:
        print("[MISS] 未见登记 %d 项：" % len(miss))
        for code, name, src in miss:
            print("  %-16s %-34s (图证: %s)" % (code, name, src))
    unknown = sorted(t for t in tids if t not in {c[0] for c in catalog})
    if unknown:
        print("[WARN] 台账里出现清单外的模板编号：%s" % " / ".join(unknown))
    print("-" * 78)
    hit = len(counted) + len(by_name)
    ratio = float(hit) / len(catalog) if catalog else 0.0
    print("覆盖率 = %d/%d = %.1f%%（已计量 %d + 按名称 %d）"
          % (hit, len(catalog), ratio * 100, len(counted), len(by_name)))
    if a.json:
        _dump_json(a.json, {
            "tool": "drafter_qto.coverage", "version": "3.1.0",
            "catalog": a.catalog, "rows": len(rows),
            "catalog_items": len(catalog),
            "counted": [c for c, _ in counted],
            "registered_not_measured": [c for c, _ in registered_only],
            "by_name": [c for c, _ in by_name],
            "missing": [{"code": c, "name": n, "source": s} for c, n, s in miss],
            "unknown_template_ids": unknown,
            "ratio": round(ratio, 4),
        })
    if a.fail_under is not None and ratio < a.fail_under:
        print("[FAIL] 覆盖率 %.1f%% 低于阈值 %.1f%%"
              % (ratio * 100, a.fail_under * 100))
        return 1
    return 0


# --------------------------------------------------------------------------
TID_RE = re.compile(r"\b([A-FX]-\d+-\d+[a-z]?)\b")


def cmd_verify(a):
    """同步校验：开项清单 CSV 与配套文档里的编号必须一致。

    为什么需要：曾经出现脚本自带清单与文档清单各自演化（伸缩缝一个在
    A-3-6、一个在 A-6-3，项数也不等），结果漏项比对**按错误编号配对**，
    该报的漏项没报、不该报的报了一堆。两侧同源必须机器盯。
    """
    bad = 0
    for name, rows in sorted(CATALOGS.items()):
        doc = DOC_OF_CATALOG.get(name)
        print("=" * 78)
        print("catalog=%s  条目=%d" % (name, len(rows)))
        print("=" * 78)
        if not doc or not os.path.exists(doc):
            print("[FAIL] 未登记配套文档 — 编号无出处，无法核对")
            bad += 1
            continue
        with io.open(doc, encoding="utf-8") as stream:
            txt = stream.read()
        id_re = CATALOG_ID_RE.get(name, TID_RE)
        in_doc = set(id_re.findall(txt))
        in_csv = set(t for t, _, _ in rows)
        orphan = sorted(in_doc - in_csv)     # 文档引用了 CSV 里没有的编号
        silent = sorted(in_csv - in_doc)     # CSV 里有但文档没说明
        if orphan:
            bad += len(orphan)
            print("[FAIL] 文档引用了清单里不存在的编号（已废弃/写错）：")
            for t in orphan:
                print("   %s" % t)
        else:
            print("[OK] 文档编号 ⊆ 清单编号")
        if silent:
            bad += len(silent)
            print("[FAIL] 清单里有 %d 项文档未展开说明：" % len(silent))
            for t in silent:
                nm = next((n for i, n, _ in rows if i == t), "")
                print("   %-10s %s" % (t, nm))
        dup = [t for t in in_csv if len(t) and
               sum(1 for x, _, _ in rows if x == t) > 1]
        if dup:
            bad += len(set(dup))
            print("[FAIL] 清单里编号重复：%s" % ", ".join(sorted(set(dup))))
        print("-" * 78)
        print("文档引用=%d  清单条目=%d  未展开=%d"
              % (len(in_doc), len(in_csv), len(silent)))
    print("=" * 78)
    print("结论: %s" % ("PASS" if bad == 0 else "FAIL (%d 处漂移)" % bad))
    return 1 if bad else 0


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="drafter_qto",
        description="绘图员负责算量（Drafter-owns-QTO）机制的落账与自检工具")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("new", help="新建读数台账")
    p.add_argument("--out", required=True)
    p.add_argument("--package", default="ALL", help="分部名（桥梁/涵洞/照明…）")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("add", help="登记一条读数")
    p.add_argument("--file", required=True)
    p.add_argument("--id", dest="id", default="")
    p.add_argument("--template", default="", choices=[""] + _all_template_ids(),
                   help="开项清单模板编号，如 A-2-3（项目无关；取不到编号就留空，"
                        "coverage 会按名称兜底配对）")
    p.add_argument("--code", default="", help="项目自己的 CBS 编码")
    p.add_argument("--item", default="")
    p.add_argument("--sheet", default="")
    p.add_argument("--location", default="", help="图面位置（须可复现：坐标或具名图元）")
    p.add_argument("--direction", default="", choices=[""] + list(DIRECTIONS),
                   help="方向或维度——本项目头号事故（纵向/横向）的唯一落点")
    p.add_argument("--object", dest="object", default="", help="所属构件（禁止泛词）")
    p.add_argument("--attr", default="", help="属性（须具体到哪个尺寸/数量）")
    p.add_argument("--method", required=True,
                   choices=sorted(METHODS) + sorted(METHOD_ALIAS),
                   help="取证方法（六档）；旧写法 render_read 会被归一化为 render_read_est 并告警")
    p.add_argument("--span", default="", choices=[""] + list(SPAN_WORDS),
                   help="范围口径词（全桥/单幅/两侧/每座…）")
    p.add_argument("--evidence", default="",
                   choices=[""] + list(EVIDENCE_WORDS),
                   help="证据强度词（图纸确认/图读/推算/推定/估算/待核/敞口）")
    p.add_argument("--value", default="")
    p.add_argument("--unit", default="", help="单位符号（M3 / m³ / U / 根…；歧义别名必须显式批准）")
    p.add_argument("--unit-registry", default="", help="项目单位别名注册表 JSON（仅使用已批准的映射）")
    p.add_argument("--project-id", default="", help="校验单位别名适用范围的项目编号")
    p.add_argument("--formula", default="", help="可复算的计算式")
    p.add_argument("--source", default="",
                   help="证据去处：RFI 编号 / 台账编号 / 业主清单编号 / 规范条款号 / 图号")
    p.add_argument("--drafter", default="", help="绘图员签名（必填）")
    p.add_argument("--date", default=_today(),
                   help="落账日期 YYYY-MM-DD（默认今天；不要手敲未来日期）")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("check", help="台账自检")
    p.add_argument("--file", required=True)
    p.add_argument("--json", default="")
    p.add_argument("--unit-registry", default="", help="项目单位别名注册表 JSON（仅使用已批准的映射）")
    p.add_argument("--project-id", default="", help="校验单位别名适用范围的项目编号")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("coverage", help="与开项清单比对报漏项")
    p.add_argument("--file", required=True)
    p.add_argument("--catalog", default="road-bridge", choices=sorted(CATALOGS))
    p.add_argument("--json", default="")
    p.add_argument("--fail-under", type=float, default=None,
                   help="覆盖率低于该阈值即 exit 1（当门禁用；0~1，如 0.8）")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("verify", help="开项清单 CSV 与配套文档的编号同步校验")
    p.set_defaults(func=cmd_verify)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    # 退出码语义统一：0 通过 / 1 检查不通过 / 2 工具自身无法运行。
    # 裸 traceback 退 1 会被当成"检查不通过"，调用方分不清是数据错还是工具坏。
    try:
        return a.func(a)
    except Exception as e:                      # noqa: BLE001
        sys.stderr.write("[ERR] 工具执行失败（exit 2，不是检查不通过）：%s: %s\n"
                         % (type(e).__name__, e))
        return 2


if __name__ == "__main__":
    sys.exit(main())
