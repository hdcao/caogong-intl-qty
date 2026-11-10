#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G-OFF — 表外缺席门禁（v4.7.0）

借鉴来源：ccj-qty v3.3.0 的 G17 / G18 / 图册覆盖率闸门 / 敞口四态。
本技能 v4.6.2 之前的所有门禁都在验「表内自洽」：来源、公式、单位、汇总、
口径、跨图、数据源语义。**漏掉的东西不在表内，所以永远不会被校验到。**

本门禁只问一个问题的四个面：**有没有根本没算到的那一块？**

  COV    图册覆盖率 —— 整册空白/缺页时，那一册的量不是算错，是根本没有
  PAIR   必搭配件 + 应有项全集 —— 主体在、必随附属不在（整项漏）
  RATIO  含量比率   —— 构件在表里、量少了一块（分母疑漏 / 分子重复计）
  OI     敞口四态   —— 「挂账」是形容词不是状态；没有分类就没有收敛判据
  LEDGER 证据强度   —— 证据词强度不得超过图册状态码封顶（--ledger 传入）

三条设计红线（都是从本项目真实教训来的，不要"优化"掉）：

1. **不允许内置任何基准带 / 惯例值**。RATIO 的 band 必须由项目声明并写
   `declared_by`；没有就出 INFO「未校验」，明写跳过，绝不静默通过。
   把惯例值变成判据，正是 ×1.05 系数与「填方误用挖方表沙丘 6:1」的来路。
2. **"未核" 不等于 "通过"**。未登记页数记 WARN（`--strict` 下 FAIL），
   绝不记 PASS；无范围内图纸直接 exit 2（未运行），不是 0。
3. **比率能指路，不能定性**。带外只报「分母疑漏｜也可能是分子重复计」，
   由人下结论，脚本不替人裁决。

v4.7.0 整改（第三方审核 9 项致命/高缺陷，逐条对应 R 编号）：

  R5  退出码统一：五类判据统一返回 `(checked, cannot)`，任一 cannot != 0 → exit 2。
      旧版只把 COV 的 cannot 计入 rc，PAIR/其余类别 cannot 时仍 exit 0（致命逃逸）。
  R6  PAIR 参照系反转：新增 `systems`（bridge/culvert/lighting/building/road/mep），
      由「应有项全集 − 台账 class」判缺项（PAIR-6「应有而全无」），
      专抓「梁+支座整组未建」这种形态；原有「有 A 必查 B」降为附加判据。
  R7  受控词表 + 别名：解析 `references/disciplines-coverage.md` §7 得 CLASS_CANON，
      别名表内建（可被 doc["class_aliases"] 项目覆盖）；未识别 class → exit 2，
      防止「按文档规范名填写反而把门禁关掉」。
  R8  `checked == 0` 守卫：该类判据本应适用却一项未检 → CANNOT_RUN（exit 2）。
  R9  STATUS_CEILING 接线：COV 读 `status_code`；新增 `--ledger` 校验证据强度封顶；
      补法语 PRO/DCE/DOE/IFI/IFD（层级待核 NF P 02-001）。
  R10 COV 数值校验：`pages_*` 必须是非负 int（且非 bool），非法 → CANNOT_RUN 不抛异常；
      `pages_valid + blank_pages > pages_total` → FAIL「页数登记自相矛盾」。
  R11 RATIO 硬化：观测值支持字符串数字；无法转数 / 带与观测未匹配 → WARN「未校验」；
      报告输出「声明 N 条 / 实检 M 项 / 未匹配 K 项」。
  R12 占位词改结构性判据（语言无关）：`looks_sourced()` 要求长度 ≥ 8 且含
      图号/册号/条款/页码类线索；纯中文词表不再作为唯一防线。
  R13 敞口 0 条 + SCOPED：键缺失 → exit 2；`open_items_declared_zero: true`
      是「本项目确无敞口」的合法出路；SCOPED 必须声明待决数量或出处依据。

v4.7.1 误报归零（R6 的返工，硬门槛）：

  R6a 桥涵分部不混：`references/appurtenance-pairing.md` §6 目录树把 02.OUVRAGES D'ART
      （桥）与 03.BUSES（涵洞）列为两个独立分部 —— **有桥不必有涵**。故 bridge 系统
      剔除 culvert / head_wall / apron，涵洞归 culvert 系统。
  R6b 「应有」改为**条件应有**（专业上不存在绝对全集），SYSTEM_EXPECTED 改三档：
        required    缺 → FAIL  PAIR-6R（梁式桥的梁/支座/垫石/桥面板等）
        either      互斥组，组内 ≥1 即满足（pile_cap 与 spread_footing 二选一；
                    head_wall 与 wing_wall 二选一）→ 缺 FAIL PAIR-6E
        conditional 缺 → INFO PAIR-6C + 专业依据；optional 缺静默
      每一条 conditional / either 项都必须在 CONDITIONAL_WHY 里有专业依据，
      否则 C0b 自测报错 —— 防止「把所有东西塞进条件档换取零误报」。
  R6c 结构体系降级：doc["structure_system"]=beam|rigid_frame|integral|arch。
      刚构/整体式桥天然无支座、无垫石、无伸缩缝；拱桥无主梁。声明后自动降级为不判缺
      （PAIR-6D INFO）。**不声明则按梁式桥判**，降级不可白送（C33 守住这一点）。
  R6d 附加配对判据（PAIR-1/PAIR-2）与 required 档去重：required 已判缺的不再重复报；
      互斥组已满足的不再报（圆管涵用端墙替代翼墙时，不再报「有涵洞无翼墙」）。

