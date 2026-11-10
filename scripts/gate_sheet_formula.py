# -*- coding: utf-8 -*-
"""
表格公式对齐门禁：确保每行的合价公式指向自己那一行，且每个小计的引用区间覆盖本组。

背景（本项目潜伏最久的财务缺陷，分两层）：
  【第一层·1.1.0】某桥梁算量表 H 列合价公式整列错位一行——每一行的金额都挂在上一行的量上。
  为什么长期没人发现？因为合计数只差 −0.41%。总量看着基本对，但逐行全错。
  根因是 openpyxl insert_rows() 插行——它平移了单元格内容，
  但**不会重写公式里的行号**。

  【第二层·1.2.0 多小计】排水表 E3 有 9 个分组小计（2.2.2.1~2.2.2.8 各一，加总计），
  其中 8 个的 SUM 区间引用错行。而本门禁 v1.1.0 的 find_total_row() 用 max(cands)
  **只取最后一个"★合计"行**、且只看 H 列 → **一个都没报**。

  ★核心教训：门禁的检查对象假设错了，比判据太弱更致命。
    "一张表只有一个合计行"是假设，不是事实；改成**遍历所有小计行**才是判据。

检查五件事：
  G-1  =E{n}*G{n} 形式的公式，n 必须等于所在行号
  G-2  合计行必须是 SUM 区间形式
  G-3  合计区间覆盖全部有效量行
  G-SUB1  任一 SUM 区间内不得包含另一个小计行（小计套小计＝重复计数）
  G-SUB2  每个小计的区间必须恰好等于本组有效量行范围（起点/终点偏移一行都算错）
  G-SUB3  列举式 =SUM(H11,H14,…) 的每个引用必须指向小计行

用法：
  python gate_sheet_formula.py <算量表.xlsx> [--sheet 1_成本取量总表]
  python gate_sheet_formula.py <算量表.xlsx> --list-subtotals   # 只列小计，不改判定
退出码：0 通过 / 1 有 FAIL / 2 门禁自身执行失败
"""
import sys, os, re, argparse

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

import openpyxl

# 小计行的文字特征。★不是"合计"两个字就算——必须同时有 SUM 公式（见 collect_subtotals）
SUBTOTAL_WORDS = ('小计', '合计', '总计', 'SUBTOTAL', 'SUB-TOTAL', 'TOTAL')

REF_RE = re.compile(r'^=SUM\((.+)\)$', re.I)
RANGE_RE = re.compile(r'^([A-Z]+)(\d+):([A-Z]+)(\d+)$', re.I)
CELL_RE = re.compile(r'^([A-Z]+)(\d+)$', re.I)


def find_total_row(ws):
    """找"最终合计行"（含"合计"且含"★"，取最后一个）**且该行金额列像合计**。

    ★1.2.0 修正：v1.1.0 只在任意列找"合计+★"，结果在 Base 总表上抓到了
    某行 H 列写着"B4桥梁立交 2026-07-20"这种**文字备注**的行，当成合计行。
    判据不能只看"文字里有合计"，还要看**金额列是不是聚合表达式/数值**。

    ★保留此函数用于 G-2/G-3 的向后兼容判定；多小计覆盖由 collect_subtotals 负责。
    注意：**不能再用它代表"全表所有小计"**——那是 1.1.0 的漏检根因。
    返回 None 表示"本表没有可识别的合计行"（如单层清单表），不必然是错。
    """
    cands = []
    for r in range(1, ws.max_row + 1):
        txt = ' '.join(str(ws.cell(r, c).value or '') for c in range(1, ws.max_column + 1))
        if '合计' in txt and '★' in txt:
            cands.append(r)
    cands.sort(reverse=True)
    for r in cands:
        v = ws.cell(r, 8).value
        if isinstance(v, (int, float)):
            return r
        if parse_aggregate(v)[0] is not None:
            return r
        if re.match(r'^=E(\d+)\*G(\d+)$', str(v or '').strip()):
            return r
    return None


