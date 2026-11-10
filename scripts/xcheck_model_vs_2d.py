# -*- coding: utf-8 -*-
"""
三维模型 × 二维算量 交叉复核器。

这套机制解决的是最难查的一类错误：**两边看起来一致，实际都错。**

三条铁律（缺一条，这套检查就没有意义）：
  1. **两侧必须不同源**。若模型和二维表同抄一个错误参数（例如都按 0.65 m³/m 算梁），
     比对必然显示"完全一致"，而两边全错。这是**共模错误**，只有回原图重建参数才抓得到。
     所以本脚本会先检查同源，再比数值。
  2. **可比范围要先声明**。不是所有量都能比：钢筋/模板/支座这类 LOT 在模型里
     通常没有独立几何，比不出来。硬比会得到一堆假 MAJOR，比不比对更糟。
     用 --skip-lot 显式排除，并在报告里写明覆盖边界。
  3. **第一次看到差异，先怀疑导出，再怀疑被检对象**。实测首轮出现的 3 个"差异"
     全部是导出问题（标记漏写、口径误剔、构件漏登记），没有一个是真的算错。
     --stage export 可以先只核对"两边条目集合是否一致"，把导出问题挡在数值比对之前。

用法：
  # 第一步：核对两边条目集合（先抓导出漏项）
  python xcheck_model_vs_2d.py <模型侧.json> <二维侧.json> --stage export
  # 第二步：数值比对
  python xcheck_model_vs_2d.py <模型侧.json> <二维侧.json> --skip-lot 钢筋 模板 支座
  # 生成可追溯报告
  python xcheck_model_vs_2d.py <模型侧.json> <二维侧.json> --report 交叉复核报告.md

两侧 JSON 统一格式：
  [{"name": "箱涵混凝土", "value": 10.12773, "unit": "m3",
    "kind": "quantity", "param_source": "HA-035061-001 图面直读"}, ...]
  kind 取 quantity（可量）/ lot（不可量的定额项，需 --skip-lot 排除）
  param_source 必填：写清这一项的参数是"图面直读"还是"沿用上一版"——
                    共模错误的排查入口就在这里。

退出码：有 MAJOR / 同源 / 条目集不一致 → 1
"""
import sys, os, json, argparse, math
from decimal import Decimal
from collections import Counter
try:
    from qto_core import atomic_write_text, convert as qto_convert, load_unit_alias_registry, DataValidationError
except ImportError:  # supports import from sibling tooling as well as direct execution
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from qto_core import atomic_write_text, convert as qto_convert, load_unit_alias_registry, DataValidationError

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


EXACT_TOL = 0.005     # ≤0.5% 视为一致
MINOR_TOL = 0.05      # ≤5% 视为差异需说明；>5% 为 MAJOR


def load(p):
    # utf-8-sig 兼容 PowerShell / Excel 导出的 BOM 头
    with open(p, encoding='utf-8-sig') as f:
        data = json.load(f)
    if isinstance(data, dict) and isinstance(data.get('items'), list):
        data = data['items']
    if not isinstance(data, list):
        raise ValueError('%s: JSON 顶层必须是数组或含 items 数组的对象' % p)
    bad = [i for i, row in enumerate(data, 1) if not isinstance(row, dict)]
    if bad:
        raise ValueError('%s: 第 %s 项不是对象' % (p, ', '.join(map(str, bad[:10]))))
    names = [norm(row) for row in data if norm(row)]
    dupes = sorted(n for n, count in Counter(names).items() if count > 1)
    if dupes:
        raise ValueError('%s: name 重复，禁止字典覆盖造成静默丢项：%s' % (p, '、'.join(dupes[:10])))
    return data


def norm(d):
    return str(d.get('name', '')).strip()


