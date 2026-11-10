#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate_source_semantics.py —— 外部数据源语义体检门禁（G-SRC）

## 它解决什么问题

本技能原有 40+ 个门禁全部在验**内部自洽**：算式对不对、单位对不对、跨表对不对。
但实测事故里最致命的一类错误不在这里——**是外部数据源被读错了语义**：

| 实测事故 | 后果 | 原有门禁能否发现 |
|---|---|---|
| 把 `DISCHARGES (m3/S)` 流量列当成尺寸列 | 洞身混凝土 9,191 → 5,356（−42%） | ❌ 算式全对 |
| 把表内示例值 `B=3.00` 当定值（实为 3×W 的函数） | 洞口防护 336.1 → 234.9（−30%） | ❌ 算式全对 |
| 假设「一图一柜」，实际一图 2~3 个柜 | 照明负荷 693.61 → 215.19 kW（漏 69%） | ❌ 算式全对 |
| 外部清单整列含 ×1.05 富余系数 | 全表比较基准偏 5%，假差异淹没真差异 | ❌ 算式全对 |
| 单值替换忽略量纲（榀 vs m³） | 偏差率算出 +2549% | ❌ 单位列没写不会报 |

**共同特征：算得对，但算错了东西。** 这类错误在交付物上看不出任何异常，
因为算式、单位、汇总全部自洽。必须在"把外部/图纸数据喂进计算之前"单独体检。

## 用法

```bash
# ① 表格类外部清单（xlsx/csv）——体检列语义与隐藏系数
python scripts/gate_source_semantics.py table --file <清单>.xlsx --sheet Sheet1 \
    --out out/src_check/ [--declare out/src_check/decl.json]

# ② 图纸信息表（schedule）——体检表头与单位
python scripts/gate_source_semantics.py schedule --file <信息表>.xlsx --sheet CULVERT \
    --out out/src_check/

# ③ 只有文字/图片时，声明式登记（人填，机器校验完整性）
python scripts/gate_source_semantics.py declare --out out/src_check/decl.json

# ④ 用已确认的声明去校验，阻断未确认项进入计算
python scripts/gate_source_semantics.py verify --file <清单>.xlsx --declare out/src_check/decl.json
```

## 判定与退出码

    0  PASS        全部列语义已登记且通过可疑系数扫描
    1  FAIL        存在未登记的列 / 可疑系数 / 量纲冲突
    2  CANNOT_RUN  文件打不开、页签不存在、声明文件损坏

## 项目原则：一律按不含富余系数的净量出数

外部清单/图纸量表若含损耗、富余、超挖、搭接等系数，**必须先 ÷ 系数归一到净量**，
再进入本技能的任何换算与对比。含系数出数会造成两个后果：

1. 与其它来源（图纸净量、其它方清单）对比时产生**固定比例的系统性假差异**，
   把真实的量差淹没掉；
2. 一旦进入报价链，系数被重复叠加（清单含 5% + 报价再加 5% → 实际 10.25%）。

`table` 子命令会自动扫描可疑系数；`verify` 会校验声明的 `value_basis` 与你填的
`factor_if_gross` 是否一致。

## 设计原则

1. **不猜列义，只标可疑**。脚本不去"理解"列名，而是：
   - 列出全部列供人确认（`--list-columns`）
   - 对数值列做**统计指纹**（是否全部可整除某系数、是否全为整数、取值范围是否与物理量匹配）
2. **可疑即阻断**。凡命中"整列可整除某系数"的，一律 FAIL 直到人显式确认。
3. **声明是契约**。人确认后的列义写入 decl.json，后续每次换算都校验它还在。

