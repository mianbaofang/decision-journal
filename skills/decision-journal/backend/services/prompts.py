"""6 模式 prompt 模板。

对齐 HTML 版本 Prompts 对象，含 _humanize 去 AI 化约束。
auto 模式不直接用，由 mode_recognizer 路由后调用对应模式。
"""

from typing import Any, Dict

# 去 AI 味系统指令（基于 humanizer-zh 原则）
_HUMANIZE = """重要：用朋友聊天的口吻说话，不要像AI机器人。具体要求：
1. 别用"首先/其次/最后/综上所述/此外/然而"这种机械连接词，换成"说白了/对了/但问题是/所以啊/老实说"
2. 句子长短混搭，别都是工整的中长句，偶尔来个短句。甚至断句。
3. 加入个人色彩："我个人觉得"、"依我看"、"说实话"
4. 用具体代替抽象，别空泛地说"有很多优势"，要说"能省下一半时间"
5. 可以有停顿和思考："嗯..."、"让我想想"、"怎么说呢"
6. 结尾别用"希望对您有帮助"，要像"就这样，有问题随时问\""""

# 价值观键名 → 中文标签
_VALUE_NAMES = {
    "efficiency": "效率优先",
    "risk": "风险规避",
    "growth": "成长导向",
    "relationship": "关系维护",
}


def rational(question: str, values: Dict[str, int]) -> str:
    """理性分析模式 prompt。

    Args:
        question: 用户的问题。
        values: 价值观权重 {efficiency, risk, growth, relationship}，0-100。
            取 top 2 注入 prompt，让 AI 优先考虑这些维度。

    Returns:
        严格 JSON prompt，输出结构：
        {"type":"rational","pros":[...],"cons":[...],"conclusion":"..."}
    """
    value_hint = ""
    if values:
        sorted_vals = sorted(values.items(), key=lambda x: x[1], reverse=True)
        top = [f"{_VALUE_NAMES.get(k, k)}({v})" for k, v in sorted_vals[:2]]
        value_hint = f"\n用户价值观偏好：{'、'.join(top)}。请在分析时优先考虑这些维度。"
    return (
        f"{_HUMANIZE}\n\n"
        f"你是决策助手，像个靠谱的朋友帮人分析问题。针对问题：\"{question}\"{value_hint}，输出严格JSON：\n"
        '{"type":"rational","pros":["利1（用大白话说）","利2"],"cons":["弊1","弊2"],'
        '"conclusion":"一句话建议，像朋友会说的那种","nextStep":"一个成本最低、可撤回的具体动作"}\n'
        "利弊各2-4条，结论一句话，nextStep 必须具体到一个今天或本周能做的动作。别用书面语，用口语。"
    )


def random(question: str) -> str:
    """天意随机模式 prompt。

    Returns:
        严格 JSON prompt，输出结构：
        {"type":"random","options":["选项1"..."选项6"]}，每项 ≤6 字。
    """
    return (
        f"{_HUMANIZE}\n\n"
        f"你是决策助手。针对问题：\"{question}\"，给出正好6个候选选项，输出严格JSON：\n"
        '{"type":"random","options":["选项1","选项2","选项3","选项4","选项5","选项6"]}\n'
        "选项要具体、接地气、有差异化，别用\"选项A\"这种。每项不超过6个字。"
    )


def nature(question: str, weather_ctx: Dict[str, Any]) -> str:
    """自然启示模式 prompt。

    Args:
        question: 用户的问题。
        weather_ctx: 天气上下文，字段对齐 HTML 版本（isReal/source/city/updateTime/
            date/time/season/weather/temperature/temperatureRange/wind/windMeter/
            humidity/air/pressure/sun）。

    Returns:
        严格 JSON prompt，输出结构含 time/season/weather/sun/wind/source/isReal/
        signal/poem/suggestion。
    """
    is_real = bool(weather_ctx.get("isReal"))
    if is_real:
        source_note = (
            f"数据源：{weather_ctx.get('source','')}，"
            f"城市：{weather_ctx.get('city','')}，"
            f"更新时间：{weather_ctx.get('updateTime') or '实时接口返回'}"
        )
    else:
        source_note = f"数据源：{weather_ctx.get('source','')}（天气接口未配置或不可用时的降级数据）"

    lines = [
        f"当前时间：{weather_ctx.get('date','')} {weather_ctx.get('time','')}".strip(),
        f"季节：{weather_ctx.get('season','')}",
        f"天气：{weather_ctx.get('weather','')}",
        f"气温：{weather_ctx.get('temperature')}℃" if weather_ctx.get("temperature") else "",
        f"温度区间：{weather_ctx.get('temperatureRange')}" if weather_ctx.get("temperatureRange") else "",
        f"风向风力：{weather_ctx.get('wind','')}",
        f"风速：{weather_ctx.get('windMeter')}" if weather_ctx.get("windMeter") else "",
        f"湿度：{weather_ctx.get('humidity')}" if weather_ctx.get("humidity") else "",
        f"空气质量：{weather_ctx.get('air')}" if weather_ctx.get("air") else "",
        f"气压：{weather_ctx.get('pressure')}" if weather_ctx.get("pressure") else "",
        f"天光信号：{weather_ctx.get('sun','')}",
        source_note,
    ]
    weather_line = "，".join(x for x in lines if x)

    time_v = weather_ctx.get("time", "")
    season_v = weather_ctx.get("season", "")
    weather_v = weather_ctx.get("weather", "")
    sun_v = weather_ctx.get("sun", "")
    wind_v = weather_ctx.get("wind", "")
    source_v = weather_ctx.get("source", "")
    is_real_json = "true" if is_real else "false"

    return (
        f"{_HUMANIZE}\n\n"
        f"你是自然决策师，像个看惯了山水的朋友。{weather_line}。"
        f"针对问题：\"{question}\"，结合此刻真实或降级的自然条件，用自然意象给点启示。\n"
        "输出严格JSON："
        '{"type":"nature",'
        f'"time":"{time_v}","season":"{season_v}",'
        f'"weather":"{weather_v}","sun":"{sun_v}",'
        f'"wind":"{wind_v}","source":"{source_v}",'
        f'"isReal":{is_real_json},'
        '"signal":"自然信号名","poem":"结合自然条件的诗意解读2-3句，别太文绉绉",'
        '"suggestion":"基于自然逻辑的建议一句话，像朋友会说的话"}'
    )


