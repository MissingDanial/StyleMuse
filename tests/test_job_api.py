import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import app
from tests.test_jobs import Skill


@pytest.fixture
def api(tmp_path):
    (tmp_path / "authors" / "sample").mkdir(parents=True)
    Skill.generations = Skill.reviews = 0
    with patch.object(app, "project_root", tmp_path), patch.object(app, "AuthorStyleSkill", Skill), \
         patch.dict(app.app.config, {"ACCESS_TOKEN":"", "JOB_WORKER_ENABLED":False, "JOB_DATABASE":None}):
        client = app.app.test_client()
        yield client, app._job_service(), tmp_path


def submit(client, key="key", **options):
    return client.post("/api/jobs", json={"author":"sample", "topic":"故乡的狗", "include_retrieval":False, **options},
                       headers={"Idempotency-Key":key})


def semantic_version(client, service, key="semantic"):
    evidence_id = "a" * 64
    def check(skill, *args):
        skill.last_review_result = {"decision":"warn", "passed":False, "score":90}
        skill.last_plagiarism_result = {"originality_status":"needs_review", "passed":None,
                                       "semantic":{"candidates":[{"evidence_id":evidence_id}]}}
    job = submit(client, key=key).get_json()["data"]
    with patch.object(Skill, "_run_post_generation_checks", check):
        service.run_once()
    return job["version_id"], evidence_id


def test_reuse_review_persists_without_changing_machine_or_article_results(api):
    client, service, _ = api
    version_id, evidence_id = semantic_version(client, service)
    original = client.get(f"/api/versions/{version_id}").get_json()["data"]
    response = client.post(f"/api/versions/{version_id}/reuse-review", json={
        "evidence_id":evidence_id, "decision":"independent", "note":"相同物件，但行动与因果关系不同。"})
    assert response.status_code == 200
    reopened = client.get(f"/api/versions/{version_id}").get_json()["data"]
    assert reopened["reuse_reviews"][0]["decision"] == "independent"
    assert reopened["reuse_review_summary"]["resolved"] == 1
    assert reopened["article"] == original["article"]
    assert reopened["plagiarism"] == original["plagiarism"]
    assert reopened["review"] == original["review"]
    from skills.author_style.job_store import JobStore
    assert JobStore(service.store.path).get_version(version_id)["reuse_reviews"] == reopened["reuse_reviews"]


def test_review_updates_are_auditable_and_evidence_belongs_to_saved_version(api):
    client, service, _ = api
    version_id, evidence_id = semantic_version(client, service)
    url = f"/api/versions/{version_id}/reuse-review"
    assert client.post(url, json={"evidence_id":"forged", "decision":"reuse"}).status_code == 409
    assert client.post(url, json={"evidence_id":evidence_id, "decision":[]}).status_code == 400
    assert client.post(url, json={"evidence_id":evidence_id, "decision":"reuse", "note":"x" * 2001}).status_code == 400
    for decision in ("uncertain", "quotation"):
        assert client.post(url, json={"evidence_id":evidence_id, "decision":decision}).status_code == 200
    with service.store.connection() as db:
        assert db.execute("SELECT count(*) FROM reuse_reviews WHERE version_id=?", (version_id,)).fetchone()[0] == 2
    assert client.get(f"/api/versions/{version_id}").get_json()["data"]["reuse_reviews"][0]["decision"] == "quotation"
    with patch.dict(app.app.config, {"ACCESS_TOKEN":"private"}):
        assert client.post(url, json={"evidence_id":evidence_id, "decision":"reuse"}).status_code == 401


def test_job_submission_is_immediate_and_replay_is_idempotent(api):
    client, service, _ = api
    response = submit(client)
    assert response.status_code == 202
    job = response.get_json()["data"]
    assert job["status"] == "queued" and Skill.generations == 0
    assert submit(client).status_code == 200
    assert client.get("/api/jobs/by-key/key").get_json()["data"]["id"] == job["id"]
    service.run_once()
    result = client.get("/api/jobs/" + job["id"]).get_json()["data"]
    assert result["status"] == "completed" and Skill.generations == 1
    assert len(client.get("/api/articles").get_json()["data"]["items"]) == 1
    assert client.get("/api/versions/" + job["version_id"]).get_json()["data"]["article"] == result["article"]


