#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
跨表/ 跨页签一致性门禁（1.1.0 新增 · G-XS）

**为什么要有这个门禁**
抓过的事故：源表改了，汇总表 A 页签的 C7 已同步，但同一工作簿另一页签
「2_完整成本结构」的 D10（桥梁）还停在前一天的旧值——**两侧各自都对，
中间那一环没走**。金额差 5585 万，而当时全部门禁全绿。

**它和「汇总表写死常数」(G-DUP) 是两种不同的脱钩**：
- G-DUP：汇总值是硬写死的常数，源表改它不跟        → 靠`gate_drawing_basis` 抓
- G-XS（本门禁）：**同一工作簿内两个页签各算一遍**，两遍算的值不同  → 靠本门禁抓

G-DUP 抓不到这一类，因为两处都是"活"的、都看起来自洽。

**核心判据**
1. 声明映射：同一实体在不同页签被写入多处时，必须建立映射并逐项比对
2. 恒等式：跨表派生值必须引用源（公式）或实算相等，不许两处各算
3. 防陈旧：映射两侧的时间戳不得相差超过阈值（可选）
4. **G-XSC 映射覆盖率（1.2.0）**：未登记进映射的编码，跨表门禁**从未检查过**。
   "映射不全"是本门禁唯一的失效方式，必须机器证明覆盖（见 check_coverage）。

用法
----
    python gate_cross_sheet.py <汇总.xlsx> --map <映射.json>
    python gate_cross_sheet.py <汇总.xlsx> --map <映射.json> --max-age-hours 24

映射文件格式
------------
    {
      "note": "说明，可选",
      "stale_ok": false,
      "coverage": {                    // ★1.2.0：不写这一块，门禁无法自证覆盖全表
        "sheet": "1_成本取量总表",
        "code_col": 2,
        "min_coverage": 1.0,
        "exclude": [{"code": "CBS-X", "reason": "口径不可比"}]
      },
      "pairs": [
        {"name": "2.3 桥梁合计",
         "a": {"sheet": "1_全项目成本汇总", "cell": "C7"},
         "b": {"sheet": "2_完整成本结构(V7.2)", "cell": "D10"},
         "mode": "derive",          # derive=须相等或公式引用；either=只比数值
         "tol": 1.0
        }
      ],
      "sums": [
        {"name": "D6:D12 应等于直接费",
         "sheet": "2_完整成本结构(V7.2)",
         "range": "D6:D12",
         "equals": {"sheet": "1_全项目成本汇总", "range": "C3:C9"},
         "tol": 1.0
        }
      ]
    }