def dialogue(question: str) -> str:
    """对话引导模式 prompt。

    Returns:
        严格 JSON prompt，输出结构：
        {"type":"dialogue","question":"...","options":["选项1","选项2","选项3"]}。
    """
    return (
        f"{_HUMANIZE}\n\n"
        f"你是决策助手，通过反问帮用户自己想明白。针对问题：\"{question}\"，反问一个关键问题，输出严格JSON：\n"
        '{"type":"dialogue","question":"反问问题，要戳中要害、像朋友会问的那种",'
        '"options":["选项1","选项2","选项3"]}\n'
        "选项要具体，别用\"是/否/不确定\"这种敷衍的。"
    )


def fengshui(question: str, bazi: Any = None) -> str:
    """风水玄学模式 prompt。

    只使用本地引擎能验证的信息（年柱、时辰）；缺失的月柱/日柱明确标注，
    不要求模型凭空排盘，也不把民俗参考包装成预测。

    Args:
        question: 用户的问题。
        bazi: 本地 BaziEngine 的分析结果（dict 或对象），可为 None。

    Returns:
        严格 JSON prompt，输出结构含 needBirth/question/bazi/wuxing/element/
        analysis/suggestion/baziAudit。
    """
    if isinstance(bazi, dict):
        audit = bazi.get("audit", "") or ""
        pillars = bazi.get("pillars") if isinstance(bazi.get("pillars"), dict) else {}
        missing = bazi.get("missing") or []
    else:
        audit = ""
        pillars = {}
        missing = []
    pillar_text = "；".join(f"{key}柱：{value}" for key, value in pillars.items()) or "未提供"
    missing_text = "、".join(str(item) for item in missing) or "无"

    return (
        f"{_HUMANIZE}\n\n"
        f"你是决策参考助手，用传统民俗视角帮人换个角度看问题，但绝不宣称预测或事实。"
        f"问题：\"{question}\"。\n"
        f"本地引擎可验证的信息：{pillar_text}。缺失项：{missing_text}。审计：{audit or '未提供'}\n"
        "约束：只能引用上面给出的柱子，不得自行推算或编造月柱、日柱、大运、十神或喜用神。"
        "如果缺失关键出生信息，needBirth=true，并在 question 里追问缺少的项；"
        "如果信息足够，needBirth=false，给出明确标注为「民俗参考」的有限解读，不构成专业建议。\n"
        "输出严格JSON："
        '{"type":"fengshui","needBirth":false,"question":"",'
        '"bazi":"只列可用柱子，并标注缺失",'
        '"wuxing":"基于可用信息的有限解读，不编造十神或大运",'
        '"element":"初步参考，不作为喜用神结论",'
        '"analysis":"2-4句民俗视角解读，明确标注参考性质",'
        '"suggestion":"一句可执行的现实建议",'
        '"baziAudit":"仅年柱与时辰，未做完整排盘"}'
    )


def auto(question: str) -> str:
    """auto 模式 prompt（占位）。

    auto 模式不直接用，应由 mode_recognizer 先识别问题类型，
    再路由到具体模式（rational/random/nature/dialogue/fengshui）调用对应 prompt。
    本函数仅在未路由时作降级提示。
    """
    return (
        f"{_HUMANIZE}\n\n"
        f"auto 模式应由 mode_recognizer 先识别问题类型，再路由到具体模式"
        f"（rational/random/nature/dialogue/fengshui）。问题：\"{question}\"。"
        "请先用 mode_recognizer 识别，再调用对应模式的 prompt。"
    )
