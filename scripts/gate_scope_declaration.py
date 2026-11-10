# -*- coding: utf-8 -*-
"""
口径声明门禁：确保每一行的量都声明了它是哪个口径。

背景（本项目三次同源错误的共同根因）：
  1. 桥2 桥面宽：把纵向梁区总长 25998 当成横向桥面宽 15750（维度错误，-39.4%）
  2. 桥1 承台数：只看了单幅的 A1-P1-P2-P3，实际南北双幅共 6 个（少算一半）
  3. 桥2 支座：图纸 36 是单幅数，全桥应为 36x2=72（差点误改）

这三次都不是"算错"，是"算的是另一个口径"，而表里没有写明是哪个口径。
本门禁把口径从"记在备注里的期望"变成"机器可核的字段"。

检查两件事：
  G-A  凡是数量行，必须在专用“范围口径/计量口径”列声明口径；旧模板才回退到名称+备注
  G-B  单侧与两侧、单幅与全桥必须成对出现（避免"只写了一半"）

用法：
  python gate_scope_declaration.py <算量表.xlsx> [--sheet 1_成本取量总表]
退出码：有FAIL 返回 1，全过返回 0
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

# 口径词：出现任一即视为该行涉及口径问题
# ★1.2.1 补「全线/全段/全长/整体」——1.2.0 的判据只有桥梁口径词
#   （单幅/全桥/两幅…），道路专业的"全线 18.2km"根本不在列表里，
#   导致 B3 全线项永远判红（38 条假红）。判据缺词与漏报同样有害：
#   人一见大面积假红，就会开始无视红灯。
# ★4.0.1 判据与受控词表同源：此前本正则漏掉了 qto_vocab.SPAN_WORDS 里的
#   「总计 / 合计 / 每座 / 单座 / 每跨 / 每延米」等词，而台账 span 受控词表
#   恰恰允许这些词——于是房建项目里 span=总计/合计 的合法行被整批判成
#   「未声明口径」（Port City 实测：口径列走 fallback 的 总计/合计 行曾被整批判为未声明）。凡受控词表允许作范围口径的词，
#   判据必须认；判据另可补充受控词表之外的口径写法（合计口径/全线/…）。
try:
    from qto_vocab import SPAN_WORDS as _SPAN_WORDS
except ImportError:                                     # 独立运行时的兜底
    _SPAN_WORDS = ("全桥", "单幅", "双幅", "两侧", "单侧", "每座", "单座",
                   "总计", "单件", "合计", "每跨", "每延米", "全段", "每根")
SCOPE_SOLO = re.compile(r'单幅|单侧|全桥|两幅|双幅|单件|每座|每根|合计口径|两侧|双侧'
                        r'|全线|全段|全长|整体口径|\bLOT\b'
                        + '|'.join(_SPAN_WORDS))
# 成对要求：(单侧词, 必须同时出现的配对词)
PAIR_RULES = [
    (re.compile(r'单幅'), re.compile(r'全桥|两幅|双幅|两幅合计|双幅合计')),
    (re.compile(r'单侧'), re.compile(r'两侧|双侧|全桥')),
    (re.compile(r'单件'), re.compile(r'全桥|合计|总件|总套')),
]
# 明确排除：这些词出现在"说明"性备注里，不构成口径声明
EXEMPT = re.compile(r'(历史插入|公式缺陷修复|报价权重|不适用|说明：)')


def col_of(ws, header_kw, start=1, maxr=5):
    """在前几行的表头里找列号。"""
    for r in range(1, min(ws.max_row, maxr) + 1):
        for c in range(start, ws.max_column + 1):
            v = str(ws.cell(r, c).value or '')
            if header_kw in v:
                return c
    return None


def open_ws(path, sheet):
    """读工作表，统一兜底。

    实测踩过：某些从 PowerShell / WPS / 导出的 xlsx 会让openpyxl 直接抛
    UnicodeDecodeError 或 KeyError（XML 声明与实际编码不符）。
    这种文件仍可读，故统一在这里兜一次，各门禁不再各自踩一遍。
    """
    try:
        return openpyxl.load_workbook(path, data_only=False)[sheet]
    except Exception:
        with open(path, 'rb') as f:
            raw = f.read()
        if raw[:2] == b'PK':          # 确实是 zip(xlsx)，是 XML 层问题
            import io, zipfile
            zf = zipfile.ZipFile(io.BytesIO(raw))
            names = [n for n in zf.namelist() if n.startswith('xl/worksheets/sheet')]
            if not names:
                raise
            data = {n: zf.read(n).decode('utf-8', errors='replace') for n in names}
            return _ShimSheet(data[sorted(data)[0]])
        raise


class _ShimSheet:
    """最小只读工作表接口：给兜底路径用，只提供 cell() 与 max_row/max_column。"""
    def __init__(self, xml):
        import re as _re
        self._rows = {}
        maxc = 0
        for m in _re.finditer(r'<row[^>]*r="(\d+)"[^>]*>(.*?)</row>', xml, _re.S):
            rn = int(m.group(1)); cells = {}
            for cm in _re.finditer(r'<c([^>]*)r="([A-Z]+)(\d+)"([^>]*)>(.*?)</c>|<c([^>]*)/>', m.group(2), _re.S):
                attrs = cm.group(1) or ''
                col = cm.group(2) or ''
                body = cm.group(5) or ''
                if not col:
                    continue
                ci = 0
                for ch in col:
                    ci = ci * 26 + (ord(ch) - 64)
                maxc = max(maxc, ci)
                v = ''
                if 't="s"' in attrs:
                    v = body
                else:
                    vm = _re.search(r'<v>(.*?)</v>', body, _re.S)
                    v = vm.group(1) if vm else ''
                cells[ci] = v
            self._rows[rn] = cells
        self.max_row = max(self._rows) if self._rows else 0
        self.max_column = maxc

    def cell(self, r, c):
        v = self._rows.get(r, {}).get(c, None)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return v
        return v


def run(path, sheet):
    ws = open_ws(path, sheet)
    c_code = col_of(ws, 'CBS') or col_of(ws, '编码') or 2
    c_name = col_of(ws, '名称') or col_of(ws, '分项') or 3
    c_unit = col_of(ws, '单位') or 4
    c_qty = col_of(ws, '工程量') or col_of(ws, '数量') or 5
    c_scope = col_of(ws, '范围口径') or col_of(ws, '计量口径') or col_of(ws, '口径')
    c_note = col_of(ws, '备注') or col_of(ws, '说明') or 9

    rows = []
    for r in range(1, ws.max_row + 1):
        q = ws.cell(r, c_qty).value
        code = ws.cell(r, c_code).value
        if not isinstance(q, (int, float)):
            continue
        if not code or '合计' in str(ws.cell(r, c_name).value or ''):
            continue
        rows.append({
            'r': r,
            'code': str(code),
            'name': str(ws.cell(r, c_name).value or ''),
            'scope': str(ws.cell(r, c_scope).value or '') if c_scope else '',
            'note': str(ws.cell(r, c_note).value or ''),
        })

    missing, unpaired = [], []
    for d in rows:
        # 有专用范围口径列时，以该结构化字段为主判据，禁止靠备注里的
        # 口径词“补绿”；只有旧模板没有专用列时，才兼容旧版 name+note。
        text = d['scope'] if c_scope else (d['name'] + ' ' + d['note'])
        # 备注中的“说明/不适用”等历史豁免词不能覆盖已存在的结构化口径字段；
        # 结构化字段为空时必须报缺失。只在旧模板回退模式下尊重旧式豁免备注。
        if not c_scope and EXEMPT.search(d['note']):
            continue
        if not SCOPE_SOLO.search(text):
            missing.append('r%d %s' % (d['r'], d['code']))
            continue
        for solo, need in PAIR_RULES:
            if solo.search(text) and not need.search(text):
                unpaired.append('r%d %s（缺%s）' % (d['r'], d['code'], need.pattern))
                break

    return len(rows), missing, unpaired


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='1_成本取量总表')
    a = ap.parse_args()
    try:
        n, missing, unpaired = run(a.xlsx, a.sheet)
    except Exception as e:
        print('读取失败：%s' % e)
        print('状态：unavailable —— 表不存在或工作表名不对')
        return 2

    print('口径声明门禁 | 表：%s' % os.path.basename(a.xlsx))
    print('数量行 %d 条' % n)
    # ---- 零数量行守卫（v2.1.1）----
    # G-A/G-B 都以"数量行"为分母；0 条时 missing 与 unpaired 必然为空，
    # 旧版由此打印「门禁通过」——分母为 0 的通过不算通过。
    if n == 0:
        print('[未执行] 扫描到 **0 条数量行**（工程量列为数值的行）。')
        print('         口径声明是否成对、是否遗漏，一条都判断不了。')
        print('         先核对 --sheet 与列序，别把"没数据"当成"口径都对"。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2
    if missing:
        print('[FAIL] G-A 未声明口径 %d 条：%s' % (len(missing), '；'.join(missing[:5])))
    else:
        print('[PASS] G-A 全部数量行均已声明口径')
    if unpaired:
        print('[FAIL] G-B 口径未成对 %d 条：%s' % (len(unpaired), '；'.join(unpaired[:5])))
    else:
        print('[PASS] G-B 单/全、单侧/两侧均成对')

    if missing or unpaired:
        print('!! 未通过：口径不明会让读者无法判断量级，先补口径声明再交付')
        return 1
    print('== 口径声明门禁通过 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())