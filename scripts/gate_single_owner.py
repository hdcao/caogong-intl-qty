# -*- coding: utf-8 -*-
"""
单实物单入口门禁（1.2.0 新增 · G-OWN）

**抓过的事故（2026-10-20 本项目实测）**
桥梁表里墩身与墩墙两行**同时**按"6 个墩位"计量——墩身 360 CM、墩墙 420 CM。
实际双肢墩每墩位只有 2 肢、全桥 3 墩位，墩墙真值 210 CM。
两行都"有图纸出处"、都"有口径声明"，单价也正常，**没有任何一条既有门禁报红**：
- 单位门禁看单价 → 单价没错；
- 公式门禁看行内 E×G → 行内自洽；
- 基准门禁只比已登记的那几行 → 墩墙没登记基准。
错的是一个实物被开了两个计量入口，而两个入口的口径各行写各的。

**这条根因在这个项目里出现了三次**，形态各不相同：
1. 墩身 / 墩墙（同一墩位，两次计量）
2. 汇总行 + 分段明细行并存（同一片场地算两倍）
3. 同一 CBS 码在两处各写一遍（一处改、另一处不跟）

**判据（全部数值型/可复算，不做语义猜测）**
  G-OWN1 [FAIL] 同码下"合计/小计/总计"行 的量 ≈ 其余同码行之和  → 汇总+明细并存，重复计量
  G-OWN2 [FAIL] 同码 + 同单位 + 名称完全相同的行 > 1            → 确定重复
  G-OWN3 [WARN] 同 CPB 父项 + 同单位，两行部位计数相同但构件词不同
                （如"6 个墩位墩身"与"6 个墩位墩墙"）            → 两个入口共用一份部位，必有一行口径错
  G-OWN4 [WARN] 同 CPB 父项 + 同单位，两行量成整数倍（2~10，±1.5%） → 疑整倍数口径错

G-OWN3 为什么只 WARN：6 个墩位上确实可以同时有墩身和墩墙，**理论上合法**。
但"两行都写同一个部位计数"是强信号——双肢结构的肢数被当成墩位数，就是这么错的。
机器把信号摆出来，人定；换成 FAIL 会逼人加白名单，白名单一多这条门禁就废了。

★1.2.1 G-OWN3 **接受 ack 台账**（`--ack`）：人确认"并集合法"后可不再重复报警，
  但 ack 带数值指纹，量变了就报 ACK-STALE 要求重新确认。
★1.2.1 **G-OWN4 故意不接受 ack**：整倍数命中的形态就是本项目真实踩过的错法
  （肢数/幅数/根数被当成另一量）。"这次是巧合"这种判断一旦被 ack 允许，
  这条门禁就在最关键的那一类错误上失效了。整倍数只能回图纸数原始个数。

用法
----
    python gate_single_owner.py <算量表.xlsx> [--sheet 1_成本取量总表]
        [--cpb-col 1] [--code-col 2] [--name-col 3] [--qty-col 5] [--unit-col 4] [--note-col 9]
退出码：0 = 通过（含 WARN）；1 = 有 FAIL；2 = 门禁自身执行失败
"""
import argparse
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


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# ★v2.1.1：这里原本有一行 sys.stdout.reconfigure(encoding='utf-8')，
#   它会把上面的兜底推翻——强制 utf-8 后控制台按 GBK 解释字节，中文变乱码，
#   而 errors="replace" 的兜底也就白做了。删掉，只保留兜底块。

try:
    from ack_util import AckLedger, add_ack_arg
except Exception:                                     # 单文件分发时降级
    AckLedger, add_ack_arg = None, None

# 构件词表：只收道桥里"同一部位会被拆成多个计量项"的构件。
# ★不加"土方/基层/面层"这类——那些是按层计量，同名多行是常态。
MEMBER_WORDS = (
    '墩墙', '墩身', '墩柱', '承台', '盖梁', '桥台', '台身', '背墙', '翼墙',
    '桩基', '灌注桩', '钻孔桩', '桩', '基础', '梁', '桥面板', '湿接缝', '横隔梁',
    '护栏', '伸缩缝', '支座', '箱涵', '管涵', '涵洞', '端墙', '垫层', '搭板',
)

