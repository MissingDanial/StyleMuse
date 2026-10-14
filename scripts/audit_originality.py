"""Recheck saved ablation articles locally; no model/embedding/API invocation."""
import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skills.author_style.cache import atomic_write_json, atomic_write_text
from skills.author_style.loader import SOURCE_PARSER_VERSION, load_source_documents, reference_documents
from skills.author_style.plagiarism import check_plagiarism
from skills.author_style.reviewer import review_article
from skills.author_style.safety import validate_simple_name


def audit(samples_path: Path, author_dir: Path, output: Path, include_semantic=False):
    samples_path, author_dir, output = Path(samples_path), Path(author_dir), Path(output)
    if output.resolve() == samples_path.parent.resolve():
        raise ValueError("输出目录必须与原实验目录分开，避免覆盖原始样本")
    raw_samples = samples_path.read_bytes()
    samples = json.loads(raw_samples.decode("utf-8"))
    if not isinstance(samples, list) or not all(isinstance(row, dict) and isinstance(row.get("article"), str) for row in samples):
        raise ValueError("samples 必须是包含 article 的对象数组")
    panels_path = samples_path.parent / "panels.json"
    panels = {row["id"]: row for row in json.loads(panels_path.read_text(encoding="utf-8"))} if panels_path.exists() else {}
    for sample in samples:
        condition = sample.get("condition", "off")
        context_key = {"rag": "rag_context", "random": "placebo_context"}.get(condition)
        if context_key and context_key not in panels.get(sample.get("panel"), {}):
            raise ValueError("缺少保存的参考上下文，无法完整复查该实验条件")
    sources = load_source_documents(author_dir)
    snapshot = samples_path.parent / "runtime" / "authors" / author_dir.name
    reference_dir = snapshot if snapshot.is_dir() else author_dir
    def read_reference(name):
        path = reference_dir / name
        return path.read_text(encoding="utf-8") if path.exists() else ""
    guide, few_shot = read_reference("style_guide.md"), read_reference("few_shot.md")
    index, profile = None, None
    if include_semantic:
        from skills.author_style.semantic_reuse import SemanticIndex, load_profile, local_semantic_embeddings
        profile = load_profile(ROOT / "benchmarks/semantic_reuse/profile.json")
        index = SemanticIndex(sources + reference_documents(guide, few_shot), local_semantic_embeddings({}),
                              author_dir / "cache/semantic-reuse")
    results = []
    for sample in samples:
        condition = sample.get("condition", "off")
        panel = panels.get(sample.get("panel"), {})
        context = panel.get("rag_context", "") if condition == "rag" else panel.get("placebo_context", "") if condition == "random" else ""
        references = reference_documents(guide, few_shot, context)
        reuse = check_plagiarism(sample["article"], sources + references)
        if index:
            from skills.author_style.semantic_reuse import merge_semantic_result
            semantic = index.query(sample["article"], threshold=profile["threshold"], top_k=profile.get("top_k", 5))
            semantic["calibration"] = {"dataset_sha256": profile["dataset_sha256"], "basis": profile["calibration"], "automatic_verdict": False}
            reuse = merge_semantic_result(reuse, semantic)
        reviewed = review_article(author_name=author_dir.name, topic=sample.get("topic", ""), tone="default",
                                 length="short", article=sample["article"], source_documents=sources,
                                 style_guide=guide, few_shot=few_shot, plagiarism_result=reuse)
        results.append({"id": sample.get("id"), "condition": condition, "topic": sample.get("topic"),
                        "article": sample["article"], "originality": reuse, "review": reviewed,
                        "legacy": {"max_common": sample.get("plagiarism", {}).get("max_common"),
                                   "rule_decision": sample.get("rule_review", {}).get("decision"),
                                   "subjective_originality": sample.get("judge_mean", {}).get("originality")}})
    statuses = Counter(row["originality"]["originality_status"] for row in results)
    failed_requirements = [row for row in results if row["review"]["requirement_status"] == "fail"]
    summary = {
        "schema_version": 2, "source_parser_version": SOURCE_PARSER_VERSION,
        "paid_calls": 0, "subjective_judgments_unchanged": True,
        "sample_sha256": hashlib.sha256(raw_samples).hexdigest(), "sample_count": len(results),
        "reference_snapshot": "frozen_runtime" if reference_dir == snapshot else "current_author_files",
        "source_count": len(sources), "style_source_count": sum(doc.metadata.get("style_eligible", True) for doc in sources),
        "source_revisions": [{"id": doc.metadata["source_id"], "title": doc.metadata.get("title"),
                              "sha256": doc.metadata["content_sha256"]} for doc in sources],
        "originality_status_counts": {status: statuses[status] for status in ("low_risk", "needs_review", "high_risk", "not_checked")},
        "hard_requirement_fail_count": len(failed_requirements),
        "legacy_rule_pass_but_hard_fail": sum(row["legacy"]["rule_decision"] == "pass" for row in failed_requirements),
        "high_risk_ids": [row["id"] for row in results if row["originality"]["originality_status"] == "high_risk"],
        "needs_review_ids": [row["id"] for row in results if row["originality"]["originality_status"] == "needs_review"],
        "semantic": "candidates_only" if index else "not_checked", "structure": "not_checked",
        "semantic_candidates": sum(len(row["originality"].get("semantic", {}).get("candidates", [])) for row in results),
        "semantic_candidate_count_before_display_limit": sum(row["originality"].get("semantic", {}).get("candidate_count", 0) for row in results),
        "semantic_display_truncated_samples": sum(bool(row["originality"].get("semantic", {}).get("display_truncated")) for row in results),
        "semantic_adjudication": "pending" if index else "not_checked",
        "scope": "Current local complete works and saved prompt references; candidate risk only",
    }
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "samples.json", results)
    atomic_write_json(output / "summary.json", summary)
    lines = ["# 已有样本的文字与语义候选复查" if index else "# 已有样本的文字复用复查", "",
             f"本地复查 {len(results)} 篇，新增 API 调用为 0。主观盲评分与原实验记录保持不变。",
             f"原文来源 {len(sources)} 篇，其中 {summary['style_source_count']} 篇进入风格检索范围；查重保留全部来源。",
             "", "| 文字复用风险 | 篇数 |", "|---|---:|",
             *[f"| {status} | {count} |" for status, count in summary["originality_status_counts"].items()], "",
             f"篇幅等硬要求失败 {len(failed_requirements)} 篇，其中 {summary['legacy_rule_pass_but_hard_fail']} 篇曾被规则评审判为通过。",
             "", "高风险样本：" + ("、".join(summary["high_risk_ids"]) or "无"),
             "需复核样本：" + ("、".join(summary["needs_review_ids"]) or "无"), "",
             "这些状态反映文字检查和已启用的语义候选召回。语义候选尚待逐项复核，情节结构未自动检查，也不是侵权判决。",
             "去标点后的连续重合、多处短片段和近似窗口可能增加告警，需要核对具体证据与惯用表达。",
             "逐篇原文、生成文、对应位置、来源及检查范围保存在同目录 samples.json。"]
    atomic_write_text(output / "report.md", "\n".join(lines) + "\n")
    if samples_path.read_bytes() != raw_samples:
        raise RuntimeError("原始样本在复查期间发生变化")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--author", default="liu_liangcheng")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic", action="store_true", help="运行本地 BGE 语义候选召回，不调用生成或付费 API")
    args = parser.parse_args()
    author_name = validate_simple_name(args.author, "作家名称")
    summary = audit(args.samples, ROOT / "authors" / author_name, args.output, include_semantic=args.semantic)
    print(json.dumps({key: value for key, value in summary.items() if key != "source_revisions"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
