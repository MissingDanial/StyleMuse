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
