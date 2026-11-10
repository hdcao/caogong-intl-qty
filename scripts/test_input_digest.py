#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_input_digest.py —— 输入消化闸门自证

测的不是"能不能跑"，而是"能不能拦住带着未决问题开工"。
每个用例都是本项目真实发生过的场景。

    python scripts/test_input_digest.py
    → 0 全部符合预期 / 1 有不符合 / 2 自身无法运行
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, 'input_digest.py')
PY = sys.executable

ITEMS = ['D-%02d' % i for i in range(1, 13)]

# 合格出处的样板：含卷号 + 页码 + 条款号，满足「可回查」判据
SRC_OK = '标书 Vol.2 p.147 §3.4'
ANSWER_OK = '已确认（详见附录）'


def run(args):
    p = subprocess.run([PY, TOOL] + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def fresh(tmp):
    f = os.path.join(tmp, 'takeoff', 'input_digest.json')
    run(['init', '--root', tmp, '--project', 'TEST'])
    return f


def fill_all(f, src=SRC_OK, by='张三', ans=ANSWER_OK, ids=None):
    for i in (ids or ITEMS):
        run(['set', '--file', f, '--id', i, '--answer', ans,
             '--source', src, '--by', by])


CASES = []


def case(name, fn, expect_rc, expect_in=''):
    CASES.append((name, fn, expect_rc, expect_in))


# C1 全新清单未登记 → 必须 FAIL
def c1(tmp):
    f = fresh(tmp)
    return run(['verify', '--file', f])


case('C1 未做消化直接出量 → 必须FAIL', c1, 1, '输入未消化完')


# C2 全部登记 → 必须 PASS
def c2(tmp):
    f = fresh(tmp)
    fill_all(f)
    return run(['verify', '--file', f])


case('C2 12项全登记且带出处 → 必须PASS', c2, 0, '[PASS]')


# C3 缺出处 → set 阶段就拒绝
def c3(tmp):
    f = fresh(tmp)
    return run(['set', '--file', f, '--id', 'D-01', '--answer', ANSWER_OK,
                '--source', '', '--by', '张三'])


case('C3 缺出处 → 必须拒绝写入', c3, 1, '无出处的消化等同于没消化')


# C4 AI 代签 → 必须拒绝（出处合法，确保是 by 判据拦住）
def c4(tmp):
    f = fresh(tmp)
    return run(['set', '--file', f, '--id', 'D-01', '--answer', ANSWER_OK,
                '--source', SRC_OK, '--by', 'AI'])


case('C4 AI代签 → 必须拒绝', c4, 1, 'AI 不得代签')


# C5 有未决 RFI 且 strict → 必须 FAIL
def c5(tmp):
    f = fresh(tmp)
    fill_all(f)
    run(['issue', '--file', f, '--id', 'D-07', '--kind', 'RFI',
         '--text', '箱涵孔数待确认'])
    return run(['verify', '--file', f, '--strict'])


case('C5 未决RFI+strict → 必须FAIL', c5, 1, '未决阻断项')


# C6 无清单文件 → 必须 CANNOT_RUN(2)
def c6(tmp):
    return run(['verify', '--file', os.path.join(tmp, 'nonexist.json')])


case('C6 无消化清单 → 必须CANNOT_RUN', c6, 2, 'CANNOT_RUN')


# ============ 以下为本轮整改新增（D4 模板对账 / D5 结构性判据） ============

def _write_rec(f, rec):
    """直接落盘一个清单文件（用来构造被篡改/残缺的清单）"""
    os.makedirs(os.path.dirname(os.path.abspath(f)), exist_ok=True)
    json.dump(rec, open(f, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def _load(f):
    return json.load(open(f, encoding='utf-8'))


# C7 【D4】items 为空列表 → 必须 CANNOT_RUN(2)，不得 PASS
def c7(tmp):
    f = fresh(tmp)
    rec = _load(f)
    rec['items'] = []
    _write_rec(f, rec)
    return run(['verify', '--file', f])


case('C7 items为空列表 → 必须CANNOT_RUN（曾误判PASS）', c7, 2, 'CANNOT_RUN')


# C8 【D4】删掉 11 条必答项只留 1 条 → 必须 FAIL 并点名缺失项
def c8(tmp):
    f = fresh(tmp)
    rec = _load(f)
    rec['items'] = [x for x in rec['items'] if x['id'] == 'D-01']
    for x in rec['items']:
        x.update(answer=ANSWER_OK, source=SRC_OK, by='张三',
                 at='2026-11-04 10:00', status='DONE')
    _write_rec(f, rec)
    return run(['verify', '--file', f])


case('C8 只留1条必答项 → 必须FAIL点名缺失', c8, 1, '必答项缺失')


# C8b 缺失项须被逐一点名（断言报告里出现 D-05 与 D-12）
def c8b(tmp):
    f = fresh(tmp)
    rec = _load(f)
    rec['items'] = [x for x in rec['items'] if x['id'] == 'D-01']
    for x in rec['items']:
        x.update(answer=ANSWER_OK, source=SRC_OK, by='张三',
                 at='2026-11-04 10:00', status='DONE')
    _write_rec(f, rec)
    rc, out = run(['verify', '--file', f])
    want = ['D-05', 'D-12']
    return rc, out + ('' if all(w in out for w in want) else ' || 缺失项未点名')


case('C8b 缺失项须逐一点名(D-05/D-12)', c8b, 1, 'D-05, ')


# C9 【D5】by="AI助手" → 必须 FAIL（旧码只拦字面 'ai'）
def c9(tmp):
    f = fresh(tmp)
    return run(['set', '--file', f, '--id', 'D-01', '--answer', ANSWER_OK,
                '--source', SRC_OK, '--by', 'AI助手'])


case('C9 by="AI助手" → 必须FAIL（曾通过）', c9, 1, 'AI 不得代签')


# C10 【D5】source="x" → 必须 FAIL（旧码只判非空）
def c10(tmp):
    f = fresh(tmp)
    return run(['set', '--file', f, '--id', 'D-01', '--answer', ANSWER_OK,
                '--source', 'x', '--by', '张三'])


case('C10 source="x" → 必须FAIL（曾通过）', c10, 1, '出处不可回查')


# C10b 已写入清单的坏出处，verify 也必须拦住（不能只在 set 拦）
def c10b(tmp):
    f = fresh(tmp)
    rec = _load(f)
    for x in rec['items']:
        x.update(answer=ANSWER_OK, source='x', by='张三',
                 at='2026-11-04 10:00', status='DONE')
    _write_rec(f, rec)
    return run(['verify', '--file', f])


case('C10b 清单内坏出处 → verify必须FAIL', c10b, 1, '出处不可回查')


# C10c answer 为纯占位 "x" → 必须 FAIL
def c10c(tmp):
    f = fresh(tmp)
    return run(['set', '--file', f, '--id', 'D-01', '--answer', 'x',
                '--source', SRC_OK, '--by', '张三'])


case('C10c answer="x"占位 → 必须FAIL', c10c, 1, '无实质内容')


# C11 【反向断言】12 项齐全且出处含图号/册号/条款 → 必须 PASS
def c11(tmp):
    f = fresh(tmp)
    fill_all(f, src='标书 Vol.2 p.147 §3.4', by='张三')
    return run(['verify', '--file', f])


case('C11 12项齐全+出处含图号 → 必须PASS（防假红）', c11, 0, '[PASS]')


# C11b 外部清单类出处（含图号）同样应通过
def c11b(tmp):
    f = fresh(tmp)
    fill_all(f, src='图纸 CUL-03 图号 DWG-1201 p.2', by='李四')
    return run(['verify', '--file', f])


case('C11b 出处为图号+页码 → 必须PASS', c11b, 0, '[PASS]')


def main():
    print('=' * 74)
    print('输入消化闸门 · 自证')
    print('=' * 74)
    ok = 0
    fails = []
    for name, fn, exp, exp_in in CASES:
        tmp = tempfile.mkdtemp(prefix='digest_')
        try:
            rc, out = fn(tmp)
        except Exception as e:
            rc, out = -1, 'EXC %s' % e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        good = (rc == exp) and (exp_in in out if exp_in else True)
        print('  [%s] %-42s rc=%s expect=%s'
              % ('PASS' if good else 'FAIL', name[:42], rc, exp))
        if good:
            ok += 1
        else:
            fails.append((name, rc, out))
    print('-' * 74)
    print('用例覆盖 %d/%d' % (ok, len(CASES)))
    if fails:
        for nm, rc, out in fails:
            print('✗ %s (rc=%s)' % (nm, rc))
            print('  ', out[:300].replace('\n', ' | '))
        return 1
    print('全部符合预期：未消化/无出处/AI代签/未决RFI 均被拦住。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
