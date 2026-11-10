#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""大型项目算量总控（v4.0.0 新增）。

解决三件事：
1) **按计划连续推进**：批次队列 + 依赖 + 验收条件写在计划里，不边想边做；
2) **不被打断拖死**：OI 一律挂账继续，只有硬停点才停；每批次一行进度，不写过程长文；
3) **随时能接续**：状态落盘（plan/state 两个 JSON），会话中断、token 耗尽、
   换一个人接手，都能用 `resume` 恢复到"下一批该做什么"。

配套：测量策略卡（contract/measurement 未确认不得出交付版）。

用法（项目根目录下）：
  python scripts/project_orchestrator.py init --root . --project "Port City" --discipline 房建
  python scripts/project_orchestrator.py add-batch --root . --id B01 --title "建册与普查" \
      --scope "全部图纸目录" --inputs "图纸目录" --accept "建册 CONFIRMED;空壳与复本已登记" --budget medium
  python scripts/project_orchestrator.py next --root .
  python scripts/project_orchestrator.py start --root . --id B01
  python scripts/project_orchestrator.py done --root . --id B01 --readings 26 --gates "7/2/0" --tokens 12000
  python scripts/project_orchestrator.py oi --root . --id B01 --text "户数 653 vs 658 待业主确认"
  python scripts/project_orchestrator.py resume --root .
  python scripts/project_orchestrator.py card-init --root .
  python scripts/project_orchestrator.py card-check --root .
  python scripts/project_orchestrator.py selftest
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time

from qto_core import atomic_write_json

PLAN_REL = os.path.join("takeoff", "plan", "project_plan.json")
STATE_REL = os.path.join("takeoff", "plan", "project_state.json")
CARD_REL = os.path.join("takeoff", "plan", "measurement_strategy_card.json")
STATUSES = ("PENDING", "IN_PROGRESS", "DONE", "BLOCKED")


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _normalize_root(root):
    """把 --root 归一成项目根绝对路径，并拦住最常见的误用。

    **抓过的事故（2026-10-29 实测）**
    PLAN_REL 自带 `takeoff/plan/` 前缀，所以 --root 必须是**项目根**。
    在 `.../viaduc_pk295/takeoff` 目录下传 `--root plan`，不会报错，
    而是静默生成 `takeoff/plan/takeoff/plan/project_state.json`；
    后续每条命令都打印 [OK]，但 `status` 读的仍是项目根下的旧文件——
    表现为「命令全成功、数据一点没变」，查了很久才发现是路径套娃。

    判据：root 的末级目录不能是 plan / takeoff（那是相对路径套在
    PLAN_REL 前缀上才会出现的形态）。命中即报错并给出正确写法，
    绝不"帮用户猜一个路径继续写"——写错地方比写失败更难发现。
    """
    p = os.path.abspath(root)
    tail = os.path.basename(p).lower()
    if tail in ("plan", "takeoff"):
        return False, (
            "[ERR] --root 指向了 %s，但本工具的路径常量已含 takeoff/plan/ 前缀，\n"
            "      继续执行会在 %s 下再套一层，写进去的进度读不回来。\n"
            "      正确用法：--root 传**项目根**（含 takeoff/ 的那一级），例如\n"
            "        cd <项目根> && python project_orchestrator.py status --root .\n"
            "      或直接传绝对路径 --root \"D:/proj/xxx\"。"
            % (p, os.path.join(p, "takeoff", "plan")))
    return True, p


def _load(root, rel, required=True):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        if required:
            raise SystemExit(f"[ERR] 缺少 {rel}，先运行 init")
        return None
    with io.open(path, encoding="utf-8") as fp:
        return json.load(fp)


def _save(root, rel, obj):
    atomic_write_json(os.path.join(root, rel), obj)


def _batch(plan, bid):
    for b in plan["batches"]:
        if b["id"] == bid:
            return b
    raise SystemExit(f"[ERR] 计划里没有批次 {bid}")


def _bs(state, bid):
    return state["batches"].setdefault(
        bid, {"status": "PENDING", "readings": 0, "gates": "", "oi": [],
              "tokens": None, "history": []})


