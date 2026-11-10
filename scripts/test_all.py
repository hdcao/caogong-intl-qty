#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release smoke/regression suite. Run from any directory: python scripts/test_all.py."""
from __future__ import annotations
import ast, importlib.util, os, re, signal, subprocess, sys, time, tempfile, argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from qto_core import release_source_fingerprint

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SMOKE_SUITE_ID = "QTO-RELEASE-SMOKE-V12"
SMOKE_EXPECTED_CHECKS = 34
# static_guards() 返回的护栏条数。L-6 需要它来推算本次运行的最终 total（它身处护栏序列中，
# 不能用 len(checks) 数自己）。加护栏时必须同步递增——不同步会让 L-6 报红，这是刻意的失败方向。
STATIC_GUARD_COUNT = 7
# run_e2e.py 消费该常量（smoke_evidence_valid() 用）。v4.6.4 起它是**单一真源**：
# run_e2e 从本文件 import，不再自己硬编码——但 L-4/L-6 仍盯着，因为"下一个人可能改回去"。
E2E_RUNNER = ROOT / "examples/e2e_demo/run_e2e.py"
# v4.6.2/v4.6.3 的三个整改自证脚本：必须进默认闸（L-5 守着），不能只靠 --extended 的
# CLI --help 冒烟偶然跑通——那三个脚本没有 argparse，`--help` 会直接跑全部用例，是侥幸不是设计。
SELF_TEST_SCRIPTS = {
    "source semantics gate self-test": "test_source_semantics.py",
    "input digest gate self-test": "test_input_digest.py",
    "execution receipt gate self-test": "test_execution_receipt.py",
}
# L-7 豁免清单：跨文件同名但**语义上本就按模块不同**的常量。
# 只放名字字符串，逐条注明理由；不做"忽略全部"式的宽豁免。
CONST_CONSISTENCY_EXEMPT = {
    # 各脚本自带工具标识与版本号，本就应各自独立
    "TOOL": "各脚本的工具名，按定义不同",
    "TOOL_VERSION": "各脚本自身版本，随各自演进",
    "VERSION": "各脚本自身版本，随各自演进",
}


def run(label, argv, timeout=180):
    """Run one check with bounded process cleanup and no inherited stdout pipe.

    Logging directly to a temporary file avoids a timeout deadlock where a child or
    detached descendant keeps PIPE open after the main process has been killed.
    """
    print(f"\n=== {label} ===", flush=True)
    started = time.monotonic()
    try:
        with tempfile.TemporaryFile(mode="w+b") as log_file:
            try:
                proc = subprocess.Popen(
                    argv, cwd=str(ROOT), stdout=log_file, stderr=subprocess.STDOUT,
                    start_new_session=(os.name == "posix"),
                )
            except OSError as exc:
                elapsed = time.monotonic() - started
                print(f"[FAIL] could not start command: {type(exc).__name__}: {exc} ({elapsed:.1f}s)")
                return False
            try:
                rc = proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "posix":
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    else:
                        proc.kill()
                rc = 124
            log_file.flush()
            log_file.seek(0)
            output = log_file.read().decode("utf-8", errors="replace")
    except Exception as exc:
        elapsed = time.monotonic() - started
        print(f"[FAIL] harness error: {type(exc).__name__}: {exc} ({elapsed:.1f}s)")
        return False
    elapsed = time.monotonic() - started
    if timed_out:
        print(output[-4000:])
        print(f"[FAIL] timed out after {timeout}s; process group terminated; remaining checks will continue ({elapsed:.1f}s)")
        return False
    if rc == 0:
        tail = output.strip().splitlines()[-4:]
        print("\n".join(tail))
        print(f"[PASS] exit={rc} ({elapsed:.1f}s)")
    else:
        print(output[-12000:])
        print(f"[FAIL] exit={rc} ({elapsed:.1f}s)")
    return rc == 0


