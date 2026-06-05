"""
Unit tests for utility functions in app.py and skills/author_style/analyzer.py.

Uses pytest style. Mocks external dependencies and uses temp directories
for file operations.
"""

from unittest.mock import patch, MagicMock

import pytest
from langchain_core.documents import Document

from skills.author_style.rag_chain import extract_text_from_response
from skills.author_style.analyzer import (
    extract_basic_stats,
    collect_sample_texts,
    parse_analysis_result,
)


# ---------------------------------------------------------------------------
# Tests for extract_text_from_response
# ---------------------------------------------------------------------------

class TestExtractTextFromResponse:
    """Tests for rag_chain.extract_text_from_response."""

    def test_string_input(self):
        """A plain string is returned as-is."""
        result = extract_text_from_response("hello world")
        assert result == "hello world"

    def test_list_with_text_blocks(self):
        """A list of objects with .type == 'text' and .text attribute."""
        block1 = MagicMock()
        block1.type = "text"
        block1.text = "first part"

        block2 = MagicMock()
        block2.type = "text"
        block2.text = "second part"

        result = extract_text_from_response([block1, block2])
        assert result == "first part\nsecond part"

    def test_list_with_dict_blocks(self):
        """A list of dicts with type == 'text' and text key."""
        blocks = [
            {"type": "text", "text": "alpha"},
            {"type": "text", "text": "beta"},
        ]
        result = extract_text_from_response(blocks)
        assert result == "alpha\nbeta"

    def test_list_mixed_blocks(self):
        """A list mixing MagicMock objects and dicts."""
        obj_block = MagicMock()
        obj_block.type = "text"
        obj_block.text = "from_obj"

        dict_block = {"type": "text", "text": "from_dict"}

        result = extract_text_from_response([obj_block, dict_block])
        assert result == "from_obj\nfrom_dict"

    def test_list_skips_non_text_blocks(self):
        """Non-text blocks are skipped."""
        obj_block = MagicMock()
        obj_block.type = "image"
        obj_block.text = "should_skip"

        dict_block = {"type": "text", "text": "kept"}

        result = extract_text_from_response([obj_block, dict_block])
        assert result == "kept"

    def test_empty_list_returns_empty_string(self):
        """An empty list produces an empty string."""
        result = extract_text_from_response([])
        assert result == ""

    def test_unknown_type_falls_back_to_str(self):
        """A non-str, non-list value is converted via str()."""
        result = extract_text_from_response(12345)
        assert result == "12345"

    def test_none_falls_back_to_str(self):
        """None is converted to the string 'None'."""
        result = extract_text_from_response(None)
        assert result == "None"


# ---------------------------------------------------------------------------
# Tests for _read_env_file and _write_env_file roundtrip
# ---------------------------------------------------------------------------

class TestEnvFileRoundtrip:
    """Tests for app._read_env_file and app._write_env_file."""

    def _import_app_helpers(self, env_file_path):
        """Import app helpers with ENV_FILE patched to a temp path."""
        import importlib
        import app as app_module
        # Patch the module-level ENV_FILE constant
        with patch.object(app_module, "ENV_FILE", env_file_path):
            importlib.reload(app_module)
            return app_module

    def test_read_nonexistent_file_returns_empty_dict(self, tmp_path):
        """Reading a non-existent .env file returns an empty dict."""
        import app as app_module
        fake_path = tmp_path / ".env"
        with patch.object(app_module, "ENV_FILE", fake_path):
            result = app_module._read_env_file()
            assert result == {}

    def test_write_then_read_roundtrip(self, tmp_path):
        """Writing a dict and reading it back produces the same key-value pairs."""
        import app as app_module
        fake_path = tmp_path / ".env"
        with patch.object(app_module, "ENV_FILE", fake_path):
            data = {"KEY_A": "value_a", "KEY_B": "value_b"}
            app_module._write_env_file(data)
            result = app_module._read_env_file()
            assert result["KEY_A"] == "value_a"
            assert result["KEY_B"] == "value_b"

    def test_read_preserves_comments_and_blanks(self, tmp_path):
        """Comments and blank lines are ignored when reading."""
        import app as app_module
        fake_path = tmp_path / ".env"
        fake_path.write_text(
            "# this is a comment\n\nKEY=val\n# another comment\nOTHER=123\n",
            encoding="utf-8",
        )
        with patch.object(app_module, "ENV_FILE", fake_path):
            result = app_module._read_env_file()
            assert result == {"KEY": "val", "OTHER": "123"}

    def test_write_preserves_order_and_comments(self, tmp_path):
        """_write_env_file preserves comments and existing ordering."""
        import app as app_module
        fake_path = tmp_path / ".env"
        original = "# header\nOLD_KEY=old_val\nANOTHER=yes\n"
        fake_path.write_text(original, encoding="utf-8")

        with patch.object(app_module, "ENV_FILE", fake_path):
            # Update one key and add a new one
            app_module._write_env_file({"OLD_KEY": "new_val", "NEW_KEY": "brand_new", "ANOTHER": "yes"})

            content = fake_path.read_text(encoding="utf-8")
            assert "# header" in content
            assert "OLD_KEY=new_val" in content
            assert "ANOTHER=yes" in content
            assert "NEW_KEY=brand_new" in content