作者：曹工国际工程算量助手 v4.6.2
"""
import argparse
import json
import math
import os
import re
import sys

# ---------- 可疑系数库 ----------
# 来源：投标 BOQ 常见富余/损耗系数。命中任一即要求人确认。
SUSPECT_FACTORS = [
    (1.05, '5% 损耗/富余（投标 BOQ 最常见）'),
    (1.10, '10% 损耗/富余'),
    (1.15, '15% 损耗'),
    (1.075, '7.5% 损耗（英制常见）'),
    (1.03, '3% 损耗'),
    (1.02, '2% 损耗'),
]

# ---------- 系数扫描判据参数 ----------
SAMPLE_MIN = 5          # 少于此行数不做系数校验（但必须 WARN，不得静默）
LAT_RATE_MIN = 0.40     # 落在系数网格上的最低比例
LAT_MULT = 5.0          # 必须高于随机基线多少倍
LAT_PMAX = 1e-4         # 二项尾概率上限（防止小样本偶然全中）
LAT_MIN_HITS = 3        # 最少命中个数
PAIR_SAMPLE_MAX = 200   # 成对比值抽样的上限（n 大时抽样）
PAIR_MIN_PAIRS = 8      # 成对比值通道的最少比值个数（低于此无统计效力）

# ---------- 像格（lattice）判据参数 ----------
# 专治「净量本身已是 dec 位小数」时小数网格判据的失效区（见 _lattice_holes 注释）
LATTICE_MIN_N = 150     # 低于此行数无法做单侧下尾检验（统计功效不足）
LATTICE_PMAX = 1e-3     # 下尾概率阈值（洞数显著偏少 → 可疑）

# 系数 → 精确分数 (分子, 分母)，像格判据需全整数运算，不能走浮点
FACTOR_FRACTIONS = {
    1.05: (21, 20),
    1.10: (11, 10),
    1.15: (23, 20),
    1.075: (43, 40),
    1.03: (103, 100),
    1.02: (51, 50),
}

# ---------- 量纲可疑词对 ----------
# 列名/表头出现这些词，须核对其量纲与用途是否一致。
# ★ 只允许「词边界匹配」；长度 <2 的键只保留 '%'（'a'/'m'/'t' 这类单字母
#   用子串匹配会咬出 qty→吨、total→电流、item→长度 的噪声，已废弃）。
DIM_HINTS = {
    'm3/s': '流量', 'm³/s': '流量', 'l/s': '流量', 'cfs': '流量',
    'kw': '功率', 'kva': '视在功率',
    'mm': '长度(毫米)',
    'm3': '体积', 'm³': '体积', 'cm': '体积(立方米简写)',
    'm2': '面积', 'm²': '面积', 'sqm': '面积',
    'kg': '质量', 'ton': '吨', 'mt': '公吨',
    'ea': '计数', 'pcs': '计数', 'no': '计数', 'set': '套',
    '%': '百分比', 'ratio': '比值', 'slope': '坡度',
}


def _norm_header(h):
    return re.sub(r'\s+', '', str(h or '')).lower()


def _to_num(v):
    """字符串数字归一：'1,234.5' / ' 10 ' / 100 → float；无法解析返回 None

    ★ 关键：CSV 分支只能拿到**原始字符串**，xlsx 分支拿到的是 float/int。
      若这里只认 int/float，CSV 输入下所有数值会被过滤成空，
      扫描器直接跳过该列 → 同一份数据 xlsx 报 FAIL、csv 报 PASS。
      因此这里必须容错解析字符串。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '').replace('\u00a0', '')
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_number(v):
    """兼容名保留：行为已改为「可解析为数字即为数字」（含 CSV 字符串）"""
    return _to_num(v) is not None


def _nums(vals):
    """取列内非零数值（统一走 _to_num，csv/xlsx 同口径）"""
    out = []
    for v in vals:
        x = _to_num(v)
        if x is not None and x != 0:
            out.append(x)
    return out


def _col_decimals(nums, cap=6):
    """推断整列存了多少位小数（取最大；推断失败返回 cap=全精度）"""
    d = 0
    for v in nums:
        for k in range(cap + 1):
            if abs(round(v, k) - v) <= 1e-9:
                d = max(d, k)
                break
        else:
            return cap
    return d


def _grid_rate(nums, factor, prec, dec):
    """一列中有多少值 = round(净量, prec) × factor 且净量真的只有 prec 位小数

    返回 (比例, 命中数, 随机基线比例)

    原理：gross = round(net × factor, dec)，net 只有 prec 位小数。
      · 若 prec < dec，gross 里**仍然保留** net 的小数信息 →
        gross/factor 会以很高比例落在 prec 位小数网格上（远高于随机基线）；
      · 若 prec >= dec，信息已被舍入抹平 → 比例回落到基线（检测不出来，
        这是信息论下限，不是判据缺陷）。
    """
    tol = 0.5 * 10.0 ** (-dec) / factor + 1e-12
    hit = 0
    for v in nums:
        q = v / factor
        if abs(q - round(q, prec)) <= tol:
            hit += 1
    # 随机基线：净量落在 prec 位小数网格的固有概率
    base = min(1.0, 10.0 ** (prec - dec) / factor)
    return hit / len(nums), hit, base