def static_guards(active_regression, prior_check_count):
    """静态一致性护栏：`写进技能包 ≠ 会被加载 ≠ 会被执行`。

    v4.6.3 的审核发现本技能在同一处重犯该病：48 个脚本里 9 个从未在 SKILL.md 出现，
    其中包括三个整改自证脚本与发布冒烟唯一入口 test_all.py 自己；文档声明 H01~H43 而实体
    是 H01~H48；SMOKE_EXPECTED_CHECKS 与实际检查数脱钩。这些**可加载性/一致性**问题此前
    零机械防线——只能靠人读 500 行 SKILL.md 与 48 个文件去比对。
    本函数把它们变成 7 条断言，进默认闸（不只在 --extended）。
    返回 [(label, ok, detail), ...]，每条即一项 checks 结果。

    prior_check_count: 调用前已累计的检查数（供 L-6 推算本次真实 total，避免自己数自己）。
    """
    skill_path = ROOT / "SKILL.md"
    skill = skill_path.read_text(encoding="utf-8")
    results = []

    # L-1 引用完整性：references/*.md 每个 basename 都必须能在 SKILL.md 里被检索到
    #（按 basename 检索，兼容 `a.md` / `b.md` 这种同列表简写）。
    refs = sorted(p.name for p in (ROOT / "references").glob("*.md"))
    missing_refs = [name for name in refs if name not in skill]
    results.append((
        "L-1 reference discoverability (every references/*.md named in SKILL.md)",
        not missing_refs,
        f"{len(refs) - len(missing_refs)}/{len(refs)} references referenced; missing={missing_refs}",
    ))

    # L-2 门禁可发现性：scripts/gate_*.py 每个文件名都必须出现在 SKILL.md，
    # 否则门禁存在但 agent 永远不会被引导去跑它。
    # ★ 同一类缺陷不止于 gate_*.py：v4.6.3 里**发布冒烟唯一入口 test_all.py 自己在
    #   SKILL.md 500 行中零出现**，三个整改自证脚本同样零出现——这就是"写进技能包 ≠
    #   会被加载"在整改自证上的原样复发。故此处把"发布闸自身及其自证"一并纳入检查：
    #   如果入口不可发现，前面所有检查做得再好，也没人知道该跑哪一条命令。
    gates = sorted(p.name for p in SCRIPTS.glob("gate_*.py"))
    release_harness = ["test_all.py", *SELF_TEST_SCRIPTS.values()]
    must_be_named = gates + [name for name in release_harness if name not in gates]
    missing_gates = [name for name in must_be_named if name not in skill]
    results.append((
        "L-2 gate + release-harness discoverability (every gate_*.py and test_all.py/self-tests named in SKILL.md)",
        not missing_gates,
        f"{len(must_be_named) - len(missing_gates)}/{len(must_be_named)} gate/release scripts referenced "
        f"({len(gates)} gate_*.py + {len(release_harness)} release harness); missing={missing_gates}",
    ))

    # L-3 硬规矩编号一致：references 里 H\d{2} 的最大编号 N，必须等于 SKILL.md 中
    # 每一处 `H01~HNN` 声明的上界。v4.6.3 里 SKILL.md 同一文件内 L450 写 H01~H43、
    # L490 写 H01~H48，自相矛盾——纯文本无法自查，必须机械对账。
    rules_path = ROOT / "references/hard-rules-and-stop-conditions.md"
    rule_numbers = sorted({int(m.group(1)) for m in
                           re.finditer(r"H(\d{2})", rules_path.read_text(encoding="utf-8"))})
    max_rule = rule_numbers[-1] if rule_numbers else 0
    declared = [(lineno, int(m.group(1)))
                for lineno, line in enumerate(skill.splitlines(), 1)
                for m in re.finditer(r"H01\s*[~～\-]\s*H(\d{2})", line)]
    bad_decl = [(lineno, upper) for lineno, upper in declared if upper != max_rule]
    if not declared:
        detail = (f"max rule in references = H{max_rule:02d}, but SKILL.md declares no "
                  f"`H01~HNN` range at all — the hard-rule range is undiscoverable")
    elif bad_decl:
        detail = (f"max rule in references = H{max_rule:02d}; SKILL.md declares mismatched "
                  f"upper bound(s): " + ", ".join(f"L{lineno} says H{up:02d}" for lineno, up in bad_decl))
    else:
        detail = (f"max rule in references = H{max_rule:02d}; all {len(declared)} SKILL.md "
                  f"range declaration(s) match (lines {[ln for ln, _ in declared]})")
    results.append((
        "L-3 hard-rule range consistency (SKILL.md H01~HNN == max rule in references)",
        bool(declared) and not bad_decl,
        detail,
    ))

    # L-4 冒烟常量单一真源：run_e2e.py **不得再自己定义** SMOKE_EXPECTED_CHECKS，
    # 必须从本文件导入。真实 total 与本常量的对账由 main() 收口处的 exit 1 保留
    # （total != 常量 → rc=1），这里守的是**结构**而非数值：
    #   v4.6.3 的事故结构是「同一事实硬编码在两个模块、只校验其中一处」。把值改对不是修复；
    #   让"第二份拷贝"根本不存在才是修复。数值等价性由 L-6 与 main() 收口双重把关。
    # ★ 本护栏只防"悄悄多出一份硬编码"；绝对值由 main() 的 total 对账兜底，
    #   请勿因看到 L-4 绿就删掉那段对账。
    try:
        runner_text = E2E_RUNNER.read_text(encoding="utf-8")
        local_redefine = re.search(
            r"^SMOKE_EXPECTED_CHECKS\s*=", runner_text, re.MULTILINE) is not None
        imports_from_test_all = re.search(
            r"^\s*from\s+test_all\s+import\s+[^\n]*\bSMOKE_EXPECTED_CHECKS\b",
            runner_text, re.MULTILINE) is not None
        read_ok, read_err = True, ""
    except OSError as exc:
        local_redefine = imports_from_test_all = False
        read_ok, read_err = False, f"{type(exc).__name__}: {exc}"
    single_source = (not local_redefine) and imports_from_test_all
    results.append((
        "L-4 smoke constant single-source (run_e2e imports it, does not redefine it)",
        read_ok and single_source,
        (f"{E2E_RUNNER.name}: 本地重新定义={local_redefine}, 从 test_all 导入={imports_from_test_all}"
         if read_ok else f"无法读取 {E2E_RUNNER.name}: {read_err}")
        + ("" if single_source else " —— 两处硬编码必然漂移（v4.6.3 事故结构）"),
    ))

    # L-5 新自测必须在默认闸内：三个整改自证必须真的出现在「过滤后的」regression 列表里。
    # 只写进 regression 但不在 critical_regressions 集合里，默认模式会被过滤掉——等于没跑。
    active_labels = {label for label, _ in active_regression}
    active_argv = " ".join(" ".join(map(str, argv)) for _, argv in active_regression)
    missing_selftests = [label for label, script in SELF_TEST_SCRIPTS.items()
                         if label not in active_labels and script not in active_argv]
    results.append((
        "L-5 remediation self-tests are inside the default gate (not --extended only)",
        not missing_selftests,
        f"{len(SELF_TEST_SCRIPTS) - len(missing_selftests)}/{len(SELF_TEST_SCRIPTS)} self-tests "
        f"active in this run's regression gate; missing={missing_selftests}",
    ))

    # ── L-6 跨模块真实对账（run_e2e 实际生效的值 vs 本次真实 total）────────────────────
    # 为什么需要它：v4.6.3 的守卫 `total != SMOKE_EXPECTED_CHECKS` 比的**两个变量都来自
    # test_all.py 自己**；run_e2e 的同名常量从未被 import、从未参与比较。审核员实测：只改
    # run_e2e 一侧 → 证据链断，而 test_all 自检全绿退出。事故结构是「同一事实硬编码在两个
    # 模块、只校验其中一处」，不是「值写错了」。v4.6.4 已把 run_e2e 改为从本文件导入（单一
    #  真源），此处再把"它实际生效的值"与"本次真实检查数"对账，两条一起才能封住双侧。
    #
    # ★ 主判据是下面的**数值比对**（比较两个模块里的同一事实），不是后面那条
    #   smoke_evidence_valid() 断言——后者喂的是本函数自己拼出来的日志，本质仍是"构造+校验"，
    #   只能证明函数自身行为稳定，**不能**证明常量没漂移。后人请勿把那条当成有效防线。
    n_guards_before_l6 = len(results)
    # L-6 要算出「本次运行的最终 total」，但它身处护栏序列之中，不能用 len(checks)。
    # 用显式声明的护栏总数推算；若有人加了一条护栏却忘记递增 STATIC_GUARD_COUNT，
    # L-6 会因 total 对不上而报红——这正是我们要的失败方向（而不是悄悄放过）。
    expected_total = prior_check_count + STATIC_GUARD_COUNT
    try:
        runner_spec = importlib.util.spec_from_file_location("_w4_e2e_runner_probe", E2E_RUNNER)
        if runner_spec is None or runner_spec.loader is None:
            raise ImportError(f"cannot build import spec for {E2E_RUNNER}")
        runner_mod = importlib.util.module_from_spec(runner_spec)
        runner_spec.loader.exec_module(runner_mod)
        effective = runner_mod.SMOKE_EXPECTED_CHECKS
        effective_suite = runner_mod.SMOKE_SUITE_ID
    except Exception as exc:  # 包结构损坏本身就是缺陷，不能当"跳过"
        results.append((
            "L-6 cross-module smoke constant truth (run_e2e effective value == real total)",
            False,
            f"无法导入 {E2E_RUNNER.name} 读取其生效常量：{type(exc).__name__}: {exc}",
        ))
        effective = effective_suite = None
    if effective is not None:
        numeric_ok = (effective == expected_total == SMOKE_EXPECTED_CHECKS)
        suite_ok = (effective_suite == SMOKE_SUITE_ID)
        # 次要断言：真实运行的日志形态必须能被 run_e2e 接受（构造日志，仅验函数契约）。
        real_digest = release_source_fingerprint(ROOT)
        log_text = (f"SOURCE TREE SHA256: {real_digest}\n"
                    "SOURCE TREE UNCHANGED: True\n"
                    f"RELEASE SMOKE RESULT: {expected_total}/{expected_total} passed "
                    f"(suite={SMOKE_SUITE_ID})\n"
                    f"SOURCE TREE SHA256: {real_digest}\n"
                    "PASS: smoke/regression suite passed.")
        accepts_ok = bool(runner_mod.smoke_evidence_valid(log_text, real_digest))
        results.append((
            "L-6 cross-module smoke constant truth (run_e2e effective value == real total)",
            numeric_ok and suite_ok and accepts_ok,
            f"run_e2e 生效 SMOKE_EXPECTED_CHECKS={effective}, 本次真实 total={expected_total}, "
            f"test_all 常量={SMOKE_EXPECTED_CHECKS}; suite_id 一致={suite_ok}; "
            f"真实日志形态被接受={accepts_ok}"
            + ("" if numeric_ok else " —— 单侧漂移会让真实全绿日志被判无效（v4.6.3 事故形态）"),
        ))

    # ── L-7 跨模块同名常量一致性（通用机制，覆盖"这一类错误"而非一个实例）──────────────
    # 本包反复出现的缺陷模式是「同一事实被声明两次，代码只校验其中一处」。L-4/L-6 只守住
    # SMOKE_* 这一个实例；L-7 用 AST 静态扫描（不 import，避免副作用）把"重复定义且不一致"
    # 这件事本身变成可检测的：凡顶层 `NAME = <字面量>` 出现在 ≥2 个文件，值必须一致。
    const_map = {}
    for source in sorted(list(SCRIPTS.glob("*.py")) + list((ROOT / "examples/e2e_demo").glob("*.py"))):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue  # 语法错误由 compileall 那一项负责报，不在本护栏重复计分
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and isinstance(node.value, ast.Constant)):
                name = node.targets[0].id
                if name.isupper() and len(name) > 1:
                    const_map.setdefault(name, []).append(
                        (source.relative_to(ROOT).as_posix(), node.lineno, node.value.value))
    shared = {name: occ for name, occ in const_map.items() if len({o[0] for o in occ}) >= 2}
    conflicts = {name: occ for name, occ in shared.items()
                 if len({repr(o[2]) for o in occ}) > 1 and name not in CONST_CONSISTENCY_EXEMPT}
    conflict_detail = "; ".join(
        f"{name}: " + ", ".join(f"{path}:{lineno}={value!r}" for path, lineno, value in occ)
        for name, occ in sorted(conflicts.items()))
    results.append((
        "L-7 cross-module same-name constant consistency (AST scan, no import)",
        not conflicts,
        f"检查 {len(shared)} 个跨文件同名常量，冲突 {len(conflicts)} 个"
        + (f" —— {conflict_detail}" if conflicts else
           f"（已豁免 {len(CONST_CONSISTENCY_EXEMPT)} 个语义本就按模块不同的名字）"),
    ))
    return results