# 部位计数：名称/备注里的"N 个墩位 / N 座 / N 根 / N 片 / N 榀 / N 台 / N 孔 / N 道"
QTY_WORDS = '墩位|墩|座|根|片|榀|台|处|孔|道|条|个'
PART_RE = re.compile(r'(\d+)\s*(?:' + QTY_WORDS + r')\s*(墩位|座|根|片|榀|台|处|孔|道|条)')
# 宽松版：只取"数字+量词"
LOOSE_RE = re.compile(r'(\d+)\s*(' + QTY_WORDS + r')')

# ★附属项排除表：这些行即使写了"N 个墩位"，也不是主体构件的另一个计量入口。
#   不加这张表会产生大量假 WARN（实测：'墩顶 5% 横向排水找坡层（3 墩位）'
#   与 '双肢墩墙（3 墩位）' 被配成一对）。**假 WARN 一多，整条门禁就没人看了。**
ADJUNCT_WORDS = ('排水', '找坡', '防水', '涂装', '防腐', '标志', '标线', '照明',
                 '绿化', '回填', '开挖', '清理', '注浆', '探测', '检测', '试验',
                 '养生', '预压', '破除', '凿除', '支架', '脚手架')

SUBTOTAL_WORDS = ('合计', '小计', '总计', 'TOTAL', 'SUBTOTAL', '小 计')


def load(path):
    import openpyxl
    try:
        return openpyxl.load_workbook(path, data_only=False)
    except Exception as e:
        raise RuntimeError('无法打开 %s：%s' % (path, e))


def norm(s):
    return re.sub(r'\s+', '', str(s or ''))


def members_of(text):
    """按词表顺序返回命中的构件词（长词优先，避免'桩基'与'桩'双计）。"""
    t = norm(text)
    hits = []
    for w in MEMBER_WORDS:
        if w in t:
            # 已被更长的词覆盖则跳过（如命中'灌注桩'就不再记'桩'）
            if any(w in h and w != h for h in hits):
                continue
            if any(h in w and w != h for h in hits):
                # 已有短词命中，替换为长词
                hits = [h for h in hits if not (h in w)]
            hits.append(w)
    return hits


def part_count(text):
    """从名称/备注取部位计数，返回 (数字, 量词) 或 None。

    取**第一个**"数字+量词"：这类描述通常"6 个墩位 × 21.7×7.0×1.6m"，
    先出现的是部位个数；后面的数字是尺寸，不会被 QTY_WORDS 接住。
    """
    m = PART_RE.search(str(text or ''))
    if m:
        return int(m.group(1)), m.group(2)
    m = LOOSE_RE.search(str(text or ''))
    if m:
        return int(m.group(1)), m.group(2)
    return None


def collect(ws, cols):
    """收集数据行：(行号, CPB, 码, 名称, 量, 单位, 备注)。"""
    rows = []
    for r in range(1, ws.max_row + 1):
        code = str(ws.cell(r, cols['code']).value or '').strip()
        if not code:
            continue
        q = ws.cell(r, cols['qty']).value
        if not isinstance(q, (int, float)):
            continue
        rows.append({
            'r': r,
            'cpb': str(ws.cell(r, cols['cpb']).value or '').strip(),
            'code': code,
            'name': str(ws.cell(r, cols['name']).value or '').strip(),
            'qty': q,
            'unit': str(ws.cell(r, cols['unit']).value or '').strip().upper(),
            'note': str(ws.cell(r, cols['note']).value or '').strip(),
        })
    return rows