退出码三态：0 通过 / 1 有 FAIL（--strict 时 WARN 计入）/ 2 门禁未运行。
"""
import argparse
import copy
import csv
import io
import json
import os
import re
import sys

SCHEMA = "off-table-gaps/v2"

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.normpath(os.path.join(HERE, "..", "references"))
COVERAGE_DOC = os.path.join(REF_DIR, "disciplines-coverage.md")

# ---------------------------------------------------------------------------
# 必搭配件表（附加判据，R6 之后不再是唯一判据）
# 精确匹配类目名，禁止前缀模糊（bearing 与 bearing_pedestal 用前缀匹配会互相冒充）。
# 规范名一律取 references/disciplines-coverage.md §7 受控词表，
# 别名解析见 CLASS_ALIASES（如 light_pole → lighting_pole）。
# 允许「并入母体」的口径，但须在 attributes_note 留可回查字据（R12）。
# ---------------------------------------------------------------------------
MANDATORY_COMPANIONS = [
    dict(required="bearing_pedestal", when_all=("bearing",),
         when_any=("pier_cap", "abutment"),
         why="支座与墩台都在却无垫石 —— 常被墩帽外包矩形吞掉，且混凝土等级通常高一级"),
    dict(required="bearing", when_all=("girder",), when_any=(),
         why="有梁无支座 —— 支座未建或未计。数量须**直读支座表/支座布置图**，"
             "不得按梁片数推算（appurtenance-pairing.md §2.5 案例 5：推算值作废）"),
    dict(required="approach_slab", when_all=("abutment",), when_any=(),
         why="有桥台无搭板 —— 台后附属整组漏"),
    dict(required="wing_wall", when_all=("culvert",), when_any=(),
         why="有涵洞无翼墙 —— 洞口附属整组漏"),
    dict(required="cable_handhole", when_all=("lighting_pole",), when_any=(),
         why="有灯杆无手孔 —— 基础/手孔/接地极陪随 1:1"),
    dict(required="earth_electrode", when_all=("lighting_pole",), when_any=(),
         why="有灯杆无接地极 —— 机电按系统树自电源侧向末端核对"),
    dict(required="pole_foundation", when_all=("lighting_pole",), when_any=(),
         why="有灯杆无基础 —— 高杆与 12m 杆基础差一个量级，不能一个 m³/根 打天下"),
]

# ---------------------------------------------------------------------------
# R6 应有项全集：system → {规范 class 名: 该技能 road-bridge 88 条目录中的对应条目}
# 参照系 = 「这个 system 应该出现哪些构件」，与台账里恰好写了什么无关。
# 这一步是 R6 的核心：旧版判据方向反了（有 girder 才查 bearing），
# 于是「梁 + 支座整组未建」实检 0 组、exit 0 完全逃逸。
#
# 条目来源：scripts/drafter_qto.py 的 CATALOGS["road-bridge"]（88 条，项目无关模板）。
# 归并原则：同一个 class 只要目录里有独立条目即为「应有项」；细目做语义归并，
# 但不用目录描述的字符串匹配（太脆），而是在此显式建表。
# ⚠️ 绝不复用 CATALOGS["building-unit"]：其实测 41 条是某沙特住宅项目专属检查项
#    （「楼板图绘图单位制」「木门 DT 系总量」），不是通用房建开项表。
# ⚠️「应有而全无」只出 WARN（--strict 下 FAIL），由人定性：桥无涵洞、桥台无桩
#    都是合法形态。确属「本项目不该有」时，用 system_waivers 留可回查字据：
#    {"bridge": {"culvert": "本桥为旱桥，B-1 涵洞分部不适用，见 C-1-2 横断面"}}
#    字据同样过 looks_sourced()（R12），不接受「不适用」「N/A」这种无出处说法。
# ---------------------------------------------------------------------------
SYSTEM_EXPECTED = {
    # ===== 桥梁（road-bridge 目录 A-x-x 段；不含 B 组涵洞，见下方说明）=====
    # 依据：appurtenance-pairing.md §6 目录树把 02.OUVRAGES D'ART（桥）与 03.BUSES（涵洞）
    #       列为两个独立分部 —— **有桥不必有涵**，故 culvert/head_wall/apron 一律不入 bridge。
    "bridge": dict(
        # required：梁式桥设立即必然存在的构件，缺即 FAIL
        # （grounded in road-bridge 88 条 A-1~A-7 段，可断言）
        required=[
            "pier_column",      # A-2-1 墩柱
            "abutment",         # A-2-3/4 台身台帽
            "bearing",          # A-3-1 支座
            "bearing_pedestal",  # A-3-2 支座垫石（本技能 appurtenance §2.2 明列必查）
            "girder",           # A-4-1 主梁
            "deck_slab",        # A-4-5 桥面板
            "parapet",          # A-6-1 护栏
            "approach_slab",    # A-6-4 桥头搭板
            "rebar",            # A-4-7 普通钢筋
        ],
        # either：互斥组，组内 ≥1 项存在即算满足（缺则 FAIL PAIR-6E）
        # 桩基承台 pile_cap 与扩大基础 spread_footing 二选一 ——
        # 桥台可能是扩大基础无桩（appurtenance §2.2 明示），二者不同时存在
        either=[("pile_cap", "spread_footing")],
        # conditional：条件应有，缺只报 INFO，不阻断（每条 why 给专业依据）
        conditional=[
            "pile",
            "pier_cap",         # A-2-2 墩帽盖梁（单柱墩/实体墩/刚构可无）
            "shear_key",
            "diaphragm",
            "end_diaphragm",
            "wet_joint",
            "expansion_joint",
            "deck_waterproofing",
            "asphalt_wearing",
            "drainage_spout",
            "bridge_road_marking",
            "bridge_lighting",
            "underbridge_lighting",
            "inspection_walkway",
            "wing_wall",
            "formwork",
            "excavation",
        ],
        # optional：仅特定桥型/合同范围，缺一律静默（连 INFO 都不出）
        optional=["ocl_mast_foundation"],
    ),

    # ===== 涵洞（B-1-x 段单列）=====
    "culvert": dict(
        # 洞口构造二选一：箱涵/盖板涵常用翼墙（wing_wall），圆管涵常用端墙/八字口
        # （head_wall）。故 head_wall 与 wing_wall 互为替代，用 either 组表达。
        required=["culvert", "rebar"],
        either=[("head_wall", "wing_wall")],
        conditional=["apron", "cut_off_wall", "approach_slab"],
        optional=[],
    ),

    # ===== 道路（C-1-x / D-1-x 段）=====
    "road": dict(
        required=["road_base"],       # C-1-3 路面结构层（不论沥青/水泥砼都必须有）
        either=[],                    # 路面材料由设计定，不设互斥必备组
        conditional=[
            "asphalt_surface", "pavement_marking",
            "excavation", "fill_compacted", "kerb",
            "drainage_pipe", "manhole", "gully",
        ],
        optional=[],
    ),

    # ===== 室外照明 / 路灯（E-1-x 段）=====
    "lighting": dict(
        required=["lighting_pole"],   # E-1-6 灯杆
        either=[],
        conditional=[
            "luminaire", "lamp_head",   # E-1-7 灯具/灯头（可按杆型组套计价并入灯杆，故不列必备）
            "pole_foundation", "anchor_bolt",
            "mv_cable", "package_substation",
            "feeder_pillar", "lighting_panel",
            "lv_cable", "ecc_conductor",
            "conduit", "cable_handhole", "direct_buried",
            "earth_electrode", "earth_grid",
        ],
        optional=[],
    ),

    # ===== 房建 =====
    # ⚠️ 本技能**没有**项目无关的房建开项目录（CATALOGS["building-unit"] 实测 41 条
    #    是某沙特住宅项目专属检查项，不可复用）。故房建一律按「条件应有」处理，
    #    required 留空 —— 不凭空断言语义，也不为了「有输出」而制造误报。
    "building": dict(
        required=[],
        either=[],        # 无项目无关目录 → 不设硬性必备组，全走条件档
        conditional=[
            "column", "rc_wall", "brick_wall", "block_wall",
            "footing_isolated", "footing_strip", "beam", "slab", "rebar", "formwork",
            "door", "window", "internal_partition", "floor_finish", "wall_finish",
            "paint", "roof_covering", "waterproofing", "suspended_ceiling",
            "tiling", "balustrade", "handrail", "external_works",
        ],
        optional=[],
    ),

    # ===== 机电（通用 §7 词表，同上：无项目无关目录 → 全条件）=====
    "mep": dict(
        required=[],
        either=[],        # 同上：无项目无关目录 → 不设硬性必备组
        conditional=[
            "pipe_run", "duct_run", "cable_tray",
            "pipe_fitting", "valve", "pipe_support", "sanitary_fixture",
            "sprinkler_main", "sprinkler_head",
            "duct_fitting", "grille", "fan", "ahu", "fcu",
            "cable", "conduit", "db", "luminaire", "socket", "switch", "earthing",
        ],
        optional=[],
    ),
}

# ---------------------------------------------------------------------------
# 条件应有（conditional）的专业依据：缺项时原样写进 INFO 消息。
# 规则：**不许出现没有依据的条件项** —— 没有 why 的条目说明它其实该进 required，
# 或该删掉。这条防止「先把所有东西塞进 conditional 换取零误报」。
# ---------------------------------------------------------------------------
CONDITIONAL_WHY = {
    # --- 桥梁 ---
    "pier_cap": "盖梁按墩型设置：单柱墩/实体墩/刚构墩梁固结无盖梁；双柱墩才有",
    "pile_cap": "承台为桩基础专有；桥台常为扩大基础无桩，故与 spread_footing 互斥",
    "spread_footing": "扩大基础为天然地基专有；桩基础地段用承台，故与 pile_cap 互斥",
    "pile": "仅桩基础桥设桩；桥台常为扩大基础无桩（appurtenance §2.2）",
    "shear_key": "抗震挡块按抗震设防烈度设置，低烈度区常不设",
    "diaphragm": "横隔梁按梁型设置：T/I 梁常设，箱梁以顶底板替代，板梁可不设",
    "end_diaphragm": "端横梁按支座布置与梁端构造设置，连续梁端部常并入湿接缝",
    "wet_joint": "湿接缝仅装配式梁（预制梁拼装）才有；现浇梁无此构造",
    "expansion_joint": "伸缩缝按联长与温度变形设置，连续刚构/整体式桥无伸缩缝",
    "deck_waterproofing": "桥面防水按桥型与环境等级设置，涵洞顶/旱桥可不设",
    "asphalt_wearing": "桥面铺装材料随设计（沥青/水泥砼）而定，厚度常另册注明",
    "drainage_spout": "泄水管按桥面纵横坡与跨河/跨路条件设置，短桥可集中排水",
    "bridge_road_marking": "桥面标线仅公路/城市桥梁设置，铁路桥与专用桥无",
    "bridge_lighting": "桥上照明按道路等级与业主运营要求设置",
    "underbridge_lighting": "桥下照明按跨越道路等级/业主景观要求设置，"
                            "独立分部 07.EQUIPEMENTS/ECLAIRAGE/SOUS-OUVRAGE（appurtenance §6）",
    "inspection_walkway": "检修道/防抛网按桥梁规模与管养要求设置，中小桥常不设",
    "wing_wall": "翼墙/锥坡按填高与地形设置：旱桥或挖方段可无（桥台侧）；"
                 "箱涵/盖板涵常用翼墙作洞口，圆管涵则用端墙，故与 head_wall 互斥（涵洞侧）",
    "formwork": "模板为措施项，按构件几何派生；是否单列取决于计价口径",
    "excavation": "基坑开挖按基础型式与埋深发生，岩石明挖浅基可并入土石方分部",
    "ocl_mast_foundation": "接触网支柱基础仅电气化铁路桥设",
    # --- 涵洞 ---
    "cut_off_wall": "隔水墙按地下水位与渗流条件设置，干燥地段可无",
    "head_wall": "端墙为圆管涵/盖板涵常用洞口型式；箱涵多用翼墙，故与 wing_wall 互斥",
    "apron": "进出口铺砌按水流冲刷与洞口防护要求设置，旱涵/填方涵可无",
    "approach_slab": "搭板按台后填高判定：<1.5 m 时常不设（appurtenance §2.1）",
    # --- 道路 ---
    "fill_compacted": "填方按纵断发生，纯挖方段无填方",
    "kerb": "路缘石按市政/公路横断面型式设置，土路肩段无",
    "drainage_pipe": "排水管按排水系统设计发生，漫流段可无",
    "manhole": "检查井随排水管设置",
    "gully": "雨水口随排水与路面汇水设置",
    "asphalt_surface": "面层材料随设计（沥青/水泥砼）而定",
    "pavement_marking": "标线按公路等级与运营要求设置",
    # --- 照明 ---
    "pole_foundation": "灯杆基础按杆型与地基设置，附墙安装段可无独立基础",
    "anchor_bolt": "锚栓仅法兰式杆基设；直埋杆无锚栓",
    "mv_cable": "中压电缆仅自带箱变/接入中压网时发生",
    "package_substation": "箱变仅在项目自建电源时设置，接市政低压时无",
    "feeder_pillar": "馈电柱按布置距离设置，短里程可直供",
    "lighting_panel": "照明配电盘按回路数与供电半径设置",
    "lv_cable": "低压电缆按回路表发生",
    "ecc_conductor": "接地导体 ECC 与主缆等长，按接地系统设计设置",
    "conduit": "保护管按敷设方式设置，直埋段无管",
    "cable_handhole": "手孔按穿缆距离与转弯设置",
    "direct_buried": "直埋段按敷设方式设置，穿管段无直埋",
    "earth_electrode": "接地极按接地电阻要求设置，可利用结构钢筋时减少",
    "earth_grid": "接地干线/网按接地系统设计设置",
    "lamp_head": "灯头随灯具型式",
    # --- 房建 ---
    "column": "柱按结构体系设置，剪力墙/筒体体系可以墙代柱",
    "rc_wall": "混凝土墙按结构体系设置，框架填充墙体系无砼墙",
    "brick_wall": "砌体墙按材料设计设置，全砼/全钢体系无",
    "block_wall": "砌块墙按材料设计设置，全砼/全钢体系无",
    "footing_isolated": "独立基础按柱网与地基承载力设置",
    "footing_strip": "条形基础按墙下荷载设置",
    "beam": "梁按结构体系设置，无梁楼盖/剪力墙体系可无",
    "slab": "板按楼盖体系设置",
    "rebar": "钢筋按结构材料设置，纯钢结构无砼钢筋",
    "door": "门按建筑功能设置，纯工业构筑物可无",
    "window": "窗按采光通风要求设置",
    "internal_partition": "内隔墙按平面功能设置，大空间可无",
    "floor_finish": "楼地面装修按做法表设置，毛坯交付可无",
    "wall_finish": "墙面装修按做法表设置",
    "paint": "涂料按装修做法设置",
    "roof_covering": "屋面面层按屋面构造设置",
    "waterproofing": "防水按部位与水密要求设置",
    "suspended_ceiling": "吊顶按空间要求设置，工业厂房常无",
    "tiling": "块料按装修做法设置",
    "balustrade": "栏杆按临边防护与建筑要求设置",
    "handrail": "扶手随楼梯/坡道设置",
    "external_works": "室外工程按总平与合同界面设置",
    # --- 机电 ---
    "pipe_run": "给排水管道按系统设置，无给排水需求的构筑物可无",
    "duct_run": "风管按暖通系统设置，自然通风/分体空调方案无风管",
    "cable_tray": "桥架按敷设方式设置，穿管/直埋段无桥架",
    "pipe_fitting": "管件按管径变化与转向发生",
    "valve": "阀门按系统控制要求设置",
    "pipe_support": "支架按管径与敷设方式设置",
    "sanitary_fixture": "洁具按建筑功能设置",
    "sprinkler_main": "喷淋主管按消防系统设置，无喷淋系统时无",
    "sprinkler_head": "喷头随喷淋系统",
    "duct_fitting": "风管部件按路由变化发生",
    "grille": "风口按送排风末端设置",
    "fan": "风机按通风系统设置",
    "ahu": "空调机组按系统型式设置，分体空调无 AHU",
    "fcu": "风机盘管按半集中式系统设置，全空气系统无 FCU",
    "cable": "电缆按供配电系统设置",
    "db": "配电箱/柜按配电系统设置",
    "luminaire": "灯具按照明设计设置",
    "socket": "插座按使用功能设置",
    "switch": "开关随灯具控制",
    "earthing": "接地按系统接地型式设置",
}

# ---------------------------------------------------------------------------
# 结构体系降级：刚构/整体式/拱桥会**天然没有**某些梁式桥构件，
# 若不降级就是硬误报（刚构桥被要求有支座）。降级 = 从 required 移入 optional。
# 用法：doc["structure_system"] = "beam"(默认) | "rigid_frame" | "integral" | "arch"
# ---------------------------------------------------------------------------
STRUCTURE_SYSTEM_DEMOTE = {
    # 刚构：墩梁固结 → 无支座、无垫石、无伸缩缝、无独立盖梁、无抗震挡块
    "rigid_frame": {"bearing", "bearing_pedestal", "expansion_joint",
                    "pier_cap", "shear_key"},
    # 整体式桥台：台梁固结 → 无支座、无垫石、无伸缩缝
    "integral": {"bearing", "bearing_pedestal", "expansion_joint"},
    # 拱桥：无梁式上部 → 无主梁/横隔梁/端横梁/湿接缝/垫石
    "arch": {"girder", "diaphragm", "end_diaphragm", "wet_joint",
             "bearing_pedestal"},
}

# 结构体系取值归一：接受 team-lead 指定的三值命名 + 本脚本的历史同义词。
# 归一后未在上面 DEMOTE 表里的（simply_supported / continuous_on_bearings）
# 一律视为「有支座」体系，不做降级。
STRUCTURE_SYSTEM_ALIASES = {
    "simply_supported": "simply_supported",
    "simple": "simply_supported", "简支": "simply_supported",
    "continuous_on_bearings": "continuous_on_bearings",
    "continuous": "continuous_on_bearings", "连续梁": "continuous_on_bearings",
    "rigid_frame": "rigid_frame", "刚构": "rigid_frame",
    "integral": "integral", "整体式": "integral",
    "arch": "arch", "拱桥": "arch",
    # 旧写法兼容（v4.7.1 曾用 beam 表示默认梁式桥）
    "beam": "simply_supported",
}

# 结构体系**合法**取值（用于校验未识别值）。空/未声明单独处理。
STRUCTURE_SYSTEM_VALID = set(STRUCTURE_SYSTEM_ALIASES)

# ---------------------------------------------------------------------------
# R7 别名表（内建 dict，避免新增 yaml 解析依赖；可用 doc["class_aliases"] 项目覆盖）
#   {"bearing": ["bearing_pad", ...]} —— 键为 §7 规范名，值为可接受的同义词/中文/历史写法。
#   项目覆盖方式：在登记表里写
#     "class_aliases": {"bearing": ["POT PTFE", "盆式橡胶支座"]}
#   合并语义为「追加」而非「替换」，避免项目覆盖把内置词表整体关掉。
# 优先级：规范名本身优先（如 `earthing` 既是 §7 电气规范名，又是 earth_electrode 的
#   常见误写 —— 两者都认，见 _class_groups 的双向闭包）。
# ---------------------------------------------------------------------------
CLASS_ALIASES = {
    "bearing": ("bearing_pad", "elastomeric_bearing", "pot_bearing", "支座", "板式橡胶支座"),
    "bearing_pedestal": ("垫石", "支座垫石"),
    "lighting_pole": ("light_pole", "lamp_post", "灯杆"),
    "cable_handhole": ("handhole", "手孔", "手孔井"),
    "earth_electrode": ("earthing", "接地极"),
    "earth_grid": ("接地干线",),
    "girder": ("主梁", "梁"),
    "pier_cap": ("墩帽", "盖梁"),
    "pier_column": ("墩柱", "墩身"),
    "diaphragm": ("横隔梁", "横梁"),
    "expansion_joint": ("伸缩缝",),
    "deck_slab": ("桥面板",),
    "pile": ("灌注桩", "钻孔灌注桩", "桩基"),
    "pile_cap": ("承台",),
    "abutment": ("桥台",),
    "wing_wall": ("翼墙",),
    "head_wall": ("端墙",),
    "apron": ("铺砌", "进出口铺砌"),
    "culvert": ("涵洞", "箱涵", "圆管涵"),
    "approach_slab": ("搭板", "桥头搭板"),
    "parapet": ("护栏", "防撞墙"),
    "luminaire": ("灯具",),
    "pole_foundation": ("灯杆基础",),
    "ecc_conductor": ("ecc", "接地导体"),
}

# 图册状态码 → 允许的最高证据强度
# 英语图制（SD/DD/IFx/CD/AB）+ 法语图制（APS/APD/EXE）。
# PRO/DCE/DOE/IFI/IFD：法语阶段码，**层级待核 NF P 02-001**（DCE 为招标文件包，
# 不是单一图册成熟度等级；此处按招标阶段一律封顶到「图读·尺寸链」，宁可低不可高）。
STATUS_CEILING = {
    "IFT": "图读·尺寸链", "TENDER": "图读·尺寸链",
    "IFA": "图读·尺寸链", "IFR": "图读·尺寸链",
    "IFP": "图读·尺寸链", "IFB": "图读·尺寸链",
    "SD": "图读·尺寸链", "DD": "图读·尺寸链",
    "IFC": "图读·标注", "CD": "图读·标注",
    "AB": "图读·标注（须与实测互证）", "AS-BUILT": "图读·标注（须与实测互证）",
    "APS": "图读·尺寸链", "APD": "图读·尺寸链", "EXE": "图读·标注",
    # --- 法语阶段码（层级待核 NF P 02-001）---
    "PRO": "图读·尺寸链", "DCE": "图读·尺寸链", "DOE": "图读·尺寸链",
    "IFI": "图读·尺寸链", "IFD": "图读·尺寸链",
}

# R9 证据强度档位：数越小越弱。取值层级 图注 > 图面实测 > 尺寸链推算。
EVIDENCE_RANK = {
    "图读·尺寸链": 1,
    "图读·标注": 2,
    "图读·量表": 2,
    "实测": 3,
}

# 敞口四态：只允许这四个码。写「挂账」「待核」「持续跟踪」= 没写。
OPEN_ITEM_STATES = ("CLOSED", "SCOPED", "RFI", "EXTERNAL")
# R12 结构性出处判据（语言无关）：长度 ≥ 8 且命中图号/册号/条款/页码/年份类线索。
# 取代原先的纯中文占位词表（该词表实测拦不住 "AI 自拟" / "general practice" / "assumed"）。
SOURCE_RE = re.compile(r"(卷|册|Vol\.?|Section|§|p\.|page|图|表|\d{2,})", re.I)
# 隔离类词判 FAIL；说明类词（未计/估算/待核）只出 INFO —— 一刀切会误报
ZOMBIE_MARKERS = ("推定区", "不得进报价", "暂列", "隔离行")


def now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def load(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, d):
    d_dir = os.path.dirname(os.path.abspath(path))
    if d_dir:
        os.makedirs(d_dir, exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def looks_sourced(s):
    """R12：结构性「可回查出处」判据（语言无关）。

    只认「能回查到某张图/某册/某条款/某页」的说法，不认语种、不认语气。
    实测被这条拦下的：AI 自拟 / general practice / assumed / AI / 常规值 /
    见图 / N/A / OK / 无 —— 这些在旧版纯中文词表下全部通过。
    """
    s = (s or "").strip()
    return len(s) >= 8 and bool(SOURCE_RE.search(s))


# ---------------------------------------------------------------------------
# R7 受控词表：解析 references/disciplines-coverage.md §7 代码块
# ---------------------------------------------------------------------------
_CANON_TOKEN = re.compile(r"[a-z][a-z0-9_]*(?:_?\([a-z]+\))?\*?")
_CANON_CACHE = {}


def _norm_token(tok):
    t = tok.rstrip("*")
    m = re.match(r"^([a-z][a-z0-9_]*)\(([a-z]+)\)$", t)
    if m:
        t = m.group(1).rstrip("_") + "_" + m.group(2)
    return t


def class_canon():
    """返回 (canon:set, families:set, err:str)。

    解析失败必须报 CANNOT_RUN（err 非空），**不得静默降级为空词表**——
    空词表会让所有 class 都「未识别」，也可被反过来当成「全都放行」。
    """
    if "v" in _CANON_CACHE:
        return _CANON_CACHE["v"]
    res = _parse_class_canon()
    _CANON_CACHE["v"] = res
    return res


def _parse_class_canon():
    try:
        lines = io.open(COVERAGE_DOC, encoding="utf-8").read().splitlines()
    except OSError as e:
        return set(), set(), "无法读取受控词表 %s：%s" % (COVERAGE_DOC, e)
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith("## 7.") and "Class" in ln:
            start = i
            break
    if start is None:
        return set(), set(), "参考文献中找不到 `## 7. Class 名速查` 段（词表不可用）"
    canon, families = set(), set()
    in_fence, closed = False, False
    for ln in lines[start + 1:]:
        s = ln.strip()
        if s.startswith("```"):
            if not in_fence:
                in_fence = True
                continue
            closed = True
            break
        if not in_fence:
            continue
        for tok in _CANON_TOKEN.findall(s):
            if tok.endswith("*"):          # 族前缀（precast_*）
                families.add(_norm_token(tok))
                continue
            t = _norm_token(tok)
            if len(t) >= 2:
                canon.add(t)
    if not closed:
        return set(), set(), "§7 代码块未闭合（词表解析中断）"
    if len(canon) < 100:
        return set(), set(), ("§7 词表解析异常：仅解析到 %d 条（阈值 100）→ 受控词表不可用，"
                              "禁止降级为「放行」" % len(canon))
    return canon, families, ""


def _aliases(overrides=None):
    """内置别名 + 项目覆盖（追加语义）。"""
    out = {k: set(v) for k, v in CLASS_ALIASES.items()}
    if isinstance(overrides, dict):
        for k, v in overrides.items():
            if not isinstance(k, str):
                continue
            vals = v if isinstance(v, (list, tuple)) else [v]
            out.setdefault(k, set()).update(str(x).strip() for x in vals if str(x).strip())
    return out


def _class_groups(name, overrides=None):
    """该名可接受的规范 class 集合（自身 + 所属别名词条，双向闭包）。"""
    canon, families, _ = class_canon()
    out = set()
    if name in canon:
        out.add(name)
    for pre in families:
        if name.startswith(pre):
            out.add(name)
    for c, alts in _aliases(overrides).items():
        if name == c or name in alts:
            out.add(c)
    return out


def _present(cls, raw_names, overrides=None):
    """台账里是否存在 cls（含别名/等价名写法）。"""
    want = {cls} | set(_aliases(overrides).get(cls, ()))
    for n in raw_names:
        if n in want:
            return True
        if want & _class_groups(n, overrides):
            return True
    return False


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def add(findings, level, code, obj, msg):
    findings.append(dict(level=level, code=code, object=obj, msg=msg))


def _is_count(v):
    """R10：页码类字段必须是非负整数，且 bool 不算 int（True 会被当 1）。"""
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _to_float(v):
    """R11：观测值允许字符串数字（"9999" → 9999.0）；无法转数返回 None。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", "").replace(" ", ""))
        except ValueError:
            return None
    return None


