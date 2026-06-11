"""
Unit tests for Flask API endpoints in app.py.

Uses pytest style with the Flask test client. External dependencies
(LLM calls, skills.author_style internals) are mocked. Temporary
directories are used for any file-system operations.
"""

import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app_instance(tmp_path):
    """Create a Flask app instance with patched file paths for isolation."""
    # Patch ENV_FILE and MODELS_FILE before importing the app module so that
    # tests never touch the real project files.
    import app as app_module

    env_file = tmp_path / ".env"
    models_file = tmp_path / "models.json"
    authors_dir = tmp_path / "authors"
    authors_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(app_module, "ENV_FILE", env_file), \
         patch.object(app_module, "MODELS_FILE", models_file):
        app_module.app.config["TESTING"] = True
        yield app_module.app


@pytest.fixture
def client(app_instance):
    """Flask test client."""
    return app_instance.test_client()


# ---------------------------------------------------------------------------
# GET /api/authors
# ---------------------------------------------------------------------------

class TestApiListAuthors:
    """Tests for GET /api/authors."""

    def test_returns_ok_with_list(self, client):
        """Returns a JSON response with ok=True and a data list."""
        mock_authors = [
            {"name": "test_author", "has_style_guide": True,
             "has_vector_store": False, "txt_files": 3, "epub_files": 0},
        ]
        with patch("app.list_authors", return_value=mock_authors):
            resp = client.get("/api/authors")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1
        assert body["data"][0]["name"] == "test_author"

    def test_returns_empty_list_when_no_authors(self, client):
        """Returns an empty data list when there are no authors."""
        with patch("app.list_authors", return_value=[]):
            resp = client.get("/api/authors")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert body["data"] == []

    def test_returns_500_on_exception(self, client):
        """Returns a 500 error when list_authors raises."""
        with patch("app.list_authors", side_effect=RuntimeError("boom")):
            resp = client.get("/api/authors")
        assert resp.status_code == 500
        body = resp.get_json()
        assert body["ok"] is False
        assert "boom" in body["error"]


# ---------------------------------------------------------------------------
# DELETE /api/author/<name>
# ---------------------------------------------------------------------------

