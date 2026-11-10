#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成人工审阅稿（human-review-loop 的机器载体）

**它补的洞**：门禁查自洽（公式/口径/单位/跨表），查不了读图。
2026-10-24 对比 openclaude 单遍算量，对方六处结构误读（桩径与墩柱直径对调、
承台与盖梁尺寸串位、多片箱读成单箱、墩柱双柱 1.20×3.40）**自校验一项都没拦**——
自校验验的是算术，不是读法。能抓这类错的只有一件事：
让一个能翻开原图的人，按「图号出处 + 图面位置」逐行回图复核。

**机制来源**：外部技能族 pk-boq-review 的 HTML 审阅稿（浏览器里改、留痕、
AI 读回变更表）。借机制，不借代码。改造点见 `references/human-review-loop.md` §二：
审阅稿必须带图号出处 + 图面位置，且把缺这两列的行**标红**——
没有位置的行，审阅者签的只是「我信你」。

**两条机器强制**：
1. `--sheet` 必传——sheet 身份 =（簿名, 页签名），不猜不默认（F-E16：
   两个分包簿同名 sheet 数值差 100 倍，按 sheet 名取表取错簿）。
2. 导出变更表时，「推翻」必须同时有改后值和 ≥8 字理由，否则**拒绝导出**并
   列出问题行——理由不可核查的修改，三个月后没人能复核。

**第三条机器强制（v2.1.2 新增，check 子命令）**：AI 读回 changes.json 前，
必须先跑 `check` 子命令做机检（推翻缺理由 / row 不在数据行范围 /
old_value 与当前单元格对不上 / sheet 表名与实参不符 / 空变更表）。
机器先拦，AI 后读——退回不能只靠自觉。

用法
----
    python make_review_html.py <表>.xlsx --sheet 1_成本取量总表 --out review.html
    python make_review_html.py check <changes.json> --xlsx <表>.xlsx --sheet <页签>

退出码（两种模式通用三态）：
    0 = 已生成 / 变更表合规放行；
    1 = 变更表有不合规项（逐行列出行号+原因，退回去让审阅者补）；
    2 = 无法运行（文件/页签/列映射/必需角色缺失/坏 JSON/空变更表）。
