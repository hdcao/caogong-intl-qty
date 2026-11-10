#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""input_digest.py —— 输入消化清单（标书/图纸前置消化）

## 为什么需要它

本项目 21 轮迭代反复暴露同一类问题，最深一层的根因是：

    标书与图纸在前期没有被充分消化，问题是在"算"的过程中被偶然发现的。

典型表现（均为本项目实测）：
  · 第 12 轮才发现箱涵信息表 `DISCHARGES(m3/S)` 是流量列不是尺寸列
    → 此前两轮按错误列义算出 5,356 m³（正确 9,191）
  · 第 18 轮才发现照明负荷表每张含 2~3 个配电柜
    → 此前按"一图一柜"漏算 69% 负荷
  · 第 21 轮才发现外部清单整列含 ×1.05 富余系数
    → 此前所有对比都带 5% 系统性假差异

这些都不是"算错了"，而是**算之前没看清要算的是什么**。
它们的共同特征是：**在消化阶段看一眼前置表头/目录/合同条款就能避免**。

## 设计要点

1. **不产出数量**。本工具只登记"我确认了什么、依据在哪"，不算任何量。
2. **每条必须有出处，且出处必须可回查**。`--source` 是必填，且必须含
   卷/册/条款/图号/页码这类可定位信息 —— "x"、"见附件" 不算出处。
3. **必须有人签署**。`--by` 不得是 AI/机器人/助手/自动/空。
4. **必须与模板对账**。`verify` 会把**实际清单**与 `DIGEST_ITEMS` 模板逐条
   核对：`items` 为空、不是列表、或必答项被删减 → 一律阻断，绝不 PASS。
5. **未消化完是硬阻断**。`verify` 非 0 → 只能出 DRAFT。

## 用法

```bash
# 生成清单（自动扫图纸目录，列出待消化项）
python scripts/input_digest.py init --root . --project "SPARK-EPR" \\
    --drawings "D:/drawings" --tender "D:/tender"

# 逐条登记
python scripts/input_digest.py set --file takeoff/input_digest.json \\
    --id D-03 --answer "CESMM4 + 补遗1 §3.4" --source "标书 Vol.2 p.147 §3.4" --by 张三

# 登记发现的问题
python scripts/input_digest.py issue --file takeoff/input_digest.json \\
    --id D-07 --kind OI --text "箱涵 36 孔 vs 22 孔待业主确认"

# 验收（交付前必跑）
python scripts/input_digest.py verify --file takeoff/input_digest.json --strict
```

## 退出码

    0  PASS      全部必答项已登记、出处可回查、已由人签署、无未决阻断项
    1  FAIL      存在未登记/出处不可回查/未签署/必答项缺失/未决阻断项
    2  CANNOT_RUN 清单不存在、损坏、items 为空或结构非法