def run(rows, ledger=None, sheet='', table=''):
    fails, warns, notes = [], [], []
    if not rows:
        return fails, warns, ['本表未发现可识别的数据行（编码列/量列为空）']

    # ── G-OWN1 汇总行 + 明细行并存（同码）──
    by_code = {}
    for d in rows:
        by_code.setdefault(d['code'], []).append(d)
    for code, g in by_code.items():
        if len(g) < 2:
            continue
        for d in g:
            txt = norm(d['name']) + norm(d['note'])
            if not any(w in txt for w in SUBTOTAL_WORDS):
                continue
            others = [x for x in g if x['r'] != d['r'] and x['unit'] == d['unit']]
            if not others:
                continue
            s = sum(x['qty'] for x in others)
            if s and abs(d['qty'] - s) / abs(s) <= 0.02:
                fails.append(
                    'G-OWN1 %s r%d「%s」量 %.4g ≈ 其余 %d 行之和 %.4g（±2%%）→ '
                    '汇总行与明细行并存，同一实物算了两遍'
                    % (code, d['r'], d['name'][:26], len(others), s))

    # ── G-OWN2 同码 + 同单位 + 同名 重复 ──
    seen = {}
    for d in rows:
        k = (d['code'], d['unit'], norm(d['name']))
        seen.setdefault(k, []).append(d['r'])
    for (code, unit, nm), rs in seen.items():
        if len(rs) > 1 and nm:
            fails.append('G-OWN2 %s（%s）名称完全相同却出现 %d 行：r%s → 确定重复计量'
                         % (code, unit, len(rs), '、r'.join(map(str, rs))))

    # ── 按 CPB 父项 + 单位分组，做 G-OWN3 / G-OWN4 ──
    groups = {}
    for d in rows:
        key = (d['cpb'], d['unit'])
        groups.setdefault(key, []).append(d)

    for (cpb, unit), g in groups.items():
        if len(g) < 2 or not cpb or unit not in ('CM', 'M3', 'M³', 'MT', 'LM', 'SM', 'M2', 'M²'):
            continue
        # G-OWN3 同部位计数 + 不同构件词
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                a, b = g[i], g[j]
                # 附属项不参与"同一部位两个入口"的判定
                if any(w in a['name'] + a['note'] for w in ADJUNCT_WORDS) or \
                   any(w in b['name'] + b['note'] for w in ADJUNCT_WORDS):
                    continue
                pa = part_count(a['name'] + ' ' + a['note'])
                pb = part_count(b['name'] + ' ' + b['note'])
                if not pa or pa != pb:
                    continue
                ma, mb = members_of(a['name']), members_of(b['name'])
                if not ma or not mb or set(ma) & set(mb):
                    # 同一构件词 = 同构件，交给 G-OWN2/G-OWN1；这里只管"不同构件"
                    continue
                # 量级差太远（>20 倍）多半是不同尺度的构件，编号不同而已
                lo0, hi0 = min(abs(a['qty']), abs(b['qty'])), max(abs(a['qty']), abs(b['qty']))
                if lo0 <= 0 or hi0 / lo0 > 20:
                    continue
                warns.append(
                    'G-OWN3 %s（%s）r%d「%s」与 r%d「%s」都按 %d %s 计量，构件却是 %s / %s '
                    '→ 同一份部位被开了两个计量入口，逐行确认是"并集"还是"包含关系"'
                    % (cpb, unit, a['r'], a['name'][:20], b['r'], b['name'][:20],
                       pa[0], pa[1], '/'.join(ma), '/'.join(mb)))
                # ── ack：已人工确认"并集合法"的成对行不再报 ──
                if ledger:
                    rk = '%d+%d' % (min(a['r'], b['r']), max(a['r'], b['r']))
                    exp = 'qty=%.2f/%.2f' % (a['qty'], b['qty'])
                    st, rs = ledger.hit('G-OWN3', table, sheet, rk, exp)
                    if st == 'ACK':
                        warns.pop()
                        notes.append('ACK G-OWN3 r%s 与 r%s 已确认：%s'
                                     % (a['r'], b['r'], rs))
                    elif st == 'STALE':
                        warns.append('G-OWN3-ACKSTALE r%s 与 r%s：%s（ack 不能自动续期，'
                                     '值已变须重新确认）' % (a['r'], b['r'], rs))
                # G-OWN4 整倍数
                lo, hi = min(a['qty'], b['qty']), max(a['qty'], b['qty'])
                if lo > 0:
                    k = hi / lo
                    for n in (2, 3, 4, 5, 6, 8, 10):
                        if abs(k - n) / n <= 0.015:
                            warns.append(
                                'G-OWN4 %s（%s）r%d=%.4g 与 r%d=%.4g 恰为 %d 倍 '
                                '→ 疑整倍数口径错（肢数/幅数/根数被当成另一量），'
                                '不要按倍数反推，回图纸数原始个数'
                                % (cpb, unit, a['r'], a['qty'], b['r'], b['qty'], n))
                            break

    if not fails and not warns:
        notes.append('未发现重复计量入口（同码 %d 个，CPB×单位组 %d 组）'
                     % (len(by_code), len(groups)))
    return fails, warns, notes


