#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_execution_receipt.py —— 门禁执行凭证自证

测的是"能不能拦住没跑门禁就交付"，以及 v4.6.4 整改后
"能不能拦住**手写凭证**"。

两类用例刻意分开，别混：

  · **判定类**（C3/C5/C13）：凭证签名**有效**，但数值可疑（零行/分母缩水/
    分母缺失）。做法是用真实密钥重新签名后写进去 —— 这是测试替身，
    目的是把 verify 的判定分支单独逼出来，不涉及签名。
  · **真伪类**（C8/C9/C12）：**不**重新签名，模拟"手改 JSON"。这类必须
    被签名/对账拦住，重新签名就等于自己给自己发通行证，测了等于没测。

    python scripts/test_execution_receipt.py
    → 0 全部符合预期 / 1 有不符合预期 / 2 自身无法运行
"""
import atexit
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, 'gate_execution_receipt.py')
PY = sys.executable

LIGHT = ['G-A', 'G-SRC', 'G-OFF', 'G-RUN']
KEY_NAME = '.gate_run_key'


def run(args, cwd=None):
    p = subprocess.run([PY, TOOL] + args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace', cwd=cwd, timeout=300)
    return p.returncode, (p.stdout or '') + (p.stderr or '')


# ---------------------------------------------------------------------------
# 夹具：造一个**真能跑过**的最小项目（轻档四个门禁全绿）
# ---------------------------------------------------------------------------
def build_xlsx(path, scope='全桥'):
    """算量表。scope=None 时口径列为空 → G-A 会真判 FAIL。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = u'1_成本取量总表'
    ws.append([u'序号', u'CBS编码', u'名称', u'单位', u'工程量', u'单价', u'范围口径'])
    for i, (code, name, unit, qty, price) in enumerate([
            ('CBS-CV-001', 'Concrete', 'm3', 100.0, 100.0),
            ('CBS-CV-002', 'Rebar', 't', 20.0, 5000.0)], 1):
        ws.append([i, code, name, unit, qty, price, scope or ''])
    wb.save(path)


def build_decl(xlsx, path):
    """外部数据源语义声明：逐列登记并人工签署（G-SRC 的唯一输入）。"""
    ws = openpyxl.load_workbook(xlsx).active
    cols = []
    for i, h in enumerate([c.value for c in ws[1]], 1):
        role = ('quantity' if h == u'工程量'
                else 'unit' if h == u'单位'
                else 'code' if u'编码' in str(h) else 'other')
        cols.append(dict(col_index=i, header=h, col_role=role, unit='m3',
                         value_basis='net', factor_if_gross=1.0, note='x',
                         confirmed_by=u'张工', confirmed_at='2026-01-01'))
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(dict(file=xlsx, sheet=u'1_成本取量总表', columns=cols), f,
                  ensure_ascii=False, indent=1)


def _required_classes(exp):
    """从 SYSTEM_EXPECTED 的一个 system 里取"必须存在"的 class 列表。

    对结构变化免疫：gate_off_table.py 在不同版本里把 SYSTEM_EXPECTED
    写成 {class: 出处} 的平铺 dict，或 {required/either/conditional/optional}
    的分档 dict。夹具只取"必填项"，不假设是哪一种 —— 否则别的门禁一改结构，
    本自证会以"夹具写错"的名义假红，掩盖真正的回归。
    """
    if not isinstance(exp, dict):
        return []
    tiers = ('required', 'either', 'conditional', 'optional')
    if any(k in exp for k in tiers):
        out = list(exp.get('required') or [])
        for grp in exp.get('either') or []:
            # 互斥组：取组内第一项即可满足
            if isinstance(grp, (list, tuple)) and grp:
                out.append(grp[0])
            elif isinstance(grp, str):
                out.append(grp)
        return out
    return list(exp)


def build_off(path):
    """表外缺席登记表：四类判据都有可检对象且无 FAIL。"""
    sys.path.insert(0, HERE)
    import gate_off_table as g
    doc = dict(schema='off-table-gaps/v2',
               drawings=[dict(sheet='S-201', scope_status='in_scope',
                              pages_total=10, pages_valid=10, blank_pages=0,
                              status_code='IFC', note='')],
               systems=['bridge'],
               # 显式声明结构体系：必须声明，不能靠"未声明→默认"的兜底分支
               # 让夹具依赖门禁的容错路径（兜底分支一旦写错，夹具会连带假红，
               # 分不清是门禁坏还是夹具坏）。
               structure_system='simply_supported',
               component_classes=_required_classes(g.SYSTEM_EXPECTED['bridge']),
               system_waivers={}, class_aliases={}, attributes_note={},
               ratio_bands=[], observed_ratios=[],
               open_items=[], open_items_declared_zero=True, scan_files=[])
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)


