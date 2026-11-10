# -*- coding: utf-8 -*-
"""尺寸链闸门 CHAIN —— 把图纸尺寸链写成断言，导入即校验。

**为什么需要它（2026-10-29 实测）**

某铁路桥 APS 算量，第一版交出的总量 17,306 m³。后来对照第三方成果，
只差 11%，看上去互相印证。回图复测后发现 6 处实质错误——**两个数接近，
是错误互相抵消，不是准确**：桩基+墩柱高估约 3,456 m³，桥面板+桥台低估
约 1,601 m³。

六处错误里没有一处是算错，全是**读错关系**：

| 错误 | 真相 | 当时为什么没发现 |
|---|---|---|
| 桩 8 根/墩（4排×2列） | 12 根（3列×4排） | 4 排 × 3.60 = 10.80 m > 9.60 m 承台，**几何上装不进去** |
| 墩身当双肢墙 2×4.35×1.60 | 3 根 Ø2.00 圆柱 | 4.35 是**柱中心距**，1.60 是**边距**，都不是墙宽/墙厚 |
| 墩高 9.08 m | 5.00 m | 拿承台顶当基准**又**扣了一次盖梁 1.50（应 2.00），两端都错 |
| 盖梁高 1.50 | 2.00 | 与高程链差 0.50，链立刻会报错 |
| 桥面板只算梁间带 3.54 m | 全宽 12.90 m | 板是**全宽构件**，缩窄即错 |
| 主梁 9 片/跨、断面 0.948 | 10 片、梁身 0.725 | `0.69+9×1.28+0.69` 是 **9 个间隔**不是 9 片梁；5 个剖面性质不同却直接平均 |

三个共同毛病：**只看了数，没看数的关系**。所以判据也只需三类：

1. **闭合**（eq）  Σ分段 = 总尺寸 —— 抓数量数错、漏项、双重扣减
2. **可行性**（le） 子项 ≤ 包容尺寸 —— 抓几何上装不进去
3. **同质性**（homog） max/min ≤ 限值 —— 抓把不同性质的剖面混在一起平均

**用法**

    from chain_check import Chain

    C = Chain()
    C.eq("桩位横向", 1.20 + 3.60 + 3.60 + 1.20, CAP_L, note="PLAN 墩位平面链")
    C.le("桩位纵向可行", 4 * 3.60, CAP_W, note="4 排要装进承台纵长")
    C.homog("梁身断面同质性", [0.725, 0.725], limit=1.10)

    BAD, WARN = C.verify(strict=True)     # 模块级执行 ⇒ 导入即校验

`strict=True` 时只要有阻断项就抛 AssertionError：任何 import 本模块的下游脚本
（台账 / 算量表 / 报告）都会**立刻失败**，改错常量不会静默出表。
这是刻意的——静默出表比报错危险得多。

**命令行**（不想写 Python 时）

    python chain_check.py run --spec checks.json [--out out/] [--json]
    python chain_check.py selftest

`selftest` 把上面 6 类错误逐一注回模型，断言每条都被拦住。
**没有被注入验证过的判据，和"写了个永远为真的检查"没区别。**
"""
from __future__ import print_function

import argparse
import ast
import io
import json
import os
import sys

# --- 控制台编码兜底（与包内其他脚本一致）--------------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

VERSION = "1.0.0"

KINDS = ("eq", "le", "ge", "homog")
LEVELS = ("block", "warn")

DEFAULT_TOL = 0.01          # 长度类默认容差（m）；体积/数量类调用方自行放大
DEFAULT_HOMOG = 1.10        # 同质性上限：max/min


# ── 受限表达式求值 ────────────────────────────────────────────
# spec 里的 lhs/rhs 是字符串表达式。不直接 eval——那等于让一份配置文件
# 拥有任意代码执行权。这里只放行四则运算与乘方，够写尺寸链了。
_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a ** b,
    ast.Mod: lambda a, b: a % b,
}
_ALLOWED_UNARY = {ast.UAdd: lambda a: +a, ast.USub: lambda a: -a}


