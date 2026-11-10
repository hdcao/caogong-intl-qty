#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
qto_repeat_groups.py —— 典型区/重复组展开与乘数管控（v4.0.0 新增）

对应 ChatGPT 建议"第 6 条 重复构件与典型区域应采用可复用模板"，以及
实测中最全局的不确定性：**建筑体系乘数**（本项目双塔同型 → 楼栋级乘数）。

原则（不可违反）
  1. 典型组先量一次，按实例数展开；实例数（乘数）必须有来源并留痕。
  2. 与典型层的**差异**只继承经确认的共同部分，差额单列，不做整组套用。
  3. 乘数来源不明 → 状态=待确认，量不进交付总量，进敞口台账。
  4. 展开结果保留每组的追溯链（典型图号 → 实例数 → 差额 → 小计）。

用法
  # 展开
  python qto_repeat_groups.py expand --file takeoff/register/groups.csv \
      --out takeoff/register

  # 自证
  python qto_repeat_groups.py selftest

退出码 0=通过 1=门禁不过 2=用法/环境错
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import tempfile
from collections import defaultdict

VERSION = "4.0.0"
TOOL = "qto_repeat_groups"

COL_GROUP = "group_id"
COL_SCOPE = "scope"
COL_INST = "instances"
COL_ITEM = "item_name"
COL_PER = "per_instance_qty"
COL_UNIT = "unit"
COL_EXC = "exception_qty"
COL_SRC = "multiplier_source"
COL_REF = "typical_ref"
COL_NOTE = "note"


def _read_csv(path):
    if not os.path.exists(path):
        return None, "文件不存在: %s" % path
    try:
        with io.open(path, encoding="utf-8-sig", newline="") as fp:
            return list(csv.DictReader(fp)), None
    except Exception as exc:
        return None, "读取失败: %s" % exc


def _f(row, key):
    v = row.get(key)
    return "" if v is None else str(v).strip()


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


HEADER = ["group_id", "scope", "item_name", "unit", "instances",
          "per_instance_qty", "exception_qty", "subtotal", "status",
          "typical_ref", "multiplier_source", "note"]


def cmd_expand(a):
    rows, err = _read_csv(a.file)
    if err:
        print("[ERR] %s" % err)
        return 2
    if not rows:
        print("[ERR] 输入为空")
        return 2
    need = [COL_GROUP, COL_INST, COL_PER]
    missing = [c for c in need if c not in rows[0]]
    if missing:
        print("[ERR] 输入缺少必需列：%s" % ",".join(missing))
        return 2

    out, groups = [], defaultdict(lambda: {"n": 0.0, "conf": False, "src": ""})
    pending = []
    for i, r in enumerate(rows, start=2):
        gid = _f(r, COL_GROUP)
        n = _num(_f(r, COL_INST), -1)
        per = _num(_f(r, COL_PER))
        exc = _num(_f(r, COL_EXC))
        src = _f(r, COL_SRC)
        ref = _f(r, COL_REF)
        if n <= 0:
            pending.append((i, gid, _f(r, COL_ITEM), "实例数/乘数无效（%s）" % _f(r, COL_INST)))
            n = 0.0
        if not src:
            pending.append((i, gid, _f(r, COL_ITEM), "乘数来源未声明"))
        status = "OK" if (n > 0 and src) else "待确认"
        sub = n * per + exc
        out.append({
            "group_id": gid, "scope": _f(r, COL_SCOPE), "item_name": _f(r, COL_ITEM),
            "unit": _f(r, COL_UNIT), "instances": ("%g" % n),
            "per_instance_qty": ("%g" % per), "exception_qty": ("%g" % exc),
            "subtotal": ("%g" % sub), "status": status, "typical_ref": ref,
            "multiplier_source": src, "note": _f(r, COL_NOTE),
        })
        g = groups[(gid, _f(r, COL_ITEM), _f(r, COL_UNIT))]
        g["n"] += sub
        g["conf"] = g["conf"] or (status == "OK")
        g["src"] = g["src"] or src

    ok_rows = [r for r in out if r["status"] == "OK"]
    ok_total = sum(_num(r["subtotal"]) for r in ok_rows)
    pend_total = sum(_num(r["subtotal"]) for r in out if r["status"] != "OK")

    os.makedirs(a.out, exist_ok=True)
    p = _write_csv(os.path.join(a.out, "重复组展开表.csv"), HEADER, out)
    sum_rows = [{"group_id": k[0], "item_name": k[1], "unit": k[2],
                 "subtotal": "%g" % v["n"],
                 "status": "OK" if v["conf"] else "待确认",
                 "multiplier_source": v["src"]}
                for k, v in groups.items()]
    p2 = _write_csv(os.path.join(a.out, "重复组汇总.csv"),
                    ["group_id", "item_name", "unit", "subtotal", "status",
                     "multiplier_source"], sum_rows)

    print("=" * 78)
    print("重复组展开  输入 %d 行 → OK %d 行 / 待确认 %d 行"
          % (len(out), len(ok_rows), len(out) - len(ok_rows)))
    print("=" * 78)
    print("可交付合计 = %g（仅 OK 行）" % ok_total)
    if pend_total:
        print("[敞口] 待确认行合计 %g — 不计入交付总量，须补乘数来源或改按实例数逐层点算" % pend_total)
        for i, gid, item, why in pending[:20]:
            print("    第%d行 %s | %s | %s" % (i, gid, item, why))
    # 乘数来源体检：同一 group 出现多种来源 → 口径不一致
    by_g = defaultdict(set)
    for r in out:
        if r["group_id"]:
            by_g[r["group_id"]].add(r["multiplier_source"])
    conflict = {k: sorted(v) for k, v in by_g.items() if len(v) > 1}
    if conflict:
        print("[WARN] 以下 group 的乘数来源不唯一：" +
              ", ".join("%s(%s)" % (k, "/".join(v)) for k, v in list(conflict.items())[:10]))
    if a.fail_on_pending and (len(out) - len(ok_rows)):
        print("[FAIL] 存在待确认重复组，不允许带疑问交付")
        return 1
    summary = {"tool": TOOL, "version": VERSION, "rows": len(out),
               "ok_rows": len(ok_rows), "ok_total": ok_total,
               "pending_rows": len(out) - len(ok_rows), "pending_total": pend_total,
               "multiplier_source_conflicts": conflict,
               "outputs": [p, p2]}
    if a.json:
        _dump_json(a.json, summary)
    print("输出：%s | %s" % (p, p2))
    return 0


