"""Regression tests for source provenance and evidence-based text reuse checks."""
from unittest.mock import patch
import json

import pytest
from langchain_core.documents import Document

from skills.author_style.loader import load_all_chunks, load_source_documents, reference_documents
from skills.author_style.plagiarism import check_plagiarism
from skills.author_style.reviewer import review_article

SOURCE = "黄昏时，我把旧钥匙交给了摆渡人，他把它挂在桥边，等我回来时，渡口已变成了菜园，只有那把钥匙还悬在树上。"


def _bundle(tmp_path):
    works = tmp_path / "works"
    works.mkdir()
    text = ("【标题】渡口\n【来源】https://example.com/work\n【分类】当代名篇\n【正文】\n"
            + SOURCE + "\n\n" + "岸边的白杨长高了。" * 8
            + "\n===========================================\n"
            + "【标题】发布会\n【分类】各地文讯\n【正文】\n" + "编辑介绍作品的获奖情况。" * 10)
    (works / "bundle.txt").write_text(text, encoding="utf-8")
    return text


def test_article_sources_keep_title_url_and_original_positions(tmp_path):
    text = _bundle(tmp_path)
    docs = load_source_documents(tmp_path)
    assert [doc.metadata["title"] for doc in docs] == ["渡口", "发布会"]
    assert docs[0].metadata["source_url"] == "https://example.com/work"
    assert docs[0].metadata["attribution"] == "unverified"
    for doc in docs:
        assert text[doc.metadata["source_start"]:doc.metadata["source_end"]] == doc.page_content
        assert "【正文】" not in doc.page_content
        assert "===" not in doc.page_content
    assert docs[0].metadata["source_id"] != docs[1].metadata["source_id"]


def test_reference_articles_excluded_from_retrieval_but_available_for_reuse_checks(tmp_path):
    _bundle(tmp_path)
    sources = load_source_documents(tmp_path)
    chunks = load_all_chunks(tmp_path, 50, 10)
    assert len(sources) == 2
    assert all(doc.metadata["title"] == "渡口" for doc in chunks)
    assert len({doc.metadata["chunk_id"] for doc in chunks}) == len(chunks)
    original = sources[0]
    for chunk in chunks:
        start = chunk.metadata["source_start"] - original.metadata["source_start"]
        assert original.page_content[start:start + len(chunk.page_content)] == chunk.page_content


def test_plain_work_remains_retrievable_and_cache_changes_with_parser_version(tmp_path):
    works = tmp_path / "works"
    works.mkdir()
    (works / "work.txt").write_text(SOURCE * 3, encoding="utf-8")
    assert load_all_chunks(tmp_path)
    from skills.author_style import loader
    with patch.object(loader, "SOURCE_PARSER_VERSION", 99), patch.object(loader, "split_documents", wraps=loader.split_documents) as split:
        load_all_chunks(tmp_path)
    assert split.called


def test_malformed_bundle_reports_source_instead_of_indexing_headers(tmp_path):
    works = tmp_path / "works"
    works.mkdir()
    (works / "bad.txt").write_text("【标题】缺少正文\n【分类】当代名篇\n" + "正文未标注。" * 20, encoding="utf-8")
    with pytest.raises(ValueError, match="缺少【正文】"):
        load_source_documents(tmp_path)


def test_punctuation_only_copy_is_not_passed_and_evidence_uses_original_text():
    changed = SOURCE.replace("，", "、").replace("。", "；")
    result = check_plagiarism(changed, [Document(page_content=SOURCE, metadata={"title": "渡口", "source_start": 100})])
    assert result["passed"] is False
    assert result["originality_status"] == "high_risk"
    hit = next(item for item in result["evidence"] if item["kind"] == "punctuation_insensitive")
    assert hit["generated"]["text"] == changed[hit["generated"]["start"]:hit["generated"]["end"]]
    assert hit["source"]["text"] == SOURCE[hit["source"]["start"] - 100:hit["source"]["end"] - 100]
    assert "、" in hit["generated"]["text"] and "，" in hit["source"]["text"]


