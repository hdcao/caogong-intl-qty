#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_execution_receipt.py —— 门禁执行凭证（G-RUN）

## 它解决什么问题

本项目连续 21 轮迭代，反复出现同一类事故，根因已定位为一句话：

    「写进技能包」≠「会被加载」≠「会被执行」

历史实证：
  · 某轮：G-X0 规则、门禁、脚本三样齐全，仍写出"交叉检核通过（独立代码路径）"，
    实际是自建对照表、根本没跑脚本 → 13 项全 0.000% 的假通过漏网。
  · 某轮：教训刚写进记忆，下一轮同类错误**第三次**重犯。
  · 某轮：改了台账没同步基准，门禁连报 6 次不同步（这次门禁起作用了，
    因为**真的跑了**）。

结论：**规则的有效性不取决于写得多好，而取决于"没跑就出不了交付物"。**
本脚本提供这道机械闸：交付前必须凑齐一份**执行凭证**，逐条声明每个必跑门禁
的实际退出码与检查行数；缺项、伪造、行数为 0 一律 FAIL。

## v4.6.4 整改：凭证不再可手写

旧版（v4.6.3 及以前）有三个结构性漏洞，被第三方审核判定为"可完全伪造"：

  L1  `record --exit-code 0 --rows 1` 手写一行 JSON 即可拿到「通过」——
      凭证里的数字**没有任何运行痕迹**可核。
  L2  `verify` 从不与 `TIER_GATES` 对账：整条删掉 G-OFF，`verify` 仍返 0——
      "凭证里有什么就检什么"，等于清单可以自己删。
  L3  `record` 不传 `--expect-rows` 时 `rows_expected = rows`——
      分母由分子自己定义，缩水判据自我参照恒真。
  L4  无类型断言（`False == 0` 通过），无任何签名（改 exit_code / run_at 全无痕）。

本版对应四条硬修：

  R1 **运行即留痕**：新增 `run` 子命令，自己用 subprocess 真正执行该档位每个
     门禁，记录真实 `exit_code`、从 stdout 抽取的 `rows_checked`、
     `output_sha256`、`cmdline`、`run_at`、`run_id`。参数无法自动构造的门禁
     **记为 `manual_required` 并明确列出**，绝不伪造成功。
     每行用 HMAC-SHA256 签名：`id|exit_code|rows_checked|output_sha256`，
     密钥 `<凭证同目录>/.gate_run_key`（32 字节）。
  R2 **与清单对账**：`verify` 读 `rec['tier']`，把 `TIER_GATES[tier]` 的 id 全集
     与凭证里的 required 条目逐一对账，缺失**逐一点名**；清单外的自造 id 记 WARN
     且不计入通过数。档位不在 `TIER_GATES` → CANNOT_RUN。
  R3 **分母不得自参照**：`rows_expected` 只能来自门禁自身输出里的分母，或
     `run --expect-rows ID=N` 显式声明；缺失即 `None` + WARN「未声明分母」。
     `record --exit-code` 已移除，`record` 必须带 `--attest-from <run_id>`。
  R4 **类型与签名断言**：`exit_code` 须 `int` 且非 `bool`；`rows_checked` 须
     `int>=0`；`rows_expected` 若非 None 须 `int>=0`；签名不符 → FAIL
     「凭证与运行记录不一致（疑似手改）」；有执行记录却无密钥 → CANNOT_RUN。

## HMAC 的能力边界（务必知道，别把它当防伪钢印）

HMAC 密钥 `.gate_run_key` 就放在凭证同目录，**任何能写凭证的手也能写密钥**。
所以签名能证明的只有一件事：

    「这份凭证自 run 签发之后没有被改过」

它**不能**证明「这些门禁真的被跑过」——有人完全可以自己造密钥、自己按格式
签一份全绿凭证。要证后半句，用：

```bash
python scripts/gate_execution_receipt.py verify --file <凭证> --recheck
```

`--recheck` 会按凭证里记录的 `cmdline` **当场重跑**每个门禁，比对退出码与
检查行数（输出哈希含时间戳，重跑必然不同，故不比哈希）。
交付前复核、以及验收别人交来的凭证时，应当用 `--recheck`。

签名原文两条，缺一不可：
  `attest       = HMAC(key, "id|exit_code|rows_checked|output_sha256")`
  `attest_denom = HMAC(key, "denom|id|rows_expected")`
第二条是补的：R1 规定的原文只覆盖 4 个字段，不含 `rows_expected`，
而它正是「分母缩水」的阈值——不签它，把分母调小就能把缩水洗成通过。

## 凭证格式（gate_receipt.json）

```json
{
 "project": "SPARK-EPR", "tier": "中档", "generated_at": "2026-11-04T15:20:00",
 "run": {"run_id": "9f3c…", "run_at": "2026-11-04T15:19:02", "tier": "中档"},
 "gates": [
   {"id": "G-OFF", "required": true, "exit_code": 0, "rows_checked": 7,
    "rows_expected": null, "cmdline": ["python", "gate_off_table.py", "check", "…"],
    "run_at": "2026-11-04T15:19:05", "run_id": "9f3c…",
    "output_sha256": "5a1f…", "attest": "b7d2…", "manual_required": false}
 ]
}
```

## 用法

```bash
# ① 生成模板（列出该档位必跑门禁）
python scripts/gate_execution_receipt.py init --root . --tier 全量 \
    --out takeoff/gate_receipt.json

# ② 真跑（唯一能写 exit_code 的入口）
python scripts/gate_execution_receipt.py run --file takeoff/gate_receipt.json \
    --tier 轻档 --root . --xlsx takeoff/quantity_takeoff.xlsx \
    --off-file takeoff/off_table.json [--only G-OFF,G-SRC] [--timeout 240]

# ③ 交付前验收（这是唯一的放行闸）
python scripts/gate_execution_receipt.py verify --file takeoff/gate_receipt.json \
     [--strict] [--recheck]
```

## 退出码

    0  凭证完整、与清单一致、签名有效、无 FAIL / 无未运行 / 无零行
    1  存在缺项、FAIL、零行、分母缩水、签名不符或未声明分母（strict）
    2  凭证文件缺失/损坏、档位未知，或缺少签名密钥（= 无法核验，不是"通过"）

## 四条硬断言

1. **required 门禁缺失 → FAIL**，并逐一点名。没记录 = 没跑，不许默认通过。
2. **rows_checked == 0 → FAIL**。零行等于没检（与 SKILL.md L312 一致）。
3. **rows_checked < rows_expected → FAIL**。分母缩水说明只检了部分，
   剩下的判据并未被验证（与 SKILL.md L338 一致）。
4. **签名不符 → FAIL**。凭证里的每个数字都必须能追到一次真实运行。
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import re
import shlex
import subprocess
import sys
import uuid

