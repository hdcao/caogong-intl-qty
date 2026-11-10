#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vector_forensics.py -- deterministic forensics for vector drawings with NO text layer
(文字转曲 / CAD-exported PDFs where get_text() is always empty).

Three commands:
  probe   - classify the sheet: page size, /Rotate, text length, fonts, drawing count,
            fill-colour histogram, rect-size histogram, line-angle histogram.
            These three histograms are the map for reading the sheet.
  symbols - count symbols by (fill colour + rect size + aspect) filtering and spatial
            clustering of rect centres. Outputs the "blocks per symbol" histogram,
            which is what turns "a symbol" into "N measurable objects".
  chain   - dimension-chain closure: verify  total == sum(parts), or reverse-solve the
            count from  total = 2*edge + unit + n*spacing.

Stdlib only, except pymupdf (fitz) which is imported lazily with a clear message.

Design rules learned in production:
  * tol must be calibrated between "distance between blocks inside one symbol" and
    "distance between neighbouring symbols" -- see README notes in
    references/vector-forensics.md (T4).
  * filter by aspect ratio: dashed line segments are long thin rects, symbols are square.
  * the legend area repeats the same symbols; exclude it or subtract a constant.
"""

from __future__ import print_function

import argparse
try:
    from qto_core import parse_decimal, DataValidationError, atomic_write_json
except ImportError:
    parse_decimal = None
    DataValidationError = ValueError
    atomic_write_json = None

import collections
import json
import math
import os
import sys

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


__version__ = "3.2.0"

# 判型 → 首选路线。判型的唯一目的是选路线；只给标签不给路线，
# 标签就会被按字面读（"有文字层"→"解析文字层"），而部分转曲的图恰恰不能这么走。
ROUTE = {
    "vector_no_text":
        "矢量取证五件套：probe → symbols → chain → 渲染切块 → OCR（仅辅助）",
    "partial_vectorized":
        "文本层直取 + 矢量取证补缺口（双校准法标定后量测）；不是二选一",
    "vector_with_text":
        "文本层解析（最快最准）",
    "empty_or_scanned":
        "原生 dpi 切块 + 二值化",
}

# 图元数 / 文字长度 超过此比值即判"部分转曲"。
# 依据：本桥 p3 实测 615893/10382 = 59.3；完整文字层的普通图纸通常在 5 以下。
PARTIAL_RATIO = 20.0


def _fitz():
    try:
        import fitz  # PyMuPDF
        return fitz
    except ImportError:
        try:
            import pymupdf as fitz
            return fitz
        except ImportError:
            sys.stderr.write(
                "ERROR: vector_forensics needs PyMuPDF.\n"
                "       pip install pymupdf\n")
            raise SystemExit(2)


# --------------------------------------------------------------------------- helpers

def _fmt_colour(c):
    if c is None:
        return None
    try:
        return [round(float(x), 3) for x in c]
    except Exception:
        return list(c)


def _parse_colour(s):
    """'1,0,1' -> (1.0, 0.0, 1.0)"""
    parts = [p.strip() for p in s.split(",")]
    if len(parts) == 1:
        parts = parts[0].split()
    if len(parts) not in (3, 4):
        raise argparse.ArgumentTypeError("colour must be R,G,B (e.g. 1,0,1)")
    return tuple(float(p) for p in parts[:3])


def _parse_pair(s):
    """'1.9-4.5' -> (1.9, 4.5);  '4.5' -> (0.0, 4.5)"""
    s = s.strip()
    if "-" in s:
        a, b = s.split("-", 1)
        return (float(a), float(b))
    return (0.0, float(s))


def _clusters_union_find(pts, tol):
    """Grid-accelerated union-find clustering. O(n) for realistic symbol counts."""
    n = len(pts)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    cell = {}
    for i, (x, y) in enumerate(pts):
        cell.setdefault((int(x // tol), int(y // tol)), []).append(i)
    for (cx, cy), idxs in cell.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cell.get((cx + dx, cy + dy), ()):
                    for i in idxs:
                        if j <= i:
                            continue
                        if abs(pts[i][0] - pts[j][0]) <= tol and abs(pts[i][1] - pts[j][1]) <= tol:
                            a, b = find(i), find(j)
                            if a != b:
                                parent[a] = b
    out = collections.defaultdict(int)
    for i in range(n):
        out[find(i)] += 1
    return out


def _collect_rects(page, colour=None, size=None, aspect=None):
    """Return list of dicts for filled rects (dr['rect']) matching the filters."""
    res = []
    for dr in page.get_drawings():
        fill = dr.get("fill")
        if colour is not None:
            if fill is None:
                continue
            fc = _fmt_colour(fill)
            if fc is None or len(fc) < 3:
                continue
            if any(abs(fc[i] - colour[i]) > 0.02 for i in range(3)):
                continue
        r = dr["rect"]
        w, h = float(r.width), float(r.height)
        if w <= 0 or h <= 0:
            continue
        if size is not None:
            if not (size[0] <= w <= size[1]):
                continue
        if aspect is not None:
            a = w / h
            if not (aspect[0] <= a <= aspect[1]):
                continue
        res.append({
            "x": round((r.x0 + r.x1) / 2.0, 3),
            "y": round((r.y0 + r.y1) / 2.0, 3),
            "w": round(w, 3),
            "h": round(h, 3),
            "fill": _fmt_colour(fill),
        })
    return res


# --------------------------------------------------------------------------- probe

def cmd_probe(args):
    fitz = _fitz()
    doc = fitz.open(args.pdf)
    pages = []
    pnos = range(len(doc))
    if getattr(args, "page", 0):
        # 1 起给人用，0 作"全部"；这里把 1 映射到第 0 页
        idx = args.page - 1 if args.page >= 1 else 0
        pnos = [idx] if 0 <= idx < len(doc) else []
        if not pnos:
            print("[ERR] --page %d 超出范围（共 %d 页）" % (args.page, len(doc)))
            return 2
    for pno in pnos:
        pg = doc[pno]
        txt = pg.get_text("text")
        fonts = []
        try:
            fonts = sorted(set(f[3] for f in pg.get_fonts()))
        except Exception:
            pass
        drawings = pg.get_drawings()

        fills = collections.Counter()
        sizes = collections.Counter()
        angles = collections.Counter()
        nrect = nline = 0
        for dr in drawings:
            f = _fmt_colour(dr.get("fill"))
            if f is not None:
                fills[tuple(f)] += 1
            r = dr["rect"]
            w, h = float(r.width), float(r.height)
            if w > 0 and h > 0:
                sizes[(round(w, 1), round(h, 1))] += 1
            for it in dr.get("items", ()):
                if it[0] == "re":
                    nrect += 1
                elif it[0] == "l":
                    nline += 1
                    p1, p2 = it[1], it[2]
                    L = math.hypot(p2.x - p1.x, p2.y - p1.y)
                    if L > 5:
                        ang = int(round(math.degrees(math.atan2(p2.y - p1.y, p2.x - p1.x)) % 180))
                        angles[ang] += 1

        if fills or sizes or angles:
            # ★ 判型三档不够用。"有文本层"不等于"文字层完整"——
            #   部分转曲的图（本桥 p3：text=10382、图元 615893）文字层只剩跨号/标高/图名，
            #   尺寸标注仍是轮廓线。此时必须"文本层直取 + 矢量补缺口"，不是二选一。
            #   旧版只有 vector_no_text / vector_with_text 两档，这类图被判成后者，
            #   于是被路由到"只解析文本层"，量测类信息（如无文字标注的桩径）整类漏掉。
            n_txt = len(txt.strip())
            if not n_txt and drawings:
                kind = "vector_no_text (文字转曲)"
            elif n_txt and drawings:
                ratio = len(drawings) / float(n_txt)
                kind = ("partial_vectorized (部分转曲)"
                        if ratio > args.partial_ratio
                        else "vector_with_text (完整文字层)")
            else:
                kind = "empty_or_scanned"
        else:
            kind = "no_vector_content"

        pages.append({
            "page": pno + 1,
            "width_pt": round(float(pg.rect.width), 1),
            "height_pt": round(float(pg.rect.height), 1),
            "rotation": pg.rotation,
            "text_len": len(txt.strip()),
            "fonts": fonts,
            "drawings": len(drawings),
            "rect_items": nrect,
            "line_items": nline,
            "kind": kind,
            "route": ROUTE.get(kind.split(" ")[0], ""),
            "fill_top": [{"colour": list(k), "n": v} for k, v in fills.most_common(args.top)],
            "size_top": [{"w": k[0], "h": k[1], "n": v} for k, v in sizes.most_common(args.top)],
            "angle_top": [{"deg": k, "n": v} for k, v in angles.most_common(args.top)],
        })
    doc.close()

    doc_out = {"tool": "vector_forensics.probe", "version": __version__,
               "pdf": os.path.abspath(args.pdf), "pages": pages}

    if args.out:
        if os.path.isdir(args.out) or str(args.out).endswith(("/", "\\")):
            print("[ERR] --out 需要**文件名**（如 out/probe.json），不是目录：%r"
                  % args.out)
            return 2
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(doc_out, fh, ensure_ascii=False, indent=1)

    for p in pages:
        print("=" * 78)
        print("p%-3d %sx%s pt  rot=%s  text=%d  fonts=%d  drawings=%d  -> %s"
              % (p["page"], p["width_pt"], p["height_pt"], p["rotation"],
                 p["text_len"], len(p["fonts"]), p["drawings"], p["kind"]))
        if p.get("route"):
            print("   route  %s" % p["route"])
        for key, label in (("fill_top", "fill"), ("size_top", "size"), ("angle_top", "angle")):
            rows = p[key][:args.top]
            if not rows:
                continue
            s = ", ".join(
                ("%s:%d" % (r["colour"], r["n"])) if key == "fill_top" else
                ("%sx%s:%d" % (r["w"], r["h"], r["n"])) if key == "size_top" else
                ("%sdeg:%d" % (r["deg"], r["n"]))
                for r in rows)
            print("   %-6s %s" % (label, s))
    return 0


# --------------------------------------------------------------------------- symbols

def cmd_symbols(args):
    fitz = _fitz()
    doc = fitz.open(args.pdf)
    pno = args.page - 1
    pg = doc[pno]
    rects = _collect_rects(pg, colour=args.color, size=args.size, aspect=args.aspect)
    if args.exclude:
        x0, y0, x1, y1 = args.exclude
        rects = [r for r in rects if not (x0 <= r["x"] <= x1 and y0 <= r["y"] <= y1)]
    pts = [(r["x"], r["y"]) for r in rects]
    cl = _clusters_union_find(pts, args.tol) if pts else {}
    hist = collections.Counter(cl.values())

    out = {
        "tool": "vector_forensics.symbols", "version": __version__,
        "pdf": os.path.abspath(args.pdf), "page": args.page,
        "filters": {"colour": list(args.color) if args.color else None,
                    "size": list(args.size) if args.size else None,
                    "aspect": list(args.aspect) if args.aspect else None,
                    "tol_pt": args.tol,
                    "exclude_box": args.exclude},
        "n_blocks": len(rects),
        "n_symbols": sum(cl.values()) and len(cl),
        "n_objects": sum(hist[k] * k for k in hist),
        "hist_blocks_per_symbol": {str(k): v for k, v in sorted(hist.items())},
        "centres": [list(p) for p in pts] if args.dump else None,
    }
    doc.close()

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        if atomic_write_json is not None:
            atomic_write_json(args.out, out)
        else:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1, allow_nan=False)

    print("blocks=%d  symbols=%d  objects=%d"
          % (out["n_blocks"], out["n_symbols"], out["n_objects"]))
    print("blocks per symbol:", dict(sorted(hist.items())))
    if not rects:
        print("NOTE: nothing matched. Run `probe` first and check the fill/size histograms.")
    return 0


# --------------------------------------------------------------------------- chain

def _finite_number(value, label):
    if parse_decimal is not None:
        try:
            number = parse_decimal(value, field_name=label)
            return float(number)
        except Exception as exc:
            raise ValueError(str(exc))
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("%s must be finite" % label)
    return number

def cmd_chain(args):
    """Dimension-chain closure / reverse-solve."""
    if args.total is None:
        sys.stderr.write("ERROR: chain needs --total\n")
        return 2
    try:
        total = _finite_number(args.total, "--total")
        tol = _finite_number(args.tol_num, "--tol")
        if tol < 0:
            raise ValueError("--tol must be >= 0")
    except (ValueError, TypeError) as exc:
        print("[ERR] 数值参数无效：%s" % exc, file=sys.stderr)
        return 2
    out = {"tool": "vector_forensics.chain", "version": __version__, "total": total}
    ok = True

    if args.parts:
        try:
            parts = [_finite_number(p, "--parts") for p in args.parts.split(",")]
        except (ValueError, TypeError) as exc:
            print("[ERR] --parts 含非法数值：%s" % exc, file=sys.stderr)
            return 2
        s = sum(parts)
        resid = total - s
        out.update(mode="sum", parts=parts, sum=round(s, 4), residual=round(resid, 4))
        out["closed"] = abs(resid) <= tol
        ok = out["closed"]
        print("Σparts = %.4f   total = %.4f   residual = %.4f   -> %s"
              % (s, total, resid, "CLOSED" if ok else "NOT CLOSED"))
    elif args.equal is not None:
        try:
            n = int(args.equal)
            edge = _finite_number(args.edge or 0.0, "--edge")
            unit = _finite_number(args.unit or 0.0, "--unit")
            if n <= 0:
                raise ValueError("--equal must be > 0")
            if edge < 0 or unit < 0:
                raise ValueError("--edge/--unit must be >= 0")
        except (ValueError, TypeError) as exc:
            print("[ERR] 等分参数无效：%s" % exc, file=sys.stderr)
            return 2
        avail = total - 2 * edge - unit
        spacing = avail / n if n else 0.0
        count = n + 1
        check = 2 * edge + unit + n * spacing
        out.update(mode="equal", n_spaces=n, edge=edge, unit=unit,
                   spacing=round(spacing, 4), count=count, check=round(check, 4))
        out["closed"] = abs(check - total) <= tol
        ok = out["closed"]
        print("%d equal spaces: spacing = %.4f   implied count = %d   -> %s"
              % (n, spacing, count, "CLOSED" if ok else "NOT CLOSED"))
        print("  (%s = 2*%.4f + %.4f + %d*%.4f)" % ("OK" if ok else "MISMATCH", edge, unit, n, spacing))
    else:
        sys.stderr.write("ERROR: chain needs --parts or --equal\n")
        return 2

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        if atomic_write_json is not None:
            atomic_write_json(args.out, out)
        else:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=1, allow_nan=False)
    return 0 if ok else 1


# --------------------------------------------------------------------------- selftest

def cmd_selftest(args):
    """Meta-gate: known geometry must be recovered deterministically."""
    fails = []

    # chain: 23610 = 2*1194 + 1640 + 8*2448
    class A(object):
        pass
    a = A()
    a.total = 23610; a.parts = None; a.equal = 8; a.edge = 1194; a.unit = 1640
    a.tol_num = 1e-6; a.out = None
    rc = cmd_chain(a)
    if rc != 0:
        fails.append("chain/equal did not close for 23610")

    # 12 girders -> 11 gaps of 1630 = 17930
    b = A(); b.total = 17930; b.parts = ",".join(["1630"] * 11)
    b.equal = None; b.tol_num = 1e-6; b.out = None
    if cmd_chain(b) != 0:
        fails.append("chain/parts did not close for 17930 = 11 x 1630")

    c = A(); c.total = 20000; c.parts = "1630,1630,1630"; c.equal = None
    c.tol_num = 1e-6; c.out = None
    if cmd_chain(c) == 0:
        fails.append("chain accepted a non-closing chain (gate too weak)")

    # clustering: 3 symbols of 6 blocks each, blocks 4pt apart, symbols 60pt apart
    pts = []
    for s in range(3):
        for k in range(6):
            pts.append((s * 60.0, k * 4.0))
    cl = _clusters_union_find(pts, 6.0)
    hist = collections.Counter(cl.values())
    if len(cl) != 3 or hist.get(6) != 3:
        fails.append("clustering tol=6 gave %s (expected 3 symbols x 6)" % dict(hist))
    cl2 = _clusters_union_find(pts, 2.0)
    if len(cl2) == 3:
        fails.append("clustering tol=2 wrongly merged blocks (gate too weak)")

    rep = {"tool": "vector_forensics.selftest", "version": __version__,
           "cases": 5, "failed": len(fails), "failures": fails}
    if args.out:
        os.makedirs(args.out if os.path.isdir(args.out) else
                    (os.path.dirname(os.path.abspath(args.out)) or "."), exist_ok=True)
        p = args.out if os.path.isdir(args.out) else args.out
        if os.path.isdir(p):
            p = os.path.join(p, "vector_forensics_selftest.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0 if not fails else 1


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="vector_forensics.py",
        description="Deterministic forensics for vector drawings with no text layer.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("probe", help="classify sheet + colour/size/angle histograms")
    p.add_argument("pdf")
    p.add_argument("--out", help="输出 JSON 的**文件名**（不是目录）")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--page", type=int, default=0,
                   help="只探第几页，从 0 起算；默认 0=全部页。"
                        "A1 大图单页可能 37 万图元，全跑要 2 分钟以上")
    p.add_argument("--partial-ratio", type=float, default=PARTIAL_RATIO,
                   help="图元数/文字长度 超过此值判『部分转曲』（默认 %.0f）。"
                        "部分转曲的图文字层只剩跨号/标高/图名，尺寸标注仍是轮廓线，"
                        "必须走『文本层直取 + 矢量补缺口』" % PARTIAL_RATIO)
    p.set_defaults(func=cmd_probe)

    s = sub.add_parser("symbols", help="count symbols by colour+size, clustered spatially")
    s.add_argument("pdf")
    s.add_argument("--page", type=int, default=1)
    s.add_argument("--color", type=_parse_colour, default=None,
                   help="fill colour R,G,B e.g. 1,0,1")
    s.add_argument("--size", type=_parse_pair, default=None,
                   help="rect width range in pt, e.g. 1.9-4.5")
    s.add_argument("--aspect", type=_parse_pair, default=None,
                   help="w/h range, e.g. 0.75-1.33 (excludes dash segments)")
    s.add_argument("--tol", type=float, default=6.0,
                   help="cluster tolerance in pt: between in-symbol and inter-symbol distance")
    s.add_argument("--exclude", type=float, nargs=4, default=None, metavar=("X0", "Y0", "X1", "Y1"),
                   help="exclude a box (e.g. the legend area) in PDF points")
    s.add_argument("--dump", action="store_true", help="include block centres in JSON")
    s.add_argument("--out")
    s.set_defaults(func=cmd_symbols)

    c = sub.add_parser("chain", help="dimension-chain closure / reverse-solve")
    c.add_argument("--total", type=float)
    c.add_argument("--parts", help="comma separated parts, e.g. 29025,29000,29000,29025")
    c.add_argument("--equal", type=int, help="number of equal spaces, e.g. 8")
    c.add_argument("--edge", type=float, default=0.0, help="edge distance on each side")
    c.add_argument("--unit", type=float, default=0.0, help="width of one unit (e.g. girder)")
    c.add_argument("--tol-num", dest="tol_num", type=float, default=1e-6)
    c.add_argument("--out")
    c.set_defaults(func=cmd_chain)

    t = sub.add_parser("selftest", help="meta-gate: verify the tool itself")
    t.add_argument("--out")
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
