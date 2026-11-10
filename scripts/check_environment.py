# -*- coding: utf-8 -*-
"""
环境检查：进入算量流程前先看清手上有什么。

只读检查，不修改任何配置、不安装、不登录、不输出任何凭据值。

四态：
  ready          核心能力齐备，直接开工
  partial        核心可用、部分能力不可用，说明受限功能
  needs_setup    当前任务所需的关键能力缺失，只列缺失项
  unavailable    装了但用不了（版本/权限/环境问题），给恢复步骤

用法：
  python check_environment.py                # 全量检查
  python check_environment.py --json         # 机读输出
退出码：ready/partial 返回 0，needs_setup/unavailable 返回 1
"""
import sys, os, json, importlib, argparse

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


PY_MIN = (3, 9)


def check_python():
    v = sys.version_info
    return v[:2] >= PY_MIN, '%d.%d.%d（需 ≥ %d.%d）' % (v[0], v[1], v[2], PY_MIN[0], PY_MIN[1])


def check_module(name, hint):
    # 用 importlib 而非 __import__：语义更明确，且不触发动态执行告警
    try:
        m = importlib.import_module(name)
        ver = getattr(m, '__version__', '未知')
        return True, '已安装 %s' % ver, hint
    except ImportError:
        return False, '未安装', hint


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    items = []

    ok, msg = check_python()
    items.append({'id': 'python', 'name': 'Python 运行时', 'required': True,
                  'status': 'ready' if ok else 'unavailable', 'detail': msg,
                  'fallback': '换用满足版本要求的 Python'})

    for mod, name, hint, req in [
        ('openpyxl', 'Excel 读写', 'pip install openpyxl（读算量表与成本表必需）', True),
        ('pymupdf', 'PDF 矢量提取', 'pip install pymupdf（读轮廓化图纸、数图元用）', True),
        ('PIL', '图像裁切', 'pip install pillow（高倍渲染后裁切局部用）', False),
    ]:
        ok, msg, hint2 = check_module(mod, hint)
        items.append({'id': mod, 'name': name, 'required': req,
                      'status': 'ready' if ok else ('partial' if not req else 'needs_setup'),
                      'detail': msg, 'fallback': hint2})

    # 矢量提取不可用时的降级：改走高倍渲染读图
    items.append({'id': 'read-figures', 'name': '数图元（数桩位/数支座这类问题）',
                  'required': False,
                  'status': 'ready' if any(i['id'] == 'pymupdf' and i['status'] == 'ready' for i in items) else 'partial',
                  'detail': '有 pymupdf 可直接读矢量坐标（推荐）',
                  'fallback': '无 pymupdf 时改用 200~300dpi 渲染 + 裁切目视，'
                              '但必须在交付说明里标注"数量未经矢量法复核"'})

    req_fail = [i for i in items if i['required'] and i['status'] in ('needs_setup', 'unavailable')]
    opt_miss = [i for i in items if not i['required'] and i['status'] in ('needs_setup', 'partial')]
    if req_fail:
        state = 'needs_setup'
    elif opt_miss:
        state = 'partial'
    else:
        state = 'ready'

    if a.json:
        print(json.dumps({'state': state, 'items': items}, ensure_ascii=False, indent=2))
    else:
        print('环境检查：%s' % {'ready': '就绪', 'partial': '部分就绪',
                              'needs_setup': '需配置', 'unavailable': '不可用'}[state])
        print()
        for i in items:
            tag = {'ready': '✓', 'partial': '○', 'needs_setup': '✗', 'unavailable': '✗'}[i['status']]
            req = '必需' if i['required'] else '可选'
            print('[%s] %-14s %s%s' % (tag, i['name'], i['detail'],
                                        '（%s）' % req if not i['required'] else ''))
            if i['status'] != 'ready':
                print('降级：%s' % i['fallback'])
        print()
        if state == 'ready':
            print('可以开始。')
        elif state == 'partial':
            print('核心能力齐备，%d 项可选能力缺失，按上面的降级路径走，并在交付时标注限制。' % len(opt_miss))
        else:
            print('关键能力缺失，先补齐：')
            for i in req_fail:
                print('  · %s：%s' % (i['name'], i['fallback']))

    return 0 if state in ('ready', 'partial') else 1


if __name__ == '__main__':
    sys.exit(main())