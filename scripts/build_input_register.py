# -*- coding: utf-8 -*-
"""
建册脚本（第 0 步 · 1.2.0 新增，★强制）

把"我在算什么"锁成一张可复核的表：图纸台账 + 算量范围声明 + 待确认清单。

两个子命令
----------
  scan   扫一个图纸目录，机械地提取图号/文件名/大小/页数/SHA-256，生成 seed csv
         （图名、工作包、部位**故意不猜**——猜出来的台账比没有台账更危险）
  build  读 seed csv（补齐后）→ 输出固定格式 input_register.xlsx + input_register.json，
         自动列出脚本能证明的待确认项，并给出 CONFIRMED / DRAFT 判定

★为什么改强制（与外部同类实现的差别）
  外面的做法是"发现缺口只报告、永远 exit 0"。结果：表建了，没人看，缺口照旧。
  本脚本：存在 open/pending 项 → 退出码 1 → 算量成果只能标 DRAFT，不得作为交付。
  要强行推进必须显式 --allow-draft，并在 Cover 页盖 DRAFT 章、在 Open Items 记一条。
  **降级要留痕，不能靠自觉。**

用法
----
  python build_input_register.py scan --dir <图纸目录> --out takeoff/register/
  python build_input_register.py build --register takeoff/register/drawings_seed.csv \
          --project "SPARK EPR-1" --out takeoff/register/ \
          [--scope takeoff/register/scope.csv] [--allow-draft]
退出码：0 CONFIRMED ／ 1 DRAFT（有未闭合项）／ 2 未执行
"""
import argparse
import csv
import datetime as dt
import hashlib
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

COLS = ['sheet', 'title', 'discipline', 'work_package', 'part', 'rev', 'date',
        'scale', 'source_type', 'file', 'sha256', 'scope_status',
        'superseded_by', 'blocks', 'confirmed_by', 'status', 'notes']
COLS_CN = ['图号', '图名', '专业', '工作包', '部位/分段', '版本', '图纸日期', '比例',
           '源类型', '文件', 'SHA-256', '算量范围', '被替代为', '关联构件',
           '确认人', '状态', '备注']
HEAD_EN = ['No.', 'Sheet', 'Title', 'Discipline', 'Work Package', 'Part', 'Rev',
           'Date', 'Scale', 'Source Type', 'File', 'SHA-256', 'Scope Status',
           'Superseded By', 'Blocks', 'Confirmed By', 'Status', 'Notes']

STATUS_OK = ('confirmed', 'received', 'pending', 'missing', 'superseded')
SCOPE_OK = ('full', 'partial', 'excluded')
SRC_OK = ('vector', 'raster', 'pdf')

SHEET_RE = re.compile(r'([A-Z]{1,4}-?\d{4,8}(?:\.\d{1,3})?)', re.I)

SCOPE_KEYS = [
    ('计量标准与合同类型', '如：Lump Sum 总价合同，按 CPB 权重里程碑付款；不按 CESMM/POMI/SMM7 计量'),
    ('里程基准与起终点', '如：STA 0+000 ~ 18+200，基准 = 设计中心线'),
    ('单位制与交付单位', '如：SI；体积 CM(m³)、钢筋 MT、长度 LM、面积 SM'),
    ('编码体系', '如：业主 CPB（Schedule C Attachment）为汇总层，我方 CBS 为拆分层'),
    ('图纸版本裁决规则', '如：澄清函/Addendum 附件优先于主图包；同图号取最高版本'),
    ('推定层是否计入合计', '如：推定层单列，可整层剔除，不进报价合计'),
    ('工作包字典', 'WP-01 道路 / WP-02 排水 / WP-03 桥梁 / WP-04 交安 / WP-05 路灯 / WP-06 立交'),
    ('部位字典', '桥1 / 桥2 / IC-102 / CH6+000~CH8+000 / 全项目'),
]


def sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def infer_source(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.dwg', '.dxf'):
        return 'vector'
    if ext in ('.pdf',):
        return 'pdf'
    if ext in ('.tif', '.tiff', '.png', '.jpg', '.jpeg'):
        return 'raster'
    return 'pdf'


def pdf_pages(path):
    """尽量数页数，失败返回空串（不阻断）。"""
    try:
        import pypdf
        return str(len(pypdf.PdfReader(path).pages))
    except Exception:
        pass
    try:
        raw = open(path, 'rb').read()
        return str(len(re.findall(rb'/Type\s*/Page[^s]', raw)))
    except Exception:
        return ''


# ── scan ────────────────────────────────────────────────────
def do_scan(a):
    if not os.path.isdir(a.dir):
        print('目录不存在：%s' % a.dir)
        return 2
    rows, no_sheet = [], []
    for fn in sorted(os.listdir(a.dir)):
        p = os.path.join(a.dir, fn)
        if not os.path.isfile(p):
            continue
        if os.path.splitext(fn)[1].lower() not in ('.pdf', '.dwg', '.dxf', '.tif',
                                                   '.tiff', '.png', '.jpg', '.jpeg'):
            continue
        m = SHEET_RE.search(fn)
        sheet = m.group(1).upper() if m else ''
        rec = {
            'sheet': sheet,
            'title': '',                       # ★不猜
            'discipline': '',
            'work_package': '',
            'part': '',
            'rev': '',
            'date': '',
            'scale': '',
            'source_type': infer_source(p),
            'file': os.path.abspath(p),
            'sha256': sha256_of(p),
            'scope_status': 'full',
            'superseded_by': '',
            'blocks': '',
            'confirmed_by': '',
            'status': 'pending',
            'notes': ('页数 %s' % pdf_pages(p)) if p.lower().endswith('.pdf') else '',
        }
        if not sheet:
            no_sheet.append(fn)
            rec['notes'] = (rec['notes'] + '；文件名未识别出图号').strip('；')
        rows.append(rec)

    os.makedirs(a.out, exist_ok=True)
    out_csv = os.path.join(a.out, 'drawings_seed.csv')
    with io.open(out_csv, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print('扫描完成：%d 个图纸文件 → %s' % (len(rows), out_csv))
    if no_sheet:
        print('[WARN] %d 个文件名未识别出图号（需人工补）：%s'
              % (len(no_sheet), '；'.join(no_sheet[:5])))
    print('★下一步（必须人做，脚本不代劳）：')
    print('  ① 补「图名 / 专业 / 工作包 / 部位」——只有图号等于没说在算什么')
    print('  ② 对照《LIST OF DRAWINGS》核对图号（对不上的以图面标题栏为准）')
    print('  ③ 标 superseded_by：澄清函/Addendum 有新版的，旧版必须写被谁替代')
    print('  ④ 缺图的补一行 status=missing 并写 blocks（缺这张图影响哪些构件）')
    print('  ⑤ 补齐后跑 build；状态仍是 DRAFT 时不得作为交付')
    return 0


# ── build ───────────────────────────────────────────────────
def read_csv(p):
    with io.open(p, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def norm_enum(v, ok, default):
    v = str(v or '').strip().lower()
    return v if v in ok else default


def do_build(a):
    if not os.path.isfile(a.register):
        print('台账不存在：%s｜建册未执行（exit 2）' % a.register)
        return 2
    rows = read_csv(a.register)
    if not rows:
        print('台账为空：%s｜建册未执行（exit 2）' % a.register)
        return 2

    open_items = []
    sheets = {}

    for i, r in enumerate(rows):
        ln = i + 2
        for k in COLS:
            r.setdefault(k, '')
        r['sheet'] = str(r['sheet']).strip()
        r['status'] = norm_enum(r['status'], STATUS_OK, 'pending')
        r['scope_status'] = norm_enum(r['scope_status'], SCOPE_OK, 'full')
        r['source_type'] = norm_enum(r['source_type'], SRC_OK, infer_source(r['file'] or ''))
        st = r['status']

        if not r['sheet']:
            open_items.append(('台账第 %d 行没有图号' % ln, 'high',
                               '没有图号的记录无法被算量表引用'))
            continue
        if r['sheet'] in sheets:
            # 同图号重复：允许（不同版本），但必须互标 superseded
            pass
        sheets.setdefault(r['sheet'], []).append(r)

        # 补算哈希
        fp = r['file']
        if fp and os.path.isfile(fp) and not r['sha256']:
            try:
                r['sha256'] = sha256_of(fp)
            except Exception as e:
                open_items.append(('%s 哈希计算失败：%s' % (r['sheet'], e), 'low', ''))

        if st in ('confirmed', 'received'):
            if not r['file']:
                open_items.append(('%s 声明 %s 但没写文件路径' % (r['sheet'], st), 'high',
                                   '无法证明算的是哪一版图'))
            elif not os.path.isfile(r['file']):
                open_items.append(('%s 磁盘上找不到文件：%s' % (r['sheet'], r['file']), 'high',
                                   '图不在手上的行不能算 confirmed'))
        if st == 'missing' and not r['blocks']:
            open_items.append(('%s 缺图但没写"关联构件"' % r['sheet'], 'high',
                               '缺这张图影响哪些构件没人说得清 → 事后必然争议'))
        if st == 'superseded' and not r['superseded_by']:
            open_items.append(('%s 标了被替代但没写"被替代为"' % r['sheet'], 'high',
                               '不知道用哪一版算'))
        if st == 'confirmed' and not r['confirmed_by']:
            open_items.append(('%s 状态 confirmed 但没有确认人' % r['sheet'], 'high',
                               'R-G-15：AI 不代签，确认人必须是人'))
        if r['scope_status'] == 'full' and not r['work_package']:
            open_items.append(('%s 算量范围 full 但没写工作包' % r['sheet'], 'medium',
                               '不知道这张图喂给清单的哪一部分'))
        if not r['part']:
            open_items.append(('%s 没写部位/分段' % r['sheet'], 'low',
                               '无法按部位切片，模型与算量表对不上时查不出是哪一段'))

    # 跨行检查：被替代的图号必须存在
    for sheet, rs in sheets.items():
        for r in rs:
            tgt = str(r.get('superseded_by') or '').strip()
            if tgt and tgt not in sheets:
                open_items.append(('%s 的"被替代为"指向 %s，台账里没有这张' % (sheet, tgt),
                                   'high', '替代链断了，等于不知道最终用哪版'))
        if len(rs) > 1:
            revs = set(str(x.get('rev') or '') for x in rs)
            if len(revs) > 1 and not any(str(x.get('superseded_by') or '').strip() for x in rs):
                open_items.append(('%s 台帐里有 %d 个版本（%s）但没有互标"被替代为"'
                                   % (sheet, len(rs), '、'.join(sorted(revs))), 'high',
                                   '两版并存且未裁决，算量基准不确定'))

    # 范围声明
    scope_rows = []
    if a.scope and os.path.isfile(a.scope):
        scope_rows = read_csv(a.scope)
    if not scope_rows:
        open_items.append(('算量范围声明（Scope）未填写', 'high',
                           '里程基准/单位制/版本裁决规则没定，后面每个数都可能各按各的口径'))
        scope_rows = [{'field': k, 'value': ''} for k, _ in SCOPE_KEYS]

    hard = [x for x in open_items if x[1] == 'high']
    status = 'CONFIRMED' if not open_items else 'DRAFT'
    if a.allow_draft and status == 'DRAFT':
        open_items.append(('以 --allow-draft 强行推进（建册未闭合）', 'high',
                           '交付物必须标 DRAFT，并在交付说明里写明哪些项未闭合'))

    # ── 写 xlsx ──
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    H = Font(bold=True, size=10)
    HF = PatternFill('solid', fgColor='DCE6F1')
    T = Font(bold=True, size=13)
    WRAP = Alignment(wrap_text=True, vertical='top')

    # Cover
    ws = wb.active
    ws.title = 'Cover'
    ws['A1'] = '建册 / INPUT REGISTER'
    ws['A1'].font = T
    meta = [
        ('项目 Project', a.project or ''),
        ('建册状态 Register Status', status),
        ('图册版本 Drawing Set Rev', a.rev_addendum or ''),
        ('生成时间 Generated', dt.datetime.now().strftime('%Y-%m-%d %H:%M')),
        ('图纸条目 Drawings', str(len(rows))),
        ('confirmed / received', '%d / %d' % (
            sum(1 for r in rows if r['status'] == 'confirmed'),
            sum(1 for r in rows if r['status'] == 'received'))),
        ('missing / superseded', '%d / %d' % (
            sum(1 for r in rows if r['status'] == 'missing'),
            sum(1 for r in rows if r['status'] == 'superseded'))),
        ('待确认项 Open Items', str(len(open_items))),
        ('其中 high', str(len(hard))),
    ]
    for i, (k, v) in enumerate(meta, start=3):
        ws.cell(i, 1, k).font = H
        ws.cell(i, 2, v)
    ws.cell(len(meta) + 5, 1, '★签发：AI 出表，不代签（R-G-15）').font = Font(bold=True, color='C00000')
    ws.cell(len(meta) + 6, 1, '编制 ______________    复核 ______________    确认 ______________')
    if status != 'CONFIRMED':
        ws.cell(len(meta) + 8, 1, '★本册为 DRAFT：存在未闭合项，算量成果不得作为交付').font = \
            Font(bold=True, size=12, color='C00000')
    ws.column_dimensions['A'].width = 30
    ws.column_dimensions['B'].width = 46

    # Drawings
    wd = wb.create_sheet('Drawings')
    wd.cell(1, 1, '图纸台账 / DRAWING REGISTER').font = T
    wd.cell(2, 1, '★图号以磁盘实有文件与图面标题栏为准；status=pending 的行不得被算量表引用').font = \
        Font(italic=True, size=9, color='808080')
    for j, h in enumerate(HEAD_EN, start=1):
        c = wd.cell(4, j, h)
        c.font = H
        c.fill = HF
        c.alignment = WRAP
    for j, h in enumerate(COLS_CN, start=1):
        c = wd.cell(5, j, h)
        c.font = H
        c.fill = HF
        c.alignment = WRAP
    for i, r in enumerate(rows, start=6):
        wd.cell(i, 1, i - 5)
        for j, k in enumerate(COLS, start=2):
            wd.cell(i, j, r.get(k, ''))
    wd.freeze_panes = 'C6'
    wd.auto_filter.ref = 'A5:%s%d' % (get_column_letter(len(COLS) + 1), 5 + len(rows))
    widths = [5, 16, 40, 10, 12, 14, 6, 11, 8, 9, 44, 20, 10, 14, 26, 10, 11, 30]
    for j, w in enumerate(widths, start=1):
        wd.column_dimensions[get_column_letter(j)].width = w

    # Scope
    wsp = wb.create_sheet('Scope')
    wsp.cell(1, 1, '算量范围声明 / SCOPE STATEMENT').font = T
    for j, h in enumerate(['字段 Field', '内容 Content'], start=1):
        c = wsp.cell(3, j, h)
        c.font = H
        c.fill = HF
    given = {str(x.get('field') or '').strip(): str(x.get('value') or '') for x in scope_rows}
    for i, (k, default) in enumerate(SCOPE_KEYS, start=4):
        wsp.cell(i, 1, k)
        wsp.cell(i, 2, given.get(k) or '')
        if not given.get(k):
            wsp.cell(i, 3, '（建议：%s）' % default).font = Font(size=9, color='808080')
        wsp.cell(i, 2).alignment = WRAP
    wsp.column_dimensions['A'].width = 26
    wsp.column_dimensions['B'].width = 62
    wsp.column_dimensions['C'].width = 46

    # Open Items
    wo = wb.create_sheet('Open Items')
    wo.cell(1, 1, '待确认清单 / OPEN ITEMS').font = T
    wo.cell(2, 1, '★high 项未闭合时，本册为 DRAFT（脚本退出码 1）；'
                  '每条都要有去向：补资料 / 书面假设 / 提澄清').font = \
        Font(italic=True, size=9, color='808080')
    for j, h in enumerate(['No.', '待确认事项', '等级', '为什么必须确认', '处置', '责任人', '关闭日期'], start=1):
        c = wo.cell(4, j, h)
        c.font = H
        c.fill = HF
    for i, (t, sev, why) in enumerate(open_items, start=5):
        wo.cell(i, 1, i - 4)
        wo.cell(i, 2, t)
        wo.cell(i, 3, sev)
        wo.cell(i, 4, why)
        wo.cell(i, 5, '')          # 处置：补资料/书面假设/提澄清
        wo.cell(i, 6, '')
        wo.cell(i, 7, '')
    for j, w in enumerate([5, 62, 8, 52, 14, 10, 12], start=1):
        wo.column_dimensions[get_column_letter(j)].width = w

    os.makedirs(a.out, exist_ok=True)
    xlsx = os.path.join(a.out, 'input_register.xlsx')
    wb.save(xlsx)

    payload = {
        'register_status': status,
        'project': a.project,
        'drawing_set_rev': a.rev_addendum,
        'generated_at': dt.datetime.now().isoformat(timespec='seconds'),
        'drawings': rows,
        'scope': [{'field': k, 'value': given.get(k, '')} for k, _ in SCOPE_KEYS],
        'open_items': [{'item': t, 'severity': s, 'why': w} for t, s, w in open_items],
    }
    with io.open(os.path.join(a.out, 'input_register.json'), 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print('建册 | %s | 图纸 %d 条 | 待确认 %d 项（high %d）'
          % (status, len(rows), len(open_items), len(hard)))
    print('  产出：%s' % xlsx)
    print('  产出：%s' % os.path.join(a.out, 'input_register.json'))
    for t, sev, _w in open_items[:8]:
        print('  [%s] %s' % (sev.upper(), t))
    if len(open_items) > 8:
        print('  …另有 %d 项，见 Open Items 页' % (len(open_items) - 8))

    if status == 'CONFIRMED':
        print('== 建册已确认：可进入算量 ==')
        return 0
    if a.allow_draft:
        print('!! 建册为 DRAFT（已 --allow-draft）：算量成果**必须标 DRAFT**，'
              '并在交付说明里写明未闭合项')
        return 1
    print('!! 建册未闭合：**算量成果只能标 DRAFT，不得作为交付**。'
          '补齐台账后重跑，或 --allow-draft 显式降级（会留痕）。')
    return 1


def main():
    ap = argparse.ArgumentParser(description='建册：图纸台账 + 范围声明 + 待确认清单')
    sub = ap.add_subparsers(dest='cmd')

    s1 = sub.add_parser('scan', help='扫描图纸目录生成 seed csv')
    s1.add_argument('--dir', required=True)
    s1.add_argument('--out', required=True)

    s2 = sub.add_parser('build', help='生成 input_register.xlsx + json 并判定')
    s2.add_argument('--register', required=True)
    s2.add_argument('--project', default='')
    s2.add_argument('--out', required=True)
    s2.add_argument('--scope', default=None, help='范围声明 csv（field,value）')
    s2.add_argument('--rev-addendum', default='', help='图册版本，如 "Addendum-2（2026-10-15）"')
    s2.add_argument('--allow-draft', action='store_true',
                    help='建册未闭合时强行推进（会在 Cover 盖章、Open Items 留痕）')

    a = ap.parse_args()
    if a.cmd == 'scan':
        return do_scan(a)
    if a.cmd == 'build':
        return do_build(a)
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