def _event(state, bid, action, note=""):
    entry = {"at": now(), "batch": bid, "action": action, "note": note}
    state["log"].append(entry)
    if bid:
        _bs(state, bid)["history"].append(entry)
    state["updated_at"] = now()


def _next_batch(plan, state):
    for b in plan["batches"]:
        st = _bs(state, b["id"])["status"]
        if st != "PENDING":
            continue
        deps = b.get("depends_on", [])
        if all(_bs(state, d)["status"] == "DONE" for d in deps):
            return b
    return None


def _progress(state):
    done = sum(1 for v in state["batches"].values() if v["status"] == "DONE")
    blocked = sum(1 for v in state["batches"].values() if v["status"] == "BLOCKED")
    readings = sum(int(v.get("readings") or 0) for v in state["batches"].values())
    ois = sum(len(v.get("oi") or []) for v in state["batches"].values())
    tokens = sum(int(v["tokens"]) for v in state["batches"].values() if v.get("tokens"))
    return done, blocked, readings, ois, tokens


def cmd_init(a):
    if os.path.isfile(os.path.join(a.root, PLAN_REL)) and not a.force:
        print("[ERR] 计划已存在（--force 可重建，重建会清空状态）")
        return 2
    plan = {
        "schema_version": "1.0.0", "project": a.project, "discipline": a.discipline,
        "created_at": now(),
        "policy": {
            "continuous": "默认连续推进；OI 挂账继续，只有 SKILL.md 停止条件与批次验收需人裁决才停",
            "progress": "每批次一行：批次号 | 落账行数 | 门禁(过/未运行/待确认) | 新增OI | 下一批",
            "per_batch": "先普查后开项；先验重后落账；缺口三分类（图有量无/量有图无/图在标注不足）",
            "token": "索引先行、按需读取、内容指纹跳过未变文件；超预算降级为登记+挂账，不硬撑全量",
        },
        "batches": [],
    }
    state = {"project": a.project, "updated_at": now(), "batches": {}, "log": []}
    for b in plan["batches"]:
        _bs(state, b["id"])
    _save(a.root, PLAN_REL, plan)
    _save(a.root, STATE_REL, state)
    _event(state, "", "init", a.project)
    _save(a.root, STATE_REL, state)
    # ★打印绝对路径：旧版只打印相对路径 "takeoff\plan\..."，
    #   即使 root 传错（套娃）看上去也一模一样，无法自查。
    print(f"[OK] 已建立总控：{os.path.join(a.root, PLAN_REL)}"
          f" / {os.path.join(a.root, STATE_REL)}")
    print("下一步：add-batch 登记批次（含输入契约与验收条件），或 card-init 生成测量策略卡")
    return 0


