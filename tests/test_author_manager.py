"""
Tests for author workspace management.
"""

import json
from unittest.mock import patch

from skills.author_style.author_manager import create_author


def test_create_author_preserves_existing_config(tmp_path):
    authors_dir = tmp_path / "authors"
    author_dir = authors_dir / "test_author"
    author_dir.mkdir(parents=True)
    config_file = author_dir / "config.json"
    config_file.write_text(
        json.dumps({
            "name": "test_author",
            "llm_model": "custom-model",
            "temperature": 0.42,
            "tone_options": {"default": "custom tone"},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    with patch("skills.author_style.config.AUTHORS_DIR", authors_dir):
        create_author("test_author", analyze=False, build_index=False)

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["name"] == "test_author"
    assert saved["llm_model"] == "custom-model"
    assert saved["temperature"] == 0.42
    assert saved["tone_options"] == {"default": "custom tone"}


def test_create_author_merges_extra_config_over_existing_config(tmp_path):
    authors_dir = tmp_path / "authors"
    author_dir = authors_dir / "test_author"
    author_dir.mkdir(parents=True)
    config_file = author_dir / "config.json"
    config_file.write_text(
        json.dumps({"name": "test_author", "temperature": 0.42}, ensure_ascii=False),
        encoding="utf-8",
    )

    with patch("skills.author_style.config.AUTHORS_DIR", authors_dir):
        create_author(
            "test_author",
            analyze=False,
            build_index=False,
            extra_config={"temperature": 0.9, "chunk_size": 80},
        )

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["temperature"] == 0.9
    assert saved["chunk_size"] == 80
