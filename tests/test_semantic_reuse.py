"""Semantic retrieval is source evidence for review, never an automatic verdict."""
import json
from unittest.mock import patch

import numpy as np
import pytest
from langchain_core.documents import Document

from skills.author_style.semantic_reuse import SemanticIndex, sentence_units, merge_semantic_result, load_profile
from skills.author_style.plagiarism import check_plagiarism
from scripts.evaluate_semantic_reuse import validate_dataset, choose_threshold


class Embeddings:
    model = "fake"
    calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        return [[1, 0] if "钥匙" in text else [0, 1] for text in texts]

    def embed_query(self, text):
        raise AssertionError("semantic comparison must not add retrieval query instructions")


SOURCE = "黄昏时，我将旧钥匙交给摆渡人，托他保管到我回来的那一天。"
GENERATED = "傍晚我把一枚钥匙托付给撑船的老人，约定重返这里时领取。"


def docs():
    return [Document(page_content=SOURCE, metadata={"title": "渡口", "source_id": "source-1", "source_start": 100,
                                                   "source_line": 8, "filename": "work.txt"})]


def test_sentence_and_context_spans_are_exact_and_do_not_cross_paragraphs():
    text = "开头很短。\n\n" + SOURCE + "第二句叙述另一件足够具体的事，保留它的完整句子。"
    spans = sentence_units(text)
    assert spans
    assert all(span["text"] == text[span["start"]:span["end"]] for span in spans)
    assert all("开头很短" not in span["text"] for span in spans)
    assert any("第二句" in span["text"] and "钥匙" in span["text"] for span in spans)
    assert sentence_units("=" * 100) == []


def test_semantic_candidate_has_correct_source_and_generated_positions(tmp_path):
    index = SemanticIndex(docs(), Embeddings(), tmp_path)
    result = index.query(GENERATED, threshold=0.9)
    hit = result["candidates"][0]
    assert hit["kind"] == "semantic_candidate" and hit["assessment"] == "pending"
    assert hit["source"]["text"] == SOURCE[hit["source"]["start"] - 100:hit["source"]["end"] - 100]
    assert hit["generated"]["text"] == GENERATED[hit["generated"]["start"]:hit["generated"]["end"]]
    assert hit["source"]["line"] == 8
    assert len(hit["evidence_id"]) == 64


def test_cache_reuse_and_changed_source_invalidates_vectors(tmp_path):
    model = Embeddings()
    SemanticIndex(docs(), model, tmp_path)
    assert model.calls == 1
    cached = SemanticIndex(docs(), model, tmp_path)
    assert cached.cache_hit and model.calls == 1
    changed = [Document(page_content=SOURCE + "那一天桥边开满了野花。", metadata=docs()[0].metadata)]
    SemanticIndex(changed, model, tmp_path)
    assert model.calls == 2


def test_corrupt_numpy_cache_rebuilds_without_pickle(tmp_path):
    model = Embeddings()
    SemanticIndex(docs(), model, tmp_path)
    next(tmp_path.glob("*.npz")).write_bytes(b"broken")
    rebuilt = SemanticIndex(docs(), model, tmp_path)
    assert not rebuilt.cache_hit and model.calls == 2


def test_failed_embedding_does_not_replace_existing_good_cache(tmp_path):
    SemanticIndex(docs(), Embeddings(), tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.glob("*.npz")}
    broken = Embeddings()
    broken.embed_documents = lambda texts: [[float("nan"), 1] for _ in texts]
    with pytest.raises(ValueError):
        SemanticIndex([Document(page_content=SOURCE + "后来我又走了很长的路。")], broken, tmp_path)
    assert {path.name: path.read_bytes() for path in tmp_path.glob("*.npz")} == before


def test_semantic_candidates_cannot_clear_exact_copy_red_flags(tmp_path):
    lexical = check_plagiarism(SOURCE, docs())
    semantic = SemanticIndex(docs(), Embeddings(), tmp_path).query(SOURCE)
    merged = merge_semantic_result(lexical, semantic)
    assert merged["originality_status"] == "high_risk" and merged["passed"] is False


def test_paraphrase_candidate_requires_review_and_keeps_structure_unchecked(tmp_path):
    lexical = check_plagiarism(GENERATED, docs())
    merged = merge_semantic_result(lexical, SemanticIndex(docs(), Embeddings(), tmp_path).query(GENERATED))
    assert merged["originality_status"] == "needs_review" and merged["passed"] is None
    assert merged["methods"]["semantic"] == "candidates_only"
    assert merged["methods"]["structure"] == "not_checked"


def test_semantic_failure_is_not_a_clean_scan():
    result = merge_semantic_result(check_plagiarism(GENERATED, docs()), {"status": "error", "warning": "local unavailable"})
    assert result["passed"] is None and result["originality_status"] == "needs_review"


def test_unrelated_candidate_embedding_can_be_filtered(tmp_path):
    index = SemanticIndex(docs(), Embeddings(), tmp_path)
    result = index.query("我在车站修补了一整夜的背包，第二天继续去县城寻找工作。", threshold=0.8)
    assert result["candidates"] == []


def test_two_source_patchwork_keeps_both_source_alignments():
    second = "父亲把坏钟埋进了屋后的泥土里，说它应该在这里好好休息。"
    documents = docs() + [Document(page_content=second, metadata={"source_id":"source-2"})]
    result = SemanticIndex(documents, Embeddings()).query(SOURCE + "\n\n" + second, threshold=0.9)
    assert {hit["source_id"] for hit in result["candidates"]} == {"source-1", "source-2"}


def test_profile_model_and_segmentation_version_are_validated(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"model":"fake", "threshold":0.5, "mode":"symmetric_no_instruction"}), encoding="utf-8")
    with pytest.raises(ValueError, match="模型"):
        load_profile(profile)


def test_dataset_groups_are_disjoint_and_holdout_does_not_select_threshold():
    dataset = {"sources":[{"id":"one","split":"development"}], "cases":[
        {"family":"one","split":"development","reuse":True,"source_ids":["one"]},
        {"family":"one","split":"holdout","reuse":True,"source_ids":["one"]}]}
    with pytest.raises(ValueError, match="leaked"):
        validate_dataset(dataset)
    rows = [{"reuse":True,"source_score":0.7,"max_score":0.7}, {"reuse":False,"source_score":None,"max_score":0.99}]
    assert choose_threshold(rows) < 0.7


def test_production_semantic_failure_degrades_without_using_remote_embeddings(tmp_path):
    from skills.author_style import rag_chain
    (tmp_path / "style_guide.md").write_text("风格资料足够长以用于句段比对，但是不含任何具体事件。", encoding="utf-8")
    with patch.object(rag_chain, "get_author_dir", return_value=tmp_path), patch.object(rag_chain, "get_llm", return_value=object()):
        skill = rag_chain.AuthorStyleSkill("test")
        skill.config["semantic_reuse_enabled"] = True
        with patch("skills.author_style.semantic_reuse.local_semantic_embeddings", side_effect=RuntimeError("no local model")), \
             patch.object(rag_chain, "get_embeddings", side_effect=AssertionError("no remote fallback")):
            skill._run_post_generation_checks(GENERATED, "渡口", "default", "short")
    assert skill.last_plagiarism_result["methods"]["semantic"] == "error"
    assert skill.last_plagiarism_result["passed"] is None
