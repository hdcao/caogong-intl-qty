#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单位量纲一致性门禁（G-UNIT；技能包 v4.3.1）

**抓过的事故**
同一张算量表内，桥1 的体积行记 m³、桥2 的体积行记 m³×10——同表两套惯例，
交叉复核因此长期报 −90% 的假差异，掩盖真实问题。

**误判过程比事故本身更值得记**
1. 先判"模型侧单位标注错"
2. 基于这个**未验证**的判断做全局 ×10 归一
3. 把原本 6 项 EXACT 全推成+900% MAJOR——方向完全反了
4. 撤销后再查才发现是二维表自己不统一

**判据演进（全部经注入验证，留痕勿再改回）**
| 版 | 判据 | 结果 |
|---|---|---|
| v1 | 体积行跨数量级即 FAIL | ❌ 误报。湿接缝 31 m³ 与承台 14582 m³跨 4 档，那是**构件尺寸差异**，完全正常 |
| v2 | 单价偏离中位 >10 倍 | ❌ 漏报。只改量不改价时无感；且注入把中位数一起抬上去，15000/1600=9.4 < 10 |
| v3 | 绝对区间 [100, 60000] | ❌ 漏报。区间太宽，15000 落在里面 |
| **v4** | **按数量级分桶，整档偏离主桶则告警** | ✅ 抗注入，不依赖中位数也不依赖固定区间 |
| v4.1 | 单位符号先剥括注再归类（`M3（参考）`→`M3`） | ✅ 修复括注导致的漏检。 |
| v4.2 | 单位量纲从核心注册表派生，`CM` 只由项目审批注册表启用 | ✅ 防止单位在不同门禁中被赋予不一致的默认含义。 |

★**v4.1→v4.2 单位治理**：单位符号先剥离括注，再查核心维度注册表；否则 `M3（参考）` 可能在判据侧被漏检。v4.2 同时明确：格式归一不代表批准歧义符号，`CM（综合）` 归一为 `CM` 后仍不得全局猜作体积，只有经批准且绑定当前项目的别名注册表才能启用该解释。`CM` 无映射、映射未批准或项目编号不匹配时必须阻断，而不是悄悄归入体积组。

★**核心洞见**：量的绝对大小**无法**区分"小构件"与"量纲错"——两者都会正常出现。
但"单价整档偏离主桶"是能区分的：m³ 与 m³×10 的差别是**整一个数量级**。

★**本门禁只出 WARN 不出 FAIL**：离群可能是①特殊材料（高强/预应力，单价本该高）
或②量纲错配。**两者都必须人工确认**，机器不该替人判。所以它的作用是
**让你看见这一档**，而不是替你决定对错。

用法
----
    python gate_unit_consistency.py <表>.xlsx
    python gate_unit_consistency.py <表>.xlsx --compare <另一侧量.json>

