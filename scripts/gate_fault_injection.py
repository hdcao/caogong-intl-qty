# -*- coding: utf-8 -*-
"""
门禁自证：往算量表里注入已知缺陷，看门禁抓不抓得到。

为什么必须有这个脚本（本项目踩过的坑）：
  建了 40 多条门禁断言，其中大量是 `"groundYAt" in h` 这样的**字符串存在性检查**。
  它能防"代码被删"，防不了"代码还在但算错了"。
  实测：往实现里注入一个错误函数，门禁仍然全 PASS——拦截率 0。
  **一个从没抓过任何东西的门禁，等于没有门禁。**

  本脚本做反向验证：
  1. 备份原表
  2. 逐个注入已知缺陷（量改错 / 口径删掉 / 公式错位 / 合计漏项/ 基准不符）
  3. 跑对应门禁，看是否报 FAIL
  4. 还原原表，用文件摘要确认一字未改
  5. 汇总捕获率；捕获率不达100% 不算这套门禁上线

用法：
  python gate_fault_injection.py <算量表.xlsx> [--sheet 1_成本取量总表]
退出码：全部捕获返回 0，有漏网返回 1
"""
import sys, os, re, glob, json, io, hashlib, shutil, tempfile, subprocess, argparse

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

HERE = os.path.dirname(os.path.abspath(__file__))


def digest(path):
    return hashlib.md5(open(path, 'rb').read()).hexdigest()[:12]


def find_row(ws, code):
    for r in range(1, ws.max_row + 1):
        if str(ws.cell(r, 2).value or '') == code:
            return r
    return None


def find_total_row(ws):
    """找最终合计行。★1.2.0：改为多列扫——v1.1.0 只扫第 3 列，
    排水表/E3 的合计文字在第 2 列，于是"合计区间截断"用例在 E3 上永远"未注入"，
    看着像表结构不支持，其实是**注入器自己找错了列**。"""
    cands = []
    for r in range(1, ws.max_row + 1):
        txt = ' '.join(str(ws.cell(r, c).value or '')
                       for c in range(1, min(ws.max_column, 10) + 1))
        if '合计' in txt and '★' in txt:
            cands.append(r)
    return max(cands) if cands else None