def evidence_rank(word):
    """R9：证据词 → 强度档位。未知词返回 None（不猜、不放行）。"""
    w = (word or "").strip()
    if not w:
        return None
    for k, v in EVIDENCE_RANK.items():
        if w.startswith(k):
            return v
    return None


def ceiling_rank(ceiling):
    """封顶值里可能带注解（如「图读·标注（须与实测互证）」），取基档。"""
    return evidence_rank(ceiling)


def template():
    return dict(
        schema=SCHEMA, created=now(),
        _note="COV 登记页数 + status_code；PAIR 填 systems 与 component_classes（class 名须取 "
              "references/disciplines-coverage.md §7 受控词表）；RATIO 的 band 必须项目声明并写 "
              "declared_by（须可回查：图号/册号/条款/页码）；OI 状态只允许 CLOSED/SCOPED/RFI/EXTERNAL。"
              "本项目确无敞口时置 open_items=[] 且 open_items_declared_zero=true",
        drawings=[
            dict(sheet="S-201", scope_status="in_scope",
                 pages_total=None, pages_valid=None, blank_pages=None,
                 status_code="",
                 note="pages_* 留 null = 未核，不记通过；status_code 见 status 子命令，"
                      "留空 = 证据强度封顶未校验（COV-4）")
        ],
        # R6 参照系：声明本项目涉及哪些系统（可多选 bridge/culvert/lighting/building/road/mep）
        # 「应有项全集 − 台账 class」= 整组未建，专抓形态 2/3（梁+支座整组漏）
        systems=["bridge"],
        # 结构体系（必填，不写会 WARN/ strict FAIL）：
        #   simply_supported        简支（支座类判据照常适用）
        #   continuous_on_bearings  连续梁（支座类判据照常适用）
        #   rigid_frame             刚构（墩梁固结，天然无支座/垫石/伸缩缝 → 自动降级）
        #   integral                整体式桥台（台梁固结，同上）
        #   arch                    拱桥（无梁式上部）
        structure_system="simply_supported",
        component_classes=["girder", "bearing", "pier_cap"],
        # R6 豁免：确认某 system 下某构件「本项目不该有」时留可回查字据（不接受「N/A」「不适用」）
        system_waivers={},
        # R7 项目级别名追加（可选）：{"bearing": ["POT PTFE"]}
        class_aliases={},
        attributes_note={},
        ratio_bands=[],          # 空 = 未校验（INFO），脚本绝不自带惯例带
        observed_ratios=[],      # [{object, measure, value, unit}]
        open_items=[
            dict(id="OI-01", status="挂账", basis="", impact="",
                 note="把这条改成四态之一；SCOPED 须带 pending_count 或 basis")
        ],
        # R13 本项目确无敞口时的合法出路（不是逼人造假 CLOSED）
        open_items_declared_zero=False,
        scan_files=[],           # 待扫的生成器/模板文件，用于僵尸标记反查
    )


