# -*- coding: utf-8 -*-
"""跨图一致性校验 G-XD —— 同一参数在两个视图里给了两个值，必须一致。

**它补的是三道既有门禁都管不到的那一段**

    chain_check      一张图内：尺寸链闭不闭合
    gate_cross_sheet 一个工作簿跨页签：汇总一不一致
    xcheck_model_vs_2d 模型 vs 二维：两套数对不对得上

    "平面图说层高 3600、剖面图说 3300，你按哪个算？" —— 此前没人管。

算完一整层才发现层高是另一个数，代价是一个数量级。所以这道门禁
**必须放在算量之前**（SKILL.md 第 1.4 步）。

**六种结论，不许互相折算**

    PASS          两侧有值、同基准、同范围、差 ≤ 容差
    CONFLICT      两侧有值且差 > 容差           → exit 1，该参数下游量不得出交付值
    UNVERIFIABLE  缺一侧 / 缺溯源 / 不同基准 / 范围不同 / 数值写法歧义
                                               → 挂 OI 继续算，不单独致红
    RULE_CONFLICT 同一图号出现两个内容（哈希不同）→ 先定版，比不了
    UNFILLED      模板一个字没填               → 补填或写 note="N/A+理由"
    N/A           经判断不适用（note 声明）    → 单列，不计入通过

`UNVERIFIABLE` 是"没查"，不是"没问题"。把它折成 PASS 就是这道门禁最常见的死法：
16 项里只填了 3 项，报"3/3 通过"。

**容差不用统一值；派生值另开一档**

    标高 / 结构尺寸      ±0        差 1 mm 就是两个值，没有"粉刷层"解释
    标高（派生 vs 标注）  ±10 mm    分段取整累加 vs 总标注，天然有舍入差
    装修面层             ±5 mm     建筑做法允许的施工偏差
    角度 / 坡度          0.1%
    计数                 ±0        整数严格相等

用建筑口径的 ±5 mm 套到结构上，"层高 3600 vs 3300"这类真错会被当公差放过去。
反过来，拿 ±0 比"分段取整累加 vs 总标注"（3.30×3 对 9.89），会把每一条
合规的高程链都判成冲突——所以派生值必须显式写 `tol: elevation_derived`。

**版本闸：只拦"同一图号两个内容"，不拦"不同图册版本不同"**

    a.sheet == b.sheet 且 hash 不同   → RULE_CONFLICT（真版本分裂，必先定版）
    a.rev != b.rev（同哈希或不同图册）→ 继续比，标 cross_rev
                                        PASS 记「跨版一致」，CONFLICT 提示先核修订表
    --strict-rev                       → 恢复"rev 不同即停"，仅在合同要求同版对照时用

**为什么改**（v4.6.1）：建筑图 Rev C、剖面图 Rev B 是正常出图状态——立面上个月没动
不代表版本分裂。旧口径按"rev 不同即停"会让项目第二个月起 16 项全部 RULE_CONFLICT，
矩阵永远出不了 PASS，跑两次整条门禁就被跳过。这正是本项目记过的"假红比假绿更耗人"。
真要定版时，依据是**修订表与修订云线**（该参数所在区域是否被云线覆盖），不是比 rev 大小。

**数值写法：法语小数逗号必须认**

`3,60` 在法语/北非图册里是 3.60，不是 360。解析规则：逗号后 1~2 位按小数、
后 3 位按歧义（判 UNVERIFIABLE 要求改写），空格与不换行空格一律当千分位删除。
把逗号当千分位删掉，就是 100 倍污染，而且两侧写法相同时还会"错得一样"蒙混过去。

**用法**

    python cross_drawing_check.py init --spec takeoff/cross_check.json --project "XX"
    python cross_drawing_check.py run  --spec takeoff/cross_check.json --out out/xd.json
    python cross_drawing_check.py run  --spec spec.json --only B1,C2      # 只看标高类
    python cross_drawing_check.py selftest

退出码：0 无冲突（可含 UNVERIFIABLE）／1 有 CONFLICT／2 无法运行。
"""
from __future__ import print_function

import argparse
import io
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

VERSION = "1.0.0"
GATE_ID = "G-XD"

