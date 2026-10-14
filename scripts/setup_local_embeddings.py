"""Download/verify local BGE, build an author index, optionally save .env settings.

No chat completions are performed. Only --download allows fetching public weights.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--deepseek-key-file", type=Path)
    parser.add_argument("--author", default="liu_liangcheng")
    parser.add_argument("--threshold", type=float, default=1.0, help="Squared L2 distance on unit vectors; lower is more similar")
    args = parser.parse_args()
    if not 0 < args.threshold <= 2:
        parser.error("Threshold must be in (0,2]")
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1", HF_HUB_DISABLE_XET="1")
    from skills.author_style.config import get_embeddings, get_author_dir, load_author_config
    from skills.author_style.loader import load_all_chunks
    from skills.author_style.vector_cache import create_vector_store, load_vector_store, vector_manifest
    from skills.author_style.cache import atomic_write_json, atomic_write_text
    from skills.author_style.local_embeddings import MODEL_NAME, QUERY_INSTRUCTION
    author = get_author_dir(args.author)
    if not author.is_dir():
        parser.error("Author does not exist")
    cfg = {**load_author_config(args.author), "embedding_provider": "local", "embedding_model": MODEL_NAME,
           "embedding_api_key": "", "embedding_base_url": "", "embedding_cache_dir": ".stylemuse/models",
           "embedding_local_only": not args.download, "embedding_query_instruction": QUERY_INSTRUCTION}
    start = time.perf_counter()
    embedding = get_embeddings(cfg)
    print("Loading local BGE model (CPU, no provider key)...", flush=True)
    vectors = embedding.embed_documents(["村里的狗趴在门前晒太阳。", "小狗躺在院子里休息。", "数据库通过事务保证一致性。"])
    query = embedding.embed_query("故乡的狗")
    import numpy as np
    similarities = np.asarray(vectors) @ np.asarray(query)
    if len(vectors[0]) != 512 or max(similarities[:2]) <= similarities[2]:
        raise ValueError("Chinese semantic sanity check failed")
    print("PASS normalized 512-dimensional semantic vectors", flush=True)
    documents = load_all_chunks(author, cfg["chunk_size"], cfg["chunk_overlap"])
    if not documents:
        raise ValueError("No source documents to index")
    directory = author / "cache" / "faiss"
    manifest = vector_manifest(documents, cfg)
    store = load_vector_store(embedding, directory, manifest)
    if store is None:
        print(f"Building real FAISS index: {len(documents)} chunks...", flush=True)
        store = create_vector_store(documents, embedding, directory, manifest)
    results = {}
    for topic in ("故乡的狗", "深夜的地铁", "一只旧闹钟", "数据库事务与并发隔离"):
        hits = store.similarity_search_with_score(topic, k=3)
        results[topic] = [{"distance": round(float(score), 5), "accepted": float(score) <= args.threshold,
                           "preview": doc.page_content[:80], "metadata": doc.metadata} for doc, score in hits]
    # Verify an offline reload, not just a successful initial download.
    offline = get_embeddings({**cfg, "embedding_local_only": True})
    offline.embed_query("本地检索验证")
    report = {"provider": "local", "model": MODEL_NAME, "runtime": "FastEmbed ONNX CPU", "dimensions": 512,
              "normalized": True, "chunks": len(documents), "threshold": args.threshold,
              "threshold_cosine_equivalent": round(1 - args.threshold / 2, 3), "queries": results,
              "semantic_probe_scores": similarities.tolist(), "offline_reload_passed": True,
              "seconds": round(time.perf_counter() - start, 3)}
    output = ROOT / ".stylemuse" / "embedding-setup.json"
    atomic_write_json(output, report)
    if args.configure:
        from dotenv import dotenv_values
        path = ROOT / ".env"
        values = dict(dotenv_values(path, interpolate=False)) if path.exists() else {}
        values.update(EMBEDDING_PROVIDER="local", EMBEDDING_MODEL=MODEL_NAME, EMBEDDING_API_KEY="",
                      EMBEDDING_BASE_URL="", EMBEDDING_CACHE_DIR=".stylemuse/models", EMBEDDING_LOCAL_ONLY="1")
        if args.deepseek_key_file:
            from scripts.live_deepseek_eval import load_key
            values.update(LLM_PROVIDER="openai", LLM_MODEL="deepseek-flash", LLM_BASE_URL="https://api.deepseek.com/v1",
                          LLM_API_KEY=load_key(args.deepseek_key_file))
        values.setdefault("STYLEMUSE_LOG_PREVIEWS", "0")
        lines = []
        for key, value in values.items():
            if value is None:
                continue
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            lines.append(f"{key}='{escaped}'")
        atomic_write_text(path, "\n".join(lines) + "\n")
        author_cfg_path = author / "config.json"
        author_cfg = json.loads(author_cfg_path.read_text(encoding="utf-8")) if author_cfg_path.exists() else {}
        author_cfg.update(embedding_provider="local", embedding_model=MODEL_NAME, embedding_base_url="",
                          embedding_api_key="", embedding_local_only=True,
                          embedding_query_instruction=QUERY_INSTRUCTION, similarity_threshold=args.threshold)
        atomic_write_json(author_cfg_path, author_cfg)
        print("PASS .env and author configuration saved (credentials not printed)", flush=True)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
