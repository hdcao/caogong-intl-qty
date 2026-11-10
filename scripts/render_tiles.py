# -*- coding: utf-8 -*-
"""渲染切块 manifest 工具 —— 让"切块读字"可复现。

**为什么要有它**

`drawing-reading-playbook.md` T7 把切块读数从"最后手段"升为"主力取证之一"，
并且承认 `render_read_text` 是 **medium** 证据。但 medium 的前提是**可复现**：
别人拿你的 manifest 必须能还原到唯一一块 tile、tile 内唯一坐标。

做不到这一点，"我看到了 1.28"和"我记得我看到了 1.28"没有区别。

**工具管三件事**

1. **建坐标系**：轴线像素坐标映射表（轴号 → 页面坐标），后续定位只查表不目测；
2. **切块纪律**：15% 重叠、优先沿轴线切、统一 scale=3.0；
3. **字高判据**：每块推算标注字高 **p10**（不用 min，理由见下），<10 px 判不合格
   （读出来是猜的，不是读的）。

**三条判据都写死在 check 里，不靠自觉**

    render_tiles.py build  --pdf <图>.pdf --page 2 --scale 3.0 --overlap 0.15 \
                           --axes "A:320,E:1180" --out tiles/
    render_tiles.py check  --manifest tiles/manifest.json --min-glyph 10
    render_tiles.py selftest

退出码：0 通过／1 存在不合格 tile／2 参数或文件问题（**含全篇字高未校验**——
"没测到"不是"通过"）。

**边界（诚实声明）**

- 字高只能从**文字层**量。转曲图与扫描件没有字符对象，量不到——
  此时记 `glyph_source="unmeasurable"`。**部分块未校验 → WARN；全部块未校验 → exit 2**：
  一张一个字都量不到的图，"字高≥10px"这条判据等于没写，给绿灯就是假绿；
- 本工具不做 OCR、不做二值化、不做版面分析；它只负责切块、算字高、写 manifest；
- `--axes` 坐标单位是 **PDF 点（pt）**，不是屏幕像素。用 `vector_forensics.py probe`
  或 `extract_dwg_vector.py` 拿到的坐标同属 pt，可直接用。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

VERSION = "1.0.0"
DEFAULT_SCALE = 3.0
DEFAULT_OVERLAP = 0.15
DEFAULT_MIN_GLYPH = 10.0


# --------------------------------------------------------------------------
# 纯判据（不依赖 PyMuPDF，selftest 直接调这些）
# --------------------------------------------------------------------------
def tile_overlap_ratio(t1, t2):
    """两块的交叠面积 / 较小块面积。0 = 硬边界。"""
    ax0, ay0, ax1, ay1 = t1
    bx0, by0, bx1, by1 = t2
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    a1 = max(0.0, (ax1 - ax0)) * max(0.0, (ay1 - ay0))
    a2 = max(0.0, (bx1 - bx0)) * max(0.0, (by1 - by0))
    small = min(a1, a2)
    if small <= 0:
        return 0.0
    return inter / small


def overlap_ok(bbox, neighbors, min_ratio):
    """块与任一相邻块的重叠 ≥ min_ratio 即算合格。

    比较留 1e-6 的余量：切块坐标落到 3 位小数，15.000% 会算成 0.1499997。
    少了这个余量，规划器刚好切在 15% 时每块都判不合格——那是纯数值噪声造成的假红。
    """
    if not neighbors:
        return True
    return any(tile_overlap_ratio(bbox, nb) >= min_ratio - 1e-6 for nb in neighbors)


def glyph_verdict(glyph_px, min_glyph):
    """(level, msg)。level ∈ ok / fail / unverified。"""
    if glyph_px is None:
        return ("unverified",
                "字高未校验（页面无文字层，转曲或扫描）——这一块的字高靠人工确认，"
                "不得默认合格")
    if glyph_px < min_glyph:
        return ("fail", "最小标注字高 %.1f px < %.0f px——读出来的数字是猜的，"
                        "须缩小 tile 或提高 scale 后重切" % (glyph_px, min_glyph))
    return ("ok", "字高 %.1f px ≥ %.0f px" % (glyph_px, min_glyph))


def axes_label(bbox, axes):
    """tile 覆盖的轴号区间，如 'A~C'。axes = [(name, x_pt), …] 已按 x 排序。"""
    if not axes:
        return ""
    x0, _, x1, _ = bbox
    inside = [n for n, x in axes if x0 <= x <= x1]
    near = [n for n, x in axes if x0 - 1e-6 <= x <= x1 + 1e-6]
    picked = near or inside
    if not picked:
        return ""
    return picked[0] if len(picked) == 1 else "%s~%s" % (picked[0], picked[-1])


# --------------------------------------------------------------------------
# 布局
# --------------------------------------------------------------------------
def plan_tiles(rect, cols, rows, axes_x=None, overlap=DEFAULT_OVERLAP):
    """生成切块 bbox 列表 [(x0,y0,x1,y1), …]。

    切分线优先吸附到轴线坐标：轴线上通常没有标注，切在那里不会截断标注文字。
    重叠按 **最终块宽** 的比例外扩，扩后夹在页面内。

    外扩量按 `mx = tw·r/(2−r)` 解：若按 `tw·r/2` 外扩，块宽变成 `tw(1+r/2)`，
    实际交叠比 `r/(1+r/2)` 只有 0.1395——**声明 15%、交付 14%**，check 按 15% 判会全红。
    交付值与声明值必须是同一个数，否则要么假红（判据严）要么假绿（判据松）。
    """
    x0, y0, x1, y1 = rect

    def cuts(lo, hi, n, snaps):
        pts = [lo + (hi - lo) * i / float(n) for i in range(n + 1)]
        # 内部分割线吸附到最近的轴线（容差 = 一个分割间距的 12%）
        if snaps and n > 1:
            tol = (hi - lo) / float(n) * 0.12
            out = [pts[0]]
            for p in pts[1:-1]:
                cand = [s for s in snaps if lo < s < hi and abs(s - p) <= tol]
                out.append(min(cand, key=lambda s: abs(s - p)) if cand else p)
            out.append(pts[-1])
            pts = out
        return pts

    xs = cuts(x0, x1, cols, axes_x)
    ys = cuts(y0, y1, rows, None)
    tw, th = (x1 - x0) / float(cols), (y1 - y0) / float(rows)
    # 解 2m/(tw+m) = overlap，使最终交叠比真的等于 overlap
    k = overlap / (2.0 - overlap) if overlap < 1.0 else 0.0
    mx, my = tw * k, th * k

    tiles = []
    for r in range(rows):
        for c in range(cols):
            bx0 = max(x0, xs[c] - mx)
            by0 = max(y0, ys[r] - my)
            bx1 = min(x1, xs[c + 1] + mx)
            by1 = min(y1, ys[r + 1] + my)
            tiles.append((round(bx0, 3), round(by0, 3), round(bx1, 3), round(by1, 3)))
    return tiles


def neighbors_of(idx, cols, rows):
    """4 邻接（左/右/上/下），返回下标列表。"""
    r, c = divmod(idx, cols)
    out = []
    if c > 0:
        out.append(idx - 1)
    if c < cols - 1:
        out.append(idx + 1)
    if r > 0:
        out.append(idx - cols)
    if r < rows - 1:
        out.append(idx + cols)
    return out


# --------------------------------------------------------------------------
def _import_fitz():
    # 新版包名是 pymupdf，fitz 只是兼容别名且已弃用（import fitz 会打警告到 stderr，
    # 污染门禁输出）。先试新名，再回落旧名。
    try:
        import pymupdf
        return pymupdf, None
    except Exception:
        pass
    try:
        import fitz  # PyMuPDF 旧入口
        return fitz, None
    except Exception as e:  # pragma: no cover - 环境相关
        return None, str(e)


def _parse_axes(spec):
    """'A:320,E:1180' → [('A',320.0), ('E',1180.0)]，按坐标排序。"""
    out = []
    if not spec:
        return out
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError("--axes 每项须写成 轴号:坐标，收到 %r" % part)
        name, coord = part.rsplit(":", 1)
        out.append((name.strip(), float(coord)))
    out.sort(key=lambda t: t[1])
    return out


def _collect_sizes(page):
    """页面文字层的字号列表（pt）。无文字层返回 []。"""
    sizes = []
    try:
        d = page.get_text("dict")
    except Exception:
        return sizes
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if str(span.get("text") or "").strip():
                    s = float(span.get("size") or 0)
                    if s > 0:
                        sizes.append((s, tuple(span.get("bbox") or (0, 0, 0, 0))))
    return sizes


def _size_stats_in(sizes, bbox):
    """tile 内的字号统计 → {"min", "p10", "n"}（pt）。

    **为什么判据用 p10 而不是 min**（2026-11-02 实测教训）：
    本项目图册第 1 张整页只有 **一个** 3.14 pt 的孤立注记，其余标注 7.85~49 pt。
    按 min 判 ⇒ 该块字高 9.4 px < 10 px ⇒ 判不合格；但这一块其实是可读的
    （p10 = 7.85 pt × 3 = 23.6 px）。**这就是假红**：一个图签小字把所有块判死，
    跑两次之后没人再看这条判据——G-FRESH 已经演过一次同样的剧本。
    所以默认按 p10 判，min 单列在 manifest 里备查（真要查最小字可读性用 --stat min）。
    """
    x0, y0, x1, y1 = bbox
    hit = []
    for s, bb in sizes:
        cx, cy = (bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            hit.append(s)
    if not hit:
        return {"min": None, "p10": None, "n": 0}
    hit.sort()
    # 样本少于 4 个时 p10 退化为 min（保守：宁可多报一次可读性问题）
    idx = 0 if len(hit) < 4 else min(len(hit) - 1, int(len(hit) * 0.1))
    return {"min": round(hit[0], 3), "p10": round(hit[idx], 3), "n": len(hit)}


def cmd_build(a):
    fitz, err = _import_fitz()
    if fitz is None:
        print("[ERR] 需要 PyMuPDF：pip install pymupdf（%s）" % err)
        return 2
    if not os.path.exists(a.pdf):
        print("[ERR] 找不到 PDF：%s" % a.pdf)
        return 2
    if a.scale <= 0:
        print("[ERR] --scale 须 >0")
        return 2
    if a.overlap <= 0:
        print("[ERR] --overlap 须 >0（相邻块无重叠时，跨块边界的标注会被截成两半，"
              "整行丢失且不报错）；建议 0.15")
        return 2
    if a.overlap >= 0.5:
        print("[ERR] --overlap 须 <0.5（重叠过半等于重复计内容）")
        return 2
    if not (0 <= a.margin < 0.5):
        print("[ERR] --margin 须在 [0,0.5) 内（≥0.5 会把有效区反过来）")
        return 2

    try:
        axes = _parse_axes(a.axes)
    except ValueError as e:
        print("[ERR] %s" % e)
        return 2

    doc = fitz.open(a.pdf)
    if a.page < 1 or a.page > doc.page_count:
        print("[ERR] --page %d 越界（该文件共 %d 页）" % (a.page, doc.page_count))
        return 2
    page = doc.load_page(a.page - 1)
    rect = page.rect
    m = a.margin
    work = (rect.x0 + rect.width * m, rect.y0 + rect.height * m,
            rect.x1 - rect.width * m, rect.y1 - rect.height * m)

    cols, rows = a.cols, a.rows
    if a.grid:
        try:
            cols, rows = [int(x) for x in a.grid.lower().split("x")]
        except Exception:
            print("[ERR] --grid 须写成 3x4")
            return 2
    if cols < 1 or rows < 1:
        print("[ERR] --cols/--rows 须 ≥1")
        return 2

    sizes = _collect_sizes(page)
    boxes = plan_tiles(work, cols, rows, [x for _, x in axes], a.overlap)

    out_dir = a.out
    tdir = os.path.join(out_dir, "tiles")
    if not os.path.isdir(tdir):
        os.makedirs(tdir)

    mat = fitz.Matrix(a.scale, a.scale)
    entries = []
    for i, bb in enumerate(boxes):
        clip = fitz.Rect(*bb)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        name = "p%02d_r%dc%d.png" % (a.page, i // cols + 1, i % cols + 1)
        path = os.path.join(tdir, name)
        pix.save(path)
        min_sz = _size_stats_in(sizes, bb)
        glyph_px = round(min_sz["p10"] * a.scale, 2) if min_sz["p10"] else None
        entries.append({
            "tile": os.path.relpath(path, out_dir).replace("\\", "/"),
            "index": i, "row": i // cols + 1, "col": i % cols + 1,
            "page": a.page,
            "bbox_pt": [round(v, 3) for v in bb],
            "bbox_px": [round(bb[0] * a.scale), round(bb[1] * a.scale),
                        round(bb[2] * a.scale), round(bb[3] * a.scale)],
            "size_px": [pix.width, pix.height],
            "scale": a.scale,
            "axis_range": axes_label(bb, axes),
            "glyph_pt": min_sz["p10"],              # 判据用：p10 字号
            "glyph_px": glyph_px,                    # 判据用：p10 × scale
            "glyph_min_pt": min_sz["min"],            # 备查：本块最小字号（可能来自图签）
            "glyph_min_px": (round(min_sz["min"] * a.scale, 2)
                             if min_sz["min"] else None),
            "glyph_samples": min_sz["n"],
            "glyph_source": "text_layer" if min_sz["n"] else "unmeasurable",
            # 文档承诺的字段（references/drawing-reading-playbook.md）：建的时候就写，
            # 不留到 check 现算——现算的结果和当时的切法可能不是一回事
            "overlap_ok": None,
        })

    # 重叠自检（建的时候就查，不要留到 check）
    bad_ov = []
    for i, e in enumerate(entries):
        nb = [entries[j]["bbox_pt"] for j in neighbors_of(i, cols, rows)]
        ok_ov = overlap_ok(e["bbox_pt"], nb, a.overlap)
        e["overlap_ok"] = ok_ov if nb else True   # 边缘块受页面边界限制，不强制
        if not ok_ov:
            bad_ov.append(e["tile"])

    manifest = {
        "tool": "render_tiles.py", "tool_version": VERSION,
        "pdf": os.path.abspath(a.pdf), "page": a.page,
        "page_rect_pt": [round(rect.x0, 3), round(rect.y0, 3),
                         round(rect.x1, 3), round(rect.y1, 3)],
        "work_rect_pt": [round(v, 3) for v in work],
        "scale": a.scale, "overlap": a.overlap,
        "grid": {"cols": cols, "rows": rows},
        "axes": [{"name": n, "x_pt": x} for n, x in axes],
        "has_text_layer": bool(sizes),
        # 判据口径是 p10；min 单列备查（两者都留，免得后人以为没测）
        "p10_glyph_px": min([e["glyph_px"] for e in entries
                             if e["glyph_px"] is not None], default=None),
        "min_glyph_any_px": min([e["glyph_min_px"] for e in entries
                                 if e["glyph_min_px"] is not None], default=None),
        "tiles": entries,
    }
    mpath = os.path.join(out_dir, "manifest.json")
    with io.open(mpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2))

    print("[OK] %d 块 tile → %s" % (len(entries), tdir))
    print("     scale=%.1f  overlap=%.0f%%  grid=%dx%d  轴线=%d 条"
          % (a.scale, a.overlap * 100, cols, rows, len(axes)))
    if not sizes:
        print("[WARN] 该页无文字层（转曲或扫描）——字高无法测算，check 会记「未校验」")
    elif manifest["p10_glyph_px"] is not None:
        print("     标注字高 p10 ≈ %.1f px（判据口径）｜min ≈ %.1f px（单列备查）"
              % (manifest["p10_glyph_px"], manifest["min_glyph_any_px"]))
    if bad_ov:
        print("[WARN] %d 块重叠不足（边缘块受页面边界限制，属正常）；其余请查 --overlap"
              % len(bad_ov))
    print("     manifest: %s" % mpath)
    return 0


def cmd_check(a):
    if not os.path.exists(a.manifest):
        print("[ERR] 找不到 manifest：%s" % a.manifest)
        return 2
    try:
        with io.open(a.manifest, encoding="utf-8") as f:
            man = json.load(f)
    except Exception as e:
        print("[ERR] manifest 不是合法 JSON：%s" % e)
        return 2

    tiles = man.get("tiles") or []
    if not tiles:
        print("[ERR] manifest 里没有 tile——空 manifest 判 exit 2（没查不等于通过）")
        return 2

    grid = man.get("grid") or {}
    cols = int(grid.get("cols") or 1)
    rows = int(grid.get("rows") or 1)
    want_ov = float(man.get("overlap") or 0.0)

    fails, warns, oks = [], [], 0
    for i, t in enumerate(tiles):
        bb = tuple(t.get("bbox_pt") or (0, 0, 0, 0))
        # 判据一：字高（默认按 p10，min 只备案——理由见 _size_stats_in 注释）
        g = t.get("glyph_px") if a.stat == "p10" else t.get("glyph_min_px")
        level, msg = glyph_verdict(g, a.min_glyph)
        if level == "fail":
            fails.append("%s 字高不足（%s）：%s" % (t.get("tile"), a.stat, msg))
        elif level == "unverified":
            warns.append("%s %s" % (t.get("tile"), msg))
        else:
            oks += 1
        # min 与 p10 差距过大时提示：说明块内有图签/极小注记，判 min 会假红
        gmin, gp10 = t.get("glyph_min_px"), t.get("glyph_px")
        if gmin and gp10 and gp10 > 0 and gmin < gp10 * 0.5:
            warns.append("%s 块内最小字高 %.1f px 远小于 p10 %.1f px——"
                         "可能是图签/脚注小字；判据按 p10，如要查最小字可读性用 --stat min"
                         % (t.get("tile"), gmin, gp10))
        # 判据二：重叠（只对内部块强制，边缘块受页面边界限制）
        nb = [tiles[j].get("bbox_pt") for j in neighbors_of(i, cols, rows)]
        if nb and not overlap_ok(bb, nb, want_ov):
            fails.append("%s 与相邻块重叠不足（应 ≥ %.0f%%）——硬边界会截断多行标注，"
                         "整行丢失且不报错" % (t.get("tile"), want_ov * 100))
        # 判据三：轴号（沿轴线切的目的是能挂轴号）
        if (man.get("axes") or []) and not str(t.get("axis_range") or "").strip():
            warns.append("%s 未落到任何轴号区间——这一块的读数无法写进 location"
                         % t.get("tile"))

    print("=" * 78)
    print("切块 manifest 检查  %s" % os.path.basename(a.manifest))
    print("=" * 78)
    for m in fails:
        print("[FAIL] %s" % m)
    for m in warns:
        print("[WARN] %s" % m)
    key = "glyph_px" if a.stat == "p10" else "glyph_min_px"
    n_unv = sum(1 for t in tiles if t.get(key) is None)
    print("-" * 78)
    print("tile=%d  通过=%d  FAIL=%d  WARN=%d  字高未校验=%d"
          % (len(tiles), oks, len(fails), len(warns), n_unv))
    rc = exit_of(len(fails), n_unv, len(tiles))
    if rc == 1:
        print("判定：不合格（exit 1）")
        return 1
    if rc == 2:
        print("判定：无法判定（exit 2）——全部 tile 字高未校验，本图无可测文字层。"
              "字高不成立时「字高≥10px」这条判据等同于没写，不能报通过；"
              "转曲/扫描图请改用 tile 精读指令做人工复核。")
        return 2
    print("判定：通过（%d 块字高未校验已单列，不折算为通过）" % n_unv)
    return 0


def exit_of(n_fail, n_unv, n_tiles):
    """结论 → 退出码。抽出来是为了能被 selftest 断言（判据对、退出码错，照样假绿）。"""
    if n_fail:
        return 1
    if n_tiles and n_unv == n_tiles:
        return 2
    return 0


# --------------------------------------------------------------------------
def cmd_selftest(a):
    """判据自证：重叠、字高、轴号三组，含"不得误报"的反向断言。"""
    ok = 0
    total = 0

    # 退出码：判据对而退出码错，等于门禁没接上
    for label, got_rc, want_rc in [
        ("有 FAIL → 1",              exit_of(2, 0, 9), 1),
        ("全通过 → 0",               exit_of(0, 0, 9), 0),
        ("部分字高未校验 → 0",       exit_of(0, 3, 9), 0),
        ("全部字高未校验 → 2",       exit_of(0, 9, 9), 2),
        ("FAIL 优先于未校验 → 1",    exit_of(1, 9, 9), 1),
    ]:
        total += 1
        hit = (got_rc == want_rc)
        if hit:
            ok += 1
        print("  [%s] 退出码 %-22s → %d（期望 %d）"
              % ("符合" if hit else "不符", label, got_rc, want_rc))

    # p10 与 min 必须分得开：一个 3 pt 图签小字不该判死整块
    total += 1
    hit = glyph_verdict(7.85 * 3.0, 10.0)[0] == "ok"
    ok += 1 if hit else 0
    print("  [%s] p10=7.85pt×3.0=23.6px 判合格（不因 3.14pt 图签假红）→ %s"
          % ("符合" if hit else "不符", "ok" if hit else "fail"))

    # 重叠：阈值必须等于宣称的 15%，不许打折（旧实现按 7.5% 判，口径松一半）
    total += 1
    blk = (0.0, 0.0, 100.0, 100.0)
    hit = (overlap_ok(blk, [(84.0, 0.0, 184.0, 100.0)], 0.15)        # 16% → 合格
           and not overlap_ok(blk, [(86.0, 0.0, 186.0, 100.0)], 0.15))  # 14% → 不足
    ok += 1 if hit else 0
    print("  [%s] 重叠 16%% 合格 / 14%% 不足（阈值等于宣称的 15%%，不打折）"
          % ("符合" if hit else "不符"))

    def chk(label, got, want):
        nonlocal ok, total
        total += 1
        hit = (got == want)
        ok += 1 if hit else 0
        print("  [%s] %-40s → %s" % ("符合" if hit else "不符", label, got))

    # 重叠判据：15% 重叠的两块
    A = (0, 0, 100, 100)
    B_ok = (85, 0, 185, 100)      # 重叠 15/100 = 15%
    B_hard = (100, 0, 200, 100)   # 硬边界
    chk("重叠 15% 判合格(>=7.5%)", overlap_ok(A, [B_ok], 0.075), True)
    chk("硬边界判不合格", overlap_ok(A, [B_hard], 0.075), False)
    chk("重叠率计算 15%", round(tile_overlap_ratio(A, B_ok), 4), 0.15)

    # 字高判据
    chk("字高 12px ≥10 判 ok", glyph_verdict(12.0, 10.0)[0], "ok")
    chk("字高 9px <10 判 fail", glyph_verdict(9.0, 10.0)[0], "fail")
    chk("字高 10px 边界判 ok", glyph_verdict(10.0, 10.0)[0], "ok")
    chk("无文字层判 unverified（不是 ok）", glyph_verdict(None, 10.0)[0], "unverified")

    # 反向断言：字高充足的大块不得被判 fail
    chk("反向：字高 30px 不得判 fail", glyph_verdict(30.0, 10.0)[0] != "fail", True)

    # 轴号区间
    axes = [("A", 0.0), ("B", 100.0), ("C", 200.0)]
    chk("轴号区间 A~C", axes_label((0, 0, 200, 50), axes), "A~C")
    chk("单轴区间 A", axes_label((0, 0, 100, 50), axes), "A~B")

    # 布局：切分线吸附轴线
    tiles = plan_tiles((0, 0, 300, 100), 3, 1, [100.0, 200.0], 0.0)
    xs = sorted(set([t[0] for t in tiles] + [t[2] for t in tiles]))
    chk("切分线落在轴线上（含 100/200）", all(any(abs(x - v) < 1e-6 for v in (0, 100, 200, 300))
                                          for x in xs), True)

    # 邻接
    chk("3x2 网格中 0 号块有 2 个邻居", len(neighbors_of(0, 3, 2)), 2)
    chk("9 宫格中心块有 4 个邻居", len(neighbors_of(4, 3, 3)), 4)

    print()
    print("SELFTEST %d/%d" % (ok, total))
    if ok != total:
        print("SELFTEST FAIL")
        return 1
    print("SELFTEST PASS")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="渲染切块 manifest（重叠 / 字高 / 轴线映射）")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("build", help="切块并生成 manifest")
    p1.add_argument("--pdf", required=True)
    p1.add_argument("--page", type=int, default=1, help="页码，从 1 开始")
    p1.add_argument("--scale", type=float, default=DEFAULT_SCALE,
                    help="统一渲染倍数，默认 3.0（不做 2/3 分级判断）")
    p1.add_argument("--overlap", type=float, default=DEFAULT_OVERLAP,
                    help="块间重叠比例，默认 0.15，禁止 0")
    p1.add_argument("--grid", default="", help="写成 3x4；给了就覆盖 --cols/--rows")
    p1.add_argument("--cols", type=int, default=3)
    p1.add_argument("--rows", type=int, default=3)
    p1.add_argument("--axes", default="", help='轴线坐标，如 "A:320,E:1180"（单位 pt）')
    p1.add_argument("--margin", type=float, default=0.03,
                    help="有效区外扩边距比例（去掉图签/空白），默认 0.03")
    p1.add_argument("--out", required=True)
    p1.set_defaults(func=cmd_build)

    p2 = sub.add_parser("check", help="检查 manifest 是否满足切块纪律")
    p2.add_argument("--manifest", required=True)
    p2.add_argument("--min-glyph", type=float, default=DEFAULT_MIN_GLYPH)
    p2.add_argument("--stat", choices=("p10", "min"), default="p10",
                    help="字高判据用 p10（默认，防图签小字假红）还是 min（最严）")
    p2.set_defaults(func=cmd_check)

    p3 = sub.add_parser("selftest", help="判据自证")
    p3.set_defaults(func=cmd_selftest)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    return a.func(a)


if __name__ == "__main__":
    # 顶层兜底：崩溃退 2（工具没跑成），不退 1（会被读成"有不合格 tile"）。
    # 实测未加这一层时，损坏 PDF / --out 指向文件 / --margin 0.6 都带着 traceback 退 1。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _e:
        print("[ERR] 工具执行失败（exit 2，不是判据不通过）：%s: %s"
              % (type(_e).__name__, _e))
        sys.exit(2)