# ---------------------------------------------------------------------------
# COV —— 图册覆盖率（R5 / R9 / R10）
# ---------------------------------------------------------------------------
def check_coverage(doc, findings, strict=False):
    """返回 (checked, cannot)。R5：cannot 由调用方统一汇总成 exit 2。"""
    drawings = [d for d in (doc.get("drawings") or [])
                if isinstance(d, dict) and d.get("scope_status") != "excluded"]
    if not drawings:
        add(findings, "CANNOT_RUN", "COV-0", "-", "范围内无图纸 → 门禁未运行，不得记通过")
        return 0, 1
    checked, cannot = 0, 0
    for d in drawings:
        sheet = d.get("sheet") or "?"
        # R9：封顶未校验（看不到状态码不许默认 IFC）
        code = (d.get("status_code") or "")
        code = code.strip().upper() if isinstance(code, str) else ""
        if not code:
            add(findings, "WARN", "COV-4", sheet,
                "未登记 status_code → 证据强度封顶未校验（COV/LEDGER 只做了「有没有」的一半）。"
                "看不到状态码就挂 RFI，不许默认按 IFC 处理")
        elif code not in STATUS_CEILING:
            add(findings, "WARN", "COV-4", sheet,
                "状态码「%s」不在 STATUS_CEILING 中 → 封顶未知，挂 RFI，不许默认按 IFC" % code)
        total, valid, blank = (d.get("pages_total"), d.get("pages_valid"),
                               d.get("blank_pages"))
        # R10：类型非法 → CANNOT_RUN，绝不抛 TypeError 让「崩溃」冒充「有 FAIL」
        bad = [(n, d.get(n)) for n in ("pages_total", "pages_valid", "blank_pages")
               if d.get(n) is not None and not _is_count(d.get(n))]
        if bad:
            cannot += 1
            add(findings, "CANNOT_RUN", "COV-3", sheet,
                "页数字段类型非法：%s → 覆盖率判据未运行（须为非负整数；字符串 \"10\" 不得当 10 用）"
                % "、".join("%s=%r" % (n, v) for n, v in bad))
            continue
        if total is None or (valid is None and blank is None):
            add(findings, "WARN" if not strict else "FAIL", "COV-1", sheet,
                "未登记页数（pages_total/pages_valid）→ 记「未核」，**不是通过**。"
                "整册空白时该册的量不是算错，是根本没有，表内门禁全绿也查不到")
            continue
        if valid is None:
            valid = total - (blank or 0)
        checked += 1
        if valid < 0 or valid > total:
            add(findings, "FAIL", "COV-2", sheet,
                "页数登记自相矛盾：total=%s valid=%s" % (total, valid))
            continue
        # R10：pages_valid + blank_pages > pages_total 是登记矛盾（10 有效 + 8 空白 = 18 页 > 10 页）
        if blank is not None and valid + blank > total:
            add(findings, "FAIL", "COV-2", sheet,
                "页数登记自相矛盾：pages_total=%d，而 pages_valid=%d + blank_pages=%d = %d > total。"
                "同一页不可能既有效又空白 —— 先更正登记，再谈覆盖率"
                % (total, valid, blank, valid + blank))
            continue
        if valid < total:
            add(findings, "FAIL", "COV-2", sheet,
                "缺页/空白 %d 张（total=%s valid=%s）。须先定性该册覆盖的分部是否在算量范围内，"
                "再决定出不出量；直接跳过等于把整册当成不存在" % (total - valid, total, valid))
    return checked, cannot


# ---------------------------------------------------------------------------
# PAIR —— 必搭配件 + 应有项全集（R5 / R6 / R7 / R8 / R12）
# ---------------------------------------------------------------------------
def _all_classes_of(sysd):
    """system 定义里的全部 class（required + either + conditional + optional）。"""
    return (set(sysd.get("required") or [])
            | set(sysd.get("optional") or [])
            | set(sysd.get("conditional") or [])
            | {c for g in (sysd.get("either") or []) for c in g})


def _infer_systems(raw_names, overrides=None):
    """从 component_classes 反推 systems（v4.6.x 旧格式向后兼容）。

    判据：按「命中该 system 特征项的数量」排序，只保留**得分最高的一档**（含并列）。
    为何不返回全部命中：`rebar`/`approach_slab`/`wing_wall` 等通用项会让
    bridge 与 culvert 同时命中，于是桥梁项目被要求提供涵洞项 → 正是要修的误报。
    故取最高档：主系统命中数通常远高于恰好沾边的邻接系统。
    """
    if not raw_names:
        return []
    hits = {}
    for key, sysd in SYSTEM_EXPECTED.items():
        allc = _all_classes_of(sysd)
        n = sum(1 for c in allc if _present(c, raw_names, overrides))
        if n:
            hits[key] = n
    if not hits:
        return []
    top = max(hits.values())
    return sorted(k for k, n in hits.items() if n == top)


def check_pair(doc, findings, strict=False):
    """返回 (checked, cannot)。

    三段判据合成一个 PAIR 类别：
      ① 受控词表校验（R7）：未识别 class → CANNOT_RUN
      ② 应有项全集（R6）：expected_for_systems − 台账 class 非空 → WARN「应有而全无」
      ③ 必搭配件（原有附加判据）：有 A 必查 B
    """
    raw = doc.get("component_classes")
    if not isinstance(raw, list) or not raw:
        add(findings, "CANNOT_RUN", "PAIR-0", "-",
            "无构件 class 集合（component_classes 缺失或为空）→ 必搭配件与应有项全集判据均未运行，"
            "不得记通过")
        return 0, 1

    overrides = doc.get("class_aliases") or {}
    raw_names = [str(x).strip() for x in raw if str(x).strip()]
    checked, cannot = 0, 0

    # ① R7：受控词表 + 别名
    canon, families, err = class_canon()
    if err:
        add(findings, "CANNOT_RUN", "PAIR-0c", "-",
            "受控词表不可用：%s → PAIR 判据未运行（禁止降级放行）" % err)
        cannot += 1
    unknown = [n for n in raw_names if not _class_groups(n, overrides)]
    if unknown:
        cannot += 1
        add(findings, "CANNOT_RUN", "PAIR-4", "，".join(unknown),
            "未识别 class：%s → PAIR 判据未运行。规范名须取 references/disciplines-coverage.md §7"
            "（如支座=bearing、垫石=bearing_pedestal、灯杆=lighting_pole、手孔=cable_handhole、"
            "接地极=earth_electrode）；确属项目特有名时在 class_aliases 里登记后重跑。"
            "按文档命名反而把门禁关掉，正是这条要防的" % "，".join(unknown))
    if unknown or err:
        # 词表不可用 → 判据整体不可信，后面的结论一律不下
        return checked, cannot

    # ② R6：应有项全集（参照系反转 —— 抓到「整组未建」）
    # 三档判据：required（缺即 FAIL）/ either 互斥组（组内 ≥1 即满足）/
    #          conditional（缺只 INFO，理由写进消息）/ optional（缺静默）。
    systems = doc.get("systems")
    waivers = doc.get("system_waivers") or {}
    # 结构体系降级：刚构/整体式/拱桥天然无支座等构件，降级即移入 optional（不报）
    # 结构体系归一 + 未声明告警（team-lead 规格）：
    #   simply_supported / continuous_on_bearings → 支座类判据照常适用
    #   rigid_frame / integral / arch → 走 STRUCTURE_SYSTEM_DEMOTE 降级
    #   未声明 → WARN PAIR-3 明写「按默认适用」；strict 下升 FAIL。
    #   **未声明不等于可绕过**：默认按简支梁式桥判，刚构必须自己声明才降级。
    raw_ss = doc.get("structure_system")
    ssystem = None
    if isinstance(raw_ss, str) and raw_ss.strip():
        ssystem = STRUCTURE_SYSTEM_ALIASES.get(raw_ss.strip().lower(), raw_ss.strip().lower())
        if ssystem not in STRUCTURE_SYSTEM_VALID:
            add(findings, "WARN", "PAIR-3b", str(raw_ss),
                "未识别的 structure_system「%s」→ 按默认（简支梁式桥）处理。"
                "合法取值：simply_supported / continuous_on_bearings / rigid_frame / "
                "integral / arch" % raw_ss)
            ssystem = "simply_supported"
    else:
        ssystem = "simply_supported"
        # 只出 INFO（不 WARN/不 FAIL）：未填一个**新增**字段 ≠ 判据未运行。
        # 若升成 WARN/FALSE，会让所有未及补登 structure_system 的**正确项目**被判红 ——
        # 验收反向断言 R2/R3/R4 正是在这一步被抓出来的（误报比漏报更伤门禁）。
        # 真正的安全阀在 C33：不声明时按简支判，刚构必须自己声明才降级，不会白送。
        add(findings, "INFO", "PAIR-3", "-",
            "未声明 structure_system → 支座类判据按**默认简支梁式桥**适用。"
            "刚构/整体式/拱桥请显式声明（simply_supported / continuous_on_bearings / "
            "rigid_frame / integral / arch），否则会被要求有支座")
    demote = STRUCTURE_SYSTEM_DEMOTE.get(ssystem, set())
    # 向后兼容：v4.6.x 的老 off_table.json 没有 systems 字段。若直接 exit 2，
    # 会让所有存量项目一夜之间变成「门禁未运行」（静默断裂）。故：
    #   ① 能由 component_classes 反推出系统 → WARN 提示补登，判据照跑；
    #   ② 完全反推不出（class 太少/太泛）→ 才 CANNOT_RUN。
    if not isinstance(systems, list) or not systems:
        inferred = _infer_systems(raw_names, overrides)
        if inferred:
            # 同理只出 INFO：能反推出参照系，说明判据确实跑了，不是「未运行」。
            # 升 WARN/FAIL 会让所有存量 v4.6.x 项目在 strict 下整片变红（向后不兼容）。
            add(findings, "INFO", "PAIR-3", "-",
                "未声明 systems（v4.6.x 旧格式）→ 已按 component_classes 反推为 %s 运行。"
                "请补登 systems 字段（可多选：%s），以免「整组未建」的参照系依赖反推"
                % ("/".join(inferred), " / ".join(sorted(SYSTEM_EXPECTED))))
            systems = inferred
        else:
            cannot += 1
            add(findings, "CANNOT_RUN", "PAIR-3", "-",
                "未声明 systems 且无法从 component_classes 反推 → 应有项全集判据未运行。"
                "可多选：%s。没有参照系，「整组未建」永远查不到"
                % " / ".join(sorted(SYSTEM_EXPECTED)))
    if isinstance(systems, list) and systems:
        for s in systems:
            key = (s or "").strip().lower() if isinstance(s, str) else ""
            exp = SYSTEM_EXPECTED.get(key)
            if exp is None:
                cannot += 1
                add(findings, "CANNOT_RUN", "PAIR-5", str(s),
                    "未知 system「%s」→ 应有项全集判据未运行（合法取值：%s）"
                    % (s, " / ".join(sorted(SYSTEM_EXPECTED))))
                continue
            checked += 1
            w_key = waivers.get(key) if isinstance(waivers.get(key), dict) else {}

            def _waiver(cls):
                """返回可回查字据文本；不合格或缺失返回 None。"""
                w = w_key.get(cls)
                return w if isinstance(w, str) and looks_sourced(w) else None

            # --- required：缺即 FAIL（结构体系降级项不算缺） ---
            for cls in sorted(exp["required"]):
                if cls in demote:
                    add(findings, "INFO", "PAIR-6D", cls,
                        "[%s] 结构体系 %s 天然无「%s」→ 已降级，不判缺"
                        % (key, ssystem, cls))
                    continue
                if _present(cls, raw_names, overrides):
                    continue
                w = _waiver(cls)
                if w:
                    add(findings, "INFO", "PAIR-7", cls,
                        "[%s] 声明本项目无此项：%s（字据可回查，复核时复验）" % (key, w))
                    continue
                why = CONDITIONAL_WHY.get(cls, "")
                add(findings, "FAIL", "PAIR-6R", cls,
                    "应有而全无（必备项）：%s 系统必须有「%s」（%s），台账里一个都没有 → "
                    "整组未建 / 整项漏。漏掉的东西不在表内，表内自洽的门禁一条都抓不到。"
                    "确属本项目不该有，用 system_waivers 留可回查字据%s"
                    % (key, cls, exp["required"] and "required 档" or "required 档",
                       ("；" + why) if why else ""))

            # --- either：互斥组，组内 ≥1 项存在即满足 ---
            for grp in exp.get("either") or []:
                grp = tuple(grp)
                if any(c in demote for c in grp):
                    add(findings, "INFO", "PAIR-6D", "/".join(grp),
                        "[%s] 结构体系 %s 天然无该组 → 已降级，不判缺" % (key, ssystem))
                    continue
                if any(_present(c, raw_names, overrides) for c in grp):
                    continue
                w = next((_waiver(c) for c in grp if _waiver(c)), None)
                if w:
                    add(findings, "INFO", "PAIR-7", "/".join(grp),
                        "[%s] 声明本项目无该组：%s（字据可回查）" % (key, w))
                    continue
                adds = "；".join("%s：%s" % (c, CONDITIONAL_WHY.get(c, "")) for c in grp)
                add(findings, "FAIL", "PAIR-6E", "/".join(grp),
                    "互斥组整组未建（二者至少要有其一）：%s 系统应具备 %s 之一，台账里一个都没有 → "
                    "基础型式整组漏。%s" % (key, " 或 ".join(grp), adds))

            # --- conditional：条件应有，缺只 INFO（不阻断） ---
            for cls in sorted(exp.get("conditional") or []):
                if cls in demote:
                    continue          # 降级项：连 INFO 都不出
                if _present(cls, raw_names, overrides):
                    continue
                if _waiver(cls):
                    add(findings, "INFO", "PAIR-7", cls,
                        "[%s] 声明本项目无此项：%s（字据可回查）" % (key, _waiver(cls)))
                    continue
                why = CONDITIONAL_WHY.get(cls)
                add(findings, "INFO", "PAIR-6C", cls,
                    "条件应有（不阻断）：[%s] 台账无「%s」—— %s。"
                    "按项目实际/图注判定：确实不设则无需处理，确应设则补登"
                    % (key, cls, why or "（依据缺失，请补 CONDITIONAL_WHY）"))
                if not why:           # 依据缺失本身要能被审计看见
                    add(findings, "WARN", "PAIR-6W", cls,
                        "条件项「%s」缺专业依据说明 → 门禁自身的判据质量缺陷，须补齐 why" % cls)
            # --- optional：缺静默 ---

    # ③ 原有「有 A 必查 B」—— 附加判据，不再是唯一判据
    # 去重规则：若 req 已被「required 档」覆盖（PAIR-6R 已 FAIL）或已被结构体系降级，
    # 这里不再重复报 —— 同一条缺项报两遍会让报告失真。
    notes = doc.get("attributes_note") or {}
    covered = set()          # 已由 required 档负责的 class
    for s in (systems if isinstance(systems, list) else []):
        key = (s or "").strip().lower() if isinstance(s, str) else ""
        exp = SYSTEM_EXPECTED.get(key)
        if exp:
            covered |= set(exp["required"])
    for rule in MANDATORY_COMPANIONS:
        if not all(_present(c, raw_names, overrides) for c in rule["when_all"]):
            continue
        if rule["when_any"] and not any(_present(c, raw_names, overrides)
                                        for c in rule["when_any"]):
            continue
        req = rule["required"]
        if _present(req, raw_names, overrides):
            continue
        if req in covered:
            continue          # required 档已判缺，不重复
        if req in demote:
            continue          # 结构体系天然无此项，不判缺
        # either 组已满足则不重复报（如圆管涵用端墙替代翼墙：wing_wall 缺但 head_wall 在）
        if any(req in grp and any(_present(c, raw_names, overrides) for c in grp)
               for s in (systems if isinstance(systems, list) else [])
               for grp in (SYSTEM_EXPECTED.get((s or "").strip().lower() if isinstance(s, str) else "",
                                               {}) or {}).get("either") or []):
            continue
        checked += 1
        note = notes.get(req, "")
        if isinstance(note, str) and looks_sourced(note):
            add(findings, "INFO", "PAIR-2", req,
                "声明并入母体：%s（口径已留字据，复核时回查）" % note)
        elif note:
            add(findings, "FAIL", "PAIR-2", req,
                "并入母体的字据不合格：%r → 须含图号/册号/条款/页码，否则与「没查」同形。"
                "（%s）" % (note, rule["why"]))
        else:
            add(findings, "WARN", "PAIR-1", req,
                "必搭配件缺项 —— %s。%s。"
                "漏掉的东西不在表内，表内自洽的门禁一条都抓不到"
                % (rule["why"], "允许并入母体，但须在 attributes_note 留可回查字据"))
    return checked, cannot


