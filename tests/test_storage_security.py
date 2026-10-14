import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from ebooklib import epub
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

import app
from skills.author_style import config, author_manager
from skills.author_style.epub_loader import parse_epub, parse_epub_from_zip, load_epub_books
from skills.author_style.vector_cache import create_vector_store, load_vector_store, vector_manifest


@pytest.fixture
def client(tmp_path):
    with patch.object(app, "ENV_FILE", tmp_path / ".env"), patch.object(app, "project_root", tmp_path), \
         patch.dict(app.app.config, {"TESTING": True, "ACCESS_TOKEN": ""}):
        yield app.app.test_client()


@pytest.mark.parametrize("payload", [{"LLM_MODEL":"model\nINJECTED=1"}, {"LLM_MODEL":123},
    {"LLM_BASE_URL":"file:///etc/passwd"}, {"LLM_PROVIDER":"invalid"}, {"UNKNOWN":"value"}])
def test_invalid_configuration_is_rejected_without_file_write(client, payload):
    assert client.post("/api/config", json=payload).status_code == 400
    assert not app.ENV_FILE.exists()


def test_clear_and_quoted_config_values_roundtrip(client):
    assert client.post("/api/config", json={"LLM_API_KEY":"key # spaces ' quote \\ slash", "LLM_BASE_URL":"https://example.com/v1"}).status_code == 200
    assert app._read_env_file()["LLM_API_KEY"] == "key # spaces ' quote \\ slash"
    assert client.post("/api/config", json={"LLM_BASE_URL":"__CLEAR__"}).status_code == 200
    assert app._read_env_file()["LLM_BASE_URL"] == ""


def test_access_password_and_bearer_protect_api(client):
    with patch.dict(app.app.config, {"ACCESS_TOKEN":"test-password"}):
        assert client.get("/api/config").status_code == 401
        assert client.post("/api/session", json={"password":"wrong"}).status_code == 401
        assert client.get("/api/config", headers={"Authorization":"Bearer test-password"}).status_code == 200
        assert client.post("/api/session", json={"password":"test-password"}).status_code == 200
        assert client.get("/api/config").status_code == 200
        assert client.delete("/api/session").status_code == 200
        assert client.get("/api/config").status_code == 401


def test_unicode_access_password(client):
    with patch.dict(app.app.config, {"ACCESS_TOKEN":"随机访问密码"}):
        assert client.post("/api/session", json={"password":"随机访问密码"}).status_code == 200
        assert client.get("/api/config").status_code == 200


def test_remote_requests_and_cross_origin_writes_are_denied(client):
    assert client.get("/api/config", environ_overrides={"REMOTE_ADDR":"192.0.2.1"}).status_code == 401
    assert client.post("/api/config", json={"LLM_MODEL":"model"}, headers={"Origin":"https://other.example"}).status_code == 403
    assert client.get("/api/config", headers={"Host":"other.example"}).status_code == 403


def test_oversized_upload_returns_json_error(client):
    with patch.dict(app.app.config, {"MAX_CONTENT_LENGTH":10}):
        response = client.post("/api/author", data=b"x" * 11, content_type="application/octet-stream")
    assert response.status_code == 413
    assert response.get_json()["ok"] is False


@pytest.mark.parametrize("route", ["/api/write", "/api/write-stream", "/api/rewrite"])
@pytest.mark.parametrize("payload", [[], {"author":42}, {"author":"sample","topic":"topic","max_tokens":-1},
    {"author":"sample","topic":"topic","temperature":"NaN"}, {"author":"sample","topic":"topic","include_retrieval":"false"},
    {"author":"sample","topic":"topic","save_dir":"static"}])
def test_bad_generation_input_never_calls_model(client, route, payload):
    with patch.object(app, "AuthorStyleSkill") as skill:
        assert client.post(route, json=payload).status_code == 400
        skill.assert_not_called()