# 控制台编码兜底：中文 Windows 默认 cp936，一条 INFO 就可能抛 UnicodeEncodeError
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors='replace')
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.abspath(__file__)
KEY_NAME = '.gate_run_key'          # HMAC 密钥（与凭证同目录）
LOG_DIR = '.gate_run_log'           # 每次 run 的逐门禁摘要（供 record 回填）
VERSION = '4.6.5'

# 三档位各自必跑的门禁清单（与 SKILL.md「三档渐进」对齐）
TIER_GATES = {
    '全量': [
        ('G-CHAIN', 'chain_check.py run', '尺寸链闭合'),
        ('G-XD', 'cross_drawing_check.py run', '跨图一致性（4组22项）'),
        ('G-FRESH', 'gate_basis_freshness.py', '基线新鲜度'),
        ('G-DB', 'gate_drawing_basis.py', '量 vs 基线'),
        ('G-SUB', 'gate_sheet_formula.py', '多小计逐组覆盖'),
        ('G-A', 'gate_scope_declaration.py', '口径声明'),
        ('G-OWN', 'gate_single_owner.py', '一物一入口'),
        ('G-UNIT', 'gate_unit_consistency.py', '单位量纲'),
        ('G-DW', 'gate_dimension_wording.py', '跨列自洽'),
        ('G-XS', 'gate_cross_sheet.py', '跨表一致'),
        ('G-EXP', 'gate_expected_total.py', '业主清单闭环'),
        ('G-SRC', 'gate_source_semantics.py', '外部数据源语义'),
        ('G-OFF', 'gate_off_table.py check', '表外缺席（覆盖率/必搭配件/比率带/敞口四态）'),
        ('G-FI', 'gate_fault_injection.py', '门禁注入自证'),
        ('G-RUN', 'gate_execution_receipt.py verify', '本凭证自身'),
    ],
    '中档': [
        ('G-CHAIN', 'chain_check.py run', '尺寸链闭合'),
        ('G-XD', 'cross_drawing_check.py run', '跨图一致性'),
        ('G-FRESH', 'gate_basis_freshness.py', '基线新鲜度'),
        ('G-DB', 'gate_drawing_basis.py', '量 vs 基线'),
        ('G-SUB', 'gate_sheet_formula.py', '多小计逐组覆盖'),
        ('G-A', 'gate_scope_declaration.py', '口径声明'),
        ('G-OWN', 'gate_single_owner.py', '一物一入口'),
        ('G-UNIT', 'gate_unit_consistency.py', '单位量纲'),
        ('G-SRC', 'gate_source_semantics.py', '外部数据源语义'),
        ('G-OFF', 'gate_off_table.py check', '表外缺席'),
        ('G-RUN', 'gate_execution_receipt.py verify', '本凭证自身'),
    ],
    '轻档': [
        ('G-A', 'gate_scope_declaration.py', '口径声明'),
        ('G-SRC', 'gate_source_semantics.py', '外部数据源语义'),
        ('G-OFF', 'gate_off_table.py check', '表外缺席'),
        ('G-RUN', 'gate_execution_receipt.py verify', '本凭证自身'),
    ],
}


def now():
    return datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


# ---------------------------------------------------------------------------
# R1：签名与密钥
# ---------------------------------------------------------------------------
def _home_key_path():
    return os.path.join(os.path.expanduser('~'), '.caogong', 'gate_run_key')


def _same_dir_key_path(receipt_path):
    return os.path.join(os.path.dirname(os.path.abspath(receipt_path)), KEY_NAME)


# 可信来源：密钥不在项目树内。'same_dir' 是**不可信**的 —— 见 _load_key 注释。
TRUSTED_KEY_SOURCES = ('env', 'home')


def _resolve_key(receipt_path):
    """返回 (source, location)。source ∈ {'env','home','same_dir',None}。

    优先级：环境变量 > 用户级目录 > 凭证同目录（仅为兼容老凭证，不可信）。

    为什么要有这一层（v4.6.4 修）：
      旧版把密钥固定放在凭证同目录 `.gate_run_key`。密钥与被保护对象同处一个
      可写目录时，**任何能写凭证的手也能写密钥并重算签名**，HMAC 的防护为 0。
      已实测攻击：自造 32B 密钥 + 自签 receipt + 假 run_log → verify 返回 0
      「签名有效」。故必须把默认位置移出项目树，并把同目录来源判为不可信。
    """
    env = (os.environ.get('GATE_RUN_KEY') or '').strip()
    if env:
        return 'env', None
    home = _home_key_path()
    if os.path.exists(home):
        return 'home', home
    return 'same_dir', _same_dir_key_path(receipt_path)


def _parse_key(raw, where):
    try:
        key = bytes.fromhex(raw)
    except Exception as e:
        raise RuntimeError('密钥格式非法（须 hex）：%s（%s）' % (where, e))
    if len(key) < 16:
        raise RuntimeError('密钥长度 %d 字节，过短（须≥16）：%s' % (len(key), where))
    return key


def _load_key(receipt_path):
    """读 HMAC 密钥。返回 (key, source)；没有密钥返回 (None, None)。

    损坏一律抛 RuntimeError —— 不得静默当没有（静默会让"密钥坏了"变成"无需签名"）。
    **source == 'same_dir' 时调用方必须判不可信**：能写凭证的人也能写这个密钥。
    """
    src, p = _resolve_key(receipt_path)
    if src == 'env':
        return _parse_key((os.environ.get('GATE_RUN_KEY') or '').strip(),
                          '$GATE_RUN_KEY'), 'env'
    if not os.path.exists(p):
        return None, None
    try:
        raw = open(p, 'r', encoding='utf-8').read().strip()
    except OSError as e:
        raise RuntimeError('密钥文件读不出：%s（%s）' % (p, e))
    return _parse_key(raw, p), src


def _ensure_key(receipt_path):
    """取密钥用于签名；没有则生成到**用户级目录**（不在项目树内）。"""
    key, src = _load_key(receipt_path)
    if key:
        if src == 'same_dir':
            print('[WARN] 正在使用与凭证同目录的密钥 %s —— 该密钥拦不住有意伪造，'
                  '建议迁移到 $GATE_RUN_KEY 或 %s'
                  % (_same_dir_key_path(receipt_path), _home_key_path()))
        return key
    if (os.environ.get('GATE_RUN_KEY') or '').strip():
        # 环境变量给了但解析失败，_load_key 已抛；走到这里说明是空值
        pass
    p = _home_key_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    key = os.urandom(32)
    with open(p, 'w', encoding='utf-8') as f:
        f.write(key.hex())
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    print('[OK] 已生成 HMAC 密钥：%s（32 字节，用户级目录，不在项目树内）' % p)
    print('     该密钥可防"无意误改"与"跨机伪造"；同机同用户下的有意自签无法用'
          '本地文件手段拦住，需外部不可写证据源（CI 日志/git 提交）。')
    return key


def _attest_msg(gid, exit_code, rows_checked, sha):
    """签名原文。None 一律写 'null'，避免 None/''/'None' 三者互相冒充。"""
    return '%s|%s|%s|%s' % (
        gid,
        'null' if exit_code is None else exit_code,
        'null' if rows_checked is None else rows_checked,
        'null' if sha is None else sha,
    )