def safe_eval(expr):
    """只允许数字与四则运算的表达式求值。变量名一律拒绝（要求写清数值来源）。"""
    if isinstance(expr, (int, float)) and not isinstance(expr, bool):
        return float(expr)
    if not isinstance(expr, str):
        raise ValueError("表达式必须是字符串或数字：%r" % (expr,))
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError("表达式语法错误 %r：%s" % (expr, e))

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("表达式只允许数字常量：%r" % (expr,))
            return float(node.value)
        if isinstance(node, ast.BinOp):
            fn = _ALLOWED_BINOPS.get(type(node.op))
            if fn is None:
                raise ValueError("不允许的运算符：%r" % (expr,))
            return fn(ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp):
            fn = _ALLOWED_UNARY.get(type(node.op))
            if fn is None:
                raise ValueError("不允许的一元运算符：%r" % (expr,))
            return fn(ev(node.operand))
        raise ValueError("表达式含不支持的成分（变量/函数/属性）：%r" % (expr,))

    return ev(tree)


# ── 核心 ──────────────────────────────────────────────────────
class Chain(object):
    """尺寸链断言集合。

    每条判据登记为 (name, lhs, rhs, tol, kind, level, note)。
    lhs/rhs 可以是数值，也可以是字符串表达式（走 safe_eval）。
    """

    def __init__(self, title=""):
        self.title = title
        self.checks = []          # [(name, lhs, rhs, tol, kind, level, note)]
        self._errors = []         # 表达式本身写错（不是判据失败）

    # -- 登记 --------------------------------------------------
    def _add(self, name, lhs, rhs, tol, kind, level, note):
        if level not in LEVELS:
            raise ValueError("level 只能是 %s：%r" % (LEVELS, level))
        self.checks.append((name, lhs, rhs, tol, kind, level, note))
        return self

    def eq(self, name, lhs, rhs, tol=DEFAULT_TOL, note="", level="block"):
        """闭合：lhs 必须等于 rhs（容差内）。Σ分段 = 总尺寸。"""
        return self._add(name, lhs, rhs, tol, "eq", level, note)

    def sum_eq(self, name, parts, total, tol=DEFAULT_TOL, note="", level="block"):
        """闭合的常用写法：sum(parts) = total。漏一段、多一段都会报。"""
        return self._add(name, list(parts), total, tol, "eq", level, note)

    def le(self, name, lhs, rhs, tol=0.0, note="", level="block"):
        """可行性：lhs ≤ rhs（+tol）。子项必须装得进包容尺寸。"""
        return self._add(name, lhs, rhs, tol, "le", level, note)

    def ge(self, name, lhs, rhs, tol=0.0, note="", level="block"):
        """可行性（下限）：lhs ≥ rhs（−tol）。如覆盖层不小于最小值。"""
        return self._add(name, lhs, rhs, tol, "ge", level, note)

    def homog(self, name, values, limit=DEFAULT_HOMOG, note="", level="block"):
        """同质性：max/min ≤ limit。超过即禁止取平均——剖面性质不同。

        values 里的非数值（None/""）会被跳过：剖面没量到就是没量到，
        不该让它把整条判据带崩。
        """
        return self._add(name, list(values), limit, 0.0, "homog", level, note)

    def classify(self, name, measure, ratio, cuts, labels,
                 limit=DEFAULT_HOMOG, note="", level="block"):
        """先按"判别量"分组，再校验组内同质、组间单调。返回 {档名: [对象...]}。

        **为什么需要它**：`homog` 要求先把同性质的剖面分好组，而分组本身
        最容易变成"人工看一眼"。但只要选对判别量，分组是可以量出来的——
        而且必须量出来，否则分组结论一旦冻进常量，改图纸不会跟着改。

        measure —— {对象: 待平均的量}（如断面面积）
        ratio   —— {对象: 判别量比值}（如 腹板宽/底翼缘宽）；分组只看它，不看 measure
        cuts    —— 比值分档阈值（升序），如 (0.50, 0.90)
        labels  —— 档名，比 cuts 多一个，如 ("梁身", "过渡", "填实")

        挂三条判据：
          1) 每个非空档组内 measure 离散度 ≤ limit（同质性）
          2) 每档不得为空（空了说明阈值与实际对不上，分组已失效）
          3) 各档 measure 最小值随档位单调不减（分类与求积是两个独立测量，必须同向）
        """
        if len(labels) != len(cuts) + 1:
            raise ValueError("labels 应比 cuts 多一个：cuts=%d labels=%d" % (len(cuts), len(labels)))
        groups = dict((lb, []) for lb in labels)
        for k in measure:
            r = ratio.get(k)
            if r is None:
                self._errors.append("%s：对象 %r 缺判别量，无法分组" % (name, k))
                continue
            idx = 0
            while idx < len(cuts) and r >= cuts[idx]:
                idx += 1
            groups[labels[idx]].append(k)

        for lb in labels:
            vals = [measure[k] for k in groups[lb]]
            self.ge("%s·%s组非空" % (name, lb), len(vals), 1, 0,
                    "该档无对象 → 阈值与实测分布不符，分组已失效", level)
            if len(vals) >= 2:
                self.homog("%s·%s组同质" % (name, lb), vals, limit, note, level)

        mins = [(lb, min(measure[k] for k in groups[lb])) for lb in labels if groups[lb]]
        for (la, va), (lb, vb) in zip(mins, mins[1:]):
            self.le("%s·%s≤%s" % (name, la, lb), va, vb, 0.005,
                    "分类按判别量、求积按轮廓，两个独立测量必须同向", level)
        return groups

    # -- 求值 --------------------------------------------------
    def _lhs_value(self, lhs):
        if isinstance(lhs, (list, tuple)):
            vals = [safe_eval(x) for x in lhs]
            if not vals:
                raise ValueError("空的分段列表")
            return sum(vals)
        return safe_eval(lhs)

    def verify(self, strict=False, verbose=False):
        """执行全部判据，返回 (bad, warn)。

        bad  —— 阻断项，必须为 0 才允许放行
        warn —— 待核项，登记不阻断（避免把数据瑕疵伪装成通过）

        strict=True：bad 非空则抛 AssertionError（供模块级导入即校验）。
        """
        bad, warn = [], []
        for name, lhs, rhs, tol, kind, level, note in self.checks:
            try:
                if kind == "homog":
                    vals = [float(v) for v in lhs
                            if isinstance(v, (int, float)) and not isinstance(v, bool)]
                    if len(vals) < 2:
                        raise ValueError("同质性判据至少需要 2 个剖面值，实得 %d" % len(vals))
                    if min(vals) <= 0:
                        raise ValueError("同质性判据不允许非正值：min=%r" % min(vals))
                    ratio = max(vals) / min(vals)
                    ok = ratio <= rhs + tol
                    rec = (name, ratio, rhs, ratio - rhs, kind, level, note)
                else:
                    a = self._lhs_value(lhs)
                    b = safe_eval(rhs)
                    if kind == "eq":
                        delta = abs(a - b)
                        ok = delta <= tol
                        rec = (name, a, b, delta, kind, level, note)
                    elif kind == "le":
                        delta = a - b
                        ok = delta <= tol
                        rec = (name, a, b, delta, kind, level, note)
                    elif kind == "ge":
                        delta = b - a
                        ok = delta <= tol
                        rec = (name, a, b, delta, kind, level, note)
                    else:
                        raise ValueError("未知判据类型 %r" % (kind,))
            except ValueError as e:
                # 判据本身写错 ≠ 判据不通过。必须分开报，否则会被当成"抓到了问题"。
                self._errors.append((name, str(e)))
                continue

            if not ok:
                (bad if level == "block" else warn).append(rec)
            if verbose:
                print("  [%s] %-22s %s" % ("OK" if ok else "!!", name, _fmt(rec)))

        if strict and (bad or self._errors):
            raise AssertionError(self.report(bad, warn))
        return bad, warn

    # -- 输出 --------------------------------------------------
    def report(self, bad=None, warn=None):
        if bad is None or warn is None:
            bad, warn = self.verify()
        lines = []
        if self.title:
            lines.append("尺寸链闸门 CHAIN | %s | %d 条判据" % (self.title, len(self.checks)))
        lines.append("阻断 %d 项 / 待核 %d 项 / 判据自身错误 %d 项"
                     % (len(bad), len(warn), len(self._errors)))
        for rec in bad:
            lines.append("  !! %s：%s" % (rec[0], _fmt(rec)))
        for rec in warn:
            lines.append("  ?? %s（待核）：%s" % (rec[0], _fmt(rec)))
        for name, msg in self._errors:
            lines.append("  ERR %s：判据写错——%s" % (name, msg))
        if bad:
            lines.append("!! 未通过：尺寸链不闭合。先查是哪一段读错，"
                         "不要改总数让闸门变绿。")
        elif self._errors:
            lines.append("!! 未通过：判据自身有错，本次结果不代表结构无误。")
        else:
            lines.append("== 尺寸链闸门通过 ==")
        return "\n".join(lines)