# --------------------------------------------------------------------------
# 校验矩阵（4 组 22 项）—— 与 references/cross-drawing-consistency.md §一 同源
# tol = 容差类别；detail = 该项目要验的是什么
# --------------------------------------------------------------------------
MATRIX = [
    # A 组：平面 × 立面
    ("A1", "A", "外窗水平位置",   "structure", "窗中心到最近轴线距离"),
    ("A2", "A", "外窗宽度",       "structure", "洞口宽 vs 立面窗宽"),
    ("A3", "A", "外门位置与宽度", "structure", "门洞定位 vs 立面门位"),
    ("A4", "A", "阳台/雨篷出挑",  "structure", "挑出深度（平面 vs 立面）"),
    ("A5", "A", "外墙材料分格",   "finish",    "材料与分格线"),
    # B 组：平面 × 剖面
    ("B1", "B", "层高",           "elevation", "楼层标高差 vs 剖面层高"),
    ("B2", "B", "门洞高",         "structure", "门窗表门高 vs 剖面门洞"),
    ("B3", "B", "窗台高",         "finish",    "门窗表窗台高 vs 剖面"),
    ("B4", "B", "楼梯净高",       "structure", "梯段与净高"),
    ("B5", "B", "室内外高差",     "elevation", "±0.000 与室外地坪"),
    ("B6", "B", "吊顶净高",       "finish",    "吊顶标高 vs 剖面"),
    # C 组：立面 × 剖面
    ("C1", "C", "建筑总高",       "elevation", "最高点标高"),
    ("C2", "C", "逐层标高",       "elevation", "各层标高（与 B1 三角互证）"),
    ("C3", "C", "女儿墙高度",     "structure", "女儿墙（立面 vs 剖面）"),
    ("C4", "C", "屋面形式",       "finish",    "檐口轮廓 vs 屋面坡度"),
    ("C5", "C", "外窗竖向位置",   "structure", "窗上下口标高（与 B3 互证）"),
    # D 组：建筑 × 结构（v4.6.1）——前三组是"建筑三视图之间"，这一组是"两个专业之间"。
    # 真实事故里后者频率更高、后果更大：一错就是一整层或一整个分部，不是一扇窗。
    ("D1", "D", "轴网与总尺寸",       "structure", "建筑 vs 结构：开间/进深/总长总宽"),
    ("D2", "D", "构件厚度",           "structure", "板厚/墙厚/基础底板厚：建筑 vs 结构"),
    ("D3", "D", "梁底标高 vs 净高",   "structure", "梁底标高 vs 房间净高/吊顶完成面"),
    ("D4", "D", "楼梯踏步数与层高",   "elevation_derived",
     "踏步数 × 踏步高 = 层高（填乘积与该层层高比）"),
    ("D5", "D", "基础埋深与 ±0.000",  "elevation", "基底/承台底标高 vs ±0.000 与室外地坪"),
    ("D6", "D", "层数与标准层界定",   "count",     "层数与标准层范围：建筑 vs 结构"),
]
MATRIX_BY_ID = {m[0]: m for m in MATRIX}

GROUP_LABEL = {"A": "平面×立面", "B": "平面×剖面", "C": "立面×剖面",
               "D": "建筑×结构"}

# 容差类别 → (绝对容差, 说明)。单位一律按 mm 折算后比较。
TOL = {
    "elevation":         (0.0,  "标高类 ±0：差 1 mm 就是两个值"),
    "elevation_derived": (10.0, "派生标高 ±10 mm：分段取整累加 vs 总标注的舍入差"),
    "structure":         (0.0,  "结构尺寸 ±0：施工图精度不放公差"),
    "finish":            (5.0,  "装修面层 ±5 mm：建筑做法允许偏差"),
    "angle":             (0.0,  "角度以下按相对差 0.1% 判"),
    "count":             (0.0,  "计数类严格相等"),
}
ANGLE_REL_TOL = 0.001

# 长度单位 → mm。**只做同维度换算**，跨维度（m³ 与 m²）一律判不可比。
LEN_TO_MM = {
    "mm": 1.0, "毫米": 1.0,
    "cm": 10.0, "厘米": 10.0,
    "m": 1000.0, "米": 1000.0,
    "km": 1000000.0, "公里": 1000000.0,
}

PASS, CONFLICT, UNVERIFIABLE, RULE_CONFLICT, UNFILLED = (
    "PASS", "CONFLICT", "UNVERIFIABLE", "RULE_CONFLICT", "UNFILLED")


