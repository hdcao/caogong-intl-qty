#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ACK 台账：把「人工已确认」的门禁 WARN 正式留痕（1.2.1 新增）

**为什么需要它**
gate_unit_consistency 的 G-U2 与 gate_single_owner 的 G-OWN3 都是
**WARN 不 FAIL** 的设计——它们摆出信号，由人判。问题是：人判完之后，
下一轮重跑**同样的 WARN 又出现了**。于是只有三条路：

1. 把判据放宽/删掉 → 真错也一起放过去了（最坏）
2. 每轮人工重看一遍 → 实际做不到，看着看着就不看了
3. **把"已确认"写成一个可复算的台账条目** ← 本模块

★这条设计针对的是本项目已记录在案的失败模式：
「人一旦见过假红，就会开始无视红灯。」
消除假红不能靠让门禁闭嘴，只能靠**把人的判断变成有指纹、会被反查的条目**。

**指纹（expect）为什么要**
只按 (gate, table, sheet, row) 认账，会出现「ack 完这一行，量价被人悄悄改了，ack 还挂着」——
比不 ack 更危险，因为它给了一个"已经看过了"的假安心。
所以每条 ack 必须记录**ack 当时的数值指纹**；重跑时指纹不符就报 ACK-STALE
并**照常计 WARN**，而不是静默放过。

**反查（unused）为什么**
行被删了、判据不再触发、或者行号错位，ack 条目就该失效。
不去反查，台账会一直积累僵尸条目，最后没人知道哪条还有效。

**table 列为什么必须存在**
同一个项目里 B3/B4/E3 三张表的页签名都叫「1_成本取量总表」、行号也都从 3 起。
只用 (gate, sheet, row) 做键，B3 的 r14 和 E3 的 r14 会撞成一条——
一个 ack 悄悄替另一张表的行背书，这是最难发现的那类错。

CSV 列：gate,table,sheet,row,expect,reason,date,by
  gate    门禁项，如 G-U2 / G-OWN3
  table   工作簿文件名（**精确匹配**，不做子串匹配——子串会让 'E3' 同时命中两张 E3 表）
  sheet   页签（同一表可能有多页签，行号只在页签内唯一）
  row     行号；成对判据写 '59+60'（升序，加号连接）；
         G-XST 写 'expect:<期望项id>:<父行号>+<子行号>'（一对父子一条，各自独立）
  expect  数值指纹，如 'qty=87360.00|price=200.00'
  reason  人判断的理由——**必须指向可核查的依据**，不是"没问题"
  date    YYYY-MM-DD
  by      确认人/岗位
