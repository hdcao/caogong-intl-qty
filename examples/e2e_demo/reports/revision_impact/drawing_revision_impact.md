# Drawing Revision Impact Report

- Tool version: `1.1.0`
- Project: `SIM-E2E-001`
- Baseline snapshot SHA-256: `009c5d1afcf893b902327e177326583648e475d16918824681b4f809670a409c`
- Current snapshot SHA-256: `342b76e2b1c51cbafb12692e97735e1735d87565c775037f8ef6522a077b671d`
- Release review eligible: **YES**

## Summary

| Change type | Count |
|---|---:|
| ADDED | 1 |
| REVISION_AND_CONTENT_CHANGED | 1 |
| UNCHANGED | 2 |

## Sheet changes

| Sheet | Change | Baseline rev | Current rev | Impact disposition | Impact rows | Blockers |
|---|---|---|---|---|---:|---:|
| RD-001 | REVISION_AND_CONTENT_CHANGED | A | B | CLOSED_RECALCULATED_OR_REVIEWED | 1 | 0 |
| RD-002 | UNCHANGED | A | A | NOT_REQUIRED | 0 | 0 |
| RD-003 | UNCHANGED | A | A | NOT_REQUIRED | 0 | 0 |
| RD-004 | ADDED |  | B | CLOSED_NO_QUANTITY_IMPACT | 1 | 0 |

## Scope limitations

- This is a register/hash-based inventory comparison, not pixel-level PDF overlay or CAD semantic differencing.
- A changed file hash does not identify the exact geometric change or prove that quantities changed.
- An unchanged hash does not prove that the drawing baseline is contractually approved.
- A cleared impact row requires an identified human reviewer and evidence reference; software does not sign off engineering quantities.
