#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方案级口径分离门禁（1.1.0 新增 · G-PROP）

**抓过的事故**
可选项（OPTIONS，CPB 第 4 类）2390 万混进了基础方案直接费。
它是**最后**被发现的——因为：
- 成本同步脚本把所有「量×价」直接累加，没有区分方案归属
- 每一行的金额单独看都是对的
- 交叉复核与公式门禁全都通过（它们不判方案归属）

**为什么这类错比算量错更隐蔽**
算量错会体现在某一行的金额上，容易发现；
口径错会让**整份报价的方案级别归属错误**——业主一眼就能看出
"你这份 Base 报价里怎么有个不该有的东西"，属于会被直接质疑专业性的错误。

**核心判据**
1. 基础方案直接费 = Σ(非 OPTIONS 行金额)，OPTIONS 金额必须**单列**且不计入
2. 若工作簿内声明了 options_tag（如 CPB 编码以 4 开头），须与实际行一致
3. 声明的"基础方案合计"必须等于实算的基础部分，不许含 OPTIONS

用法
----
    python gate_scope_options.py <成本表>.xlsx
    python gate_scope_options.py <成本表>.xlsx --cpb-col 1 --base-label "1_成本取量总表"
    python gate_scope_options.py <成本表>.xlsx --summary "汇总.xlsx" --summary-cell "C7"

