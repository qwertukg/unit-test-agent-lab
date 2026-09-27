import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_lab.gateway import app


def test_gateway_auth_and_actual_translation_boundary(monkeypatch):
    monkeypatch.setenv("GATEWAY_KEY", "internal")
    monkeypatch.setenv("CLOUD_API_KEY", "upstream-not-in-prompt")
    monkeypatch.setenv("CLOUD_MODEL", "provider/model")
    monkeypatch.setenv("GATEWAY_ID", "spark-2")
    seen = []

    async def acompletion(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(model_dump=lambda: {"model": kwargs["model"], "choices": [], "usage": {"total_tokens": 1}})

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(acompletion=acompletion))
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json={}).status_code == 401
        body = {"model": "client-cannot-choose-upstream", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100000}
        result = client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer internal"})
        assert result.status_code == 200 and result.headers["x-gateway-id"] == "spark-2"
        assert seen[0]["max_tokens"] == 8000 and seen[0]["model"] == "provider/model"
        assert "upstream-not-in-prompt" not in result.text
        monkeypatch.setenv("GATEWAY_DISABLED", "1")
        assert client.post("/v1/chat/completions", json=body, headers={"Authorization": "Bearer internal"}).status_code == 503
