# -*- coding: utf-8 -*-
"""
基准时效门禁（1.2.0 新增 · G-FRESH）

**抓过的事故（2026-10-20 本项目实测）**
"图纸基准核对"门禁当时**全绿 4 项**。但同一天落表修正把承台量从 14,582 改成
1,458.24，基准文件里仍是 14582 —— 之所以还是绿的，是因为那次改的是**另外几行**，
基准项没被触到。等真正改到基准项时，"基准过期"与"数值不符"会混成同一个红，
人分不清"是我读错图"还是"是我忘了更新基准"。

**它和 gate_drawing_basis 的分工（两个都要跑，顺序不能反）**
  G-FRESH（本门禁）：基准**本身**可不可信——写完多久了？覆盖够不够？版本对齐没有？
  gate_drawing_basis：拿可信的基准去比数值（G-E）。
  ★先跑 G-FRESH：基线不可信时，比出来的"通过"是假的。

**门禁读的是第二份真相。基线不同步时，它越"通过"越危险。**

判据
  G-FR1 [FAIL] 缺 verified_at（基准最近一次人工复核时间）→ 无时效锚
  G-FR2 [FAIL] 算量表 mtime 晚于 verified_at → 表在基准复核后被改过，必须重新复核
               （--allow-newer 降为 WARN；但降级要在交付说明里写明）
  G-FR3 [FAIL] item 缺 basis 或 basis 写成"按图/按图纸" → 出处不可查
  G-FR4 [WARN] 有效基准项覆盖不到 --min-coverage（默认 0.5）的数据行
               → 其余行没有图纸锚点，"全部通过"只覆盖了半张表
  G-FR5 [WARN] item 缺 asof（该数字对应哪一版图纸）→ 无法做版本裁决
  G-FR6 [FAIL] 基准缺 drawing_set_rev（这批基准对应哪一版图册）
               → 澄清函/Addendum 下发新版图后，无法判断要不要重读

用法
----
    python gate_basis_freshness.py <算量表.xlsx> --basis <基准.json>
        [--sheet 1_成本取量总表] [--allow-newer] [--min-coverage 0.5] [--init <模板>]
退出码：0 通过（含 WARN）／1 有 FAIL／2 门禁自身执行失败
"""
import argparse
import datetime as dt
import io
import json
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
# v2.1.1: 删除了 sys.stdout.reconfigure(encoding='utf-8')——它会推翻上面的
# errors="replace" 兜底，控制台按 GBK 解释 utf-8 字节，中文变乱码。

PLACEHOLDER_BASIS = ('', '按图', '按图纸', '按图纸测量', '见附图', '暂定')


def load_json(p):
    with io.open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def parse_time(s):
    """接受 '2026-10-20' / '2026-10-20 15:56' / ISO8601。返回 datetime 或 None。"""
    if not s:
        return None
    s = str(s).strip().replace('/', '-').replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(s[:len(fmt) + 2].strip(), fmt)
        except Exception:
            continue
    try:
        return dt.datetime.fromisoformat(str(s))
    except Exception:
        return None


