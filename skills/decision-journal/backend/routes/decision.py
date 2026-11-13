"""决策记录 CRUD 路由。

- POST   /api/decision        保存（新增/覆盖）
- GET    /api/decision/{id}   查询单条
- PATCH  /api/decision/{id}   部分更新（executed/regret/dialogueHistory）
- DELETE /api/decision/{id}   删除

全部走 SQLite 持久化层（db.py），不再使用内存 _DECISIONS。
"""

import json
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse

import db
from models.schemas import DecisionPatch, DecisionSave

router = APIRouter()


@router.post("/api/decision")
def save_decision(body: DecisionSave) -> Dict[str, Any]:
    """保存一条决策记录，自动补齐 id 与 createdAt。返回保存后的 dict。"""
    payload = body.model_dump(exclude_none=True)
    saved = db.save_decision(payload)
    return saved


@router.get("/api/decision/{decision_id}")
def get_decision(decision_id: str) -> Dict[str, Any]:
    """按 id 查询单条决策记录。不存在返回 404。"""
    dec = db.get_decision(decision_id)
    if not dec:
        raise HTTPException(status_code=404, detail="decision not found")
    return dec


@router.patch("/api/decision/{decision_id}")
def update_decision(decision_id: str, body: DecisionPatch) -> Dict[str, Any]:
    """更新 executed/regret/dialogueHistory。返回更新后的 dict。"""
    patches = body.model_dump(exclude_none=True)
    updated = db.update_decision(decision_id, patches)
    if not updated:
        raise HTTPException(status_code=404, detail="decision not found")
    return updated


@router.delete("/api/decision/{decision_id}")
def delete_decision(decision_id: str) -> Dict[str, Any]:
    """删除一条决策记录。不存在返回 404。"""
    ok = db.delete_decision(decision_id)
    if not ok:
        raise HTTPException(status_code=404, detail="decision not found")
    return {"ok": True}


def _brief_markdown(decision: Dict[str, Any]) -> str:
    """把一条决策记录渲染成可分享、可 diff 的 Markdown 简报。"""
    brief = decision.get("brief") or {}
    result = decision.get("result") or {}
    question = decision.get("question") or "(未记录问题)"
    lines = [f"# 决策简报 · {question}", ""]
    lines.append(f"- 记录时间：{decision.get('createdAt') or '未知'}")
    lines.append(f"- 使用视角：{decision.get('mode') or '未知'}")
    source = brief.get("source") or result.get("source") or result.get("_source") or "unknown"
    lines.append(f"- 结果来源：{source}")
    confidence = brief.get("confidence")
    if isinstance(confidence, int):
        lines.append(f"- 置信度：{confidence}%")
    summary = brief.get("summary") or result.get("conclusion") or result.get("signal") or result.get("suggestion")
    if summary:
        lines += ["", "## 结论", "", str(summary)]
    perspectives = brief.get("perspectives") or []
    if perspectives:
        lines += ["", "## 支持与视角", ""] + [f"- {item}" for item in perspectives]
    risks = brief.get("risks") or []
    if risks:
        lines += ["", "## 风险", ""] + [f"- {item}" for item in risks]
    next_steps = brief.get("nextSteps") or []
    if next_steps:
        lines += ["", "## 下一步", ""] + [f"- {item}" for item in next_steps]
    score = result.get("score") if isinstance(result, dict) else None
    if isinstance(score, dict):
        lines += ["", "## 评分", ""]
        for key, label in (
            ("benefit", "收益"),
            ("risk", "风险"),
            ("cost", "成本"),
            ("reversibility", "可逆性"),
            ("valueFit", "价值匹配"),
            ("confidence", "评分信心"),
        ):
            if isinstance(score.get(key), int):
                lines.append(f"- {label}：{score[key]}")
    lines += ["", "## 状态", ""]
    lines.append(f"- 已执行：{'是' if decision.get('executed') else '否'}")
    lines.append(f"- 标记后悔：{'是' if decision.get('regret') else '否'}")
    lines += ["", "> 决策由你做出；本简报只整理证据、风险与下一步，不构成专业建议。", ""]
    return "\n".join(lines)


@router.get("/api/decision/{decision_id}/export")
def export_decision(decision_id: str, format: str = Query("md", pattern="^(md|json)$")) -> Any:
    """导出可分享的决策简报：Markdown 或 JSON。"""
    decision = db.get_decision(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail="decision not found")
    if format == "json":
        return JSONResponse(decision)
    markdown = _brief_markdown(decision)
    return PlainTextResponse(
        markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="decision-{decision_id}.brief.md"'},
    )