"""
import argparse
import datetime
import html as _html          # ★别名：渲染函数里 html 已是字符串变量，别撞名
import io
import json
import os
import sys

# --- 控制台编码兜底（v2.1.0）-------------------------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
# ------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    import colmap
except ImportError:
    colmap = None

ROLES = ["code", "name", "unit", "value", "basis", "draw", "pos",
         "evidence", "conf", "formula"]
REQUIRED = ["name", "unit", "value"]
SUBTOTAL_WORDS = {"合计", "小计", "总计", "共计", "合价", "总价", "汇总"}
MIN_REASON = 8


def _s(v):
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parse_explicit(a):
    """解析 --col role=N。返回 (explicit, errs)。errs 非空即 exit 2。"""
    explicit = {}
    errs = []
    for item in a.col:
        if "=" not in item:
            errs.append("--col 格式应为 role=N，收到 %r" % item)
            continue
        role, _, val = item.partition("=")
        try:
            explicit[role.strip()] = int(val)
        except ValueError:
            errs.append("--col 的列号必须是整数：%r" % item)
    return explicit, errs


def _resolve_columns(a, ws):
    """列映射落地（生成与 check 共用同一条路径）。返回 (cols, hrow, errs)。

    errs 非空即 exit 2——列映射没有落地，按位置猜列正是 F-E16 那一类错。
    """
    explicit, errs = _parse_explicit(a)
    if errs:
        return None, None, errs
    if colmap is None:
        return None, None, ["缺少 colmap.py——没有列映射就不生成审阅稿"
                            "（按位置猜列正是 F-E16 那一类错）"]
    detected, col_headers, hrow = colmap.detect_columns(
        ws, ROLES, header_row=a.header_row or None)
    for ln in colmap.render_map(detected, title="审阅稿列映射"):
        print(ln)
    aerrs, warns = colmap.audit_columns(detected, explicit, required=REQUIRED,
                                        col_headers=col_headers)
    for w in warns:
        print("[WARN] %s" % w)
    if aerrs:
        return None, None, aerrs
    cols = {role: colmap.resolve(explicit, detected, role) for role in ROLES}
    return cols, hrow, []


def _collect_review_rows(ws, cols, hrow):
    """扫描可审量行。返回 (rows, n_sub)。

    ★生成与 check 共用同一套行身份判据：check 说的「数据行范围」
    就是审阅稿实际覆盖的那批行——小计行/空名行不在内。
    """
    rows = []
    n_sub = 0
    for r in range(hrow + 1, ws.max_row + 1):
        rec = {"row": r}
        for role in ROLES:
            c = cols.get(role) or 0
            rec[role] = ws.cell(r, c).value if c else None
        name = _s(rec["name"])
        val = rec["value"]
        if not name and not _num(val):
            continue
        if name.startswith("★") or name in SUBTOTAL_WORDS \
                or "小计" in name or "合计" in name:
            n_sub += 1
            continue
        if not name:
            continue
        miss = []
        if not _s(rec["draw"]):
            miss.append("draw")
        if not _s(rec["pos"]):
            miss.append("pos")
        rows.append({
            "row": r,
            "code": _s(rec["code"]),
            "name": name,
            "unit": _s(rec["unit"]),
            "value": val if _num(val) else _s(val),
            "basis": _s(rec["basis"]),
            "draw": _s(rec["draw"]),
            "pos": _s(rec["pos"]),
            "evidence": _s(rec["evidence"]),
            "conf": _s(rec["conf"]),
            "formula": _s(rec["formula"]),
            "miss": miss,
        })
    return rows, n_sub


def run(argv=None):
    ap = argparse.ArgumentParser(
        prog="make_review_html",
        description="生成人工审阅稿（带图号出处/图面位置，人可逐行挑战读图）",
        epilog="子命令：check <changes.json> --xlsx <表.xlsx> --sheet <页签>"
               "——AI 读回 changes.json 前的机器校验"
               "（0 放行 / 1 不合规 / 2 无法运行）")
    ap.add_argument("xlsx")
    # ★sheet 必传，不给默认值：sheet 身份 =（簿名, 页签名）。
    #   两个分包簿同名 sheet 数值差 100 倍的实测教训，见 failure-modes F-E16。
    ap.add_argument("--sheet", required=True,
                    help="页签名（必传，不猜不默认；跨簿汇总前先人工确认 sheet 白名单）")
    ap.add_argument("--out", default="",
                    help="输出 HTML 路径（默认 review_<簿名>_<日期>.html "
                         "落在当前目录，会打印绝对路径）")
    ap.add_argument("--col", action="append", default=[],
                    help="覆盖列映射，格式 role=N（如 --col pos=11），可多次")
    ap.add_argument("--header-row", type=int, default=0,
                    help="表头行号（0 = 自动定位）")
    a = ap.parse_args(argv)

    if not os.path.exists(a.xlsx):
        print("[未执行] 表文件不存在：%s" % a.xlsx)
        return 2
    try:
        import openpyxl
        wb = openpyxl.load_workbook(a.xlsx, data_only=False)
    except Exception as e:
        print("[未执行] 无法打开表：%s" % e)
        return 2
    if a.sheet not in wb.sheetnames:
        print("[未执行] 没有页签「%s」（现有：%s）" % (a.sheet, wb.sheetnames))
        return 2
    ws = wb[a.sheet]

    cols, hrow, errs = _resolve_columns(a, ws)
    if errs:
        for e in errs:
            print("[未执行] %s" % e)
        print("[未执行] 退出码 2——列映射没有落地，不生成审阅稿。")
        return 2

    rows, n_sub = _collect_review_rows(ws, cols, hrow)
    if not rows:
        print("[未执行] 扫描到 0 行可审量行（名称非空）。空表不生成审阅稿——"
              "审一份没有数的表，只会产出「已审」的假象。")
        return 2

    n_miss = sum(1 for x in rows if x["miss"])
    print("-" * 74)
    print("可审量行 %d（小计行 %d 行只读不审）｜缺图号出处/图面位置 %d 行"
          % (len(rows), n_sub, n_miss))
    if n_miss:
        print("[WARN] 以下行缺图号出处或图面位置——审阅者无法回图，"
              "签的只是「我信你」：")
        for x in rows:
            if x["miss"]:
                print("       r%-4d %-30s 缺：%s"
                      % (x["row"], x["name"][:30],
                         "、".join("图号出处" if m == "draw" else "图面位置"
                                   for m in x["miss"])))
        print("       先回读图步骤补位置，再进审阅稿（human-review-loop.md §二）。")

    out = a.out or ("review_%s_%s.html"
                    % (os.path.splitext(os.path.basename(a.xlsx))[0],
                       datetime.date.today().strftime("%Y%m%d")))
    meta = {
        "tool": "make_review_html",
        "version": "3.1.0",
        "table": os.path.basename(a.xlsx),
        "sheet": a.sheet,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "reviewable": len(rows),
        "missing_pos": n_miss,
    }
    html = _render(meta, rows)
    with io.open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("[OK] 审阅稿已生成：%s（%d 字节）" % (out, len(html.encode("utf-8"))))
    # ★默认落当前目录是交付物的预期行为（P2-10 明确不改落点），
    #   但相对路径换个 cwd 就找不着——生成后把绝对路径打出来。
    print("     已写：%s" % os.path.abspath(out))
    print("     人在浏览器里逐行选 确认/推翻/待补充，导出 changes.json 后交回。")
    return 0


# --------------------------------------------------------------------------
HTML_HEAD = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>人工审阅稿 · __TABLE__ / __SHEET__</title>
<style>
  :root { color-scheme: light; }
  body { font-family: "HarmonyOS Sans SC","Microsoft YaHei","PingFang SC",sans-serif;
         margin: 0; padding: 20px; background: #f5f6f8; color: #1f2329; }
  .card { background: #fff; border: 1px solid #e3e6eb; border-radius: 8px;
          padding: 16px 20px; margin-bottom: 14px; }
  h1 { font-size: 18px; margin: 0 0 6px; }
  .meta { font-size: 12.5px; color: #646a73; line-height: 1.8; }
  .meta b { color: #1f2329; }
  .warn { background: #fff7e6; border: 1px solid #ffd591; border-radius: 6px;
          padding: 8px 12px; font-size: 12.5px; color: #ad6800; margin-top: 8px; }
  .bar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  button { font: inherit; font-size: 13px; padding: 6px 14px; border-radius: 6px;
           border: 1px solid #1677ff; background: #1677ff; color: #fff; cursor: pointer; }
  button.ghost { background: #fff; color: #1677ff; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  select, input[type=text], input[type=number] { font: inherit; font-size: 12.5px;
           padding: 3px 6px; border: 1px solid #d0d3d9; border-radius: 4px;
           background: #fff; width: 100%%; box-sizing: border-box; }
  table { border-collapse: collapse; width: 100%%; font-size: 12.5px; }
  th, td { border: 1px solid #e3e6eb; padding: 5px 7px; text-align: left;
           vertical-align: top; }
  thead th { background: #f2f3f5; position: sticky; top: 0; z-index: 2;
             white-space: nowrap; }
  tbody tr:nth-child(even) { background: #fafbfc; }
  td.miss { background: #fff1f0; }
  .badge { display: inline-block; font-size: 11px; padding: 0 6px; border-radius: 3px;
           background: #fff1f0; color: #cf1322; border: 1px solid #ffa39e; }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .v-ok { color: #389e0d; } .v-no { color: #cf1322; font-weight: 600; }
  .v-wait { color: #8c8c8c; }
  textarea { width: 100%%; height: 180px; font-family: Consolas, monospace;
             font-size: 11.5px; margin-top: 10px; }
  .foot { font-size: 12px; color: #8c8c8c; line-height: 1.7; }
</style>
</head>
<body>
<div class="card">
  <h1>人工审阅稿 · __TABLE__</h1>
  <div class="meta">
    sheet 身份：<b>（簿名 __TABLE__，页签 __SHEET__）</b>——跨簿汇总前先人工确认 sheet 白名单（F-E16）<br>
    生成时间：__GENAT__ ｜ 可审量行 <b>__NROWS__</b> ｜ 缺图号出处/图面位置 <b>__NMISS__</b> 行（已标红）<br>
    审阅对象不是「数对不对」，是「<b>这个数从哪条尺寸链读来的</b>」：按「图号出处 + 图面位置」回原图复核。
  </div>
  __MISSWARN__
</div>
<div class="card bar">
  <span>进度：<b id="prog">0 / __NROWS__</b></span>
  <label>筛选
    <select id="filter">
      <option value="all">全部</option>
      <option value="todo">只看未审</option>
      <option value="bad">只看有异议（推翻/待补充）</option>
      <option value="miss">只看缺图号或图面位置</option>
    </select>
  </label>
  <button id="export">导出变更表（changes.json）</button>
  <button id="copy" class="ghost">复制 JSON</button>
  <button id="reset" class="ghost">清空本机记录</button>
</div>
<div class="card" style="padding:0;overflow:auto">
<table>
  <thead><tr>
    <th>行</th><th>编码</th><th>名称</th><th>单位</th><th class="num">工程量</th>
    <th>口径声明</th><th>图号出处</th><th>图面位置</th><th>证据档</th><th>依据类型</th>
    <th>审阅结论</th><th>改后值</th><th>审阅备注（推翻必填，≥8 字）</th>
  </tr></thead>
  <tbody id="tb"></tbody>
</table>
</div>
<div class="card foot">
  三条纪律：① 推翻必须同时给<b>改后值</b>和<b>≥8 字可核查理由</b>，否则拒绝导出；
  ② 变更表（changes.json）是唯一回读入口，对话里说的「我改一下」不算数；
  ③ 确认的结论由 AI 落成 ack 台账（带数值指纹，行被改过会报 ACK-STALE）。<br>
  本页自包含，可邮件/可拷贝；选择与文字存在本机 localStorage，刷新不丢。
</div>
<textarea id="out" placeholder="导出的 changes.json 会显示在这里（同时自动下载）"></textarea>
<script>
var ROWS = __DATA__;
var META = __META__;
var KEY = "review:" + META.table + ":" + META.sheet;
var state = {};
try { state = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { state = {}; }
var VERDICTS = ["待审", "确认", "推翻", "待补充"];

function save() { localStorage.setItem(KEY, JSON.stringify(state)); }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c];
  });
}

function st(r) { return state[r] || {}; }

function render() {
  var f = document.getElementById("filter").value;
  var tb = document.getElementById("tb");
  tb.innerHTML = "";
  var done = 0;
  ROWS.forEach(function (r) {
    var s = st(r.row);
    var v = s.verdict || "待审";
    if (v !== "待审") { done++; }
    if (f === "todo" && v !== "待审") { return; }
    if (f === "bad" && v !== "推翻" && v !== "待补充") { return; }
    if (f === "miss" && (!r.miss || !r.miss.length)) { return; }
    var tr = document.createElement("tr");
    var opts = VERDICTS.map(function (x) {
      return '<option' + (x === v ? " selected" : "") + ">" + x + "</option>";
    }).join("");
    var missHtml = r.miss && r.miss.length
      ? ' <span class="badge">缺' + r.miss.map(function (m) {
          return m === "draw" ? "图号出处" : "图面位置";
        }).join("/") + "</span>" : "";
    var cls = function (k) { return r.miss && r.miss.indexOf(k) >= 0 ? ' class="miss"' : ""; };
    tr.innerHTML =
      "<td>" + r.row + "</td>" +
      "<td>" + esc(r.code) + "</td>" +
      "<td>" + esc(r.name) + missHtml + "</td>" +
      "<td>" + esc(r.unit) + "</td>" +
      '<td class="num">' + esc(r.value) + "</td>" +
      "<td>" + esc(r.basis) + "</td>" +
      "<td" + cls("draw") + ">" + esc(r.draw) + "</td>" +
      "<td" + cls("pos") + ">" + esc(r.pos) + "</td>" +
      "<td>" + esc(r.conf) + "</td>" +
      "<td>" + esc(r.evidence) + "</td>" +
      '<td><select data-row="' + r.row + '" data-k="verdict">' + opts + "</select></td>" +
      '<td><input type="text" data-row="' + r.row + '" data-k="new_value" value="' +
        esc(s.new_value == null ? "" : s.new_value) + '"></td>' +
      '<td><input type="text" data-row="' + r.row + '" data-k="reason" value="' +
        esc(s.reason == null ? "" : s.reason) + '"></td>';
    tb.appendChild(tr);
  });
  document.getElementById("prog").textContent = done + " / " + ROWS.length;
}

document.getElementById("tb").addEventListener("change", function (e) {
  var t = e.target, row = t.getAttribute("data-row"), k = t.getAttribute("data-k");
  if (!row || !k) { return; }
  state[row] = state[row] || {};
  state[row][k] = t.value;
  save(); render();
});
document.getElementById("filter").addEventListener("change", render);

function collect() {
  var changes = [];
  ROWS.forEach(function (r) {
    var s = st(r.row);
    var v = s.verdict || "待审";
    if (v === "待审" && !s.new_value && !s.reason) { return; }
    changes.push({
      row: r.row, code: r.code, name: r.name, unit: r.unit,
      old_value: r.value, verdict: v,
      new_value: s.new_value == null ? "" : s.new_value,
      reason: s.reason == null ? "" : s.reason
    });
  });
  return changes;
}

document.getElementById("export").addEventListener("click", function () {
  var changes = collect();
  if (!changes.length) {
    alert("没有任何行被审阅——空变更表交回去等于「已审」。");
    return;
  }
  var bad = changes.filter(function (c) {
    return c.verdict === "推翻" &&
           (!String(c.new_value).trim() || String(c.reason).trim().length < __MINREASON__);
  });
  if (bad.length) {
    alert("有 " + bad.length + "行标了「推翻」但缺改后值或理由不足 __MINREASON__ 字：\\n" +
          bad.map(function (c) { return "  r" + c.row + " " + c.name; }).join("\\n") +
          "\\n理由不可核查的修改，三个月后没人能复核——补齐后再导出。");
    return;
  }
  var payload = {
    tool: META.tool, version: META.version,
    table: META.table, sheet: META.sheet,
    generated_at: META.generated_at,
    exported_at: new Date().toISOString().slice(0, 16).replace("T", " "),
    reviewed: changes.filter(function (c) { return c.verdict !== "待审"; }).length,
    changes: changes
  };
  var txt = JSON.stringify(payload, null, 2);
  document.getElementById("out").value = txt;
  var blob = new Blob([txt], {type: "application/json"});
  var aEl = document.createElement("a");
  aEl.href = URL.createObjectURL(blob);
  aEl.download = "changes_" + META.sheet + ".json";
  aEl.click();
});

document.getElementById("copy").addEventListener("click", function () {
  var t = document.getElementById("out");
  if (!t.value) { alert("先点「导出变更表」"); return; }
  t.select(); document.execCommand("copy");
  alert("已复制到剪贴板");
});

document.getElementById("reset").addEventListener("click", function () {
  if (!confirm("清空本机保存的审阅记录？（不影响已导出的 changes.json）")) { return; }
  state = {}; save(); render();
});

render();
</script>
</body>
</html>
"""


