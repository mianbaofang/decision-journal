# Host Agent workflow / 宿主 Agent 模式

The host Agent performs reasoning using the model already serving the conversation. The local CLI stores that result; `record` never calls another model. This does not extract or reuse the host platform's API key. The optional standalone chat / Web workbench still needs its own model configuration for real inference.

宿主 Agent 先在当前对话中分析问题、已知证据、未知信息、利弊与最小下一步，再按用户要求记录到本地。不要为保存一条结果再次调用模型，也不要把启发式分数说成成功概率。

## Prepare once / 首次准备

Only local storage/review/export or the workbench needs this setup; a conversation-only analysis can stay entirely with the host Agent.

From the installed Skill root, create a virtual environment and install the bundled backend requirements. Use that environment's Python for every later command.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt
.venv/Scripts/python scripts/decision_journal.py --help
```

macOS / Linux: replace `.venv/Scripts/python` with `.venv/bin/python`. This preparation downloads Python dependencies; reasoning itself belongs to the host Agent. A local FastAPI process and SQLite are shared with the optional workbench.

## Record an analysis / 记录分析

Write UTF-8 JSON to a user-chosen output path, for example `decision.json`:

```json
{
  "question": "Which learning plan should I try?",
  "mode": "rational",
  "result": {
    "pros": ["Plan A fits the available evening time"],
    "cons": ["Its real workload has not been measured"],
    "conclusion": "Try one week before committing",
    "nextStep": "Track time on two sample sessions"
  },
  "journal": {
    "chosenOption": "One-week trial of A",
    "expectedOutcome": "Two completed sessions without overtime",
    "reviewDate": "2026-12-01"
  }
}
```

```bash
python scripts/decision_journal.py --action record --input decision.json
```

Use the returned `id` for review or export. `result.source` (and optional `brief.source`) is set to `host-agent`. The payload is validated before storage by the local API. `record` does not call `/api/chat` or require an LLM key. Keep unsupported facts explicitly unknown; do not invent confidence to fill a field. Do not reuse a record ID unless the user intends to update that record.

## Review later / 事后复盘

Write `review.json` containing the patch below; preserve the user's actual outcome rather than reconstructing a prediction after the event.

```json
{"regret": false, "journal": {"actualOutcome": "Completed two sessions", "reviewNote": "Keep the shorter schedule"}}
```

```bash
python scripts/decision_journal.py --action review --id RECORD_ID --input review.json
python scripts/decision_journal.py --action export --id RECORD_ID --format md
```

计划字段可留空，不影响一句话开始。保存计划不算已复盘；填写实际结果 / 复盘笔记或明确标记后悔状态时才记录复盘时间。后悔率只基于已复盘样本；无样本显示空缺。复盘日期只是本地记录，不会后台提醒。

## Scoring boundary / 评分边界

The standalone rational score is a transparent heuristic: major-risk keyword signals take precedence over low-stakes signals; normalized duplicate reasons count once. It is not a measured probability, an evidence-quality score, or a validated risk model. It cannot resolve negation or every language and needs human context. Host-authored records may omit scores altogether.

## Data maintenance / 数据维护

Default records live outside the installation at `~/.decision-journal/decision_journal.db`. `data status` inspects without starting a backend; `data backup` creates a private SQLite snapshot. [Upgrade, migration and recovery](DATA_STORAGE.md) describes explicit overrides and the `--merge` / `--replace` safeguards. Backups may contain SQLite-saved keys and do not include separate configuration files.