def main():
    ap = argparse.ArgumentParser(description='单实物单入口门禁（G-OWN）')
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='1_成本取量总表')
    ap.add_argument('--cpb-col', type=int, default=1)
    ap.add_argument('--code-col', type=int, default=2)
    ap.add_argument('--name-col', type=int, default=3)
    ap.add_argument('--qty-col', type=int, default=5)
    ap.add_argument('--unit-col', type=int, default=4)
    ap.add_argument('--note-col', type=int, default=9)
    if add_ack_arg:
        add_ack_arg(ap)
    a = ap.parse_args()

    if not os.path.isfile(a.xlsx):
        print('状态：unavailable —— 找不到表 %s' % a.xlsx)
        return 2
    try:
        wb = load(a.xlsx)
        if a.sheet not in wb.sheetnames:
            print('[FAIL] G-OWN0 页签不存在：%s｜可选：%s'
                  % (a.sheet, '、'.join(wb.sheetnames)))
            print('　　门禁**未能执行**，这个退出码不代表检查结论。')
            return 2
        ws = wb[a.sheet]
        cols = {'cpb': a.cpb_col, 'code': a.code_col, 'name': a.name_col,
                'qty': a.qty_col, 'unit': a.unit_col, 'note': a.note_col}
        rows = collect(ws, cols)
        ledger = AckLedger(getattr(a, 'ack', None)) if AckLedger else None
        fails, warns, notes = run(rows, ledger=ledger, sheet=a.sheet,
                                  table=os.path.basename(a.xlsx))
    except Exception as e:
        print('[FAIL] G-OWN0 门禁执行失败：%s' % e)
        return 2

    print('单实物单入口门禁 G-OWN | 表：%s | 数据行 %d'
          % (os.path.basename(a.xlsx), len(rows)))
    # ---- 零数据行守卫（v2.1.1）----
    # G-OWN1~4 全部以"行与行之间是否并存/重复"为判据，0 行时天然无发现。
    # 旧版在这种情况下照样打印「门禁通过」，等于替一张空表背书。
    if not rows:
        print('[未执行] 扫描到 **0 行数据行**（编码列与名称列同时有值的行）。')
        print('         无行可比时，"未发现重复"不构成任何结论。')
        print('         先核对 --sheet / --code-col / --name-col 是否指对了位置。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2
    if ledger and ledger.n:
        print('ACK 台账：%d 条（%s）' % (ledger.n, a.ack))
    for n in notes:
        print('[INFO] %s' % n)
    for w in warns:
        print('[WARN] %s' % w)
    for f in fails:
        print('[FAIL] %s' % f)
    if ledger:
        for ln in ledger.report(table=os.path.basename(a.xlsx), gates=("G-OWN3",)):
            print(ln)
    if not fails:
        print('[PASS] G-OWN1/2 未发现"同码汇总行+明细行并存"或"同码同名重复"')
    if not warns:
        print('[PASS] G-OWN3/4 未发现共用部位计数或整倍数嫌疑')

    if fails:
        print('!! 未通过：同一实物被开了两个计量入口。'
              '先定"哪一行是 owner"，把另一行置 0 并写明"不计量，原因=…"，不要两行都改小。')
        return 1
    print('== 单实物单入口门禁通过（WARN 项需人工确认）==')
    return 0


if __name__ == '__main__':
    sys.exit(main())