def _render(meta, rows):
    # script 内嵌 JSON 的双保险：`</` → `<\/` 中和 `</script` breakout；
    # `<` → `\u003c` 让产物**全文件 grep 不到裸 `<`**——「不可执行」不该
    # 靠推理链成立，要机器可证。JSON 解析后两者等价，零行为变化。
    data = (json.dumps(rows, ensure_ascii=False)
            .replace("</", "<\\/").replace("<", "\\u003c"))
    meta_js = (json.dumps(meta, ensure_ascii=False)
               .replace("</", "<\\/").replace("<", "\\u003c"))
    if meta["missing_pos"]:
        warn = ('<div class="warn">⚠ %d 行缺图号出处或图面位置（表中已标红）。'
                '审阅者无法回图的行，先回读图步骤补位置再审——'
                '对着一串数字签字，签的只是「我信你」。</div>'
                % meta["missing_pos"])
    else:
        warn = ""
    html = HTML_HEAD
    # ★簿名/页签名外部可控（v2.1.2 实测 stored XSS：页签名
    #   <img src=x onerror=alert(1)> 会原样进 <title> 与正文身份行，
    #   审阅者一打开审阅稿就触发）。替换前必须转义。
    #   __GENAT__/__NROWS__/__NMISS__/__MISSWARN__ 是自生成值，
    #   __DATA__/__META__ 已走 json.dumps（且 </ 已防 </script> 早退），
    #   均不在此列。
    html = html.replace("__TABLE__", _html.escape(meta["table"], quote=True))
    html = html.replace("__SHEET__", _html.escape(meta["sheet"], quote=True))
    html = html.replace("__GENAT__", meta["generated_at"])
    html = html.replace("__NROWS__", str(meta["reviewable"]))
    html = html.replace("__NMISS__", str(meta["missing_pos"]))
    html = html.replace("__MISSWARN__", warn)
    html = html.replace("__DATA__", data)
    html = html.replace("__META__", meta_js)
    html = html.replace("__MINREASON__", str(MIN_REASON))
    return html