# ---------------------------------------------------------------------------
# Tests for _mask_key
# ---------------------------------------------------------------------------

class TestMaskKey:
    """Tests for app._mask_key."""

    def test_empty_string_returns_empty(self):
        import app as app_module
        assert app_module._mask_key("") == ""

    def test_short_key_returns_four_stars(self):
        """Keys shorter than 8 chars return '****'."""
        import app as app_module
        assert app_module._mask_key("abc") == "****"
        assert app_module._mask_key("1234567") == "****"  # exactly 7

    def test_exactly_eight_chars(self):
        """An 8-char key shows first 4 and last 4 with nothing masked."""
        import app as app_module
        result = app_module._mask_key("abcdefgh")
        assert result == "abcdefgh"

    def test_long_key_masks_middle(self):
        """A long key shows first 4 and last 4 chars with '*' in between."""
        import app as app_module
        key = "sk-1234567890abcdef"
        result = app_module._mask_key(key)
        assert result.startswith("sk-1")
        assert result.endswith("cdef")
        assert "*" in result
        assert len(result) == len(key)

    def test_key_with_special_characters(self):
        """Masking works with keys containing dashes and underscores."""
        import app as app_module
        key = "ab-cd_ef-ghij"
        result = app_module._mask_key(key)
        assert result.startswith("ab-c")
        assert result.endswith("ghij")
        assert "*" in result
        assert len(result) == len(key)


# ---------------------------------------------------------------------------
# Tests for parse_analysis_result
# ---------------------------------------------------------------------------

class TestParseAnalysisResult:
    """Tests for analyzer.parse_analysis_result."""

    def test_valid_two_part_response(self):
        """Correctly splits when both separators are present."""
        response = (
            "===STYLE_GUIDE===\n"
            "# Style Guide Content\n"
            "Some description.\n"
            "===FEW_SHOT===\n"
            "## Examples\n"
            "Example paragraph one."
        )
        style_guide, few_shot = parse_analysis_result(response)
        assert "Style Guide Content" in style_guide
        assert "===STYLE_GUIDE===" not in style_guide
        assert "Examples" in few_shot
        assert "===FEW_SHOT===" not in few_shot

    def test_missing_few_shot_separator(self):
        """When ===FEW_SHOT=== is absent, everything goes to style_guide."""
        response = "Just some plain text without the separator."
        style_guide, few_shot = parse_analysis_result(response)
        assert style_guide == response.strip()
        assert few_shot == ""

    def test_empty_response(self):
        """Empty input yields empty outputs."""
        style_guide, few_shot = parse_analysis_result("")
        assert style_guide == ""
        assert few_shot == ""

    def test_extra_whitespace_stripped(self):
        """Leading/trailing whitespace is stripped from both parts."""
        response = "   ===STYLE_GUIDE===\n  guide text  \n===FEW_SHOT===\n  shot text  "
        style_guide, few_shot = parse_analysis_result(response)
        assert style_guide == "guide text"
        assert few_shot == "shot text"


# ---------------------------------------------------------------------------
# Tests for collect_sample_texts
# ---------------------------------------------------------------------------

