"""Isolated server for browser QA, using local model and embedding doubles."""
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
from langchain_core.embeddings import Embeddings
from skills.author_style import config, rag_chain, author_manager, semantic_reuse


class Model:
    def invoke(self, messages):
        prompt = messages[0].content
        if "===STYLE_GUIDE===" in prompt:
            text = "===STYLE_GUIDE===\n短句，日常意象，保留段落。\n===FEW_SHOT===\n乡村黄昏的意象。"
        else:
            text = "故乡的狗慢慢走过旧街，风把落叶推向墙角。\n\n" * 24
        time.sleep(0.15)
        return type("Reply", (), {"content":text})()
    def stream(self, messages):
        yield type("Chunk", (), {"content": "黄昏时，我把旧钥匙交给了摆渡人，他把它挂在桥边，等我回来时，渡口已变成了菜园，只有那把钥匙还悬在树上。\n\n"})()
        for index in range(30):
            time.sleep(0.08)
            yield type("Chunk", (), {"content":f"故乡的狗在第{index + 1}次风声里抬起头。\n\n"})()


class LocalEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[float(len(text)),float(sum(map(ord,text)) % 100)] for text in texts]
    def embed_query(self, text):
        return self.embed_documents([text])[0]


if __name__ == "__main__":
    with TemporaryDirectory(prefix="browser-qa-", dir=ROOT / ".tmp") as directory:
        root = Path(directory)
        authors = root / "authors"
        config.AUTHORS_DIR = author_manager.AUTHORS_DIR = authors
        app.project_root = root
        app.app.config.update(ACCESS_TOKEN="browser-password", JOB_WORKER_ENABLED=True)
        rag_chain.get_llm = lambda *args, **kwargs: Model()
        author_manager.get_llm = lambda *args, **kwargs: Model()
        config.get_embeddings = lambda *args, **kwargs: LocalEmbeddings()
        semantic_reuse.local_semantic_embeddings = lambda *args, **kwargs: LocalEmbeddings()
        author = author_manager.create_author("sample", analyze=False, build_index=False)
        from skills.author_style.cache import atomic_write_json
        atomic_write_json(author / "config.json", {"semantic_reuse_enabled": True})
        (author / "style_guide.md").write_text("短句，乡村日常意象。", encoding="utf-8")
        (author / "few_shot.md").write_text("黄昏时，我把旧钥匙交给了摆渡人，他把它挂在桥边，等我回来时，渡口已变成了菜园，只有那把钥匙还悬在树上。", encoding="utf-8")
        (author / "works" / "sample.txt").write_text("河岸上的柳树看着水面，老人站在桥头听船声。" * 40, encoding="utf-8")
        try:
            app.app.run(host="127.0.0.1", port=5367, debug=False)
        finally:
            for service in app.app.extensions.get("job_services", {}).values():
                service.close()