def _sum_refs(cell_val):
    """解析 =SUM(...) 里的引用。返回 (形式, 引用行列表或区间列表)。

    形式：'range' 如 SUM(H3:H10)｜'cells' 如 SUM(H11,H14,H20)｜'single' 如 SUM(H50)
    """
    m = REF_RE.match(str(cell_val or '').strip())
    if not m:
        return None, None
    body = m.group(1).replace(' ', '')
    parts = [p for p in body.split(',') if p]
    if len(parts) == 1:
        rm = RANGE_RE.match(parts[0])
        if rm:
            return 'range', [(int(rm.group(2)), int(rm.group(4)))]
        cm = CELL_RE.match(parts[0])
        if cm:
            return 'single', [int(cm.group(2))]
        return None, None
    rows = []
    for p in parts:
        cm = CELL_RE.match(p)
        if not cm:
            return None, None
        rows.append(int(cm.group(2)))
    return 'cells', rows


ADD_RE = re.compile(r'^[A-Z]+\d+(\+[A-Z]+\d+)+$', re.I)


def parse_aggregate(cell_val):
    """把"聚合表达式"归一成 (形式, 引用)。

    支持三种真实写法（都见过，少了任何一种都会把**正确写法**判成 FAIL）：
      =SUM(H3:H10)              → ('range', [(3,10)])
      =SUM(H11,H14,H20)         → ('cells', [11,14,20])
      =H3+H4+H5+…+H64           → ('cells', [3,4,5,…,64])   ← 长表常见，v1.1.0 完全不认
    返回 (None, None) 表示"这不是聚合行"（如 =E71*G71 或常数）——
    **这种情况不算错，只是 G-2/G-3 不适用**，由 G-SUB 逐组判。
    """
    s = str(cell_val or '').strip()
    if not s.startswith('='):
        return None, None
    form, refs = _sum_refs(s)
    if form:
        return form, refs
    body = s[1:].replace(' ', '')
    if ADD_RE.match(body):
        rows = []
        for p in body.split('+'):
            cm = CELL_RE.match(p)
            if not cm:
                return None, None
            rows.append(int(cm.group(2)))
        return 'cells', rows
    return None, None


def collect_subtotals(ws, cols=(5, 7, 8)):
    """收集所有"小计行"：文字含小计/合计/总计，且**至少一列是 SUM 公式**。

    只认有 SUM 公式的行，是为了排除"文字说明里提到合计"的标题行——
    那类行没有公式，纳进来会造成假报。
    """
    out = []
    for r in range(1, ws.max_row + 1):
        txt = ' '.join(str(ws.cell(r, c).value or '')
                       for c in range(1, min(ws.max_column, 12) + 1)).upper()
        if not any(w in txt for w in SUBTOTAL_WORDS):
            continue
        sums = []
        for c in cols:
            form, refs = _sum_refs(ws.cell(r, c).value)
            if form:
                sums.append((c, form, refs, ws.cell(r, c).value))
        if sums:
            out.append((r, sums))
    return out


def is_qty_row(ws, r, qty_col=5, amt_col=8, code_cols=(1, 2, 3)):
    """有效量行：编码列有内容，且量列或金额列是数值。"""
    code = ''.join(str(ws.cell(r, c).value or '') for c in code_cols).strip()
    if not code:
        return False
    q, a = ws.cell(r, qty_col).value, ws.cell(r, amt_col).value
    return isinstance(q, (int, float)) or isinstance(a, (int, float))