def _is_blank(pair):
    """模板项一个字都没填 → UNFILLED。

    与 UNVERIFIABLE 分开是为了可读性：不是"查了但没法比"，是"根本没查"。
    两者都不算通过，但处置不同——未填要人补，不可比要图证。
    """
    for side in ("a", "b"):
        s = pair.get(side) or {}
        for k in ("sheet", "loc", "value", "rev", "hash"):
            if str(s.get(k) or "").strip():
                return False
    return True


# --------------------------------------------------------------------------
# 逗号后 3 位时无法判断是小数还是千分位：3,605 可能是 3.605 也可能是 3605。
# 这种写法一律要求改写，不猜——猜错的代价是 1000 倍，且看不出错。
_AMBIG_COMMA = object()


def _num(v):
    """取数值；非数字返回 None（空串/None/'见图' 一律算缺值）。

    法语/北非图册用逗号作小数点（`3,60`），用空格作千分位（`1 250`）。
    旧实现 `s.replace(",", "")` 把 3,60 读成 360 —— 100 倍污染，且两侧写法
    相同时"错得一样"，能一路绿到交付。这里按位置判断，判不了就报歧义。
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    # 千分位：普通空格、不换行空格、窄空格
    for ch in ("\u00a0", "\u202f", " ", "'"):
        s = s.replace(ch, "")
    if "," in s and "." in s:
        # 3,600.50（英式）vs 3.600,50（欧式）：最后一个分隔符是小数点
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        if 3 <= len(tail) and tail.isdigit():
            return _AMBIG_COMMA
        s = head + "." + tail
    try:
        return float(s)
    except ValueError:
        return None


def _is_ambiguous(v):
    return _num(v) is _AMBIG_COMMA


def _side_ok(side):
    """R1 溯源：图号 + 图面位置缺一即不可比对。返回缺失项列表。"""
    miss = []
    if not str(side.get("sheet") or "").strip():
        miss.append("图号")
    if not str(side.get("loc") or "").strip():
        miss.append("图面位置")
    return miss


def check_pair(pair, strict_rev=False):
    """单个校验对 → (结论, 说明, 明细dict)。"""
    pid = str(pair.get("id") or "").strip().upper()
    meta = MATRIX_BY_ID.get(pid)
    param = str(pair.get("param") or "").strip() or (meta[2] if meta else pid or "未命名")
    # 显式声明优先于矩阵默认（派生标高、按项目前言调整的容差都要能覆盖）
    tol_cls = str(pair.get("tol") or "").strip() or (meta[3] if meta else "")

    a = pair.get("a") or {}
    b = pair.get("b") or {}
    detail = {"id": pid, "param": param, "tol_class": tol_cls}

    if not meta and tol_cls not in TOL:
        return (UNVERIFIABLE,
                "校验项 %r 不在矩阵内，且未声明 tol——自定义项必须给容差类别（%s），"
                "否则容差无依据" % (pid or param, "/".join(sorted(TOL))), detail)

    # --- 版本闸 -----------------------------------------------------------
    ra, rb = str(a.get("rev") or "").strip(), str(b.get("rev") or "").strip()
    ha, hb = str(a.get("hash") or "").strip(), str(b.get("hash") or "").strip()
    sa, sb = str(a.get("sheet") or "").strip(), str(b.get("sheet") or "").strip()
    cross_rev = bool(ra and rb and ra != rb)
    # 真版本分裂：同一个图号出现两个内容。不同图册 rev 不同是常态，不是分裂。
    if ha and hb and ha != hb and sa and sa == sb:
        return (RULE_CONFLICT,
                "版本分裂：同一图号 %s 的两份文件哈希不同（%s… / %s…）——"
                "须先定版，否则比的是两个不同版本" % (sa, ha[:8], hb[:8]), detail)
    if cross_rev and strict_rev:
        return (RULE_CONFLICT,
                "版本分裂（--strict-rev）：%s rev%s ≠ %s rev%s——先定版再校"
                % (sa or "图A", ra, sb or "图B", rb), detail)
    if cross_rev:
        detail["cross_rev"] = True

    # --- R1 溯源 ----------------------------------------------------------
    ma, mb = _side_ok(a), _side_ok(b)
    if ma or mb:
        parts = []
        if ma:
            parts.append("A 侧缺" + "/".join(ma))
        if mb:
            parts.append("B 侧缺" + "/".join(mb))
        return (UNVERIFIABLE, "溯源不足（%s）——没有图号与图面位置就没法回图核对"
                % "；".join(parts), detail)

    # --- R6 同范围 --------------------------------------------------------
    # 全楼门窗表 对 单层平面点数，必然不等，但两侧都对。这不是冲突，是范围不同。
    qa = str(a.get("scope") or "").strip()
    qb = str(b.get("scope") or "").strip()
    if qa and qb and qa != qb:
        return (UNVERIFIABLE,
                "范围不同（%s vs %s）——全楼/单层、单幅/双幅的计数必然不等，"
                "这不是冲突；须换算到同一范围再比" % (qa, qb), detail)

    # --- 数值写法歧义 -----------------------------------------------------
    for tag, side in (("A", a), ("B", b)):
        if _is_ambiguous(side.get("value")):
            return (UNVERIFIABLE,
                    "%s 侧数值 %r 小数点歧义（逗号后 3 位：可能是小数，也可能是千分位）"
                    "——请改写为 `3.605` 或 `3605` 再比，工具不替你猜"
                    % (tag, side.get("value")), detail)

    # --- R4 缺值不等于跳过 ------------------------------------------------
    va, vb = _num(a.get("value")), _num(b.get("value"))
    detail.update({"a": va, "b": vb})
    if va is None and vb is None:
        return (UNVERIFIABLE, "两侧都没有该参数的值", detail)
    if va is None:
        return (UNVERIFIABLE, "A 侧（%s）无该参数值，B 侧=%s——缺项不得当作一致"
                % (a.get("sheet"), vb), detail)
    if vb is None:
        return (UNVERIFIABLE, "B 侧（%s）无该参数值，A 侧=%s——缺项不得当作一致"
                % (b.get("sheet"), va), detail)

    # --- R2 同基准：单位换算必须显式 -------------------------------------
    ua = str(a.get("unit") or "").strip().lower()
    ub = str(b.get("unit") or "").strip().lower()
    detail.update({"unit_a": ua, "unit_b": ub})
    conv = None
    if ua and ub and ua != ub:
        if ua in LEN_TO_MM and ub in LEN_TO_MM:
            conv = {"from": ua, "to": ub,
                    "factor": LEN_TO_MM[ua] / LEN_TO_MM[ub],
                    "note": "同维度长度换算，已显式记录"}
            # 统一折算到 mm 再比，避免换算方向搞反
            va_mm, vb_mm = va * LEN_TO_MM[ua], vb * LEN_TO_MM[ub]
        else:
            return (UNVERIFIABLE,
                    "单位不可比（%s vs %s）——跨维度或词表外单位不做静默换算；"
                    "须由项目规则给出换算依据" % (ua, ub), detail)
    elif not ua and not ub:
        # 两侧都没写单位：按标注原值比，记一笔（至少两侧同在一种假设下）
        va_mm, vb_mm = va, vb
        detail["unit_note"] = "两侧均未写单位，按原值比较"
    elif not ua or not ub:
        # 只有一侧写了单位：不能拿另一侧的系数去缩放两侧。
        # 旧实现 3.6 m vs 3600（无单位）会把两侧同乘 1000，差 3,596,400 —— 假红。
        return (UNVERIFIABLE,
                "只有一侧写了单位（%s / %s）——单位不明不折算，补单位再比"
                % (ua or "空", ub or "空"), detail)
    else:
        f = LEN_TO_MM.get(ua, LEN_TO_MM.get(ub, 1.0))
        va_mm, vb_mm = va * f, vb * f

    detail["convert"] = conv
    detail["a_mm"], detail["b_mm"] = round(va_mm, 4), round(vb_mm, 4)

    # --- R3 容差 ----------------------------------------------------------
    diff = abs(va_mm - vb_mm)
    detail["diff_mm"] = round(diff, 4)
    if tol_cls == "angle":
        tol_step = abs(va_mm) * ANGLE_REL_TOL
        tol_desc = "角度 ±0.1%"
    else:
        tol_step = TOL.get(tol_cls, TOL["structure"])[0]
        tol_desc = "%s（%s）" % (TOL.get(tol_cls, TOL["structure"])[1], tol_cls)
    detail["tol"] = round(tol_step, 4)

    # 差值用 _fmt：%.3f 会把 1e-7 印成「差 0.000 超容差 0」，自相矛盾
    if diff <= tol_step + 1e-9:
        return (PASS, "一致（差 %s ≤ 容差 %s）——%s%s"
                % (_fmt(diff), _fmt(tol_step), tol_desc,
                   "（跨版本对照，须确认该参数未被修订触及）" if cross_rev else ""),
                detail)
    return (CONFLICT,
            "%s：A 侧=%s %s（%s）、B 侧=%s %s（%s），差 %s 超容差 %s —— %s%s；"
            "该参数的下游量在裁决前不得出交付值（H33）"
            % (param, _fmt(va), ua or "-", a.get("sheet"), _fmt(vb), ub or "-",
               b.get("sheet"), _fmt(diff), _fmt(tol_step), tol_desc,
               "（两侧版本不同，先核修订表/修订云线，别当图纸矛盾）" if cross_rev else ""),
            detail)


def _fmt(x):
    if x is None:
        return "-"
    return ("%.4f" % x).rstrip("0").rstrip(".") if isinstance(x, float) else str(x)


# --------------------------------------------------------------------------
def cmd_init(a):
    pairs = []
    for pid, grp, param, tol_cls, detail in MATRIX:
        pairs.append({
            "id": pid, "group": GROUP_LABEL[grp], "param": param,
            "tol": tol_cls, "what": detail,
            "a": {"sheet": "", "loc": "", "value": "", "unit": "mm", "rev": "",
                  "hash": "", "scope": ""},
            "b": {"sheet": "", "loc": "", "value": "", "unit": "mm", "rev": "",
                  "hash": "", "scope": ""},
            "note": "",
        })
    spec = {
        "project_id": a.project or "", "gate": GATE_ID, "tool_version": VERSION,
        "instruction": ("填两侧取值。unit 必须写（mm/cm/m），别留空；"
                        "派生值对照（分段累加 vs 总标注）把该项 tol 改成 elevation_derived；"
                        "计数项填 scope（单层/全楼/单幅/双幅），两侧范围不同不算冲突；"
                        "不适用项把 note 写成 N/A + 理由（不许删项，删了就看不出没查）"),
        "pairs": pairs,
    }
    p = a.spec
    d = os.path.dirname(os.path.abspath(p))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    if os.path.exists(p) and not a.force:
        print("[WARN] 已存在，未覆盖：%s（加 --force 覆盖）" % p)
        return 0
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(spec, ensure_ascii=False, indent=2))
    print("[OK] 已生成校验矩阵模板：%s（%d 项）" % (p, len(pairs)))
    print("     不适用项请写 note=\"N/A + 理由\"，不要删项")
    return 0


def cmd_run(a):
    if not os.path.exists(a.spec):
        print("[ERR] 找不到 spec：%s" % a.spec)
        return 2
    try:
        with io.open(a.spec, encoding="utf-8") as f:
            spec = json.load(f)
    except Exception as e:
        print("[ERR] spec 不是合法 JSON：%s" % e)
        return 2

    pairs = spec.get("pairs") or []
    if not isinstance(pairs, list) or not pairs:
        print("[ERR] pairs 为空——空矩阵判 exit 2（没查不等于通过）")
        return 2

    only = None
    if a.only:
        only = set(x.strip().upper() for x in a.only.split(",") if x.strip())

    results = []
    for pair in pairs:
        pid = str(pair.get("id") or "").strip().upper()
        if only and pid not in only:
            continue
        note = str(pair.get("note") or "")
        if note.strip().upper().startswith("N/A"):
            results.append({"id": pid, "param": pair.get("param"),
                            "verdict": "N/A", "msg": note.strip(), "detail": {}})
            continue
        if _is_blank(pair):
            results.append({"id": pid, "param": pair.get("param"),
                            "verdict": UNFILLED, "msg": "模板未填", "detail": {}})
            continue
        verdict, msg, detail = check_pair(pair, strict_rev=a.strict_rev)
        results.append({"id": pid, "param": detail.get("param") or pair.get("param"),
                        "verdict": verdict, "msg": msg, "detail": detail})

    if only and not results:
        print("[ERR] --only %s 没有匹配到任何校验项" % a.only)
        return 2

    n_pass = sum(1 for r in results if r["verdict"] == PASS)
    n_conf = sum(1 for r in results if r["verdict"] == CONFLICT)
    n_unv = sum(1 for r in results if r["verdict"] == UNVERIFIABLE)
    n_rev = sum(1 for r in results if r["verdict"] == RULE_CONFLICT)
    n_na = sum(1 for r in results if r["verdict"] == "N/A")
    n_blank = sum(1 for r in results if r["verdict"] == UNFILLED)
    blanks = [r["id"] for r in results if r["verdict"] == UNFILLED]
    n_cross = sum(1 for r in results
                  if (r.get("detail") or {}).get("cross_rev"))

    print("=" * 78)
    print("跨图一致性校验  %s   spec=%s" % (GATE_ID, os.path.basename(a.spec)))
    print("=" * 78)
    for r in results:
        if r["verdict"] in (PASS, "N/A", UNFILLED):
            continue
        print("[%s] %-4s %s" % (r["verdict"], r["id"], r["msg"]))
    if n_blank:
        print("[%s] %d 项未填：%s" % (UNFILLED, n_blank, "、".join(blanks)))
    if n_conf == 0 and n_unv == 0 and n_rev == 0 and n_blank == 0:
        print("[OK] 所有可比项一致")
    print("-" * 78)
    print("通过 %d / 冲突 %d / 无法校验 %d / 版本分裂 %d / 不适用 %d / 未填 %d （共 %d 项）"
          % (n_pass, n_conf, n_unv, n_rev, n_na, n_blank, len(results)))
    if n_cross:
        print("其中 %d 项为**跨版本对照**（两侧 rev 不同）——PASS 不等于同版确认，"
              "须按修订表/修订云线确认该参数未被修订触及" % n_cross)
    if n_unv or n_rev:
        print("注意：无法校验 %d 项、版本分裂 %d 项**不是通过**，是没查到——"
              "交付报告须原样列出缺什么" % (n_unv, n_rev))
    if n_blank:
        print("注意：未填 %d 项同样是没查，不能计入通过；补填或写 note=\"N/A+理由\"" % n_blank)

    # 全篇未填 = 没跑。与"空表矩阵判 exit 2"同一条纪律：没查不给绿灯。
    if n_blank == len(results):
        print("[ERR] 矩阵全篇未填——判 exit 2（没查不等于通过）")
        return 2
    if n_pass == 0:
        print("[ERR] 没有任何一项可比且一致（通过 0）——判 exit 2，"
              "这不是「无冲突」，是「什么都没证实」")
        return 2

    if a.out:
        payload = {
            "gate_id": GATE_ID, "tool_version": VERSION,
            "project_id": spec.get("project_id") or "",
            "spec": os.path.abspath(a.spec),
            "executed": True,
            "checked_count": len(results),
            "expected_count": len(pairs),
            "summary": {"pass": n_pass, "conflict": n_conf, "unverifiable": n_unv,
                        "version_split": n_rev, "na": n_na, "unfilled": n_blank,
                        "unfilled_ids": blanks},
            "results": results,
        }
        d = os.path.dirname(os.path.abspath(a.out))
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        print("报告：%s" % a.out)
    return exit_of(n_pass, n_conf, len(results), n_na)


def exit_of(n_pass, n_conf, n_total, n_na=0):
    """结论 → 退出码。抽成函数是为了能被 selftest 断言——
    上一版把 `return 1 if n_conf else 0` 直接写在 cmd_run 里，selftest 只测判据
    不测退出码，于是"退出码恒 0"的变异体可以全绿通过（评审实测逃逸）。"""
    if n_conf:
        return 1
    if n_pass == 0:
        return 2
    return 0


# --------------------------------------------------------------------------
def _mk(pid, va, vb, ua="mm", ub="mm", rev_a="V0", rev_b="V0",
        ha="h1", hb="h1", sa="图A/1", la="PLAN", sb="图B/2", lb="COUPE"):
    return {"id": pid,
            "a": {"sheet": sa, "loc": la, "value": va, "unit": ua, "rev": rev_a, "hash": ha},
            "b": {"sheet": sb, "loc": lb, "value": vb, "unit": ub, "rev": rev_b, "hash": hb}}


def _hash_split():
    """同一个图号、两份文件、内容不同 —— 这才是真版本分裂。"""
    p = _mk("C1", 12000, 12000, ha="aaa", hb="bbb")
    p["b"]["sheet"] = p["a"]["sheet"]
    p["b"]["loc"] = "COUPE A-A 另一版"
    return p


def _ztol(va, vb, tol):
    p = _mk("Z9", va, vb)
    p["tol"] = tol
    return p


def _scope_pair():
    p = _mk("A1", 40, 40)
    p["a"]["scope"] = "全楼"
    p["b"]["scope"] = "单层"
    return p


def _derived(va, vb):
    p = _mk("C2", va, vb)
    p["tol"] = "elevation_derived"
    return p


def cmd_selftest(a):
    """自证：基线必须全过；注入的每一类缺陷必须被拦住；
    **且合法项不得出现在 findings 里**（反向断言——误报比漏报更伤）。"""
    cases = [
        # (标签, pair, 期望结论)
        ("基线：层高两侧一致 3600",              _mk("B1", 3600, 3600), PASS),
        ("层高说明 3600 vs 剖面 3300",           _mk("B1", 3600, 3300), CONFLICT),
        ("层高差 1 mm（标高 ±0 必须抓）",         _mk("B1", 3600, 3601), CONFLICT),
        ("结构尺寸差 0.5 mm（不放公差）",         _mk("C3", 1200, 1200.5), CONFLICT),
        ("装修面层差 3 mm（±5 内应放行）",        _mk("B3", 900, 903), PASS),
        ("装修面层差 8 mm（超 ±5 应抓）",         _mk("B3", 900, 908), CONFLICT),
        ("单位 mm vs m 的等价值（3600 / 3.6）",   _mk("B1", 3600, 3.6, "mm", "m"), PASS),
        ("单位不可比（mm vs m³）",                _mk("B1", 3600, 3.6, "mm", "m³"), UNVERIFIABLE),
        ("缺 B 侧值",                             _mk("B2", 2100, ""), UNVERIFIABLE),
        ("两侧都缺值",                            _mk("B2", "", ""), UNVERIFIABLE),
        ("不同图册 rev 不同（跨版，应继续比）",   _mk("B1", 3600, 3600, rev_a="V0", rev_b="V1"), PASS),
        ("同图号两内容（哈希不同）",              _hash_split(), RULE_CONFLICT),
        ("不同图号哈希不同（常态，不代表分裂）",  _mk("C1", 12000, 12000, ha="aaa", hb="bbb"), PASS),
        ("矩阵外自定义项未声明容差",              _mk("Z9", 1, 1), UNVERIFIABLE),
        ("矩阵外自定义项声明了容差",              _ztol(1, 1, "finish"), PASS),
        # 法语/北非图册写法（本项目主打法语图册，这条必须过）
        ("法语小数逗号 3,60 m vs 3600 mm",         _mk("B1", "3,60", 3600, "m", "mm"), PASS),
        ("千分位空格 1 250 vs 1250",               _mk("C3", "1 250", 1250), PASS),
        ("逗号后 3 位（3,605）判歧义不猜",         _mk("B1", "3,605", 3605, "m", "mm"), UNVERIFIABLE),
        # 单位缺失不得拿另一侧系数缩放两侧
        ("只有一侧写单位（3.6 m vs 3600 空）",     _mk("B1", 3.6, 3600, "m", ""), UNVERIFIABLE),
        # 范围不同不是冲突
        ("全楼表 vs 单层平面（scope 不同）",        _scope_pair(), UNVERIFIABLE),
        # 派生标高 ±10 mm 档
        ("派生标高差 8 mm（±10 内应放行）",        _derived(9882, 9890), PASS),
        ("派生标高差 15 mm（超 ±10 应抓）",        _derived(9882, 9897), CONFLICT),
    ]
    # 缺溯源的单独构造（_mk 会给齐 sheet/loc）
    p = _mk("A2", 1500, 1500)
    p["b"]["loc"] = ""
    cases.append(("B 侧缺图面位置（R1）", p, UNVERIFIABLE))

    ok = 0
    for label, pair, expect in cases:
        got, msg, _ = check_pair(pair)
        hit = (got == expect)
        ok += 1 if hit else 0
        print("  [%s] %-32s → %-13s %s" % ("符合" if hit else "不符", label, got,
                                           "" if hit else "（期望 %s）: %s" % (expect, msg)))

    # 反向断言：等价换算与容差内的项，绝不许出现在 CONFLICT 里
    reverse = [
        ("3600 mm vs 3.6 m 等价", _mk("B1", 3600, 3.6, "mm", "m")),
        ("903 vs 900（±5 内）", _mk("B3", 900, 903)),
        ("两侧完全相等", _mk("C1", 12000, 12000)),
    ]
    for label, pair in reverse:
        got, _, _ = check_pair(pair)
        hit = (got == PASS)
        ok += 1 if hit else 0
        print("  [%s] 反向断言 %-26s → %-13s %s"
              % ("符合" if hit else "不符", label, got, "" if hit else "（不得判 CONFLICT）"))

    # 未填 / 不可比的区分（模板全空时不能被读成"通过"）
    blank = {"id": "B1", "a": {"sheet": "", "loc": "", "value": "", "unit": "mm",
                               "rev": "", "hash": ""},
             "b": {"sheet": "", "loc": "", "value": "", "unit": "mm",
                   "rev": "", "hash": ""}}
    hit = _is_blank(blank)
    ok += 1 if hit else 0
    print("  [%s] 全空模板项识别为 UNFILLED           → %s"
          % ("符合" if hit else "不符", "是" if hit else "否"))
    filled = _mk("B1", 3600, 3300)
    hit2 = not _is_blank(filled)
    ok += 1 if hit2 else 0
    print("  [%s] 已填项不得被误判为 UNFILLED          → %s"
          % ("符合" if hit2 else "不符", "是" if hit2 else "否"))

    # strict_rev 打开时，rev 不同必须回到"先定版"
    got, _, _ = check_pair(_mk("B1", 3600, 3600, rev_a="V0", rev_b="V1"), strict_rev=True)
    hit = (got == RULE_CONFLICT)
    ok += 1 if hit else 0
    print("  [%s] --strict-rev 下 rev 不同判 RULE_CONFLICT → %s"
          % ("符合" if hit else "不符", got))

    # 退出码（上一版 selftest 完全不测这一层，实测"退出码恒 0"变异体全绿逃逸）
    exits = [
        ("有冲突 → 1",            exit_of(3, 1, 16), 1),
        ("全一致 → 0",            exit_of(16, 0, 16), 0),
        ("全篇未填 → 2",          exit_of(0, 0, 16), 2),
        ("全篇 N/A → 2（等于没查）", exit_of(0, 0, 16, n_na=16), 2),
        ("只有不可比 → 2",        exit_of(0, 0, 16), 2),
    ]
    for label, got_x, want_x in exits:
        hit = (got_x == want_x)
        ok += 1 if hit else 0
        print("  [%s] 退出码 %-26s → %d（期望 %d）"
              % ("符合" if hit else "不符", label, got_x, want_x))

    total = len(cases) + len(reverse) + 2 + 1 + len(exits)
    print()
    print("SELFTEST %d/%d（含 %d 条反向断言 + 2 条未填识别 + %d 条退出码断言）"
          % (ok, total, len(reverse), len(exits)))
    if ok != total:
        print("SELFTEST FAIL")
        return 1
    print("SELFTEST PASS")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="跨图一致性校验 G-XD（%s）" % GATE_ID)
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("init", help="生成 22 项校验矩阵模板")
    p1.add_argument("--spec", required=True)
    p1.add_argument("--project", default="")
    p1.add_argument("--force", action="store_true")
    p1.set_defaults(func=cmd_init)

    p2 = sub.add_parser("run", help="执行校验")
    p2.add_argument("--spec", required=True)
    p2.add_argument("--out", default="")
    p2.add_argument("--only", default="", help="只跑指定项，逗号分隔，如 B1,C2")
    p2.add_argument("--strict-rev", action="store_true", dest="strict_rev",
                    help="两侧 rev 不同即判 RULE_CONFLICT（默认跨版对照；"
                         "仅在合同要求同版对照时用）")
    p2.set_defaults(func=cmd_run)

    p3 = sub.add_parser("selftest", help="自证（含反向断言）")
    p3.set_defaults(func=cmd_selftest)

    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    return a.func(a)


if __name__ == "__main__":
    # 顶层兜底：崩溃必须退 2（门禁没跑成），不能退 1——退 1 会被读成"查到冲突"。
    # 与 drafter_qto.py 同一约定；不加这一层，非法 spec 会带着 traceback 退 1。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as _e:
        print("[ERR] 工具执行失败（exit 2，不是检查不通过）：%s: %s"
              % (type(_e).__name__, _e))
        sys.exit(2)
