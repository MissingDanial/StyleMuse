"""Calibrate local candidate recall on development source families, then test once."""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from langchain_core.documents import Document
from skills.author_style.cache import atomic_write_json, atomic_write_text
from skills.author_style.local_embeddings import LocalEmbeddings, MODEL_NAME
from skills.author_style.semantic_reuse import SemanticIndex, SEMANTIC_VERSION


def validate_dataset(dataset):
    sources = {row["id"]: row for row in dataset["sources"]}
    if len(sources) != len(dataset["sources"]):
        raise ValueError("duplicate source id")
    groups = defaultdict(set)
    for row in dataset["cases"]:
        if row["split"] not in {"development", "holdout"} or type(row["reuse"]) is not bool:
            raise ValueError("invalid label or split")
        groups[row["split"]].add(row["family"])
        for source in row["source_ids"]:
            if source not in sources or sources[source]["split"] != row["split"]:
                raise ValueError("source family leaked across splits")
        if row["reuse"] != bool(row["source_ids"]):
            raise ValueError("reuse label needs source ids")
    if groups["development"] & groups["holdout"]:
        raise ValueError("source family leaked across splits")
    if not groups["development"] or not groups["holdout"]:
        raise ValueError("need development and holdout groups")


def metrics(rows, threshold):
    positives = [row for row in rows if row["reuse"]]
    negatives = [row for row in rows if not row["reuse"]]
    source_hits = sum(row["source_score"] is not None and row["source_score"] >= threshold for row in positives)
    triggers = sum(row["max_score"] is not None and row["max_score"] >= threshold for row in negatives)
    return {"cases": len(rows), "positives": len(positives), "negatives": len(negatives),
            "expected_source_hits": source_hits, "source_recall_at_5": source_hits / len(positives) if positives else None,
            "negative_candidate_triggers": triggers,
            "negative_trigger_rate": triggers / len(negatives) if negatives else None}


def choose_threshold(rows, target=0.95):
    # Fit ONLY development expected-source recall; never optimize on holdout.
    values = sorted({-1.0, *[row["source_score"] for row in rows if row["reuse"] and row["source_score"] is not None]})
    eligible = [value for value in values if metrics(rows, value)["source_recall_at_5"] >= target]
    return max(-1.0, max(eligible) - 0.000001) if eligible else -1.0


def evaluate(dataset_path, output):
    raw = Path(dataset_path).read_bytes()
    dataset = json.loads(raw.decode("utf-8"))
    validate_dataset(dataset)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    protocol = {"dataset_sha256": hashlib.sha256(raw).hexdigest(), "model": MODEL_NAME,
                "semantic_version": SEMANTIC_VERSION, "mode": "symmetric_no_instruction", "top_k": 5,
                "selection": "highest development threshold retaining at least 95% expected-source recall",
                "decision_scope": "candidate retrieval only; all candidates require human review", "paid_calls": 0,
                "group_splits": {split: sorted({row["family"] for row in dataset["cases"] if row["split"] == split})
                                 for split in ("development", "holdout")}}
    atomic_write_json(output / "protocol.json", protocol)
    embedding = LocalEmbeddings(query_instruction="", local_files_only=True)
    documents = [Document(page_content=source["text"], metadata={"source_id": source["id"], "title": source["title"]})
                 for source in dataset["sources"]]
    index = SemanticIndex(documents, embedding, ROOT / ".stylemuse/semantic-benchmark-cache")
    rows = []
    for row in dataset["cases"]:
        found = index.query(row["generated"], threshold=-1.0, top_k=5, max_candidates=50)
        expected = [hit["similarity"] for hit in found["candidates"] if hit["source_id"] in row["source_ids"]]
        all_scores = [hit["similarity"] for hit in found["candidates"]]
        rows.append({**row, "source_score": max(expected) if expected else None,
                     "max_score": max(all_scores) if all_scores else None, "candidates": found["candidates"]})
    development = [row for row in rows if row["split"] == "development"]
    threshold = choose_threshold(development)
    profile = {**protocol, "threshold": threshold, "calibration": "constructed_development_candidates",
               "automatic_verdict": False}
    summary = {"threshold": threshold, "development": metrics(development, threshold),
               "holdout": metrics([row for row in rows if row["split"] == "holdout"], threshold),
               "by_kind_holdout": {kind: metrics([row for row in rows if row["split"] == "holdout" and row["kind"] == kind], threshold)
                                   for kind in sorted({row["kind"] for row in rows})},
               "model": MODEL_NAME, "paid_calls": 0, "automatic_verdict": False,
               "limitation": dataset["label_basis"]}
    atomic_write_json(output / "profile.json", profile)
    atomic_write_json(output / "samples.json", rows)
    atomic_write_json(output / "summary.json", summary)
    holdout = summary["holdout"]
    atomic_write_text(output / "report.md", "\n".join([
        "# 本地语义候选召回测评", "", f"模型：{MODEL_NAME}；双侧直接编码；付费 API 调用 0。",
        f"来源家族分离：开发集 {len(protocol['group_splits']['development'])} 组、留出集 {len(protocol['group_splits']['holdout'])} 组。",
        f"候选阈值 {threshold:.6f}，仅由开发集选择；测试集不参与调参。", "",
        f"留出集 {holdout['positives']} 个复用样本中召回期望来源 {holdout['expected_source_hits']} 个。",
        f"{holdout['negatives']} 个独立写作样本中有 {holdout['negative_candidate_triggers']} 个触发候选。", "",
        "候选触发不是抄袭判定；明确引用同样具有来源复用，应另行人工判定引用方式。",
        "标签来自自主构造的可追溯变换，没有独立人工审定，样本规模小，不能外推为真实散文的准确率。",
        "保留相同物件而改变事件的负例专门检验主题/实体相近造成的复核负担。所有候选须人工确认。", ""]))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "benchmarks/semantic_reuse/cases.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.dataset, args.output), ensure_ascii=False, indent=2))