def project(tmp, bad_scope=False, partial=False, empty=False):
    """把**预跑好的**凭证复制进本用例的临时目录。

    为什么要预跑再复制，而不是每个用例各跑一次 `run`：
    本机每个 Python 子进程冷启动约 4~8 秒，一次 `run` 要起 4 个门禁子进程。
    13 个用例各跑一次 → 6 分钟，自证慢到没人愿意在提交前跑，等于没有自证。

    复制的安全性：签名只覆盖 `id|exit_code|rows_checked|output_sha256`
    与分母，**不含路径**，所以同一批真实运行的凭证可以安全地落在不同目录；
    `verify` 也不会重新执行门禁、不会重算 output_sha256。
    """
    os.makedirs(os.path.join(tmp, 'takeoff'), exist_ok=True)
    kind = ('empty' if empty
            else 'partial' if partial
            else 'red' if bad_scope else 'green')
    src, src_root = _template(kind)
    f = os.path.join(tmp, 'takeoff', 'gate_receipt.json')
    shutil.copy(src, f)
    # v4.6.4：密钥默认已移到用户级目录（~/.caogong/gate_run_key），不再落在
    # 凭证同目录。但同目录来源仍**必须能读到**（仅为向后兼容），否则：
    #   ① 老凭证验不了；② 本自证的 project() 复制夹具后拿不到密钥。
    # 故这里把模板实际使用的密钥复制进每个用例的临时目录。
    src_key = _key_path_used(src)
    if src_key and os.path.exists(src_key):
        dst = os.path.join(tmp, 'takeoff', KEY_NAME)
        shutil.copy(src_key, dst)
        try:
            os.chmod(dst, 0o600)
        except OSError:
            pass
    return f, tmp


def _key_path_used(receipt):
    """取该凭证实际使用的密钥文件路径（可能是用户级或同目录）。"""
    env = (os.environ.get('GATE_RUN_KEY') or '').strip()
    if env:
        return None                      # 环境变量方式：无需复制文件
    home = os.path.join(os.path.expanduser('~'), '.caogong', 'gate_run_key')
    if os.path.exists(home):
        return home
    p = os.path.join(os.path.dirname(os.path.abspath(receipt)), KEY_NAME)
    return p if os.path.exists(p) else None


def real_run(f, root, only=None, strict=False, extra=None):
    """真跑（走 run 子命令，唯一能写 exit_code 的入口）。"""
    args = ['run', '--file', f, '--tier', u'轻档', '--root', root]
    if only:
        args += ['--only', only]
    if strict:
        args += ['--strict']
    return run(args + list(extra or []), cwd=root)


_TEMPLATES = {}
_TEMPLATE_ROOT = None


def _template(kind):
    """会话内只真跑一次，结果复用（kind: green / red / partial / empty）。"""
    global _TEMPLATE_ROOT
    if kind in _TEMPLATES:
        return _TEMPLATES[kind]

    if _TEMPLATE_ROOT is None:
        _TEMPLATE_ROOT = tempfile.mkdtemp(prefix='receipt_tpl_')
        atexit.register(shutil.rmtree, _TEMPLATE_ROOT, True)

    root = os.path.join(_TEMPLATE_ROOT, kind)
    os.makedirs(os.path.join(root, 'takeoff'), exist_ok=True)
    x = os.path.join(root, 'takeoff', 'quantity_takeoff.xlsx')
    build_xlsx(x, None if kind == 'red' else u'全桥')
    build_decl(x, os.path.join(root, 'takeoff', 'src_declare.json'))
    build_off(os.path.join(root, 'takeoff', 'off_table.json'))
    f = os.path.join(root, 'takeoff', 'gate_receipt.json')
    run(['init', '--out', f, '--tier', u'轻档', '--project', 'TEST'])

    if kind == 'empty':
        pass                            # 只 init，不跑：全部条目都是未运行
    elif kind == 'partial':
        real_run(f, root, only='G-A,G-SRC,G-RUN')
    else:
        real_run(f, root)

    _TEMPLATES[kind] = (f, root)
    return _TEMPLATES[kind]


def _key(receipt):
    with open(os.path.join(os.path.dirname(os.path.abspath(receipt)),
                           KEY_NAME), encoding='utf-8') as f:
        return bytes.fromhex(f.read().strip())


def _sig(key, gid, ec, rc, sha):
    msg = '%s|%s|%s|%s' % (gid, 'null' if ec is None else ec,
                           'null' if rc is None else rc,
                           'null' if sha is None else sha)
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).hexdigest()


