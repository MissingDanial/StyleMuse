"""
通用作家风格仿写 Skill - 风格分析器

从作家作品中自动提取写作风格特征，生成 style_guide.md 和 few_shot.md。
"""

import re
import random
from pathlib import Path
from typing import List, Dict, Any

from langchain_core.documents import Document
from .logger import get_logger
from .llm_logger import extract_response_text, invoke_with_logging

log = get_logger(__name__)


def extract_basic_stats(texts: List[str]) -> Dict[str, Any]:
    """
    提取基础文本统计信息。

    Args:
        texts: 文本片段列表

    Returns:
        统计信息字典
    """
    all_text = "\n".join(texts)

    # 句子分割
    sentences = re.split(r"[。！？…]+", all_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 2]

    # 平均句长
    avg_sentence_len = sum(len(s) for s in sentences) / max(len(sentences), 1)

    # 词汇（按字符级别统计常用字）
    chars = re.findall(r"[一-鿿]", all_text)
    total_chars = len(chars)

    # 字频统计
    char_freq: Dict[str, int] = {}
    for c in chars:
        char_freq[c] = char_freq.get(c, 0) + 1
    top_chars = sorted(char_freq.items(), key=lambda x: -x[1])[:30]

    # 标点习惯
    punctuation = {
        "逗号（，）": len(re.findall(r"，", all_text)),
        "句号（。）": len(re.findall(r"。", all_text)),
        "感叹号（！）": len(re.findall(r"！", all_text)),
        "问号（？）": len(re.findall(r"？", all_text)),
        "省略号（…）": len(re.findall(r"…", all_text)),
        "破折号（——）": len(re.findall(r"——", all_text)),
    }

    # 段落长度
    paragraphs = [p.strip() for p in all_text.split("\n") if len(p.strip()) > 10]
    avg_para_len = sum(len(p) for p in paragraphs) / max(len(paragraphs), 1)

    # 短句 vs 长句比例
    short_sentences = [s for s in sentences if len(s) <= 15]
    long_sentences = [s for s in sentences if len(s) >= 40]

    return {
        "total_chars": total_chars,
        "sample_count": len(texts),
        "sentence_count": len(sentences),
        "avg_sentence_len": round(avg_sentence_len, 1),
        "avg_paragraph_len": round(avg_para_len, 1),
        "short_sentence_ratio": round(len(short_sentences) / max(len(sentences), 1), 2),
        "long_sentence_ratio": round(len(long_sentences) / max(len(sentences), 1), 2),
        "top_chars": top_chars,
        "punctuation": punctuation,
    }


def collect_sample_texts(documents: List[Document], max_samples: int = 20, sample_len: int = 500) -> List[str]:
    """
    从文档中随机采样文本片段。

    Args:
        documents: Document 列表
        max_samples: 最大采样数
        sample_len: 每个样本的目标长度

    Returns:
        采样文本列表
    """
    # 合并所有文本
    all_texts = [doc.page_content for doc in documents if len(doc.page_content) > 50]

    if not all_texts:
        return []

    samples = []
    # 随机采样
    pool = list(all_texts)
    random.shuffle(pool)

    for text in pool:
        if len(text) >= sample_len:
            # 随机截取一段
            start = random.randint(0, max(0, len(text) - sample_len))
            samples.append(text[start : start + sample_len])
        else:
            samples.append(text)

        if len(samples) >= max_samples:
            break

    return samples