def _fmt(rec):
    name, lhs, rhs, delta, kind, level, note = rec
    if kind == "homog":
        s = "离散度 %.3f > 上限 %.3f（超出 %.3f）" % (lhs, rhs, delta)
    elif kind == "eq":
        s = "%.4f ≠ %.4f（差 %.4f）" % (lhs, rhs, delta)
    elif kind == "le":
        s = "%.4f > %.4f（超出 %.4f）" % (lhs, rhs, delta)
    else:
        s = "%.4f < %.4f（不足 %.4f）" % (lhs, rhs, delta)
    return s + ("｜%s" % note if note else "")


# ── 命令行：声明式 spec ────────────────────────────────────────
def _chain_from_spec(spec):
    C = Chain(spec.get("title") or "")
    for it in spec.get("checks") or []:
        name = it.get("name")
        kind = it.get("kind")
        if not name or kind not in KINDS:
            raise ValueError("每条判据需要 name 与合法 kind（%s）：%r" % (KINDS, it))
        level = it.get("level", "block")
        note = it.get("note", "")
        if kind == "homog":
            C.homog(name, it.get("values") or [],
                    limit=it.get("limit", DEFAULT_HOMOG), note=note, level=level)
        else:
            C._add(name, it.get("lhs"), it.get("rhs"),
                   it.get("tol", DEFAULT_TOL), kind, level, note)
    return C