退出码：0 = 通过（含需人工确认的 WARN）；1 = 执行失败。
"""
import argparse
import io
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


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from ack_util import AckLedger, add_ack_arg
except Exception:                                     # 单文件分发时降级
    AckLedger, add_ack_arg = None, None

try:
    from qto_core import DataValidationError, load_unit_alias_registry, normalize_unit as core_normalize_unit, unit_dimensions
except Exception:  # 单文件降级时保守处理：不把歧义符号猜成体积
    DataValidationError = ValueError
    load_unit_alias_registry = None
    unit_dimensions = None
    core_normalize_unit = lambda value: str(value or "").strip().casefold().replace("³", "3").replace("²", "2").replace(" ", "").replace(".", "")

# 量纲来自 qto_core 单一事实来源。CM 等歧义符号只可由项目审批的单位注册表启用。
_BUILTIN_DIMS = unit_dimensions() if unit_dimensions else {
    'm': 'length', 'lm': 'length', 'm2': 'area', 'sm': 'area',
    'm3': 'volume', 'm³': 'volume', 'mc': 'volume', 'cub': 'volume',
    'ea': 'count', 'u': 'count', 'no': 'count', 'nr': 'count', 't': 'mass', 'mt': 'mass',
}
VOL_UNITS = {str(k).upper() for k, v in _BUILTIN_DIMS.items() if v == 'volume'} | {'CBM'}
# Every supported non-volume unit is explicitly recognized as non-volume, including count/mass/item.
KNOWN_NONVOLUME_UNITS = {str(k).upper() for k, v in _BUILTIN_DIMS.items() if v != 'volume'}
# ★1.2.1 补登：LOT（包干）、区段·月（工期类计量）、张（板/图纸）、个/处/套/台/组/条/孔/月
#   这些**都不是体积单位**，登记它们不是放宽判据，而是让 G-U4 只对"真的没见过的东西"报警。
#   未登记 → 每次运行都报同一串符号 → WARN 变成背景噪音。
OTHER = {'EA', 'MT', 'KG', 'T', 'NO', 'PC', '项', '座', '根', '道', '块',
         'LOT', '张', '个', '处', '套', '台', '组', '条', '孔', '月',
         '区段·月', '区段', 'SHEET'}
# 占位符：表示"本行不按单位计量"（小计行、备注行常写这些），不参与归类也不报警
PLACEHOLDER = {'—', '——', '-', '–', '/', 'N/A', 'NA', '无', '不适用'}


def base_unit(u):
    """剥掉括注限定词，取基础单位符号：`LM（参考）`→`LM`、`张(SHEET)`→`张`。

    ★这一步必须发生在归类之前。理由见文件头的单位归一与项目审批边界说明：
    若只在告警里提醒"这个符号没登记"，`M3（参考）` 就永远不会被当作体积行检查，
    等于用"温柔的提醒"换掉了真正的检查。
    """
    s = str(u).strip().upper()
    for sep in ('（', '(', '[', '【', '［'):
        i = s.find(sep)
        if i > 0:
            s = s[:i]
    return core_normalize_unit(s).upper()


def load_workbook(path):
    import openpyxl
    try:
        return openpyxl.load_workbook(path, data_only=False)
    except Exception as e:
        raise RuntimeError('无法打开 %s：%s' % (path, e))


def load_json(p):
    with io.open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def decade(v):
    """取数量级（以 10 为底）。"""
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(float(v)) or v <= 0:
        return None
    return int(math.floor(math.log10(v)))


def main():
    ap = argparse.ArgumentParser(description='单位量纲一致性门禁（G-UNIT）')
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='1_成本取量总表')
    ap.add_argument('--unit-col', type=int, default=4)
    ap.add_argument('--qty-col', type=int, default=5)
    ap.add_argument('--name-col', type=int, default=3)
    ap.add_argument('--price-col', type=int, default=7)
    ap.add_argument('--compare', default=None)
    ap.add_argument('--unit-registry', default='', help='Approved project unit-alias registry JSON')
    ap.add_argument('--project-id', default='', help='Project ID required when using the project unit registry')
    if add_ack_arg:
        add_ack_arg(ap)
    a = ap.parse_args()

    alias_registry = {}
    if a.unit_registry:
        if not a.project_id:
            print('[未执行] G-U0c 使用 --unit-registry 时必须同时提供 --project-id。')
            return 2
        if load_unit_alias_registry is None:
            print('[未执行] G-U0c qto_core 不可用，无法安全加载项目单位注册表。')
            return 2
        try:
            alias_registry = load_unit_alias_registry(a.unit_registry, project_id=a.project_id)
        except Exception as exc:
            print('[未执行] G-U0c 项目单位注册表无效：%s' % exc)
            return 2
    alias_dims = {str(k).upper(): str(v.get('_dimension')) for k, v in alias_registry.items()}
    active_vol_units = VOL_UNITS | {k for k, v in alias_dims.items() if v == 'volume'}
    active_nonvolume_units = KNOWN_NONVOLUME_UNITS | {k for k, v in alias_dims.items() if v != 'volume'}

    # ---- 前置读取失败一律 exit 2（v2.1.1）----
    # 这三处原本都 return 1，与"检查不通过"混为一谈：汇总里看到非零就以为
    # 表被查出了问题，其实门禁根本没跑起来（路径错、文件坏、页签名不对）。
    if not os.path.exists(a.xlsx):
        print('[未执行] G-U0 表文件不存在：%s' % a.xlsx)
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2
    try:
        import openpyxl
        wb = openpyxl.load_workbook(a.xlsx, data_only=False)
    except Exception as e:
        print('[未执行] G-U0 无法打开表：%s' % e)
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2

    if a.sheet not in wb.sheetnames:
        print('[未执行] G-U0 没有页签「%s」（现有：%s）'
              % (a.sheet, wb.sheetnames))
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2

    ws = wb[a.sheet]
    print('单位量纲一致性门禁 G-UNIT | 表：%s | 页签：%s'
          % (os.path.basename(a.xlsx), a.sheet))

    rows, unknown = [], set()
    vol_seen, vol_noprice = 0, 0
    for r in range(3, ws.max_row + 1):
        u = str(ws.cell(r, a.unit_col).value or '').strip()
        q = ws.cell(r, a.qty_col).value
        p = ws.cell(r, a.price_col).value
        nm = str(ws.cell(r, a.name_col).value or '')
        if nm.startswith('★') and '合计' in nm:
            continue
        if not u or not isinstance(q, (int, float)):
            continue
        if u in PLACEHOLDER:
            continue
        b = base_unit(u)                  # ★先归一，再归类
        if b in active_vol_units:
            vol_seen += 1
            # v2.1.0：单价不再作为"能不能看见这行"的前置条件。
            # 此前 APS 阶段（单价列全空）34 行体积项被整体 continue，
            # 门禁检查 0 行却 exit 0，汇总里记成"通过"——空转被当成干净。
            if isinstance(p, (int, float)):
                rows.append((r, nm, q, p))
            else:
                vol_noprice += 1
        elif b in active_nonvolume_units or b in OTHER:
            continue
        else:
            unknown.add(u)

    print('体积类行 %d 条（其中 %d 条有单价，%d 条无单价）'
          % (vol_seen, len(rows), vol_noprice))
    print('-' * 74)
    if vol_seen == 0:
        print('[未执行] G-U0a 本表未发现体积项——体积类是 G-UNIT 唯一能判的对象。')
        print('         若本表确无体积项（如纯钢筋/纯支座表），本门禁结构性无话可说；'
              '报告中写 "N/A（无体积项）"，不得写 "PASS"。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2
    if not rows:
        print('[未执行] G-U0b 表内存在 %d 行体积项，但全部因**单价缺失**未能进入检查。'
              % vol_seen)
        print('         G-UNIT 本项判为「未运行」，不得计入通过。')
        print('         处置：补单价后重跑，或改用 G-DW / boq_lint 做量纲文本比对；'
              '并在交付报告中写明"单位量纲未核"。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2

    # ── v4 判据：按数量级分桶，看有没有整档偏离 ──────────────────
    buckets = {}
    for r, nm, q, p in rows:
        buckets.setdefault(decade(p), []).append((r, nm, q, p))

    print('单价数量级分布：')
    for e in sorted(buckets):
        ps = [x[3] for x in buckets[e]]
        print('  10^%-3d %2d 行  单价 %10.2f ~ %10.2f  例：%s'
              % (e, len(buckets[e]), min(ps), max(ps), str(buckets[e][0][1])[:24]))

    warns = 0
    tf = os.path.basename(a.xlsx)
    ledger = AckLedger(getattr(a, 'ack', None)) if AckLedger else None
    if ledger and ledger.n:
        print('ACK 台账：%d 条（%s）' % (ledger.n, a.ack))
    if len(buckets) >= 2:
        # 主桶 = 行数最多；并列时取数量级更大的（高端材料通常是主流）
        main_e = max(buckets, key=lambda e: (len(buckets[e]), e))
        main_n = len(buckets[main_e])
        main_ps = [x[3] for x in buckets[main_e]]
        main_lo, main_hi = min(main_ps), max(main_ps)

        # 主桶自身的跨度（若主桶内部就跨了很宽，说明本表单价本就分散，不判）
        main_span = (main_hi / main_lo) if main_lo else 1.0

        minor = []
        if main_n >= 3 and main_span < 10:
            for e in sorted(buckets):
                if e is None or e == main_e:
                    continue
                gap = (10.0 ** e) / (10.0 ** main_e) if e > main_e \
                    else (10.0 ** main_e) / (10.0 ** e)
                if gap >= 10:
                    minor.extend(buckets[e])

        if minor:
            # ── 逐行过 ack 台账：已人工确认的不再重复报警 ──
            open_rows, acked, stale = [], [], []
            for r, nm, q, p in minor:
                exp = 'qty=%.2f|price=%.2f' % (q, p)
                st, rs = ledger.hit('G-U2', tf, a.sheet, str(r), exp) if ledger else ('', '')
                if st == 'ACK':
                    acked.append((r, nm, rs))
                elif st == 'STALE':
                    stale.append((r, nm, rs))
                    open_rows.append((r, nm, q, p))
                else:
                    open_rows.append((r, nm, q, p))

            if acked:
                print()
                print('[ACK] G-U2 %d 行已人工确认（不计 WARN）：' % len(acked))
                for r, nm, rs in acked:
                    print('       r%-4d %-28s 理由：%s' % (r, str(nm)[:28], rs or '（未写理由）'))

            for r, nm, rs in stale:
                warns += 1
                print()
                print('[WARN] G-U2-ACKSTALE r%d「%s」%s'
                      % (r, str(nm)[:28], rs))
                print('　　　　ack 记的是确认当时的值；现在值变了，说明这一行被改过。'
                      '**ack 不能自动续期**，必须重新确认后改台账的 expect。')

            if open_rows:
                warns += 1
                print()
                print('[WARN] G-U2 有 %d 行单价**整档偏离主桶**（%d 行主桶为 10^%d，'
                      '跨度 %.1f 倍）：'
                      % (len(open_rows), main_n, main_e, main_span))
                for r, nm, q, p in open_rows[:8]:
                    print('       r%-4d %-26s 量%11.2f 单价%9.2f'
                          % (r, str(nm)[:26], q, p))
                print('　　★两种可能，必须人工确认：')
                print('　　 ① 特殊材料（高强/预应力/进口），单价本该高——'
                      '若如此，请在名称或备注注明材料等级。')
                print('　　 ② **量纲错配**：同一单位符号下有的行按 m³ 记、'
                      '有的按 m³×10 记。')
                print('　　★若判定为 ②：先确认本表惯例（与几何算式同量纲的那种），'
                      '归一后**量与单价配套调整以保持金额不变**。')
                print('　　★确认无误后写入 ack 台账（--ack）：'
                      '理由须指向可核查依据（图纸/招标文件/指标报告），'
                      '不要只写"没问题"。')
            elif not stale:
                print()
                print('[PASS] G-U2 本次 %d 行整档偏离**全部已有 ack 结论**，无新增'
                      % len(minor))
        else:
            print()
            print('[PASS] G-U2 无整档偏离（主桶 10^%d，%d 行，跨度 %.1f 倍）'
                  % (main_e, main_n, main_span))
    else:
        print()
        print('[PASS] G-U2 单价全部集中在同一数量级')

    if unknown:
        print()
        print('[WARN] G-U4 未登记的单位符号：%s'
              % '；'.join(sorted(unknown)[:8]))
        print('　　 分两步看：① 若是**非体积单位**（LOT/张/工期类），'
              '请在 OTHER/量纲注册表登记；② 若疑似体积单位的变体写法，'
              '应先通过规范单位词表或项目批准别名注册表明确量纲。CM 等歧义代码不得猜作体积。')

    # ack 台账反查：僵尸条目要清，否则"已确认"会失去含义
    if ledger:
        for ln in ledger.report(table=tf, gates=("G-U2", "G-U4")):
            print(ln)

    # 与另一侧比量级（可选）
    if a.compare:
        print()
        try:
            other = load_json(a.compare)
            items = other if isinstance(other, list) else other.get('items', [])
            odec = {}
            for it in items:
                if base_unit(it.get('unit', '')) in active_vol_units:
                    e = decade(it.get('qty', it.get('value')))
                    odec[e] = odec.get(e, 0) + 1
            mine = set(decade(x[3]) for x in rows)
            only = mine - set(odec)
            print('另一侧单价数量级分布：%s' % dict(sorted(odec.items())))
            if only:
                warns += 1
                print('[WARN] G-U5 本表有 %d 个数量级另一侧没有：%s'
                      '｜★两侧若应同源同量纲，这是脱钩信号'
                      % (len(only), sorted(only)))
            else:
                print('[PASS] G-U5 两侧数量级分布一致')
        except Exception as e:
            print('[未执行] G-U5 读另一侧失败：%s' % e)
            print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
            return 2

    print('---')
    print('【本门禁的定位与局限，务必知道】')
    print('· 它是**WARN 不是 FAIL**：离群可能是特殊材料，也可能是量纲错，'
          '机器不该替人判。它的作用是让你看见这一档。')
    print('· **量的绝对大小无法用于判断量纲**：小构件与大构件天然差几个数量级，'
          '实测据此判会误报（湿接缝 31 m³ vs 承台 14582 m³）。')
    print('· 它**替代不了模型×二维交叉复核**：量纲问题要在两侧同源比对时'
          '才暴露得出来——单看一张表永远分不清"贵"与"错"。')
    if warns:
        print('· 本次有 %d 条 WARN 需人工确认后写入交付说明。' % warns)
    print('== 单位门禁结束 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())