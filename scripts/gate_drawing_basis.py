# -*- coding: utf-8 -*-
"""
图纸基准核对门禁：把表里的量和已确认的图纸读数逐条比。

为什么需要它（本项目实测）：
  工程量的准确性最终取决于"图上那个数对不对"。人工读图会出错，
  但**错法是有限的几种**，把这几种做成基准表，就能机器拦住。

  实测抓到的三类错误：
    ·维度错误：把纵向梁区总长当横向桥面宽（26.0m → 真值 15.75m）
    · 数量口径错：双幅共用桩号，只看一排（3 个承台 → 真值 6 个）
    · 规格张冠李戴：沿用另一座桥的参数（承台厚 1600 vs 1500）

用法：
  1) 先用 --init 生成基准文件模板，按项目填写
  2) 再用 基准文件 比对算量表
  python gate_drawing_basis.py <算量表.xlsx> --basis <基准.json>
         [--sheet 1_成本取量总表] [--init <输出模板>]
退出码：不符返回 1，全过返回 0

基准文件格式（JSON）：
  {
    "note": "基准来源必须写图号+图面位置，不写'按图'",
    "items": [
      {"code": "CBS-BRG-P03", "qty": 14582, "unit": "CM",
       "basis": "HA-035103.01 上下两排各3个承台，单个21.7x7.0x1.6m",
       "tolerance": 0.005}
    ]
  }
"""
import sys, os, re, json, argparse

# --- 控制台编码兜底（v2.1.0）-------------------------------------------
# 中文 Windows 控制台默认 cp936，无法编码 ✓/✗/★ 等符号：一条 INFO 输出
# 就会抛 UnicodeEncodeError，让**干净的表**被判成"检查不通过"（实测
# gate_sheet_formula.py 在 ACP=936 下必崩）。这里保留控制台编码，只把
# 不可编码字符替换掉——不换编码，避免中文变乱码。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
# ------------------------------------------------------------------------

import openpyxl


def load_basis(p):
    # utf-8-sig 兼容 PowerShell / Excel 导出的 BOM 头（这类文件很常见）
    with open(p, encoding='utf-8-sig') as f:
        return json.load(f)


def find_row(ws, code):
    for r in range(1, ws.max_row + 1):
        if str(ws.cell(r, 2).value or '') == code:
            return r
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('xlsx')
    ap.add_argument('--sheet', default='1_成本取量总表')
    ap.add_argument('--basis')
    ap.add_argument('--init', metavar='OUT')
    a = ap.parse_args()

    if a.init:
        tpl = {
            "note": "把每条基准的出处写成'图号 + 图面位置'，不要写'按图纸'。"
                    "tolerance 是允许相对误差，缺省 0.5%。",
            "items": [
                {"code": "CBS-BRG-P03", "qty": 0, "unit": "CM",
                 "basis": "HA-xxxxxx.01 尺寸链 a+b+c", "tolerance": 0.005}
            ]
        }
        with open(a.init, 'w', encoding='utf-8') as f:
            json.dump(tpl, f, ensure_ascii=False, indent=2)
        print('基准模板已生成：%s（填好 items 后用 --basis 核对）' % a.init)
        return 0

    if not a.basis:
        print('缺 --basis 或 --init。用法：--init 生成模板，或 --basis 核对。')
        return 2
    if not os.path.isfile(a.xlsx):
        print('状态：unavailable —— 找不到算量表 %s' % a.xlsx)
        return 2

    data = load_basis(a.basis)
    items = data.get('items', [])
    ws = openpyxl.load_workbook(a.xlsx, data_only=False)[a.sheet]

    # ---- 零数据行守卫（v2.1.1）----
    # 表是空的时候，下面每一条基准都会掉进 notfound，最终 exit 1「量不符」。
    # 这个 1 指向的是"表里没有这些编码"，但报告读者会读成"量算错了"——
    # 两件事完全不同，且空表根本不是"被查出了问题"。判「未运行」。
    n_data = sum(1 for r in range(1, ws.max_row + 1)
                 if ws.cell(r, 2).value
                 and isinstance(ws.cell(r, 5).value, (int, float)))
    if n_data == 0:
        print('图纸基准核对门禁 | 表：%s | 页签：%s'
              % (os.path.basename(a.xlsx), a.sheet))
        print('[未执行] 扫描到 **0 行有效量行**（编码+工程量为数值的行）。')
        print('         表里没有可比对的量，%d 条基准一条都对不上——'
              '这既不是"量错了"，也不是"量对了"。' % len(items))
        print('         先核对 --sheet，或确认这张表是否还没填。')
        print('[未执行] 退出码 2（门禁无法运行）——这不是"检查不通过"，也不是"通过"。')
        return 2

    off, notfound, no_basis, placeholder = [], [], [], []
    checked = 0
    for it in items:
        code, exp = it.get('code'), it.get('qty')
        if not it.get('basis') or it.get('basis', '').strip() in ('', '按图', '按图纸'):
            no_basis.append(code)
        # ★模板占位（qty=0 且 basis 里还是模板串）不算一条真基准——
        #   否则会出现"基准文件里只有模板却显示全部通过"的假绿。
        if not isinstance(exp, (int, float)) or exp == 0:
            placeholder.append(code)
            continue
        r = find_row(ws, code)
        if r is None:
            notfound.append(code)
            continue
        got = ws.cell(r, 5).value
        if not isinstance(got, (int, float)):
            off.append('%s 期望 %s 实为 %s（非数值）' % (code, exp, got))
            continue
        checked += 1
        tol = it.get('tolerance', 0.005)
        if abs(got - exp) / abs(exp) > tol:
            off.append('%s 实为 %s ≠ 基准 %s（超 %.1f%%）｜依据：%s'
                       % (code, got, exp, tol * 100, it.get('basis', '未写依据')))

    print('图纸基准核对门禁 | 表：%s | 基准 %d 条（有效 %d 条）'
          % (os.path.basename(a.xlsx), len(items), checked))
    if placeholder:
        print('[FAIL] G-B0 基准仍是模板占位（qty 为 0 或缺失）%d 条：%s'
              % (len(placeholder), '；'.join(map(str, placeholder[:5]))))
        print('       模板占位不算基准——先把基准文件填成真实读数再核对')
    else:
        print('[PASS] G-B0 无模板占位，基准均为真实读数')
    if no_basis:
        print('[FAIL] G-C 基准未写出处 %d 条：%s' % (len(no_basis), '；'.join(map(str, no_basis[:5]))))
    else:
        print('[PASS] G-C 全部基准均写明图号出处')
    if notfound:
        print('[FAIL] G-D 算量表中找不到这些编码 %d 条：%s' % (len(notfound), '；'.join(map(str, notfound[:5]))))
    for o in off:
        print('[FAIL] G-E 量与基准不符：%s' % o)
    if not off:
        print('[PASS] G-E 全部量与基准相符')

    if off or notfound or no_basis or placeholder or checked == 0:
        print('!! 未通过：量与图纸基准不符，先查是否读错维度 / 口径 / 张冠李戴')
        return 1
    print('== 图纸基准核对门禁通过 ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())