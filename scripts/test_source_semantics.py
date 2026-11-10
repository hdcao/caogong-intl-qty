#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_source_semantics.py —— G-SRC 门禁自证

不测「能不能跑」，而测「能不能抓住真实事故」。
每个用例都复现一条本项目实际发生过的错误，断言门禁必须报红。
同时保留反向断言：干净的净量表必须 PASS，不许乱咬。

★★ 重要：输入格式必须覆盖 csv 与 xlsx 两条分支。
   历史事故：CSV 分支返回原始字符串、而门禁只认 int/float，
   同一份数据 xlsx 报 FAIL、csv 报 PASS（结论不一致）。
   C7 就是这条缺陷的回归锁。

    python scripts/test_source_semantics.py
    → 退出码 0 全部符合预期 / 1 有用例不符预期 / 2 自身无法运行
"""
import csv
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
GATE = os.path.join(HERE, 'gate_source_semantics.py')
PY = sys.executable

CASES = []


def case(name, expect_rc, expect_in='', files=None, args=None, fmt=None):
    CASES.append(dict(name=name, expect_rc=expect_rc, expect_in=expect_in,
                      files=files or {}, args=args or [], fmt=fmt))


def make_xlsx(path, headers, rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def make_csv(path, headers, rows):
    """CSV 分支读到的是原始字符串 —— 这正是 D1 缺陷的触发条件"""
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def write_table(path, headers, rows):
    """按扩展名自动选存法"""
    if path.lower().endswith('.csv'):
        make_csv(path, headers, rows)
    else:
        make_xlsx(path, headers, rows)


# ---------- 用例定义 ----------
# C1 复现 E6：外部清单整列含 ×1.05 富余系数
ROWS_105 = [
    ['3-02.a', '0.30m SUBGRADE', 144648.00, 'm3'],
    ['3-02.b', 'AGGREGATE BASE COURSE', 134316.00, 'm3'],
    ['3-04.b.2', 'Reinforced Concrete', 9769.20, 'm3'],
    ['4-03.b.15', 'Girder', 2656.99, 'm3'],
    ['4-03.c.4', 'Bearing PAD', 201.60, 'Ea'],
    ['4-04.c.4', 'Bearing PAD', 75.60, 'Ea'],
    ['3-03.a', 'Base Course', 416035.20, 'm2'],
    ['3-03.b', 'Wearing Course', 408114.00, 'm2'],
]
HDR_QTY = ['code', 'item', 'qty', 'unit']

case('C1 整列含×1.05富余系数 → 必须FAIL',
     1, 'SRC-FACTOR', {'t.xlsx': (HDR_QTY, ROWS_105)})

# C2 复现 E1：同表既有流量单位又有尺寸单位
ROWS_FLOW = [
    ['CUL_03', 'CHAINAGE', 'BOX', 2.64, 6.76, 76.6, 2500, 1500, 1],
    ['CUL-01', '1+152', 'BOX', 2.80, 6.80, 59.0, 2500, 1500, 1],
    ['CUL-02', '2+300', 'BOX', 2.70, 7.77, 60.7, 2500, 1500, 10],
    ['CUL-08', '6+057', 'BOX', 0.22, 11.80, 70.2, 2500, 1500, 2],
    ['CUL-09', '6+617', 'BOX', 1.97, 4.91, 62.0, 2500, 1500, 1],
    ['CUL-10', '6+907', 'BOX', 0.28, 14.38, 64.9, 2500, 1500, 2],
]
case('C2 流量列与尺寸列同表 → 必须FAIL提示量纲高发',
     1, 'SRC-DIM',
     {'f.xlsx': (['no', 'chainage', 'type', 'DISCHARGES (m3/s)',
                  'DISCHARGES2 (m3/s)', 'L', 'WIDTH (mm)', 'HEIGHT (mm)',
                  'barrels'], ROWS_FLOW)})

# C3 反向断言：干净净量表不许乱咬
ROWS_CLEAN = [
    ['A', 'item1', 1000, 'm3'], ['B', 'item2', 2500, 'm3'],
    ['C', 'item3', 371, 'm3'], ['D', 'item4', 1999, 'm3'],
    ['E', 'item5', 813, 'm3'], ['F', 'item6', 2407, 'm3'],
    ['G', 'item7', 55, 'm3'], ['H', 'item8', 91, 'm3'],
]
case('C3 干净净量表 → 必须PASS（不误报）',
     0, '', {'c.xlsx': (HDR_QTY, ROWS_CLEAN)})

# C4 复现 E4：数值列未登记语义 → verify 必须阻断
case('C4 数值列未登记语义 → verify必须FAIL',
     1, '未登记语义')

# C5 声明签署缺失 → 必须阻断
case('C5 声明未由人签署 → verify必须FAIL',
     1, 'confirmed_by 未由人签署')

# C6 声明 net 但统计上像 gross → 必须 WARN
case('C6 声明net但疑似gross → 必须WARN',
     0, 'WARN')


# ============ 以下为本轮整改新增（D1/D2/D3 + DIM_HINTS） ============

# C7 【D1 回归锁】同一份 ×1.05 数据分别存 csv 与 xlsx，
#    必须都 FAIL 且结论一致。历史缺陷：csv 全 PASS。
case('C7a 同一份×1.05·CSV → 必须FAIL（曾漏检）',
     1, 'SRC-FACTOR', {'t.csv': (HDR_QTY, ROWS_105)})
case('C7b 同一份×1.05·XLSX → 必须FAIL',
     1, 'SRC-FACTOR', {'t.xlsx': (HDR_QTY, ROWS_105)})

# C8 【D3】4 行整列 ×1.05 → 必须 WARN 样本不足（不是 PASS，不是 FAIL）
ROWS_4X105 = ROWS_105[:4]   # 取同一份 ×1.05 数据的前 4 行
case('C8 4行×1.05 → 必须WARN样本不足（不静默不误报）',
     0, 'SRC-FACTOR-SKIP', {'t4.xlsx': (HDR_QTY, ROWS_4X105)})

# C9 【D2】非整数基数（1 位小数净量）×1.05 → 存 2 位小数，必须 FAIL
#    说明：净量若已是 2 位小数，×1.05 再舍入到 2 位会**抹掉全部系数信息**
#    （数学上不可检出，见 gate 内 _grid_rate 的注释），
#    因此这里用现实中更常见的 1 位小数净量（图纸/清单常见 0.1 精度）。
BASE_DEC = [76.6, 143.9, 29.5, 318.9, 55.7, 201.4, 88.2, 412.1]
ROWS_DEC105 = [['R%02d' % i, 'item%d' % i, round(b * 1.05, 2), 'm3']
               for i, b in enumerate(BASE_DEC, 1)]
case('C9 小数净量×1.05（曾因整数判据漏检） → 必须FAIL',
     1, 'SRC-FACTOR', {'tdec.xlsx': (HDR_QTY, ROWS_DEC105)})

# C10 【D2 混合系数】一半 ×1.05 一半 ×1.10 → 必须告警（复数系数可同时命中）
MIX_BASE = [76.6, 143.9, 29.5, 318.9, 90.4, 120.7, 55.2, 201.5, 88.3, 412.6, 17.8, 45.9]
ROWS_MIX = []
for i, b in enumerate(MIX_BASE, 1):
    f = 1.05 if i <= len(MIX_BASE) // 2 else 1.10
    ROWS_MIX.append(['M%02d' % i, 'item%d' % i, round(b * f, 2), 'm3'])
case('C10 混合系数1.05+1.10 → 必须告警',
     1, 'SRC-FACTOR', {'tmix.xlsx': (HDR_QTY, ROWS_MIX)})

# C11 【反向断言】干净的小数净量表（无系数）→ 必须 PASS，防假红
ROWS_CLEAN_DEC = [
    ['A', 'item1', 2267.37, 'm3'], ['B', 'item2', 2803.26, 'm3'],
    ['C', 'item3', 4621.81, 'm3'], ['D', 'item4', 2333.59, 'm3'],
    ['E', 'item5', 2544.13, 'm3'], ['F', 'item6', 2941.05, 'm3'],
    ['G', 'item7', 931.46, 'm3'], ['H', 'item8', 2564.42, 'm3'],
    ['I', 'item9', 3153.11, 'm3'], ['J', 'item10', 3966.95, 'm3'],
    ['K', 'item11', 479.68, 'm3'], ['L', 'item12', 1523.97, 'm3'],
]
case('C11 干净小数净量表（无系数） → 必须PASS（防假红）',
     0, '', {'tcleandec.xlsx': (HDR_QTY, ROWS_CLEAN_DEC)})

# ============ 小数净量（净量本身已带小数）×1.05 专项 ============
# 背景：BOQ 的工程量本来就带小数（m3 常记 2 位），而富余系数恰恰最常出现在
#      这类带小数的外部清单上。旧的「v/factor 是整数」判据在这类数据上 0/8 命中。
#
# ★ 必须如实说明的数学边界（不要再尝试"修"这条，它不可修）：
#   当净量本身就是 dec 位小数、gross=round(净量×f, dec) 也是 dec 位小数时，
#   gross 的取值几乎覆盖全体 dec 位小数（round(1.05k) 对整数 k 的像覆盖 95.24%），
#   系数信息已被舍入抹平。此时：
#     · 小数网格判据（_grid_rate）在信息上不可能区分；
#     · 唯一残余信号是「像格洞」——gross 只能落在 round(m×f) 的像上，
#       约有 (1-1/f)=4.76% 的格点永远取不到，而干净列平均会踩到，
#       故需要 n>=150 才有统计功效（见 gate 内 LATTICE_MIN_N）。
#   实测：n=8 时干净列「恰好 0 洞」的概率 (20/21)^8 = 67.7%，
#       与真 gross 列完全不可区分 → 8 行级别的纯小数 gross 无法检出，
#       只能靠 declare/verify 的人工声明兜底（这也正是本门禁的设计本意）。


def _gross_2dp(net_vals, factor):
    """模拟 Excel：先 half-up 到 2 位小数再乘系数，再 half-up 回 2 位小数"""
    import math
    out = []
    for v in net_vals:
        out.append(math.floor(v * 100 * factor + 0.5) / 100.0)
    return out


def _uniq_net(n, seed=20260505):
    """生成 n 个**互不相同**的 2 位小数净量（真实 BOQ 形态）

    ★ 必须互不相同：像格洞判据按唯一值计数，重复值不提供新证据。
      曾用「8 个数重复 25 次」构造 n=200，导致假红（见 gate 内去重注释）。
    """
    import random
    r = random.Random(seed)
    seen = set()
    out = []
    while len(out) < n:
        v = round(r.uniform(1, 9000), 2)
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


# C13 小数净量（2 位小数，模拟真实 BOQ）×1.05，样本足够大 → 必须 FAIL
#     这一档走「像格洞」判据：纯小数净量的 gross 一个洞都不踩。
_net13 = _uniq_net(200)
# 更正（v4.6.4）：原 C13 期望"200 行 2 位小数可检出"在统计上不成立。
# 实测 z=1.66（n=200）/ z=3.50（n=1000）。故 200 行必须如实声明不可检出，
# 而不是要求它报红 —— 要求报红等于要求门禁把噪声当信号。
case('C13 小数净量×1.05 200行 → 必须声明不可检出（不得假报红）',
     0, 'SRC-FACTOR-LIMIT',
     {'tlat.xlsx': (HDR_QTY, [['L%03d' % i, 'item%d' % i, v, 'm3']
                              for i, v in enumerate(_gross_2dp(_net13, 1.05), 1)])})

# C13b 真正可检出的样本量（1000 行，z>3）→ 必须 FAIL
_net13b = _uniq_net(1000)
case('C13b 小数净量×1.05 1000行 → 必须FAIL（达到统计分辨率）',
     1, 'SRC-FACTOR',
     {'tlatb.xlsx': (HDR_QTY, [['L%04d' % i, 'item%d' % i, v, 'm3']
                               for i, v in enumerate(_gross_2dp(_net13b, 1.05), 1)])})

# C14 【反向断言】同一份小数净量但不乘系数 → 必须 PASS（防假红）
case('C14 同数据不乘系数（干净小数大样本） → 必须PASS（防假红）',
     0, '',
     {'tlatc.xlsx': (HDR_QTY, [['L%03d' % i, 'item%d' % i, v, 'm3']
                               for i, v in enumerate(_net13, 1)])})

# C15 整数基数 ×1.05 → 仍须 FAIL（改判据不能丢掉原能力）
ROWS_INT_105 = [['I%02d' % i, 'item%d' % i, round(k * 1.05, 2), 'm3']
                for i, k in enumerate([105, 210, 315, 420, 525, 630, 735, 840], 1)]
case('C15 整数基数×1.05 → 必须FAIL（保住原能力）',
     1, 'SRC-FACTOR', {'tint.xlsx': (HDR_QTY, ROWS_INT_105)})

# C16 【边界说明性断言】8 行纯 2 位小数 gross 在数学上不可检出 → 必须 PASS，
#     但必须输出 [INFO] SRC-FACTOR-LIMIT 声明「受精度限制不可检出，非已确认无系数」。
#     这条不是"没修好"，而是把不可修的边界钉在测试里，防止后人反复"修"它：
#     零发现必须显式标注为"未校验"，不能静默 PASS。
ROWS_SMALL_LAT = [['S%02d' % i, 'item%d' % i, g, 'm3']
                  for i, g in enumerate(_gross_2dp(
                      [76.63, 143.87, 29.45, 318.92, 55.71, 201.36, 88.24, 412.09],
                      1.05), 1)]
case('C16 8行纯小数gross（不可检出） → PASS但须INFO声明边界',
     0, 'SRC-FACTOR-LIMIT', {'tsmall.xlsx': (HDR_QTY, ROWS_SMALL_LAT)})

# C17 【回归锁】重复值不构成独立证据 → 必须 PASS，不得假红
#     历史缺陷：像格洞判据按原始行数统计，「8 个干净值重复 19 次」被当成
#     n=152 的独立样本（8 个数天然踩不到洞）→ 误判为 ×1.05。已改为按唯一值计数。
_DUP_NET = [76.63, 143.87, 29.45, 318.92, 55.71, 201.36, 88.24, 412.09] * 19
case('C17 8个干净值重复19次（去重后仍不足） → 必须PASS',
     0, '', {'tdup.xlsx': (HDR_QTY, [['D%03d' % i, 'item%d' % i, v, 'm3']
                                     for i, v in enumerate(_DUP_NET, 1)])})


def run_one(c):
    tmp = tempfile.mkdtemp(prefix='srcsem_')
    try:
        fpath = None
        for fn, (hdrs, rows) in c['files'].items():
            fpath = os.path.join(tmp, fn)
            write_table(fpath, hdrs, rows)

        nm = c['name']
        is_table_case = nm.startswith(('C1', 'C2', 'C3', 'C7', 'C8',
                                      'C9', 'C10', 'C11',
                                      'C13', 'C14', 'C15', 'C16', 'C17'))
        if is_table_case:
            args = [PY, GATE, 'table', '--file', fpath,
                    '--out', os.path.join(tmp, 'out')]
            p = subprocess.run(args, capture_output=True, text=True,
                               encoding='utf-8', errors='replace')
            out = (p.stdout or '') + (p.stderr or '')
            ok = (p.returncode == c['expect_rc'] and
                  (c['expect_in'] in out if c['expect_in'] else True))
            return ok, p.returncode, out

        # verify 类：先造表和声明
        fpath = os.path.join(tmp, 'v.xlsx')
        if nm.startswith('C4'):
            make_xlsx(fpath, HDR_QTY, ROWS_CLEAN)
            decl = dict(file=fpath, sheet='Sheet1', columns=[
                # 故意只登记 code/item，数值列 qty 不登记
                dict(col_index=1, header='code', col_role='code', unit='-',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
                dict(col_index=2, header='item', col_role='name', unit='-',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
            ])
        elif nm.startswith('C5'):
            make_xlsx(fpath, HDR_QTY, ROWS_CLEAN)
            decl = dict(file=fpath, sheet='Sheet1', columns=[
                dict(col_index=1, header='code', col_role='code', unit='-',
                     value_basis='net', confirmed_by='＿＿＿＿＿＿',
                     confirmed_at='2026-11-04'),
                dict(col_index=2, header='item', col_role='name', unit='-',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
                dict(col_index=3, header='qty', col_role='quantity', unit='m3',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
                dict(col_index=4, header='unit', col_role='unit', unit='-',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
            ])
        else:  # C6
            make_xlsx(fpath, HDR_QTY, ROWS_105)
            decl = dict(file=fpath, sheet='Sheet1', columns=[
                dict(col_index=1, header='code', col_role='code', unit='-',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
                dict(col_index=2, header='item', col_role='name', unit='-',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
                dict(col_index=3, header='qty', col_role='quantity', unit='m3',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
                dict(col_index=4, header='unit', col_role='unit', unit='-',
                     value_basis='net', confirmed_by='张三', confirmed_at='2026-11-04'),
            ])
        dpath = os.path.join(tmp, 'decl.json')
        json.dump(decl, open(dpath, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        args = [PY, GATE, 'verify', '--file', fpath, '--declare', dpath]
        p = subprocess.run(args, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        out = (p.stdout or '') + (p.stderr or '')
        ok = (p.returncode == c['expect_rc'] and
              (c['expect_in'] in out if c['expect_in'] else True))
        return ok, p.returncode, out
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def run_c12():
    """【DIM_HINTS】list-columns 对 qty 列不得标成"吨"等噪声提示（反向断言）

    历史缺陷：DIM_HINTS 含单字母键 'a'/'m'/'t'，用 `if k in nh` 子串匹配 →
      qty → 含 't' → 标成"吨"；total → 标成"电流"；item → 标成"长度"。
    """
    tmp = tempfile.mkdtemp(prefix='srcsem_dim_')
    try:
        p = os.path.join(tmp, 'd.xlsx')
        make_xlsx(p, ['qty', 'total', 'item', 'm3', 'ton'], [
            ['q1', 't1', 'i1', 1.5, 2.5],
            ['q2', 't2', 'i2', 3.5, 4.5],
            ['q3', 't3', 'i3', 5.5, 6.5],
        ])
        r = subprocess.run([PY, GATE, 'list-columns', '--file', p],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        out = (r.stdout or '') + (r.stderr or '')
        bad = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                # 行首是列序号，第二段是列名
                hdr = parts[1] if parts[0].isdigit() else None
                if hdr in ('qty', 'total', 'item'):
                    if '吨' in line or '电流' in line or '长度' in line:
                        bad.append(line.strip())
                if hdr in ('m3', 'ton'):
                    if '体积' not in line and '吨' not in line:
                        bad.append('漏提示 ' + line.strip())
        ok = (r.returncode == 0 and not bad and 'qty' in out)
        return ok, r.returncode, ('OUT:\n' + out + '\nBAD:' + repr(bad))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print('=' * 72)
    print('G-SRC 外部数据源语义门禁 · 自证')
    print('=' * 72)
    n_ok = 0
    fails = []
    for c in CASES:
        try:
            ok, rc, out = run_one(c)
        except Exception as e:
            ok, rc, out = False, -1, 'EXC %s' % e
        mark = 'PASS' if ok else 'FAIL'
        print('  [%s] %-48s rc=%s expect=%s' % (mark, c['name'][:48], rc,
                                                c['expect_rc']))
        if ok:
            n_ok += 1
        else:
            fails.append((c['name'], rc, out))

    # C12
    try:
        ok, rc, out = run_c12()
    except Exception as e:
        ok, rc, out = False, -1, 'EXC %s' % e
    name12 = 'C12 list-columns 对qty不得标成"吨"（DIM_HINTS）'
    print('  [%s] %-48s rc=%s expect=0' % ('PASS' if ok else 'FAIL',
                                           name12[:48], rc))
    if ok:
        n_ok += 1
    else:
        fails.append((name12, rc, out))

    total = len(CASES) + 1
    print('-' * 72)
    print('用例覆盖 %d/%d' % (n_ok, total))
    if fails:
        print()
        for nm, rc, out in fails:
            print('✗ %s (rc=%s)' % (nm, rc))
            print('  输出片段：', out[:400].replace('\n', ' | '))
        return 1
    print('全部符合预期：能抓住真实事故，且不误咬干净表。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
