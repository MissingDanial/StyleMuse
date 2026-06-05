"""
通用作家风格仿写 Skill - 抄袭检测模块

生成后与原文做相似度比对，超标则提示。
"""

import re
from difflib import SequenceMatcher
from typing import List, Tuple

from langchain_core.documents import Document


def longest_common_substring(s1: str, s2: str) -> str:
    """查找两个字符串的最长公共子串"""
    m = len(s1)
    n = len(s2)
    max_len = 0
    end_pos = 0

    # 使用滚动数组优化空间
    prev = [0] * (n + 1)
    curr = [0] * (n + 1)

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if s1[i - 1] == s2[j - 1]:
                curr[j] = prev[j - 1] + 1
                if curr[j] > max_len:
                    max_len = curr[j]
                    end_pos = i
            else:
                curr[j] = 0
        prev, curr = curr, [0] * (n + 1)

    return s1[end_pos - max_len : end_pos]


def check_plagiarism(
    generated: str,
    source_documents: List[Document],
    max_common_len: int = 15,
    similarity_threshold: float = 0.6,
) -> dict:
    """
    检查生成内容与原文的相似度。

    Args:
        generated: 生成的文章
        source_documents: 原始文档列表
        max_common_len: 最长公共子串允许长度（字符）
        similarity_threshold: 整体相似度阈值

    Returns:
        {
            "passed": bool,          # 是否通过
            "max_common": int,       # 最长公共子串长度
            "max_common_text": str,  # 最长公共子串内容
            "similar_docs": list,    # 高相似度文档片段
            "warning": str,          # 警告信息
        }
    """
    if not generated or not source_documents:
        return {"passed": True, "max_common": 0, "max_common_text": "", "similar_docs": [], "warning": ""}

    max_common = 0
    max_common_text = ""
    similar_docs = []

    for doc in source_documents:
        source = doc.page_content
        if len(source) < 10:
            continue

        # 1. 最长公共子串检测
        lcs = longest_common_substring(generated, source)
        if len(lcs) > max_common:
            max_common = len(lcs)
            max_common_text = lcs

        # 2. 整体相似度
        ratio = SequenceMatcher(None, generated[:500], source[:500]).ratio()
        if ratio >= similarity_threshold:
            similar_docs.append({
                "title": doc.metadata.get("title", "未知"),
                "similarity": round(ratio, 3),
                "snippet": source[:100],
            })

    # 判定
    passed = max_common <= max_common_len
    warning = ""

    if not passed:
        warning = f"检测到与原文存在 {max_common} 字连续重复（允许上限 {max_common_len} 字）：「{max_common_text}」"
    elif similar_docs:
        warning = f"有 {len(similar_docs)} 个片段整体相似度较高，建议重新生成"

    return {
        "passed": passed,
        "max_common": max_common,
        "max_common_text": max_common_text,
        "similar_docs": similar_docs[:5],
        "warning": warning,
    }