退出码：0 = 通过；1 = 有FAIL 项；2 = 门禁执行失败。
"""
import argparse
import io
import os
import re
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
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from ack_util import AckLedger, add_ack_arg
except Exception:                                     # 单文件分发时降级
    AckLedger, add_ack_arg = None, None

# 判定 OPTIONS 的关键词：CPB 编码以 4 开头、或文本含 OPTIONS/可选项
OPT_PAT = re.compile(r'^\s*4(\.\d+)*\s*$|OPTIONS|可选项|OPTION\b', re.I)


def load_workbook(path):
    import openpyxl
    try:
        return openpyxl.load_workbook(path, data_only=False)
    except Exception as e:
        raise RuntimeError('无法打开 %s：%s' % (path, e))


def run(path, sheet, cpb_col, qty_col, price_col, first_row, fill=True):
    wb = load_workbook(path)
    if sheet not in wb.sheetnames:
        return None, '工作簿里没有页签「%s」（现有：%s）' % (sheet, wb.sheetnames)
    ws = wb[sheet]

    base = opt = 0.0
    opt_rows, unlabeled = [], []
    n_base = n_opt = n_inherit = n_qty = 0
    last_cpb = ''

    for r in range(1, ws.max_row + 1):
        # ★段头继承：分段式表格（E3 实测 19 个段头、34 个金额行）只在段头行写 CPB 编码，
        #   明细行的第 1 列是空的。直接取本行第 1 列会把这些行全判成"未标编码"——
        #   本项目实测一次误报 34 行 / 2649 万，占总表近六成。
        #   这不是放宽判据：人读这张表时，明细行**就是**属于上一个段头的；
        #   继承之后 G-P2 仍然抓得住"从头到尾没有任何段头"的真漏编码，
        #   而且能把"段头是 OPTIONS、明细行没标"这种真正危险的情形认出来
        #   ——恰恰是原来那种读法漏掉的。
        #   ★段头扫描必须从第 1 行起：表头段（r1 列名 / r2 首个 CPB 段头）在
        #     first_row 之上，若从 first_row 起扫，紧接其后的明细行会凭空变成"无段头"。
        c1 = str(ws.cell(r, cpb_col).value or '').strip()
        if c1:
            last_cpb = c1
        if r < first_row:
            continue
        cpb = c1 if c1 else (last_cpb if fill else '')

        q = ws.cell(r, qty_col).value
        u = ws.cell(r, price_col).value
        if isinstance(q, (int, float)):
            n_qty += 1
        if not isinstance(q, (int, float)) or not isinstance(u, (int, float)):
            continue
        name = str(ws.cell(r, 3).value or '')
        amt = q * u

        # 合计行/小计行不计入（它们本身是公式，会重复计算）
        if name.startswith('★') and '合计' in name:
            continue

        if not c1 and cpb:
            n_inherit += 1
        is_opt = bool(OPT_PAT.search(cpb))
        if not cpb:
            unlabeled.append((r, name[:28], amt))
        if is_opt:
            opt += amt
            opt_rows.append((r, cpb, name[:28], amt))
            n_opt += 1
        else:
            base += amt
            n_base += 1

    return dict(base=base, opt=opt, opt_rows=opt_rows,
                unlabeled=unlabeled, n_base=n_base, n_opt=n_opt,
                n_qty=n_qty, n_inherit=n_inherit, fill=fill,
                sheetnames=wb.sheetnames), None


def main():
    ap = argparse.ArgumentParser(description='方案级口径分离门禁（G-PROP）')
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='1_成本取量总表')
    ap.add_argument('--cpb-col', type=int, default=1)
    ap.add_argument('--qty-col', type=int, default=5)
    ap.add_argument('--price-col', type=int, default=7)
    ap.add_argument('--first-row', type=int, default=3)
    ap.add_argument('--no-fill', action='store_true',
                    help='关闭段头继承（分段式表格的明细行将全部判为"未标编码"）')
    ap.add_argument('--summary', default=None,
                    help='汇总表路径；提供后核对其基础方案合计是否含 OPTIONS')
    ap.add_argument('--summary-cell', default=None, help='如 C7')
    if add_ack_arg:
        add_ack_arg(ap)
    a = ap.parse_args()

    try:
        d, err = run(a.xlsx, a.sheet, a.cpb_col, a.qty_col, a.price_col,
                     a.first_row, fill=not a.no_fill)
    except Exception as e:
        print('[FAIL] G-P0 门禁执行失败：%s' % e)
        return 2
    if err:
        print('[FAIL] G-P0 %s' % err)
        return 2

    print('方案级口径分离门禁 G-PROP | 表：%s | 页签：%s'
          % (os.path.basename(a.xlsx), a.sheet))
    print('基础行 %d ｜ OPTIONS 行 %d' % (d['n_base'], d['n_opt']))
    if d['n_inherit']:
        print('段头继承：%d 行未在自己行内写 CPB 编码，已按所在段头归属'
              '（分段式表格的正常写法；如需按原样判，加 --no-fill）'
              % d['n_inherit'])
    print('-' * 74)
    print('基础方案直接费（不含 OPTIONS） %18.2f' % d['base'])
    print('OPTIONS（应单列，不计入基础）   %18.2f' % d['opt'])
    # ---- 空分母守卫（v2.1.0）----
    # 本表 APS 阶段单价列全空时，q×p 一行都算不出来，base=0.00，
    # 而汇总里被记成"exit 0 通过"——检查了 0 行却说没问题。
    if d.get('n_qty', 0) > 0 and d['n_base'] + d['n_opt'] == 0:
        print('-' * 74)
        print('[未执行] G-P0b 表内有 %d 行工程量，但**单价列全空**，'
              '一行金额都算不出来。' % d['n_qty'])
        print('         G-PROP 本项判为「未运行」，不得计入通过。'
              '请在齐价表上重跑，或在报告中写明"方案口径分离未核"。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2
    # 整张表一条量化行都没有：Base/OPTIONS 的划分、汇总核对全部无从谈起。
    if d.get('n_qty', 0) == 0:
        print('-' * 74)
        print('[未执行] G-P0c 本表扫描到 **0 行带工程量的行**。')
        print('         Base 与 OPTIONS 的划分、以及 G-P3 汇总核对，'
              '一条判据都没能执行。')
        print('         空表上的"方案级口径分离通过"是假绿——先核对 --sheet 与列序。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2
    print('-' * 74)
    fails = []
    tf = os.path.basename(a.xlsx)
    ledger = AckLedger(getattr(a, 'ack', None)) if AckLedger else None

    def ack_of(gate, rowkey, expect):
        """返回 (是否已确认, 理由)。台账里 row 写 '*' 表示整表级判据。"""
        if not ledger:
            return False, ''
        st, rs = ledger.hit(gate, tf, a.sheet, rowkey, expect)
        if st == 'ACK':
            return True, rs
        if st == 'STALE':
            return False, 'ACK-STALE ' + rs
        return False, ''

    if d['n_opt'] == 0:
        ok, rs = ack_of('G-P1', '*', 'n_opt=0')
        if ok:
            print('[ACK] G-P1 已确认本表无 OPTIONS 段：%s' % rs)
        else:
            print('[WARN] G-P1 未发现任何 OPTIONS 行。'
                  '若本项目确无可选项，请确认这不是"忘了填 CPB 编码"造成的漏认。'
                  + (('\n       ' + rs) if rs else ''))
    else:
        print('[PASS] G-P1 识别到 %d 条 OPTIONS 行：' % d['n_opt'])
        for r, cpb, name, amt in d['opt_rows'][:10]:
            print('       r%-4d %-14s %-28s %14.2f' % (r, cpb, name, amt))

    if d['unlabeled']:
        tot = sum(x[2] for x in d['unlabeled'])
        ok, rs = ack_of('G-P2', '*', 'n_unlabeled=%d' % len(d['unlabeled']))
        if ok:
            print('[ACK] G-P2 %d 行无段头已确认：%s' % (len(d['unlabeled']), rs))
        else:
            print('[WARN] G-P2 有 %d 个有金额但 **CPB 编码为空** 的行（合计 %.2f）：'
                  % (len(d['unlabeled']), tot))
            for r, name, amt in d['unlabeled'][:6]:
                print('       r%-4d %-30s %14.2f' % (r, name, amt))
            print('       这些行**连所属段头也在它之上找不到**（已做段头继承），'
                  '无法判方案归属，默认计入基础方案。'
                  '若其中有属 OPTIONS 的，会被误计入 Base。')
            if rs:
                print('       ' + rs)

    # 与汇总表核对
    if a.summary and a.summary_cell:
        try:
            swb = load_workbook(a.summary)
            # 允许 "页签!C7" 或只给 "C7"
            if '!' in a.summary_cell:
                sh, cl = a.summary_cell.split('!', 1)
            else:
                # ★2026-07-20 自测修正：原实现取 sheetnames[0]，而真实汇总表
                #   第一个页签是「0_说明与口径」这类说明页，取不到数就误报失败。
                #   改为在**所有页签**里找该格有数值的那一个。
                sh, cl = None, a.summary_cell
                for cand in swb.sheetnames:
                    if isinstance(swb[cand][a.summary_cell].value, (int, float)):
                        sh = cand
                        break
                if sh is None:
                    raise RuntimeError(
                        '所有页签的 %s 都不是数值（页签：%s）｜'
                        '请用「页签!格」形式明确指定'
                        % (a.summary_cell, swb.sheetnames))
            v = swb[sh][cl].value
            is_f = isinstance(v, str) and v.startswith('=')
            nv = v if isinstance(v, (int, float)) else None
            if nv is None:
                print('[FAIL] G-P3 汇总表 %s!%s 取不到数值（=%r）'
                      % (sh, cl, v))
                fails.append('G-P3')
            elif abs(nv - d['base']) > 1:
                delta = nv - d['base']
                print('[FAIL] G-P3 汇总表 %s!%s = %.2f ≠ 本表基础方案实算 %.2f'
                      '（差 %+.2f）' % (sh, cl, nv, d['base'], delta))
                print('　　★查两处：①汇总把 OPTIONS（%.2f）计入了基础报价；'
                      '②有行本属 OPTIONS 却未标 CPB 编码，被默认计入基础。'
                      % d['opt'])
                print('　　本表识别的基础行 %d 条 / OPTIONS 行 %d 条，'
                      '与汇总口径对齐后再放行。' % (d['n_base'], d['n_opt']))
                fails.append('G-P3')
            else:
                print('[PASS] G-P3 汇总表 %s!%s = %.2f = 基础方案实算%s'
                      % (sh, cl, nv, '（公式引用）' if is_f else ''))
        except Exception as e:
            print('[FAIL] G-P3 核对汇总表失败：%s' % e)
            fails.append('G-P3')

    print('---')
    if ledger:
        for ln in ledger.report(table=tf, gates=('G-P1', 'G-P2')):
            print(ln)
    if fails:
        print('!! 未通过：方案级口径有问题。'
              '口径错比算量错更隐蔽——它让整份报价的方案归属错误。')
        return 1
    print('== 方案级口径分离门禁通过 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())