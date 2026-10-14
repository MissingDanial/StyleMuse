"""Verify configured local retrieval; --live opts into one real writing call."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--author", default="liu_liangcheng")
    parser.add_argument("--topic", default="故乡的狗")
    args = parser.parse_args()
    from skills.author_style.rag_chain import AuthorStyleSkill
    from skills.author_style.config import get_llm
    from skills.author_style.cache import atomic_write_json, atomic_write_text
    skill = AuthorStyleSkill(args.author, max_tokens=1200, temperature=0.7)
    context = skill._retrieve_context(args.topic, True)
    if not context or skill.retrieval_warning:
        raise RuntimeError("Configured retrieval returned no accepted context: " + skill.retrieval_warning)
    store = skill._get_vector_store()
    negative = skill._retrieve_context("数据库事务与并发隔离", True)
    if negative:
        raise RuntimeError("Unrelated negative query passed the configured threshold")
    report = {"author": args.author, "topic": args.topic, "context_chars": len(context),
              "index_dimensions": int(store.index.d), "indexed_chunks": int(store.index.ntotal),
              "embedding_provider": skill.config["embedding_provider"], "embedding_model": skill.config["embedding_model"],
              "threshold": skill.config["similarity_threshold"], "negative_query_filtered": True, "live": args.live}
    if args.live:
        from scripts.live_deepseek_eval import Recorder, ObservedModel
        output = ROOT / ".stylemuse" / "rag-verification" / datetime.now().strftime("%Y%m%d-%H%M%S")
        output.mkdir(parents=True, exist_ok=False)
        recorder = Recorder(output, skill.config["llm_api_key"], 1)
        skill.config.update(max_retries=0, review_llm_enabled=False)
        skill.llm = ObservedModel(get_llm(skill.config), recorder, skill.config["llm_model"], "real-rag-write")
        article = skill.write(args.topic, length="short", include_retrieval=True)
        call = recorder.calls[0]
        if call["status"] != "ok" or call.get("finish_reason") != "stop":
            raise RuntimeError("Live generation did not finish normally")
        if skill.config["llm_api_key"] in article:
            raise ValueError("Credential-like text blocked from article artifact")
        atomic_write_text(output / "article.txt", article)
        report.update(model=call.get("actual_model"), seconds=call["seconds"], usage=call.get("usage"),
                      article_chars=len(article), retrieval_warning=skill.retrieval_warning,
                      review=skill.last_review_result, plagiarism=skill.last_plagiarism_result)
        if skill.retrieval_warning:
            raise RuntimeError("Live generation unexpectedly fell back without RAG")
        recorder.save("result.json", report)
    filename = "rag-verification-live.json" if args.live else "rag-verification.json"
    atomic_write_json(ROOT / ".stylemuse" / filename, report)
    print(json.dumps({k:v for k,v in report.items() if k not in {"review", "plagiarism"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