# ---------------------------------------------------------------------------
# RATIO —— 含量比率（R5 / R11 / R12）
# ---------------------------------------------------------------------------
def check_ratios(doc, findings, stats=None):
    """返回 (checked, cannot)；明细统计写入 stats（声明/实检/未匹配）。"""
    if stats is None:
        stats = {}
    bands = doc.get("ratio_bands") or []
    observed = doc.get("observed_ratios") or []
    stats["declared"] = len(bands)
    stats["checked"] = 0
    stats["unmatched"] = 0
    if not bands:
        add(findings, "INFO", "RATIO-0", "-",
            "项目未声明 ratio_bands → **未校验，明写跳过**。"
            "本门禁不内置任何基准带：惯例值的适用范围正是历史错误的来路")
        return 0, 0
    checked, cannot = 0, 0
    for b in bands:
        if not isinstance(b, dict):
            cannot += 1
            add(findings, "CANNOT_RUN", "RATIO-7", "?", "ratio_bands 条目不是对象 → 未校验")
            continue
        bid = b.get("id", "?")
        by = (b.get("declared_by") or "").strip()
        # R12：结构性出处判据，取代纯中文占位词表
        if not looks_sourced(by):
            add(findings, "FAIL", "RATIO-3", bid,
                "基准带缺可回查 declared_by（填「%s」等同没声明）→ 须含图号/册号/条款/页码。"
                "带是项目事实不是常识，AI 不得自拟带" % (by or "空"))
            continue
        band = b.get("band") or [None, None]
        if not isinstance(band, (list, tuple)) or len(band) < 2:
            add(findings, "INFO", "RATIO-4", bid, "基准带定义不完整 → 未校验，不静默变成通过")
            continue
        lo, hi = band[0], band[1]
        lo_f, hi_f = _to_float(lo), _to_float(hi)
        if lo_f is None or hi_f is None:
            add(findings, "INFO", "RATIO-4", bid,
                "基准带上下限非数值（%r, %r）→ 未校验，不静默变成通过" % (lo, hi))
            continue
        matched = 0
        for o in observed:
            if not isinstance(o, dict):
                continue
            if b.get("object") and o.get("object") != b["object"]:
                continue
            if o.get("measure") != b.get("numerator_measure"):
                continue
            matched += 1
            v = _to_float(o.get("value"))
            if v is None:
                add(findings, "WARN", "RATIO-6", o.get("object", bid),
                    "观测值非数值（value=%r）→ 未校验。字符串数字应可转数，"
                    "转不了说明这一行本身不是量" % (o.get("value"),))
                continue
            checked += 1
            if not (lo_f <= v <= hi_f):
                add(findings, "WARN", "RATIO-1", o.get("object", bid),
                    "比率 %.2f %s 落在声明带 [%.2f, %.2f] 之外 → "
                    "**分母疑漏｜也可能是分子重复计**。比率只指路不定性，由人下结论"
                    % (v, o.get("unit", ""), lo_f, hi_f))
        if matched == 0:
            stats["unmatched"] += 1
            add(findings, "WARN", "RATIO-5", bid,
                "基准带与观测值未匹配（object=%r，numerator_measure=%r）→ 未校验。"
                "键名不一致时旧版会静默什么也不输出，等于带白声明"
                % (b.get("object"), b.get("numerator_measure")))
    stats["checked"] = checked
    return checked, cannot


# ---------------------------------------------------------------------------
# OI —— 敞口四态（R5 / R12 / R13）
# ---------------------------------------------------------------------------
def check_open_items(doc, findings):
    """返回 (checked, cannot)。"""
    if "open_items" not in doc or doc.get("open_items") is None:
        add(findings, "CANNOT_RUN", "OI-0", "-",
            "未登记敞口台账（open_items 键缺失或为 null）→ 收敛判据未运行，不得记通过")
        return 0, 1
    items = doc.get("open_items")
    if not isinstance(items, list):
        add(findings, "CANNOT_RUN", "OI-0", "-",
            "open_items 须为列表（当前 %s）→ 收敛判据未运行" % type(items).__name__)
        return 0, 1
    if not items:
        # R13：0 条敞口是合法状态，但必须显式声明 —— 不逼人造假 CLOSED
        if doc.get("open_items_declared_zero") is True:
            add(findings, "INFO", "OI-0", "-",
                "已显式声明本项目无敞口（open_items=[] 且 open_items_declared_zero=true）")
            return 1, 0
        add(findings, "FAIL", "OI-0", "-",
            "台账为空且未显式声明：没有敞口台账就没有收敛判据。"
            "如确无敞口，请置 `open_items_declared_zero: true`；"
            "否则逐条落四态（CLOSED/SCOPED/RFI/EXTERNAL）—— 造一条假 CLOSED 不算完成")
        return 0, 0
    checked = 0
    for it in items:
        if not isinstance(it, dict):
            add(findings, "FAIL", "OI-1", "?", "敞口条目不是对象 → 未判态")
            continue
        no = it.get("id", "?")
        raw_status = it.get("status")
        raw = raw_status.strip().upper() if isinstance(raw_status, str) else ""
        checked += 1
        if raw not in OPEN_ITEM_STATES:
            add(findings, "FAIL", "OI-1", no,
                "状态写「%s」→ 未判态。「挂账」是形容词，把四件性质不同的事压成一个词，"
                "必须落 CLOSED / SCOPED / RFI / EXTERNAL 之一"
                % (raw_status if raw_status is not None else "空"))
            continue
        text = "%s %s" % (it.get("note") or "", it.get("basis") or "")
        if ("已结" in text or raw == "CLOSED") and any(
                w in text for w in ("仍", "但是", "待", "未结")):
            add(findings, "FAIL", "OI-8", no,
                "混态未拆条（已结与仍未结同时成立）→ 未结那半会被状态吞掉")
        if raw == "CLOSED":
            basis = (it.get("basis") or "").strip()
            # R12：结构性出处判据
            if not looks_sourced(basis):
                add(findings, "FAIL", "OI-2", no,
                    "已结但 basis=「%s」拿不出可回查出处（须含图号/册号/条款/页码）→ "
                    "「已结」与「没查」同形" % (basis or "空"))
        if raw == "SCOPED":
            # R13：SCOPED 不是「什么都不写」就过
            pc = it.get("pending_count")
            basis = (it.get("basis") or "").strip()
            ok_count = isinstance(pc, int) and not isinstance(pc, bool) and pc > 0
            if not (ok_count or looks_sourced(basis)):
                add(findings, "FAIL", "OI-6", no,
                    "SCOPED 未声明待决数量或依据（pending_count 须为 int>0，或 basis 须含"
                    "图号/册号/条款/页码）→ 口径闭环却没有待数清单，等于把未计藏在合法状态后面")
        if raw in ("RFI", "EXTERNAL"):
            impact = str(it.get("impact") or "")
            if not any(ch.isdigit() for ch in impact):
                add(findings, "WARN", "OI-4", no,
                    "RFI/EXTERNAL 未量化影响（无 ±m³ / % / 个）→ 没有决策价值；"
                    "且须回答「下一轮读图能不能解决」，答「能」的退回待办而非 RFI")
            if "下一轮" not in text and "读图" not in text:
                add(findings, "WARN", "OI-5", no,
                    "未回答「下一轮读图能不能解决」—— 答能的不是 RFI，是待办")
    # 僵尸标记：已 CLOSED 的敞口编号仍被临时隔离词点到
    closed = {it.get("id") for it in items
              if isinstance(it, dict) and isinstance(it.get("status"), str)
              and it.get("status").upper() == "CLOSED"}
    for p in doc.get("scan_files") or []:
        if isinstance(p, dict):
            content, label = p.get("content", ""), p.get("path", "?")
        else:
            label = p
            try:
                content = io.open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
        if not isinstance(content, str):
            continue
        for line_no, line in enumerate(content.splitlines(), 1):
            if any(m in line for m in ZOMBIE_MARKERS):
                hit = [c for c in closed if c and c in line]
                if hit:
                    add(findings, "FAIL", "OI-3", ",".join(hit),
                        "%s:%d 僵尸标记：敞口已 CLOSED，但「推定区/不得进报价/暂列」"
                        "的开关还开着 → 已核实的量被排除在报价外" % (label, line_no))
    return checked, 0


