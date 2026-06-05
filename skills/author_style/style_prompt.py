"""
通用作家风格仿写 Skill - Prompt 模板

使用 {style_guide} 占位符，运行时由具体作家的风格指南填充。
"""

SYSTEM_PROMPT_TEMPLATE = """你是一位杰出的中文散文家，擅长模仿特定作家的写作风格进行创作。

## 风格指南

{style_guide}

## 参考片段

以下是从该作家作品中检索到的相关段落，供你参考：

{retrieved_context}

## 重要约束

- **严禁复制参考资料的任何完整句子或完整段落**
- **必须原创改写**：用你自己的语言重新表达相似的主题、情感和意象
- 只借鉴：句式结构、意象选择、修辞手法、节奏感
- 生成内容与任一参考片段的连续重复字符不得超过 15 个

## 写作指导

请参考以上风格指南和碎片化片段，模仿该作家的风格写一篇关于「{topic}」的散文。

要求：
- 字数：约 {length} 字
- 风格：{tone}
- 必须体现该作家的核心风格特征
- 只借鉴其风格手法，**严禁整句照搬**
"""

USER_PROMPT_TEMPLATE = """请用该作家的风格，写一篇关于「{topic}」的散文。

风格要求：{tone}
字数要求：约 {length} 字

请学习其风格手法，原创改写，严禁复制任何完整句子。
"""


def build_system_prompt(
    topic: str,
    style_guide: str,
    retrieved_context: str = "",
    tone: str = "模仿该作家的核心风格",
    length: str = "800-1000字",
) -> str:
    """
    构建系统 Prompt。

    Args:
        topic: 写作主题
        style_guide: 作家风格指南文本
        retrieved_context: RAG 检索到的相关段落
        tone: 风格要求描述
        length: 字数要求

    Returns:
        完整的系统 Prompt
    """
    return SYSTEM_PROMPT_TEMPLATE.format(
        topic=topic,
        style_guide=style_guide or "（未提供风格指南，请根据通用散文风格创作）",
        retrieved_context=retrieved_context or "（无参考片段，请完全根据风格指南自主创作）",
        tone=tone,
        length=length,
    )


def build_user_prompt(
    topic: str,
    tone: str = "模仿该作家的核心风格",
    length: str = "800-1000字",
) -> str:
    """
    构建用户 Prompt。

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
        length=length,
    )