def test_missing_author_is_not_created_by_request_validation(client, tmp_path):
    from skills.author_style import rag_chain
    authors = tmp_path / "authors"
    with patch.object(config, "AUTHORS_DIR", authors), patch.object(rag_chain, "get_llm") as llm:
        response = client.post("/api/write-stream", json={"author":"missing","topic":"topic"})
        assert response.status_code == 404
        llm.assert_not_called()
        assert not (authors / "missing").exists()


def test_concurrent_same_topic_saves_never_overwrite(tmp_path):
    with patch.object(app, "project_root", tmp_path):
        with ThreadPoolExecutor(max_workers=8) as pool:
            paths = list(pool.map(lambda index: app._save_article("author", "same", str(index)), range(20)))
    assert len(set(paths)) == 20
    assert {p.read_text(encoding="utf-8") for p in paths} == {str(i) for i in range(20)}


def test_archive_and_restore_preserve_author_data(tmp_path):
    authors = tmp_path / "authors"
    with patch.object(config, "AUTHORS_DIR", authors), patch.object(author_manager, "AUTHORS_DIR", authors):
        author = author_manager.create_author("sample", analyze=False, build_index=False)
        for i in range(5):
            (author / "works" / f"{i}.txt").write_text(str(i))
        assert author_manager.delete_author("sample", confirm=False)
        assert not author.exists()
        assert author_manager.list_authors() == []
        archive_id = next((authors / ".trash").iterdir()).name
        restored = author_manager.restore_author(archive_id)
        assert sorted(p.read_text() for p in (restored / "works").glob("*.txt")) == [str(i) for i in range(5)]


class LocalEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[float(len(text)), float(sum(map(ord, text)) % 100)] for text in texts]
    def embed_query(self, text):
        return self.embed_documents([text])[0]


def test_vector_cache_rejects_changed_corpus_model_and_parameters(tmp_path):
    embedding = LocalEmbeddings()
    documents = [Document(page_content="source", metadata={"title":"sample"})]
    cfg = {"embedding_provider":"local","embedding_model":"a","chunk_size":100}
    manifest = vector_manifest(documents, cfg)
    create_vector_store(documents, embedding, tmp_path, manifest)
    assert load_vector_store(embedding, tmp_path, manifest) is not None
    assert load_vector_store(embedding, tmp_path, vector_manifest([Document(page_content="new")], cfg)) is None
    assert load_vector_store(embedding, tmp_path, vector_manifest(documents, {**cfg,"embedding_model":"b"})) is None
    assert load_vector_store(embedding, tmp_path, vector_manifest(documents, {**cfg,"chunk_size":50})) is None
    assert vector_manifest(documents, {**cfg,"embedding_api_key":"secret"}) == manifest


def test_failed_vector_publish_preserves_old_generation(tmp_path):
    embedding = LocalEmbeddings()
    documents = [Document(page_content="source")]
    manifest = vector_manifest(documents, {})
    create_vector_store(documents, embedding, tmp_path, manifest)
    before = (tmp_path / "manifest.json").read_text()
    with patch("langchain_community.vectorstores.FAISS.save_local", side_effect=OSError("disk error")):
        with pytest.raises(OSError):
            create_vector_store(documents, embedding, tmp_path, manifest)
    assert (tmp_path / "manifest.json").read_text() == before
    assert load_vector_store(embedding, tmp_path, manifest) is not None


def _epub(path, marker):
    book = epub.EpubBook()
    book.set_identifier("sample")
    book.set_title("Actual Book Title")
    book.set_language("zh")
    # Insert items in the opposite order from the spine.
    chapters = [epub.EpubHtml(title=str(i), file_name=f"chapter{i}.xhtml", lang="zh") for i in range(2)]
    for i, chapter in enumerate(chapters):
        chapter.content = "<html><body><p>" + (f"{marker}{i} 正文。" * 20) + "</p><p>第二段落。</p></body></html>"
        book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", chapters[1], chapters[0]]
    epub.write_epub(str(path), book)


