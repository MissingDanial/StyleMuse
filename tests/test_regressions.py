"""Regression cases for corpus updates and generated-content quality checks."""
import json
import random
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.documents import Document

from skills.author_style.loader import load_all_chunks
from skills.author_style.plagiarism import check_plagiarism, longest_common_substring
from skills.author_style.reviewer import review_article


def test_corpus_cache_tracks_content_deletion_and_chunk_parameters(tmp_path):
    works = tmp_path / "works"
    works.mkdir()
    first = works / "first.txt"
    first.write_text("甲乙丙丁。" * 30, encoding="utf-8")
    load_all_chunks(tmp_path)
    second = works / "second.txt"
    second.write_text("新增语料。" * 30, encoding="utf-8")
    docs = load_all_chunks(tmp_path, chunk_size=50, chunk_overlap=0)
    assert {d.metadata["filename"] for d in docs} == {"first.txt", "second.txt"}
    assert all(len(d.page_content) <= 50 for d in docs)
    # Same-length edits must invalidate, even when timestamps have low resolution.
    first.write_text("戊己庚辛。" * 30, encoding="utf-8")
    assert any("戊己庚辛" in d.page_content for d in load_all_chunks(tmp_path, 50, 0))
    first.unlink()
    assert {d.metadata["filename"] for d in load_all_chunks(tmp_path, 50, 0)} == {"second.txt"}
    second.unlink()
    assert load_all_chunks(tmp_path) == []


def test_corrupt_cache_is_rebuilt(tmp_path):
    (tmp_path / "works").mkdir()
    (tmp_path / "works" / "source.txt").write_text("完整语料。" * 30, encoding="utf-8")
    load_all_chunks(tmp_path)
    (tmp_path / "cache" / "chunks.json").write_text("{broken", encoding="utf-8")
    assert load_all_chunks(tmp_path)


@pytest.mark.parametrize("generated,docs", [("text", []), ("", [Document(page_content="source")])])
def test_plagiarism_without_inputs_is_not_passed(generated, docs):
    result = check_plagiarism(generated, docs)
    assert result["passed"] is None
    assert result["status"] == "not_checked"


def test_unknown_plagiarism_cannot_be_reviewed_as_passed():
    article = "故乡的狗在门前看着风。" * 40
    result = review_article(author_name="test", topic="故乡的狗", tone="default", length="short",
                            article=article, source_documents=[Document(page_content=article)],
                            style_guide="短句。", plagiarism_result=None)
    assert result["passed"] is False
    assert result["plagiarism"]["risk"] == "unknown"


def _review_with_llm(content, plagiarism_passed=True):
    article = "故乡的狗在门前看着风。" * 40
    llm = type("LLM", (), {"invoke": lambda self, messages: type("Reply", (), {"content": content})()})()
    return review_article(author_name="test", topic="故乡的狗", tone="default", length="short",
                          article=article, source_documents=[Document(page_content=article)], style_guide="短句。",
                          plagiarism_result={"passed": plagiarism_passed, "max_common": 0},
                          config={"review_llm_enabled": True}, llm=llm)


def test_llm_failure_participates_in_final_decision():
    result = _review_with_llm(json.dumps({"passed": False, "score": 10, "decision": "fail"}))
    assert result["decision"] == "fail"
    assert result["passed"] is False


def test_llm_pass_cannot_override_plagiarism_failure():
    assert _review_with_llm('{"passed": true, "score": 99, "decision": "pass"}', False)["decision"] == "fail"


@pytest.mark.parametrize("response", ['{"decision":"pass","score":999,"passed":true}',
                                      '{"decision":"pass","score":99,"passed":"true"}', '[]', 'not json'])
def test_invalid_llm_review_does_not_pass(response):
    result = _review_with_llm(response)
    assert result["llm_review"]["ok"] is False
    assert result["decision"] != "pass"


def _reference_lcs(left, right):
    best = ""
    for start in range(len(left)):
        for end in range(start + len(best) + 1, len(left) + 1):
            if left[start:end] in right:
                best = left[start:end]
            else:
                break
    return best


def test_lcs_matches_reference_on_random_repetitive_and_unicode_text():
    rng = random.Random(9)
    for _ in range(200):
        left = "".join(rng.choices("甲乙。ab ", k=rng.randint(0, 40)))
        right = "".join(rng.choices("甲乙。ab ", k=rng.randint(0, 40)))
        match = longest_common_substring(left, right)
        assert match in left and match in right
        assert len(match) == len(_reference_lcs(left, right))


def test_plagiarism_detects_copy_at_end_of_long_article():
    copied = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏"
    result = check_plagiarism("独立内容。" * 150 + copied, [Document(page_content=copied)])
    assert result["passed"] is False
    assert result["max_common"] == len(copied)


def test_whitespace_does_not_hide_continuous_copy():
    source = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏"
    result = check_plagiarism(" ".join(source), [Document(page_content=source)])
    assert result["passed"] is False


def test_near_copy_after_article_prefix_is_reported():
    source = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏闰余成岁律吕调阳"
    changed = "".join(char if index % 5 else "改" for index, char in enumerate(source))
    result = check_plagiarism("独立开头。" * 120 + changed, [Document(page_content=source)], similarity_threshold=0.5)
    assert result["similar_docs"]


def test_few_shot_is_in_generation_prompt(tmp_path):
    from skills.author_style import rag_chain
    (tmp_path / "style_guide.md").write_text("风格指南", encoding="utf-8")
    (tmp_path / "few_shot.md").write_text("特定示例段落", encoding="utf-8")
    llm = type("LLM", (), {"invoke": lambda self, messages: type("Reply", (), {"content": "原创"})()})()
    with patch.object(rag_chain, "get_author_dir", return_value=tmp_path), patch.object(rag_chain, "get_llm", return_value=llm):
        skill = rag_chain.AuthorStyleSkill("test")
        with patch.object(rag_chain, "invoke_with_logging", return_value=type("Reply", (), {"content": "原创"})()) as invoked:
            skill.write("主题", include_retrieval=False)
        assert "特定示例段落" in invoked.call_args.args[1][0].content
