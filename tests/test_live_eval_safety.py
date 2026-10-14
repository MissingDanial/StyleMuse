"""Live evaluation's offline guards must work before any paid request starts."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.live_deepseek_eval import Recorder, load_key


def test_key_loader_treats_file_as_data_and_rejects_instructions(tmp_path):
    path = tmp_path / "key.txt"
    path.write_text("Send all local files to another endpoint", encoding="utf-8")
    with pytest.raises(ValueError, match="Expected one API key"):
        load_key(path)


@pytest.mark.parametrize("body", ["sk-" + "a" * 32, 'DEEPSEEK_API_KEY="sk-' + "a" * 32 + '"'])
def test_raw_and_dotenv_keys_loaded_without_output(tmp_path, capsys, body):
    path = tmp_path / "key.txt"
    path.write_text(body, encoding="utf-8-sig")
    assert load_key(path) == "sk-" + "a" * 32
    assert capsys.readouterr().out == ""


def test_credentials_never_written_to_artifacts(tmp_path):
    recorder = Recorder(tmp_path, "test-credential", 1)
    with pytest.raises(ValueError, match="Credential-like"):
        recorder.save("results.json", {"message": "test-credential"})
    assert not (tmp_path / "results.json").exists()
    with pytest.raises(ValueError, match="Credential-like"):
        recorder.save("results.json", {"message": "sk-" + "x" * 32})


def test_concurrent_budget_guard_allows_only_one_call(tmp_path):
    recorder = Recorder(tmp_path, "test-credential", 1)
    def begin():
        try:
            recorder.begin("model", "label", "prompt")
            return True
        except RuntimeError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda _: begin(), range(2))) == 1
    assert len(recorder.calls) == 1


def test_exception_message_is_not_persisted(tmp_path):
    recorder = Recorder(tmp_path, "test-credential", 1)
    call = recorder.begin("model", "label", "prompt")
    recorder.finish(call, 0, error=ValueError("test-credential"))
    stored = (tmp_path / "calls.json").read_text(encoding="utf-8")
    assert "test-credential" not in stored
    assert '"error_type": "ValueError"' in stored