def _sign(key, gid, exit_code, rows_checked, sha):
    return hmac.new(key, _attest_msg(gid, exit_code, rows_checked, sha).encode('utf-8'),
                    hashlib.sha256).hexdigest()


def _denom_msg(gid, rows_expected):
    """分母单独签名。

    R1 的签名原文按整改要求只覆盖 4 个字段（id|exit_code|rows_checked|
    output_sha256），而 `rows_expected` 恰恰是"分母缩水"判据的阈值 —— 不签它，
    攻方只要把 rows_expected 调小就能把"分母缩水"改成 PASS。故另签一条
    `attest_denom`，两者都要过。
    """
    return 'denom|%s|%s' % (gid, 'null' if rows_expected is None else rows_expected)


def _sign_denom(key, gid, rows_expected):
    return hmac.new(key, _denom_msg(gid, rows_expected).encode('utf-8'),
                    hashlib.sha256).hexdigest()


def _log_path(receipt_path, run_id, gid):
    d = os.path.join(os.path.dirname(os.path.abspath(receipt_path)), LOG_DIR, run_id)
    return os.path.join(d, gid + '.json')


# ---------------------------------------------------------------------------
# R1：参数适配器 —— 把 TIER_GATES 里的命令前缀补全成可执行 argv
#
# 每个适配器返回 (额外参数列表, 缺失说明)。缺参数时返回 (None, '缺 --xxx（或…）')，
# 由 run 记为 manual_required —— 绝不猜、绝不伪造成功。
# ---------------------------------------------------------------------------
def _n(text, pattern, gi=1):
    m = re.search(pattern, text or '')
    if not m:
        return None
    try:
        return int(m.group(gi))
    except (TypeError, ValueError):
        return None


def _need(ctx, key, flag, candidates):
    """显式参数优先；否则在 root 下按惯例路径找；都没有 → 缺参数说明。"""
    v = ctx.get(key)
    if v:
        return v, None
    root = ctx.get('root') or '.'
    for rel in candidates:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p, None
    return None, '缺 --%s（或把文件放到 %s）' % (flag, candidates[0])


def _xlsx(ctx):
    return _need(ctx, 'xlsx', 'xlsx', ['takeoff/quantity_takeoff.xlsx'])


def _b_sheet_gates(ctx, gid):
    """只要算量表（+ 可选 --sheet/--ack）的门禁。"""
    x, miss = _xlsx(ctx)
    if miss:
        return None, miss
    args = [x]
    if ctx.get('sheet'):
        args += ['--sheet', ctx['sheet']]
    if ctx.get('ack'):
        args += ['--ack', ctx['ack']]
    return args, None


def _b_basis(ctx, gid):
    x, miss = _xlsx(ctx)
    if miss:
        return None, miss
    if gid == 'G-FRESH':
        b, m2 = _need(ctx, 'basis_freshness', 'basis-freshness',
                      ['takeoff/basis.json', 'takeoff/basis_freshness.json'])
    else:
        b, m2 = _need(ctx, 'basis_drawing', 'basis-drawing',
                      ['takeoff/drawing_baseline.json', 'reference/drawing_baseline.json'])
    if m2:
        return None, m2
    args = [x, '--basis', b]
    if ctx.get('sheet'):
        args += ['--sheet', ctx['sheet']]
    return args, None


def _b_chain(ctx, gid):
    spec, miss = _need(ctx, 'chain_spec', 'chain-spec',
                       ['takeoff/chain_spec.json', 'takeoff/chain.json'])
    if miss:
        return None, miss
    return ['--spec', spec], None


def _b_xd(ctx, gid):
    spec, miss = _need(ctx, 'xd_spec', 'xd-spec',
                       ['takeoff/cross_check.json', 'takeoff/cross_check_spec.json'])
    if miss:
        return None, miss
    return ['--spec', spec], None


def _b_xs(ctx, gid):
    s, miss = _need(ctx, 'summary_xlsx', 'summary-xlsx',
                    ['takeoff/summary_control.xlsx'])
    if miss:
        return None, miss
    mp, m2 = _need(ctx, 'xs_map', 'xs-map',
                   ['takeoff/cross_sheet_map.json', 'reference/cross_sheet_map.json'])
    if m2:
        return None, m2
    return [s, '--map', mp], None


def _b_exp(ctx, gid):
    x, miss = _xlsx(ctx)
    if miss:
        return None, miss
    e, m2 = _need(ctx, 'expect_json', 'expect-json',
                  ['takeoff/expectations.json', 'reference/expectations.json'])
    if m2:
        return None, m2
    args = [x, '--expect', e]
    if ctx.get('sheet'):
        args += ['--sheet', ctx['sheet']]
    return args, None


def _b_src(ctx, gid):
    """G-SRC 的 --file 是待体检的外部清单；未单独给就用算量表。"""
    f = ctx.get('src_file')
    if not f:
        f, miss = _xlsx(ctx)
        if miss:
            return None, miss
    d, m2 = _need(ctx, 'src_declare', 'src-declare',
                  ['takeoff/src_declare.json', 'out/src_check/decl.json'])
    if m2:
        return None, m2
    return ['verify', '--file', f, '--declare', d], None


def _b_off(ctx, gid):
    f, miss = _need(ctx, 'off_file', 'off-file', ['takeoff/off_table.json'])
    if miss:
        return None, miss
    args = ['--file', f]
    if ctx.get('strict'):
        args += ['--strict']
    return args, None


def _b_fi(ctx, gid):
    x, miss = _xlsx(ctx)
    if miss:
        return None, miss
    args = [x]
    if ctx.get('sheet'):
        args += ['--sheet', ctx['sheet']]
    if ctx.get('xs_map'):
        args += ['--allow-subprocess', '--xs-map', ctx['xs_map']]
    return args, None


ARG_BUILDERS = {
    'G-CHAIN': _b_chain,
    'G-XD': _b_xd,
    'G-FRESH': _b_basis,
    'G-DB': _b_basis,
    'G-SUB': _b_sheet_gates,
    'G-A': _b_sheet_gates,
    'G-OWN': _b_sheet_gates,
    'G-UNIT': _b_sheet_gates,
    'G-DW': _b_sheet_gates,
    'G-XS': _b_xs,
    'G-EXP': _b_exp,
    'G-SRC': _b_src,
    'G-OFF': _b_off,
    'G-FI': _b_fi,
    'G-RUN': None,      # 由 run 内部特判（verify 自身，见 cmd_run）
}