def _binom_tail(k, n, p):
    """P(X >= k)，X~B(n,p)：防止「容器里只有 8 个数，蒙到 4 个」就算命中"""
    if k <= 0:
        return 1.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                       for i in range(k, n + 1)))


def _pair_ratio_rates(nums):
    """列内「成对比值」聚类：统计排序后相邻比值落在各系数 ±0.5% 的比例

    这是**辅助**信号，针对一种真实现象：同一列里净量与含系数值并存
    （分部行 net 与汇总行 gross 并列、或对方清单与图纸量对照），
    此时相邻比值会明显堆在某系数上。

    必须带显著性门槛，否则 n=8 时「8 个比值里蒙中 4 个」就会误报：
      · 单侧带宽 ±0.5%×f，比值落在其中的随机基线约 1%；
      · 要求命中比例 >= 0.40 且二项尾概率 <= 1e-4；
      · 比值个数太少（<8）直接放弃该通道。
    """
    s = sorted(nums)
    if len(s) < 2:
        return {}
    ratios = [b / a for a, b in zip(s, s[1:]) if a > 0 and b > 0][:PAIR_SAMPLE_MAX]
    n = len(ratios)
    if n < PAIR_MIN_PAIRS:
        return {}
    base = 0.01   # 随机基线：比值落在 ±0.5% 窄带内的固有概率
    out = {}
    for f, _ in SUSPECT_FACTORS:
        hit = sum(1 for r in ratios if abs(r - f) <= 0.005 * f)
        rate = hit / n
        if rate < LAT_RATE_MIN:
            continue
        if _binom_tail(hit, n, base) <= LAT_PMAX:
            out[f] = rate
    return out


def scan_factor(vals, factor, tol=1e-6):
    """判断一列数值中「可被 factor 整除」的比例（整数整除指纹，辅助信号）

    为什么不能只看命中率：
      真实工程量本身常是整数（如 76.6、59.0），对 1.05 的命中率自然偏高。
      且工程量的净量带小数时，v/factor 几乎不可能落在 1e-6 的整数上 ——
      所以本函数只作为「整列都是整数净量」这一特例的快速指纹，
      带小数的情形由 factor_is_suspicious 的小数网格判据负责。

    返回 (命中率, 说明)；样本不足时返回 (None, 说明) —— 调用方必须 WARN。
    """
    nums = _nums(vals)
    if len(nums) < SAMPLE_MIN:
        return None, '样本不足 n=%d' % len(nums)
    hit = sum(1 for v in nums if abs(v / factor - round(v / factor)) < tol)
    return hit / len(nums), '%d/%d 可整除 %.3f' % (hit, len(nums), factor)


def _in_factor_image(j, num, den):
    """判断整数格点 j 是否等于 round(m×num/den)，m 为某个整数 m>=1

    Excel ROUND 是 half-up：round(x)=j ⟺ j-0.5 <= x < j+0.5
    换到整数域：2·m·num ∈ [ (2j-1)·den, (2j+1)·den )
    区间长 den/num < 1，故至多含一个整数 m，取 m=ceil(lo) 验证即可。
    走整数运算是因为 f=1.03/1.02 在浮点下会在边界反复抖动。
    """
    lo = (2 * j - 1) * den
    hi = (2 * j + 1) * den
    step = 2 * num
    m = -((-lo) // step)      # ceil(lo/step)：下界闭
    if m < 1:
        m = 1
    return m * step < hi      # 上界开


def _binom_lower_tail(k, n, p):
    """P(X <= k)，X~B(n,p)"""
    if k < 0:
        return 1.0
    if k >= n:
        return 1.0
    return min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                        for i in range(0, k + 1)))