def cmd_add_batch(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    if any(b["id"] == a.id for b in plan["batches"]):
        print(f"[ERR] 批次 {a.id} 已存在")
        return 2
    batch = {"id": a.id, "title": a.title, "scope": a.scope,
             "inputs": [x for x in a.inputs.split(";") if x],
             "acceptance": [x for x in a.accept.split(";") if x],
             "depends_on": [x for x in a.depends.split(",") if x],
             "token_budget": a.budget}
    plan["batches"].append(batch)
    _bs(state, a.id)
    _event(state, a.id, "add-batch", a.title)
    _save(a.root, PLAN_REL, plan)
    _save(a.root, STATE_REL, state)
    print(f"[OK] 批次 {a.id} 已入队（{a.title}），当前共 {len(plan['batches'])} 批")
    return 0


def _print_card(plan, state, b):
    print("=" * 70)
    print(f"下一批任务卡：{b['id']} {b['title']}")
    print(f"  范围：{b.get('scope') or '（未填）'}")
    print(f"  输入契约：{'；'.join(b.get('inputs') or []) or '（未填）'}")
    print(f"  验收条件：{'；'.join(b.get('acceptance') or []) or '（未填）'}")
    print(f"  依赖：{','.join(b.get('depends_on') or []) or '无'}｜token 预算档：{b.get('token_budget')}")
    print("  执行提示：先普查后开项、先验重后落账；OI 挂账继续；完成后 done 登记再取下一批")
    print("=" * 70)


def cmd_next(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    b = _next_batch(plan, state)
    if not b:
        done, blocked, readings, ois, tokens = _progress(state)
        print(f"无可执行批次（完成 {done}，阻断 {blocked}，待办依赖未解或全部完成）")
        return 1 if blocked else 0
    _print_card(plan, state, b)
    return 0


def cmd_start(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    _batch(plan, a.id)
    bs = _bs(state, a.id)
    if bs["status"] == "DONE":
        print(f"[ERR] {a.id} 已完成，需返工请用 reopen（不要静默改数）")
        return 2
    bs["status"] = "IN_PROGRESS"
    bs["started_at"] = now()
    _event(state, a.id, "start")
    _save(a.root, STATE_REL, state)
    print(f"[OK] {a.id} 开工（{now()}）")
    return 0


def cmd_done(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    b = _batch(plan, a.id)
    bs = _bs(state, a.id)
    bs["status"] = "DONE"
    bs["finished_at"] = now()
    bs["readings"] = a.readings
    bs["gates"] = a.gates
    if a.tokens is not None:
        bs["tokens"] = a.tokens
    _event(state, a.id, "done", f"readings={a.readings} gates={a.gates} {a.note}")
    _save(a.root, STATE_REL, state)
    done, blocked, readings, ois, tokens = _progress(state)
    print(f"{a.id} | 落账 {a.readings} 行 | 门禁 {a.gates} | 累计 OI {ois} | 累计落账 {readings} 行")
    nb = _next_batch(plan, state)
    print(f"下一批：{nb['id']} {nb['title']}" if nb else "全部已排队批次执行完毕")
    return 0


def cmd_oi(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    _batch(plan, a.id)
    _bs(state, a.id)["oi"].append({"at": now(), "text": a.text, "status": "OPEN"})
    _event(state, a.id, "oi", a.text)
    _save(a.root, STATE_REL, state)
    print(f"[挂账继续] {a.id} 新增 OI：{a.text}（不阻断本批与后续批次；硬停点见 SKILL.md 停止条件）")
    return 0


def cmd_block(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    _batch(plan, a.id)
    bs = _bs(state, a.id)
    bs["status"] = "BLOCKED"
    _event(state, a.id, "block", a.text)
    _save(a.root, STATE_REL, state)
    print(f"[硬停点] {a.id} 阻断：{a.text}。解除后把状态改回 IN_PROGRESS 再继续")
    return 1


def cmd_reopen(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    _batch(plan, a.id)
    _bs(state, a.id)["status"] = "IN_PROGRESS"
    _event(state, a.id, "reopen", a.text)
    _save(a.root, STATE_REL, state)
    print(f"[OK] {a.id} 已重开（返工原因已留痕：{a.text}）")
    return 0


def cmd_status(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    if getattr(a, "json", ""):
        print(json.dumps({"plan": plan, "state": state}, ensure_ascii=False, indent=1))
        return 0
    print(f"项目：{plan['project']}（{plan.get('discipline') or '专业未定'}） 更新：{state['updated_at']}")
    for b in plan["batches"]:
        bs = _bs(state, b["id"])
        print(f"  {b['id']:<6} {bs['status']:<12} 落账{bs.get('readings') or 0:>5}  "
              f"门禁{bs.get('gates') or '-':<10} OI{len(bs.get('oi') or []):>2}  {b['title']}")
    done, blocked, readings, ois, tokens = _progress(state)
    print(f"合计：{len(plan['batches'])} 批｜完成 {done}｜阻断 {blocked}｜落账 {readings} 行｜OI {ois} 条｜已记 token {tokens}")
    return 0


def cmd_resume(a):
    plan = _load(a.root, PLAN_REL)
    state = _load(a.root, STATE_REL)
    cmd_status(a)
    inflight = [b["id"] for b in plan["batches"] if _bs(state, b["id"])["status"] == "IN_PROGRESS"]
    if inflight:
        print(f"中断恢复点：{','.join(inflight)} 处于 IN_PROGRESS——先核对这批的落账与门禁是否已写盘，"
              "未写盘的按本批验收条件补跑，不要整批重来")
    blocked = [b["id"] for b in plan["batches"] if _bs(state, b["id"])["status"] == "BLOCKED"]
    if blocked:
        print(f"阻断未解：{','.join(blocked)}（解除后 reopen）")
    return cmd_next(a)


CARD_TEMPLATE = {
    "schema_version": "1.0.0",
    "project": "TO_BE_CONFIRMED",
    "discipline": "TO_BE_CONFIRMED",
    "measurement_standard": "TO_BE_CONFIRMED",
    "measurement_standard_version": "TO_BE_CONFIRMED",
    "project_specific_amendments": [],
    "unit_system": "SI",
    "quantity_layers": {
        "design_theoretical": "设计理论量（图纸/模型直接几何量）",
        "contract_measured": "合同计量量（按指定测量规则扣减/分类后）",
        "material_requirement": "材料需求量（含经批准的损耗参数，单列）",
        "procurement": "采购量（按包装/供货规格取整，单列）",
        "tender_boq": "投标/BOQ 数量（按合同清单口径）",
    },
    "waste_policy": "TO_BE_CONFIRMED（净量与损耗不得混写；损耗率必须有项目批准出处）",
    "drawing_baseline": "TO_BE_CONFIRMED（有效版本/补遗/冲突裁决规则）",
    "scale_policy": "逐张图纸双校准；未校准或超差不得出数",
    "classification": {"work_breakdown": "TO_BE_CONFIRMED", "cost_reporting": "ICMS 或项目指定",
                       "note": "测量规则/工程分类/成本报告/BIM 信息要求四者不得互相替代"},
    "deliverable": {"boq_structure": "TO_BE_CONFIRMED", "rounding": "TO_BE_CONFIRMED",
                    "marked_up_drawings": False, "structured_export": "takeoff/register/算量表.xlsx"},
    # v4.0.0：识图口径三防（不填清就不许出安装量）
    "digitization_rules": {
        "legend_pages_excluded": "TO_BE_CONFIRMED（符号/图例/示意页不计安装量；"
                                  "只用于读懂型号↔符号对应，单列存档）",
        "label_blocks_excluded": "TO_BE_CONFIRMED（标签块/文字标注块不是构件，不参与计数）",
        "replica_policy": "TO_BE_CONFIRMED（同内容指纹只计 1 张；确属不同版本用文件版本时间裁决）",
        "version_conflict_rule": "TO_BE_CONFIRMED（跨专业冲突先比文件版本时间，再按合同优先级）",
    },
    # v4.0.0：体系乘数登记（楼栋/塔数/重复区段 → 全局最大不确定性）
    "system_multiplier": {
        "declared": {},          # 例：{"塔楼": 2, "重复区段": 26}
        "evidence": "",          # 每条乘数的图证/文件依据
        "note": "乘数必须登记来源；未登记的乘数不得隐含在总量里",
    },
    "review": {"critical_items": [], "release_authority": "TO_BE_CONFIRMED",
               "correct_refusal_required": True},
    "approval_status": "DRAFT",
}

REQUIRED_CARD_KEYS = [
    "project", "discipline", "measurement_standard", "measurement_standard_version",
    "drawing_baseline", "scale_policy", "deliverable", "review",
    "digitization_rules", "system_multiplier", "quantity_layers", "approval_status",
]
REQUIRED_LAYER_KEYS = ["design_theoretical", "contract_measured",
                       "material_requirement", "procurement", "tender_boq"]


def cmd_card_init(a):
    if os.path.isfile(os.path.join(a.root, CARD_REL)) and not a.force:
        print("[ERR] 策略卡已存在（--force 重建）")
        return 2
    _save(a.root, CARD_REL, CARD_TEMPLATE)
    print(f"[OK] 测量策略卡已生成：{CARD_REL}（所有 TO_BE_CONFIRMED 必须由项目确认）")
    return 0


def cmd_card_check(a):
    card = _load(a.root, CARD_REL)
    problems = []

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, str) and "TO_BE_CONFIRMED" in obj:
            problems.append(path)
    walk(card)

    # 结构检查：关键段不得缺（缺段＝口径没定，不是"待确认"那种软问题）
    for k in REQUIRED_CARD_KEYS:
        if k not in card:
            problems.append("(缺字段) %s" % k)
    layers = card.get("quantity_layers") or {}
    for k in REQUIRED_LAYER_KEYS:
        if k not in layers:
            problems.append("(缺层) quantity_layers.%s" % k)
    dr = card.get("digitization_rules") or {}
    for k in ("legend_pages_excluded", "label_blocks_excluded", "replica_policy",
              "version_conflict_rule"):
        if k not in dr:
            problems.append("(缺口径) digitization_rules.%s" % k)

    if problems:
        print("[FAIL] 测量策略卡存在未确认项（%d）：" % len(problems))
        for p in problems:
            print("   -", p)
        print("计量依据/口径/版本未确认前，不得出 DELIVERED；只能标 DRAFT 并请求确认")
        return 1
    if card.get("approval_status") != "APPROVED":
        print("[FAIL] approval_status 不是 APPROVED（当前：%s）" % card.get("approval_status"))
        return 1
    print("[PASS] 测量策略卡已确认（计量依据/数量分层/识图口径/乘数登记/交付/审核责任齐备）")
    return 0


def cmd_selftest(a):
    root = tempfile.mkdtemp(prefix="qto_plan_")
    ok = True

    def expect(cond, label):
        nonlocal ok
        print(("[PASS] " if cond else "[FAIL] ") + label)
        ok = ok and bool(cond)

    ns = argparse.Namespace
    expect(cmd_init(ns(root=root, project="测试项目", discipline="房建", force=False)) == 0, "init 建总控")
    expect(cmd_add_batch(ns(root=root, id="B01", title="建册", scope="全部图纸",
                            inputs="图纸目录", accept="建册CONFIRMED", depends="", budget="low")) == 0, "入队 B01")
    expect(cmd_add_batch(ns(root=root, id="B02", title="户型", scope="标准层",
                            inputs="DXF", accept="门禁0/0", depends="B01", budget="medium")) == 0, "入队 B02（依赖 B01）")
    expect(cmd_next(ns(root=root)) == 0, "next 给出 B01")
    b = _next_batch(_load(root, PLAN_REL), _load(root, STATE_REL))
    expect(b and b["id"] == "B01", "依赖未解时 B02 不会被提前执行")
    expect(cmd_start(ns(root=root, id="B01")) == 0, "B01 开工")
    expect(cmd_oi(ns(root=root, id="B01", text="户数待业主确认")) == 0, "OI 挂账不阻断")
    expect(cmd_done(ns(root=root, id="B01", readings=26, gates="7/2/0", tokens=9000, note="")) == 0, "B01 完成")
    b = _next_batch(_load(root, PLAN_REL), _load(root, STATE_REL))
    expect(b and b["id"] == "B02", "B01 完成后 B02 解锁")
    expect(cmd_block(ns(root=root, id="B02", text="结构体系未闭合")) == 1, "B02 硬停点")
    expect(_next_batch(_load(root, PLAN_REL), _load(root, STATE_REL)) is None, "阻断后无可执行批次")
    expect(cmd_reopen(ns(root=root, id="B02", text="补证齐全")) == 0, "解除阻断重开")
    expect(cmd_done(ns(root=root, id="B02", readings=40, gates="7/2/0", tokens=15000, note="")) == 0, "B02 完成")
    state = _load(root, STATE_REL)
    done, blocked, readings, ois, tokens = _progress(state)
    expect((done, blocked, readings, ois, tokens) == (2, 0, 66, 1, 24000), "状态合计正确（2 批/66 行/1 OI/24000 token）")
    expect(cmd_resume(ns(root=root)) == 0, "resume 可接续")
    expect(cmd_card_init(ns(root=root, force=False)) == 0, "生成测量策略卡")
    expect(cmd_card_check(ns(root=root)) == 1, "未确认的策略卡不得放行")
    card = _load(root, CARD_REL)
    card["measurement_standard"] = "CESMM4"
    card["measurement_standard_version"] = "2012"
    card["project"] = "测试项目"
    card["discipline"] = "房建"
    card["waste_policy"] = "净量与损耗分列，损耗率按合同附件 3"
    card["drawing_baseline"] = "2026-10-07 Tender R0"
    card["classification"]["work_breakdown"] = "CBS"
    card["deliverable"]["boq_structure"] = "业主 BOQ 章节"
    card["deliverable"]["rounding"] = "两位小数"
    card["review"]["release_authority"] = "曹工"
    card["digitization_rules"]["legend_pages_excluded"] = "图例页不计安装量，只产出型号对照"
    card["digitization_rules"]["label_blocks_excluded"] = "标签块不计构件"
    card["digitization_rules"]["replica_policy"] = "同内容指纹只计 1 张"
    card["digitization_rules"]["version_conflict_rule"] = "冲突按文件版本时间裁决"
    card["system_multiplier"]["declared"] = {"塔楼": 2}
    card["system_multiplier"]["evidence"] = "效果图实证双塔"
    card["approval_status"] = "APPROVED"
    _save(root, CARD_REL, card)
    expect(cmd_card_check(ns(root=root)) == 0, "确认后的策略卡放行")
    print("SELFTEST:", "PASS" if ok else "FAIL", "（临时目录：", root, "）")
    return 0 if ok else 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="project_orchestrator",
                                 description="大型项目算量总控：批次计划/断点续算/OI 挂账/测量策略卡")
    sub = ap.add_subparsers(dest="cmd")

    def add(name, fn, **kw):
        p = sub.add_parser(name, **kw)
        p.add_argument("--root", required=True)
        p.set_defaults(func=fn)
        return p

    p = add("init", cmd_init, help="建立项目总控")
    p.add_argument("--project", required=True)
    p.add_argument("--discipline", default="")
    p.add_argument("--force", action="store_true")
    p = add("add-batch", cmd_add_batch, help="登记一个批次")
    p.add_argument("--id", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--scope", default="")
    p.add_argument("--inputs", default="")
    p.add_argument("--accept", default="")
    p.add_argument("--depends", default="")
    p.add_argument("--budget", default="medium", choices=["low", "medium", "high"])
    add("next", cmd_next, help="取下一批任务卡")
    p = add("start", cmd_start, help="批次开工")
    p.add_argument("--id", required=True)
    p = add("done", cmd_done, help="批次完成登记")
    p.add_argument("--id", required=True)
    p.add_argument("--readings", type=int, default=0)
    p.add_argument("--gates", default="")
    p.add_argument("--tokens", type=int, default=None)
    p.add_argument("--note", default="")
    p = add("oi", cmd_oi, help="挂账（不阻断）")
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True)
    p = add("block", cmd_block, help="硬停点阻断")
    p.add_argument("--id", required=True)
    p.add_argument("--text", required=True)
    p = add("reopen", cmd_reopen, help="解除阻断/返工重开")
    p.add_argument("--id", required=True)
    p.add_argument("--text", default="")
    p = add("status", cmd_status, help="查看进度")
    p.add_argument("--json", action="store_true")
    add("resume", cmd_resume, help="中断恢复")
    p = add("card-init", cmd_card_init, help="生成测量策略卡")
    p.add_argument("--force", action="store_true")
    add("card-check", cmd_card_check, help="校验测量策略卡")
    p = sub.add_parser("selftest", help="全流程自证")
    p.set_defaults(func=cmd_selftest)
    a = ap.parse_args(argv)
    if not getattr(a, "func", None):
        ap.print_help()
        return 2
    if getattr(a, "root", None) is not None:
        ok, fixed = _normalize_root(a.root)
        if not ok:
            print(fixed)
            return 2
        a.root = fixed
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
