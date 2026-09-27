import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agent_lab.api import create_app
from agent_lab.config import Settings


def test_api_auth_and_cancel_resume(store, tmp_path):
    settings = Settings(_env_file=None, api_token=SecretStr("test-admin"), webhook_secret=SecretStr("hook"),
                        project_config=tmp_path / "projects.yaml")
    app = create_app(settings, store)
    run = store.new_run(12, 3, "a" * 40, "coverage", {})
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 200
        assert client.get("/runs").status_code == 401
        assert client.post("/webhooks/gitlab", json={}).status_code == 401
        headers = {"Authorization": "Bearer test-admin"}
        assert client.get("/runs", headers=headers).json()[0]["id"] == run
        assert client.post(f"/runs/{run}/cancel", headers=headers).json()["cancel_requested"]
        store.status(run, "needs_human", "cancelled")
        assert client.post(f"/runs/{run}/resume", headers=headers).status_code == 409


def test_note_webhook_persists_original_text_before_processing(store, tmp_path, project):
    config = tmp_path / "projects.yaml"
    config.write_text(yaml.safe_dump({"projects": [project.model_dump(mode="json")]}))
    settings = Settings(_env_file=None, webhook_secret=SecretStr("hook"), project_config=config)
    body = {"project": {"id": project.id}, "object_kind": "note", "user": {"id": 7}, "merge_request": {"iid": 20},
            "object_attributes": {"noteable_type": "MergeRequest", "id": 51, "note": "@unit-test-agent original text"}}
    with TestClient(create_app(settings, store)) as client:
        headers = {"X-Gitlab-Token": "hook", "X-Gitlab-Event-UUID": "note-event"}
        assert client.post("/webhooks/gitlab", json=body, headers=headers).status_code == 202
        assert client.post("/webhooks/gitlab", json=body, headers=headers).json()["duplicate"]
    saved = store.query("SELECT payload FROM lab_events", one=True)["payload"]
    assert saved["note_snapshot"]["body"] == "@unit-test-agent original text"
