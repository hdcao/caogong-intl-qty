# 大型项目推进协议（v4.0.0）

> 适用：图纸上百张、跨专业、需要多轮复核的国际工程项目。
> 目标：**按计划连续推进、尽量不打断人、随时能接续、token 花在刀刃上。**

---

## 0. 先建计划，再动手

拿图纸就跑脚本是大型项目最常见的失败方式。正确顺序：

```
init（建计划） → card-init（建测量策略卡） → add-batch（排批次） → 逐批执行
```

工具：`scripts/project_orchestrator.py`

```bash
python scripts/project_orchestrator.py init --root . --project "Port City 港城公寓" --discipline 房建
python scripts/project_orchestrator.py card-init --root .
python scripts/project_orchestrator.py add-batch --root . --id B01 --title "建册与全量普查" \
    --scope "全部图纸目录" --inputs "图纸目录" --accept "建册 CONFIRMED；空壳与复本已登记" --budget medium
```

每个批次必须写清五件事，缺一不可：

| 字段 | 含义 |
|---|---|
| `scope` | 这一批覆盖哪些图/哪些部位 |
| `inputs` | 依赖哪些前序产物（批次 id 或文件路径） |
| `accept` | **验收条件**——什么算做完（可机械判定） |
| `budget` | 预计 token 量级（low/medium/high），用于排期与续算 |
| `depends_on` | 依赖批次 id |

**验收条件必须可判定**，例如"台账新增 N 行且门禁 0 FAIL/0 WARN"，
而不是"把 MEP 消化一下"。

---

## 1. 不打断协议（硬规矩）

| 情形 | 正确动作 | 禁止动作 |
|---|---|---|
| 遇到不确定（缺尺寸、口径不明、版本冲突） | **挂 OI 继续做下一批** | 停下来反复问用户 |
| 同一问题在不同批次反复出现 | 合并成一条 OI，附图证 | 每批重问一遍 |
| 某批次被卡住（缺关键输入） | 标 BLOCKED，挂 OI，转做无依赖的批次 | 硬等、或跳过不做 |
| 用户给了口头原则 | 立刻写进策略卡/口径表，然后继续 | 每遇到一个实例再问一次 |

**只有三种情况允许停下来问**：

1. 合同的计量口径本身没定（影响全部数量）；
2. 图纸缺失导致该批次完全无法开工（不是"部分量不了"）；
3. 交付物形式与合同要求冲突。

其余全部走 OI 台账。

---

## 2. 断点续算（会话中断/token 耗尽/换人接手）

状态落盘在 `takeoff/plan/project_plan.json` 与 `project_state.json`。

```bash
python scripts/project_orchestrator.py resume --root .
```

`resume` 输出三段信息，直接决定下一步：

1. **进度**：每批 status / 落账行数 / 门禁结果 / OI 数 / token；
2. **中断恢复点**：处于 `IN_PROGRESS` 的批次 → 先核对落账与门禁是否已写盘，
   **未写盘的按本批验收条件补跑，不要整批重来**；
3. **阻断未解**：`BLOCKED` 批次 → 解除后 `reopen`。

批次状态机：

```
PENDING ──start──> IN_PROGRESS ──done──> DONE
                      │
                      └──block──> BLOCKED ──reopen──> PENDING
```

`done` 时必须回填验收数据，否则状态不可信：

```bash
python scripts/project_orchestrator.py done --root . --id B01 --readings 26 --gates "7/2/0" --tokens 12000
```

`--gates` 记成 `必跑数/失败数/警告数`（例：`7/0/0`）。

---

## 3. Token 经济（大型项目专用）

| 做法 | 原因 |
|---|---|
| 机械能提取的（文件名/图号/哈希/图层统计/实体计数）**一律走脚本**，不要用自然语言推理 | 一次全量扫描 1227 个文件，人工翻页成本是脚本的几百倍 |
| 每批只回一句结论 + 数字，**不写过程长文** | 过程在落账与脚本日志里，回话里重复就是浪费 |
| 长文本产物（HTML 报告/清单）**生成文件，不要贴进对话** | 贴进对话的 token 是文件的数十倍 |
| 大表用 CSV/Excel 交付，**对话里只给汇总数** | |
| 已消化过的图纸**不重复扫** | 用内容指纹（sig）做幂等，重复内容直接跳过 |
| 派生数据（明细→汇总）走脚本，**不手抄** | 手抄必错，且不可复现 |

**推荐节奏**：每批一次「读图纸 → 落账 → 跑门禁 → 记 OI → done」的循环，
循环内不打断用户；循环间只报一行进度。

---

## 4. 每批的标准动作序列

```
1. next          取下一批（自动跳过依赖未完成的批）
2. start         标 IN_PROGRESS
3. 判型+读图     按 references/drawing-reading-playbook.md
4. 落账          drafter_qto.py add --file ... --drafter 曹工
5. 三防          qto_legend_guard.py classify / dupes
6. 门禁          drafter_qto.py check --file ...
7. oi            疑问挂账，不打断
8. done          回填行数/门禁/token
```

---

## 5. 批次预算与 token 记账

`--budget` 只做排期参考，`--tokens` 是真实记账。用途：

- 判断"这个项目已经花了多少"；
- 发现某批 token 异常高 → 通常意味着口径反复摇摆，应停下来固化口径；
- 续算时判断还剩多少余量，决定是否压缩后续批次。

**经验值**：全量普查批（机械扫描）low；读图落账批 medium；
跨专业冲突裁决批 high。high 批次应尽量拆小。

---

## 6. 交付前必须收口的三件事

1. 策略卡 `card-check` 通过（计量依据/口径/乘数/交付/审核责任无 TO_BE_CONFIRMED）；
2. OI 台账每条都有处置（已解答 / 转交付说明的假设 / 明确挂起）；
3. 台账里无 `待确认` 行、无未裁定的版本冲突、无未登记的乘数。
