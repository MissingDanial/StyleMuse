"""CPU embeddings using FastEmbed's ONNX models, without a provider API key."""
from functools import lru_cache
from pathlib import Path
from threading import RLock

import numpy as np
from langchain_core.embeddings import Embeddings

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
_load_lock = RLock()


@lru_cache(maxsize=4)
def _load_engine(model_name, cache_dir, threads, local_files_only):
    try:
        from fastembed import TextEmbedding
    except ImportError as exc:
        raise RuntimeError("本地嵌入依赖未安装，请安装 requirements-local-embeddings.txt") from exc
    engine = TextEmbedding(model_name=model_name, cache_dir=cache_dir, threads=threads,
                           providers=["CPUExecutionProvider"], local_files_only=local_files_only)
    return engine, RLock()


class LocalEmbeddings(Embeddings):
    def __init__(self, model=MODEL_NAME, cache_dir=None, threads=2, batch_size=32,
                 query_instruction=QUERY_INSTRUCTION, local_files_only=True):
        self.model = model
        self.cache_dir = str(Path(cache_dir or Path(__file__).resolve().parents[2] / ".stylemuse/models").resolve())
        self.threads, self.batch_size = int(threads), int(batch_size)
        if not 1 <= self.threads <= 32 or not 1 <= self.batch_size <= 256:
            raise ValueError("本地嵌入线程数或批大小超出范围")
        self.query_instruction = query_instruction
        self.local_files_only = local_files_only

    def embed_documents(self, texts):
        if not texts:
            return []
        # Initialization and inference are serialized per shared engine. Author
        # tasks reuse model weights without racing tokenizer/session setup.
        with _load_lock:
            engine, lock = _load_engine(self.model, self.cache_dir, self.threads, self.local_files_only)
        with lock:
            matrix = np.asarray(list(engine.embed(texts, batch_size=self.batch_size, parallel=None)), dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[0] != len(texts) or not np.isfinite(matrix).all():
            raise ValueError("本地嵌入模型返回无效向量")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        if np.any(norms <= 0):
            raise ValueError("本地嵌入模型返回零向量")
        return (matrix / norms).tolist()

    def embed_query(self, text):
        return self.embed_documents([self.query_instruction + text])[0]