# ---------------------------------------------------------------------------
# R1：从 stdout 抽 检查行数 / 分母。
#
# 分母只认**门禁自己打印的总量**（它自己扫描时确定的分母），或 `run --expect-rows`
# 显式声明的人工核对总量。绝不拿 `--rows` 顶替 —— 那是审核指出的「自我参照恒真」。
# 工具没打印总量 → 返回 None，verify 报「未声明分母」，不静默 PASS。
# 凡工具只打印一个总数（如 G-A 的「数量行 N 条」），该数既是分子也是分母，
# 这是门禁的声明而非调用方的输入，允许作为分母。
# ---------------------------------------------------------------------------
def _same(t, pat):
    n = _n(t, pat)
    return (n, n) if n is not None else (None, None)


def _ex_off(t):
    m = re.search(r'实检：COV (\d+) 册 / PAIR (\d+) 组 / RATIO (\d+) 项 / '
                  r'OI (\d+) 条 / LEDGER (\d+) 行', t or '')
    if not m:
        return None, None
    s = sum(int(x) for x in m.groups())
    return s, s


def _ex_dw(t):
    a = _n(t, r'量纲校验覆盖：已校验 (\d+) 行')
    if a is None:
        return None, None
    b = _n(t, r'未校验 (\d+) 行')
    return a, a + (b or 0)


def _ex_chain(t):
    total = _n(t, r'(\d+) 条判据')
    if total is None:
        return None, None
    block = _n(t, r'阻断 (\d+) 项') or 0
    pend = _n(t, r'待核 (\d+) 项') or 0
    return total - block - pend, total


def _ex_run(t):
    a = _n(t, r'通过 (\d+) 项')
    if a is None:
        return None, None
    return (a + (_n(t, r'未运行 (\d+) 项') or 0)
            + (_n(t, r'待确认 (\d+) 项') or 0)), None


def _ex_unit(t):
    n = _n(t, r'体积类行 (\d+) 条（其中 (\d+) 条有单价')
    if n is None:
        return None, None
    return _n(t, r'体积类行 \d+ 条（其中 (\d+) 条有单价'), n


EXTRACTORS = {
    'G-CHAIN': _ex_chain,
    'G-XD': lambda t: (_n(t, r'通过 (\d+) / 冲突'), _n(t, r'（共 (\d+) 项）')),
    'G-FRESH': lambda t: (_n(t, r'有效基准 (\d+) 条 / 数据行'),
                          _n(t, r'有效基准 \d+ 条 / 数据行 (\d+)')),
    'G-DB': lambda t: (_n(t, r'基准 \d+ 条（有效 (\d+) 条）'),
                       _n(t, r'基准 (\d+) 条（有效')),
    'G-SUB': lambda t: _same(t, r'量行 (\d+) 条'),
    'G-A': lambda t: _same(t, r'数量行 (\d+) 条'),
    'G-OWN': lambda t: _same(t, r'数据行 (\d+)'),
    'G-UNIT': _ex_unit,
    'G-DW': _ex_dw,
    'G-XS': lambda t: (_n(t, r'通过 (\d+) / 冲突'), _n(t, r'（共 (\d+) 项）')),
    'G-EXP': lambda t: _same(t, r'已扫 (\d+) 项期望'),
    'G-SRC': lambda t: (_n(t, r'已登记 (\d+) /'), _n(t, r'已登记 \d+ / (\d+)')),
    'G-OFF': _ex_off,
    'G-FI': lambda t: (_n(t, r'用例覆盖 (\d+)/'), _n(t, r'用例覆盖 \d+/(\d+)')),
    'G-RUN': _ex_run,
}


def _extract(gid, text):
    fn = EXTRACTORS.get(gid)
    if not fn:
        return None, None
    try:
        return fn(text)
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------
def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _save(path, rec):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)


def _find(gates, gid):
    for g in gates:
        if isinstance(g, dict) and g.get('id') == gid:
            return g
    return None


def _row_of(rec, gid, prefix, desc):
    row = _find(rec.get('gates') or [], gid)
    if row is not None:
        return row
    row = dict(id=gid, required=True, exit_code=None, rows_checked=None,
               rows_expected=None, cmd='python scripts/' + prefix,
               note=desc, run_at=None)
    rec.setdefault('gates', []).append(row)
    print('[INFO] 凭证里没有 %s，已补建条目（本档位必跑）' % gid)
    return row


def _type_ok_int(v):
    return type(v) is int          # 布尔是 int 的子类，必须显式排除


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
def cmd_init(a):
    tier = a.tier or '中档'
    if tier not in TIER_GATES:
        print('[CANNOT_RUN] 未知档位 %r，可选 %s' % (tier, list(TIER_GATES)))
        return 2
    path = a.out or os.path.join(a.root or '.', 'takeoff', 'gate_receipt.json')
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    rec = dict(
        _README=[
            '本文件是门禁执行凭证，是交付放行的唯一机械依据。',
            'exit_code / rows_checked 只能由 `run` 子命令真跑后写入，禁止手工填写。',
            'required 缺失 / exit_code!=0 / rows_checked==0 / 签名不符 → verify 判 FAIL。',
            '每行 attest = HMAC(key, "id|exit_code|rows_checked|output_sha256")。',
            '★ 防护边界（务必如实理解）：密钥默认可读自 $GATE_RUN_KEY 或同目录 .gate_run_key。',
            '  因此本凭证只能拦住「无意的漏填与涂抹」，**拦不住有意伪造**——',
            '  任何能写本文件的手，也能写同目录的密钥并重算签名。',
            '  要真正防伪造，须把 GATE_RUN_KEY 放进用户级环境变量并由第三方保管。',
            '  本项目已验证：自造密钥 + 自签 + 假日志可使 verify 返回 0（见 selftest C14）。',
        ],
        project=a.project or '<项目名>',
        tier=tier,
        generated_at=now(),
        gates=[
            dict(id=gid, required=True, exit_code=None, rows_checked=None,
                 rows_expected=None, cmd='python scripts/' + cmd,
                 note=desc, run_at=None)
            for gid, cmd, desc in TIER_GATES[tier]
        ],
    )
    _save(path, rec)
    print('[OK] 凭证模板已生成：%s（档位=%s，%d 个必跑门禁）'
          % (path, tier, len(rec['gates'])))
    print('     下一步：run --file %s --tier %s（唯一能写 exit_code 的入口）'
          % (path, tier))
    for g in rec['gates']:
        print('     %-10s %s' % (g['id'], g['note']))
    return 0


# ---------------------------------------------------------------------------
# run（R1：真跑 + 留痕 + 签名）
# ---------------------------------------------------------------------------
def _make_ctx(a):
    ctx = dict(
        root=os.path.abspath(a.root or '.'),
        xlsx=a.xlsx, sheet=a.sheet, summary_xlsx=a.summary_xlsx,
        basis_freshness=a.basis_freshness, basis_drawing=a.basis_drawing,
        chain_spec=a.chain_spec, xd_spec=a.xd_spec, xs_map=a.xs_map,
        expect_json=a.expect_json, src_file=a.src_file,
        src_declare=a.src_declare, off_file=a.off_file, ack=a.ack,
        strict=bool(a.strict),
    )
    return ctx