def test_epub_title_spine_paragraphs_fallback_and_cache_invalidation(tmp_path):
    books = tmp_path / "epub"
    books.mkdir()
    path = books / "sample.epub"
    _epub(path, "旧内容")
    docs = parse_epub(path)
    assert docs[0].metadata["book_title"] == "Actual Book Title"
    assert "旧内容1" in docs[0].page_content
    assert "\n\n" in docs[0].page_content
    assert len(parse_epub_from_zip(path)) == 2
    cache = tmp_path / "cache.json"
    load_epub_books(books, cache)
    _epub(path, "新内容")
    assert "新内容1" in load_epub_books(books, cache)[0].page_content


def test_stream_done_is_not_sent_if_saving_fails(client):
    class FakeSkill:
        def __init__(self, *args, **kwargs): pass
        def write_stream(self, **kwargs): yield "draft"
        def _run_post_generation_checks(self, *args): pass
    with patch.object(app, "AuthorStyleSkill", FakeSkill), patch.object(app, "_save_article", side_effect=OSError("disk full")):
        response = client.post("/api/write-stream", json={"author":"sample","topic":"topic","include_retrieval":False})
        events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]
    assert "done" not in [event["type"] for event in events]
    assert events[-1]["type"] == "error"


def test_full_stream_review_save_and_rewrite_version_chain(client, tmp_path):
    from skills.author_style import rag_chain
    authors = tmp_path / "authors"
    with patch.object(config, "AUTHORS_DIR", authors), patch.object(author_manager, "AUTHORS_DIR", authors):
        directory = author_manager.create_author("sample", analyze=False, build_index=False)
        (directory / "works" / "source.txt").write_text("河岸上有一棵柳树，老人站在桥头，听远处的船声。" * 30, encoding="utf-8")
        (directory / "style_guide.md").write_text("短句，日常意象。", encoding="utf-8")
        class Model:
            def stream(self, messages):
                for text in ("故乡的狗望着门前的风。" * 20, "我走过旧巷，听见院子里一声轻响。" * 10):
                    yield type("Chunk", (), {"content":text})()
            def invoke(self, messages):
                return type("Reply", (), {"content":"故乡的狗在落日里慢慢走。" * 35})()
        with patch.object(rag_chain, "get_llm", return_value=Model()):
            response = client.post("/api/write-stream", json={"author":"sample","topic":"故乡的狗", "length":"short","include_retrieval":False})
            events = [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines() if line.startswith("data: ")]
            done = events[-1]
            assert done["type"] == "done"
            assert done["plagiarism"]["status"] == "checked"
            assert done["review"]["agent"] == "rule"
            assert [e["stage"] for e in events if e["type"] == "stage"] == ["generating","checking","saving"]
            draft_path = directory / "output" / (done["version"]["version_id"] + ".txt")
            assert draft_path.read_text(encoding="utf-8") == done["article"]
            rewrite = client.post("/api/rewrite", json={"author":"sample","topic":"故乡的狗","length":"short",
                "include_retrieval":False,"article":done["article"],"review":done["review"],"parent_version":done["version"]["version_id"]})
            assert rewrite.status_code == 200
            version = rewrite.get_json()["data"]["version"]
            assert version["kind"] == "rewrite"
            assert version["parent_version"] == done["version"]["version_id"]
            assert len(list((directory / "output").glob("*.version.json"))) == 2


def test_cli_saves_review_and_version_metadata_without_web_import(tmp_path):
    import main
    from argparse import Namespace
    from skills.author_style import rag_chain
    authors = tmp_path / "authors"
    with patch.object(config, "AUTHORS_DIR", authors):
        directory = author_manager.create_author("sample", analyze=False, build_index=False)
        model = type("Model", (), {"invoke":lambda self,messages:type("Reply", (), {"content":"故乡的狗在门边打盹。" * 40})()})()
        with patch.object(rag_chain, "get_llm", return_value=model):
            main.cmd_write(Namespace(author="sample",topic="故乡的狗",tone="default",length="short",model=None,temperature=None,no_retrieval=True))
        assert len(list((directory / "output").glob("*.review.json"))) == 1
        assert len(list((directory / "output").glob("*.version.json"))) == 1