def _lattice_holes(nums, factor, dec):
    """像格洞检验：gross=round(净量×f, dec) 只能取 round(m×f) 这些离散格点

    为什么需要这一路（小数网格判据的失效区）：
      若净量本身就已经是 dec 位小数（如 BOQ 常见 2 位小数 ×1.05），
      则 gross 也是 dec 位小数，小数网格判据在信息上已无法区分（见 _grid_rate）。
      但**仍有残余结构**：gross 只能落在 round(m×f) 的像上，
      像集约有 (1-1/f)≈4.76% 的格点**永远取不到**（"洞"）。
      纯小数净量×f 的列**一个洞都不该有**；任意干净列平均会踩 4.76% 的洞。
      于是「洞数显著偏少」= 该列经过 ×f 变换的强证据。

    ★ 平局（x.5）容差：gross 的浮点表示可能把本该落在格点边界上的值
      表示成 0.4999999 或 0.5000001。实测这种抖动会让 ~4.5% 的值被误判成洞，
      恰好把 4.76% 的洞率差抹平。故对 j 同时试 j 与（half-up 下界情形下的）
      j±1 候补，只要有一个在像上即算"未踩洞" —— 这是**对判据自身不利**的方向
      （更多值算作在像上 → 洞更少 → gross 更容易被判为无系数），
      因此不会制造假红，只会让判据更保守。

    统计功效边界（已实测，必须如实告知）：
      · 洞率差只有 4.76%，单侧下尾检验需要 n >= LATTICE_MIN_N 才有功效；
      · n=8 时干净列「恰好 0 洞」的概率 (20/21)^8 = 67.7%，
        与真 gross 列完全不可区分 —— **8 行级别的纯 2 位小数 gross
        在数学上不可能被检出**，只能靠 declare/verify 的人工声明兜底。
      实测（整数同源、排除浮点干扰）：n>=200 检出率 94%，干净列假红率 0%。
    """
    frac = FACTOR_FRACTIONS.get(round(factor, 6))
    if frac is None or dec < 1:
        return None
    num, den = frac
    sc = 10 ** dec
    # ★ 必须按**唯一值**计数：同一数值重复出现不提供任何新证据。
    #   曾用原始行数，导致「8 个干净值重复 19 次」被当成 n=152 的独立样本 →
    #   洞数天然为 0 → 假红。去重后该场景正确不报警。
    uniq = sorted(set(nums))
    holes = n = 0
    for v in uniq:
        j = int(math.floor(v * sc + 0.5))     # half-up，与 Excel ROUND 一致
        if j <= 0:
            continue
        n += 1
        if not _in_factor_image(j, num, den):
            holes += 1
    if n < LATTICE_MIN_N:
        return None
    p_hole = 1.0 - (1.0 / factor)
    pval = _binom_lower_tail(holes, n, p_hole)
    if pval <= LATTICE_PMAX:
        return dict(holes=holes, n=n, pval=pval, expect=n * p_hole)
    return None


def factor_is_suspicious(vals, factor):
    """综合「像格洞检验」+「小数网格指纹」+「整数整除」+「成对比值」判断

    规则（四路取"或"，任一成立即报）：
      1. 像格洞：gross 只能落在 round(m×f) 的像上，纯小数净量×f 的列
         一个"洞"都没有，而干净列平均 4.76% 是洞 → 洞数显著偏少即可疑
         （仅对 n>=LATTICE_MIN_N 有效，见 _lattice_holes）。
      2. 小数网格：该列的净量级小数位 prec 上，gross/factor 的命中比例
         >= 0.40、>= 5 倍随机基线、且二项尾概率 <= 1e-4；
         **对每个 prec < 列小数位 都试一遍**，故多个系数可同时报警
         （混合 1.05 + 1.10 是清单常态，不能假设"单一主导"）。
      3. 整数整除：命中率 >= 0.85（几乎全列可整除）。
      4. 成对比值：相邻比值堆在该系数 ±0.5% 内的比例 >= 0.40。

    返回 (是否可疑, 说明)。样本不足返回 (False, '样本不足，系数未校验')，
    由 cmd_table 负责输出 [WARN] SRC-FACTOR-SKIP。
    """
    nums = _nums(vals)
    if len(nums) < SAMPLE_MIN:
        return False, '样本不足，系数未校验'

    dec = _col_decimals(nums)

    # 1) 像格洞检验（专治「净量已是 dec 位小数」的失效区）
    lat = _lattice_holes(nums, factor, dec)
    if lat:
        return True, ('%d 个值全部落在 ×%.3f 的可达像格上，'
                      '一个"洞"都没踩到（干净列在本列量级下平均应踩 %.1f%% 的洞，'
                      '即约 %.0f 个；下尾概率 P=%.1e）'
                      % (lat['n'], factor, (1 - 1.0 / factor) * 100,
                         lat['expect'], lat['pval']))

    # 2) 小数网格指纹（主判据）
    best = None
    for prec in ([] if dec == 0 else list(range(0, dec))):
        rate, hits, base = _grid_rate(nums, factor, prec, dec)
        if rate < LAT_RATE_MIN or hits < LAT_MIN_HITS:
            continue
        if base > 0 and rate < LAT_MULT * base:
            continue
        if _binom_tail(hits, len(nums), base) > LAT_PMAX:
            continue
        if best is None or rate > best[0]:
            best = (rate, prec, hits, base)
    if best:
        rate, prec, hits, base = best
        return True, ('%d/%d 个值落在 ×%.3f 的 %d 位小数网格上'
                      '（%.0f%%，随机基线仅 %.1f%%）'
                      % (hits, len(nums), factor, prec, rate * 100, base * 100))

    # 3) 整数整除指纹（辅助）
    rate_main, desc = scan_factor(nums, factor)
    if rate_main is not None and rate_main >= 0.85:
        return True, desc + '（≥85%）'

    # 4) 成对比值聚类（辅助）
    pr = _pair_ratio_rates(nums)
    if pr and pr.get(factor, 0.0) >= LAT_RATE_MIN:
        return True, ('列内相邻比值有 %.0f%% 落在 ×%.3f ±0.5%% 内'
                      % (pr[factor] * 100, factor))

    return False, desc