def test_near_copy_requires_review_and_returns_actual_matching_window():
    copied = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏闰余成岁律吕调阳"
    changed = "".join(char if index % 5 else "改" for index, char in enumerate(copied))
    source = "非匹配开头。" * 100 + copied
    generated = "独立开头。" * 100 + changed
    result = check_plagiarism(generated, [Document(page_content=source)], similarity_threshold=0.5)
    assert result["passed"] is None
    assert result["originality_status"] == "needs_review"
    hit = next(item for item in result["evidence"] if item["kind"] == "near_text")
    assert hit["source"]["start"] > 0
    assert hit["source"]["text"] == source[hit["source"]["start"]:hit["source"]["end"]]
    assert hit["source"]["text"] != source[:100]


def test_multiple_short_copies_require_review_and_coverage_does_not_double_count_sources():
    phrases = ["赤橙黄绿青蓝紫黑白灰", "天地玄黄宇宙洪荒日月", "甲乙丙丁戊己庚辛壬癸"]
    generated = "新的观察。".join(phrases)
    docs = [Document(page_content=phrase, metadata={"title": str(index)}) for index, phrase in enumerate(phrases)]
    first = check_plagiarism(generated, docs)
    second = check_plagiarism(generated, docs + docs)
    assert first["originality_status"] == "needs_review"
    assert first["passed"] is None
    assert first["coverage"] == second["coverage"]
    assert first["coverage"]["matched_chars"] == 30


def test_source_locations_survive_nfkc_expansion_and_whitespace():
    source = "前言\nＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴ ﬁ"
    generated = "新段落\n A B C D E F G H I J K L M N O P Q R S T fi"
    result = check_plagiarism(generated, [Document(page_content=source)])
    assert result["passed"] is False
    for hit in result["evidence"]:
        assert hit["generated"]["text"] == generated[hit["generated"]["start"]:hit["generated"]["end"]]
        assert hit["source"]["text"] == source[hit["source"]["local_start"]:hit["source"]["local_end"]]


def test_same_direct_copy_does_not_repeat_as_three_equivalent_evidence_rows():
    result = check_plagiarism(SOURCE, [Document(page_content=SOURCE)])
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["kind"] == "exact"
    assert result["coverage"]["ratio"] == 1.0


def test_long_repetitive_source_has_bounded_copy_evidence():
    result = check_plagiarism("甲" * 80, [Document(page_content="甲" * 100000)])
    assert result["max_common"] == 80
    assert result["evidence_count"] == 1
    assert result["coverage"]["matched_chars"] == 80


def test_llm_review_prompt_limits_evidence_without_mutating_the_saved_report():
    from skills.author_style.review_prompt import build_review_prompt
    detected = {"originality_status": "high_risk", "evidence": [
        {"title": "来源", "kind": "exact", "generated": {"text": "甲" * 10000},
         "source": {"text": "乙" * 10000}} for _ in range(20)]}
    prompt = build_review_prompt("作者", "主题", "default", "short", "正文", plagiarism_result=detected)
    assert len(prompt) < 7000
    assert "甲" * 401 not in prompt
    assert len(detected["evidence"][0]["source"]["text"]) == 10000


def test_offline_audit_refuses_to_overwrite_the_original_sample_directory(tmp_path):
    from scripts.audit_originality import audit
    with pytest.raises(ValueError, match="原实验目录分开"):
        audit(tmp_path / "samples.json", tmp_path, tmp_path)


