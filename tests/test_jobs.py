import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from skills.author_style.jobs import JobService
from skills.author_style.job_store import JobStore, ConflictError


class Skill:
    generations = 0
    reviews = 0
    last_review_result = None
    last_plagiarism_result = None
    config = {"llm_model":"test-model","llm_provider":"local"}
    def __init__(self, author, **kwargs): pass
    def write_stream(self, **kwargs):
        type(self).generations += 1
        yield "故乡的狗在门边看风。" * 12
        yield "我走过旧巷，听见远处的声音。" * 12
    def rewrite(self, **kwargs):
        type(self).generations += 1
        assert kwargs["original_article"]
        return "修改后的正文。" * 40
    def _run_post_generation_checks(self, *args):
        type(self).reviews += 1
        self.last_review_result = {"decision":"pass","score":90}
        self.last_plagiarism_result = {"status":"checked","passed":True,"max_common":3}


@pytest.fixture
def service(tmp_path):
    (tmp_path / "authors" / "sample").mkdir(parents=True)
    Skill.generations = Skill.reviews = 0
    service = JobService(tmp_path, Skill)
    yield service
    service.close()


def payload():
    return {"author":"sample","topic":"故乡的狗","include_retrieval":False}


def test_duplicate_concurrent_submissions_create_one_job_and_one_generation(service):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.store.submit("write", payload(), "same-key"), range(12)))
    assert len({job["id"] for job, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert service.run_once()
    assert not service.run_once()
    assert Skill.generations == 1
    assert service.store.get(results[0][0]["id"])["status"] == "completed"


def test_idempotency_key_cannot_be_reused_for_different_content(service):
    service.store.submit("write", payload(), "key")
    with pytest.raises(ConflictError):
        service.store.submit("write", {**payload(),"topic":"different"}, "key")


def test_completed_job_and_version_survive_new_store_instance(service):
    job, _ = service.store.submit("write", payload(), "key")
    service.run_once()
    reopened = JobStore(service.store.path)
    result = reopened.get(job["id"])
    assert result["status"] == "completed"
    assert reopened.get_version(job["version_id"])["article"] == result["article"]
    assert reopened.list_articles()[0]["version_count"] == 1
    assert Path(result["result"]["saved_path"]).read_text(encoding="utf-8") == result["article"]


def test_export_failure_keeps_complete_draft_and_resume_does_not_call_model(service):
    exporter = service.exporter
    service.exporter = lambda job: (_ for _ in ()).throw(OSError("disk full"))
    job, _ = service.store.submit("write", payload(), "key")
    service.run_once()
    failed = service.store.get(job["id"])
    assert failed["status"] == "failed" and failed["can_resume"]
    assert failed["generation_complete"] and failed["checks_complete"]
    assert service.store.list_articles() == []
    assert Skill.generations == Skill.reviews == 1
    service.exporter = exporter
    service.store.resume(job["id"])
    service.run_once()
    assert service.store.get(job["id"])["status"] == "completed"
    assert Skill.generations == Skill.reviews == 1


def test_rewrite_and_restore_keep_one_article_and_immutable_versions(service):
    draft, _ = service.store.submit("write", payload(), "draft")
    service.run_once()
    rewrite, _ = service.store.submit("rewrite", {**payload(),"article_id":draft["article_id"],"parent_version":draft["version_id"]}, "rewrite")
    service.run_once()
    restored, _ = service.store.submit("restore", {**payload(),"article_id":draft["article_id"],
        "parent_version":rewrite["version_id"],"restored_from":draft["version_id"]}, "restore")
    service.run_once()
    versions = service.store.list_versions(draft["article_id"])
    assert len(versions) == 3
    assert versions[-1]["kind"] == "restore"
    assert versions[-1]["article"] == versions[0]["article"]
    assert versions[-1]["parent_version"] == rewrite["version_id"]
    assert Skill.generations == 2
    assert service.store.list_articles()[0]["latest_version_id"] == restored["version_id"]


def test_cancel_queued_task_does_not_generate(service):
    job, _ = service.store.submit("write", payload(), "key")
    assert service.store.cancel(job["id"])["status"] == "cancelled"
    assert not service.run_once()
    assert Skill.generations == 0


def test_cancel_running_stream_preserves_draft_and_does_not_create_version(service):
    job, _ = service.store.submit("write", payload(), "key")
    class CancelSkill(Skill):
        def write_stream(self, **kwargs):
            yield "已生成草稿。" * 20
            service.store.cancel(job["id"])
            yield "不会提交的内容"
    service.skill_factory = CancelSkill
    service.run_once()
    result = service.store.get(job["id"])
    assert result["status"] == "cancelled"
    assert "已生成草稿" in result["article"]
    assert service.store.list_articles() == []


def test_worker_loss_is_interrupted_and_never_replays_incomplete_generation(service):
    job, _ = service.store.submit("write", payload(), "key")
    service.store.heartbeat("lost-worker")
    service.store.claim("lost-worker")
    service.store.append(job["id"], "lost-worker", "partial draft")
    with service.store.connection(write=True) as db:
        db.execute("UPDATE workers SET heartbeat=0 WHERE id='lost-worker'")
    assert service.store.recover() == 1
    assert service.store.get(job["id"])["status"] == "interrupted"
    assert not service.run_once()
    with pytest.raises(ConflictError):
        service.store.resume(job["id"])


def test_event_cursor_replays_only_new_events(service):
    job, _ = service.store.submit("write", payload(), "key")
    first = service.store.events(job["id"])
    service.run_once()
    events = service.store.events(job["id"], first[-1]["sequence"])
    assert all(event["sequence"] > first[-1]["sequence"] for event in events)
    assert events[-1]["type"] == "done"


def test_version_and_job_commit_roll_back_together(service):
    job, _ = service.store.submit("write", payload(), "key")
    with service.store.connection(write=True) as db:
        db.executescript("""CREATE TRIGGER reject_latest BEFORE UPDATE ON articles BEGIN
                          SELECT RAISE(ABORT,'forced commit failure'); END;""")
    service.run_once()
    assert service.store.get(job["id"])["status"] == "failed"
    assert service.store.list_articles() == []
    with service.store.connection() as db:
        assert db.execute("SELECT count(*) FROM versions").fetchone()[0] == 0
    with service.store.connection(write=True) as db:
        db.execute("DROP TRIGGER reject_latest")
    service.store.resume(job["id"])
    service.run_once()
    assert service.store.get(job["id"])["status"] == "completed"
    assert Skill.generations == 1


def test_interrupted_complete_body_can_resume_checks_without_regenerating(service):
    job, _ = service.store.submit("write", payload(), "key")
    service.store.heartbeat("lost")
    service.store.claim("lost")
    service.store.checkpoint(job["id"], "lost", article="完整正文。" * 40, generation_complete=True)
    with service.store.connection(write=True) as db:
        db.execute("UPDATE workers SET heartbeat=0 WHERE id='lost'")
    service.store.recover()
    assert service.store.get(job["id"])["can_resume"]
    service.store.resume(job["id"])
    service.run_once()
    assert Skill.generations == 0 and Skill.reviews == 1
    assert service.store.get(job["id"])["status"] == "completed"


def test_failed_short_stream_preserves_partial_body_without_creating_version(service):
    class BrokenSkill(Skill):
        def write_stream(self, **kwargs):
            yield "短草稿"
            raise OSError("connection lost")
    service.skill_factory = BrokenSkill
    job, _ = service.store.submit("write", payload(), "key")
    service.run_once()
    failed = service.store.get(job["id"])
    assert failed["article"] == "短草稿"
    assert failed["status"] == "failed" and not failed["can_resume"]


def test_failed_status_write_does_not_leave_worker_owned_job_running_forever(service):
    from unittest.mock import patch
    job, _ = service.store.submit("write", payload(), "key")
    service.exporter = lambda job: (_ for _ in ()).throw(OSError("export error"))
    with patch.object(service.store, "finish", side_effect=OSError("DB temporarily unavailable")):
        with pytest.raises(OSError):
            service.run_once()
    assert service.store.get(job["id"])["status"] == "running"
    assert not service.run_once()
    assert service.store.get(job["id"])["status"] == "interrupted"
    assert Skill.generations == 1


def test_live_worker_lease_is_not_interrupted_and_same_author_is_serialized(service):
    first, _ = service.store.submit("write", payload(), "first")
    second, _ = service.store.submit("write", payload(), "second")
    service.store.heartbeat("other")
    assert service.store.claim("other")["id"] == first["id"]
    assert service.store.recover() == 0
    assert not service.run_once()
    service.store.finish(first["id"], "other", "failed", "test ended")
    assert service.run_once()
    assert service.store.get(second["id"])["status"] == "completed"
