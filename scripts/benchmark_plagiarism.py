"""Benchmark duplicate detection against local source texts, without model calls."""
import argparse
import json
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skills.author_style.config import get_author_dir
from skills.author_style.loader import load_source_documents, split_documents
from skills.author_style.plagiarism import check_plagiarism


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author", default="liu_liangcheng")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 100:
        parser.error("rounds must be between 1 and 100")
    logging.disable(logging.CRITICAL)
    sources = load_source_documents(get_author_dir(args.author))
    if not sources:
        parser.error("author has no source documents")
    article = "这是新写的文章。" * 75
    result = {"article_chars": len(article), "source_chars": sum(len(d.page_content) for d in sources)}
    for name, corpus in (("same_chunks", split_documents(sources)), ("full_source", sources)):
        times = []
        for _ in range(args.rounds):
            start = time.perf_counter()
            checked = check_plagiarism(article, corpus)
            times.append(time.perf_counter() - start)
        result[name] = {"seconds": times, "median_seconds": statistics.median(times),
                        "max_common": checked["max_common"], "passed": checked["passed"]}
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