def _build_argv(gid, prefix, ctx):
    parts = shlex.split(prefix)
    script = os.path.join(HERE, parts[0])
    base = parts[1:]
    if gid == 'G-RUN':
        return None, '本凭证自身：由 run 内部以 verify --skip-self 执行'
    builder = ARG_BUILDERS.get(gid)
    if builder is None:
        return None, '无参数适配器，需人工按 SKILL.md 跑'
    extra, miss = builder(ctx, gid)
    if miss:
        return None, miss
    return [sys.executable, script] + base + list(extra), None


def _exec(argv, cwd, timeout):
    """真跑；返回 (exit_code, 合并输出)。超时记 124（不是 0，也不是崩溃）。"""
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout)
        out = (p.stdout or b'') + (p.stderr or b'')
        return p.returncode, out.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        return 124, '[超时] %d 秒内未结束，门禁未完成（不等于通过）' % timeout
    except FileNotFoundError as e:
        return 127, '[无法执行] %s' % e


def cmd_run(a):
    if not os.path.exists(a.file):
        print('[CANNOT_RUN] 凭证不存在：%s（先跑 init）' % a.file)
        return 2
    try:
        rec = _load(a.file)
    except Exception as e:
        print('[CANNOT_RUN] 凭证损坏：%s' % e)
        return 2

    tier = a.tier or rec.get('tier')
    if tier not in TIER_GATES:
        print('[CANNOT_RUN] 未知档位 %r，可选 %s' % (tier, list(TIER_GATES)))
        return 2
    if rec.get('tier') and rec.get('tier') != tier:
        print('[CANNOT_RUN] 凭证档位 %r 与 --tier %r 不一致 —— 换档位请重新 init'
              % (rec.get('tier'), tier))
        return 2

    plan = [g for g, _, _ in TIER_GATES[tier]]
    if a.only:
        want = [x.strip() for x in a.only.split(',') if x.strip()]
        bad = [x for x in want if x not in plan]
        if bad:
            print('[FAIL] --only 里这些 id 不在「%s」档位必跑清单：%s'
                  % (tier, ', '.join(bad)))
            print('       自造 id 不得写入凭证（verify 也不计入通过数）。')
            return 1
        plan = [g for g in plan if g in want]
    # G-RUN 必须最后跑：它校验的正是前面那些记录
    plan.sort(key=lambda g: g == 'G-RUN')

    try:
        key = _ensure_key(a.file)
    except RuntimeError as e:
        print('[CANNOT_RUN] %s' % e)
        return 2
    run_id = uuid.uuid4().hex
    run_at = now()
    ctx = _make_ctx(a)
    overrides = _parse_expect_rows(a.expect_rows)

    print('== G-RUN 真跑（run_id=%s，档位=%s）==' % (run_id[:8], tier))
    print('   root=%s' % ctx['root'])
    print()
    manual = []
    for gid in plan:
        prefix = dict((g, c) for g, c, _ in TIER_GATES[tier])[gid]
        desc = dict((g, d) for g, _, d in TIER_GATES[tier])[gid]
        row = _row_of(rec, gid, prefix, desc)

        if gid == 'G-RUN':
            argv = [sys.executable, SELF, 'verify', '--file',
                    os.path.abspath(a.file), '--skip-self', 'G-RUN']
            miss = None
        else:
            argv, miss = _build_argv(gid, prefix, ctx)

        if miss:
            row.update(exit_code=None, rows_checked=None, rows_expected=None,
                       output_sha256=None, cmdline=None, run_at=None,
                       run_id=run_id, manual_required=True, manual_reason=miss,
                       attest=_sign(key, gid, None, None, None),
                       attest_denom=_sign_denom(key, gid, None))
            manual.append((gid, miss))
            print('[手动] %-8s 参数无法自动构造：%s' % (gid, miss))
            _save(a.file, rec)
            continue

        rc, out = _exec(argv, ctx['root'], a.timeout)
        checked, expected = _extract(gid, out)
        if gid == 'G-RUN':
            # 自证项的分母 = 本档位必跑清单里除自己之外的条数（与内部
            # `verify --skip-self G-RUN` 的计入口径一致：清单外 id 不计入）
            expected = len([x for x in plan if x != 'G-RUN'])
        if overrides.get(gid) is not None:
            expected = overrides[gid]
        sha = hashlib.sha256(out.encode('utf-8', 'replace')).hexdigest()
        row.update(exit_code=int(rc), rows_checked=checked,
                   rows_expected=expected, output_sha256=sha,
                   cmdline=list(argv), cmd=' '.join(shlex.quote(x) for x in argv),
                   run_at=now(), run_id=run_id, manual_required=False,
                   manual_reason=None,
                   attest=_sign(key, gid, int(rc), checked, sha),
                   attest_denom=_sign_denom(key, gid, expected))
        _save(a.file, rec)
        try:
            lp = _log_path(a.file, run_id, gid)
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            _save(lp, dict(id=gid, exit_code=int(rc), rows_checked=checked,
                           rows_expected=expected, output_sha256=sha,
                           cmdline=list(argv), run_at=row['run_at'],
                           run_id=run_id, attest=row['attest'],
                           attest_denom=row['attest_denom']))
        except OSError as e:
            print('[WARN] 运行摘要写入失败（不影响本次验收）：%s' % e)
        print('[执行] %-8s exit=%-3s rows=%s/%s'
              % (gid, rc, '—' if checked is None else checked,
                 '—' if expected is None else expected))

    rec['run'] = dict(run_id=run_id, run_at=run_at, tier=tier,
                      tool=os.path.basename(SELF), version=VERSION,
                      planned=list(plan))
    _save(a.file, rec)

    print()
    if manual:
        print('[注意] 以下 %d 个门禁**未自动运行**，须人工跑并把参数补上后重跑 run：'
              % len(manual))
        for gid, why in manual:
            print('       %-8s %s' % (gid, why))
        print('       （它们现在记的是"未运行"，verify 会判 FAIL —— 这是对的）')
        print()
    print('== 最终验收（verify）==')
    return cmd_verify(argparse.Namespace(file=a.file, strict=bool(a.strict),
                                         skip_self=None))


def _parse_expect_rows(items):
    """`--expect-rows G-A=7` → {'G-A': 7}。只做显式声明，不做默认推断。"""
    out = {}
    for it in items or []:
        if '=' not in it:
            print('[WARN] --expect-rows %r 格式应为 ID=N，已忽略' % it)
            continue
        k, v = it.split('=', 1)
        try:
            out[k.strip()] = int(v)
        except ValueError:
            print('[WARN] --expect-rows %r 的 N 不是整数，已忽略' % it)
    return out


