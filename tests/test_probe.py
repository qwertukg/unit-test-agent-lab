import json

import httpx
import pytest

from agent_lab.probe import probe


def test_probe_rejects_missing_json_support_before_tool_generation(monkeypatch):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400, json={"error": "JSON support unavailable"})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="JSON capability probe HTTP 400"):
        probe("http://test-double/v1", "explicit-test-key", "double")
    assert len(calls) == 1 and "tools" not in calls[0]


@pytest.mark.parametrize("mode", ["json_object", "prompt"])
def test_probe_checks_json_tool_arguments_and_tool_result(monkeypatch, mode):
    monkeypatch.setenv("MODEL_JSON_MODE", mode)
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        message = {"role": "assistant", "content": '{"ready":true}'}
        if len(calls) == 2:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "fixture-1",
                "type": "function", "function": {"name": "read_fixture", "arguments": '{"name":"pricing"}'}}]}
        elif len(calls) == 3:
            assert calls[-1]["messages"][-2]["role"] == "tool"
            message = {"role": "assistant", "content": '{"total":900}'}
        return httpx.Response(200, json={"model": "explicit-double", "choices": [{"message": message}],
                                        "usage": {"total_tokens": 10}})

    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handler)))
    result = probe("http://test-double/v1", "explicit-test-key", "double")
    assert len(calls) == 3 and len(result["usage"]) == 3
    assert result["tool_call"] == result["structured_response"] == "passed"
    assert ("response_format" in calls[0]) == (mode == "json_object")