# ---------------------------------------------------------------------------
# LEDGER —— 证据强度封顶（R9）
# ---------------------------------------------------------------------------
def check_ledger(doc, findings, rows=None):
    """按 --ledger CSV（drawing,evidence_word）校验证据强度不超过该图册封顶。

    返回 (checked, cannot)。
    """
    if rows is None:
        return 0, 0            # 未启用：不适用，不是「未运行」
    if not rows:
        add(findings, "CANNOT_RUN", "LEDGER-0", "-",
            "--ledger 已指定但台账为空 → 证据强度封顶判据未运行")
        return 0, 1
    by_sheet = {}
    for d in (doc.get("drawings") or []):
        if isinstance(d, dict) and d.get("sheet"):
            by_sheet[str(d["sheet"]).strip()] = d
    checked, cannot = 0, 0
    for i, r in enumerate(rows, 1):
        sheet = (r.get("drawing") or "").strip()
        word = (r.get("evidence_word") or "").strip()
        label = sheet or ("第 %d 行" % i)
        d = by_sheet.get(sheet)
        if d is None:
            add(findings, "WARN", "LEDGER-1", label,
                "台账图号在 drawings 里找不到 → 该行封顶未校验（图号先对齐再谈证据强度）")
            continue
        code = d.get("status_code") or ""
        code = code.strip().upper() if isinstance(code, str) else ""
        if not code:
            cannot += 1
            add(findings, "CANNOT_RUN", "LEDGER-0", label,
                "该册未登记 status_code → 证据强度封顶未校验。看不到状态码就挂 RFI，不许默认按 IFC")
            continue
        ceiling = STATUS_CEILING.get(code)
        if ceiling is None:
            cannot += 1
            add(findings, "CANNOT_RUN", "LEDGER-0", label,
                "状态码「%s」不在 STATUS_CEILING → 封顶未知，挂 RFI，不许默认按 IFC" % code)
            continue
        r_word = evidence_rank(word)
        r_ceil = ceiling_rank(ceiling)
        if r_word is None:
            add(findings, "WARN", "LEDGER-2", label,
                "证据词「%s」不在取值层级内（图读·尺寸链 / 图读·标注 / 图读·量表 / 实测）"
                "→ 未校验" % (word or "空"))
            continue
        if r_ceil is None:
            cannot += 1
            add(findings, "CANNOT_RUN", "LEDGER-0", label,
                "封顶值「%s」无法解析 → 未校验" % ceiling)
            continue
        checked += 1
        if r_word > r_ceil:
            add(findings, "FAIL", "LEDGER-3", label,
                "证据强度「%s」超出图册状态 %s 的封顶「%s」→ 把「我量到了」说成「设计确认了」。"
                "证据强度最终值 = min(来源强度, 图册成熟度上限)" % (word, code, ceiling))
    return checked, cannot


def load_ledger(path):
    """读 --ledger CSV（表头须含 drawing,evidence_word）。"""
    with io.open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        fields = [x.strip() for x in (rd.fieldnames or [])]
        if "drawing" not in fields or "evidence_word" not in fields:
            raise ValueError("ledger CSV 表头须为 drawing,evidence_word（实际：%s）"
                             % ",".join(fields))
        rows = []
        for r in rd:
            rows.append(dict(drawing=(r.get("drawing") or "").strip(),
                             evidence_word=(r.get("evidence_word") or "").strip()))
    return rows


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
def zero_guard(findings, owner, checked, applicable):
    """R8 checked==0 守卫（显式兜底，不再靠各判据自觉）。

    某类判据本应适用（applicable=True）却一项未检（checked==0），且该类没有
    产出任何 FAIL/WARN/CANNOT_RUN（即只在「静默通过」），补记一条 CANNOT_RUN。
    旧版的实病：check_companions 的 checked==0 一路静默 exit 0。
    """
    if not applicable or checked:
        return 0
    blocked = any(f["code"].startswith(owner + "-")
                  and f["level"] in ("FAIL", "WARN", "CANNOT_RUN")
                  for f in findings)
    if blocked:
        return 0
    add(findings, "CANNOT_RUN", owner + "-8", "-",
        "%s 判据实检 0 项（本应适用）→ 门禁未运行，不得记通过。"
        "「没检到」和「检了没问题」必须分开，否则分母永远是假的" % owner)
    return 1


def do_check(doc, strict=False, ledger_rows=None):
    """跑全部门禁。返回 (findings, counts, rc, stats)。

    R5：五类判据都返回 (checked, cannot)，任一 cannot != 0 → rc = 2。
    旧版只把 COV 的 cannot 计入 rc，其余类别的 CANNOT_RUN 仍 exit 0 —— 这是
    「component_classes 缺失 → PAIR-0 CANNOT_RUN 但退出码 0」的根因。
    """
    findings = []
    stats = {}
    cov_n, cov_c = check_coverage(doc, findings, strict)
    pair_n, pair_c = check_pair(doc, findings, strict)
    ratio_n, ratio_c = check_ratios(doc, findings, stats)
    oi_n, oi_c = check_open_items(doc, findings)
    led_n, led_c = check_ledger(doc, findings, ledger_rows)
    counts = dict(COV=cov_n, PAIR=pair_n, RATIO=ratio_n, OI=oi_n, LEDGER=led_n)
    # R8：每类判据的 checked==0 兜底（适用性各自判定）
    guard = zero_guard(findings, "COV", cov_n, True)
    guard += zero_guard(findings, "PAIR", pair_n, True)
    guard += zero_guard(findings, "RATIO", ratio_n,
                        bool(doc.get("ratio_bands")))
    guard += zero_guard(findings, "OI", oi_n, True)
    guard += zero_guard(findings, "LEDGER", led_n, ledger_rows is not None)
    cannot = cov_c + pair_c + ratio_c + oi_c + led_c + guard
    fails = [f for f in findings if f["level"] == "FAIL"]
    warns = [f for f in findings if f["level"] == "WARN"]
    if cannot:
        return findings, counts, 2, stats
    if fails or (strict and warns):
        return findings, counts, 1, stats
    return findings, counts, 0, stats


def print_report(findings, counts, stats, rc, strict):
    print("=" * 78)
    print("G-OFF 表外缺席门禁 / off-table gaps")
    print("=" * 78)
    for f in findings:
        print("  [%-10s] %-9s %-16s %s" % (f["level"], f["code"], f["object"], f["msg"]))
    print("-" * 78)
    print("  实检：COV %d 册 / PAIR %d 组 / RATIO %d 项 / OI %d 条 / LEDGER %d 行"
          % (counts["COV"], counts["PAIR"], counts["RATIO"], counts["OI"], counts["LEDGER"]))
    if stats:
        print("  RATIO 明细：声明 %d 条 / 实检 %d 项 / 未匹配 %d 项"
              % (stats.get("declared", 0), stats.get("checked", 0),
                 stats.get("unmatched", 0)))
    n_fail = sum(1 for f in findings if f["level"] == "FAIL")
    n_warn = sum(1 for f in findings if f["level"] == "WARN")
    n_info = sum(1 for f in findings if f["level"] == "INFO")
    n_cr = sum(1 for f in findings if f["level"] == "CANNOT_RUN")
    print("  结论：FAIL %d / WARN %d / INFO %d / CANNOT_RUN %d %s" %
          (n_fail, n_warn, n_info, n_cr, "(strict)" if strict else ""))
    print("  exit=%d  (%s)" % (rc, {0: "通过", 1: "有未通过", 2: "门禁未运行"}[rc]))
    return rc


def cmd_init(a):
    save(a.out, template())
    print("已生成登记表:", a.out)
    print("  五类判据：COV 图册页数+状态码 / PAIR 应有项全集+必搭配件 / RATIO 比率带 / "
          "OI 敞口四态 / LEDGER 证据封顶(--ledger)")
    print("  systems 必须声明（bridge/culvert/lighting/building/road/mep）—— "
          "没有参照系就查不出「整组未建」")
    print("  structure_system 必填：simply_supported / continuous_on_bearings / "
          "rigid_frame / integral / arch")
    print("    —— 不声明则按简支梁式桥判并告警；刚构/整体式/拱桥必须显式声明，否则会误报缺支座")
    print("  应有项分三档：required 缺即 FAIL / either 互斥组(≥1 即满足) / "
          "conditional 缺只 INFO(带专业依据)")
    print("  component_classes 须取 references/disciplines-coverage.md §7 受控词表名，"
          "未识别名会让门禁 exit 2（这是防止「按文档命名反而关掉门禁」）")
    print("  注意：ratio_bands 故意留空 —— 不内置基准带；留空即出 INFO「未校验」")
    print("  本项目确无敞口：置 open_items=[] 且 open_items_declared_zero=true")
    return 0


def cmd_check(a):
    if not os.path.exists(a.file):
        print("missing input: %s" % a.file, file=sys.stderr)
        return 2
    try:
        doc = load(a.file)
    except (OSError, ValueError) as e:
        print("cannot read: %s" % e, file=sys.stderr)
        return 2
    ledger_rows = None
    if getattr(a, "ledger", None):
        if not os.path.exists(a.ledger):
            print("missing ledger: %s" % a.ledger, file=sys.stderr)
            return 2
        try:
            ledger_rows = load_ledger(a.ledger)
        except (OSError, ValueError) as e:
            print("cannot read ledger: %s" % e, file=sys.stderr)
            return 2
    findings, counts, rc, stats = do_check(doc, a.strict, ledger_rows)
    if a.out:
        save(a.out, dict(schema=SCHEMA, checked=now(), strict=a.strict,
                         counts=counts, ratio_stats=stats,
                         findings=findings, exit_code=rc))
    return print_report(findings, counts, stats, rc, a.strict)


def cmd_status_ceiling(a):
    code = (a.code or "").upper().strip()
    print("  图册状态码 %s → 允许最高证据强度：%s"
          % (code, STATUS_CEILING.get(code, "未知 → 挂 RFI，不许默认按 IFC 处理")))
    return 0 if code in STATUS_CEILING else 1


