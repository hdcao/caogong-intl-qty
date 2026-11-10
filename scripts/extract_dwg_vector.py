# -*- coding: utf-8 -*-
"""
矢量提取：从轮廓化 PDF 里直接数图元坐标，用来回答"图上有几个XX"。

为什么需要它（本项目实测）：
  问"一个承台里有几个桩位"时，靠渲染裁图数是**错的路**——
  实测在同一张图上用像素暗度阈值、圆图元聚类等方法试了十几次，
  要么数错、要么被噪音压倒（该图圆图元 4151 个，其中大部分是标号圆）。

  正解是不看渲染图，直接读矢量图元坐标：
  全图 1362 个矩形里，**恰好 24 个是正方形且边长 23.64pt**——
  这24 个排成 3 行 x 8 列的规则点阵，一眼就是"3 个承台 x 每台 8 个"或"两排各 4 个"。
  噪声被尺寸+长宽比唯一筛掉，结论可复现。

用法：
  # ★ 判型：先看每页有没有文字层（毫秒级，这是第 1 步的第一条命令）
  python extract_dwg_vector.py <图纸.pdf> --list
  # 看某尺寸/形状的图元有多少、怎么分布
  python extract_dwg_vector.py <图纸.pdf> --shape square --size 23.64
  # 看所有近圆图元
  python extract_dwg_vector.py <图纸.pdf> --shape circle --max 40
  # 按坐标网格聚类（把散点归成阵列）
  python extract_dwg_vector.py <图纸.pdf> --shape square --grid 40
退出码：正常 0；文件读不了 2

注意：轮廓化 PDF（文字变成线条）读不出文字，图形坐标仍完整可用。
      `--list` 的 text 只数**文字层字符**，与"有没有标注"不等价：
      文字层残缺（部分转曲）时 text>0 但仍有大量尺寸要靠图元取证，
      详见 references/drawing-reading-playbook.md §0 判型矩阵。
"""
import sys, os, argparse, math, collections

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



def list_pages(path, count_drawings=False):
    """判型：逐页打印文字层/字体/图片/图元，用于选取证路线。

    `get_cdrawings()` 比 `get_drawings()` 快一个数量级，但仍要遍历图元，
    37 万图元的 A1 页约 8~14 s/页，故默认不数，只有 --count 才数。
    """
    import pymupdf
    doc = pymupdf.open(path)
    print('文件：%s   页数：%d' % (os.path.basename(path), doc.page_count))
    print('%-4s %-14s %-8s %-8s %-10s %-8s %s'
          % ('页', '图幅(pt)', '文字数', '字体', '图片', '图元', '判型'))
    for i, page in enumerate(doc, 1):
        txt = page.get_text('text') or ''
        n_txt = len(txt.strip())
        n_font = len(page.get_fonts())
        n_img = len(page.get_images())
        if count_drawings:
            n_dr = len(page.get_cdrawings())
            dr_s = str(n_dr)
            if n_txt and n_dr > 20000:
                kind = '矢量+文字层(标注可能残缺)'
            elif n_txt:
                kind = '矢量+文字层'
            elif n_dr:
                kind = '矢量·无文字层(转曲)'
            else:
                kind = '无图元(可能是扫描图)'
        else:
            dr_s = '—'
            if n_txt:
                kind = '有文字层→先走文本层'
            elif n_img:
                kind = '无文字层+有图片→扫描件路线'
            else:
                kind = '无文字层→矢量取证路线'
        print('%-4d %-14s %-8d %-8d %-10d %-8s %s'
              % (i, '%.0fx%.0f' % (page.rect.width, page.rect.height),
                 n_txt, n_font, n_img, dr_s, kind))
    doc.close()
    print('\n读法：文字数>0 且图元很多 → 文本层打底 + 图元补缺口（不要二选一）；')
    print('      文字数=0 而图元上千 → 文字转曲，走矢量取证；提高渲染倍数或反复 OCR 都救不回来。')
    print('      --count 可同时数图元（慢，每页可达十几秒）。')
    return 0


def load_drawings(path):
    import pymupdf
    doc = pymupdf.open(path)
    out = []
    for pno, page in enumerate(doc):
        for d in page.get_drawings():
            out.append((pno, d['rect'], d.get('items') or []))
    return out