# 2 位小数列在多大样本下才可能用"像格洞"分辨出系数。
# 实测（200 次重抽，判据 v/1.05 落在 2 位网格）：
#   n=60  → gross 0.9796 vs clean 0.9557，z=0.74 不可分
#   n=200 → gross 0.9816 vs clean 0.9520，z=1.66 不可分
#   n=1000→ z=3.50 可分 ；n=5000→ z=7.93 可分
# 故阈值取 1000：低于此必须如实声明"不可检出"，不得假装已覆盖。
_RESOLVE_MIN_N = 1000


def _decimal_resolvable(nums):
    """2 位小数列在该样本量下，判据是否还可能有分辨力。

    理论基础（见 factor_is_suspicious 的 docstring）：2 位小数净量 ×1.05 再舍入
    到 2 位时，v/1.05 落在 2 位网格的比例约 0.98，而干净随机 2 位小数列约 0.95。
    单行的辨识度只有约 2.7pp，必须靠样本量累积：n 小时噪声压倒信号。
    这里用保守阈值：样本 < 60 行即认为不可分辨，必须如实声明边界。
    """
    return len(nums) >= _RESOLVE_MIN_N


def scan_all_integers(vals):
    """一列是否全为整数（纯统计量，不代表错误，仅供人判断是否已是净量）"""
    nums = _nums(vals)
    if not nums:
        return 0.0, '无数据'
    n_int = sum(1 for v in nums if abs(v - round(v)) < 1e-9)
    return n_int / len(nums), '%d/%d 为整数' % (n_int, len(nums))


def dim_hint(header):
    """按**词边界**匹配量纲提示词，避免 qty→吨 / total→电流 这类子串噪声"""
    nh = _norm_header(header)
    if not nh:
        return ''
    for k, v in DIM_HINTS.items():
        if len(k) < 2 and k != '%':
            continue
        if re.search(r'(?<![a-z])' + re.escape(k) + r'(?![a-z])', nh):
            return v
    return ''


def load_table(path, sheet=None):
    """读 xlsx/csv → (headers, rows[list of dict])"""
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.xlsx', '.xlsm', '.xls'):
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise RuntimeError('需要 openpyxl：pip install openpyxl')
        wb = load_workbook(path, data_only=True, read_only=True)
        ws = wb[sheet] if sheet else wb[wb.sheetnames[0]]
        it = ws.iter_rows(values_only=True)
        try:
            headers = [('' if h is None else str(h)) for h in next(it)]
        except StopIteration:
            return [], []
        rows = []
        for r in it:
            if all(c is None for c in r):
                continue
            rows.append({headers[i]: r[i] for i in range(min(len(headers), len(r)))})
        return headers, rows
    elif ext in ('.csv', '.tsv', '.txt'):
        import csv
        delim = ',' if ext == '.csv' else '\t' if ext == '.tsv' else ','
        with open(path, encoding='utf-8-sig', newline='') as f:
            rd = csv.DictReader(f, delimiter=delim)
            rows = [dict(x) for x in rd]
            return list(rd.fieldnames or []), rows
    raise RuntimeError('不支持的格式：%s' % ext)