# ---------------------------------------------------------------------------
# 自证（自带夹具，不依赖真实项目数据）
# ---------------------------------------------------------------------------
def selftest():
    """自带夹具：改判据的那天恰好没有真实项目数据，所以夹具必须在包里。

    C1~C12 为 v4.6.3 原有 12 用例（夹具按 v2 schema 适配，断言不放宽）；
    C13~C25 为 v4.7.0 整改新增反向用例，每条对应一个 R 项。
    """
    cases = []
    canon, families, err = class_canon()

    def case(name, doc, want_rc, want_code, strict=False, ledger=None):
        findings, _, rc, _ = do_check(doc, strict, ledger)
        hit = any(f["code"] == want_code for f in findings)
        codes = [f["code"] for f in findings if f["code"] == want_code]
        cases.append((name, rc == want_rc and hit,
                      "rc=%d(期望%d) %s hit=%d" % (rc, want_rc, want_code, len(codes))))

    def raw_case(name, ok, desc):
        cases.append((name, ok, desc))

    # --- 干净夹具：满编 bridge 台账 + 状态码 + 合法 CLOSED 依据 ---------------
    def _all_of(sysd):
        """system 定义的全部 class（required+either+conditional+optional）。"""
        return sorted(set(sysd["required"]) | set(sysd.get("optional") or [])
                      | set(sysd.get("conditional") or [])
                      | {c for g in (sysd.get("either") or []) for c in g})

    bridge_all = _all_of(SYSTEM_EXPECTED["bridge"])
    base = template()
    base["drawings"] = [dict(sheet="S-1", scope_status="in_scope",
                             pages_total=10, pages_valid=10, blank_pages=0,
                             status_code="IFC", note="")]
    base["systems"] = ["bridge"]
    base["structure_system"] = "beam"
    base["component_classes"] = list(bridge_all)
    base["system_waivers"] = {}
    base["ratio_bands"] = []
    base["observed_ratios"] = []
    base["open_items"] = [dict(id="OI-01", status="CLOSED",
                              basis="HA-035034-01 立面 x642", impact="", note="")]
    base["open_items_declared_zero"] = False
    base["scan_files"] = []
    clean = copy.deepcopy(base)

    def d_of(**mods):
        d = copy.deepcopy(clean)
        d.update(mods)
        return d

    # --- 夹具自身完整性：SYSTEM_EXPECTED 的 class 必须都在 §7 受控词表内 -----
    # （否则「应有项」用了词表外的名字，用户无论怎么写都会被判未识别 → 死锁）
    bad_expect = sorted({c for sysd in SYSTEM_EXPECTED.values() for c in _all_of(sysd)
                         if c not in canon})
    raw_case("C0 应有项取自§7词表", not err and not bad_expect,
             "canon=%d 词表外=%s" % (len(canon), bad_expect or "无"))

    # C0b 判据自身质量：每个 conditional **及 either 组内项**必须有专业依据
    #（else 就是「塞进条件档换零误报」，或互斥组报缺时给不出理由）
    _need_why = set()
    for sysd in SYSTEM_EXPECTED.values():
        _need_why |= set(sysd.get("conditional") or [])
        for grp in (sysd.get("either") or []):
            _need_why |= set(grp)
    no_why = sorted(c for c in _need_why if c not in CONDITIONAL_WHY)
    raw_case("C0b 条件项均有依据", not no_why,
             "需依据项=%d 缺依据=%s" % (len(_need_why), no_why or "无"))

    # C0c 跨分部纯净：桥与涵洞是两个独立分部（appurtenance §6 02.OUVRAGES D'ART vs 03.BUSES）
    # bridge 不得含涵洞专属项；各系统 required 也不得跨分部（rebar/conduit 这类通用项除外）
    CULVERT_ONLY = {"culvert", "head_wall", "apron"}
    leak = sorted(CULVERT_ONLY & set(_all_of(SYSTEM_EXPECTED["bridge"])))
    raw_case("C0c 桥涵分部不混", not leak,
             "bridge含涵洞项=%s" % (leak or "无"))

    # C1 干净夹具必须不被误咬（防误报）
    findings, _, rc, _ = do_check(copy.deepcopy(clean))
    hard = [f for f in findings if f["level"] in ("FAIL", "WARN", "CANNOT_RUN")]
    raw_case("C1 干净夹具不误咬", rc == 0 and not hard,
             "rc=%d hard=%d %s" % (rc, len(hard), [f["code"] for f in hard][:4]))

    # C2 缺页必须 FAIL
    d = d_of()
    d["drawings"][0]["pages_valid"] = 6
    case("C2 缺页/空白页", d, 1, "COV-2")

    # C3 未登记有效页数记「未核」，strict 下 FAIL
    # （pages_total+pages_valid 或 pages_total+blank_pages 两种合法登记方式；
    #   两者皆缺才是「未核」——不能把合法登记误判成未核）
    d = d_of()
    d["drawings"][0]["pages_valid"] = None
    d["drawings"][0]["blank_pages"] = None
    case("C3 未登记页数 strict", d, 1, "COV-1", strict=True)

    # C4 无范围内图纸 → exit 2 未运行
    d = d_of(drawings=[])
    findings, _, rc, _ = do_check(d)
    raw_case("C4 无图判未运行", rc == 2, "rc=%d" % rc)

    # C5 必搭配件缺项（有梁无支座）
    # v4.7.1：bearing 已进 bridge 的 required 档 → 该缺项由 PAIR-6R 判 FAIL（更严，非放宽）；
    #         companion 判据（PAIR-1）在 required 未覆盖时仍独立生效，见 C5b。
    d = d_of(component_classes=["girder", "pier_cap"])
    case("C5 有梁无支座", d, 1, "PAIR-6R")

    # C5b companion 判据独立生效：lighting 下 cable_handhole 等只在条件档，
    #     故「有灯杆无手孔」必须由附加配对判据 PAIR-1 报出（不是被 required 档抢走）
    d = d_of(systems=["lighting"], component_classes=["lighting_pole"])
    findings, _, rc, _ = do_check(d)
    p1 = {f["object"] for f in findings if f["code"] == "PAIR-1"}
    raw_case("C5b 附加配对待仍生效",
             {"cable_handhole", "earth_electrode", "pole_foundation"} <= p1,
             "PAIR-1=%s" % sorted(p1))

    # C6 无 band 必须明写「未校验」，不得静默通过
    findings, _, rc, _ = do_check(copy.deepcopy(clean))
    raw_case("C6 无带明写未校验",
             any(f["code"] == "RATIO-0" and f["level"] == "INFO" for f in findings),
             "INFO present")

    # C7 带缺 declared_by 必须 FAIL
    d = d_of(ratio_bands=[dict(id="RB-1", band=[120, 180], declared_by="常规值")])
    case("C7 AI 自拟带", d, 1, "RATIO-3")

    # C8 比率带外只 WARN 且文案不替人下结论
    d = d_of(ratio_bands=[dict(id="RB-1", band=[120, 180], numerator_measure="mass",
                               declared_by="结构设计说明 §4.2")],
             observed_ratios=[dict(object="pier_column", measure="mass",
                                   value=450, unit="kg/m3")])
    findings, _, rc, _ = do_check(d)
    hit = [f for f in findings if f["code"] == "RATIO-1"]
    raw_case("C8 比率带外只指路",
             bool(hit) and hit[0]["level"] == "WARN" and "由人下结论" in hit[0]["msg"],
             "warn+hedge")

    # C9 「挂账」不是状态
    d = d_of()
    d["open_items"][0]["status"] = "挂账"
    case("C9 挂账未判态", d, 1, "OI-1")

    # C10 已结但拿不出图号
    d = d_of()
    d["open_items"][0]["basis"] = "已核"
    case("C10 已结无图证", d, 1, "OI-2")

    # C11 僵尸标记
    d = d_of(scan_files=[dict(path="gen.py", content="x = 1  # OI-01 推定区·不得进报价")])
    case("C11 僵尸标记", d, 1, "OI-3")

    # C12 混态未拆条
    d = d_of()
    d["open_items"][0]["note"] = "已结；但台身厚仍待核"
    case("C12 混态未拆条", d, 1, "OI-8")

    # ===================== v4.7.0 新增反向用例 =====================
    # C13 R5/R8：component_classes 缺失 → PAIR-0 CANNOT_RUN → rc 必须 2（旧版 rc=0）
    d = d_of()
    d.pop("component_classes", None)
    case("C13 class集合缺失→rc2", d, 2, "PAIR-0")

    # C14 R6：形态 3 —— systems=bridge 而台账只有 pier_column（上部整组未建）
    # 期望：required 档逐条 FAIL（PAIR-6R），rc=1
    d = d_of(systems=["bridge"], component_classes=["pier_column"])
    findings, _, rc, _ = do_check(d)
    miss = [f for f in findings if f["code"] == "PAIR-6R"]
    raw_case("C14 整组未建(上部全漏)",
             len(miss) >= 3 and rc == 1
             and {"girder", "bearing", "deck_slab"} <= {f["object"] for f in miss},
             "PAIR-6R×%d rc=%d 例:%s" % (len(miss), rc, [f["object"] for f in miss[:3]]))

    # C15 R7：§7 规范名 lighting_pole + systems=lighting → 不得因命名空转而放过
    # 期望：cable_handhole/earth_electrode 等落入「条件应有」INFO（旧版是 PAIR 0 组 exit 0）
    d = d_of(systems=["lighting"], component_classes=["lighting_pole"])
    findings, _, rc, _ = do_check(d)
    miss = [f for f in findings if f["code"].startswith("PAIR-6")]
    present = {f["object"] for f in miss}
    raw_case("C15 §7规范名不空转",
             {"cable_handhole", "earth_electrode", "luminaire"} <= present
             and not any(f["code"] == "PAIR-4" for f in findings),
             "PAIR-6*×%d rc=%d" % (len(miss), rc))

    # C16 R7：未识别 class → CANNOT_RUN → rc 2（比 主梁/支座 这类已登记别名更硬的乱码名）
    d = d_of(component_classes=["构件甲", "构件乙"])
    case("C16 未识别class→rc2", d, 2, "PAIR-4")

    # C17 R10：valid 10 + blank 8 > total 10 → 自相矛盾 FAIL
    d = d_of()
    d["drawings"][0]["pages_valid"] = 10
    d["drawings"][0]["blank_pages"] = 8
    case("C17 页数登记自相矛盾", d, 1, "COV-2")

    # C18 R10：pages_total="10" 字符串 → CANNOT_RUN rc2 且不抛 TypeError
    d = d_of()
    d["drawings"][0]["pages_total"] = "10"
    try:
        findings, _, rc, _ = do_check(d)
        crashed = False
    except Exception as e:            # noqa: BLE001 - 自证就是要证明它不崩
        crashed, rc, findings = True, -1, []
        print("    C18 崩溃：%r" % (e,))
    raw_case("C18 字符串页数不崩溃", (not crashed and rc == 2
                                    and any(f["code"] == "COV-3" for f in findings)),
             "rc=%d crash=%s" % (rc, crashed))

    # C19 R11：观测值 "9999" 字符串 → 必须能转数并报带外 WARN（旧版静默 0 项 exit 0）
    d = d_of(ratio_bands=[dict(id="RB-1", band=[120, 180], numerator_measure="mass",
                               declared_by="结构设计说明 §4.2 表 3")],
             observed_ratios=[dict(object="pier_column", measure="mass",
                                   value="9999", unit="kg/m3")])
    findings, _, rc, stats = do_check(d)
    hit = [f for f in findings if f["code"] == "RATIO-1"]
    raw_case("C19 字符串观测值仍校验",
             bool(hit) and hit[0]["level"] == "WARN" and stats.get("checked") == 1,
             "checked=%s hit=%d" % (stats.get("checked"), len(hit)))

    # C20 R12：declared_by="AI 自拟" → FAIL（旧版纯中文词表放行）
    d = d_of(ratio_bands=[dict(id="RB-1", band=[120, 180], declared_by="AI 自拟")])
    case("C20 英文/中文占位带", d, 1, "RATIO-3")

    # C21 R13：open_items=[] 无声明 → FAIL 且提示正确写法
    d = d_of(open_items=[], open_items_declared_zero=False)
    findings, _, rc, _ = do_check(d)
    hit = [f for f in findings if f["code"] == "OI-0" and f["level"] == "FAIL"]
    raw_case("C21 空台账无声明→FAIL",
             rc == 1 and bool(hit) and "open_items_declared_zero" in hit[0]["msg"],
             "rc=%d hint=%s" % (rc, bool(hit) and "open_items_declared_zero" in hit[0]["msg"]))

    # C22 R13：open_items=[] + declared_zero=true → PASS（合法出路，不逼人造假 CLOSED）
    d = d_of(open_items=[], open_items_declared_zero=True)
    findings, _, rc, _ = do_check(d)
    raw_case("C22 声明无敞口→PASS",
             rc == 0 and any(f["code"] == "OI-0" and f["level"] == "INFO" for f in findings),
             "rc=%d" % rc)

    # C23 R13：SCOPED 不带任何其他字段 → FAIL（旧版照单放行）
    d = d_of()
    d["open_items"] = [dict(id="OI-09", status="SCOPED")]
    case("C23 SCOPED无字段→FAIL", d, 1, "OI-6")

    # C24 R12：attributes_note 一个字母 "x" → FAIL（不再降级放行）
    # 用 lighting 的 cable_handhole（条件档，不被 required 覆盖），确保走到 PAIR-2 分支
    d = d_of(systems=["lighting"], component_classes=["lighting_pole"],
             attributes_note={"cable_handhole": "x"})
    findings, _, rc, _ = do_check(d)
    hit = [f for f in findings if f["code"] == "PAIR-2"]
    raw_case("C24 字据不合格→FAIL",
             rc == 1 and bool(hit) and hit[0]["level"] == "FAIL",
             "rc=%d PAIR-2=%s" % (rc, [f["level"] for f in hit]))

    # C25 R9：status_code=APS + --ledger 标「图读·标注」→ 封顶生效 FAIL
    d = d_of()
    d["drawings"][0]["status_code"] = "APS"
    case("C25 证据强度超封顶", d, 1, "LEDGER-3",
         ledger=[dict(drawing="S-1", evidence_word="图读·标注")])

    # C26 R8：checked==0 守卫本身的单测（兜底路径不可由普通夹具自然触发，
    #         故直接调 zero_guard 证明它确实会补一条 CANNOT_RUN 而不是静默放行）
    f0 = []
    g = zero_guard(f0, "PAIR", 0, True)
    g2 = zero_guard(f0, "PAIR", 3, True)          # 有实检 → 不补
    g3 = zero_guard(f0, "RATIO", 0, False)        # 不适用 → 不补
    raw_case("C26 checked=0守卫生效",
             g == 1 and g2 == 0 and g3 == 0
             and any(x["code"] == "PAIR-8" and x["level"] == "CANNOT_RUN" for x in f0),
             "guard=%d no_double=%d inapplicable=%d" % (g, g2, g3))

    # ============ v4.7.1 误报归零（team-lead 硬门槛，6 场景） ============
    def no_hard(fs, allow_warn=()):
        """无 FAIL/CANNOT_RUN；WARN 只允许 allow_warn 里的 code。"""
        return not [f for f in fs
                    if f["level"] in ("FAIL", "CANNOT_RUN")
                    or (f["level"] == "WARN" and f["code"] not in allow_warn)]

    # C27 完整桥梁 26 项（无涵洞）→ 必须 rc=0，且不得报涵洞分部
    #    这是 team-lead 独立复现的原误报场景：旧版报 culvert/head_wall/apron/pile_cap/underbridge_lighting
    BRIDGE26 = ["pile", "spread_footing", "pier_column", "pier_cap", "abutment",
                "bearing", "bearing_pedestal", "shear_key", "girder", "diaphragm",
                "wet_joint", "end_diaphragm", "deck_slab", "deck_waterproofing",
                "asphalt_wearing", "parapet", "expansion_joint", "drainage_spout",
                "approach_slab", "bridge_road_marking", "bridge_lighting",
                "inspection_walkway", "wing_wall", "rebar", "formwork", "excavation"]
    d = d_of(systems=["bridge"], component_classes=list(BRIDGE26))
    findings, _, rc, _ = do_check(d)
    culvert_leak = [f for f in findings if f["object"] in ("culvert", "head_wall", "apron")]
    raw_case("C27 完整桥梁26项不误报",
             rc == 0 and no_hard(findings) and not culvert_leak,
             "rc=%d hard=%d 涵洞误报=%d"
             % (rc, len([f for f in findings if f["level"] in ("FAIL", "WARN")]),
                len(culvert_leak)))

    # C28 桥梁整组上部未建（只有 pier_column）→ rc=1，命中 required 多项
    d = d_of(systems=["bridge"], component_classes=["pier_column"])
    findings, _, rc, _ = do_check(d)
    req_miss = [f for f in findings if f["code"] == "PAIR-6R"]
    raw_case("C28 上部整组未建→FAIL",
             rc == 1 and len(req_miss) >= 5,
             "rc=%d PAIR-6R×%d" % (rc, len(req_miss)))

    # C29 扩大基础替代承台（无 pile_cap，也无 pier_cap —— 单柱墩/实体墩）→ rc=0
    d = d_of(systems=["bridge"], component_classes=["pier_column", "spread_footing",
                                                   "abutment", "bearing", "bearing_pedestal",
                                                   "girder", "deck_slab", "parapet",
                                                   "approach_slab", "rebar"])
    findings, _, rc, _ = do_check(d)
    raw_case("C29 扩大基础替代承台→PASS",
             rc == 0 and not any(f["code"] == "PAIR-6E" for f in findings),
             "rc=%d PAIR-6E=%d" % (rc, len([f for f in findings if f["code"] == "PAIR-6E"])))

    # C30 完整涵洞 → rc=0（且不得报桥梁分部项）
    CULVERT_ALL = _all_of(SYSTEM_EXPECTED["culvert"])
    d = d_of(systems=["culvert"], component_classes=list(CULVERT_ALL))
    findings, _, rc, _ = do_check(d)
    bridge_leak = [f for f in findings if f["object"] in ("girder", "bearing", "pier_cap")]
    raw_case("C30 完整涵洞→PASS",
             rc == 0 and no_hard(findings) and not bridge_leak,
             "rc=%d 桥项误报=%d" % (rc, len(bridge_leak)))

    # C31 圆管涵：端墙替代翼墙、无搭板 → 不得 FAIL（只允许 INFO）
    d = d_of(systems=["culvert"],
             component_classes=["culvert", "head_wall", "rebar", "apron"])
    findings, _, rc, _ = do_check(d)
    raw_case("C31 圆管涵无翼墙不FAIL",
             rc == 0 and no_hard(findings),
             "rc=%d hard=%d INFO=%d"
             % (rc, len([f for f in findings if f["level"] in ("FAIL", "WARN")]),
                len([f for f in findings if f["level"] == "INFO"])))

    # C32 刚构桥（rigid_frame，无 bearing/bearing_pedestal/expansion_joint/pier_cap）→ rc=0
    d = d_of(systems=["bridge"], structure_system="rigid_frame",
             component_classes=["pier_column", "abutment", "girder", "deck_slab",
                                "parapet", "approach_slab", "rebar", "pile_cap"])
    findings, _, rc, _ = do_check(d)
    demoted = [f for f in findings if f["code"] == "PAIR-6D"]
    raw_case("C32 刚构桥无支座不误报",
             rc == 0 and no_hard(findings)
             and {"bearing", "bearing_pedestal"} <= {f["object"] for f in demoted},
             "rc=%d 降级项=%d hard=%d%s"
             % (rc, len(demoted),
                len([f for f in findings if f["level"] in ("FAIL", "WARN")]),
                [f["object"] for f in findings if f["level"] in ("FAIL","WARN")][:3]))

    # C33 刚构桥若**未**声明 structure_system（默认 beam）→ 应 FAIL（降级不能白送）
    d = d_of(systems=["bridge"],
             component_classes=["pier_column", "abutment", "girder", "deck_slab",
                                "parapet", "approach_slab", "rebar", "pile_cap"])
    findings, _, rc, _ = do_check(d)
    raw_case("C33 未声明体系不白送",
             rc == 1 and any(f["code"] == "PAIR-6R" and f["object"] == "bearing"
                             for f in findings),
             "rc=%d" % rc)

    # ============ v4.7.2 向后兼容 + 体系取值归一 ============
    # C34 旧格式（v4.6.x 无 systems 字段）不得硬断裂：可由 class 反推，且反推不得
    #     把桥梁判成涵洞（曾出现 bridge∩culvert 通用项导致桥梁被要求提供涵洞项）
    d = d_of()
    d.pop("systems", None)
    findings, _, rc, _ = do_check(d)
    inferred = [f for f in findings if f["code"] == "PAIR-3"]
    culvert_req = [f for f in findings
                   if f["code"] in ("PAIR-6R", "PAIR-6E")
                   and str(f["object"]) in ("culvert", "head_wall", "apron",
                                            "head_wall/wing_wall")]
    raw_case("C34 旧格式可反推不误判",
             rc == 0 and inferred and not culvert_req
             and inferred[0]["level"] == "INFO",
             "rc=%d PAIR-3=%s 涵洞误报=%d"
             % (rc, inferred[0]["level"] if inferred else "-", len(culvert_req)))

    # C35 完全反推不出（class 为零）→ 仍须 CANNOT_RUN，不得静默放行
    d = d_of(component_classes=[])
    findings, _, rc, _ = do_check(d)
    raw_case("C35 反推不出仍拒跑", rc == 2,
             "rc=%d" % rc)

    # C36 structure_system 取值归一：team-lead 三值 + 历史同义词 / 中文 / 旧 beam
    norm_ok = (STRUCTURE_SYSTEM_ALIASES.get("simply_supported") == "simply_supported"
               and STRUCTURE_SYSTEM_ALIASES.get("continuous_on_bearings")
               == "continuous_on_bearings"
               and STRUCTURE_SYSTEM_ALIASES.get("rigid_frame") == "rigid_frame"
               and STRUCTURE_SYSTEM_ALIASES.get("beam") == "simply_supported"
               and STRUCTURE_SYSTEM_ALIASES.get("刚构") == "rigid_frame")
    raw_case("C36 体系取值归一", norm_ok,
             "别名表 %d 项" % len(STRUCTURE_SYSTEM_ALIASES))

    # C37 未声明 structure_system → WARN PAIR-3（strict 下 FAIL），明写按默认适用
    d = d_of()
    d.pop("structure_system", None)
    findings, _, rc, _ = do_check(d)
    soft = [f for f in findings if f["code"] == "PAIR-3"]
    findings_s, _, rc_s, _ = do_check(copy.deepcopy(d), strict=True)
    # 未声明体系必须是 INFO 且**两档都不改变 rc**（未填新增字段不得判红正确项目）
    raw_case("C37 未声明体系仅提示",
             rc == 0 and soft and soft[0]["level"] == "INFO" and rc_s == 0,
             "非strict rc=%d %s / strict rc=%d"
             % (rc, soft[0]["level"] if soft else "-", rc_s))

    # C38 五种合法体系取值都不得因体系本身报错（刚构/整体式/拱桥不得要求支座）
    ss_bad = []
    for v in ("simply_supported", "continuous_on_bearings", "rigid_frame",
              "integral", "arch"):
        dd = d_of(structure_system=v)
        fs, _, r, _ = do_check(dd)
        if r != 0:
            ss_bad.append((v, r))
    raw_case("C38 五种体系取值均可用", not ss_bad,
             "异常=%s" % (ss_bad or "无"))

    print("=" * 78)
    print("G-OFF 自证（自带夹具，不依赖真实项目数据）")
    print("=" * 78)
    for name, ok, desc in cases:
        print("  %-26s %-4s %s" % (name, "OK" if ok else "FAIL", desc))
    passed = sum(1 for _, ok, _ in cases if ok)
    print("-" * 78)
    print("  用例覆盖 %d/%d" % (passed, len(cases)))
    return 0 if passed == len(cases) else 1


def main():
    ap = argparse.ArgumentParser(description="G-OFF 表外缺席门禁", allow_abbrev=False)
    sub = ap.add_subparsers(dest="cmd")
    p1 = sub.add_parser("init", help="生成登记表模板")
    p1.add_argument("--out", required=True)
    p1.set_defaults(func=cmd_init)
    p2 = sub.add_parser("check", help="跑五类表外缺席判据")
    p2.add_argument("--file", required=True)
    p2.add_argument("--strict", action="store_true")
    p2.add_argument("--out")
    p2.add_argument("--ledger",
                    help="证据强度台账 CSV（表头 drawing,evidence_word），"
                         "校验证据词不超过该图册 status_code 封顶")
    p2.set_defaults(func=cmd_check)
    p3 = sub.add_parser("status", help="查图册状态码 → 证据强度上限")
    p3.add_argument("--code", required=True)
    p3.set_defaults(func=cmd_status_ceiling)
    p4 = sub.add_parser("selftest", help="自带夹具自证")
    p4.set_defaults(func=lambda a: selftest())
    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