def classify(drawings, shape, size_tol, ratio_tol):
    hit = []
    for pno, r, items in drawings:
        w, h = r.width, r.height
        if w < 0.05 or h < 0.05:
            continue
        ratio = abs(w - h) / max(w, h)
        if shape == 'square':
            ok = ratio < ratio_tol
        elif shape == 'rect':
            ok = ratio >= ratio_tol
        elif shape == 'circle':
            # 真正的圆：窄矩形里塞了 4 段贝塞尔
            ok = ratio < ratio_tol and any(it[0] == 'c' for it in items)
        else:
            ok = True
        if not ok:
            continue
        if size_tol > 0 and abs(w - size_tol) > size_tol * 0.02 + 0.01:
            continue
        hit.append((pno, round(r.x0, 2), round(r.y0, 2), round(w, 3), round(h, 3)))
    return hit


def cluster_grid(hit, tol):
    """把点聚成网格阵列，识别行列结构。"""
    xs = sorted({h[1] for h in hit})
    ys = sorted({h[2] for h in hit})
    def group(vals, t):
        out, cur = [], [vals[0]]
        for v in vals[1:]:
            if v - cur[-1] <= t:
                cur.append(v)
            else:
                out.append(cur); cur = [v]
        out.append(cur)
        return [sum(g) / len(g) for g in out]
    gx = group(xs, tol)
    gy = group(ys, tol)
    grid = collections.Counter()
    for pno, x, y, w, h in hit:
        cx = min(range(len(gx)), key=lambda i: abs(gx[i] - x))
        cy = min(range(len(gy)), key=lambda i: abs(gy[i] - y))
        grid[(cy, cx)] += 1
    return gx, gy, grid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pdf')
    ap.add_argument('--shape', default='square', choices=['square', 'rect', 'circle', 'all'])
    ap.add_argument('--size', type=float, default=0.0, help='按边长筛，0=不限')
    ap.add_argument('--ratio-tol', type=float, default=0.15)
    ap.add_argument('--grid', type=float, default=0.0, help='网格聚类容差，>0 启用')
    ap.add_argument('--page', type=int, default=0, help='只看第几页，从 0 起')
    ap.add_argument('--list', action='store_true',
                    help='只列每页性质（判型用，毫秒级）——第 1 步的第一条命令')
    ap.add_argument('--count', action='store_true',
                    help='--list 时同时数图元（慢，A1 大图约 8~14 s/页）')
    a = ap.parse_args()

    if not os.path.isfile(a.pdf):
        print('状态：unavailable —— 找不到文件 %s' % a.pdf)
        return 2
    if a.list:
        try:
            return list_pages(a.pdf, a.count)
        except Exception as e:
            print('状态：unavailable —— 读取失败：%s' % e)
            return 2
    try:
        drawings = load_drawings(a.pdf)
    except Exception as e:
        print('状态：unavailable —— 读取失败：%s' % e)
        print('提示：轮廓化 PDF 用 pymupdf 读矢量坐标一般没问题；若确实读不到，改用高倍渲染读图')
        return 2

    if a.page:
        drawings = [d for d in drawings if d[0] == a.page]

    hit = classify(drawings, a.shape, a.size, a.ratio_tol)
    print('文件：%s' % os.path.basename(a.pdf))
    print('图元总数：%d' % len(drawings))
    print('筛出 %s（size=%s）：%d 个' % (a.shape, a.size or '不限', len(hit)))

    if not hit:
        print('\n没有筛到。换shape 或调 --size（先跑 --shape all --grid 看全图尺寸分布）')
        return 0

    sizes = collections.Counter(h[3] for h in hit)
    print('\n尺寸分布（前 10）：')
    for s, n in sizes.most_common(10):
        print('  边长 %.3f pt→ %d 个' % (s, n))

    if a.grid > 0:
        gx, gy, grid = cluster_grid(hit, a.grid)
        print('\n网格聚类：%d 行 x %d 列（容差 %.1f pt）' % (len(gy), len(gx), a.grid))
        print('  行的y：%s' % ', '.join('%.1f' % v for v in gy[:8]))
        print('  列的 x：%s' % ', '.join('%.1f' % v for v in gx[:10]))
        rowcount = collections.Counter(k[0] for k in grid)
        print('  每行点数：%s' % ', '.join(str(rowcount[i]) for i in range(len(gy))))
        print('\n排布示意（·=一个图元）：')
        for r in range(len(gy)):
            line = ''.join('·' if grid.get((r, c)) else ' ' for c in range(len(gx)))
            print('    %s  (行%d, %d 个)' % (line, r, rowcount.get(r, 0)))
        print('\n★读法：先数行（=几排/几个构件），再数列（=每排几个）。')
        print('  若行数=2 且两排列结构相同，多半是"双幅/上下行"，'
              '总数要按两排合计，不要只算一排。')
    else:
        print('\n加 --grid 40 可以看这些图元排成几行几列（判断阵列结构用）')
    return 0


if __name__ == '__main__':
    sys.exit(main())