def build_analysis_prompt(author_name: str, samples: List[str], stats: Dict[str, Any]) -> str:
    """构建发送给 LLM 的分析 Prompt"""
    sample_text = "\n\n---\n\n".join(samples[:10])

    stats_desc = f"""## 基础统计
- 总字符数: {stats['total_chars']}
- 句子数量: {stats['sentence_count']}
- 平均句长: {stats['avg_sentence_len']} 字
- 平均段落长: {stats['avg_paragraph_len']} 字
- 短句比例（≤15字）: {stats['short_sentence_ratio']:.0%}
- 长句比例（≥40字）: {stats['long_sentence_ratio']:.0%}
- 高频字: {''.join(c for c, _ in stats['top_chars'][:15])}
- 标点习惯: {', '.join(f'{k} {v}次' for k, v in stats['punctuation'].items() if v > 0)}"""

    return f"""你是一位专业的文学评论家和写作风格分析师。请分析以下作家「{author_name}」的作品片段，输出两部分内容。

{stats_desc}

## 作品片段（采样）

{sample_text}

---

请输出两部分 Markdown 内容，用 `===STYLE_GUIDE===` 和 `===FEW_SHOT===` 分隔：

===STYLE_GUIDE===

生成一份风格指南（Markdown 格式），包含：
1. **风格概述**: 一段话总结该作家的核心风格
2. **语言特征**: 句式特点、用词习惯、修辞手法
3. **叙事视角**: 人称、叙事角度
4. **情感基调**: 情感表达方式
5. **意象与主题**: 常见的意象、主题
6. **写作禁忌**: 不符合该风格的表达方式

===FEW_SHOT===

从上面的作品片段中，挑选 3-5 个最能体现该作家风格的精彩段落，每个段落 100-200 字，标注来源主题。

注意：只输出 Markdown 内容，不要加额外解释。"""


def parse_analysis_result(response_text: str) -> tuple:
    """
    解析 LLM 返回的分析结果。

    Returns:
        (style_guide, few_shot_examples) 元组
    """
    parts = response_text.split("===FEW_SHOT===")

    if len(parts) >= 2:
        style_guide = parts[0].replace("===STYLE_GUIDE===", "").strip()
        few_shot = parts[1].strip()
    else:
        # 如果分隔符不存在，整体作为 style_guide
        style_guide = response_text.strip()
        few_shot = ""

    return style_guide, few_shot


def analyze_author(
    author_name: str,
    documents: List[Document],
    llm=None,
    output_dir: Path = None,
) -> Dict[str, str]:
    """
    分析作家写作风格，生成 style_guide.md 和 few_shot.md。

    Args:
        author_name: 作家名称
        documents: 作家的文档列表
        llm: LangChain LLM 实例（可选，不传则只生成基础统计）
        output_dir: 输出目录（可选，传则保存文件）

    Returns:
        {"style_guide": str, "few_shot": str, "stats": dict}
    """
    # 采样
    samples = collect_sample_texts(documents)
    if not samples:
        log.warning("警告: 无足够文本进行风格分析")
        return {"style_guide": "", "few_shot": "", "stats": {}}

    # 基础统计
    stats = extract_basic_stats(samples)
    log.info(f"  基础统计: {stats['sentence_count']} 句, 平均句长 {stats['avg_sentence_len']} 字")

    style_guide = ""
    few_shot = ""

    # LLM 深度分析
    if llm:
        log.info("  正在进行 LLM 风格分析...")
        prompt = build_analysis_prompt(author_name, samples, stats)

        from langchain_core.messages import HumanMessage

        response = invoke_with_logging(
            llm,
            [HumanMessage(content=prompt)],
            step="author_analysis",
            logger=log,
            metadata={
                "author": author_name,
                "sample_count": len(samples),
                "document_count": len(documents),
            },
        )
        content = extract_response_text(response.content)

        style_guide, few_shot = parse_analysis_result(content)
        log.info(f"  风格分析完成: style_guide {len(style_guide)} 字, few_shot {len(few_shot)} 字")
    else:
        # 无 LLM 时，生成基础风格描述
        style_guide = f"""## {author_name} 风格概述

（基于基础统计自动生成，未进行 LLM 深度分析）

### 语言特征
- 平均句长: {stats['avg_sentence_len']} 字
- 短句比例: {stats['short_sentence_ratio']:.0%}
- 长句比例: {stats['long_sentence_ratio']:.0%}

### 标点习惯
{chr(10).join(f'- {k}: {v}次' for k, v in stats['punctuation'].items() if v > 0)}

### 高频字
{''.join(c for c, _ in stats['top_chars'][:20])}
"""

    # 保存文件
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

        if style_guide:
            with open(output_dir / "style_guide.md", "w", encoding="utf-8") as f:
                f.write(style_guide)
            log.info(f"  已保存: {output_dir / 'style_guide.md'}")

        if few_shot:
            with open(output_dir / "few_shot.md", "w", encoding="utf-8") as f:
                f.write(few_shot)
            log.info(f"  已保存: {output_dir / 'few_shot.md'}")

    return {"style_guide": style_guide, "few_shot": few_shot, "stats": stats}
