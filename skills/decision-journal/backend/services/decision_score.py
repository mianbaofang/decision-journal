"""理性评分引擎。

移植自 HTML 版本的 DecisionScoreEngine（行 3347-3362）。
给定问题、rational 模式的 pros/cons 结果、用户价值观四元组，
计算出 benefit / risk / cost / reversibility / valueFit / confidence 六项分数。

所有分数为启发式参考值，不是实证概率；缺乏上下文时默认值不能证明风险低。
相同（含大小写、空白、标点）理由去重；不把日期词作为可逆性证据。
"""

import re
import unicodedata

from typing import Any, Dict, List, Optional

# 触发"高风险"判定的关键词
RISK_WORDS: List[str] = ["辞职", "买房", "投资", "分手", "离婚", "创业", "借钱"]

# 触发"高可逆"判定的关键词
REVERSIBLE_WORDS: List[str] = ["吃什么", "吃哪", "看什么电影", "看哪部电影", "试用", "免费试", "what to eat", "which movie", "free trial"]
RISK_WORDS += ["quit my job", "quit a job", "resign", "buy a house", "buying a house", "invest", "investment", "investments", "investing", "resigning", "divorce", "start a business", "borrow money", "break up"]


def _unique_reasons(items):
    """Exact/format-normalized duplicates are not additional evidence."""
    if not isinstance(items, list):
        return []
    found = {}
    for item in items:
        if not isinstance(item, str):
            continue
        key = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", item).casefold())
        if key:
            found.setdefault(key, item)
    return list(found.values())


def _has_signal(question: str, word: str) -> bool:
    if word.isascii():
        return bool(re.search(r"\b" + re.escape(word) + r"\b", question))
    return word in question


def _clamp(low: int, high: int, value: float) -> int:
    """把 value 限制在 [low, high] 区间并取整。"""
    return max(low, min(high, round(value)))


def score(
    question: str,
    result: Optional[Dict[str, Any]],
    values: Optional[Dict[str, int]],
) -> Dict[str, int]:
    """计算理性评分。

    参数：
      question: 用户原始问题
      result:   rational 模式产出的 {pros: [...], cons: [...]}（可为 None）
      values:   价值观四元组 {efficiency, risk, growth, relationship}，0-100 整数

    返回：{benefit, risk, cost, reversibility, valueFit, confidence}
    """
    q = unicodedata.normalize("NFKC", question or "").casefold()
    pros: List[str] = _unique_reasons((result or {}).get("pros"))
    cons: List[str] = _unique_reasons((result or {}).get("cons"))
    v = values or {}

    # risk：命中高风险词 → 72，否则 42
    risk = 72 if any(_has_signal(q, w) for w in RISK_WORDS) else 42

    # reversibility: specific low-stakes activities only; high-stakes signals take precedence.
    reversibility = 72 if risk < 72 and any(_has_signal(q, w) for w in REVERSIBLE_WORDS) else 46

    # benefit：pros 越多、且重视成长 → 越高，上限 90
    benefit = min(90, 48 + len(pros) * 10 + (6 if v.get("growth", 0) > 60 else 0))

    # cost：cons 越多、且厌恶风险 → 越高，上限 90
    cost = min(90, 38 + len(cons) * 12 + (8 if v.get("risk", 0) > 60 else 0))

    # valueFit：效率 + 成长 + (100 - risk) 三项均值
    value_fit = round(
        (
            v.get("efficiency", 50)
            + v.get("growth", 50)
            + (100 - min(100, risk))
        )
        / 3
    )

    # confidence：四项均值，clamp 到 [35, 88]
    confidence = _clamp(35, 88, (benefit + reversibility + value_fit + (100 - cost)) / 4)

    return {
        "benefit": benefit,
        "risk": risk,
        "cost": cost,
        "reversibility": reversibility,
        "valueFit": value_fit,
        "confidence": confidence,
    }