def _val_eq(old, cur):
    """old_value（changes.json 读回）与单元格当前值（data_only=False）比较。

    数值比数值；公式/文本按字符串比。**公式行对不上要报出来，
    不许静默放过**——防隔夜篡改：改数、改公式、插行错位都会现形。
    """
    if _num(old) and _num(cur):
        return float(old) == float(cur)
    return _s(old) == _s(cur)


def run_check(argv):
    """check 子命令：AI 读回 changes.json 前的机器校验（v2.1.2 新增）。

    **为什么必须机器先拦**：审阅稿导出的 changes.json 是唯一回读入口，
    但「推翻缺理由」「row 对不上当前表」「拿 A 表的变更表对 B 表」
    这类问题以前全靠 AI 读的时候自觉发现——没有闸门，就没有"必须退"。
    本子命令把 human-review-loop.md §四纪律 2 与防隔夜篡改变成退出码。

    四条机检（缺一不可）：
      ① verdict=推翻 ⇒ new_value 非空且 reason ≥ MIN_REASON 字；
      ② row 必须落在审阅稿覆盖的数据行范围内，且 old_value 与当前单元格
         工程量值一致（data_only=False 读，公式行对不上要报出来）；
      ③ changes.json 里的 sheet/表名必须与 --xlsx/--sheet 实参一致；
      ④ 空变更表（无 changes 或全空）拒绝——没有变更就不必读回。

    退出码：0 = 放行（AI 可读回）；1 = 有变更不合规（逐行列出行号+原因，
    退回去让审阅者补）；2 = 无法运行（文件不存在 / JSON 坏 / 页签不存在 /
    列映射冲突 / 空变更表）。
    """
    ap = argparse.ArgumentParser(
        prog="make_review_html check",
        description="AI 读回 changes.json 前的机器校验：exit 非 0 就退回去让审阅者补")
    ap.add_argument("changes", help="审阅者导出的 changes.json")
    ap.add_argument("--xlsx", required=True, help="算量工作簿（核对 old_value 用）")
    # ★与生成模式同纪律：--sheet 必传，不猜不默认（F-E16）。
    ap.add_argument("--sheet", required=True,
                    help="页签名（必传，须与生成审阅稿时一致）")
    ap.add_argument("--col", action="append", default=[],
                    help="覆盖列映射，格式 role=N（如 --col value=5），可多次")
    ap.add_argument("--header-row", type=int, default=0,
                    help="表头行号（0 = 自动定位）")
    a = ap.parse_args(argv)

    # ---- 无法运行（exit 2）：文件/JSON/结构/页签/列映射 -------------------
    if not os.path.exists(a.changes):
        print("[未执行] changes.json 不存在：%s" % a.changes)
        return 2
    try:
        with io.open(a.changes, encoding="utf-8-sig") as fh:
            payload = json.load(fh)
    except Exception as e:
        print("[未执行] changes.json 不是合法 JSON：%s" % e)
        return 2
    if not isinstance(payload, dict):
        print("[未执行] changes.json 顶层必须是对象（导出按钮产出的形状）")
        return 2
    changes = payload.get("changes")
    if not isinstance(changes, list):
        print("[未执行] changes.json 里没有 changes 数组——结构不对，无法逐条机检。")
        return 2
    for i, c in enumerate(changes, 1):
        if not isinstance(c, dict):
            print("[未执行] changes[%d] 不是对象——结构不对，无法逐条机检。" % i)
            return 2
    if not changes:
        print("[未执行] changes 为空——**没有变更就不必读回**。")
        print("          空变更表交回去等于「已审」（审阅稿导出时也会拦这一条）。")
        return 2

    if not os.path.exists(a.xlsx):
        print("[未执行] 表文件不存在：%s" % a.xlsx)
        return 2
    try:
        import openpyxl
        wb = openpyxl.load_workbook(a.xlsx, data_only=False)
    except Exception as e:
        print("[未执行] 无法打开表：%s" % e)
        return 2
    if a.sheet not in wb.sheetnames:
        print("[未执行] 没有页签「%s」（现有：%s）" % (a.sheet, wb.sheetnames))
        return 2
    ws = wb[a.sheet]

    cols, hrow, errs = _resolve_columns(a, ws)
    if errs:
        for e in errs:
            print("[未执行] %s" % e)
        print("[未执行] 退出码 2——列映射没有落地，核对不了 old_value。")
        return 2

    # 数据行范围 = 生成审阅稿时覆盖的那批行（同一套行身份判据）
    rows, _ = _collect_review_rows(ws, cols, hrow)
    by_row = {x["row"]: x for x in rows}
    vcol = cols.get("value") or 0

    problems = []   # (定位, 原因) —— exit 1 逐条列出

    # ③ sheet/表名必须与实参一致（拿 A 表的变更表对 B 表，读回去就是改错表）
    p_table = str(payload.get("table") or "").strip()
    p_sheet = str(payload.get("sheet") or "").strip()
    if p_table != os.path.basename(a.xlsx):
        problems.append(("表名", "changes.json 记的表名是 %r，与 --xlsx 实参 %r 不一致"
                         % (p_table, os.path.basename(a.xlsx))))
    if p_sheet != a.sheet:
        problems.append(("页签", "changes.json 记的页签是 %r，与 --sheet 实参 %r 不一致"
                         % (p_sheet, a.sheet)))

    for i, c in enumerate(changes, 1):
        tag = "changes[%d]" % i
        raw_row = c.get("row")
        try:
            r = int(raw_row)
        except (TypeError, ValueError):
            problems.append((tag, "row 缺失或不是整数：%r" % (raw_row,)))
            continue
        tag = "r%d" % r
        # ① 推翻必须同时有改后值和 ≥8 字理由（与导出按钮同标准）
        if c.get("verdict") == "推翻":
            nv = c.get("new_value")
            nv = "" if nv is None else str(nv).strip()
            rs = str(c.get("reason") or "").strip()
            if not nv:
                problems.append((tag, "verdict=推翻但 new_value 为空——"
                                      "没有改后值，读回方无从落表"))
            if len(rs) < MIN_REASON:
                problems.append((tag, "verdict=推翻但理由不足 %d 字"
                                      "（现 %d 字：%r）——理由不可核查，"
                                      "三个月后没人能复核"
                                      % (MIN_REASON, len(rs), rs)))
        # ② row 必须落在数据行范围内，且 old_value 与当前单元格值一致
        if r not in by_row:
            problems.append((tag, "不在审阅稿覆盖的数据行范围内"
                                  "（小计行/空名行不审；也可能是隔夜插行导致行号错位）"))
            continue
        cur = ws.cell(r, vcol).value if vcol else None
        old = c.get("old_value")
        if not _val_eq(old, cur):
            extra = ""
            if isinstance(cur, str) and cur.startswith("="):
                extra = "（当前单元格是公式：%s）" % cur
            elif isinstance(old, str) and old.startswith("="):
                extra = "（变更表里记的是公式：%s）" % old
            problems.append((tag, "old_value 与当前单元格工程量不一致："
                                  "变更表 %r vs 表现在 %r%s——"
                                  "防隔夜篡改，不许静默放过"
                                  % (old, cur, extra)))

    if problems:
        print("[FAIL] changes.json 有 %d 处不合规——退回去让审阅者补，AI 不读回："
              % len(problems))
        for tag, why in problems:
            print("       %-12s %s" % (tag, why))
        print("       退出码 1——这是「变更不合规」，不是「无法运行」。")
        return 1
    print("[OK] changes.json 机检通过：%d 条变更的行号/旧值/理由全部对得上当前表。"
          % len(changes))
    print("     可以按 human-review-loop.md §五 三种落法读回。")
    return 0


def main2(argv=None):
    """入口：check 子命令（读回前机检）或默认的生成模式。"""
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "check":
        return run_check(argv[1:])
    return run(argv)


if __name__ == "__main__":
    sys.exit(main2())