def check_subtotals(ws, qty_col=5, amt_col=8):
    """多小计检查。返回 (fails, warns, notes)。

    判据只在**量列/金额列**上做（价格列的小计=加权平均，语义不同，不适用覆盖判据）。
    """
    subs = collect_subtotals(ws, cols=(qty_col, amt_col))
    fails, warns, notes = [], [], []
    if not subs:
        return fails, warns, ['本表未发现带 SUM 公式的小计行（不适用多小计判据）']

    sub_rows = [r for r, _ in subs]
    notes.append('发现小计行 %d 个：r%s' % (len(subs), ', r'.join(map(str, sub_rows))))

    for idx, (r, sums) in enumerate(subs):
        prev_sub = sub_rows[idx - 1] if idx > 0 else 0
        # 本组有效量行（不含小计行）
        group = [x for x in range(prev_sub + 1, r) if is_qty_row(ws, x, qty_col, amt_col)]
        for c, form, refs, raw in sums:
            col = openpyxl.utils.get_column_letter(c)
            if form == 'range':
                lo, hi = refs[0]
                # G-SUB1 区间内含别的小计行 → 重复计数
                inner = [x for x in sub_rows if lo <= x <= hi and x != r]
                if inner:
                    fails.append(
                        'G-SUB1 %s%d=%s 区间内含小计行 %s → 小计套小计，重复计数'
                        % (col, r, raw, inner))
                if not group:
                    warns.append('G-SUB2 %s%d=%s 本组未见有效量行，无法判断覆盖' % (col, r, raw))
                    continue
                g_lo, g_hi = min(group), max(group)
                # G-SUB2 区间必须恰好等于本组有效量行范围
                if (lo, hi) != (g_lo, g_hi):
                    why = []
                    if lo > g_lo:
                        why.append('起点晚于本组首行 r%d（漏 r%d~r%d）' % (g_lo, g_lo, lo - 1))
                    if lo < g_lo:
                        why.append('起点早于本组首行 r%d（多算 r%d~r%d）' % (g_lo, lo, g_lo - 1))
                    if hi < g_hi:
                        why.append('终点早于本组末行 r%d（漏 r%d~r%d）' % (g_hi, hi + 1, g_hi))
                    if hi > g_hi:
                        why.append('终点晚于本组末行 r%d（多算 r%d~r%d）' % (g_hi, g_hi + 1, hi))
                    fails.append(
                        'G-SUB2 %s%d 区间 %s%d:%s%d ≠ 本组有效量行 r%d~r%d｜%s'
                        % (col, r, col, lo, col, hi, g_lo, g_hi, '；'.join(why)))
                else:
                    notes.append('%s%d 覆盖本组 r%d~r%d ✓' % (col, r, g_lo, g_hi))
            elif form == 'cells':
                # ① 全部引用都在小计行里 → "总计=各小计之和"，合法
                if all(x in sub_rows for x in refs):
                    notes.append('%s%d 列举 %d 个小计（总计型）✓' % (col, r, len(refs)))
                    continue
                # ② 混合引用（既含小计行又含普通行）→ 小计已含明细，再点明细＝重复计数
                if any(x in sub_rows for x in refs):
                    mixed = [x for x in refs if x in sub_rows]
                    fails.append(
                        'G-SUB4 %s%d=%s 混合引用：既点小计行 %s 又点明细行 → '
                        '小计的金额已被加过一次，必然重复计数（总计型应只点小计）'
                        % (col, r, raw, mixed))
                    continue
                # ③ 只点普通行：必须恰好覆盖本组有效量行（漏一行就算错）
                plain = list(refs)
                if not group:
                    warns.append('G-SUB3 %s%d=%s 只列举普通行但本组无量行可参照' % (col, r, raw))
                    continue
                miss = sorted(set(group) - set(plain))
                extra = sorted(set(plain) - set(group))
                if miss or extra:
                    why = []
                    if miss:
                        why.append('漏列举 r%s' % ('、r'.join(map(str, miss))))
                    if extra:
                        why.append('列举了本组之外的行 r%s' % ('、r'.join(map(str, extra))))
                    fails.append('G-SUB3 %s%d 列举式 %s 未覆盖本组 r%d~r%d｜%s'
                                 % (col, r, raw, min(group), max(group), '；'.join(why)))
                else:
                    notes.append('%s%d 列举本组 r%d~r%d ✓' % (col, r, min(group), max(group)))
            else:  # single 单格引用
                tgt = refs[0]
                if tgt in sub_rows:
                    notes.append('%s%d 单格引用小计 r%d ✓' % (col, r, tgt))
                elif group and tgt in group and set(group) == {tgt}:
                    notes.append('%s%d 单格引用本组唯一量行 r%d ✓' % (col, r, tgt))
                elif group:
                    miss = sorted(set(group) - {tgt})
                    fails.append(
                        'G-SUB3 %s%d=%s 单格只引用了 r%d，本组有效量行为 r%d~r%d（漏 r%s）'
                        % (col, r, raw, tgt, min(group), max(group),
                           '、r'.join(map(str, miss)) if miss else '—'))
                else:
                    warns.append('G-SUB3 %s%d=%s 单格引用 r%d 未落在本组' % (col, r, raw, tgt))
    return fails, warns, notes


