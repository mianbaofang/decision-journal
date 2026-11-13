---
name: decision-journal
description: 别纠结 / Decision Journal — decision support for choices, pros and cons, decision journal and review. 当用户纠结选 A 还是 B、需要利弊 / 风险 / 可逆性 / 最小下一步、想把选择整理成决策简报，或要查看决策日志与复盘时使用；also for "help me decide", "should I quit / break up / move", decision journal and decision review.
---

# 别纠结 / Decision Journal

辅助人把选择说清楚，不替人做决定。用户可以给一句话、语音或产品 / 场景图片；Skill 输出可复核的决策简报、风险和下一步动作，并把结果存进本地决策日志，便于之后复盘。

## 触发范围

在以下情况调用：

- 用户问“选 A 还是 B”、纠结是否做某件事，或需要拆开代价、风险和可逆性；
- 英文请求如 “help me decide between two jobs”“should I quit / move / break up”“pros and cons”；
- 用户要从理性、随机、自然启示、国学参考、对话引导或自动模式看同一个问题；
- 用户要查看历史决策、执行状态、后悔标记、统计或复盘；
- 用户要把模糊的纠结整理成结构化 `.brief`。

不要把普通知识问答、一次性翻译、纯写作或高风险专业判断误当成决策辅助。医疗、法律、金融、心理和雇佣等问题只给决策框架，提醒用户咨询合资格专业人士。

## 首次运行

CLI 默认会自动启动随包后端，并等待 `/api/health` 通过，不需要先手动开服务：

```bash
python scripts/decision_journal.py --question "该不该换工作" --mode auto
```

- 不想自动启动：加 `--no-autostart`，此时后端未运行会直接给出提示。
- 后端在别处：用 `--base-url http://127.0.0.1:8010` 指定。
- 需要 Web UI：运行 `python backend/main.py`，再打开 `http://127.0.0.1:8010/`；API 文档在 `/docs`。
- 自动启动日志：`~/.decision-journal/backend.log`。

## 执行方式

1. 先确认问题、目标和关键约束；信息不足时只问一个最影响结论的问题，不凭空补事实。
2. 默认使用 `auto`，也可根据用户要求使用 `rational`、`random`、`nature`、`dialogue` 或 `fengshui`。国学 / 自然模式只作参考，不伪装成预测或证据；国学模式只引用本地引擎可验证的年柱与时辰，缺失的月柱 / 日柱会明确标注。
3. 先给短结论，再列视角、证据、风险、取舍和一个可执行的下一步。明确区分真实 LLM 结果与 Demo / mock 结果。
4. 用户需要时再保存、查看或删除决策记录；返回 `decisionId` 方便后续追踪与复盘。

CLI 入口：

```bash
python scripts/decision_journal.py --question "该不该换工作" --mode auto
python scripts/decision_journal.py --action archive
python scripts/decision_journal.py --action stats
python scripts/decision_journal.py --action export --id <id> --format md
```

完整参数以 `python scripts/decision_journal.py --help`、`backend/` 路由和前端设置页为准。

## 输出约定

按模式如实输出，不补造数据：

- `rational`：`pros` / `cons` / `conclusion` / `nextStep`，以及可核对的六项评分 `score`（benefit / risk / cost / reversibility / valueFit / confidence）。
- `random`：候选项与随机结果；没有 confidence 时不显示“证据强度”。
- `nature` / `fengshui`：视角与参考信息；`fengshui` 附 `baziAudit`，说明只做了哪些可验证步骤。
- `dialogue`：追问与选项。
- `chat` 通用字段：`mode`、`source`（`real` 或 `mock`）、可选 `decisionId`。只有结果里真的带 `confidence` 时才展示置信度。

`archive`、`stats`、`decision`、`export`、`config-api` 只执行用户明确要求的记录或导出操作。导出只在本地生成 Markdown / JSON 文件，不上传。

## 配置与安全

配置优先级为：本次请求参数 > 环境变量 > SQLite 设置 > `~/.decision-journal/config.json`。API Key 和天气 Key 由用户自行提供；不得写入回答、日志或演示数据，回显时必须脱敏。没有 LLM Key 时，明确提示用户选择 Demo，不把模拟结果说成真实分析；没有天气配置时，将自然模式结果标为模拟。

隐私默认：自动朗读关闭；TTS 文本通过 POST body 发送，不进入 URL 或访问日志。只有用户主动配置的 LLM、天气与朗读服务会离开本机。

## 发布验证

本 Skill 源目录内的 `evals/` 保存触发边界用例，`reports/` 保存输出质量验收标准；二者属于维护证据，打包时排除，不随安装 ZIP 分发。发布前应同时通过包完整性校验、Skill 结构校验和 CLI 帮助页冒烟检查；真实第三方模型输出与自动激活遥测不在本包的可验证范围内。