退出码：0 = 通过；1 = 有FAIL项；2 = 门禁自身未能执行（含映射引用的页签
不存在——未形成意见，不是通过）。
"""
import argparse
import io
import json
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

TOL_DEFAULT = 1.0


# ── 读表：BOM 容错 + 失败兜底 ────────────────────────────────
def load_workbook(path):
    """读xlsx。容错：部分环境下 openpyxl 读回的文件缺样式/共享串，
    二次加载可恢复；再失败则明确报错而不是静默返回 None。"""
    import openpyxl
    try:
        return openpyxl.load_workbook(path, data_only=False)
    except Exception:
        try:
            return openpyxl.load_workbook(path, data_only=False, keep_links=False)
        except Exception as e:
            raise RuntimeError('无法打开 %s：%s' % (path, e))


def load_cached(path):
    """读同一文件的**缓存值**视图（data_only=True）。

    **抓过的事故（本项目 2026-10-29 实测）**
    算量表由 openpyxl 生成：公式写在 `<f>` 里，但没有 `<v>` 缓存值
    （只有 Excel 打开并保存过才会补上）。旧版只用 data_only=False 读，
    拿到的是公式字符串 `=SUM(...)`，一律判「存在非数值」→ 整张表假红。
    而实际上表是对的：用 data_only=True 的另一个副本或 Excel 一打开就有数。

    假红比假绿更耗人：出现过一次，整条门禁就会被跳过。
    所以这里双读——公式仍从 data_only=False 识别，数值回落到缓存。
    缓存也取不到时，如实记「未运行」而不是「脱钩」（见 check()）。
    """
    import openpyxl
    try:
        return openpyxl.load_workbook(path, data_only=True)
    except Exception:
        try:
            return openpyxl.load_workbook(path, data_only=True, keep_links=False)
        except Exception:
            return None


def load_json(p):
    """容错 BOM：PowerShell / Excel 导出的 json 都带 BOM。"""
    with io.open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def _cached_num(wb_cached, sheet, coord):
    """从缓存视图取数值；取不到返回 None。"""
    if wb_cached is None or sheet not in getattr(wb_cached, 'sheetnames', []):
        return None
    v = wb_cached[sheet][coord].value
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def cell_value(wb, sheet, cell, wb_cached=None):
    """取单格值。返回 (值, 是否公式)。

    公式格若在缓存视图里有数值，就返回该数值——调用方要的是「这一项等于几」，
    公式文本本身对比较没有意义。
    """
    if sheet not in wb.sheetnames:
        return None, False
    v = wb[sheet][cell].value
    is_f = isinstance(v, str) and v.startswith('=')
    if is_f:
        cv = _cached_num(wb_cached, sheet, cell)
        if cv is not None:
            return cv, True
    return v, is_f


def range_values(wb, sheet, rng, wb_cached=None):
    """取区域内的数值列表（跳空与非数值）。

    返回 (数值列表, 是否含公式, 无缓存值的公式格数)。
    第三项是「未运行」的判据：>0 说明本区间的合计**没法核**，
    不能当成「区间取值为空」判 FAIL。
    """
    if sheet not in wb.sheetnames:
        return None, False, 0
    ws = wb[sheet]
    out, has_formula, uncached = [], False, 0
    for row in ws[rng]:
        for c in row:
            v = c.value
            if isinstance(v, str) and v.startswith('='):
                has_formula = True
                cv = _cached_num(wb_cached, sheet, c.coordinate)
                if cv is not None:
                    out.append(cv)
                else:
                    uncached += 1
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(v)
    return out, has_formula, uncached


def num(x):
    return x if isinstance(x, (int, float)) else None


# ── 判定 ────────────────────────────────────────────────────
def check(path, spec, max_age_hours=None):
    wb = load_workbook(path)
    wb_cached = load_cached(path)      # 公式的缓存值视图（可为 None）
    results = []
    notrun = []          # ★v2.1.2：页签缺失 = 映射与表不匹配，未形成意见
                         # ★v4.5.1：公式无缓存值 = 没法核，同样记未运行（不判脱钩）
    pairs = spec.get('pairs') or []
    sums = spec.get('sums') or []

    for it in pairs:
        name = it.get('name', '?')
        a, b = it.get('a', {}), it.get('b', {})
        sa, ca = a.get('sheet'), a.get('cell')
        sb, cb = b.get('sheet'), b.get('cell')
        tol = it.get('tol', TOL_DEFAULT)
        mode = it.get('mode', 'either')

        miss = [s for s in (sa, sb) if s not in wb.sheetnames]
        if miss:
            # ★映射引用的页签不在工作簿里 = 映射与表不匹配，门禁**未形成意见**
            #   （与 v2.1.1 G-UNIT 同款语义）。记 notrun 判 2——
            #   不是「查到脱钩」判 1，也不是「通过」判 0。
            notrun.append('G-XS1 %s：工作簿里没有页签 %s' % (name, miss))
            continue

        va, fa = cell_value(wb, sa, ca, wb_cached)
        vb, fb = cell_value(wb, sb, cb, wb_cached)
        na, nb = num(va), num(vb)

        # derive 模式：至少一侧必须是公式引用（单一数据源），或两侧数值相等
        if mode == 'derive':
            if not (fa or fb):
                # 两侧都是硬值：允许，但必须在容差内
                pass
            elif (na is None or nb is None) and not (fa or fb):
                results.append((False, 'G-XS2',
                                '%s：%s!%s 与 %s!%s 至少一侧为公式，但两侧取不到数值'
                                % (name, sa, ca, sb, cb)))
                continue

        if na is None or nb is None:
            # ★v4.5.1：先分清「取不到数」和「取到了非数值」。
            #   公式格无缓存值 = 工作簿没被 Excel 重算保存过 → 本项无法核，判未运行；
            #   否则才是真正的「存在非数值」，判 FAIL。
            _formula_uncached = ((na is None and fa) or (nb is None and fb))
            if _formula_uncached:
                notrun.append(
                    'G-XS2 %s：%s!%s / %s!%s 是公式但工作簿里没有缓存值'
                    '（openpyxl 生成后未用 Excel 打开保存）→ 本项**无法核对**'
                    % (name, sa, ca, sb, cb))
                continue
            results.append((False, 'G-XS2',
                            '%s：%s!%s=%r / %s!%s=%r 存在非数值'
                            % (name, sa, ca, va, sb, cb, vb)))
            continue

        diff = abs(na - nb)
        if diff > tol:
            results.append((False, 'G-XS3',
                            '%s：%s!%s=%.2f ≠ %s!%s=%.2f（差 %.2f，容差 %.2f）'
                            '｜★典型的"两侧各自都对、中间脱钩"——查同步链断在哪一环'
                            % (name, sa, ca, na, sb, cb, nb, diff, tol)))
        else:
            results.append((True, 'G-XS3',
                            '%s：两侧一致 %.2f%s'
                            % (name, na, '（公式引用）' if (fa or fb) else '')))

    for it in sums:
        name = it.get('name', '?')
        s = it.get('sheet')
        tgt = it.get('equals', {})
        tol = it.get('tol', TOL_DEFAULT)
        if s not in wb.sheetnames or tgt.get('sheet') not in wb.sheetnames:
            # ★同上（v2.1.2 P2-8）：区间两侧页签缺失 → notrun，判 2 不判 1
            notrun.append('G-XS1 %s：页签不存在 %s / %s'
                          % (name, s, tgt.get('sheet')))
            continue
        la, fa, ua = range_values(wb, s, it.get('range', ''), wb_cached)
        lb, fb, ub = range_values(wb, tgt.get('sheet'), tgt.get('range', ''), wb_cached)
        if ua or ub:
            # ★v4.5.1：区间里有公式但无缓存值 → 合计无法核，判未运行。
            #   旧版走到下面 "区间取值为空" 判 FAIL，把「读不到」说成「脱钩」。
            notrun.append(
                'G-XS2 %s：区间 %s!%s / %s!%s 含 %d 个无缓存值的公式格 → 合计**无法核对**'
                % (name, s, it.get('range'), tgt.get('sheet'),
                   tgt.get('range'), ua + ub))
            continue
        if not la or not lb:
            results.append((False, 'G-XS2', '%s：区间取值为空' % name))
            continue
        sa_, sb_ = sum(la), sum(lb)
        if abs(sa_ - sb_) > tol:
            results.append((False, 'G-XS4',
                            '%s：%s!%s 合计 %.2f ≠ %s!%s 合计 %.2f（差 %.2f）'
                            % (name, s, it.get('range'), sa_,
                               tgt.get('sheet'), tgt.get('range'), sb_, sa_ - sb_)))
        else:
            results.append((True, 'G-XS4',
                            '%s：两区间合计一致 %.2f' % (name, sa_)))

    # 防陈旧：两侧页签写入时间不可比，但可查单元格是否为公式（活/死）
    dead = []
    for it in pairs:
        for side in (it.get('a', {}), it.get('b', {})):
            v, f = cell_value(wb, side.get('sheet'), side.get('cell'))
            if isinstance(v, (int, float)) and not f:
                dead.append('%s!%s' % (side.get('sheet'), side.get('cell')))
    return results, dead, notrun


def check_coverage(wb, spec):
    """G-XSC 映射覆盖率（1.2.0 新增）。

    **抓过的事故（本项目 2026-10-20 实测）**
    箱涵混凝土在批次表里是 10,127.7 m³，主表还停在 4,911.13 m³。
    跨表门禁当时是绿的——因为映射文件里只登记了几组关键值，**箱涵那几行压根没进映射**。
    ★"映射不全"是跨表门禁**唯一**的失效方式：没登记的项，门禁连看都不会看。

    判据
      G-XSC1 [FAIL] 源表存在未被任何 pair/sum 覆盖的编码，且不在 exclude 里
      G-XSC2 [WARN] exclude 里的编码在源表中不存在 → 映射已过期（表改过编码）
      G-XSC3 [INFO] 覆盖率统计

    映射文件格式（可选块，缺省不启用，保证向后兼容）：
      "coverage": {
        "sheet": "1_成本取量总表",   // 源表页签（默认取第一个 pair 的 a.sheet）
        "code_col": 2,               // 编码所在列
        "min_coverage": 1.0,          // 低于此比例即 FAIL，默认 1.0
        "exclude": [{"code": "CBS-X", "reason": "口径不可比，不参与跨表比对"}]
      }
    """
    out = []
    cov = spec.get('coverage')
    if not isinstance(cov, dict):
        out.append((None, 'G-XSC3',
                    '未声明 coverage 块 → **无法证明映射覆盖全表**，'
                    '跨表门禁只查了登记过的那几项（这是它唯一的失效方式）'))
        return out

    src_sheet = cov.get('sheet')
    if not src_sheet:
        for it in (spec.get('pairs') or []):
            src_sheet = it.get('a', {}).get('sheet')
            if src_sheet:
                break
    if not src_sheet or src_sheet not in wb.sheetnames:
        out.append((False, 'G-XSC1', 'coverage.sheet 不存在或未指定：%r' % src_sheet))
        return out

    code_col = int(cov.get('code_col', 2))
    min_cov = float(cov.get('min_coverage', 1.0))
    exclude = {str(x.get('code')): str(x.get('reason') or '')
               for x in (cov.get('exclude') or []) if x.get('code')}

    ws = wb[src_sheet]

    def code_at(row):
        return str(ws.cell(row, code_col).value or '').strip()

    # 源表全部编码（出现顺序去重）。★跳过表头行：编码列里写着"编码/CODE"的不是编码
    HDR = ('编码', 'CODE', '码', 'SHEET', '序号', 'NO.')
    all_codes, code_rows = [], {}
    for r in range(1, ws.max_row + 1):
        c = code_at(r)
        if not c or any(h in c.upper() for h in HDR):
            continue
        if c not in code_rows:
            all_codes.append(c)
            code_rows[c] = r

    # 被映射点到的行号
    hit_rows = set()
    for it in (spec.get('pairs') or []):
        for side in ('a', 'b'):
            cell = str(it.get(side, {}).get('cell') or '')
            m = re.match(r'^\$?([A-Z]+)\$?(\d+)$', cell.replace(' ', ''))
            if m and it.get(side, {}).get('sheet') == src_sheet:
                hit_rows.add(int(m.group(2)))
    for it in (spec.get('sums') or []):
        if it.get('sheet') == src_sheet:
            rng = str(it.get('range') or '')
            m = re.match(r'^\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)$', rng.replace(' ', ''))
            if m:
                hit_rows.update(range(int(m.group(2)), int(m.group(4)) + 1))

    covered = [c for c in all_codes if code_rows[c] in hit_rows]
    missing = [c for c in all_codes if c not in covered and c not in exclude]
    stale_ex = [c for c in exclude if c not in code_rows]

    total = len(all_codes)
    ratio = (len(covered) / float(total)) if total else 1.0

    if missing:
        msg = ('%d/%d 个编码未进映射（覆盖 %.0f%%）：%s'
               % (len(missing), total, ratio * 100, '、'.join(missing[:8])))
        if ratio < min_cov:
            out.append((False, 'G-XSC1',
                        msg + '｜★这些行的跨表一致性**从未被检查**，'
                              '要么登记进 pairs/sums，要么进 exclude 并写理由'))
        else:
            out.append((True, 'G-XSC2', msg + '（已达 min_coverage=%.2f）' % min_cov))
    else:
        out.append((True, 'G-XSC3',
                    '映射覆盖全部 %d 个编码（exclude %d 个）' % (total, len(exclude))))

    if stale_ex:
        out.append((None, 'G-XSC2',
                    'exclude 里有 %d 个编码在源表中不存在：%s → '
                    '映射已过期（表改过编码或删过行），请同步'
                    % (len(stale_ex), '、'.join(stale_ex[:6]))))
    return out


def main():
    ap = argparse.ArgumentParser(
        description='跨表/跨页签一致性门禁（G-XS）')
    ap.add_argument('xlsx')
    ap.add_argument('--map', required=True, help='映射 json（pairs + sums）')
    ap.add_argument('--max-age-hours', type=float, default=None)
    ap.add_argument('--allow-hardcoded', action='store_true',
                    help='允许映射格里是硬写死的值（默认不允许：'
                         '跨表派生值应引用源，两处各写一遍数必然脱钩）')
    a = ap.parse_args()

    # ★先校验参数文件本身存在——否则下面崩掉时 Python 仍返回退出码 1，
    #   看起来像"抓到脱钩"，实则是门禁自己没跑起来（2026-07-20 自测踩到）。
    #   这是**假通过**：调用方无法区分"报红"与"崩了"。
    if not os.path.exists(a.map):
        print('[FAIL] G-XS0 映射文件不存在：%s' % a.map)
        print('　　门禁**未能执行**，这个退出码不代表检查结论。'
              '请先按 SKILL.md 第 3 步 --init 生成映射文件。')
        return 2                      # ★2 = 执行失败，与 1（检查不通过）区分
    if not os.path.exists(a.xlsx):
        print('[FAIL] G-XS0 表文件不存在：%s' % a.xlsx)
        return 2

    try:
        spec = load_json(a.map)
    except Exception as e:
        print('[FAIL] G-XS0 映射文件解析失败：%s' % e)
        print('　　门禁**未能执行**，这个退出码不代表检查结论。')
        return 2

    if not isinstance(spec, dict):
        print('[FAIL] G-XS0 映射文件顶层必须是对象（含 pairs / sums）')
        return 2

    print('跨表一致性门禁 G-XS | 表：%s | 映射 %d 对 / %d 组区间'
          % (os.path.basename(a.xlsx),
             len(spec.get('pairs') or []), len(spec.get('sums') or [])))

    try:
        results, dead, notrun = check(a.xlsx, spec, a.max_age_hours)
        cov_notes = []
        try:
            _wb = load_workbook(a.xlsx)
            cov_out = check_coverage(_wb, spec)
        except Exception as e:
            # 覆盖率检查跑不起来 = 无法排除"映射不全"，而这正是本门禁唯一
            # 的失效方式。造一条 G-XSC1 FAIL 会让人以为"映射确实不全"，
            # 判「未运行」才准确。
            print('[未执行] G-XSC1 覆盖率检查执行失败：%s' % e)
            print('         无法证明映射覆盖全表，'
                  '本次的"数值一致"不能作为跨表已核的依据。')
            print('[未执行] 退出码 2（门禁无法运行）——'
                  '这不是"检查不通过"，也不是"通过"。')
            return 2
        for ok, code, msg in cov_out:
            # ok=None 表示这是提示项（不阻断），单独打印为 INFO
            if ok is None:
                cov_notes.append((code, msg))
            else:
                results.append((ok, code, msg))
    except Exception as e:
        print('[FAIL] G-XS0 门禁执行失败：%s' % e)
        return 2                      # ★同上：崩了不等于报红

    fails = [r for r in results if not r[0]]
    for ok, code, msg in results:
        print('[%s] %s %s' % ('PASS' if ok else 'FAIL', code, msg))
    for code, msg in cov_notes:
        print('[INFO] %s %s' % (code, msg))
    for msg in notrun:
        print('[未执行] %s——这一项未检查（不是通过，也不是脱钩）' % msg)

    if dead and not (spec.get('stale_ok', False) or a.allow_hardcoded):
        print('[WARN] G-XS5 映射中这些格是**硬写死的值**（非公式）：%s'
              '｜★跨表派生值应引用源（把其中一侧改为跨表公式），两处各写一遍数必然脱钩。'
              '本次数值虽一致，但下次改源表仍会脱钩。'
              % '；'.join(dead[:6]))
        print('　　 确实无法改为公式时加 --allow-hardcoded 显式接受，'
              '并记入交付说明。')

    n_ok = sum(1 for r in results if r[0])
    print('——')
    print('一致 %d / 共 %d 项；硬写死格 %d 个'
          % (n_ok, len(results), len(dead)))
    if fails:
        print('!! 未通过：跨表脱钩。先查同步链是哪一环断了，'
              '不要只改数字让门禁变绿。')
        return 1
    if notrun:
        print('[未执行] 退出码 2（门禁无法运行）——%d 项未检查'
              '（页签缺失，或公式无缓存值）'
              '这不是「检查不通过」，也不是「通过」。' % len(notrun))
        return 2
    print('== 跨表一致性门禁通过 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())