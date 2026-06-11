"""
Tests for the independent review agent.
"""

from langchain_core.documents import Document

from skills.author_style.reviewer import review_article


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    def invoke(self, messages):
        return FakeResponse(
            '{"passed": true, "score": 88, "decision": "pass", '
            '"requirement": {"score": 90, "issues": []}, '
            '"style": {"score": 85, "matched": ["节奏接近"], "missing": []}, '
            '"plagiarism": {"risk": "low", "notes": []}, '
            '"suggestions": ["保留当前方向"]}'
        )


def _docs():
    return [
        Document(
            page_content=(
                "故乡的风从土墙边吹过，狗在门口睡着。"
                "人慢慢走远，影子留在尘土里。"
            )
            * 20,
            metadata={"title": "sample"},
        )
    ]


def test_review_article_passes_reasonable_article():
    article = (
        "故乡的狗在黄昏里伏着，耳朵贴着风声。"
        "我从土路上走过去，看见旧院子的门半开着。"
    ) * 12

    result = review_article(
        author_name="sample_author",
        topic="故乡的狗",
        tone="default",
        length="short",
        article=article,
        source_documents=_docs(),
        style_guide="短句，乡土意象，含蓄表达。",
        plagiarism_result={"passed": True, "max_common": 4, "similar_docs": [], "warning": ""},
    )

    assert result["decision"] in {"pass", "warn"}
    assert result["score"] >= 70
    assert result["requirement"]["score"] >= 70
    assert result["plagiarism"]["risk"] == "low"


def test_review_article_fails_high_plagiarism_risk():
    result = review_article(
        author_name="sample_author",
        topic="故乡的狗",
        tone="default",
        length="short",
        article="故乡的狗在门口睡着。" * 30,
        source_documents=_docs(),
        plagiarism_result={
            "passed": False,
            "max_common": 30,
            "max_common_text": "故乡的狗在门口睡着",
            "similar_docs": [],
            "warning": "连续重复过长",
        },
    )

    assert result["decision"] == "fail"
    assert result["passed"] is False
    assert result["plagiarism"]["risk"] == "high"


def test_review_article_degrades_without_source_documents():
    result = review_article(
        author_name="sample_author",
        topic="树",
        tone="default",
        length="short",
        article="一棵树站在院子里。" * 30,
        source_documents=[],
        style_guide="朴素自然。",
        plagiarism_result={"passed": True, "max_common": 0, "similar_docs": [], "warning": ""},
    )

    assert result["style"]["score"] == 65
    assert result["style"]["missing"]


def test_review_article_attaches_llm_review_when_enabled():
    result = review_article(
        author_name="sample_author",
        topic="故乡的狗",
        tone="default",
        length="short",
        article="故乡的狗在门口睡着。" * 30,
        source_documents=_docs(),
        plagiarism_result={"passed": True, "max_common": 0, "similar_docs": [], "warning": ""},
        config={"review_llm_enabled": True},
        llm=FakeLLM(),
    )

    assert result["agent"] == "rule+llm"
    assert result["llm_review"]["ok"] is True
    assert result["llm_review"]["data"]["decision"] == "pass"
