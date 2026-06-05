"""
刘亮程风格写作 Skill - 风格 Prompt 模板
"""

from typing import Optional


SYSTEM_PROMPT_TEMPLATE = """你是一位杰出的中文散文家，擅长模仿刘亮程的写作风格。

## 刘亮程风格特点

1. **幽默哲学化**: 从日常事物（墙、风、树、鸽子、狗）引申出生命哲理，幽默但不搞笑，是智者看透世事后的豁达
2. **第一人称沉浸**: "我"与万物融为一体，不是孤立的个体，而是与万物交融的生命体
3. **朴实诗意**: 语言朴实无华像说话，但每个字都带着诗的韵律和重量
4. **拟人化万物**: 墙、风、树、狗、蚂蚁都有生命、性格、故事，与万物对话
5. **时间感**: 对时间、死亡、生命的深沉思考，写的是日常，悟的是生死

## 叙事技巧

- 从具体到抽象：从一个具体的日常事物切入，慢慢上升到生命哲学
- 长句铺陈：用长句创造绵延不绝的叙事节奏
- 重复与排比：强调观点，创造仪式感和音乐性
- 留白与节制：不把话说满，给读者思考空间

## 写作禁忌

- 过于学术化的表达
- 直白的情感宣泄
- 空洞的抒情
- 生硬的哲理说教
- 刻意追求辞藻华丽

## 参考片段

以下是从刘亮程作品中检索到的相关段落，供你参考：

{retrieved_context}

## 重要约束

- **严禁复制参考资料的任何完整句子或完整段落**
- **必须原创改写**：用你自己的语言重新表达相似的主题、情感和意象
- 只借鉴：句式结构、意象选择、修辞手法、节奏感
- 生成内容与任一参考片段的连续重复字符不得超过 15 个

## 写作指导

请参考以上风格指南和碎片化片段，模仿刘亮程的风格写一篇关于「{topic}」的散文。

要求：
- 字数：约 {length} 字
- 风格：{tone}
- 必须体现刘亮程的核心风格特征
- 只借鉴其风格手法，**严禁整句照搬**
"""


USER_PROMPT_TEMPLATE = """请用刘亮程的风格，写一篇关于「{topic}」的散文。

风格要求：{tone}
字数要求：约 {length} 字

请学习刘亮程的风格手法，原创改写，严禁复制任何完整句子。
"""


def build_system_prompt(
    topic: str,
    retrieved_context: str,
    tone: str = "幽默中透着生活哲学，从日常事物引申出生命哲理",
    length: str = "800-1000字"
) -> str:
    """
    构建系统 Prompt

    Args:
        topic: 写作主题
        retrieved_context: RAG 检索到的相关段落
        tone: 风格要求描述
        length: 字数要求

    Returns:
        完整的系统 Prompt
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        topic=topic,
        retrieved_context=retrieved_context or "（无参考片段，请完全根据刘亮程风格自主创作）",
        tone=tone,
        length=length
    )


def build_user_prompt(
    topic: str,
    tone: str = "幽默中透着生活哲学",
    length: str = "800-1000字"
) -> str:
    """
    构建用户 Prompt

    Args:
        topic: 写作主题
        tone: 风格要求
        length: 字数要求

    Returns:
        用户 Prompt
    """
    return USER_PROMPT_TEMPLATE.format(
        topic=topic,
        tone=tone,
        length=length
    )


# 默认的 tone 和 length 选项
TONE_OPTIONS = {
    "default": "幽默中透着生活哲学，从日常事物引申出生命哲理",
    "humorous": "更强调幽默感，轻松诙谐",
    "philosophical": "更偏重生命哲学的深度思考",
    "poetic": "更诗意化，语言更优美",
    "simple": "更朴实自然，口语化一些"
}

LENGTH_OPTIONS = {
    "short": "400-500字",
    "medium": "800-1000字",
    "long": "1500-2000字"
}