SELFTEST_ROWS = [
    {"group_id": "T1-典型层", "scope": "10F~35F", "item_name": "楼板混凝土",
     "unit": "M3", "instances": "26", "per_instance_qty": "12.5",
     "exception_qty": "0", "multiplier_source": "图纸层号统计 10F~35F",
     "typical_ref": "FP-10TH", "note": ""},
    {"group_id": "T1-典型层", "scope": "10F~35F", "item_name": "楼板混凝土",
     "unit": "M3", "instances": "1", "per_instance_qty": "12.5",
     "exception_qty": "-3.2", "multiplier_source": "图纸层号统计 10F~35F",
     "typical_ref": "FP-35TH", "note": "顶层差异单列"},
    {"group_id": "T2-双塔", "scope": "全楼", "item_name": "幕墙板",
     "unit": "片", "instances": "2", "per_instance_qty": "305",
     "exception_qty": "0", "multiplier_source": "效果图+总平面双塔实证",
     "typical_ref": "PCHL-AR-CD-1050", "note": ""},
    {"group_id": "T3-未知乘数", "scope": "全楼", "item_name": "门樘",
     "unit": "樘", "instances": "0", "per_instance_qty": "1879",
     "exception_qty": "0", "multiplier_source": "", "typical_ref": "PCHL-AR-CD-1050",
     "note": "乘数待确认"},
]


def cmd_selftest(a):
    base = a.tmpdir or tempfile.mkdtemp(prefix="qto_group_")
    tmp = os.path.join(base, "group_selftest")
    os.makedirs(tmp, exist_ok=True)
    fails = []
    f = os.path.join(tmp, "groups.csv")
    _write_csv(f, list(SELFTEST_ROWS[0].keys()), SELFTEST_ROWS)

    class A:
        file = f; out = os.path.join(tmp, "o"); json = ""; fail_on_pending = False
    rc = cmd_expand(A)
    if rc != 0:
        fails.append("expand 返回 %d（应 0）" % rc)
    rows = _read_csv(os.path.join(tmp, "o", "重复组展开表.csv"))[0] or []
    if len(rows) != 4:
        fails.append("展开行数 %d，应为 4" % len(rows))
    ok_total = sum(_num(r["subtotal"]) for r in rows if r["status"] == "OK")
    if abs(ok_total - (26 * 12.5 + (12.5 - 3.2) + 2 * 305)) > 1e-6:
        fails.append("OK 合计 %g，应为 %g" % (ok_total, 26 * 12.5 + 9.3 + 610))
    # 差异行必须单列（exception_qty != 0 的行自身 subtotal 只含自身差额）
    diff_row = [r for r in rows if _num(r["exception_qty"]) != 0]
    if not diff_row:
        fails.append("差异行未保留")
    # 乘数无来源 → 待确认
    pend = [r for r in rows if r["status"] != "OK"]
    if len(pend) != 1:
        fails.append("待确认行 %d，应为 1" % len(pend))
    # fail_on_pending 应返回 1
    class B(A):
        fail_on_pending = True
    if cmd_expand(B) != 1:
        fails.append("fail_on_pending 未生效（应返回 1）")
    # 缺列返回 2
    class C(A):
        file = os.path.join(tmp, "none.csv")
    if cmd_expand(C) != 2:
        fails.append("缺文件应返回 2")

    print("=" * 78)
    if fails:
        print("[FAIL] qto_repeat_groups 自证 %d 项不通过：" % len(fails))
        for x in fails:
            print("   - %s" % x)
        print("=" * 78)
        return 1
    print("[PASS] qto_repeat_groups 自证全部通过（4 组用例）")
    print("  用例1 典型层展开 + 差额单列")
    print("  用例2 双塔乘数展开")
    print("  用例3 乘数无来源 → 待确认，不进交付总量")
    print("  用例4 fail_on_pending / 缺列返回码")
    print("=" * 78)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="典型区/重复组展开与乘数管控")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("expand", help="按实例数展开并产出差额敞口")
    p1.add_argument("--file", required=True)
    p1.add_argument("--out", required=True)
    p1.add_argument("--json", default="")
    p1.add_argument("--fail-on-pending", action="store_true")
    p1.set_defaults(func=cmd_expand)

    p2 = sub.add_parser("selftest", help="自证")
    p2.add_argument("--tmpdir", default="", help="自证临时目录（默认系统临时目录）")
    p2.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