# ---------------------------------------------------------------------------
# record（R3：只能继承 run 摘要，禁止手填）
# ---------------------------------------------------------------------------
def cmd_record(a):
    if not os.path.exists(a.file):
        print('[CANNOT_RUN] 凭证不存在：%s（先跑 run）' % a.file)
        return 2
    if not a.attest_from:
        print('[FAIL] 凭证只能由 run 生成，禁止手工填写。')
        print('       正确做法：python %s run --file %s --tier <档位>'
              % (os.path.join('scripts', os.path.basename(SELF)), a.file))
        print('       若要把既有运行记录回填，须带 --attest-from <run_id>'
              '（run 会打印该 id，摘要存同目录 %s/<run_id>/）' % LOG_DIR)
        return 1

    try:
        rec = _load(a.file)
    except Exception as e:
        print('[CANNOT_RUN] 凭证损坏：%s' % e)
        return 2
    meta = rec.get('run') or {}
    if meta.get('run_id') != a.attest_from:
        print('[FAIL] --attest-from=%s 与凭证里的 run_id=%r 不一致 —— '
              '不能把别的运行结果贴进来' % (a.attest_from, meta.get('run_id')))
        return 1

    lp = _log_path(a.file, a.attest_from, a.id)
    if not os.path.exists(lp):
        print('[FAIL] %s 本次没有真实运行记录（%s 不存在）—— 不得手工填写'
              % (a.id, lp))
        return 1
    try:
        sm = _load(lp)
    except Exception as e:
        print('[CANNOT_RUN] 运行摘要损坏：%s' % e)
        return 2
    try:
        key, key_src = _load_key(a.file)
    except RuntimeError as e:
        print('[CANNOT_RUN] %s' % e)
        return 2
    if key is None:
        print('[CANNOT_RUN] 找不到 HMAC 密钥（$GATE_RUN_KEY 或 %s）—— '
              '无法核验运行摘要来源' % _home_key_path())
        return 2
    if key_src not in TRUSTED_KEY_SOURCES:
        print('[FAIL] 密钥来源不可信：%s —— 任何能写凭证的手也能写这个密钥，'
              '运行摘要无法证明来自真实运行。请改用 $GATE_RUN_KEY 或 %s'
              % (key_src, _home_key_path()))
        return 1
    want = _sign(key, a.id, sm.get('exit_code'), sm.get('rows_checked'),
                 sm.get('output_sha256'))
    if not hmac.compare_digest(str(sm.get('attest') or ''), want):
        print('[FAIL] 运行摘要 %s 的签名不匹配（疑似手改）—— 重跑 run' % lp)
        return 1

    if a.rows is not None and a.rows != sm.get('rows_checked'):
        print('[FAIL] --rows=%s 与运行记录 %r 不一致：记录值只能来自 run'
              % (a.rows, sm.get('rows_checked')))
        return 1
    if a.rows_expected is not None and a.rows_expected != sm.get('rows_expected'):
        print('[FAIL] --expect-rows=%s 与运行记录 %r 不一致：分母只能来自门禁输出'
              % (a.rows_expected, sm.get('rows_expected')))
        return 1

    row = _find(rec.get('gates') or [], a.id)
    if row is None:
        print('[FAIL] 凭证中没有门禁 %r —— 不在本档位必跑清单内？' % a.id)
        return 1
    want_denom = _sign_denom(key, a.id, sm.get('rows_expected'))
    if not hmac.compare_digest(str(sm.get('attest_denom') or ''), want_denom):
        print('[FAIL] 运行摘要 %s 的分母签名不匹配（疑似手改）—— 重跑 run' % lp)
        return 1
    row.update(exit_code=sm['exit_code'], rows_checked=sm.get('rows_checked'),
               rows_expected=sm.get('rows_expected'),
               output_sha256=sm.get('output_sha256'), cmdline=sm.get('cmdline'),
               run_at=sm.get('run_at'), run_id=a.attest_from,
               manual_required=False, manual_reason=None, attest=want,
               attest_denom=want_denom)
    _save(a.file, rec)
    print('[OK] %s 已从运行记录回填：exit=%s rows=%s/%s'
          % (a.id, row['exit_code'], row['rows_checked'],
             '?' if row['rows_expected'] is None else row['rows_expected']))
    return 0