"""
import csv
import io
import os

HEADER = ['gate', 'table', 'sheet', 'row', 'expect', 'reason', 'date', 'by']

MIN_REASON = 8          # 少于这个长度的理由不构成可核查依据


def norm_rowkey(rowkey):
    """'60+59' → '59+60'，保证成对键顺序无关。

    ★v2.1.2：G-XST 的 ack 键形如 'expect:<id>:<r1>+<r2>'——冒号前缀
    不是数字，整体 int() 会崩（ValueError）。只排序**末段行对**，
    前缀原样保留；末段不是纯数字段时整个键原样返回。
    """
    s = str(rowkey).strip()
    if '+' not in s:
        return s
    prefix, sep, tail = s.rpartition(':')
    parts = tail.split('+')
    if not all(p.strip().isdigit() for p in parts):
        return s
    ordered = '+'.join(str(x) for x in sorted(int(p) for p in parts))
    return ('%s:%s' % (prefix, ordered)) if sep else ordered


class AckLedger(object):
    """读入 ack 台账；逐条匹配；结束时反查本表未用条目。"""

    def __init__(self, path=None, verbose=True):
        self.path = path
        self.verbose = verbose
        self.entries = []      # 全部条目
        self._used = set()     # 已命中的 (gate, table, sheet, row)
        self.stale = []        # 台账自身不合法（缺列/理由太短/指纹不符的登记）
        if path and os.path.isfile(path):
            self._load(path)
        elif path:
            self.stale.append('ACK 台账文件不存在：%s（本次按"无 ack"处理）' % path)

    def _load(self, path):
        with io.open(path, encoding='utf-8-sig', newline='') as f:
            rd = csv.DictReader(f)
            miss = [c for c in HEADER if c not in (rd.fieldnames or [])]
            if miss:
                self.stale.append('ACK 台账缺列 %s，整份台账按无效处理'
                                  % '、'.join(miss))
                return
            for i, d in enumerate(rd, 2):
                d = {k: str(v or '').strip() for k, v in d.items()}
                if not d['gate'] or not d['row'] or not d['table']:
                    continue
                d['row'] = norm_rowkey(d['row'])
                d['_line'] = i
                if len(d['reason']) < MIN_REASON:
                    # ★r 前缀仅对行号键加（v2.1.2 P2-6）：'expect:6.36:17+18'
                    #   这类非数字行键直接打原值——'rexpect:...' 是错文案。
                    rk = d['row']
                    label = ('r%s' % rk) if rk[:1].isdigit() else rk
                    self.stale.append(
                        '第 %d 行（%s %s）reason 过短（< %d 字），'
                        '不构成可核查理由，该条忽略'
                        % (i, d['gate'], label, MIN_REASON))
                    continue
                self.entries.append(d)

    def hit(self, gate, table, sheet, rowkey, expect):
        """返回 (status, reason)。

        status: 'ACK'   —— 命中且指纹一致，本项不计 WARN
                'STALE' —— 有条目但指纹不符，**仍然计 WARN**，并额外告警
                ''      —— 无条目
        """
        rowkey = norm_rowkey(rowkey)
        for d in self.entries:
            if (d['gate'] != gate or d['table'] != table
                    or d['sheet'] != sheet or d['row'] != rowkey):
                continue
            self._used.add((gate, table, sheet, rowkey))
            if d['expect'] and expect and d['expect'] != expect:
                return 'STALE', ('台账第 %d 行记 %s，现值 %s——'
                                 '这一行被改过，ack 不能自动续期'
                                 % (d['_line'], d['expect'], expect))
            return 'ACK', d['reason']
        return '', ''

    def report(self, table=None, gates=None):
        """输出：① 台账自身不合法 ② 本表本门禁从未命中的条目（僵尸 ack）。

        ★必须按 gates 过滤：一份台账被多支门禁共用，若不过滤，
        gate_single_owner 会把 gate_unit_consistency 的 G-U2 条目报成僵尸——
        多出来的假红比漏报更快地教会人无视红灯。
        """
        lines = ['[WARN] ACK-BAD %s' % s for s in self.stale]
        hit_keys = set(self._used)
        for d in self.entries:
            if table is not None and d['table'] != table:
                continue
            if gates is not None and d['gate'] not in gates:
                continue
            k = (d['gate'], d['table'], d['sheet'], d['row'])
            if k not in hit_keys:
                # ★r 前缀仅对行号键加（v2.1.2 P2-6）：'expect:6.36:17+18'
                #   这类非数字行键直接打原值——'rexpect:...' 是错文案。
                rk = d['row']
                label = ('r%s' % rk) if rk[:1].isdigit() else rk
                lines.append(
                    '[WARN] ACK-STALE %s %s 的 ack 本次未触发——'
                    '行已改/已删/判据不再适用，请复核后删除该条目'
                    '（僵尸 ack 会让"已确认"失去含义）'
                    % (d['gate'], label))
        return lines

    @property
    def n(self):
        return len(self.entries)


def add_ack_arg(ap):
    """给门禁脚本挂上 --ack 参数（不传则全部 WARN 照常报出）。"""
    ap.add_argument('--ack', default=None,
                    help='ACK 台账 CSV（gate,table,sheet,row,expect,reason,date,by）。'
                         '只对**已人工确认且理由可核查**的条目放行；'
                         '不传则所有 WARN 照常报出')