def test_offline_audit_preserves_samples_and_saves_source_evidence(tmp_path):
    from scripts.audit_originality import audit
    _bundle(tmp_path)
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    samples = baseline / "samples.json"
    original = json.dumps([{"id": "p1", "article": SOURCE, "topic": "渡口", "condition": "off"}], ensure_ascii=False)
    samples.write_text(original, encoding="utf-8")
    summary = audit(samples, tmp_path, tmp_path / "rechecked")
    assert samples.read_text(encoding="utf-8") == original
    assert summary["paid_calls"] == 0
    assert summary["high_risk_ids"] == ["p1"]
    result = json.loads((tmp_path / "rechecked" / "samples.json").read_text(encoding="utf-8"))
    assert result[0]["originality"]["evidence"][0]["title"] == "渡口"


def test_offline_audit_requires_saved_rag_context(tmp_path):
    from scripts.audit_originality import audit
    samples = tmp_path / "samples.json"
    samples.write_text(json.dumps([{"article": SOURCE, "condition": "rag", "panel": "p1"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="缺少保存的参考上下文"):
        audit(samples, tmp_path, tmp_path / "rechecked")


def test_text_check_discloses_that_specific_event_paraphrase_is_not_semantically_checked():
    generated = "傍晚，我将一枚老钥匙托付给撑船的老人。重返这里时，码头已经种满蔬菜，树枝上仍吊着那件信物。"
    result = check_plagiarism(generated, [Document(page_content=SOURCE)])
    assert result["methods"]["semantic"] == "not_checked"
    assert result["methods"]["structure"] == "not_checked"
    assert result["scope"]["coverage"] == "loaded_sources_only"
    assert "尚未检查" in result["warning"]


def test_prompt_references_use_the_generation_limit():
    docs = reference_documents("指南", "示" * 2500, "动态参考")
    assert [doc.metadata["source_type"] for doc in docs] == ["style_guide", "few_shot", "retrieval"]
    assert len(docs[1].page_content) == 2000


def test_copy_from_fixed_example_is_checked_when_rag_is_off_and_no_works_exist(tmp_path):
    from skills.author_style import rag_chain
    (tmp_path / "few_shot.md").write_text(SOURCE, encoding="utf-8")
    fake_model = type("Model", (), {"invoke": lambda self, messages: type("Reply", (), {"content": SOURCE})()})()
    with patch.object(rag_chain, "get_author_dir", return_value=tmp_path), patch.object(rag_chain, "get_llm", return_value=fake_model):
        skill = rag_chain.AuthorStyleSkill("test")
        skill.write("渡口", include_retrieval=False)
    assert skill.last_plagiarism_result["originality_status"] == "high_risk"
    assert skill.last_plagiarism_result["scope"]["source_types"] == ["few_shot"]
    assert skill.last_plagiarism_result["evidence"][0]["source_type"] == "few_shot"


def test_source_loading_failure_cannot_pass_using_only_prompt_references(tmp_path):
    from skills.author_style import rag_chain
    (tmp_path / "few_shot.md").write_text(SOURCE, encoding="utf-8")
    with patch.object(rag_chain, "get_author_dir", return_value=tmp_path), patch.object(rag_chain, "get_llm", return_value=object()):
        skill = rag_chain.AuthorStyleSkill("test")
        with patch.object(skill, "_load_source_documents", side_effect=ValueError("bad corpus")):
            skill._run_post_generation_checks("我在陌生城市修理自行车。", "城市", "default", "short")
    assert skill.last_plagiarism_result["passed"] is None
    assert skill.last_plagiarism_result["scope"]["corpus_load"] == "error"
    assert skill.last_review_result["passed"] is False


def test_high_llm_score_cannot_override_length_failure():
    model = type("Model", (), {"invoke": lambda self, messages: type("Reply", (), {
        "content": '{"passed":true,"score":99,"decision":"pass"}'})()})()
    result = review_article(author_name="test", topic="城市", tone="default", length="short",
                            article="城市有清晨的车声。", style_guide="朴素表达",
                            plagiarism_result={"passed": True}, config={"review_llm_enabled": True}, llm=model)
    assert result["requirement_status"] == "fail"
    assert result["requirement"]["checks"]["length"] is False
    assert result["decision"] == "fail"
    assert result["passed"] is False