def count_data_rows(ws, code_col=2, qty_col=5):
    n = 0
    for r in range(1, ws.max_row + 1):
        if str(ws.cell(r, code_col).value or '').strip() and \
           isinstance(ws.cell(r, qty_col).value, (int, float)):
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description='基准时效门禁（G-FRESH）')
    ap.add_argument('xlsx', nargs='?')
    ap.add_argument('--sheet', default='1_成本取量总表')
    ap.add_argument('--basis')
    ap.add_argument('--init', metavar='OUT', help='生成基准文件模板（含时效字段）')
    ap.add_argument('--allow-newer', action='store_true',
                    help='表比基准新时只 WARN（默认 FAIL）——降级须写进交付说明')
    ap.add_argument('--min-coverage', type=float, default=0.5)
    ap.add_argument('--skip-mtime', action='store_true',
                    help='不查文件 mtime（用于文件被复制/解压后 mtime 失真的场合）')
    a = ap.parse_args()

    if a.init:
        tpl = {
            "drawing_set_rev": "Addendum-2（2026-10-15 下发）",
            "verified_at": "2026-10-20 15:56",
            "verified_by": "＿＿＿＿＿＿（签字，AI 不代签）",
            "note": "drawing_set_rev = 这批基准对应的图册版本；"
                    "verified_at = 最近一次人工复核基准的时间（改表后必须更新）；"
                    "每个 item 的 asof = 该读数所在图纸的版本日期。",
            "items": [
                {"code": "CBS-BRG-P03", "qty": 1458.24, "unit": "CM",
                 "basis": "HA-035111.02 承台平面 21700×7000，厚 1600（SECTION）",
                 "asof": "2026-09-28", "tolerance": 0.005}
            ]
        }
        with io.open(a.init, 'w', encoding='utf-8') as f:
            json.dump(tpl, f, ensure_ascii=False, indent=2)
        print('基准模板已生成：%s' % a.init)
        return 0

    if not a.xlsx or not a.basis:
        print('用法：gate_basis_freshness.py <算量表.xlsx> --basis <基准.json>'
              '（或 --init 生成模板）')
        return 2
    if not os.path.isfile(a.xlsx):
        print('[FAIL] G-FR0 找不到算量表 %s｜门禁**未能执行**' % a.xlsx)
        return 2
    if not os.path.isfile(a.basis):
        print('[FAIL] G-FR0 找不到基准文件 %s｜门禁**未能执行**' % a.basis)
        return 2

    try:
        data = load_json(a.basis)
    except Exception as e:
        print('[FAIL] G-FR0 基准文件解析失败：%s｜门禁**未能执行**' % e)
        return 2
    if not isinstance(data, dict):
        print('[FAIL] G-FR0 基准文件顶层必须是对象｜门禁**未能执行**')
        return 2

    import openpyxl
    try:
        wb = openpyxl.load_workbook(a.xlsx, data_only=False)
        if a.sheet not in wb.sheetnames:
            print('[FAIL] G-FR0 页签不存在：%s｜可选：%s'
                  % (a.sheet, '、'.join(wb.sheetnames)))
            return 2
        ws = wb[a.sheet]
        n_rows = count_data_rows(ws, 2, 5)
    except Exception as e:
        print('[FAIL] G-FR0 门禁执行失败：%s' % e)
        return 2

    fails, warns, notes = [], [], []
    items = data.get('items') or []

    # ---- 零数据行守卫（v2.1.1）----
    # 空表（或 --sheet 指错）时，G-FR2 的"表比基准新"必然成立（文件刚生成，
    # mtime 晚于 verified_at），于是判 exit 1「基线不可信」。这是**假红**：
    # 表里什么都没有，"表改过"毫无意义。判「未运行」而非「不通过」——
    # 假红的代价是训练人忽略整条门禁（实测 G-FRESH 就这样被绕过过一次）。
    if n_rows == 0:
        print('基准时效门禁 G-FRESH | 表：%s | 基准：%s'
              % (os.path.basename(a.xlsx), os.path.basename(a.basis)))
        print('[未执行] 扫描到 **0 行数据行**（编码+工程量为数值的行）。')
        print('         没有可核对的量，覆盖率与"表是否晚于基准"都失去意义；'
              '此时的"基线过期"是假红。')
        print('         先核对 --sheet，或确认这张表是否还没填。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2

    # ── G-FR1/G-FR6 基准自身的时间锚与版本锚 ──
    v_at = parse_time(data.get('verified_at'))
    if not v_at:
        fails.append('G-FR1 缺 verified_at（或格式无法解析）：%r → 基准没有时效锚，'
                     '改了表也不会有人知道基准该重看了' % data.get('verified_at'))
    if not str(data.get('drawing_set_rev') or '').strip():
        fails.append('G-FR6 缺 drawing_set_rev：这批基准对应哪一版图册？'
                     '澄清函/Addendum 下发新版图后，无法判断要不要重读')

    # ── G-FR2 表比基准新 ──
    if v_at and not a.skip_mtime:
        try:
            m = dt.datetime.fromtimestamp(os.path.getmtime(a.xlsx))
            if m > v_at + dt.timedelta(minutes=1):
                msg = ('G-FR2 算量表最后改动 %s 晚于基准复核 %s → '
                       '表在基准复核之后被改过，**基准已过期**'
                       % (m.strftime('%Y-%m-%d %H:%M'), v_at.strftime('%Y-%m-%d %H:%M')))
                if a.allow_newer:
                    warns.append(msg + '（--allow-newer 已降级，须在交付说明中写明）')
                else:
                    fails.append(msg + '｜复核后把 verified_at 更新到本次复核时间')
            else:
                notes.append('表未晚于基准复核时间（%s ≤ %s）'
                             % (m.strftime('%Y-%m-%d %H:%M'), v_at.strftime('%Y-%m-%d %H:%M')))
        except Exception as e:
            warns.append('G-FR2 无法读取文件 mtime：%s' % e)

    # ── G-FR3/G-FR5 逐条出处与版本日期 ──
    no_basis, no_asof, valid = [], [], 0
    for it in items:
        code = it.get('code', '?')
        b = str(it.get('basis') or '').strip()
        if b in PLACEHOLDER_BASIS or b.startswith('按图'):
            no_basis.append(code)
        if not str(it.get('asof') or '').strip():
            no_asof.append(code)
        q = it.get('qty')
        if isinstance(q, (int, float)) and q != 0 and b not in PLACEHOLDER_BASIS:
            valid += 1
    if no_basis:
        fails.append('G-FR3 基准未写出处 %d 条：%s → 写"图号 + 图面位置"，不接受"按图"'
                     % (len(no_basis), '；'.join(map(str, no_basis[:6]))))
    if no_asof:
        warns.append('G-FR5 基准缺 asof（该读数对应的图纸版本日期）%d 条：%s '
                     '→ 澄清函下发新版图后无法逐条判断要不要重读'
                     % (len(no_asof), '；'.join(map(str, no_asof[:6]))))

    # ── G-FR4 覆盖率 ──
    if n_rows:
        cov = valid / float(n_rows)
        if cov < a.min_coverage:
            warns.append('G-FR4 有效基准 %d 条 / 数据行 %d 条 = 覆盖率 %.0f%% < %.0f%% '
                         '→ 其余 %d 行没有图纸锚点；"基准全过"只覆盖了 %.0f%% 的表，'
                         '不能当成整表已核对'
                         % (valid, n_rows, cov * 100, a.min_coverage * 100,
                            n_rows - valid, cov * 100))
        else:
            notes.append('基准覆盖率 %.0f%%（%d/%d 行）' % (cov * 100, valid, n_rows))
    else:
        warns.append('G-FR4 表中未识别到数据行，覆盖率无法计算')

    # ── 输出 ──
    print('基准时效门禁 G-FRESH | 表：%s | 基准：%s'
          % (os.path.basename(a.xlsx), os.path.basename(a.basis)))
    print('  图册版本 %s ｜ 基准复核 %s ｜ 有效基准 %d 条 / 数据行 %d'
          % (data.get('drawing_set_rev') or '（未写）',
             data.get('verified_at') or '（未写）', valid, n_rows))
    for n in notes:
        print('[INFO] %s' % n)
    for w in warns:
        print('[WARN] %s' % w)
    for f in fails:
        print('[FAIL] %s' % f)

    if fails:
        print('!! 未通过：基线本身不可信。先复核基准，再跑 gate_drawing_basis 比数值。')
        print('   ★顺序不能反：基线过期时，"数值全部相符"是假通过。')
        return 1
    print('== 基准时效门禁通过（WARN 项需人工确认）==')
    return 0


if __name__ == '__main__':
    sys.exit(main())