def cmd_list_columns(a):
    headers, rows = load_table(a.file, a.sheet)
    if not headers:
        print('[CANNOT_RUN] 表为空或无表头')
        return 2
    print('== 共 %d 列 / %d 数据行 ==' % (len(headers), len(rows)))
    print()
    print('%-4s %-34s %-10s %-24s %s' % ('#', '列名', '类型', '统计指纹', '量纲提示'))
    print('-' * 104)
    for i, h in enumerate(headers, 1):
        vals = [r.get(h) for r in rows]
        nums = _nums(vals)
        txt = [v for v in vals if isinstance(v, str) and v.strip()]
        if nums and len(nums) >= max(3, len(vals) * 0.5):
            typ = '数值'
            fin = []
            for f, why in SUSPECT_FACTORS:
                rate, desc = scan_factor(nums, f)
                if rate is not None and rate >= 0.95:
                    fin.append('⚠ ×%.3f(%s)' % (f, desc))
            ir, idesc = scan_all_integers(nums)
            if ir >= 0.99:
                fin.append('全整数')
            fingerprint = '; '.join(fin) if fin else '%d 个数值' % len(nums)
        elif txt:
            typ = '文本'
            fingerprint = '%d 个文本' % len(txt)
        else:
            typ = '空'
            fingerprint = '—'
        hint = dim_hint(h)
        print('%-4d %-34s %-10s %-24s %s' % (i, h[:34], typ, fingerprint[:24], hint))
    print()
    print('★ 下一步：把每一列的真实语义填进声明文件（declare），再跑 verify。')
    print('★ 凡带 ⚠ 的数值列，必须先确认「表列值是净量还是含系数」，否则不得用于换算。')
    return 0