class TestCollectSampleTexts:
    """Tests for analyzer.collect_sample_texts."""

    def _make_docs(self, texts):
        """Helper to create Document objects from a list of strings."""
        return [Document(page_content=t, metadata={}) for t in texts]

    def test_returns_samples_from_documents(self):
        """Samples are extracted from provided documents."""
        docs = self._make_docs([
            "A" * 200,
            "B" * 300,
            "C" * 100,
        ])
        samples = collect_sample_texts(docs, max_samples=5, sample_len=100)
        assert len(samples) > 0
        assert len(samples) <= 5

    def test_short_texts_excluded(self):
        """Documents shorter than 50 chars are excluded."""
        docs = self._make_docs(["short", "tiny", "A" * 200])
        samples = collect_sample_texts(docs, max_samples=10, sample_len=50)
        # Only the 200-char doc should contribute
        for s in samples:
            assert "A" in s

    def test_empty_documents_returns_empty(self):
        """An empty document list returns an empty sample list."""
        samples = collect_sample_texts([], max_samples=5)
        assert samples == []

    def test_all_short_documents_returns_empty(self):
        """If all documents are too short, returns empty."""
        docs = self._make_docs(["ab", "cd", "ef"])
        samples = collect_sample_texts(docs, max_samples=5)
        assert samples == []

    def test_sample_len_respected(self):
        """Each sample is at most sample_len characters long."""
        docs = self._make_docs(["x" * 1000])
        samples = collect_sample_texts(docs, max_samples=3, sample_len=200)
        for s in samples:
            assert len(s) <= 200

    def test_max_samples_limit(self):
        """The number of samples never exceeds max_samples."""
        docs = self._make_docs(["word " * 200] * 50)
        samples = collect_sample_texts(docs, max_samples=3, sample_len=100)
        assert len(samples) <= 3


# ---------------------------------------------------------------------------
# Tests for extract_basic_stats
# ---------------------------------------------------------------------------

class TestExtractBasicStats:
    """Tests for analyzer.extract_basic_stats."""

    def test_returns_expected_keys(self):
        """The result dict contains all expected keys."""
        texts = ["这是第一段文字。这是第二段！还有第三段？"]
        stats = extract_basic_stats(texts)

        expected_keys = {
            "total_chars",
            "sample_count",
            "sentence_count",
            "avg_sentence_len",
            "avg_paragraph_len",
            "short_sentence_ratio",
            "long_sentence_ratio",
            "top_chars",
            "punctuation",
        }
        assert expected_keys == set(stats.keys())

    def test_sample_count_matches_input(self):
        """sample_count equals the number of input texts."""
        texts = ["文本一。", "文本二！"]
        stats = extract_basic_stats(texts)
        assert stats["sample_count"] == 2

    def test_total_chars_positive(self):
        """total_chars is a positive integer for non-empty Chinese text."""
        texts = ["我喜欢编程，它让我感到快乐。"]
        stats = extract_basic_stats(texts)
        assert stats["total_chars"] > 0

    def test_punctuation_counts(self):
        """Punctuation counts are correct for known input."""
        texts = ["你好！你好？你好。"]
        stats = extract_basic_stats(texts)
        assert stats["punctuation"]["感叹号（！）"] == 1
        assert stats["punctuation"]["问号（？）"] == 1
        assert stats["punctuation"]["句号（。）"] == 1

    def test_empty_input_no_crash(self):
        """Empty texts list does not cause a crash."""
        stats = extract_basic_stats([])
        assert stats["total_chars"] == 0
        assert stats["sentence_count"] == 0
        assert stats["sample_count"] == 0

    def test_top_chars_is_sorted_list(self):
        """top_chars is a list of (char, count) tuples sorted by frequency."""
        texts = ["啊" * 10 + "吧" * 5 + "次" * 2]
        stats = extract_basic_stats(texts)
        top = stats["top_chars"]
        assert isinstance(top, list)
        assert len(top) > 0
        # Frequencies should be non-increasing
        counts = [c for _, c in top]
        assert counts == sorted(counts, reverse=True)

    def test_ratio_values_between_zero_and_one(self):
        """Sentence ratios are between 0 and 1."""
        texts = ["短句。这是一个比较长的句子，包含了很多内容和描述。" * 3]
        stats = extract_basic_stats(texts)
        assert 0.0 <= stats["short_sentence_ratio"] <= 1.0
        assert 0.0 <= stats["long_sentence_ratio"] <= 1.0