# ── 注入用例：每个用例只破坏一件事 ──
# kind: 要注入的缺陷
# gate: 应该抓到它的门禁脚本名
CASES = [
    {
        'name': '量改错（基准值改成错值）',
        'kind': 'wrong_qty', 'gate': 'gate_drawing_basis.py',
        'detail': '把已知图纸基准的量改成别的数，看基准核对是否报红',
        # ★基准门禁必须有基准文件才能比；缺省用 --init 生成的是全 0 模板，
        #   反而会被 G-B0判为"占位值"而必然 FAIL——那不是真的抓到。
        'args': ['--basis', '@BASIS@'],
    },
    {
        'name': '口径声明被删掉',
        'kind': 'drop_scope', 'gate': 'gate_scope_declaration.py',
        'detail': '删掉某行的口径声明（改写备注使口径词消失）',
    },
    {
        'name': '公式错位一行',
        'kind': 'misalign_formula', 'gate': 'gate_sheet_formula.py',
        'detail': '把某行 =E{n}*G{n} 改成 =E{n-1}*G{n-1}',
    },
    {
        'name': '合计区间截断（漏若干行）',
        'kind': 'truncate_sum', 'gate': 'gate_sheet_formula.py',
        'detail': '把合计的 SUM 区间终点提前，制造漏项',
    },
    # ── 1.1.0 新增三个用例（对应新增的三个门禁）──
    {
        'name': '跨表脱钩（另一页签保持旧值）',
        'kind': 'cross_sheet_stale', 'gate': 'gate_cross_sheet.py',
        'detail': '把本页签的派生值改成与另一页签不同，模拟"同步链断了一环"',
        'args': ['--map', '@XS_MAP@'],
        'needs_sheet': False,     # 页签写在映射里，不接受 --sheet
    },
    {
        'name': 'OPTIONS 被误标为基础分部',
        'kind': 'options_mislabeled', 'gate': 'gate_scope_options.py',
        'detail': '把 OPTIONS 行的 CPB 编码改成 2.x，模拟"忘了标可选项"',
        # ★必须带 --summary：该门禁只有与汇总表核对（G-P3）才能发现
        #   "有行漏标"——单看本表它只会认为"少了一个 OPTIONS"，不会报错。
        'args': ['--summary', '@SELF@', '--summary-cell', 'C7'],
    },
    {
        'name': '单位量纲与单价不配套（单价×10）',
        'kind': 'unit_price_10x', 'gate': 'gate_unit_consistency.py',
        'detail': '把某体积行的单价 ×10，模拟 m³ 与 m³×10 混记',
        # ★该门禁刻意只出 WARN（离群可能是特殊材料，机器不该替人判），
        #   故按输出中的 WARN 标识判定，不按退出码。
        'severity': 'warn', 'marker': 'WARN',
    },
    # ── 1.2.0 新增三个用例（对应新增/升级的三个门禁）──
    {
        'name': '分组小计 SUM 区间漏末行',
        'kind': 'subtotal_truncate', 'gate': 'gate_sheet_formula.py',
        'detail': '把某个**分组小计**（不是最终合计）的 SUM 区间终点提前一行',
        # ★这个用例针对 1.1.0 的结构性漏检：老判据只取最后一个"★合计"行，
        #   9 个分组小计错了 8 个却全绿。若有表只有一个合计行、没有分组小计，
        #   注入无效（记为"未注入"），**不计入分母**。
    },
    {
        'name': '同一实物复制成第二个计量入口',
        'kind': 'dup_owner', 'gate': 'gate_single_owner.py',
        'detail': '把某数据行整行复制到表末（同码 / 同名称 / 同单位）',
        # 对应 R3「一个实物多个计量入口」——本项目墩身+墩墙双计的实际形态。
    },
    {
        'name': '基准过期（表比基准新）',
        'kind': 'basis_stale', 'gate': 'gate_basis_freshness.py',
        'detail': '把基准 verified_at 改成 2001 年，模拟"改了表但没更新基准"',
        # ★注入对象是 json 而不是 xlsx —— 这类用例在 main 里单独处理。
        'args': ['--basis', '@BASIS_FRESH@'],
        'file_kind': 'basis',
    },
]


