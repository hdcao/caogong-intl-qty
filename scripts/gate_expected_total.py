#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
期望总额闸门（2.1.3 · G-XST）

**它补的洞：闭环靠人记，没有闸门**
2026-10-24 对比 openclaude 单遍算量时，最有说服力的证据不是逐项尺寸，
而是**业主清单四个总数我方 4/4 闭合、它一项都对不上**：

    业主 6.05 = 130 U    → 我方 130（10 片/跨 × 13 跨）
    业主 6.36 = 3718 m³  → 我方 3718.5（梁板 3403.50 + 横隔梁 315）
    业主 6.25 = 30 m³    → 我方 30（支座垫石）
    业主 6.27 = 11.1 t   → 我方 11.1（支座垫石钢筋）

那次闭环是**人拿计算器对的**。人对一遍，下一轮改表后就断了——
本门禁把这件事变成每次跑都做的数值判据。

**机制来源**：pk-boq-compare 的 `--expect-total`（匹配 JSON 只算一次，
差额在容差内才认）。借机制，不借代码——他们的脚本与 CostSpread
项目上下文绑死，照搬等于把坑搬过来。

**五类判据**
| ID   | 判据                                                   | 级别        |
|------|--------------------------------------------------------|-------------|
| XST1 | 期望项在表中一行都没匹配到（漏项，或 match 写错）         | FAIL        |
| XST2 | 合计与期望值差超容差                                    | FAIL        |
| XST3 | 匹配行单位与期望单位不一致，且未显式声明换算              | FAIL        |
| XST4 | 匹配集内名称存在包含关系（父条目/子条目同单位，疑似重复计） | WARN，可 ack |
| XST5 | 已按人工声明的 convert 因子换算                          | INFO        |

**单位换算硬约束**：期望值与实算值**同单位才相比**。跨单位必须写
`convert: {"factor": ..., "note": ...}`——由人声明、门禁只执行不验证。
静默换正是把「量纲错」洗成「小数点错」的标准动作，本门禁不给它留路。

**期望值文件由人填**：`expectations` 是人工声明（业主 BOQ / 招标文件 /
指标报告），AI 不代填。填错 match 的后果是 XST1 假红，
输出会指明是哪一项匹配到 0 行——假红看得见，比漏报好。
`match` 可以是字符串，也可以是字符串数组（业主把几项合并成一个总数时用，
如 `["预应力梁板", "横隔梁"]` 对 3718 m³）。

