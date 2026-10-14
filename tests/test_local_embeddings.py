from threading import RLock
from unittest.mock import patch

import numpy as np
import pytest

from skills.author_style.config import get_embeddings
from skills.author_style.local_embeddings import LocalEmbeddings, QUERY_INSTRUCTION
from skills.author_style.vector_cache import vector_manifest


def test_local_provider_does_not_require_key_or_load_model():
    with patch("skills.author_style.local_embeddings._load_engine") as load:
        embedding = get_embeddings({"embedding_provider": "local"})
    assert isinstance(embedding, LocalEmbeddings)
    assert embedding.local_files_only
    load.assert_not_called()


def test_query_prefix_is_not_added_to_documents_and_vectors_are_normalized(tmp_path):
    engine = type("Engine", (), {"embed": lambda self, texts, **kwargs: [np.array([3., 4.]) for _ in texts]})()
    with patch("skills.author_style.local_embeddings._load_engine", return_value=(engine, RLock())), \
         patch.object(engine, "embed", wraps=engine.embed) as encode:
        embedding = LocalEmbeddings(cache_dir=tmp_path)
        assert np.allclose(embedding.embed_documents(["原文"]), [[0.6, 0.8]])
        assert encode.call_args.args[0] == ["原文"]
        embedding.embed_query("狗")
        assert encode.call_args.args[0] == [QUERY_INSTRUCTION + "狗"]


@pytest.mark.parametrize("vector", [[0., 0.], [float("nan"), 1.], [float("inf"), 1.]])
def test_invalid_model_vectors_rejected(tmp_path, vector):
    engine = type("Engine", (), {"embed": lambda self, texts, **kwargs: [np.array(vector)]})()
    with patch("skills.author_style.local_embeddings._load_engine", return_value=(engine, RLock())):
        with pytest.raises(ValueError, match="向量"):
            LocalEmbeddings(cache_dir=tmp_path).embed_documents(["text"])


def test_empty_batch_does_not_load_weights(tmp_path):
    with patch("skills.author_style.local_embeddings._load_engine") as load:
        assert LocalEmbeddings(cache_dir=tmp_path).embed_documents([]) == []
    load.assert_not_called()


def test_changing_query_embedding_contract_invalidates_vector_manifest():
    cfg = {"embedding_provider": "local", "embedding_model": "bge", "embedding_query_instruction": "old"}
    assert vector_manifest([], cfg) != vector_manifest([], {**cfg, "embedding_query_instruction": "new"})


def test_deepseek_embedding_configuration_rejected_before_network_call():
    with patch("langchain_openai.OpenAIEmbeddings") as client:
        with pytest.raises(ValueError, match="DeepSeek"):
            get_embeddings({"embedding_provider": "openai", "embedding_api_key": "test", "embedding_base_url": "https://api.deepseek.com/v1"})
    client.assert_not_called()


def test_local_provider_can_be_saved_and_read_through_config_api(tmp_path):
    import app
    path = tmp_path / ".env"
    with patch.object(app, "ENV_FILE", path), patch.dict(app.app.config, {"ACCESS_TOKEN": ""}):
        client = app.app.test_client()
        response = client.post("/api/config", json={"EMBEDDING_PROVIDER": "local", "EMBEDDING_MODEL": "BAAI/bge-small-zh-v1.5", "EMBEDDING_BASE_URL": "__CLEAR__"})
        assert response.status_code == 200
        config = client.get("/api/config").get_json()["data"]
        assert config["EMBEDDING_PROVIDER"] == "local" and config["EMBEDDING_BASE_URL"] == ""
        assert config["EMBEDDING_API_KEY_SET"] is False