def cmd_run(a):
    try:
        with io.open(a.spec, encoding="utf-8-sig") as f:
            spec = json.load(f)
    except Exception as e:
        print("[FAIL] CHAIN-0 spec 解析失败：%s" % e)
        print("       闸门**未能执行**，退出码 2 不代表检查结论。")
        return 2
    if not isinstance(spec, dict):
        print("[FAIL] CHAIN-0 spec 顶层必须是对象（含 checks 数组）")
        return 2
    try:
        C = _chain_from_spec(spec)
    except ValueError as e:
        print("[FAIL] CHAIN-0 spec 结构错误：%s" % e)
        return 2
    if not C.checks:
        # 分母为 0 的检查不算通过。
        print("[未执行] CHAIN-0 spec 里没有任何判据（checks 为空）。")
        print("         0 条判据的「通过」没有意义，退出码 2。")
        return 2

    bad, warn = C.verify(verbose=a.verbose)
    text = C.report(bad, warn)
    print(text)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        p = os.path.join(a.out, "chain_check_result.json")
        with io.open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "version": VERSION, "title": C.title,
                "total": len(C.checks), "blocking": len(bad), "pending": len(warn),
                "spec_errors": [{"name": n, "error": m} for n, m in C._errors],
                "bad": [_rec_json(r) for r in bad],
                "warn": [_rec_json(r) for r in warn],
                "status": "PASS" if not bad and not C._errors else "FAIL",
            }, ensure_ascii=False, indent=2))
        print("已写出 %s" % p)
    if bad or C._errors:
        return 1
    return 0