退出码：0 = 全部落在容差内；1 = 有期望项对不上；2 = 门禁无法运行
（无期望项 / 0 数据行 / 文件、页签、JSON 有问题 / 期望项配置坏未执行——
配置坏是「没有可执行判据」，记 2 不记 1）。
"""
import argparse
import io
import json
import os
import sys
import math
from decimal import Decimal, InvalidOperation

# --- 控制台编码兜底（v2.1.0）-------------------------------------------
# 中文 Windows 控制台默认 cp936，无法编码 ✓/✗/★ 等符号：一条 INFO 输出
# 就会抛 UnicodeEncodeError，让**干净的表**被判成"检查不通过"。这里保留
# 控制台编码，只把不可编码字符替换掉——不换编码，避免中文变乱码。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
# ------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from ack_util import AckLedger, add_ack_arg
except Exception:                                     # 单文件分发时降级
    AckLedger, add_ack_arg = None, None

try:
    from qto_vocab import norm_unit
except ImportError:                                   # 单文件分发时降级
    _SUP = str.maketrans({"³": "3", "²": "2", "¹": "1"})

    def norm_unit(raw):
        s = str(raw or "").strip().translate(_SUP)
        for sep in ("（", "(", "[", "【"):
            i = s.find(sep)
            if i > 0:
                s = s[:i]
        return s.replace(" ", "").replace(".", "").upper()

DEFAULT_SHEET = "1_成本取量总表"
SUBTOTAL_WORDS = {"合计", "小计", "总计", "共计", "合价", "总价", "汇总"}
PLACEHOLDER_UNIT = {"—", "——", "-", "–", "/", "N/A", "NA", "无", "不适用"}


# --------------------------------------------------------------------------
def _load_expect(path):
    """读期望值文件。返回 (spec, err)。err 非空即未运行（exit 2）。"""
    if not os.path.exists(path):
        return None, "期望值文件不存在：%s" % path
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            spec = json.load(fh)
    except Exception as e:
        return None, "期望值文件不是合法 JSON：%s" % e
    if not isinstance(spec, dict):
        return None, "期望值文件顶层必须是对象（含 expectations 数组）"
    return spec, None


def _collect_rows(ws, name_col, qty_col, unit_col):
    """取有效量行：名称非空 + 工程量为数值。小计行、占位单位行不计。

    ★小计行按**子串**匹配（v2.1.2 P2-7）：「承台合计」这类带前缀/后缀的
    合计行以前会用精确匹配漏掉，被当成数据行重复计入期望总额。
    """
    rows = []
    invalid_numeric_rows = []
    for r in range(3, ws.max_row + 1):
        nm = str(ws.cell(r, name_col).value or "").strip()
        q = ws.cell(r, qty_col).value
        u = str(ws.cell(r, unit_col).value or "").strip()
        if not nm or nm.startswith("★") or nm in SUBTOTAL_WORDS \
                or any(w in nm for w in SUBTOTAL_WORDS):
            continue
        if isinstance(q, bool):
            invalid_numeric_rows.append((r, nm, repr(q), "boolean quantity"))
            continue
        if isinstance(q, (int, float)) and not math.isfinite(float(q)):
            invalid_numeric_rows.append((r, nm, repr(q), "non-finite quantity"))
            continue
        if not isinstance(q, (int, float)):
            continue
        if u in PLACEHOLDER_UNIT:
            continue
        rows.append((r, nm, Decimal(str(q)), u))
    return rows, invalid_numeric_rows


def _containment_pairs(items):
    """items = [(row, name)]；返回名称互相包含的行对（父/子条目嫌疑）。"""
    pairs = []
    for i, (r1, n1) in enumerate(items):
        for r2, n2 in items[i + 1:]:
            if n1 in n2 or n2 in n1:
                pairs.append((r1, n1, r2, n2))
    return pairs


def run(argv=None):
    ap = argparse.ArgumentParser(
        prog="gate_expected_total",
        description="期望总额闸门 G-XST：把「业主清单闭环」变成每次必跑的数值判据")
    ap.add_argument("xlsx", help="算量工作簿 (.xlsx)")
    ap.add_argument("--expect", required=True,
                    help="期望值 JSON（人工声明，AI 不代填）")
    ap.add_argument("--sheet", default=DEFAULT_SHEET)
    ap.add_argument("--name-col", type=int, default=3)
    ap.add_argument("--qty-col", type=int, default=5)
    ap.add_argument("--unit-col", type=int, default=4)
    ap.add_argument("--tol", type=float, default=None,
                    help="相对容差（如 0.01 = 1%%），覆盖 JSON 里的 tolerance")
    if add_ack_arg:
        add_ack_arg(ap)
    a = ap.parse_args(argv)

    # ---- 前置失败一律 exit 2（与 G-UNIT / G-U0b 同语义）------------------
    spec, err = _load_expect(a.expect)
    if err:
        print("[未执行] %s" % err)
        print("[未执行] 退出码 2（门禁无法运行）——这不是「检查不通过」，也不是「通过」。")
        return 2
    exps = spec.get("expectations") or []
    if not isinstance(exps, list) or not exps:
        print("[未执行] 期望值文件里没有 expectations——**没有分母**，"
              "本门禁未形成任何结论。")
        print("          填一份人工声明的期望值再跑；空期望值不是「通过」。")
        print("[未执行] 退出码 2（门禁无法运行）——这不是「检查不通过」，也不是「通过」。")
        return 2

    if not os.path.exists(a.xlsx):
        print("[未执行] G-XST0 表文件不存在：%s" % a.xlsx)
        print("[未执行] 退出码 2（门禁无法运行）——这不是「检查不通过」，也不是「通过」。")
        return 2
    try:
        import openpyxl
        wb = openpyxl.load_workbook(a.xlsx, data_only=False)
    except Exception as e:
        print("[未执行] G-XST0 无法打开表：%s" % e)
        print("[未执行] 退出码 2（门禁无法运行）——这不是「检查不通过」，也不是「通过」。")
        return 2
    if a.sheet not in wb.sheetnames:
        print("[未执行] G-XST0 没有页签「%s」（现有：%s）" % (a.sheet, wb.sheetnames))
        print("[未执行] 退出码 2（门禁无法运行）——这不是「检查不通过」，也不是「通过」。")
        return 2
    ws = wb[a.sheet]

    rows, invalid_numeric_rows = _collect_rows(ws, a.name_col, a.qty_col, a.unit_col)
    if invalid_numeric_rows:
        print("[FAIL] G-XST-NUM 发现非有限/布尔工程量，禁止静默跳过：")
        for rr, name, raw, reason in invalid_numeric_rows[:20]:
            print("  r%d %s = %s (%s)" % (rr, name, raw, reason))
        wb.close()
        return 1
    print("期望总额闸门 G-XST | 表：%s | 页签：%s | 有效量行 %d"
          % (os.path.basename(a.xlsx), a.sheet, len(rows)))
    src = str(spec.get("source") or "").strip()
    print("期望值来源：%s" % (src or "（未写来源——期望值文件应注明出处）"))
    if not rows:
        print("[未执行] 扫描到 0 行有效量行（名称非空 + 工程量为数值）。")
        print("          没有任何数据行时，「通过」只意味着没东西可查。")
        print("          先核对 --sheet 是否指对了表、列序是否与预期一致。")
        print("[未执行] 退出码 2（门禁无法运行）——这不是「检查不通过」，也不是「通过」。")
        return 2

    try:
        default_tol = Decimal(str(spec.get("tolerance", 0.01)))
        if not default_tol.is_finite() or default_tol < 0:
            raise ValueError("tolerance must be finite and >= 0")
        if a.tol is not None:
            default_tol = Decimal(str(a.tol))
        if not default_tol.is_finite() or default_tol < 0:
            raise ValueError("--tol must be finite and >= 0")
    except (InvalidOperation, ValueError, TypeError) as exc:
        print("[未执行] 容差配置无效：%s" % exc)
        return 2

    ack = None
    if AckLedger is not None and getattr(a, "ack", None):
        ack = AckLedger(a.ack)

    table_name = os.path.basename(a.xlsx)
    fails, warns, bad_cfg = [], [], []
    print("-" * 74)

    for e in exps:
        mid = str(e.get("id") or "").strip() or "(未编号)"
        raw_match = e.get("match")
        if isinstance(raw_match, (list, tuple)):
            matches = [str(x).strip() for x in raw_match if str(x).strip()]
        else:
            matches = [str(raw_match or "").strip()]
        matches = [m for m in matches if m]
        match_desc = matches[0] if len(matches) == 1 else "｜".join(matches)
        unit = str(e.get("unit") or "").strip()
        expect = e.get("expect")
        note = str(e.get("note") or "").strip()
        try:
            expect_decimal = Decimal(str(expect))
            valid_expect = expect_decimal.is_finite()
        except (InvalidOperation, ValueError, TypeError):
            expect_decimal, valid_expect = Decimal(0), False
        if not matches or not unit or not valid_expect:
            # ★配置坏 ≠ 检查不通过（v2.1.2 P2-4）：这一项没有可执行的判据，
            #   门禁对它未形成任何意见。记入 bad_cfg——汇总里 fails 优先判 1，
            #   fails 空而 bad_cfg>0 判 2（未运行），不是「对不上」。
            bad_cfg.append(mid)
            print("[未执行] G-XST0 %s：期望项缺 match / unit / expect（数值），"
                  "这一项没有可执行的判据（不是「对不上」）。" % mid)
            continue
        try:
            tol = Decimal(str(e.get("tolerance", default_tol)))
            if not tol.is_finite() or tol < 0:
                raise ValueError("tolerance must be finite and >= 0")
        except (InvalidOperation, ValueError, TypeError) as exc:
            bad_cfg.append(mid)
            print("[未执行] G-XST0 %s：容差配置无效：%s" % (mid, exc))
            continue

        by_name = [x for x in rows
                   if any(m.lower() in x[1].lower() for m in matches)]
        if not by_name:
            fails.append(mid)
            print("[FAIL] G-XST1 %s「%s」：表内**没有一行**名称含 %r%s"
                  % (mid, match_desc, match_desc,
                     "（%s）" % note if note else ""))
            print("       要么这项真漏了，要么 match 写错——"
                  "输出指到具体项，假红漏报都看得见。")
            continue

        same = [x for x in by_name if norm_unit(x[3]) == norm_unit(unit)]
        used, converted = same, False
        if not same:
            conv = e.get("convert") or {}
            factor = conv.get("factor")
            try:
                factor_value = Decimal(str(factor))
                factor_ok = factor_value.is_finite() and factor_value > 0
            except (InvalidOperation, ValueError, TypeError):
                factor_ok = False
            if factor_ok:
                used = by_name
                converted = True
                print("[INFO] G-XST5 %s：匹配行单位 %s 与期望单位 %s 不一致，"
                      "已按人工声明因子 ×%s 换算（声明来源：%s）。"
                      % (mid, "/".join(sorted(set(x[3] for x in by_name))),
                         unit, factor,
                         str(conv.get("note") or conv.get("source") or "未写")))
                print("       门禁只执行这个因子，**不验证它对不对**——"
                      "密度/厚度这类换算的正确性由声明人负责。")
            else:
                fails.append(mid)
                seen = "/".join(sorted(set(x[3] for x in by_name)))
                print("[FAIL] G-XST3 %s「%s」：期望单位 %s，但匹配到的 %d 行单位是 %s。"
                      % (mid, match_desc, unit, len(by_name), seen))
                print("       **禁止静默换算**——如确需换算，在期望值 JSON 里显式写 "
                      "convert.factor 与来源；否则先查是不是量纲记错。")
                continue

        total = sum((x[2] for x in used), Decimal(0))
        if converted:
            try:
                factor_decimal = Decimal(str(e["convert"]["factor"]))
                if not factor_decimal.is_finite() or factor_decimal <= 0:
                    raise ValueError("convert.factor must be finite and > 0")
                total = total * factor_decimal
            except (InvalidOperation, ValueError, TypeError) as exc:
                bad_cfg.append(mid)
                print("[未执行] G-XST0 %s：换算因子无效：%s" % (mid, exc))
                continue

        # 父子条目包含关系：同单位的父子同时进匹配集，合计会重复计
        pairs = _containment_pairs([(x[0], x[1]) for x in used])
        open_pairs = []
        for r1, n1, r2, n2 in pairs:
            # ★指纹带行对身份（v2.1.2 P2-5）：一个期望项下有多对父子时，
            #   只凭 rows+sum 分不清是哪一对——表头前插一行导致行号错位、
            #   而 sum 恰好不变时，旧指纹会命中、ack 被静默续期。
            #   行对进指纹后，动了行就一定 STALE。
            fingerprint = "rows=%d|sum=%.4f|pair=%d+%d" % (
                len(used), total, r1, r2)
            status, why = ("", "")
            if ack is not None:
                # ack 键同样带行对：一对父子一条，各自独立，互不压制
                status, why = ack.hit("G-XST", table_name, a.sheet,
                                      "expect:%s:%d+%d" % (mid, r1, r2),
                                      fingerprint)
            if status == "ACK":
                print("[ACK]  G-XST4 %s r%d「%s」与 r%d「%s」包含关系已人工确认：%s"
                      % (mid, r1, n1, r2, n2, why))
            else:
                open_pairs.append((r1, n1, r2, n2, why))
        if open_pairs:
            warns.append(mid)
            for r1, n1, r2, n2, why in open_pairs:
                print("[WARN] G-XST4 %s：r%d「%s」与 r%d「%s」名称互相包含，"
                      "同单位同时计入会**重复计**。" % (mid, r1, n1, r2, n2))
                if why:
                    print("       %s" % why)
            # ★指引打印**真实可用**的键（v2.1.2 P1-2）：从 open_pairs 取第一对
            #   现场生成，不写死——写死的例子对不上台账时，人会照着抄一个
            #   永远命不中的键。键格式：expect:<期望项id>:<父行号>+<子行号>。
            ex_r1, _, ex_r2, _, _ = open_pairs[0]
            print("       确认父子口径不重叠后可写 ack 台账：gate=G-XST，"
                  "row=expect:%s:%d+%d" % (mid, ex_r1, ex_r2))
            print("       键格式 expect:<期望项id>:<父行号>+<子行号>；"
                  "一对父子一条，多对父子各写各的（ack 一对不影响另一对）；"
                  "或把 match 写精确。")

        if expect_decimal == 0:
            ok = (total == 0)
            rate = Decimal(0) if ok else None
        else:
            rate = (total - expect_decimal) / expect_decimal
            ok = abs(rate) <= tol
        tag = "PASS" if ok else "FAIL"
        rate_pct = (float(rate * 100) if rate is not None else float("inf"))
        line = ("[%s] %s「%s」%s 期望 %s 实算 %s 差 %+.2f%%（容差 %.1f%%，匹配 %d 行）"
                % (tag, mid, match_desc, unit, expect_decimal, round(total, 4),
                   rate_pct, float(tol * 100), len(used)))
        print(line)
        if note:
            print("       备注：%s" % note)
        if not ok:
            fails.append(mid)
            print("       匹配行如下——差超容差时先看它们是漏项还是 match 太宽：")
            for r, nm, q, u in used:
                print("         r%-4d %-28s %-6s %s" % (r, nm, u, q))

    print("-" * 74)
    n_fail = len(set(fails))
    n_warn = len(set(warns))
    n_bad = len(set(bad_cfg))
    print("已扫 %d 项期望：FAIL %d / WARN 未清 %d"
          % (len(exps), n_fail, n_warn))
    if n_bad:
        print("       其中 %d 项配置坏未执行（缺 match / unit / expect 数值）——"
              "门禁对它们未形成任何意见。" % n_bad)
    if ack is not None:
        for ln in ack.report(table=table_name, gates=["G-XST"]):
            print(ln)
    if n_fail:
        print("[FAIL] 退出码 1——这是「检查不通过」：有期望项对不上。")
        return 1
    if n_bad:
        print("[未执行] 退出码 2（门禁无法运行）——%d 项期望配置坏未执行，"
              "「没有分母」的项不构成通过。" % n_bad)
        return 2
    print("[PASS] 全部期望项落在容差内。")
    return 0


# --------------------------------------------------------------------------
def _selftest(a):
    """自证：造 fixture 跑 7 个场景，每个场景的退出码都必须符合预期。

    ★不注入就不算有这道门禁（gate-library「新增门禁时的要求」第 3 条）。
    """
    import shutil
    import tempfile
    try:
        import openpyxl
    except ImportError:
        print("[未执行] 缺 openpyxl，无法自证")
        return 2

    tmp = tempfile.mkdtemp(prefix="gxst_selftest_")
    try:
        book = os.path.join(tmp, "fixture.xlsx")

        def build(data):
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = DEFAULT_SHEET
            ws.cell(1, 1, "编码")
            ws.cell(1, 3, "名称")
            ws.cell(1, 4, "单位")
            ws.cell(1, 5, "工程量")
            for i, (nm, u, q) in enumerate(data, 3):
                ws.cell(i, 3, nm)
                ws.cell(i, 4, u)
                ws.cell(i, 5, q)
            wb.save(book)

        data = [
            ("箱梁边跨", "CM", 1701.75),
            ("箱梁中跨", "CM", 1701.75),
            ("箱梁横隔梁", "CM", 315.00),
            ("梁板片数", "EA", 100),
            ("支座垫石", "CM", 30.00),
            ("支座垫石钢筋", "T", 11.10),
            ("承台", "CM", 14582.00),
            ("承台垫层", "CM", 383.20),
        ]
        build(data)

        def spec_of(exps, tol=0.01, source="selftest fixture（人工声明）"):
            p = os.path.join(tmp, "exp_%d.json" % spec_of.n)
            spec_of.n += 1
            with io.open(p, "w", encoding="utf-8") as fh:
                json.dump({"source": source, "tolerance": tol,
                           "expectations": exps}, fh, ensure_ascii=False)
            return p
        spec_of.n = 0

        def go(name, exps, want, data=None, tol=0.01, sheet=DEFAULT_SHEET,
               book_path=None):
            if data is not None:
                build(data)
            p = spec_of(exps, tol)
            print("\n===== 场景 %s（期望退出码 %d）=====" % (name, want))
            got = run([book_path or book, "--expect", p, "--sheet", sheet])
            print("---- 实得退出码 %d → %s" % (got, "符合" if got == want else "不符合"))
            return got == want

        ok = True
        # S1 全中：4 项闭合，另有一条 G-XST4 WARN（承台 ⊂ 承台垫层，同单位）
        ok &= go("S1 全中+父子WARN", [
            {"id": "T1", "match": "箱梁", "unit": "CM", "expect": 3718.5,
             "note": "业主 6.36 反算：梁板 3403.50 + 横隔梁 315"},
            {"id": "T2", "match": "支座垫石", "unit": "CM", "expect": 30.0},
            {"id": "T3", "match": "支座垫石钢筋", "unit": "T", "expect": 11.1},
            {"id": "T4", "match": "承台", "unit": "CM", "expect": 14965.2},
        ], 0)
        # S2 差超容差：130 vs 实算 100
        ok &= go("S2 差超容差", [
            {"id": "T1", "match": "箱梁", "unit": "CM", "expect": 3718.5},
            {"id": "T5", "match": "梁板片数", "unit": "EA", "expect": 130},
        ], 1)
        # S3 期望项一行都没匹配到
        ok &= go("S3 漏匹配", [
            {"id": "T1", "match": "箱梁", "unit": "CM", "expect": 3718.5},
            {"id": "T7", "match": "桥面排水", "unit": "EA", "expect": 24},
        ], 1)
        # S4 单位不一致且未声明换算
        ok &= go("S4 单位不一致", [
            {"id": "T1", "match": "箱梁", "unit": "CM", "expect": 3718.5},
            {"id": "T6", "match": "箱梁", "unit": "SM", "expect": 10},
        ], 1)
        # S5 单位不一致但已声明换算：×0.1 → 371.85，落在容差内
        ok &= go("S5 声明换算后通过", [
            {"id": "T1", "match": "箱梁", "unit": "CM", "expect": 3718.5},
            {"id": "T6", "match": "箱梁", "unit": "SM", "expect": 371.85,
             "convert": {"factor": 0.1, "note": "selftest 演示：CM→SM 需厚度，此处仅为执行路径验证"}},
        ], 0)
        # S6 空表（只有表头）→ 未运行
        ok &= go("S6 空表", [
            {"id": "T1", "match": "箱梁", "unit": "CM", "expect": 3718.5},
        ], 2, data=[])
        # S7 期望值文件为空 → 未运行
        ok &= go("S7 无期望值", [], 2)

        print("\n" + "=" * 74)
        n_scn = 7
        print("结论: %s" % ("PASS（%d/%d 场景退出码全部符合预期）" % (n_scn, n_scn)
                            if ok else "FAIL（有场景退出码不符合预期）"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main2(argv=None):
    """支持子命令：run（默认）与 selftest。"""
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "selftest":
        ap = argparse.ArgumentParser(prog="gate_expected_total selftest")
        return _selftest(ap.parse_args(argv[1:]))
    return run(argv)


if __name__ == "__main__":
    sys.exit(main2())