def apply_injection(path, sheet, kind, xs_map=None):
    """就地注入缺陷，返回是否成功注入。"""
    # utf-8-sig 兼容：openpyxl 对带BOM 的 xlsx 可能不认
    try:
        wb = openpyxl.load_workbook(path, data_only=False)
    except UnicodeDecodeError:
        wb = openpyxl.load_workbook(path.decode() if isinstance(path, bytes) else path,
                                    data_only=False, rich_text=False)
    ws = wb[sheet]
    tr = find_total_row(ws)

    if kind == 'wrong_qty':
        # 找一行有备注的，改它的量
        for r in range(1, tr or ws.max_row):
            if str(ws.cell(r, 2).value or '') and isinstance(ws.cell(r, 5).value, (int, float)):
                v = ws.cell(r, 5).value
                ws.cell(r, 5).value = v * 1.37 + 1   # 故意改成一个明显不同的数
                wb.save(path)
                return 'r%d %s：%s → %s' % (r, ws.cell(r, 2).value, v, ws.cell(r, 5).value)

    elif kind == 'drop_scope':
        # 口径门禁优先检查结构化的“范围口径/计量口径”列；旧模板才回退到
        # 备注/说明列。注入必须打到门禁真实读取的字段，否则不能算成功注入。
        scope_col = None
        note_col = None
        for hr in range(1, min(ws.max_row, 5) + 1):
            for hc in range(1, ws.max_column + 1):
                hv = str(ws.cell(hr, hc).value or '').strip()
                if hv in ('范围口径', '计量口径') and scope_col is None:
                    scope_col = hc
                if hv == '备注':
                    note_col = hc
                elif hv == '说明' and note_col is None:
                    note_col = hc
        target_col = scope_col or note_col or 9
        scope_tokens = ('全桥', '单幅', '两幅', '双幅', '单侧', '两侧', '双侧',
                        '全线', '全段', '全长', '整体口径', '每座', '单座',
                        '每根', '每跨', '每延米', '单件', '每件', '合计口径', 'LOT')
        token_re = re.compile(r'(全桥|单幅|两幅|双幅|单侧|两侧|双侧|全线|全段|全长|整体口径|每座|单座|每根|每跨|每延米|单件|每件|合计口径|\bLOT\b)')
        for r in range(1, tr or ws.max_row):
            note = ws.cell(r, target_col).value
            if note and any(k in str(note) for k in scope_tokens):
                new = token_re.sub('', str(note))
                ws.cell(r, target_col).value = new[:300]
                wb.save(path)
                return 'r%d %s：结构化口径列第%d列的口径词已移除' % (r, ws.cell(r, 2).value, target_col)

    elif kind == 'misalign_formula':
        for r in range(2, (tr or ws.max_row) - 1):
            f = ws.cell(r, 8).value
            m = re.match(r'^=E(\d+)\*G(\d+)$', str(f or '').strip())
            if m and int(m.group(1)) == r:
                ws.cell(r, 8).value = '=E%d*G%d' % (r - 1, r - 1)
                wb.save(path)
                return 'r%d：公式改为引用 r%d' % (r, r - 1)

    elif kind == 'truncate_sum':
        if tr:
            m = re.match(r'^=SUM\(H(\d+):H(\d+)\)$', str(ws.cell(tr, 8).value or '').strip())
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                ws.cell(tr, 8).value = '=SUM(H%d:H%d)' % (lo, max(lo, hi - 5))
                wb.save(path)
                return '合计区间截断至 H%d:H%d（原 H%d:H%d）' % (lo, max(lo, hi - 5), lo, hi)

    # ── 1.1.0 新增注入 ──────────────────────────────────────
    elif kind == 'cross_sheet_stale':
        # ★2026-07-20 修正：跨表门禁比的是**同一个工作簿内的两个页签**，
        #   所以必须改「映射里点名的那一页签」，而不是默认第一页签。
        #   初版改第一页签 → 门禁压根没被触到（页签不存在），却报成MISSED，
        #   把"注入无效"误判成"门禁无效"。
        target_sheet, target_cell = None, None
        try:
            spec = json.load(io.open(xs_map, encoding='utf-8-sig'))
            pr = (spec.get('pairs') or [{}])[0]
            target_sheet = pr.get('a', {}).get('sheet')
            target_cell = pr.get('a', {}).get('cell')
        except Exception:
            pass
        if not target_sheet or target_sheet not in wb.sheetnames:
            return None      # ← 注入无效，交由上层记为「未注入」而非 MISSED
        tws = wb[target_sheet]
        v = tws[target_cell].value
        if not isinstance(v, (int, float)) or not v:
            return None
        tws[target_cell].value = round(v * 0.4, 2)
        wb.save(path)
        return ('%s!%s：%.2f → %.2f（该页签与另一页签脱钩）'
                % (target_sheet, target_cell, v, tws[target_cell].value))

    elif kind == 'options_mislabeled':
        # 把 CPB 以 4 开头**且有金额**的行改成 2.x
        # ★2026-07-20 修正：初版选中了标题行（A列以 4 开头但 E/G 为空），
        #   注入等于无效，门禁自然抓不到——**注入无效 ≠ 门禁无效**，必须区分。
        for r in range(3, ws.max_row + 1):
            cpb = str(ws.cell(r, 1).value or '').strip()
            q, p = ws.cell(r, 5).value, ws.cell(r, 7).value
            if (cpb.startswith('4') and isinstance(q, (int, float))
                    and isinstance(p, (int, float))):
                ws.cell(r, 1).value = '2.3.9'
                wb.save(path)
                return ('r%d：CPB %s → 2.3.9（不再是 OPTIONS），金额 %.2f'
                        % (r, cpb[:16], q * p))

    elif kind == 'unit_price_10x':
        # 体积行的单价 ×10 —— 模拟 m³ 与 m³×10 混记
        # ★2026-07-20 修正：初版选中了**量最大的行**（14912 m³ 承台），
        #   ×10 后仍不是离群值，门禁看不到。
        #   改为选**单价处于中位附近**的行——它 ×10 必然成为离群。
        vol = []
        for r in range(3, ws.max_row + 1):
            u = str(ws.cell(r, 4).value or '').strip().upper()
            p = ws.cell(r, 7).value
            if u in ('CM', 'M3', 'M³') and isinstance(p, (int, float)) and p:
                vol.append((r, p))
        if vol:
            med = sorted(x[1] for x in vol)[len(vol) // 2]
            r, p = min(vol, key=lambda x: abs(x[1] - med))
            ws.cell(r, 7).value = round(p * 10, 2)
            wb.save(path)
            return ('r%d：单价 %.2f（中位 %.2f）×10 → %.2f（量纲错配）'
                    % (r, p, med, p * 10))

    # ── 1.2.0 新增注入 ──────────────────────────────────────
    elif kind == 'subtotal_truncate':
        # 找**分组小计**（不是最后一个合计）的 SUM(range)，把终点提前一行。
        # ★必须选"区间行数 ≥2"的小计，只含一行的区间无法截断（记为未注入）。
        subs = []
        for r in range(1, ws.max_row + 1):
            txt = ' '.join(str(ws.cell(r, c).value or '')
                           for c in range(1, min(ws.max_column, 12) + 1)).upper()
            if not any(w in txt for w in ('小计', '合计', '总计', 'SUBTOTAL', 'TOTAL')):
                continue
            for col in (5, 8):
                m = re.match(r'^=SUM\(([A-Z]+)(\d+):([A-Z]+)(\d+)\)$',
                             str(ws.cell(r, col).value or '').strip(), re.I)
                if m and int(m.group(4)) > int(m.group(2)):
                    subs.append((r, col, m.group(2), m.group(4)))
        if len(subs) >= 1:
            # 优先改第 1 个（分组小计），不是最后一个（最终合计）
            r, col, lo, hi = subs[0]
            new_hi = int(hi) - 1
            old = ws.cell(r, col).value
            ws.cell(r, col).value = '=SUM(%s%s:%s%d)' % (
                openpyxl.utils.get_column_letter(col), lo,
                openpyxl.utils.get_column_letter(col), new_hi)
            wb.save(path)
            return 'r%d 小计区间 %s → 漏 r%d' % (r, old, int(hi))

    elif kind == 'dup_owner':
        # 整行复制到表末：同码/同名称/同单位 → 第二个计量入口
        src = None
        for r in range(2, ws.max_row + 1):
            code = str(ws.cell(r, 2).value or '').strip()
            q = ws.cell(r, 5).value
            if code and isinstance(q, (int, float)) and q:
                src = r
                break
        if src:
            tr = max(ws.max_row, 1) + 2   # 空两行，避免落进合计区间
            for c in range(1, min(ws.max_column, 12) + 1):
                ws.cell(tr, c).value = ws.cell(src, c).value
            ws.cell(tr, 9).value = '（注入）同一实物的第二个计量入口'
            wb.save(path)
            return 'r%d → 复制到 r%d（%s 出现两次）' % (
                src, tr, str(ws.cell(src, 2).value or '')[:20])

    wb.save(path)
    return None


def run_gate(case, path, sheet, allow_subprocess, extra=None):
    gate = case['gate']
    """调起另一个门禁脚本跑一次，看它报不报错。

    ★安全边界（这是本脚本唯一会起子进程的地方，故显式设闸）：
      - 只调**同目录**下名字以 gate_ 开头的 .py，不接受任意命令
      - 只传本次临时副本路径与固定参数，不传用户输入的字符串
      - extra 只接受 CASES 里显式声明的固定参数，不接受外部输入
      - allow_subprocess=False 时不执行，只做静态存在性检查
    ★捕获证据语义：exit 2 或非 0/1 退出码一律不算捕获；exit 1 还必须伴随目标门禁
      明确的 [FAIL]/未通过标记，且不能出现 traceback/运行时异常；告警型用例需命中指定 WARN 标识。
    """
    p = os.path.join(HERE, gate)
    if not os.path.exists(p):
        return None, '门禁脚本不存在'
    if not os.path.basename(p).startswith('gate_'):
        return None, '拒绝调用：不是 gate_*.py'
    if not allow_subprocess:
        return None, '未授权执行子进程（--allow-subprocess 未开启）'
    cmd = [sys.executable, os.path.basename(p), path]
    # ★2026-07-20 修正：**不是所有门禁都接受 --sheet**。
    #   跨表门禁的页签写在映射文件里，传 --sheet 会让 argparse 直接报错
    #   （exit 2）→ 被误判成"门禁没抓到"。这是调用链缺陷，不是门禁弱。
    #   故由各用例显式声明 needs_sheet，默认 True。
    if case.get('needs_sheet', True):
        cmd += ['--sheet', sheet]
    for a in (extra or []):
        cmd.append(a)
    rr = subprocess.run(
        cmd,
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=120, cwd=HERE)
    out = (rr.stdout or '')
    if rr.returncode == 2:
        return False, '门禁自身执行失败（exit 2），不算抓到：%s' % out[-200:]
    if rr.returncode not in (0, 1):
        return False, '门禁异常退出码 %s，不算抓到：%s' % (rr.returncode, out[-200:])

    # ★捕获必须由“目标门禁明确报告的阻断/告警”证明，而不能只看 exit!=0。
    # 否则 traceback、未处理异常、坏路径或其他运行错误也可能被错记为 CAUGHT。
    sev = case.get('severity', 'exit')
    if sev == 'warn':
        marker = case.get('marker', 'WARN')
        if rr.returncode == 0 and ('[%s]' % marker) in out:
            return True, 'exit 0；目标告警标识 [%s] 已出现' % marker
        return False, '未取得目标告警证据（exit=%s）：%s' % (rr.returncode, out[-200:])

    if rr.returncode != 1:
        return False, '门禁 exit 0，未阻断已注入缺陷：%s' % out[-200:]
    runtime_error_markers = (
        'Traceback (most recent call last):', 'Unhandled exception',
        '未处理异常', '程序崩溃', '[E2E HARNESS ERROR]')
    if any(marker in out for marker in runtime_error_markers):
        return False, '疑似运行时异常（即使 exit 1 也不算捕获）：%s' % out[-200:]
    gate_failure_markers = ('[FAIL]', '!! 未通过', '检查不通过', '门禁未通过', '存在 FAIL')
    evidence_line = next((line.strip() for line in reversed(out.splitlines())
                          if any(marker in line for marker in gate_failure_markers)), None)
    if evidence_line:
        return True, 'exit 1 + 明确失败判据：%s' % evidence_line[-180:]
    return False, 'exit 1 但无明确门禁失败标识，不算捕获：%s' % out[-200:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='1_成本取量总表')
    ap.add_argument('--allow-subprocess', action='store_true',
                    help='开启后才真正执行注入并调起门禁验证（缺省只做演练）')
    ap.add_argument('--xs-map', default=None,
                    help='跨表门禁用的映射 json 路径（用例「跨表脱钩」需要）')
    ap.add_argument('--summary', default=None,
                    help='汇总表路径（用例「OPTIONS 漏标」需要——'
                         '只有与汇总核对才能发现漏标）')
    ap.add_argument('--basis', default=None,
                    help='图纸基准 json（用例「量改错」需要；'
                         '缺省自动生成"注入前实算值"当临时基准）')
    a = ap.parse_args()

    # 子进程统一以 scripts/ 为 cwd。外部传入的相对路径必须在改变 cwd 前
    # 固化为绝对路径，否则例如 --map ../examples/... 会变成“找不到文件”，
    # 并把调用链故障误判成门禁捕获/漏检。
    if a.xs_map:
        a.xs_map = os.path.abspath(a.xs_map)
    if a.summary:
        a.summary = os.path.abspath(a.summary)
    if a.basis:
        a.basis = os.path.abspath(a.basis)

    src = os.path.abspath(a.xlsx)
    if not os.path.isfile(src):
        print('状态：unavailable —— 找不到算量表 %s' % src)
        return 2

    before = digest(src)
    tmpdir = tempfile.mkdtemp(prefix='gfi_')
    tmp = os.path.join(tmpdir, os.path.basename(src))
    shutil.copy2(src, tmp)

    print('门禁自证 | 注入目标：%s' % os.path.basename(src))
    print('原文件摘要 %s（结束前会比对，确保一字未改）' % before)
    print()

    results = []
    # OPTIONS 漏标用例需要汇总表；缺省用被注入表自身所在目录里的汇总表
    summary_path = a.summary
    if summary_path is None:
        for fn in os.listdir(os.path.dirname(src) or '.'):
            if fn.startswith('全项目成本汇总') and fn.endswith('.xlsx'):
                summary_path = os.path.join(os.path.dirname(src), fn)
                break

    # 基准门禁需要基准文件：优先用 --basis，否则临时生成一份"注入前实算值"当基准
    tmp_basis = None
    if not a.basis:
        tmp_basis = os.path.join(tmpdir, '_basis_auto.json')
        try:
            import openpyxl as _ox
            _ws = _ox.load_workbook(tmp, data_only=False)[a.sheet]
            _items = []
            for _r in range(3, min(_ws.max_row, 12) + 1):
                _code = str(_ws.cell(_r, 2).value or '').strip()
                _q = _ws.cell(_r, 5).value
                if _code and isinstance(_q, (int, float)):
                    _items.append({'code': _code, 'qty': _q,
                                   'basis': '注入前实算（自证临时基准）'})
            with io.open(tmp_basis, 'w', encoding='utf-8') as f:
                json.dump({'note': '自证临时基准，注入前实算值',
                           'items': _items}, f, ensure_ascii=False)
        except Exception as e:
            tmp_basis = None
            print('[WARN] 临时基准生成失败：%s' % e)

    # 1.2.0：为"基准过期"用例准备一份**新鲜基准**（verified_at 在未来），
    #   注入时再把它改成 2001 年——两个状态都自己造，不依赖用户手上有基准文件。
    tmp_fresh = os.path.join(tmpdir, '_basis_fresh.json')
    fresh_ok = False
    try:
        import openpyxl as _ox2
        _ws2 = _ox2.load_workbook(tmp, data_only=False)[a.sheet]
        _items2 = []
        for _r in range(2, min(_ws2.max_row, 15) + 1):
            _c = str(_ws2.cell(_r, 2).value or '').strip()
            _q = _ws2.cell(_r, 5).value
            if _c and isinstance(_q, (int, float)) and _q:
                _items2.append({'code': _c, 'qty': _q,
                                'unit': str(_ws2.cell(_r, 4).value or ''),
                                'basis': '注入自证临时基准（非图纸读数）',
                                'asof': '2026-10-01'})
        if _items2:
            with io.open(tmp_fresh, 'w', encoding='utf-8') as f:
                json.dump({'drawing_set_rev': '注入自证',
                           'verified_at': '2099-01-01 09:00',
                           'items': _items2}, f, ensure_ascii=False)
            fresh_ok = True
    except Exception as e:
        print('[WARN] 临时新鲜基准生成失败：%s' % e)

    for case in CASES:
        # ── 注入对象是 json 的用例（基准过期）单独处理 ──
        if case.get('file_kind') == 'basis':
            if not fresh_ok:
                results.append((case['name'], '跳过',
                                '本表无法生成临时基准（无编码+量行）'))
                continue
            try:
                with io.open(tmp_fresh, encoding='utf-8') as f:
                    _d = json.load(f)
                _d['verified_at'] = '2001-01-01 00:00'
                _bad = os.path.join(tmpdir, '_basis_stale.json')
                with io.open(_bad, 'w', encoding='utf-8') as f:
                    json.dump(_d, f, ensure_ascii=False)
            except Exception as e:
                results.append((case['name'], '未注入', '基准副本构造失败：%s' % e))
                continue
            shutil.copy2(src, tmp)
            caught, out = run_gate(case, tmp, a.sheet, a.allow_subprocess,
                                   ['--basis', _bad])
            results.append((case['name'], 'CAUGHT' if caught else 'MISSED',
                            'verified_at → 2001-01-01（表比基准新）；门禁证据：%s' % out))
            continue

        # 该用例声明的额外参数
        extra, skip = [], None
        for x in (case.get('args') or []):
            if x == '@XS_MAP@':
                if not a.xs_map:
                    skip = '需 --xs-map 指定跨表映射文件'
                    break
                extra.append(a.xs_map)
            elif x == '@BASIS@':
                bp = a.basis or tmp_basis
                if not bp or not os.path.isfile(bp):
                    skip = '需 --basis 指定基准文件'
                    break
                extra.append(bp)
            elif x == '@SELF@':
                if not summary_path or not os.path.isfile(summary_path):
                    skip = '需 --summary 指定汇总表（或与算量表同目录）'
                    break
                extra.append(summary_path)
            else:
                extra.append(x)
        if skip:
            results.append((case['name'], '跳过', skip))
            continue

        # 每次从干净副本开始
        shutil.copy2(src, tmp)
        info = apply_injection(tmp, a.sheet, case['kind'], a.xs_map)
        if not info:
            # ★注入无效（结构不符/目标页签不存在）——必须与"门禁没抓到"分开记，
            #   否则会把调用链缺陷当成门禁缺陷去修，方向就错了。
            results.append((case['name'], '未注入',
                            '本表结构不支持该注入（目标页签/行不存在），'
                            '**这一项不计入分母**——它不说明门禁强弱'))
            continue
        caught, out = run_gate(case, tmp, a.sheet,
                               a.allow_subprocess, extra)
        detail = '%s；门禁证据：%s' % (info, out)
        results.append((case['name'], 'CAUGHT' if caught else 'MISSED', detail))

    for name, verdict, info in results:
        print('%-28s %-8s %s' % (name, verdict, str(info)[:60]))

    shutil.rmtree(tmpdir, ignore_errors=True)

    after = digest(src)
    intact = (after == before)
    print()
    print('原文件摘要 %s → %s  %s' % (before, after, '一字未改 ✓' if intact else '被改动 ✗'))

    if not a.allow_subprocess:
        print('★ 当前为演练模式：只造临时副本、只判定门禁文件是否存在，**没有执行门禁**。')
        print('  演练不构成"门禁有效"的证据。要真正验证，加 --allow-subprocess 重跑。')
        print('  ↑上面那些 MISSED 是演练模式的必然结果，**不代表门禁无效**。')
        print()
        print('[未执行] 演练模式退出码 2 —— 这不是"通过"，也不是"检查不通过"。')
        return 2

    caught_n = sum(1 for _, v, _ in results if v == 'CAUGHT')
    # ★2026-07-20 修正分母：**只有"成功注入"的用例才说明门禁强弱**。
    #   「未注入」（表结构不支持该注入）与「跳过」（缺必需参数）都**不计入分母**——
    #   否则会把"调用链缺陷/目标选错"误判成"门禁无效"，修错方向。
    denom = sum(1 for _, v, _ in results if v not in ('未注入', '跳过'))
    total = len(results)
    print('捕获率 %d/%d（分母=成功注入的用例数；'
          '「未注入」「跳过」不计入——它们不说明门禁强弱）'
          % (caught_n, denom))
    print('用例覆盖 %d/%d —— 分母缩水说明这张表只支持部分注入，'
          '剩下的判据并未被验证。' % (denom, total))

    if not intact:
        print('!! 自证失败：原文件被改动，中止')
        return 1
    if not a.allow_subprocess:
        print('== 演练完成（未执行门禁，不代表自证通过）==')
        return 0
    if caught_n < denom:
        miss = [n for n, v, _ in results if v == 'MISSED']
        print('!! 未通过：以下缺陷**成功注入但未被捕获** → %s' % '；'.join(miss))
        print('   这才是门禁真的弱。先补判据或换更严的断言，再重跑。')
        return 1
    if denom == 0:
        print('!! 未通过：没有任何用例成功注入——'
              '本表结构与注入用例不匹配，先确认用例是否适用这张表。')
        return 1
    print('== 门禁自证通过：每个成功注入的缺陷都被抓住了 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())