def test_new_job_api_rejects_bad_payload_key_and_unauthenticated_access(api):
    client, _, _ = api
    assert client.post("/api/jobs", json={"author":"sample","topic":"topic"}).status_code == 400
    assert submit(client, kind=[]).status_code == 400
    assert submit(client, author="missing").status_code == 404
    assert submit(client, max_tokens=-1).status_code == 400
    assert submit(client, article_id="forged").status_code == 400
    with patch.dict(app.app.config, {"ACCESS_TOKEN":"private"}):
        assert client.get("/api/jobs").status_code == 401
        assert submit(client).status_code == 401
        assert client.get("/api/articles").status_code == 401


def test_cancellation_resume_and_delete_guard(api):
    client, service, _ = api
    job = submit(client).get_json()["data"]
    assert client.delete("/api/author/sample", json={"confirm":True}).status_code == 409
    assert client.post(f"/api/jobs/{job['id']}/cancel").get_json()["data"]["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 409
    assert not service.run_once()


def test_api_resume_repairs_save_failure_without_second_generation(api):
    client, service, _ = api
    job = submit(client).get_json()["data"]
    export = service.exporter
    service.exporter = lambda job: (_ for _ in ()).throw(OSError("disk full"))
    service.run_once()
    assert client.get(f"/api/jobs/{job['id']}").get_json()["data"]["can_resume"]
    service.exporter = export
    assert client.post(f"/api/jobs/{job['id']}/resume").status_code == 202
    service.run_once()
    assert Skill.generations == 1
    assert client.get(f"/api/jobs/{job['id']}").get_json()["data"]["status"] == "completed"


def test_history_rewrite_restore_compare_and_parent_ownership(api):
    client, service, _ = api
    first = submit(client).get_json()["data"]
    second = submit(client, "other", topic="另一篇作品").get_json()["data"]
    service.run_once(); service.run_once()
    rewrite = client.post("/api/jobs", json={"kind":"rewrite","version_id":first["version_id"]},
                          headers={"Idempotency-Key":"rewrite"}).get_json()["data"]
    service.run_once()
    article = client.get("/api/articles/" + first["article_id"]).get_json()["data"]
    assert len(article["versions"]) == 2
    comparison = client.get(f"/api/articles/{first['article_id']}/compare?left={first['version_id']}&right={rewrite['version_id']}")
    assert comparison.status_code == 200 and comparison.get_json()["data"]["diff"]
    assert client.get(f"/api/articles/{first['article_id']}/compare?left={first['version_id']}&right={second['version_id']}").status_code == 409
    assert client.post("/api/jobs", json={"kind":"rewrite","version_id":first["version_id"],"author":"other"},
                       headers={"Idempotency-Key":"forged"}).status_code == 409
    restored = client.post("/api/jobs", json={"kind":"restore","version_id":first["version_id"]},
                           headers={"Idempotency-Key":"restore"}).get_json()["data"]
    service.run_once()
    assert client.get("/api/versions/" + restored["version_id"]).get_json()["data"]["restored_from"] == first["version_id"]
    assert Skill.generations == 3
    replay = client.post("/api/jobs", json={"kind":"restore","version_id":first["version_id"]},
                         headers={"Idempotency-Key":"restore"})
    assert replay.status_code == 200
    assert replay.get_json()["data"]["id"] == restored["id"]


def test_events_cursor_and_sse_replay_do_not_generate_again(api):
    client, service, _ = api
    job = submit(client).get_json()["data"]
    initial = client.get(f"/api/jobs/{job['id']}/events").get_json()["data"]
    sequence = initial["events"][-1]["sequence"]
    service.run_once()
    response = client.get(f"/api/jobs/{job['id']}/stream", headers={"Last-Event-ID":str(sequence)})
    text = response.get_data(as_text=True)
    assert "id:" in text and '"type": "done"' in text
    assert all(int(line[4:]) > sequence for line in text.splitlines() if line.startswith("id: "))
    assert Skill.generations == 1


def test_legacy_history_migration_preserves_files_and_version_chain(api):
    client, service, root = api
    output = root / "authors" / "sample" / "output"
    output.mkdir()
    for name, parent, body, created in [("draft","","original","2026-10-01T00:00:00+00:00"),
                                         ("rewrite","draft","revised","2026-10-02T00:00:00+00:00")]:
        (output / f"{name}.txt").write_text(body, encoding="utf-8")
        (output / f"{name}.version.json").write_text(json.dumps({"parent_version":parent,"topic":"历史作品","created_at":created}), encoding="utf-8")
    before = {file.name:file.read_bytes() for file in output.iterdir()}
    assert len(client.get("/api/articles").get_json()["data"]["items"]) == 1
    assert len(client.get("/api/articles").get_json()["data"]["items"]) == 1
    article = service.store.list_articles()[0]
    versions = service.store.list_versions(article["id"])
    assert len(versions) == 2 and versions[1]["parent_version"] == versions[0]["version_id"]
    assert before == {file.name:file.read_bytes() for file in output.iterdir()}


def test_partial_job_exports_are_not_imported_as_finished_history(api):
    client, service, root = api
    job = submit(client).get_json()["data"]
    original = service.exporter
    def fail_after_export(checkpoint):
        original(checkpoint)
        raise OSError("after export")
    service.exporter = fail_after_export
    service.run_once()
    assert client.get("/api/articles").get_json()["data"]["items"] == []
    assert service.store.get(job["id"])["status"] == "failed"


def test_import_upload_is_persisted_and_duplicate_does_not_create_second_job(api):
    client, service, _ = api
    def request():
        return client.post("/api/import-jobs", data={"name":"new_author","files":(io.BytesIO("原创语料。".encode() * 30),"book.txt")},
                           headers={"Idempotency-Key":"import"}, content_type="multipart/form-data")
    response = request()
    assert response.status_code == 202
    job = response.get_json()["data"]
    assert request().status_code == 200
    assert request().get_json()["data"]["id"] == job["id"]
    staged = service.root / ".stylemuse" / "uploads" / job["payload"]["staging_id"] / "0.txt"
    assert staged.read_text(encoding="utf-8") == "原创语料。" * 30
    assert len(list((service.root / ".stylemuse" / "uploads").iterdir())) == 1


def test_import_worker_uses_local_models_and_cleans_staging_after_success(api):
    from skills.author_style import config, author_manager
    from tests.test_storage_security import LocalEmbeddings
    client, service, root = api
    response = client.post("/api/import-jobs", data={"name":"new_author", "files":(io.BytesIO("原创语料。".encode() * 30), "book.txt")},
                           headers={"Idempotency-Key":"import-complete"}, content_type="multipart/form-data")
    job = response.get_json()["data"]
    model = type("Model", (), {"invoke": lambda self, messages: type("Reply", (), {"content":"===STYLE_GUIDE===\n短句。\n===FEW_SHOT===\n原创片段。"})()})()
    with patch.object(config, "AUTHORS_DIR", root / "authors"), patch.object(author_manager, "get_llm", return_value=model), \
         patch.object(config, "get_embeddings", return_value=LocalEmbeddings()):
        service.run_once()
    completed = service.store.get(job["id"])
    assert completed["status"] == "completed"
    assert completed["result"]["author"]["has_vector_store"]
    assert not (root / ".stylemuse" / "uploads" / job["payload"]["staging_id"]).exists()
    copied = next((root / "authors" / "new_author" / "works").glob("*.txt"))
    assert copied.read_text(encoding="utf-8") == "原创语料。" * 30