def _sig_d(key, gid, rexp):
    msg = 'denom|%s|%s' % (gid, 'null' if rexp is None else rexp)
    return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).hexdigest()


def forge(receipt, gid, **fields):
    """测试替身：改某行数值并用**真实密钥重新签名**。

    只用于逼出 verify 的**判定分支**（零行/分母缩水/分母缺失）。
    真伪类用例（C8/C9/C12）绝不能用它 —— 那等于自己签发通行证。
    """
    with open(receipt, encoding='utf-8') as f:
        rec = json.load(f)
    key = _key(receipt)
    hit = None
    for g in rec['gates']:
        if g['id'] == gid:
            hit = g
    if hit is None:
        raise AssertionError('凭证里没有 %s' % gid)
    hit.update(fields)
    hit['attest'] = _sig(key, gid, hit.get('exit_code'),
                         hit.get('rows_checked'), hit.get('output_sha256'))
    hit['attest_denom'] = _sig_d(key, gid, hit.get('rows_expected'))
    with open(receipt, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    return receipt


CASES = []


def case(name, fn, expect_rc, expect_in=''):
    CASES.append((name, fn, expect_rc, expect_in))


# C1 空凭证 → FAIL（未运行≠通过）
def c1(tmp):
    f, root = project(tmp, empty=True)
    return run(['verify', '--file', f])


case('C1 空凭证（门禁没跑）→ 必须FAIL', c1, 1, u'未运行 ≠ 通过')


# C2 真跑全部通过 → PASS
def c2(tmp):
    f, root = project(tmp)          # 模板已是真跑全绿的凭证
    return run(['verify', '--file', f])


case('C2 真跑全绿 → 必须PASS', c2, 0, '[PASS]')


# C3 零行 → FAIL（签名有效，仅数值可疑）
def c3(tmp):
    f, root = project(tmp)
    forge(f, 'G-A', exit_code=0, rows_checked=0, rows_expected=10)
    return run(['verify', '--file', f])


case('C3 检查行数=0 → 必须FAIL', c3, 1, u'等于没检')


# C4 某门禁 exit=1 → FAIL（真跑：口径列留空，G-A 自己判红）
def c4(tmp):
    f, root = project(tmp, bad_scope=True)   # 模板：口径列留空，G-A 真跑 exit=1
    return run(['verify', '--file', f])


case('C4 门禁exit=1 → 必须FAIL', c4, 1, 'exit_code=1')


# C5 分母缩水（只检 5/10 行）→ FAIL
def c5(tmp):
    f, root = project(tmp)
    forge(f, 'G-SRC', exit_code=0, rows_checked=5, rows_expected=10)
    return run(['verify', '--file', f])


case('C5 分母缩水 5/10 → 必须FAIL', c5, 1, u'未被验证')


# C6 凭证文件缺失 → CANNOT_RUN(2)
def c6(tmp):
    return run(['verify', '--file', os.path.join(tmp, 'none.json')])


case('C6 无凭证文件 → 必须CANNOT_RUN', c6, 2, 'CANNOT_RUN')


# C7 G-OFF 必须真的进了 required 清单。
# 只漏这一个（--only 跳过它）、其余全填 → 必须 FAIL 并点名 G-OFF。
# 「写进技能包 ≠ 会被加载 ≠ 会被执行」——这条用例就是防它。
def c7(tmp):
    f, root = project(tmp, partial=True)     # 模板：--only 跳过了 G-OFF
    return run(['verify', '--file', f])


case('C7 只漏 G-OFF 一项 → 必须FAIL并点名', c7, 1, 'G-OFF')


# ---------------------------------------------------------------------------
# v4.6.4 新增反向用例
# ---------------------------------------------------------------------------
# C8 手改 exit_code（不重新签名）→ 必须 FAIL 且提示签名/哈希不一致
def c8(tmp):
    f, root = project(tmp, bad_scope=True)
    with open(f, encoding='utf-8') as fh:
        rec = json.load(fh)
    for g in rec['gates']:
        if g['id'] == 'G-A':
            assert g['exit_code'] == 1, '前置条件：G-A 真跑应 exit=1'
            g['exit_code'] = 0          # ← 把红的改成绿的，签名不动
    with open(f, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    return run(['verify', '--file', f])


case('C8 手改 exit_code 1→0 → 必须FAIL(签名)', c8, 1, u'疑似手改')


# C9 从凭证里整条删除 G-OFF（不重新签名）→ 必须 FAIL 并点名 G-OFF
def c9(tmp):
    f, root = project(tmp)
    with open(f, encoding='utf-8') as fh:
        rec = json.load(fh)
    rec['gates'] = [g for g in rec['gates'] if g['id'] != 'G-OFF']
    with open(f, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    return run(['verify', '--file', f])


case('C9 整条删除 G-OFF → 必须FAIL并点名', c9, 1, u'required 门禁缺失：G-OFF')


# C10 tier=全量 但只放 1 个自造 id → 必须 FAIL 并列出缺失清单
def c10(tmp):
    f, root = project(tmp)
    with open(f, encoding='utf-8') as fh:
        rec = json.load(fh)
    rec['tier'] = u'全量'
    rec['gates'] = [dict(id='MY-GATE', required=True, exit_code=0,
                         rows_checked=1, rows_expected=1)]
    with open(f, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    return run(['verify', '--file', f])


case('C10 全量档只放1个自造id → 必须FAIL列缺失', c10, 1, 'G-CHAIN')


# C11 record 未带 --attest-from → 必须返回 1（拒绝手填）
def c11(tmp):
    f, root = project(tmp)
    return run(['record', '--file', f, '--id', 'G-A', '--rows', '10'], cwd=root)


case('C11 record 无 --attest-from → 必须拒绝', c11, 1, u'禁止手工填写')


# C12 exit_code=false（布尔）→ 必须 FAIL（False == 0 不得过关）
def c12(tmp):
    f, root = project(tmp)
    with open(f, encoding='utf-8') as fh:
        rec = json.load(fh)
    for g in rec['gates']:
        if g['id'] == 'G-A':
            g['exit_code'] = False      # 布尔：Python 里 False == 0
    with open(f, 'w', encoding='utf-8') as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1)
    return run(['verify', '--file', f])


case('C12 exit_code=false(布尔) → 必须FAIL', c12, 1, u'类型不符')


# C13 分母未声明 → WARN「未声明分母」；strict 下 FAIL
def c13(tmp):
    f, root = project(tmp)
    forge(f, 'G-A', rows_expected=None)
    rc1, out1 = run(['verify', '--file', f])
    rc2, out2 = run(['verify', '--file', f, '--strict'])
    ok = (rc1 == 0 and u'未声明分母' in out1 and rc2 == 1
          and u'未声明分母' in out2)
    return (rc2 if ok else rc1), out1 + out2


case('C13 未声明分母 → WARN；strict→FAIL', c13, 1, u'未声明分母')


# C14 自己造密钥 + 自己签名（"我跑过了"的终极伪造）→
#     sign 层面查不出（签名本来就由同一只手签发），必须靠 --recheck 当场重跑。
#     这条用例存在的意义：签名不是钢印，把它的失效边界钉在自证里。
def c14(tmp):
    f, root = project(tmp)
    key = os.urandom(32)
    with open(os.path.join(tmp, 'takeoff', KEY_NAME), 'w', encoding='utf-8') as fh:
        fh.write(key.hex())
    gates = []
    for gid in LIGHT:
        gates.append(dict(id=gid, required=True, exit_code=0, rows_checked=1,
                          rows_expected=1, output_sha256='0' * 64,
                          cmdline=['python', '-c', 'raise SystemExit(3)'],
                          attest=_sig(key, gid, 0, 1, '0' * 64),
                          attest_denom=_sig_d(key, gid, 1)))
    with open(f, 'w', encoding='utf-8') as fh:
        json.dump(dict(project='FAKE', tier=u'轻档', gates=gates), fh,
                  ensure_ascii=False, indent=1)
    # 不带 --recheck：签名自洽 → 会通过（这是 HMAC 的固有边界）
    rc_plain, out_plain = run(['verify', '--file', f, '--strict'])
    # 带 --recheck：cmdline 跑出来 exit=3 → 必须 FAIL
    rc_check, out_check = run(['verify', '--file', f, '--strict', '--recheck'],
                              cwd=root)
    ok = rc_plain == 0 and rc_check == 1 and u'复算' in out_check
    return (rc_check if ok else rc_plain), out_plain + out_check


case('C14 自造密钥全签名 → 仅 --recheck 能拦', c14, 1, u'复算')


def main():
    print('=' * 74)
    print(u'门禁执行凭证 · 自证')
    print('=' * 74)
    ok = 0
    fails = []
    for name, fn, exp, exp_in in CASES:
        tmp = tempfile.mkdtemp(prefix='receipt_')
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
    print(u'用例覆盖 %d/%d' % (ok, len(CASES)))
    if fails:
        for nm, rc, out in fails:
            print(u'✗ %s (rc=%s)' % (nm, rc))
            print('  ', out[:400].replace('\n', ' | '))
        return 1
    print(u'全部符合预期：未跑/零行/FAIL/分母缩水/手改/删项/布尔/未声明分母 均被拦住。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