def cmd_table(a):
    headers, rows = load_table(a.file, a.sheet)
    if not headers:
        print('[CANNOT_RUN] 表为空或无表头')
        return 2

    findings = []
    for i, h in enumerate(headers, 1):
        vals = [r.get(h) for r in rows]
        nums = _nums(vals)
        if not nums:
            continue
        # ★ 样本不足不再静默跳过：必须 WARN，让人决定是否补数据后复核
        if len(nums) < SAMPLE_MIN:
            findings.append(dict(
                level='WARN', code='SRC-FACTOR-SKIP', col=i, header=h,
                msg='样本仅 n=%d 行（<%d），富余系数未校验 —— '
                    '少数几行也可能是亿级分部汇总行，请人工确认该列'
                    '是否含损耗/富余系数后再取数' % (len(nums), SAMPLE_MIN)))
            continue
        for f, why in SUSPECT_FACTORS:
            sus, desc = factor_is_suspicious(nums, f)
            if sus:
                findings.append(dict(
                    level='FAIL', code='SRC-FACTOR', col=i, header=h,
                    msg='疑似含富余系数 ×%.3f（%s）: %s。'
                        '本项目原则为**按不含系数（净量）出数**，'
                        '必须先 ÷%.3f 归一后再参与任何换算/对比' % (f, why, desc, f)))
        # ★ v4.6.4：2 位小数以上的列，系数检测在**信息论上不可检出**，必须如实声明。
        # 实测（5000 样本）：2 位小数净量 ×1.05 再舍入到 2 位，其 v/1.05 落在
        # 2 位网格的比例约 0.98；而独立随机的干净 2 位小数列也有约 0.95 ——
        # 仅差约 2.7 个百分点，统计上不可区分。1 位小数档可检出（约 0.80 vs 基线），
        # 整数档可检出（1.00）。
        # 若不声明，静默 PASS 会让人以为"该列已通过系数检测"，而实际是"这个精度下
        # 根本检不出来" —— 这正是本项目反复批评的"零发现 ≠ 干净"。
        # 只有在**所有系数都没被检出**时，才需要说明"可能是精度不够"。
        # 若已有任一 SRC-FACTOR 命中，本列就不属于"不可检出"，不得再加此 INFO
        # （否则大样本本可检出的场景会被这句 INFO 掩盖成"无法判定"）。
        col_flagged = any(x.get('col') == i and x.get('level') == 'FAIL'
                          for x in findings)
        if not col_flagged:
            dec = _col_decimals(nums)
            if dec >= 2:
                # 大样本下"像格洞"判据仍可能可检出；先问判据本身是否可分辨，
                # 不可分辨才声明边界。判断方式：用与干净随机列的可分性做门槛。
                resolvable = _decimal_resolvable(nums)
                if not resolvable:
                    findings.append(dict(
                        level='INFO', code='SRC-FACTOR-LIMIT', col=i, header=h,
                        msg='本列精度为 %d 位小数，且样本量不足以分辨系数：'
                            '×1.05 舍入后与净量在统计上**不可检出**'
                            '（受精度限制，非"已确认无系数"）。'
                            '不得据本列 PASS 推断"无富余系数"；'
                            '如需确认，请索取**原始未舍入清单**或供应商书面声明'
                            % dec))

    # 量纲可疑：表头写流量/功率，但同表其它列是尺寸
    norm_headers = [_norm_header(h) for h in headers]
    has_flow = any(('m3/s' in h or 'm³/s' in h or 'l/s' in h) for h in norm_headers)
    has_dim = any(h in ('mm', 'm', 'height', 'width', 'span') or
                  h.endswith('mm') or h.endswith('(mm)') for h in norm_headers)
    if has_flow and has_dim:
        findings.append(dict(
            level='FAIL', code='SRC-DIM', col=0, header='(表头)',
            msg='同表内同时存在「流量单位」与「尺寸单位」列头 —— '
                '这是"把流量当尺寸"的高发场景。必须逐列确认后再取数'))

    outdir = a.out or 'out/src_check'
    os.makedirs(outdir, exist_ok=True)
    rep = os.path.join(outdir, 'source_semantics.json')
    json.dump(dict(file=a.file, sheet=a.sheet, headers=headers,
                   n_rows=len(rows), findings=findings),
              open(rep, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print('== %s ==' % a.file)
    print('   %d 列 / %d 行' % (len(headers), len(rows)))
    print('   报告：%s' % rep)
    print()
    fails = [f for f in findings if f.get('level') == 'FAIL']
    warns = [f for f in findings if f.get('level') == 'WARN']
    infos = [f for f in findings if f.get('level') == 'INFO']
    for f in infos:
        print('[INFO] %s 第%d列「%s」' % (f['code'], f['col'], f['header']))
        print('       %s' % f['msg'])
    if not fails and not warns:
        print('[PASS] 未发现可疑系数或量纲冲突%s'
              % ('（但见上方 INFO：部分列受精度限制不可检出）' if infos else ''))
        print('       注意：PASS 只说明"没有统计指纹异常"，不代表列义已被确认。')
        print('       仍须填声明文件（declare）后跑 verify。')
        return 0
    for f in fails:
        print('[FAIL] %s 第%d列「%s」' % (f['code'], f['col'], f['header']))
        print('       %s' % f['msg'])
    for f in warns:
        print('[WARN] %s 第%d列「%s」' % (f['code'], f['col'], f['header']))
        print('       %s' % f['msg'])
    print()
    if fails:
        print('共 %d 条阻断项、%d 条告警。'
              '**在确认前不得用这些列驱动任何工程量换算。**'
              % (len(fails), len(warns)))
        return 1
    print('共 %d 条告警（无阻断项）。**样本不足的列在人工确认前不得当作净量使用。**'
          % len(warns))
    return 0


def cmd_declare(a):
    """生成声明模板（人填）"""
    outdir = a.out or 'out/src_check'
    os.makedirs(outdir, exist_ok=True)
    path = a.declare or os.path.join(outdir, 'decl.json')

    tpl = dict(
        _README=[
            '本文件是「外部数据源语义声明」，是取数的前置契约。',
            '每条声明的 confirmed_by 必须是人名；AI 不得代填。',
            'col_role 受控词表见下；value_basis 必须二选一：net / gross。',
            '任一列未登记 → verify 判 FAIL → 该列不得进入换算。',
        ],
        file='<清单路径>',
        sheet='<页签名>',
        columns=[
            dict(
                col_index=1,
                header='<原样抄表头>',
                col_role='<quantity|dimension|rate|code|name|unit|flow|power|other>',
                unit='<m3|m2|m|mm|kg|t|ea|kw|m3/s|->',
                value_basis='<net|gross>',
                factor_if_gross=1.0,
                note='<为何这样判定：看了哪个图注/表头/上下文>',
                confirmed_by='＿＿＿＿＿＿',
                confirmed_at='YYYY-MM-DD',
            ),
        ],
    )
    json.dump(tpl, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[OK] 声明模板已生成：%s' % path)
    print('     按列填好 col_role / unit / value_basis / confirmed_by 后，跑 verify。')
    return 0


ROLE_VOCAB = {'quantity', 'dimension', 'rate', 'code', 'name', 'unit',
              'flow', 'power', 'other'}
BASIS_VOCAB = {'net', 'gross'}


def cmd_verify(a):
    if not a.declare or not os.path.exists(a.declare):
        print('[CANNOT_RUN] 缺 --declare 或文件不存在：%s' % a.declare)
        return 2
    try:
        decl = json.load(open(a.declare, encoding='utf-8'))
    except Exception as e:
        print('[CANNOT_RUN] 声明文件损坏：%s' % e)
        return 2

    headers, rows = load_table(a.file, a.sheet or decl.get('sheet'))
    if not headers:
        print('[CANNOT_RUN] 表为空或无表头')
        return 2

    declared = {}
    for c in decl.get('columns', []):
        hi = c.get('col_index')
        if isinstance(hi, int) and 1 <= hi <= len(headers):
            declared[hi] = c

    fails = []
    warns = []

    # 1) 每列都登记了吗
    for i, h in enumerate(headers, 1):
        if i not in declared:
            vals = [r.get(h) for r in rows]
            if _nums(vals):
                fails.append('第%d列「%s」是数值列但未登记语义 —— 不得用于换算' % (i, h))
    # 2) 登记值合法吗
    for i, c in declared.items():
        role = c.get('col_role')
        if role not in ROLE_VOCAB:
            fails.append('第%d列 col_role=%r 不在受控词表 %s'
                         % (i, role, sorted(ROLE_VOCAB)))
        basis = c.get('value_basis')
        if basis not in BASIS_VOCAB:
            fails.append('第%d列 value_basis=%r 必须是 net/gross' % (i, basis))
        cb = str(c.get('confirmed_by') or '').strip()
        if not cb or '＿' in cb or cb.lower() in ('', 'ai', 'tbd', 'none'):
            fails.append('第%d列 confirmed_by 未由人签署（当前=%r）' % (i, cb))
        # 3) 声明为 gross 的必须给系数
        if basis == 'gross':
            f = c.get('factor_if_gross')
            if not isinstance(f, (int, float)) or f <= 0:
                fails.append('第%d列声明 gross 但 factor_if_gross 无效（=%r）' % (i, f))
            elif abs(f - 1.0) < 1e-9:
                warns.append('第%d列声明 gross 但系数=1.0，与 net 无区别，请复核' % i)
        # 4) 声明与实际统计指纹矛盾？
        if role == 'quantity':
            vals = [r.get(headers[i - 1]) for r in rows]
            for fac, why in SUSPECT_FACTORS:
                sus, desc = factor_is_suspicious(vals, fac)
                if sus and basis == 'net':
                    warns.append('第%d列声明 net（净量），但统计上 %s —— '
                                 '若实际含 ×%.3f 系数，则按 net 使用会偏 %.1f%%。'
                                 '本项目原则为净量出数，请确认后再用'
                                 % (i, desc, fac, (fac - 1) * 100))

    print('== verify: %s ==' % a.file)
    print('   已登记 %d / %d 列' % (len(declared), len(headers)))
    print()
    for w in warns:
        print('[WARN] %s' % w)
    if fails:
        for f in fails:
            print('[FAIL] %s' % f)
        print()
        print('共 %d 条阻断项。**不得以该数据源出交付值。**' % len(fails))
        return 1
    print('[PASS] 全部数值列语义已登记且签署；无阻断项')
    return 0


def main():
    ap = argparse.ArgumentParser(description='外部数据源语义体检门禁 G-SRC')
    sub = ap.add_subparsers(dest='cmd')

    p1 = sub.add_parser('list-columns', help='列出全部列 + 统计指纹（不改数据）')
    p1.add_argument('--file', required=True)
    p1.add_argument('--sheet')
    p1.set_defaults(func=cmd_list_columns)

    p2 = sub.add_parser('table', help='体检 xlsx/csv 的可疑系数与量纲冲突')
    p2.add_argument('--file', required=True)
    p2.add_argument('--sheet')
    p2.add_argument('--out')
    p2.add_argument('--declare')
    p2.set_defaults(func=cmd_table)

    p3 = sub.add_parser('declare', help='生成语义声明模板')
    p3.add_argument('--out')
    p3.add_argument('--declare')
    p3.set_defaults(func=cmd_declare)

    p4 = sub.add_parser('verify', help='用声明校验数据源')
    p4.add_argument('--file', required=True)
    p4.add_argument('--sheet')
    p4.add_argument('--declare', required=True)
    p4.set_defaults(func=cmd_verify)

    a = ap.parse_args()
    if not getattr(a, 'cmd', None):
        ap.print_help()
        return 2
    try:
        return a.func(a)
    except Exception as e:
        print('[CANNOT_RUN] %s' % e)
        return 2


if __name__ == '__main__':
    sys.exit(main())