# ---------------------------------------------------------------------------
# verify（R2 对账 + R3 分母 + R4 类型/签名）
# ---------------------------------------------------------------------------
def cmd_verify(a):
    path = a.file
    if not os.path.exists(path):
        print('[CANNOT_RUN] 凭证不存在：%s' % path)
        print('            **没有凭证 = 门禁没跑，不等于通过。**')
        return 2
    try:
        rec = _load(path)
    except Exception as e:
        print('[CANNOT_RUN] 凭证损坏：%s' % e)
        return 2
    if not isinstance(rec, dict):
        print('[CANNOT_RUN] 凭证顶层必须是对象')
        return 2

    tier = rec.get('tier')
    if tier not in TIER_GATES:
        print('[CANNOT_RUN] 凭证档位 %r 不在 TIER_GATES %s —— '
              '无法确定必跑清单，不得判通过' % (tier, list(TIER_GATES)))
        return 2
    expected_ids = [g for g, _, _ in TIER_GATES[tier]]

    gates = rec.get('gates')
    if not isinstance(gates, list) or not gates:
        print('[CANNOT_RUN] 凭证里没有任何门禁条目')
        return 2

    try:
        key, key_src = _load_key(path)
    except RuntimeError as e:
        print('[CANNOT_RUN] %s' % e)
        return 2
    any_filled = any(isinstance(g, dict) and g.get('exit_code') is not None
                     for g in gates)
    if any_filled and key is None:
        print('[CANNOT_RUN] 凭证里有执行记录，但找不到 HMAC 密钥'
              '（$GATE_RUN_KEY 或 ~/.caogong/gate_run_key）—— '
              '无法校验签名，凭证来源不可信')
        return 2
    # ★ 密钥与凭证同目录 = 没有防护。任何能写凭证的手也能写密钥并重算签名，
    #   实测可用"自造密钥 + 自签 + 假日志"让 verify 返回 0。
    #   故此处**判 FAIL 而非 WARN**：WARN 会被无视，而这是"凭证可能整份伪造"的
    #   定性问题。迁移办法在提示里给出（GATE_RUN_KEY 或 ~/.caogong/gate_run_key）。
    key_untrusted = None
    if any_filled and key is not None and key_src not in TRUSTED_KEY_SOURCES:
        key_untrusted = (
            '密钥来源不可信：密钥与凭证同目录（%s），任何能写凭证的手也能写密钥'
            '并重算签名 —— 本凭证无法证明"真的跑过"。'
            '迁移：把密钥移到 $GATE_RUN_KEY 或 ~/.caogong/gate_run_key 后重跑 run。'
            % os.path.join(os.path.dirname(os.path.abspath(path)), KEY_NAME))

    skipped = None
    skip = getattr(a, 'skip_self', None)
    if skip:
        if skip != 'G-RUN':
            print('[CANNOT_RUN] --skip-self 只接受 G-RUN（本次调用自身）')
            return 2
        row = _find(gates, skip)
        if row is None or row.get('exit_code') is not None:
            print('[CANNOT_RUN] --skip-self 仅用于 G-RUN 尚未记录时；'
                  '已有 G-RUN 记录不得跳过（不许用它掩盖自证失败）')
            return 2
        skipped = skip
        expected_ids = [g for g in expected_ids if g != skip]

    fails = []
    warns = []

    # ---- 密钥可信度：同目录密钥一律判 FAIL（不是 WARN，见上方注释）----
    if key_untrusted:
        fails.append(key_untrusted)

    # ---- R2 ①：required 条目对账（逐一点名）----
    present = set()
    for g in gates:
        if isinstance(g, dict) and g.get('required', True) is not False:
            present.add(g.get('id'))
    missing = [gid for gid in expected_ids if gid not in present]
    for gid in missing:
        fails.append('required 门禁缺失：%s（「%s」档位必跑，凭证里没有/被标 required=false）'
                     % (gid, tier))

    n_pass = n_fail = n_notrun = n_zero = 0
    print('== 门禁执行凭证验收 ==')
    print('   项目：%s   档位：%s'
          % (rec.get('project'), tier if tier else '?'))
    if rec.get('run'):
        print('   运行：run_id=%s  run_at=%s  工具版本=%s'
              % (str(rec['run'].get('run_id'))[:8],
                 rec['run'].get('run_at'), rec['run'].get('version')))
    if skipped:
        print('   [说明] %s 自身由本次 verify 承担，本次不计入通过数' % skipped)
    if getattr(a, 'recheck', False):
        # HMAC 只能证明"签发后没被改过"，不能证明"真的跑过"——密钥就在
        # 凭证同目录，同一只手能造凭证也能造密钥。--recheck 用记录的
        # cmdline 当场重跑一遍，把"确实跑得出这个退出码与行数"重新证一次。
        print('   [复算] 已开 --recheck：将按记录的 cmdline 重跑各门禁'
              '（输出哈希因含时间戳必定不同，故只比对退出码与检查行数）')
    print()
    print('   %-10s %-6s %-12s %-14s %s'
          % ('门禁', '退出码', '检查行数', '状态', '说明'))
    print('   ' + '-' * 76)

    for g in gates:
        if not isinstance(g, dict):
            fails.append('凭证里有一行不是对象（疑似手改）')
            continue
        gid = g.get('id', '?')
        if skipped and gid == skipped:
            continue
        ec = g.get('exit_code')
        rc = g.get('rows_checked')
        rexp = g.get('rows_expected')
        status = ''
        note = str(g.get('note', ''))[:28]

        # ---- R2 ③：清单外的自造 id 不计入通过数 ----
        if gid not in expected_ids:
            status = '非清单'
            warns.append('%s 非清单内门禁，不计入通过数（「%s」档位没有它）'
                         % (gid, tier))
            print('   %-10s %-6s %-12s %-14s %s'
                  % (gid, '—' if ec is None else ec, '—', status, note))
            continue

        if g.get('manual_required') and ec is None:
            note = ('需人工提供参数：%s' % (g.get('manual_reason') or ''))[:28]

        # ---- R4 类型断言 ----
        type_bad = []
        if ec is not None and not _type_ok_int(ec):
            type_bad.append('exit_code=%r 类型不符（须 int，布尔/字符串不算）' % (ec,))
        if rc is not None and not (_type_ok_int(rc) and rc >= 0):
            type_bad.append('rows_checked=%r 类型不符或为负（须 int>=0）' % (rc,))
        if rexp is not None and not (_type_ok_int(rexp) and rexp >= 0):
            type_bad.append('rows_expected=%r 类型不符或为负（须 int>=0 或 null）' % (rexp,))
        if type_bad:
            status = '类型不符'
            n_fail += 1
            fails.append('%s %s' % (gid, '；'.join(type_bad)))
        elif key_untrusted and (ec is not None or rc is not None):
            # 同目录密钥下签名"校验通过"是假象：签名由同一只手签发。
            # 必须显式标成「签名不可信」，不能让表格里出现 PASS 误导读者。
            status = '签名不可信'
            n_fail += 1
        elif key is not None and (ec is not None or rc is not None):
            # ---- R4 签名：手改任何字段都会对不上 ----
            # 只在"这一行确实写了执行结果"时才验证签名：从没跑过的行本来
            # 就没有签名，"缺签名"会把「未运行」误诊成「疑似手改」，
            # 让真正该补跑的项看起来像被篡改，反而模糊了处置动作。
            att = g.get('attest')
            want = _sign(key, gid, ec, rc, g.get('output_sha256'))
            att_d = g.get('attest_denom')
            want_d = _sign_denom(key, gid, rexp)
            if not att or not att_d:
                status = '无签名'
                n_fail += 1
                fails.append('%s 凭证与运行记录不一致（疑似手改）：缺 attest 签名' % gid)
            elif not hmac.compare_digest(str(att), want):
                status = '签名不符'
                n_fail += 1
                fails.append('%s 凭证与运行记录不一致（疑似手改）：签名/输出哈希对不上'
                             '—— exit_code / rows_checked / output_sha256 中有字段被改过'
                             % gid)
            elif not hmac.compare_digest(str(att_d), want_d):
                status = '签名不符'
                n_fail += 1
                fails.append('%s 凭证与运行记录不一致（疑似手改）：分母签名对不上 —— '
                             'rows_expected 被改过（调小分母即可把"分母缩水"洗成通过）'
                             % gid)

        if not status:
            # 三态要分清：ec is None = 从没跑；ec==2 = 门禁自己说"跑不了"；
            # ec!=0 = 跑了但没过。三者处置动作不同，不能混成一句"未运行"。
            if ec is None:
                status = '未运行'
                n_notrun += 1
                fails.append('%s 必跑但未记录（未运行 ≠ 通过）' % gid)
            elif ec == 2:
                status = 'CANNOT_RUN'
                n_notrun += 1
                fails.append('%s exit_code=2 —— 门禁**未能执行**（不是"检查不通过"，'
                             '也不是"通过"），须补参数/补数据后重跑' % gid)
            elif ec != 0:
                status = 'FAIL'
                n_fail += 1
                fails.append('%s exit_code=%s' % (gid, ec))
            elif rc is None:
                # 跑了且 exit=0，但工具没打印任何行数 —— 无法证明"检了东西"。
                # 零行判据与分母判据都无从适用，只能报"未取得行数"，不判 PASS。
                status = '无行数'
                n_fail += 1
                fails.append('%s 退出码 0 但未取得检查行数（工具未打印总数）—— '
                             '无法证明不是空转，须人工核对或改用 --expect-rows 声明' % gid)
            elif rc == 0:
                status = '零行'
                n_zero += 1
                fails.append('%s 检查行数=0 —— 等于没检' % gid)
            elif rexp is not None and rc < rexp:
                status = '分母缩水'
                n_fail += 1
                fails.append('%s 只检 %s/%s 行，剩余判据未被验证' % (gid, rc, rexp))
            elif rexp is None:
                # ---- R3：分母缺失不得静默 PASS ----
                status = 'PASS*'
                n_pass += 1
                warns.append('%s 未声明分母，分母缩水判据未生效（rows_checked=%s，'
                             '门禁未打印总量；确知分母时用 run --expect-rows %s=N 声明）'
                             % (gid, rc, gid))
            else:
                status = 'PASS'
                n_pass += 1

        # ---- 复算：按记录的 cmdline 当场重跑，比对退出码与行数 ----
        recheck_line = None
        if status in ('PASS', 'PASS*') and getattr(a, 'recheck', False):
            argv = g.get('cmdline')
            if not isinstance(argv, list) or not argv:
                status = '无法复算'
                n_fail += 1
                fails.append('%s --recheck 但凭证里没有 cmdline，无法复算' % gid)
            else:
                rc2, out2 = _exec(argv, os.path.dirname(os.path.abspath(path)),
                                  getattr(a, 'timeout', 240) or 240)
                c2, e2 = _extract(gid, out2)
                if rc2 != ec or (c2 is not None and rc is not None and c2 != rc):
                    status = '复算不符'
                    n_fail += 1
                    fails.append('%s 复算不符：凭证记 exit=%s rows=%s，重跑得 exit=%s rows=%s'
                                 ' —— 凭证与真实运行不一致'
                                 % (gid, ec, rc, rc2, c2))
                elif rc2 != 0:
                    status = '复算FAIL'
                    n_fail += 1
                    fails.append('%s 复算 exit=%s（凭证记 0）' % (gid, rc2))
                else:
                    recheck_line = ('   [复算] %-10s exit=%s rows=%s 与凭证一致'
                                    % (gid, rc2, '—' if c2 is None else c2))

        print('   %-10s %-6s %-12s %-14s %s'
              % (gid, '—' if ec is None else ec,
                 '—' if rc is None else '%s/%s'
                 % (rc, '?' if rexp is None else rexp),
                 status, note))
        if recheck_line:
            print(recheck_line)

    print()
    print('== 汇总（只能写这三栏）==')
    print('   通过 %d 项 / 未运行 %d 项 / 待确认 %d 项'
          % (n_pass, n_notrun, n_fail + n_zero))
    print()

    if a.strict:
        # 严格模式：任何非 PASS 都阻断；WARN 升级为 FAIL（既有逻辑保持）
        for w in warns:
            fails.append('[strict] %s' % w)
        warns = []
        bad = [g['id'] for g in gates
               if isinstance(g, dict)
               and (g.get('exit_code') != 0 or not g.get('rows_checked'))]
        if bad:
            print('[FAIL] strict 模式：以下门禁未干净通过：%s' % ', '.join(bad))

    if fails:
        for f in fails:
            print('[FAIL] %s' % f)
        print()
        print('**不得以"全部门禁通过"表述交付。** 未运行项须补跑或显式声明未审范围。')
        return 1

    for w in warns:
        print('[WARN] %s' % w)
    if warns:
        print('[PASS*] 凭证完整、签名有效、与清单一致；但上列 %d 条 WARN 未清 —— '
              '交付物须逐条说明其影响范围' % len(warns))
    else:
        print('[PASS] 凭证完整：全部必跑门禁已执行、非零行、分母未缩水、签名有效')
    print('       （本结论仅覆盖凭证中登记的门禁；未登记维度仍须在交付物里'
          '显式声明"未审及原因"）')
    return 0


