import numpy as np
import pytest
from langchain_core.documents import Document

from scripts.ablation_stats import cluster_effect, quality_score, validate_judgment
from scripts.rag_ablation import placebo_documents, context_from_docs


def test_topic_clusters_receive_equal_weight_despite_repeated_ratings():
    result = cluster_effect({"A": [6] * 10, "B": [-6]}, bootstrap_samples=1000)
    assert result["mean"] == 0
    assert result["topics"] == 2 and result["pairs"] == 11
    assert result["verdict"] == "insufficient_topics"


def test_uniform_positive_topic_effect_has_exact_two_sided_signflip_probability():
    result = cluster_effect({str(i): [4, 4] for i in range(6)}, bootstrap_samples=1000)
    assert result["mean"] == 4 and result["ci95"] == [4, 4]
    assert result["exact_signflip_p"] == pytest.approx(2 / 64, abs=1e-5)
    assert result["verdict"] == "benefit"


def test_zero_effect_is_not_interpreted_as_rag_benefit():
    result = cluster_effect({str(i): [0, 0] for i in range(6)}, bootstrap_samples=1000)
    assert result["verdict"] == "within_practical_margin"
    assert result["exact_signflip_p"] == 1
    assert quality_score({"style": 80, "naturalness": 80, "topic": 80, "originality": 80}) == 80


def test_invalid_or_unblinded_judge_mapping_is_rejected():
    with pytest.raises(ValueError, match="IDs"):
        validate_judgment({"candidates": [{"id": "wrong"}]}, ["anonymous"])
    with pytest.raises(ValueError, match="dimension"):
        validate_judgment({"candidates": [{"id": "anonymous", "style": True, "topic": 80, "naturalness": 80, "originality": 80}]}, ["anonymous"])


def test_placebo_has_matched_context_length_and_excludes_retrieved_neighbors():
    corpus = [Document(page_content=str(i) * 80, metadata={"chunk_id": i, "title": "title"}) for i in range(40)]
    accepted = [Document(page_content="参考" * 25, metadata={"chunk_id": 10, "title": "原有标题"})]
    placebo = placebo_documents(corpus, accepted, [10, 11, 12], 1)
    assert len(context_from_docs(placebo)) == len(context_from_docs(accepted))
    assert placebo[0].metadata["chunk_id"] not in range(8, 15)
    assert context_from_docs(placebo_documents(corpus, accepted, [10, 11, 12], 1)) == context_from_docs(placebo)
