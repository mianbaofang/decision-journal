# examples · 真实运行产物

本目录存放**真实运行产生**的样例（非摆拍），可随时用当前代码复现：

| 文件 | 来源 | 复现命令 |
| --- | --- | --- |
| `sample-chat-response.json` | `POST /api/chat` 的完整响应（rational 模式，无 LLM Key 时 `source=mock`） | `curl -s -X POST http://127.0.0.1:8010/api/chat -H "Content-Type: application/json" -d '{"question":"该不该换工作","mode":"rational"}'` |
| `sample-brief-export.md` | 同一条决策记录的 Markdown 导出 | `curl -s "http://127.0.0.1:8010/api/decision/<decisionId>/export?format=md"` |

生成时间：2026-11-13，版本 v0.10.0。换 Key / 换模型后产物内容会不同，结构（summary / perspectives / risks / nextSteps / score / source）保持稳定。
