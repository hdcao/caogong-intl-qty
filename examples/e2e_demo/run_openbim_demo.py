#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run a reproducible OpenBIM QTO -> BOQ -> delivery-profile -> snapshot-diff demo."""
from __future__ import annotations
import hashlib, json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO = Path(__file__).resolve().parent
REF = DEMO / "reference" / "openbim"
OUT = DEMO / "reports" / "openbim"
SCRIPT = ROOT / "scripts"


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def run(name: str, args: list[str], expected_rc: int = 0) -> dict:
    started=time.monotonic()
    proc=subprocess.run([sys.executable, *map(str,args)], cwd=ROOT, text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=60)
    log=OUT/f'{name}.log'; log.write_text(proc.stdout, encoding='utf-8')
    return {"name":name, "command":[sys.executable,*map(str,args)], "exit_code":proc.returncode,
            "expected_exit_code":expected_rc, "status":"PASS" if proc.returncode==expected_rc else "FAIL",
            "duration_seconds":round(time.monotonic()-started,3), "log":str(log.relative_to(ROOT)).replace('\\','/'),
            "output_tail":proc.stdout.strip().splitlines()[-8:]}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sources=[REF/'model_rev_a.ifc', REF/'model_rev_b.ifc', REF/'boq_mapping_registry.json', REF/'ifc_delivery_profile.json']
    before={str(p.relative_to(ROOT)):sha256(p) for p in sources}
    a_qto=OUT/'model_rev_a_qto.json'; b_qto=OUT/'model_rev_b_qto.json'
    mapping=OUT/'boq_mapping.json'; profile=OUT/'delivery_check.json'; changed=OUT/'qto_diff_revisions.json'; unchanged=OUT/'qto_diff_same.json'
    steps=[]
    steps.append(run('extract_rev_a',[SCRIPT/'ifc_qto_extract.py','extract','--file',sources[0],'--out',a_qto]))
    steps.append(run('extract_rev_b',[SCRIPT/'ifc_qto_extract.py','extract','--file',sources[1],'--out',b_qto]))
    steps.append(run('delivery_profile',[SCRIPT/'ifc_delivery_check.py','check','--qto',a_qto,'--profile',sources[3],'--out',profile]))
    steps.append(run('boq_mapping',[SCRIPT/'boq_mapping.py','map','--qto',a_qto,'--registry',sources[2],'--project-id','SIM-E2E-OPENBIM-001','--out',mapping]))
    # Changes intentionally return exit 1 until quantity/BOQ impact is reviewed.
    steps.append(run('qto_diff_changed',[SCRIPT/'ifc_qto_diff.py','compare','--baseline',a_qto,'--current',b_qto,'--out',changed], expected_rc=1))
    steps.append(run('qto_diff_unchanged',[SCRIPT/'ifc_qto_diff.py','compare','--baseline',a_qto,'--current',a_qto,'--out',unchanged], expected_rc=0))
    pipeline_out = OUT / 'pipeline'
    steps.append(run('integrated_pipeline_review', [SCRIPT/'openbim_pipeline.py', 'run',
        '--baseline-ifc', sources[0], '--current-ifc', sources[1], '--registry', sources[2],
        '--profile', sources[3], '--project-id', 'SIM-E2E-OPENBIM-001', '--out-dir', pipeline_out], expected_rc=1))
    checks=[]
    for label, path, expected in [('delivery_profile',profile,'PASS'),('boq_mapping',mapping,'PASS'),('changed_diff',changed,'REVIEW_REQUIRED'),('unchanged_diff',unchanged,'NO_CHANGE'),('integrated_pipeline_review',pipeline_out/'pipeline_summary.json','REVIEW_REQUIRED')]:
        try:
            data=json.loads(path.read_text(encoding='utf-8'))
            actual=data.get('status')
            passed=actual==expected
            if label=='changed_diff': passed=passed and data.get('metrics',{}).get('quantity_changed',0)>=1
            if label=='integrated_pipeline_review':
                passed = passed and data.get('release_eligible') is False and data.get('source_inputs_unchanged') is True and data.get('stage_statuses',{}).get('boq_mapping') == 'PASS'
            checks.append({"check":label,"expected":expected,"actual":actual,"status":"PASS" if passed else "FAIL"})
        except Exception as exc:
            checks.append({"check":label,"expected":expected,"actual":None,"status":"FAIL","error":str(exc)})
    after={str(p.relative_to(ROOT)):sha256(p) for p in sources}
    unchanged_inputs=before==after
    summary={"schema_version":"1.0.0","run_type":"SYNTHETIC_OPENBIM_QTO_WORKFLOW","project_id":"SIM-E2E-OPENBIM-001",
             "status":"PASS" if all(x['status']=='PASS' for x in steps) and all(x['status']=='PASS' for x in checks) and unchanged_inputs else "FAIL",
             "steps":steps,"checks":checks,"input_hashes_before":before,"input_hashes_after":after,"input_sources_unchanged":unchanged_inputs,
             "scope_note":"Synthetic IFC STEP quantities only. No geometry recomputation, PDF/CAD visual diff, full IDS parser or contract measurement approval is implied. Integrated pipeline keeps changed QTO at REVIEW_REQUIRED and never auto-approves it."}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False,default=str)+'\n',encoding='utf-8')
    lines=['# OpenBIM QTO 合成工作流报告','',f"- 状态：**{summary['status']}**",f"- 子命令：{sum(x['status']=='PASS' for x in steps)}/{len(steps)} expected-exit checks passed",f"- 断言：{sum(x['status']=='PASS' for x in checks)}/{len(checks)} passed",f"- 输入哈希未变化：**{unchanged_inputs}**",'','## Checks','']
    lines += [f"- {x['status']} `{x['check']}`：expected `{x['expected']}` / actual `{x['actual']}`" for x in checks]
    lines += ['', '## 限制', '', summary['scope_note'], '']
    (OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(f"OPENBIM E2E RESULT: {summary['status']} | subcommands={sum(x['status']=='PASS' for x in steps)}/{len(steps)} | assertions={sum(x['status']=='PASS' for x in checks)}/{len(checks)}")
    print(f"report={OUT/'REPORT.md'}")
    return 0 if summary['status']=='PASS' else 1

if __name__=='__main__':
    raise SystemExit(main())