def check_param_source(data, label, out):
    """参数来源缺失 → 共模错误排查不了，必须报。"""
    missing = [norm(d) for d in data if not d.get('param_source')]
    if missing:
        out.append('[FAIL] G-M2 %s 有 %d 项未写 param_source：%s'
                   % (label, len(missing), '；'.join(missing[:5])))
        out.append('没有参数来源就无法判断两边是否同源，共模错误会永远藏得住')
        return False
    srcs = set(str(d.get('param_source')) for d in data)
    out.append('[PASS] G-M2 %s 全部标注参数来源' % label)
    if len(srcs) == 1:
        out.append('[WARN] %s 全部 %d 项的参数来源只有一种："%s"'
                   % (label, len(data), list(srcs)[0]))
        out.append('       若另一侧也用同一个来源，则是共模错误，比对必然假通过')
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('model_json')
    ap.add_argument('t2d_json')
    ap.add_argument('--stage', choices=['export', 'value', 'all'], default='all')
    ap.add_argument('--skip-lot', nargs='*', default=[],
                    help='不可比的 LOT 关键字，如钢筋 模板 支座')
    ap.add_argument('--skip-mode', choices=['contains', 'exact'], default='contains',
                    help='--skip-lot 的匹配方式。contains=子串（默认，向后兼容）；'
                         'exact=键名完全相等。两种方式下，零命中的关键字都判 FAIL（G-SKIP）')
    ap.add_argument('--report', help='把结果写成 md 报告')
    ap.add_argument('--unit-registry', help='经批准的项目单位别名注册表 JSON；仅用于显式项目约定单位')
    ap.add_argument('--project-id', help='项目编号；使用 --unit-registry 时必填')
    a = ap.parse_args()

    for p in (a.model_json, a.t2d_json):
        if not os.path.isfile(p):
            print('状态：unavailable —— 找不到 %s' % p)
            print('提示：模型侧 json 应由建模脚本在生成几何时登记导出，不能手工编')
            return 2

    unit_registry = None
    if a.unit_registry and not a.project_id:
        print('[未执行] 使用 --unit-registry 时必须同时提供 --project-id。', file=sys.stderr)
        return 2
    if a.unit_registry:
        try:
            unit_registry = load_unit_alias_registry(a.unit_registry, project_id=a.project_id)
        except Exception as exc:
            print('[未执行] 项目单位注册表无效：%s' % exc, file=sys.stderr)
            return 2

    try:
        M = load(a.model_json)
        T = load(a.t2d_json)
    except Exception as exc:
        print('[未执行] 输入 JSON 无法验证：%s' % exc, file=sys.stderr)
        return 2
    lines = []
    print('三维模型 × 二维算量 交叉复核')
    print('  模型侧：%s（%d 项）' % (os.path.basename(a.model_json), len(M)))
    print('  二维侧：%s（%d 项）' % (os.path.basename(a.t2d_json), len(T)))
    print('  判定阈值：EXACT ≤%.1f%% ｜ MINOR ≤%.1f%% ｜ MAJOR >%.1f%%'
          % (EXACT_TOL * 100, MINOR_TOL * 100, MINOR_TOL * 100))
    print('  排除 LOT：%s' % ('、'.join(a.skip_lot) if a.skip_lot else '无'))
    print()

    fail = False

    # ── 同源检查（G-X0）──
    dm = {norm(d): d for d in M}
    dt = {norm(d): d for d in T}
    same_val = []
    for key in sorted(set(dm) & set(dt)):
        mv, tv = dm[key].get('value'), dt[key].get('value')
        if (not isinstance(mv, (int, float, Decimal)) or isinstance(mv, bool)
                or not isinstance(tv, (int, float, Decimal)) or isinstance(tv, bool)
                or not math.isfinite(float(mv)) or not math.isfinite(float(tv))):
            continue
        unit_model, unit_2d = dm[key].get('unit'), dt[key].get('unit')
        try:
            model_norm = qto_convert(mv, unit_model, unit_model,
                                     unit_registry=unit_registry, project_id=a.project_id)
            two_d_norm = qto_convert(tv, unit_2d, unit_model,
                                     unit_registry=unit_registry, project_id=a.project_id)
        except (DataValidationError, ValueError, ArithmeticError):
            # Incomparable/ambiguous units are handled as NOT_COMPARABLE in the
            # numeric stage, not treated as evidence of independent quantities.
            continue
        if model_norm == two_d_norm:
            same_val.append(key)
    print('[结果] 同名可比项 %d 项' % len(set(dm) & set(dt)))
    if same_val and len(same_val) == len(set(dm) & set(dt)) and len(same_val) >= 3:
        print('[FAIL] G-X0 全部同名项换算至统一单位后数值一致 —— 疑似同源/共模风险')
        print('处置：核查参数来源证据；必要时让一侧回图纸独立重建参数后再比')
        fail = True
    else:
        print('[INFO] G-X0 未发现“所有同名可比项均完全一致”的模式；数值差异本身不能证明两侧独立')

    lines.append('# 三维模型 × 二维算量 交叉复核报告')
    lines.append('')

    if a.stage in ('export', 'all'):
        # ── 条目集核对（先抓导出漏项）──
        print('\n── 第一步：条目集合核对（抓导出漏项）──')
        only_m = sorted(set(dm) - set(dt))
        only_t = sorted(set(dt) - set(dm))
        if only_m:
            print('[FAIL] G-E1 模型侧独有 %d 项：%s' % (len(only_m), '；'.join(only_m[:6])))
            print('常见成因：模型登记了但二维表没有 / 或反之')
            fail = True
        else:
            print('[PASS] G-E1 模型侧条目全部在二维侧有对应')
        if only_t:
            print('[FAIL] G-E2 二维侧独有 %d 项：%s' % (len(only_t), '；'.join(only_t[:6])))
            print('常见成因：二维算了但模型未建模，或导出时标记漏写')
            fail = True
        else:
            print('[PASS] G-E2 二维侧条目全部在模型侧有对应')
        unit_mismatch = [k for k in set(dm) & set(dt)
                         if dm[k].get('unit') and dt[k].get('unit')
                         and dm[k]['unit'] != dt[k]['unit']]
        if unit_mismatch:
            print('[WARN] 单位标记不同 %d 项；数值阶段会按注册表转换或标记 NOT_COMPARABLE：%s' % (len(unit_mismatch), '；'.join(unit_mismatch[:5])))
        lines.append('## 条目集合核对')
        lines.append('- 模型侧独有：%s' % ('、'.join(only_m) if only_m else '无'))
        lines.append('- 二维侧独有：%s' % ('、'.join(only_t) if only_t else '无'))
        lines.append('')

    if not check_param_source(M, '模型侧', lines):
        fail = True
    if not check_param_source(T, '二维侧', lines):
        fail = True

    if a.stage in ('value', 'all'):
        # ── 数值比对 ──
        print('\n── 第二步：数值比对 ──')
        skip = a.skip_lot
        # ★ skip 关键字必须可核对：记下每个关键字实际排掉了哪些行。
        #   旧版只做 `s in k` 子串匹配、且不回显映射，于是两类错误都能静默通过：
        #   ① 关键字拼错 → 一行都没排掉，等于没写，台账却照样记"已排除 N 项"；
        #   ② 关键字过宽（例如写"混凝土"）→ 整类量行被排除，比对结果变成"全 EXACT"。
        #   两者都表现为**该失败却通过**。现在：零命中判 FAIL，映射全量回显。
        skip_hit = {s: [] for s in skip}

        def _skip_reason(k):
            for s in skip:
                if (k == s) if a.skip_mode == 'exact' else (s in k):
                    skip_hit[s].append(k)
                    return s
            return None

        exact = minor = major = 0
        majors, minors, skipped, not_comparable = [], [], [], []
        for k in sorted(set(dm) & set(dt)):
            dv, tv = dm[k], dt[k]
            # 先求 skip 命中（必须无条件求值）：若写成 `kind=='lot' or _skip_reason(k)`，
            # 短路会让 kind 已是 lot 的行根本不进 skip_hit，映射表全变 0 命中——
            # 这正是本闸门要抓的"静默跳过"，不能自己先犯。
            reason = _skip_reason(k)
            if dv.get('kind') == 'lot' or reason:
                skipped.append(k); continue
            va, vb = dv.get('value'), tv.get('value')
            if (not isinstance(va, (int, float, Decimal)) or isinstance(va, bool)
                    or not isinstance(vb, (int, float, Decimal)) or isinstance(vb, bool)
                    or not math.isfinite(float(va)) or not math.isfinite(float(vb))):
                message = '[FAIL] G-NUM %s：模型/二维 value 必须是有限数值；model=%r 2d=%r' % (k, va, vb)
                print(message)
                fail = True
                continue

            unit_model = str(dv.get('unit') or '').strip()
            unit_2d = str(tv.get('unit') or '').strip()
            if not unit_model or not unit_2d:
                reason = '缺少模型侧或二维侧单位（model=%r, 2d=%r）' % (unit_model, unit_2d)
                not_comparable.append('%s：%s' % (k, reason))
                print('[NOT_COMPARABLE] G-UNIT %s：%s' % (k, reason))
                fail = True
                continue
            try:
                # Validate both units and convert 2D value into the model-side unit.
                # This also rejects ambiguous aliases unless project-approved registry applies.
                va_base = qto_convert(va, unit_model, unit_model,
                                      unit_registry=unit_registry, project_id=a.project_id)
                vb_in_model_unit = qto_convert(vb, unit_2d, unit_model,
                                               unit_registry=unit_registry, project_id=a.project_id)
            except (DataValidationError, ValueError, ArithmeticError) as exc:
                reason = str(exc)
                not_comparable.append('%s：%s' % (k, reason))
                print('[NOT_COMPARABLE] G-UNIT %s：model unit=%r, 2D unit=%r；%s'
                      % (k, unit_model, unit_2d, reason))
                fail = True
                continue

            if va_base == 0 and vb_in_model_unit == 0:
                exact += 1; continue
            diff = abs(va_base - vb_in_model_unit) / max(abs(va_base), abs(vb_in_model_unit))
            row = '%s：模型 %s %s vs 二维 %s %s（转换至模型单位后差 %.2f%%）｜模型侧参数来源：%s'
            if diff <= Decimal(str(EXACT_TOL)):
                exact += 1
            elif diff <= Decimal(str(MINOR_TOL)):
                minor += 1
                minors.append(row % (k, va, unit_model, vb, unit_2d, float(diff * 100),
                                     dv.get('param_source', '未写')))
            else:
                major += 1
                majors.append(row % (k, va, unit_model, vb, unit_2d, float(diff * 100),
                                     dv.get('param_source', '未写')))
        dead = [s for s, hit in skip_hit.items() if not hit]
        if dead:
            fail = True
            print('\n[FAIL] G-SKIP 以下 --skip-lot 关键字没有命中任何一行：%s'
                  % '、'.join(dead))
            print('       要么关键字拼错/写宽了，要么该项两侧键名不一致。'
                  '静默跳过等于没验，故判 FAIL。')
        if skip_hit:
            print('\n--skip-lot 映射（关键字 → 实际排除的行）：')
            for s, hit in skip_hit.items():
                print('  %-14s → %s' % (s, ('%d 行：%s' % (len(hit), '、'.join(hit[:6])))
                                       if hit else '0 行 ★未命中'))

        print('EXACT %d ｜ MINOR %d ｜ MAJOR %d ｜ NOT_COMPARABLE %d ｜ 已排除 LOT %d'
              % (exact, minor, major, len(not_comparable), len(skipped)))
        for s in minors:
            print('  [MINOR] %s' % s)
        for s in majors:
            print('  [MAJOR] %s' % s)
        for s in not_comparable:
            print('  [NOT_COMPARABLE] %s' % s)
        if not_comparable:
            fail = True
        if major:
            fail = True
            print('\n!! MAJOR 必须处置，不许用"大致相当"放过')
            print('排查顺序：①导出（条目/单位/标记）②口径（单幅还是全桥）'
                  '③参数来源（是否沿用上一版而非回图重读）')
        if skipped:
            print('\n已排除的不可比项（%d）：%s' % (len(skipped), '；'.join(skipped[:8])))
            print('★这些项模型无独立几何，比不出来。**必须把排除范围写进交付说明**，'
                  '不能让读者以为全专业都验过。')

        lines.append('## 数值比对')
        lines.append('- EXACT %d ｜ MINOR %d ｜ MAJOR %d ｜ NOT_COMPARABLE %d ｜ 排除 LOT %d'
                     % (exact, minor, major, len(not_comparable), len(skipped)))
        for s in minors:
            lines.append('- [MINOR] %s' % s)
        for s in majors:
            lines.append('- [MAJOR] %s' % s)
        for s in not_comparable:
            lines.append('- [NOT_COMPARABLE] %s' % s)
        if skipped:
            lines.append('')
            lines.append('### 未纳入比对的项（覆盖边界）')
            lines.append('')
            for k in skipped:
                lines.append('- %s' % k)
            lines.append('')
            lines.append('以上项模型无独立几何，未参与交叉复核。'
                         '它们只经过单一来源核算，不具备双源印证。')
        if skip_hit:
            lines.append('')
            lines.append('### --skip-lot 关键字命中映射')
            lines.append('')
            lines.append('| 关键字 | 命中行数 | 实际排除的行 |')
            lines.append('|---|---:|---|')
            for s, hit in skip_hit.items():
                lines.append('| %s | %d | %s |' % (s, len(hit), '、'.join(hit[:8]) or '**0 ★未命中**'))
            lines.append('')
            lines.append('零命中的关键字判 FAIL（G-SKIP）：拼错或写宽了，静默跳过等于没验。')

    lines.insert(2, '> 判定：%s' % ('不通过' if fail else '通过（MAJOR 0）'))
    lines.insert(3, '> 单位必须经注册表解析并转换到同一单位后比较；未知、歧义或不同量纲单位为 NOT_COMPARABLE 并阻断。')
    lines.insert(4, '> 两侧必须不同源；若全部同名项数值完全一致，判定为假通过。')
    lines.insert(5, '')

    if a.report:
        report_text = '\n'.join(lines) + '\n'
        if atomic_write_text is not None:
            atomic_write_text(a.report, report_text)
        else:
            with open(a.report, 'w', encoding='utf-8') as f:
                f.write(report_text)
        print('\n报告已写：%s' % a.report)

    print()
    print('== 未通过，需处置 ==' if fail else '== 交叉复核通过 ==')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())