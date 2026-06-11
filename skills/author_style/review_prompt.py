"""
Prompt template for the optional LLM review agent.
"""


REVIEW_JSON_SCHEMA = {
    "passed": "boolean",
    "score": "integer 0-100",
    "decision": "pass | warn | fail",
    "requirement": {
        "score": "integer 0-100",
        "issues": ["string"],
    },
    "style": {
        "score": "integer 0-100",
        "matched": ["string"],
        "missing": ["string"],
    },
    "plagiarism": {
        "risk": "low | medium | high",
        "notes": ["string"],
    },
    "suggestions": ["string"],
}


def build_review_prompt(
    author_name: str,
    topic: str,
    tone: str,
    length: str,
    article: str,
    style_guide: str = "",
    few_shot: str = "",
    plagiarism_result: dict = None,
) -> str:
    """Build a strict JSON-only review prompt."""
    return f"""你是 StyleMuse 的独立审稿 Agent。请审查生成作品是否满足写作要求、是否存在抄袭风险、文笔是否接近指定作家。

只输出一个 JSON 对象，不要输出 Markdown，不要解释 JSON 之外的内容。

JSON 结构参考：
{REVIEW_JSON_SCHEMA}

审查目标：
- 作家：{author_name}
- 主题：{topic}
- 语气：{tone}
- 长度档位：{length}

作家风格指南：
{style_guide or "未提供"}

Few-shot 示例：
{few_shot[:2000] or "未提供"}

已有重复检测结果：
{plagiarism_result or {}}

待审文章：
{article}

评分要求：
- requirement.score 评估是否扣题、是否满足长度/语气/主题要求。
- style.score 评估是否接近该作家的句式、意象、节奏、叙事视角和思想表达。
- plagiarism.risk 结合已有重复检测判断风险，不要只看字面重复。
- decision 为 pass / warn / fail。
- suggestions 给出 2-5 条可执行修改建议。
"""