class TestApiDeleteAuthor:
    """Tests for DELETE /api/author/<name>."""

    def test_requires_explicit_confirmation(self, client):
        resp = client.delete("/api/author/test_author")
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        assert "confirm=true" in body["error"]

    def test_deletes_after_confirmation(self, client):
        with patch("app.delete_author", return_value=True) as mocked:
            resp = client.delete(
                "/api/author/test_author",
                data=json.dumps({"confirm": True}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mocked.assert_called_once_with("test_author", confirm=False)

    def test_returns_400_when_delete_limit_exceeded(self, client):
        with patch("app.delete_author", side_effect=ValueError("超过单次最多删除 3 个文件")):
            resp = client.delete(
                "/api/author/test_author",
                data=json.dumps({"confirm": True}),
                content_type="application/json",
            )
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


class TestApiWrite:
    """Tests for POST /api/write."""

    def test_returns_review_result(self, client):
        class FakeSkill:
            last_plagiarism_result = {"passed": True}
            last_review_result = {"decision": "pass", "score": 90}

            def __init__(self, author, **kwargs):
                self.author = author

            def write(self, **kwargs):
                return "generated article"

        with patch("app.AuthorStyleSkill", FakeSkill), \
             patch("app._save_article", return_value="authors/test/output/article.txt"), \
             patch("app._save_review", return_value="authors/test/output/article.review.json"), \
             patch("app._save_version_metadata", return_value="authors/test/output/article.version.json"), \
             patch("app._load_json_file", return_value={"version_id": "article", "kind": "draft"}):
            resp = client.post(
                "/api/write",
                data=json.dumps({"author": "test", "topic": "topic"}),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["article"] == "generated article"
        assert data["review"] == {"decision": "pass", "score": 90}
        assert data["review_path"] == "authors/test/output/article.review.json"
        assert data["version_path"] == "authors/test/output/article.version.json"
        assert data["version"] == {"version_id": "article", "kind": "draft"}

    def test_rewrite_returns_updated_review_result(self, client):
        class FakeSkill:
            last_plagiarism_result = {"passed": True}
            last_review_result = {"decision": "warn", "score": 78}

            def __init__(self, author, **kwargs):
                self.author = author

            def rewrite(self, **kwargs):
                assert kwargs["original_article"] == "old article"
                assert kwargs["review"] == {"decision": "warn"}
                return "rewritten article"

        with patch("app.AuthorStyleSkill", FakeSkill), \
             patch("app._save_article", return_value="authors/test/output/topic_rewrite.txt"), \
             patch("app._save_review", return_value="authors/test/output/topic_rewrite.review.json"), \
             patch("app._save_version_metadata", return_value="authors/test/output/topic_rewrite.version.json") as save_version, \
             patch("app._load_json_file", return_value={"version_id": "topic_rewrite", "kind": "rewrite"}):
            resp = client.post(
                "/api/rewrite",
                data=json.dumps({
                    "author": "test",
                    "topic": "topic",
                    "article": "old article",
                    "review": {"decision": "warn"},
                    "parent_version": "topic",
                }),
                content_type="application/json",
            )

        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["article"] == "rewritten article"
        assert data["review"] == {"decision": "warn", "score": 78}
        assert data["review_path"] == "authors/test/output/topic_rewrite.review.json"
        assert data["version_path"] == "authors/test/output/topic_rewrite.version.json"
        assert data["version"] == {"version_id": "topic_rewrite", "kind": "rewrite"}
        assert save_version.call_args.kwargs["parent_version"] == "topic"

    def test_rewrite_rejects_missing_article(self, client):
        resp = client.post(
            "/api/rewrite",
            data=json.dumps({"author": "test", "topic": "topic", "review": {}}),
            content_type="application/json",
        )

        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False


# ---------------------------------------------------------------------------
# GET /api/config
# ---------------------------------------------------------------------------

class TestApiGetConfig:
    """Tests for GET /api/config."""

    def test_returns_config_with_masked_keys(self, client, app_instance):
        """Config endpoint returns expected fields with masked API keys."""
        mock_env = {
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "deepseek-chat",
            "LLM_BASE_URL": "https://api.example.com/v1",
            "LLM_API_KEY": "sk-1234567890abcdef",
            "EMBEDDING_PROVIDER": "dashscope",
            "EMBEDDING_MODEL": "text-embedding-v3",
            "EMBEDDING_API_KEY": "dash-key-abcdef1234",
        }
        with patch("app._read_env_file", return_value=mock_env):
            resp = client.get("/api/config")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        data = body["data"]
        assert data["LLM_PROVIDER"] == "openai"
        assert data["LLM_MODEL"] == "deepseek-chat"
        assert data["LLM_API_KEY_SET"] is True
        # API keys should be masked, not raw
        assert data["LLM_API_KEY"] != "sk-1234567890abcdef"
        assert "*" in data["LLM_API_KEY"]
        assert data["EMBEDDING_API_KEY_SET"] is True
        assert "*" in data["EMBEDDING_API_KEY"]

    def test_returns_empty_strings_when_env_missing(self, client):
        """Config fields default to empty strings when env is empty."""
        with patch("app._read_env_file", return_value={}):
            resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.get_json()["data"]
        assert data["LLM_PROVIDER"] == ""
        assert data["LLM_MODEL"] == ""
        assert data["LLM_API_KEY_SET"] is False


# ---------------------------------------------------------------------------
# GET /api/models
# ---------------------------------------------------------------------------

class TestApiGetModels:
    """Tests for GET /api/models."""

    def test_returns_models_list(self, client, app_instance):
        """Returns the models list from the JSON file."""
        mock_models = [
            {"provider": "openai", "name": "deepseek-chat",
             "base_url": "https://api.deepseek.com/v1", "label": "DeepSeek"},
        ]
        with patch("app._load_models", return_value=mock_models):
            resp = client.get("/api/models")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 1
        assert body["data"][0]["name"] == "deepseek-chat"

    def test_returns_default_models_when_file_missing(self, client, app_instance):
        """When the models file does not exist, default models are returned."""
        # The fixture already patched MODELS_FILE to a temp path that does not
        # exist, so _load_models will return the built-in default list.
        resp = client.get("/api/models")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert isinstance(body["data"], list)
        # The default list has at least 3 models
        assert len(body["data"]) >= 3


# ---------------------------------------------------------------------------
# POST /api/models
# ---------------------------------------------------------------------------

class TestApiSaveModels:
    """Tests for POST /api/models."""

    def test_saves_models_successfully(self, client, app_instance):
        """Posting a models list saves it and returns ok=True."""
        import app as app_module
        models_file = app_module.MODELS_FILE

        new_models = [
            {"provider": "openai", "name": "gpt-4o", "base_url": "", "label": "OpenAI"},
        ]
        resp = client.post(
            "/api/models",
            data=json.dumps({"models": new_models}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True

        # Verify the file was actually written
        saved = json.loads(models_file.read_text(encoding="utf-8"))
        assert saved == new_models

    def test_saves_empty_list(self, client, app_instance):
        """Posting an empty list is valid."""
        import app as app_module
        models_file = app_module.MODELS_FILE

        resp = client.post(
            "/api/models",
            data=json.dumps({"models": []}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

        saved = json.loads(models_file.read_text(encoding="utf-8"))
        assert saved == []

    def test_rejects_invalid_models_payload(self, client):
        resp = client.post(
            "/api/models",
            data=json.dumps({"models": {"name": "not-a-list"}}),
            content_type="application/json",
        )

        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_normalizes_model_provider_and_label(self, client, app_instance):
        import app as app_module
        models_file = app_module.MODELS_FILE

        resp = client.post(
            "/api/models",
            data=json.dumps({
                "models": [
                    {"provider": " OpenAI ", "name": " custom-model ", "base_url": "", "label": ""},
                ],
            }),
            content_type="application/json",
        )

        assert resp.status_code == 200
        saved = json.loads(models_file.read_text(encoding="utf-8"))
        assert saved == [{
            "provider": "openai",
            "name": "custom-model",
            "base_url": "",
            "label": "custom-model",
        }]


class TestSaveArticle:
    """Tests for generated article persistence helpers."""

    def test_save_article_uses_unique_filename(self, tmp_path):
        import app as app_module

        with patch.object(app_module, "project_root", tmp_path):
            first = app_module._save_article("author", "same topic", "first")
            second = app_module._save_article("author", "same topic", "second")

        assert first.name == "same topic.txt"
        assert second.name == "same topic_2.txt"
        assert first.read_text(encoding="utf-8") == "first"
        assert second.read_text(encoding="utf-8") == "second"

    def test_save_review_writes_json_next_to_article(self, tmp_path):
        import app as app_module

        article_path = tmp_path / "article.txt"
        article_path.write_text("content", encoding="utf-8")

        review_path = app_module._save_review(article_path, {"decision": "pass", "score": 91})

        assert review_path == tmp_path / "article.review.json"
        saved = json.loads(review_path.read_text(encoding="utf-8"))
        assert saved == {"decision": "pass", "score": 91}

    def test_save_review_skips_empty_review(self, tmp_path):
        import app as app_module

        assert app_module._save_review(tmp_path / "article.txt", None) is None

    def test_save_version_metadata_writes_chain_summary(self, tmp_path):
        import app as app_module

        article_path = tmp_path / "article_2.txt"
        article_path.write_text("content", encoding="utf-8")
        review = {
            "decision": "warn",
            "passed": False,
            "score": 76,
            "requirement": {"score": 82},
            "style": {"score": 70},
            "plagiarism": {"score": 88, "risk": "low"},
            "suggestions": ["revise"],
        }
        plagiarism = {"passed": True, "max_common": 5, "similar_docs": []}

        version_path = app_module._save_version_metadata(
            article_path,
            kind="rewrite",
            author="author",
            topic="topic",
            article="content",
            review=review,
            plagiarism=plagiarism,
            parent_version="article",
            request_options={"tone": "default"},
        )

        saved = json.loads(version_path.read_text(encoding="utf-8"))
        assert version_path == tmp_path / "article_2.version.json"
        assert saved["kind"] == "rewrite"
        assert saved["parent_version"] == "article"
        assert saved["review_summary"]["decision"] == "warn"
        assert saved["review_summary"]["style_score"] == 70
        assert saved["plagiarism_summary"]["max_common"] == 5
        assert saved["request"] == {"tone": "default"}


# ---------------------------------------------------------------------------
# POST /api/download
# ---------------------------------------------------------------------------

class TestApiDownload:
    """Tests for POST /api/download."""

    def test_returns_file_with_content(self, client):
        """Download endpoint returns a text file with the correct content."""
        payload = {
            "content": "这是一篇测试文章的内容。",
            "filename": "test_article.txt",
        }
        resp = client.post(
            "/api/download",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/plain")
        # Check Content-Disposition header for filename
        disposition = resp.headers.get("Content-Disposition", "")
        assert "test_article.txt" in disposition
        # Body should contain the UTF-8 encoded content
        body_text = resp.data.decode("utf-8")
        assert "这是一篇测试文章的内容" in body_text

    def test_download_default_filename(self, client):
        """When no filename is provided, defaults to article.txt."""
        payload = {"content": "hello"}
        resp = client.post(
            "/api/download",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        disposition = resp.headers.get("Content-Disposition", "")
        assert "article.txt" in disposition

    def test_download_empty_content(self, client):
        """Empty content still returns a valid file response."""
        payload = {"content": "", "filename": "empty.txt"}
        resp = client.post(
            "/api/download",
            data=json.dumps(payload),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.data == b""