作者：曹工国际工程算量助手 v4.6.2
"""
import argparse
import datetime
import json
import os
import re
import sys

# 必答项模板：四问拆成可逐条核验的条目
# (id, 分类, 问题, 是否必答, 判定提示)
DIGEST_ITEMS = [
    ('D-01', '范围', '本项目本次算量的分部/标段范围清单（含明确排除项及排除依据）', True,
     '要能逐条列出；排除项也要写，否则会漏'),
    ('D-02', '范围', '计量规则名称 + 版本 + 项目专用修订条款号', True,
     '不许写"国际通用规则/惯例"'),
    ('D-03', '范围', '合同对量差、变更、暂列的特别约定（如有）', True,
     '决定哪些量能进合同净量'),
    ('D-04', '输入', '图纸目录树已列全，缺件已单列（含图号/版本/图种）', True,
     '目录里出现而清单没有的子目录，一律登记待核'),
    ('D-05', '输入', '标书文件已通读，关键约束条款已摘录到页码', True,
     '标书与图纸互指条款须落到一端'),
    ('D-06', '输入', '图纸文字层状态已判型（text/扫描/转曲）', True,
     '决定走哪条取证路线'),
    ('D-07', '输入', '各类信息表（schedule）的表头列义已逐列确认', True,
     '★高发：流量列/尺寸列/示例值混淆'),
    ('D-08', '输入', '外部清单是否含富余/损耗系数，已判定并归一', True,
     '本项目原则：一律按净量出数'),
    ('D-09', '交付', '交付物清单与格式要求', True, ''),
    ('D-10', '交付', '精度要求（小数位/允许偏差/敞口上限）', True, ''),
    ('D-11', '交付', '验收人与放行权限', True, 'AI 不得代签'),
    ('D-12', '交付', '本次明确不审的范围及原因（须在交付物中显式声明）', True,
     '沉默会被读成"都查了"'),
]


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M')


# ---------- 结构性判据 ----------
# 为什么不能只判"非空"：实测 `--by "AI助手"` 能过（旧码只拦字面 'ai'/'tbd'/'none'），
# `--source "x"` 能过，`--answer "x"` 也能过。只判非空等于没有校验。

# 出处必须能定位到实体：卷/册/条款/页码/图号/表号/两位以上编号（图号、条款号）
SOURCE_RE = re.compile(r'(卷|册|Vol\.?|Section|§|p\.|page|图|表|\d{2,})', re.I)

# 签署人不得是 AI / 程序 / 占位
AI_SIGN_RE = re.compile(r'(?i)\b(ai|gpt|bot|auto|tbd|none)\b|机器人|自动|助手')

# 纯占位答案
PLACEHOLDER = {'x', '-', '--', '无', '待定', 'n/a', 'na', 'todo', '', '？', '?'}


def looks_sourced(s, minlen=8):
    """出处是否**可回查**：长度足够 + 含卷/册/条款/页码/图号等定位信息"""
    s = (s or '').strip()
    if len(s) < minlen:
        return False
    return bool(SOURCE_RE.search(s))


def looks_signed(by):
    """签署人是否为人：非空、无占位符、不含 AI/机器人/自动 语义"""
    by = (by or '').strip()
    if not by or '＿' in by:
        return False
    if AI_SIGN_RE.search(by):
        return False
    return True


def looks_answered(ans):
    """答案是否有实质内容：至少 2 字符且不是纯占位"""
    ans = (ans or '').strip()
    if len(ans) < 2:
        return False
    return ans.lower() not in PLACEHOLDER


def template_required_ids():
    """模板必答项 id 集合。DIGEST_ITEMS 元素 = (id, category, question, required, hint)"""
    return {x[0] for x in DIGEST_ITEMS if x[3]}


def rec_items(rec):
    """取出清单条目；结构非法返回 None（由调用方判 CANNOT_RUN）"""
    items = rec.get('items')
    if not isinstance(items, list) or not items:
        return None
    return items


def load(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding='utf-8'))
    except Exception:
        return None


def save(path, d):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(d, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def cmd_init(a):
    root = a.root or '.'
    path = a.file or os.path.join(root, 'takeoff', 'input_digest.json')

    # 扫图纸目录（只列文件名，不猜语义）
    scan = []
    if a.drawings and os.path.isdir(a.drawings):
        for dirpath, dirnames, filenames in os.walk(a.drawings):
            depth = dirpath[len(a.drawings):].count(os.sep)
            if depth > 3:
                continue
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1].lower()
                if ext in ('.pdf', '.dwg', '.dxf', '.xlsx', '.xls', '.csv'):
                    rel = os.path.relpath(os.path.join(dirpath, fn), a.drawings)
                    scan.append(rel)

    tender = []
    if a.tender and os.path.isdir(a.tender):
        for dirpath, _, filenames in os.walk(a.tender):
            for fn in sorted(filenames):
                if os.path.splitext(fn)[1].lower() in ('.pdf', '.docx', '.doc', '.xlsx', '.xls'):
                    rel = os.path.relpath(os.path.join(dirpath, fn), a.tender)
                    tender.append(rel)
    elif a.tender and os.path.isfile(a.tender):
        tender = [os.path.basename(a.tender)]

    rec = dict(
        _README=[
            '输入消化清单：确认"算哪些/按什么算/拿什么算/算到什么程度"。',
            '本清单不产出任何数量。',
            '每条必须有 answer + source（出处）+ by（签署人）。',
            '无出处的 digest_answer 视为未消化。',
            'verify 非 0 → 只能出 DRAFT，不得交付。',
        ],
        project=a.project or '<项目名>',
        generated_at=now(),
        drawings_root=a.drawings or '',
        tender_root=a.tender or '',
        items=[
            dict(id=i, category=cat, question=q, required=req, hint=hint,
                 answer='', source='', by='', at=None, status='TODO')
            for i, cat, q, req, hint in DIGEST_ITEMS
        ],
        issues=[],
        scanned_drawings=dict(count=len(scan), files=scan[:500]),
        scanned_tender=dict(count=len(tender), files=tender[:200]),
    )
    save(path, rec)
    print('[OK] 消化清单已生成：%s' % path)
    print('     必答项 %d 条；扫描到图纸 %d 个、标书文件 %d 个'
          % (len(rec['items']), len(scan), len(tender)))
    print()
    print('   %-6s %-6s %s' % ('ID', '分类', '问题'))
    for it in rec['items']:
        print('   %-6s %-6s %s' % (it['id'], it['category'], it['question'][:52]))
    print()
    print('★ 下一步：逐条 set 登记答案与出处，再跑 verify。')
    return 0


def cmd_set(a):
    rec = load(a.file)
    if rec is None:
        print('[CANNOT_RUN] 清单不存在或损坏：%s' % a.file)
        return 2
    items = rec_items(rec)
    if items is None:
        print('[CANNOT_RUN] 清单 items 为空或结构非法，无法登记。'
              '请重新跑 init 生成完整清单。')
        return 2
    hit = next((x for x in items if x.get('id') == a.id), None)
    if hit is None:
        print('[FAIL] 清单中没有条目 %r' % a.id)
        return 1
    # 出处：不能只判非空，必须可回查（卷/册/条款/页码/图号）
    if not looks_sourced(a.source):
        print('[FAIL] --source 出处不可回查（须含图号/册号/条款号/页码，'
              '且不少于 %d 字符）：%r' % (8, a.source))
        print('       无出处的消化等同于没消化；"x"/"见附件" 不算出处。')
        return 1
    # 签署：不能只判非空，必须是自然人
    if not looks_signed(a.by):
        print('[FAIL] --by 必须是人名，AI 不得代签（当前=%r）。' % (a.by,))
        return 1
    if not looks_answered(a.answer):
        print('[FAIL] --answer 无实质内容（不得为 x/-/无/待定 等占位）：%r'
              % (a.answer,))
        return 1
    hit['answer'] = a.answer
    hit['source'] = a.source
    hit['by'] = str(a.by).strip()
    hit['at'] = now()
    hit['status'] = 'DONE'
    save(a.file, rec)
    print('[OK] %s 已登记（出处：%s；签署：%s）' % (a.id, a.source, a.by))
    return 0


def cmd_issue(a):
    rec = load(a.file)
    if rec is None:
        print('[CANNOT_RUN] 清单不存在或损坏：%s' % a.file)
        return 2
    if not isinstance(rec.get('issues'), list):
        rec['issues'] = []
    kind = (a.kind or 'OI').upper()
    if kind not in ('OI', 'RFI', 'STOP'):
        print('[FAIL] --kind 只能是 OI / RFI / STOP')
        return 1
    rec['issues'].append(dict(id=a.issue_id or ('I-%02d' % (len(rec['issues']) + 1)),
                              item=a.id, kind=kind, text=a.text,
                              status='OPEN', at=now()))
    save(a.file, rec)
    print('[OK] 已登记 %s 问题：%s → %s' % (kind, a.id, a.text))
    return 0


def cmd_verify(a):
    rec = load(a.file)
    if rec is None:
        print('[CANNOT_RUN] 清单不存在或损坏：%s' % a.file)
        print('            **没有消化清单 = 没做过输入消化，不等于通过。**')
        return 2

    # ★ 结构性前置校验：items 缺失 / 不是列表 / 为空 → CANNOT_RUN(2)
    #   历史缺陷：{"items":[]} 不产生任何 fail → 输出"必答项 0 条" + [PASS]。
    #   「零条必答项」恰恰说明清单是空的，必须阻断而不是放行。
    items = rec_items(rec)
    if items is None:
        raw = rec.get('items')
        why = ('缺 items 键' if 'items' not in rec
               else 'items 不是列表（%s）' % type(raw).__name__
               if not isinstance(raw, list) else 'items 为空列表')
        print('[CANNOT_RUN] 消化清单结构非法：%s' % why)
        print('            **空清单 = 没做过输入消化，不等于通过。**')
        print('            请重新跑 init 生成完整清单后逐条登记。')
        return 2

    fails = []
    warns = []
    n_done = 0
    print('== 输入消化验收 ==')
    print('   项目：%s   生成于 %s' % (rec.get('project'), rec.get('generated_at')))
    print()

    # ★ 与 DIGEST_ITEMS 模板对账：必答项一条都不能少
    #   历史缺陷：verify 只看清单里有什么，删掉 11 条必答项只留 1 条也能 PASS。
    tpl_required = template_required_ids()
    act_required = {it.get('id') for it in items if it.get('required')}
    missing = sorted(tpl_required - act_required)
    if missing:
        fails.append('必答项缺失：%s —— 清单与模板 %d 条必答项不符，'
                     '不得以残缺清单通过' % (', '.join(missing), len(tpl_required)))

    for it in items:
        st = it.get('status')
        ans = it.get('answer')
        src = it.get('source')
        by = it.get('by')
        # 结构性判据（不是"非空"）
        bad = []
        if st != 'DONE':
            bad.append('未登记')
        if not looks_answered(ans):
            bad.append('答案无实质内容')
        if not looks_sourced(src):
            bad.append('出处不可回查（须含图号/册号/条款/页码）')
        if not looks_signed(by):
            bad.append('未由人签署（AI/占位不算）')

        if not bad:
            n_done += 1
            continue
        if it.get('required'):
            fails.append('%s [%s] %s —— %s'
                         % (it.get('id'), it.get('category'),
                            str(it.get('question'))[:40], '/'.join(bad)))
        else:
            warns.append('%s 非必答项未完成：%s'
                         % (it.get('id'), '/'.join(bad)))

    opened = [x for x in rec.get('issues', []) if x.get('status') == 'OPEN']
    blocking = [x for x in opened if x.get('kind') in ('RFI', 'STOP')]

    n_req = len(act_required)
    print('   必答项 %d 条（模板 %d 条），已完成 %d 条'
          % (n_req, len(tpl_required), n_done))
    print('   未决问题 %d 条（其中阻断级 %d 条）' % (len(opened), len(blocking)))
    print()

    if opened:
        for x in opened:
            tag = '阻断' if x.get('kind') in ('RFI', 'STOP') else '挂账'
            print('   [%s] %s %s: %s' % (tag, x.get('kind'), x.get('item'),
                                         str(x.get('text'))[:50]))
        print()

    for w in warns:
        print('[WARN] %s' % w)

    if fails:
        for f in fails:
            print('[FAIL] %s' % f)
        print()
        print('**输入未消化完。** 只能做探索性试算，成果须标 DRAFT，')
        print('   且交付物首行须写明"本次为探索性试算，未完成输入消化"。')
        return 1

    if a.strict and blocking:
        for x in blocking:
            print('[FAIL] strict：未决阻断项 %s（%s）' % (x.get('item'), x.get('kind')))
        return 1

    if blocking:
        print('[WARN] 存在 %d 条未决阻断项，非 strict 模式放行；'
              '但交付物必须列出这些敞口。' % len(blocking))

    print('[PASS] 输入消化完成：全部必答项已登记、出处可回查、已由人签署')
    if opened:
        print('       注意：仍有 %d 条未决问题，交付时须逐条列为敞口。' % len(opened))
    return 0


def main():
    ap = argparse.ArgumentParser(description='输入消化清单（标书/图纸前置消化）',
                                 allow_abbrev=False)
    sub = ap.add_subparsers(dest='cmd')

    p1 = sub.add_parser('init', help='生成消化清单')
    p1.add_argument('--root', default='.')
    p1.add_argument('--project')
    p1.add_argument('--drawings')
    p1.add_argument('--tender')
    p1.add_argument('--file')
    p1.set_defaults(func=cmd_init)

    p2 = sub.add_parser('set', help='登记一条消化结论')
    p2.add_argument('--file', required=True)
    p2.add_argument('--id', required=True)
    p2.add_argument('--answer', required=True)
    p2.add_argument('--source', required=True)
    p2.add_argument('--by', required=True)
    p2.set_defaults(func=cmd_set)

    p3 = sub.add_parser('issue', help='登记消化阶段发现的问题')
    p3.add_argument('--file', required=True)
    p3.add_argument('--id', required=True)
    p3.add_argument('--kind', default='OI')
    p3.add_argument('--text', required=True)
    p3.add_argument('--issue-id', dest='issue_id')
    p3.set_defaults(func=cmd_issue)

    p4 = sub.add_parser('verify', help='验收消化完成度')
    p4.add_argument('--file', required=True)
    p4.add_argument('--strict', action='store_true')
    p4.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    if not getattr(a, 'func', None):
        ap.print_help()
        return 2
    try:
        return a.func(a)
    except Exception as e:
        print('[CANNOT_RUN] %s' % e)
        return 2


if __name__ == '__main__':
    sys.exit(main())