def main():
    parser = argparse.ArgumentParser(description="Release smoke/regression suite")
    parser.add_argument("--extended", action="store_true",
                        help="also run every CLI help check and legacy selftest (long-running extended audit)")
    args = parser.parse_args()
    suite_id = SMOKE_SUITE_ID + ("-EXTENDED" if args.extended else "")
    py = sys.executable
    source_digest = release_source_fingerprint(ROOT)
    print(f"SOURCE TREE SHA256: {source_digest}", flush=True)
    checks = []
    initial_checks = [
        ("compileall", [py, "-m", "compileall", "-q", str(SCRIPTS), str(ROOT / "examples/e2e_demo/run_e2e.py")]),
        ("shared numeric core", [py, str(SCRIPTS / "test_qto_core.py")]),
        ("adversarial domain regressions", [py, str(SCRIPTS / "test_domain_regressions.py")]),
        ("OpenBIM QTO / BOQ / delivery profile / snapshot diff regressions", [py, str(SCRIPTS / "test_openbim_qto.py")]),
    ]
    # These checks use isolated temporary fixtures and can run concurrently.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run, label, argv) for label, argv in initial_checks]
        checks.extend(future.result() for future in futures)
    # Focus the release gate on changed/high-risk interfaces. The package has a
    # separate full CLI inventory; launching every unrelated help parser at every
    # release added latency without testing business behavior and made constrained
    # CI runs unreliable.
    critical_cli_names = {
        "drafter_qto.py", "drawing_revision_diff.py", "gate_unit_consistency.py",
        "project_orchestrator.py", "qto_project_validate.py", "xcheck_model_vs_2d.py",
        "ifc_qto_extract.py", "boq_mapping.py", "ifc_delivery_check.py", "ifc_qto_diff.py",
        "openbim_pipeline.py",
    }
    cli_scripts = (sorted(SCRIPTS.glob("*.py")) if args.extended else
                   [SCRIPTS / name for name in sorted(critical_cli_names)])
    if args.extended:
        cli_scripts = [script for script in cli_scripts
                       if script.name not in {"test_all.py", "test_qto_core.py", "test_domain_regressions.py"}]
    # The extended audit imports many large optional dependencies across all
    # entrypoints. Lower concurrency and a larger bounded timeout avoid false
    # failures from CPU contention on constrained CI/container hosts.
    cli_workers = 4 if args.extended else 6
    cli_timeout = 90 if args.extended else 30
    with ThreadPoolExecutor(max_workers=cli_workers) as pool:
        futures = [pool.submit(run, f"CLI smoke: {script.name}",
                               [py, str(script), "--help"], cli_timeout)
                   for script in cli_scripts]
        checks.extend(future.result() for future in futures)
    regression = [
        ("drafter catalog synchronization", [py, str(SCRIPTS / "drafter_qto.py"), "verify"]),
        ("dimension wording selftest", [py, str(SCRIPTS / "gate_dimension_wording.py"), "selftest"]),
        ("BOQ lint selftest", [py, str(SCRIPTS / "boq_lint.py"), "selftest"]),
        ("vector forensics selftest", [py, str(SCRIPTS / "vector_forensics.py"), "selftest"]),
        ("column mapping selftest", [py, str(SCRIPTS / "colmap.py"), "selftest"]),
        ("expected total gate selftest", [py, str(SCRIPTS / "gate_expected_total.py"), "selftest"]),
        # v4.0.0
        ("IFC structure validation selftest", [py, str(SCRIPTS / "ifc_validate.py"), "selftest"]),
        ("IFC QTO extraction selftest", [py, str(SCRIPTS / "ifc_qto_extract.py"), "selftest"]),
        ("legend/tag/replica guard selftest", [py, str(SCRIPTS / "qto_legend_guard.py"), "selftest"]),
        ("repeat group expansion selftest", [py, str(SCRIPTS / "qto_repeat_groups.py"), "selftest"]),
        ("project orchestrator selftest", [py, str(SCRIPTS / "project_orchestrator.py"), "selftest"]),
        ("project manifest/rule/unit registry selftest", [py, str(SCRIPTS / "qto_project_validate.py"), "--manifest", str(ROOT / "examples/e2e_demo/reference/project_manifest.json"), "--project-root", str(ROOT / "examples/e2e_demo"), "--selftest"]),
        # v4.5.1：尺寸链闸门自证（7 类历史错误必须全部被拦截）
        ("dimension chain gate selftest", [py, str(SCRIPTS / "chain_check.py"), "selftest"]),
        # v4.6.0：跨图一致性闸门自证（含反向断言：等价换算/容差内不得误判）
        ("cross-drawing consistency gate selftest", [py, str(SCRIPTS / "cross_drawing_check.py"), "selftest"]),
        # v4.6.0：切块 manifest 工具自证（重叠率与字高判据）
        ("render tiles manifest selftest", [py, str(SCRIPTS / "render_tiles.py"), "selftest"]),
        # v4.6.3：表外缺席门禁自证（自带夹具 12 用例，改判据当天也能跑）
        ("off-table gaps gate selftest", [py, str(SCRIPTS / "gate_off_table.py"), "selftest"]),
        # v4.6.4：v4.6.2/v4.6.3 的三个整改自证必须进默认闸，否则「三组全绿」无法被自动验证。
        # 此前它们只被 --extended 的 CLI --help 冒烟偶然跑到（三个脚本没有 argparse，
        # --help 会直接跑完全部用例）——那是侥幸，不是设计。
        ("source semantics gate self-test", [py, str(SCRIPTS / "test_source_semantics.py")]),
        ("input digest gate self-test", [py, str(SCRIPTS / "test_input_digest.py")]),
        ("execution receipt gate self-test", [py, str(SCRIPTS / "test_execution_receipt.py")]),
    ]
    if not args.extended:
        critical_regressions = {
            "drafter catalog synchronization", "dimension wording selftest",
            "IFC structure validation selftest", "IFC QTO extraction selftest", "project manifest/rule/unit registry selftest",
            "dimension chain gate selftest",
            # v4.6.0：新增门禁的自证必须每次都跑，不能只在 --extended 里跑
            "cross-drawing consistency gate selftest", "render tiles manifest selftest",
            "off-table gaps gate selftest",
            # v4.6.4：三个整改自证同样必须每次都跑（L-5 断言它们过滤后仍在）
            "source semantics gate self-test", "input digest gate self-test",
            "execution receipt gate self-test",
        }
        regression = [item for item in regression if item[0] in critical_regressions]
    # 并发度与超时：三个整改自证会各自拉起大量子进程（input_digest 单跑约 45s，但在 8 路并发下
    # 可被拖到 200s+）。若沿用统一的 240s 上限，加上它们之后会出现**假超时**——那会被误读成
    # 门禁失败。故 (a) 默认并发从 8 降到 5；(b) 对已知重负载项给单独放宽的超时。
    regression_timeouts = {
        "source semantics gate self-test": 420,
        "input digest gate self-test": 600,
        "execution receipt gate self-test": 420,
    }
    regression_workers = 4 if args.extended else 5
    with ThreadPoolExecutor(max_workers=regression_workers) as pool:
        futures = [pool.submit(run, label, argv, regression_timeouts.get(label, 240))
                   for label, argv in regression]
        checks.extend(future.result() for future in futures)
    # v4.6.4 静态护栏：把「文档写了 ≠ 会被加载 ≠ 会被执行」变成可失败的断言。
    # 必须是静态检查（不启动子进程），且必须在默认模式跑——可加载性问题不会因为
    # 没人加 --extended 就变好。传入过滤后的 regression，L-5 才能证明"真的会被执行"。
    print("\n=== static consistency guards ===", flush=True)
    for label, ok, detail in static_guards(regression, len(checks)):
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
        checks.append(ok)
    passed = sum(checks)
    total = len(checks)
    # v4.6.4：SMOKE_EXPECTED_CHECKS 必须与实际检查数自洽。
    # 背景：v4.6.3 把 total 从 23 增到 24，却漏改 examples/e2e_demo/run_e2e.py 里的
    # 同名常量，导致真实 24/24 全绿日志被 smoke_evidence_valid() 判为无效（证据链断）。
    # 旧有的守护用例用 {SMOKE_EXPECTED_CHECKS}/{SMOKE_EXPECTED_CHECKS} 自己构造日志，
    # 属于同义反复——常量写成任何值都能通过，永远发现不了漂移。此处改为与真实 total 对账。
    if not args.extended and total != SMOKE_EXPECTED_CHECKS:
        print(f"[FAIL] SMOKE_EXPECTED_CHECKS={SMOKE_EXPECTED_CHECKS} 与实际检查数 {total} 不一致 —— "
              f"该常量被 run_e2e.smoke_evidence_valid() 消费，不一致会使真实全绿日志被判无效")
        return 1
    source_digest_at_end = release_source_fingerprint(ROOT)
    source_tree_unchanged = source_digest == source_digest_at_end
    print(f"SOURCE TREE SHA256: {source_digest_at_end}")
    print(f"SOURCE TREE UNCHANGED: {source_tree_unchanged}")
    print(f"\nRELEASE SMOKE RESULT: {passed}/{total} passed (suite={suite_id})")
    if passed != total or not source_tree_unchanged:
        print("FAIL: any failed smoke/regression check or source mutation blocks release; do not treat as production acceptance.")
        return 1
    print("PASS: smoke/regression suite passed. This is not a substitute for project-specific end-to-end validation.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