def main():
    ap = argparse.ArgumentParser(description='门禁执行凭证 G-RUN',
                                 allow_abbrev=False)
    sub = ap.add_subparsers(dest='cmd')

    p1 = sub.add_parser('init', help='生成凭证模板')
    p1.add_argument('--root', default='.')
    p1.add_argument('--project')
    p1.add_argument('--tier', default='中档', choices=list(TIER_GATES))
    p1.add_argument('--out')
    p1.set_defaults(func=cmd_init)

    p0 = sub.add_parser('run', help='真跑该档位门禁并留痕（唯一能写 exit_code 的入口）')
    p0.add_argument('--file', required=True)
    p0.add_argument('--tier', choices=list(TIER_GATES))
    p0.add_argument('--root', default='.')
    p0.add_argument('--only', help='只跑指定 id，逗号分隔，如 G-OFF,G-SRC')
    p0.add_argument('--timeout', type=int, default=240)
    p0.add_argument('--strict', action='store_true',
                    help='最终验收走 strict（WARN 升级为 FAIL）')
    p0.add_argument('--expect-rows', action='append', metavar='ID=N',
                    help='显式声明某门禁的分母（须来自人工核对的总量）')
    p0.add_argument('--xlsx')
    p0.add_argument('--sheet')
    p0.add_argument('--summary-xlsx', dest='summary_xlsx')
    p0.add_argument('--basis-freshness', dest='basis_freshness')
    p0.add_argument('--basis-drawing', dest='basis_drawing')
    p0.add_argument('--chain-spec', dest='chain_spec')
    p0.add_argument('--xd-spec', dest='xd_spec')
    p0.add_argument('--xs-map', dest='xs_map')
    p0.add_argument('--expect-json', dest='expect_json')
    p0.add_argument('--src-file', dest='src_file')
    p0.add_argument('--src-declare', dest='src_declare')
    p0.add_argument('--off-file', dest='off_file')
    p0.add_argument('--ack')
    p0.set_defaults(func=cmd_run)

    p2 = sub.add_parser('record', help='从 run 摘要回填一条记录（禁止手填数值）')
    p2.add_argument('--file', required=True)
    p2.add_argument('--id', required=True)
    p2.add_argument('--attest-from', dest='attest_from', metavar='RUN_ID',
                    help='run 打印的 run_id；记录值一律从该次运行摘要继承')
    p2.add_argument('--rows', type=int,
                    help='仅用于与运行摘要核对，不一致即拒绝')
    p2.add_argument('--expect-rows', type=int, dest='rows_expected',
                    help='仅用于与运行摘要核对，不一致即拒绝')
    p2.add_argument('--cmd')
    p2.set_defaults(func=cmd_record)

    p3 = sub.add_parser('verify', help='交付前验收凭证')
    p3.add_argument('--file', required=True)
    p3.add_argument('--strict', action='store_true')
    p3.add_argument('--skip-self', dest='skip_self', metavar='G-RUN',
                    help='仅供 run 内部使用：G-RUN 尚未记录时跳过自证项')
    p3.add_argument('--recheck', action='store_true',
                    help='按凭证里的 cmdline 当场重跑各门禁，复核退出码与行数'
                         '（HMAC 只证明"签发后没被改"，--recheck 才证明"真的跑过"）')
    p3.add_argument('--timeout', type=int, default=240,
                    help='--recheck 时单条门禁的超时秒数')
    p3.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    # 注意：Python 3.13 下 dest='cmd' 在某些参数顺序下可能为 None，
    # 而 func 已正确绑定。判据以 func 为准，cmd 仅作日志。
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