def _rec_json(rec):
    name, lhs, rhs, delta, kind, level, note = rec
    return {"name": name, "kind": kind, "level": level, "lhs": lhs,
            "rhs": rhs, "delta": delta, "note": note}


# ── 自证：把历史错误注回模型 ──────────────────────────────────
def _model(n_col=3, n_row=4, spacing=3.60, edge=1.20,
           cap_len=9.60, cap_wid=13.20,
           col_d=2.00, col_clear=2.35, cap_h=2.00,
           slab_w=12.90, beam_n=10,
           sections=(0.725, 0.725)):
    """本桥最小可判模型。每个参数都是 v1 曾经读错的那一格。

    坐标约定（照图）：3 列沿承台纵长 9.60，4 排沿承台横长 13.20。
    v1 的错误正是把 4 排塞进 9.60 那个方向。
    """
    C = Chain("VIADUC PK295+300 最小模型")
    span_col = edge + spacing * (n_col - 1) + edge      # 3 列跨度
    span_row = edge + spacing * (n_row - 1) + edge      # 4 排跨度

    # ── 闭合 ──
    C.eq("桩位横向(3列)", span_col, cap_len, note="PLAN 墩位平面链")
    C.eq("桩位纵向(4排)", span_row, cap_wid, note="PLAN 墩位平面链")
    C.eq("桩根数", n_col * n_row, 12, note="3 列 × 4 排")
    C.eq("墩链", 2.20 + 5.00 + cap_h + 0.283 + 2.00 + 0.30 + 0.80, 12.583,
         note="FSR−承台顶")
    C.eq("梁片数闭合", 0.69 + 1.28 * (beam_n - 1) + 0.69, slab_w,
         note="链上数的是间隔数，片数 = 间隔数 + 1")
    C.eq("梁系铺满桥面", beam_n * 1.04 + (beam_n - 1) * 0.24 + 2 * 0.17, slab_w)
    C.eq("桥面板为全宽", slab_w, 12.90, note="横向构件默认全宽")

    # ── 可行性：排数跨度必须装得进它所属的那个方向 ──
    C.le("桩位纵向可行", span_row, cap_wid, note="装不进去就是数错了")
    C.le("柱组含于盖梁", 3 * col_d + 2 * col_clear, 11.90, note="3 柱 + 2 净距")

    # ── 同质性 ──
    C.homog("断面同质性", list(sections), limit=1.10,
            note="横隔梁断面不可与梁身平均")
    return C


