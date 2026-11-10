#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qto_vocab.py -- 口径词表与量纲判据的**单一事实来源**。

被下列脚本共用，改表只需改这里：
  - gate_dimension_wording.py   (跨列自洽门禁 G-DW)
  - drafter_qto.py              (绘图员读数台账自检)

=============================================================================
口径是两个维度（本模块最重要的设计）
=============================================================================
  范围口径 SPAN     —— 这个数覆盖多大范围：全桥 / 单幅 / 两侧 / 每座 / 每根…
  证据强度 EVIDENCE —— 这个数哪来的：图纸确认 / 推算 / 估算 / 待核

现实中这两件事常被塞进同一个格子（"图读｜全桥"），结果是：
  - 只写"全桥"：知道量多大，不知道准不准；
  - 只写"推算"：知道准不准，不知道算的是哪一段。
所以本模块把它们拆成两张表、两套检查。

=============================================================================
证据强度四级的含义
=============================================================================
  图纸确认  图面直接读得（尺寸链、专用表、图例、标注文字）
  推算      由几何/数量闭合关系导出，式子已写在计算式列
  估算      图面无依据，按规范或经验取值 —— **必须挂 RFI**
  待核      图面缺失且不拟取值，等业主/设计回复
"""

from __future__ import print_function

import re
import math

try:
    from qto_core import (parse_decimal, DataValidationError,
                          load_unit_alias_registry, unit_dimensions, normalize_unit, unit_info)
except ImportError:  # 兼容作为独立文件分发
    parse_decimal = None
    DataValidationError = ValueError
    load_unit_alias_registry = None
    unit_dimensions = None
    normalize_unit = lambda value: str(value or "").strip().casefold().replace(" ", "")
    unit_info = None

# --------------------------------------------------------------------------
# 维度一：范围口径
# --------------------------------------------------------------------------
SPAN_WORDS = ("全桥", "单幅", "双幅", "两侧", "单侧", "每座", "单座",
              "总计", "单件", "合计", "每跨", "每延米", "全段", "每根")

# --------------------------------------------------------------------------
# 维度二：证据强度
# --------------------------------------------------------------------------
SCOPE_WORDS = ("图纸确认", "推算", "估算", "待核")
# 兼容既有交付物里的写法（推定=估算、敞口=待核、图读=图纸确认）
EVIDENCE_ALIAS = {
    "图读": "图纸确认",
    "推定": "估算",
    "敞口": "待核",
}
EVIDENCE_WORDS = tuple(SCOPE_WORDS) + tuple(EVIDENCE_ALIAS.keys())
ESTIMATE_WORDS = ("估算", "推定")

# 伪闭环词：读起来像已完成，实则掩盖未解决问题
BANNED_WORDS = ("已计量", "已确认", "复核无误", "无误", "已核实", "已复核", "无问题")

# --------------------------------------------------------------------------
# 取证方法六档（method 字段）—— 回答"这个数是怎么取出来的"
# --------------------------------------------------------------------------
# v4.6.0：把原 `render_read` 一档拆成两档。原口径把"高倍切块读到明确数字"
# 和"缩略图目测估长度"并作 weak，实战里会把取证行为扭向"宁可推尺寸链也不读图"，
# 而尺寸链的前提（关系读对）本身才是弱项。分档后：
#   render_read_text  读到字 → medium，可作交付证据，须带 tile 坐标 + 字高
#   render_read_est   没字可读 → weak，必须挂 RFI
METHODS = {
    "schedule_direct":  ("专用表直读", "strong", "支座表/桩位图/钢筋表/图例/明细表上的数字"),
    "text_layer":       ("文本层解析", "strong", "PDF/DWG 有真文字层，直接解析标注文本"),
    "chain_derived":    ("尺寸链闭合", "medium", "由分段尺寸求和或均分反算得到"),
    "vector_cluster":   ("矢量聚类计数", "medium", "按填充色+尺寸+长宽比过滤图元后空间聚类计数"),
    "render_read_text": ("切块读字", "medium", "高倍切块后读到图面明确数字/文字，须记 tile 坐标与字高"),
    "render_read_est":  ("渲染估读", "weak", "无标注可读，靠像素量取或目测估长"),
}
WEAK_METHODS = ("render_read_est",)
MEDIUM_METHODS = ("chain_derived", "vector_cluster", "render_read_text")

# v4.6.0 迁移规则：历史台账里的 `render_read` 一律按 `render_read_est` 计（从低不从高）。
# 不批量改历史 CSV——改历史记录等于篡改证据链；只在读取时归一化并提示。
METHOD_ALIAS = {"render_read": "render_read_est"}

# `render_read_text` 必须留下可复现的取证坐标与字高，否则它退化成估读。
# 认三种写法：tile 文件名/编号、像素坐标、显式字高。
TILE_LOCATOR_RE = re.compile(r"(tile|切片|块号|\d+\s*px|像素|字高|@\s*\d+(?:\.\d+)?)",
                             re.IGNORECASE)

# --------------------------------------------------------------------------
# 单位 → 量纲
# --------------------------------------------------------------------------
# v2.1.0：补齐法语/欧标写法。此前只认英文缩写，导致 `m³`/`m²`/`U`/`MC`
# 全部落到"未知单位"，量纲判据整行静默失效（法语图册的默认写法全军覆没）。
# 纪律：**词表外单位 = 未校验，不是通过**（见 validate_unit）。
# Global vocabulary is derived from the calculation core. Context-sensitive aliases
# (notably legacy ``CM``) are intentionally excluded unless an approved project
# registry is explicitly supplied; a dimension-checking gate must never guess.
if unit_dimensions is not None:
    UNIT_DIM = {alias.upper(): dimension for alias, dimension in unit_dimensions().items()}
else:  # minimal fallback for standalone distribution only
    UNIT_DIM = {
        "M": "length", "MM": "length", "LM": "length",
        "M2": "area", "SM": "area", "M3": "volume", "MC": "volume",
        "KG": "mass", "T": "mass", "MT": "mass", "EA": "count", "U": "count",
    }
# Project aliases are never stored in this module-level mapping. Resolve them from
# a validated registry at the point of use to avoid cross-project state leakage.

DIM_LABEL = {"volume": "体积", "area": "面积", "length": "长度",
             "mass": "重量", "count": "数量", "item": "包干项"}

# 数值 + 紧随其后的工程量单位（先长后短，避免 m 抢了 m³）
UNIT_TOKENS = ("m³", "m3", "立方米", "m²", "m2", "平方米",
               "延米", "kg", "t", "座", "根", "片", "道", "套", "个")
NUM_UNIT_RE = re.compile(
    r"(-?\d+(?:\.\d+)?)\s*(" + "|".join(re.escape(u) for u in UNIT_TOKENS) + r")",
    re.IGNORECASE)
# 含量单位（kg/m³、t/m³）—— 描述"每方含量"，不是这个工程量本身的量纲
CONTENT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:kg|t)\s*/\s*(?:m³|m3|立方米)", re.IGNORECASE)
PAREN_RE = re.compile(r"[（(\[][^）)\]]*[）)\]]")
# 工程量计算式的特征：基数 × 含量 的乘法链
MUL_CHAIN_RE = re.compile(r"\d\s*[×*]\s*\d")

# 证据去处：出现任一类即视为"有可核查来源"
SOURCE_PATTERNS = (
    ("图号", re.compile(r"图\s*[:：]?\s*\d|\d{4,}\s*[-–]\s*[A-Z0-9]{2,}|[A-Z]{2,4}\d{2,}\s*[-–]")),
    ("图纸编号", re.compile(r"23041|23040|\bS-\d|\bA-\d|\bB-\d|V0")),
    ("规范/标准", re.compile(r"BAEL|BPEL|RPOA|EUROCODE|Fascicule|AASHTO|BS\s?EN|EN\s?\d|ACI|ASTM|UIC")),
    ("业主清单编号", re.compile(r"业主清单\s*[\d.]+|\b\d+\.\d{2}\s*=")),
    ("RFI 编号", re.compile(r"RFI\s*[-#]?\s*\w+")),
    ("台账编号", re.compile(r"台账\s*#?\d+")),
)


# --------------------------------------------------------------------------
# 判据函数
# --------------------------------------------------------------------------
_SUPERSCRIPT_FOLD = str.maketrans({"³": "3", "²": "2", "¹": "1"})


def norm_unit(raw):
    """剥离括注/空白/点号并大写，得到规范单位符号。

    单位词表只负责清理括注和格式；清理后的 `CM` 仍是歧义符号，不能因清理
    而自动认定为体积。量纲判定必须交由核心单位注册表，项目特定别名还需经过
    已批准的项目单位别名注册表。归一化必须发生在判据侧，而不只是告警侧。

    v2.1.0：上标折平（m³→M3、m²→M2）。此前 `.upper()` 把 `m³` 变成 `M³`，
    既不在词表里也不等于 `M3`，于是"最自然的写法反而不认"。
    """
    s = str(raw or "").strip()
    s = PAREN_RE.sub("", s)
    s = s.translate(_SUPERSCRIPT_FOLD)
    s = s.replace(" ", "").replace(".", "").upper()
    return s


def _active_aliases(unit_registry=None, project_id=None):
    if not unit_registry:
        return {}
    if isinstance(unit_registry, dict):
        return unit_registry
    if load_unit_alias_registry is None:
        raise ValueError("qto_core is required to load a project unit-alias registry")
    return load_unit_alias_registry(unit_registry, project_id=project_id)


def dim_of_unit(unit, unit_registry=None, project_id=None):
    normalized = norm_unit(unit)
    dim = UNIT_DIM.get(normalized)
    if dim is not None:
        return dim
    try:
        aliases = _active_aliases(unit_registry, project_id)
    except Exception:
        return None
    alias_key = normalize_unit(normalized) if normalize_unit is not None else normalized.casefold()
    alias = aliases.get(alias_key)
    if not alias:
        return None
    if unit_info is not None:
        try:
            return unit_info(unit, unit_registry=aliases, project_id=project_id)[0]
        except Exception:
            return None
    return alias.get("_dimension")


def validate_unit(raw, unit_registry=None, project_id=None):
    """返回 (规范单位, 错误信息)。单位别名必须来自已批准的项目注册表。"""
    u = norm_unit(raw)
    if not u:
        return u, None
    if u in UNIT_DIM:
        return u, None
    try:
        aliases = _active_aliases(unit_registry, project_id)
    except Exception as exc:
        return u, ("项目单位别名注册表不可用：%s" % exc)
    key = normalize_unit(u) if normalize_unit is not None else u.casefold()
    if key in aliases:
        if unit_info is not None:
            try:
                unit_info(u, unit_registry=aliases, project_id=project_id)
            except Exception as exc:
                return u, "项目单位别名无法解析：%s" % exc
        return u, None
    return u, ("未知或歧义单位 %r（规范化后 %r）。词表外/歧义单位不得静默放行；"
               "如为项目约定写法，请提供已批准且限定项目/地区/语言的单位别名注册表。"
               % (raw, u))


def dim_of_token(tok):
    t = str(tok or "").strip().lower()
    if t in ("m³", "m3", "立方米"):
        return "volume"
    if t in ("m²", "m2", "平方米"):
        return "area"
    if t in ("延米", "m长"):
        return "length"
    if t in ("kg", "t"):
        return "mass"
    if t in ("座", "根", "片", "道", "套", "个"):
        return "count"
    return None


def parse_num(s):
    """兼容旧 API 的严格数值解析；拒绝 NaN/Infinity 和模糊千分位。"""
    if parse_decimal is not None:
        try:
            value = parse_decimal(s, field_name="quantity")
            return float(value) if value is not None else None
        except Exception:
            return None
    try:
        value = float(str(s).strip())
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def find_span_words(text):
    return [w for w in SPAN_WORDS if w in str(text or "")]


def find_evidence_words(text):
    """返回命中的证据强度词（含别名），按强度从强到弱排序输出。"""
    t = str(text or "")
    hits = [w for w in EVIDENCE_WORDS if w in t]
    order = ["图纸确认", "图读", "推算", "推定", "估算", "待核", "敞口"]
    return sorted(hits, key=lambda w: order.index(w) if w in order else 99)


def canonical_evidence(text):
    """把命中的证据强度词归一到四级标准词。"""
    hits = find_evidence_words(text)
    if not hits:
        return None
    raw = hits[0]
    return EVIDENCE_ALIAS.get(raw, raw)


def find_source_labels(text):
    return [lbl for lbl, rx in SOURCE_PATTERNS if rx.search(str(text or ""))]


def find_banned(text):
    return [w for w in BANNED_WORDS if w in str(text or "")]


# --------------------------------------------------------------------------
# DW-1 量纲矛盾判据
# --------------------------------------------------------------------------
def find_dimension_conflict(unit, value, texts, unit_registry=None, project_id=None):
    """返回 (文本中复述该量的数字, 其单位, 量的量纲) 或 None。

    判据要点（[R-DW-1] 实测修正）：
      只指证"文本里数值 == 工程量列数值"的那个数字的后缀单位。
      这样既抓得住"单位列 SM 而口径写 3252 m³"的真矛盾，
      又不会被"1.05m 高的道砟挡墙"这类描述性尺寸误伤。
    """
    dim_unit = dim_of_unit(unit, unit_registry=unit_registry, project_id=project_id)
    if not dim_unit or norm_unit(unit) == "EA":
        return None
    v = parse_num(value)
    if v is None:
        return None
    blob = " ".join(str(t or "") for t in texts)
    blob = CONTENT_RE.sub(" ", blob)      # 先剥掉 kg/m³ 这类含量单位
    for m in NUM_UNIT_RE.finditer(blob):
        try:
            num = float(m.group(1))
        except ValueError:
            continue
        if abs(num - v) > max(1e-6, abs(v) * 1e-6):
            continue
        d = dim_of_token(m.group(2))
        if d and d != dim_unit:
            return (num, m.group(2), dim_unit, d)
    return None