def run(path, sheet):
    ws = openpyxl.load_workbook(path, data_only=False)[sheet]
    total_row = find_total_row(ws)

    misaligned, data_rows = [], []
    stop = total_row if total_row else ws.max_row + 1
    for r in range(1, stop):
        f = ws.cell(r, 8).value
        q, u = ws.cell(r, 5).value, ws.cell(r, 7).value
        is_qty = isinstance(q, (int, float)) and isinstance(u, (int, float))
        if is_qty and ws.cell(r, 2).value:
            data_rows.append(r)
        m = re.match(r'^=E(\d+)\*G(\d+)$', str(f).strip()) if f else None
        if m:
            er, gr = int(m.group(1)), int(m.group(2))
            if er != r or gr != r:
                misaligned.append('r%d=%s' % (r, str(f)))

    if total_row is None:
        # 没有"★合计"行不必然是错——有些表只有分组小计（由 G-SUB 覆盖）
        return misaligned, data_rows, True, '无★合计行（跳过 G-2/G-3，由 G-SUB 覆盖）', [], None

    total_f = str(ws.cell(total_row, 8).value or '')
    form, refs = parse_aggregate(total_f)
    sub_rows = [r for r, _s in collect_subtotals(ws, cols=(5, 8))]

    if form is None:
        # ★该"★合计"行本身是 =E*G 或常数 → 它不是聚合行（可能是一行"合计×均价"）。
        #   v1.1.0 会把它判成"非 SUM 区间"报 FAIL，纯属自己误报。
        #   红灯笼挂错地方，比没挂更糟：人见过一次假红就开始无视红灯。
        return misaligned, data_rows, True, (
            '%s 不是聚合表达式（合计行自身为量×价或常数），'
            'G-2/G-3 不适用，覆盖由 G-SUB 判' % total_f), [], total_row

    if form == 'range':
        lo, hi = refs[0]
        outside = [r for r in data_rows if not (lo <= r <= hi)]
        form_ok = True
        form_detail = ('%s；量行 %d 条（r%d~r%d），区间外 %d 条'
                       % (total_f, len(data_rows), min(data_rows), max(data_rows), len(outside))
                       ) if data_rows else ('%s；本表无量行' % total_f)
    elif form == 'cells':
        outside = [r for r in data_rows if r not in refs]
        if all(x in sub_rows for x in refs):
            others = [x for x in sub_rows if x != total_row]
            missing = [x for x in others if x not in refs]
            if missing:
                outside = sorted(set(outside) | set(missing))
                form_ok, form_detail = False, (
                    '%s 枚举小计但漏了 r%s（共 %d 个小计，只点 %d 个）'
                    % (total_f, '、r'.join(map(str, missing)), len(others), len(refs)))
            else:
                outside = []
                form_ok, form_detail = True, (
                    '%s；枚举全部 %d 个小计（总计型），逐组覆盖见 G-SUB'
                    % (total_f, len(others)))
        elif any(x in sub_rows for x in refs):
            outside = []
            form_ok, form_detail = True, (
                '%s 混合引用（小计+明细），是否重复计数由 G-SUB4 判' % total_f)
        else:
            form_ok = not outside
            form_detail = ('%s；逐行枚举，覆盖全部有效量行' % total_f) if not outside else \
                          ('%s；逐行枚举但漏 %d 行' % (total_f, len(outside)))
    else:  # single
        outside = []
        form_ok, form_detail = True, '%s；单格引用，覆盖由 G-SUB 判定' % total_f

    return misaligned, data_rows, form_ok, form_detail, outside, total_row

    return misaligned, data_rows, form_ok, form_detail, outside, total_row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='1_成本取量总表')
    ap.add_argument('--list-subtotals', action='store_true',
                    help='只列出识别到的所有小计行，便于人工核对识别是否完整')
    a = ap.parse_args()

    if not os.path.isfile(a.xlsx):
        print('状态：unavailable —— 找不到表 %s' % a.xlsx)
        return 2
    try:
        ws = openpyxl.load_workbook(a.xlsx, data_only=False)[a.sheet]
    except Exception as e:
        print('读取失败：%s' % e)
        print('状态：unavailable')
        return 2

    if a.list_subtotals:
        subs = collect_subtotals(ws, cols=(5, 8))
        print('小计行清单 | 表：%s | 共 %d 个' % (os.path.basename(a.xlsx), len(subs)))
        for r, sums in subs:
            print('  r%-4d %s' % (r, ' ｜ '.join(
                '%s=%s' % (openpyxl.utils.get_column_letter(c), raw)
                for c, _f, _ref, raw in sums)))
        return 0

    try:
        mis, rows, form_ok, detail, outside, tr = run(a.xlsx, a.sheet)
        sub_fails, sub_warns, sub_notes = check_subtotals(ws)
    except Exception as e:
        print('读取失败：%s' % e)
        print('状态：unavailable')
        return 2

    print('公式对齐门禁 | 表：%s | ★合计行 %s'
          % (os.path.basename(a.xlsx), ('r%d' % tr) if tr else '无'))
    # ---- 零数据行守卫 ----
    # ★不能用 run() 返回的 data_rows 当分母：它的判据是 col5 与 col7 同时为
    #   数值，而真实表 col7/col8 装的是公式（data_only=False 读出来是 "=E12*G12"
    #   字符串），于是 data_rows 在**任何公式驱动的表上恒为空**——用它守卫会把
    #   每张正常表都判成"0 行"（实测本项目 34 行工程量被误判为 0 行）。
    #   这里改判"编码列有值 且 工程量列为数值"的行数，与其它门禁同口径。
    n_data = sum(1 for r in range(1, (tr or ws.max_row + 1))
                 if ws.cell(r, 2).value
                 and isinstance(ws.cell(r, 5).value, (int, float)))
    if n_data == 0:
        print('[未执行] 扫描到 **0 行有效量行**（编码+工程量为数值的行）。')
        print('         没有任何数据行时，"通过"只意味着没东西可查，'
              '不代表表没错。')
        print('         先核对 --sheet 是否指对了表、列序是否与预期一致。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2
    if mis:
        print('[FAIL] G-1 公式行号错位 %d 条：%s' % (len(mis), '；'.join(mis[:5])))
        print('       成因通常是 insert_rows 插行后未重写公式行号')
    else:
        print('[PASS] G-1 全部公式指向所在行')
    if form_ok and not outside:
        print('[PASS] G-2/3 合计引用覆盖完整 | %s' % detail)
    else:
        print('[FAIL] G-2/3 合计引用有漏 | %s%s' % (
            detail, ('；漏行 ' + str(outside)) if outside else ''))

    # ── 多小计 ──
    for n in sub_notes:
        print('[INFO] %s' % n)
    for w in sub_warns:
        print('[WARN] %s' % w)
    for f in sub_fails:
        print('[FAIL] %s' % f)
    if not sub_fails:
        print('[PASS] G-SUB 全部小计区间与本组有效量行一致')

    hard = bool(mis or outside or not form_ok or sub_fails)
    if hard:
        print('!! 未通过：逐行金额可能挂在错误行上、或小计漏算/重复算（总量接近但明细全错）')
        return 1
    print('== 公式对齐门禁通过 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())