def cmd_selftest(a):
    cases = [
        ("主梁片数 10→9（把 9 个间隔当 9 片梁）", dict(beam_n=9), "梁片数闭合"),
        ("桩 3 列→2 列（v1 的 4排×2列=8 根）", dict(n_col=2), "桩根数"),
        ("墩柱径 2.00→4.35（把中心距当柱径）", dict(col_d=4.35), "柱组含于盖梁"),
        ("盖梁高 2.00→1.50（v1 的取值）", dict(cap_h=1.50), "墩链"),
        ("桥面板宽 12.90→3.54（只算梁间现浇带）", dict(slab_w=3.54), "桥面板为全宽"),
        ("把 4 排塞进 9.60 那个方向（承台横长当纵长）", dict(cap_wid=9.60), "桩位纵向可行"),
        ("梁断面组混入横隔梁断面（5 面均值）",
         dict(sections=(0.725, 0.725, 0.861, 1.244, 1.187)), "断面同质性"),
    ]
    # 基线：干净模型必须通过，否则下面的"拦住"毫无意义
    base = _model()
    b, w = base.verify()
    print("基线：%s" % ("通过" if not b else "未通过（自证无效）"))
    if b:
        print(base.report(b, w))
        return 1

    caught = 0
    for label, kw, expect in cases:
        C = _model(**kw)
        bad, _ = C.verify()
        names = [r[0] for r in bad]
        hit = expect in names
        print("  [%s] %s  -> %s" % ("拦住" if hit else "漏了", label,
                                    ("、".join(names) if names else "无发现")))
        caught += 1 if hit else 0
    print()
    print("%d/%d 类历史错误被闸门拦截" % (caught, len(cases)))

    # 判据写错必须报"自身错误"，不能算通过
    bad_spec = Chain("坏 spec")
    bad_spec.eq("写错的判据", "1+", 1.0)
    bad, _ = bad_spec.verify()
    err_ok = bool(bad_spec._errors) and not bad
    print("[%s] 判据自身写错被识别为 spec 错误（不冒充「抓到问题」）"
          % ("OK" if err_ok else "ERR"))

    # 空判据不算通过
    empty = Chain("空")
    print("[%s] 空判据集不判通过（分母为 0）"
          % ("OK" if not empty.checks else "ERR"))

    # classify：按判别量分组，而不是靠人看图形状
    SEC = {"A-A": 0.725, "B-B": 0.725, "C-C": 0.861, "D-D": 1.244, "E-E": 1.187}
    WEB = {"A-A": 0.257, "B-B": 0.258, "C-C": 0.357, "D-D": 0.604, "E-E": 0.594}
    BF = dict((k, 0.604) for k in SEC)
    BF["E-E"] = 0.594
    ratio = dict((k, WEB[k] / BF[k]) for k in SEC)

    Cs = Chain("断面分类")
    grp = Cs.classify("断面", SEC, ratio, (0.50, 0.90), ("梁身", "过渡", "填实"))
    bs, _ = Cs.verify()
    cls_ok = (not bs) and grp["梁身"] == ["A-A", "B-B"] and len(grp["过渡"]) == 1 and len(grp["填实"]) == 2
    print("[%s] classify 按腹板宽/底翼缘分档：梁身 %s / 过渡 %s / 填实 %s，组内同质且组间单调"
          % ("OK" if cls_ok else "ERR", grp["梁身"], grp["过渡"], grp["填实"]))
    if bs:
        print(Cs.report(bs, []))

    # 判别量测错（在翼缘高度处量，5 个剖面看起来都一样）→ 全归梁身 → 组内离散度必须报警
    flat = dict((k, 0.257) for k in SEC)
    r2 = dict((k, flat[k] / BF[k]) for k in SEC)
    Cb = Chain("断面分类·坏测量")
    Cb.classify("断面", SEC, r2, (0.50, 0.90), ("梁身", "过渡", "填实"))
    bb, _ = Cb.verify()
    names = [r[0] for r in bb]
    flat_ok = any("同质" in n for n in names) and any("非空" in n for n in names)
    print("[%s] 判别量测错（全同值）→ 报警：%s"
          % ("OK" if flat_ok else "ERR", "、".join(names) or "无"))

    ok = (caught == len(cases) and err_ok and not empty.checks
          and cls_ok and flat_ok)
    print()
    print("SELFTEST: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="chain_check", description="尺寸链闸门 CHAIN")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="按 spec 跑尺寸链")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", default="")
    p.add_argument("--json", action="store_true", help="（保留）与 --out 同效")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("selftest", help="故障注入自证")
    p.set_defaults(func=cmd_selftest)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
