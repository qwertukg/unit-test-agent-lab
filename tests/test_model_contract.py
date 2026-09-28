import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from agent_lab.config import Settings
from agent_lab.contracts import Scenarios
from agent_lab.model import Model
from agent_lab.repository import Repository


@pytest.mark.parametrize("json_mode", ["json_object", "prompt"])
@pytest.mark.parametrize("valid", [True, False])
def test_actual_client_tool_loop_usage_cache_and_secret_boundary(store, tmp_path, json_mode, valid):
    (tmp_path / "product.py").write_text("def value():\n    return 3\n")
    fixture = {"scenarios": [{"id": "S1", "purpose": "value", "requirements": ["R1"], "paths": ["product.py"],
                               "setup": "none", "inputs": "none", "expected": "3", "kind": "normal", "allowed_mocks": []}]}
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert ("response_format" in data) == (json_mode == "json_object")
        assert "never-in-prompt" not in request.content.decode()
        if len(requests) == 1:
            message = {"role": "system", "content": None, "tool_calls": [{"id": "tool-1", "type": "function",
                       "function": {"name": "read_file", "arguments": '{"path":"product.py"}'}}]}
        else:
            if len(requests) == 2:
                assert "return 3" in data["messages"][-1]["content"]
            else:
                assert data["tool_choice"] == "none"
                assert "failed schema validation" in data["messages"][-1]["content"]
            assert data["messages"][-2]["role"] == "assistant"  # Provider output cannot create a system role.
            message = {"role": "assistant", "content": json.dumps(fixture if valid else {"scenarios": "invalid"})}
        return httpx.Response(200, json={"model": "explicit-mock", "usage": {"total_tokens": 100},
                              "choices": [{"finish_reason": "stop", "message": message}]}, headers={"x-gateway-id": "test-double"})

    settings = Settings(_env_file=None, model_api_key=SecretStr("never-in-prompt"), token_price_upper_usd=1,
                        model_json_mode=json_mode)
    model = Model(settings, store, httpx.MockTransport(handler))
    run_id = store.new_run(12, 3, "a" * 40, "coverage", {})
    state = store.run(run_id)["initial_state"]
    if not valid:
        with pytest.raises(ValidationError):
            model.generate("Analyst", state, {"O1": "input"}, Repository(tmp_path), Scenarios, lambda: None)
        assert not store.query("SELECT 1 FROM lab_actions WHERE status='completed'")
        assert len(requests) == 3  # One format correction, within the existing budget.
        return
    result = model.generate("Analyst", state, {"O1": "input"}, Repository(tmp_path), Scenarios, lambda: None)
    assert result.scenarios[0].id == "S1"
    # Crash after result persisted, before the graph checkpoint. Context may now contain O6.
    assert model.generate("Analyst", state, {"O1": "input", "O6": fixture}, Repository(tmp_path), Scenarios, lambda: None) == result
    assert len(requests) == 2
    assert store.run(run_id)["tokens_used"] == 600  # Conservative 3x proxy retry accounting.
    assert len(store.query("SELECT * FROM lab_calls")) == 2


def test_format_repair_validates_again_and_preserves_budgets(store, tmp_path):
    (tmp_path / "product.py").write_text("def value(): return 3\n")
    fixture = {"scenarios": [{"id": "S1", "purpose": "value", "requirements": ["R1"], "paths": ["product.py"],
                               "setup": "none", "inputs": "none", "expected": "3", "kind": "normal", "allowed_mocks": []}]}
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        if len(requests) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "tool-1", "type": "function",
                       "function": {"name": "read_file", "arguments": '{"path":"product.py"}'}}]}
        elif len(requests) == 2:
            message = {"role": "assistant", "content": '```json\n' + json.dumps(fixture) + '\n```'}
        else:
            assert data["tool_choice"] == "none"
            assert "json_invalid" in data["messages"][-1]["content"]
            message = {"role": "assistant", "content": json.dumps(fixture)}
        return httpx.Response(200, json={"model": "explicit-mock", "usage": {"total_tokens": 100},
                              "choices": [{"finish_reason": "stop", "message": message}]})

    settings = Settings(_env_file=None, model_mode="local", max_tool_steps=2, max_model_calls=3)
    model = Model(settings, store, httpx.MockTransport(handler))
    run_id = store.new_run(12, 3, "a" * 40, "coverage", {})
    result = model.generate("Analyst", store.run(run_id)["initial_state"], {}, Repository(tmp_path), Scenarios, lambda: None)
    assert result.scenarios[0].id == "S1"
    assert len(requests) == 3
    assert store.run(run_id)["model_calls"] == 3
    assert store.run(run_id)["tokens_used"] == 900


@pytest.mark.parametrize("ignores_limit", [False, True])
def test_last_request_reserves_final_answer_without_executing_extra_tools(store, tmp_path, ignores_limit):
    from agent_lab.contracts import BudgetExceeded

    (tmp_path / "product.py").write_text("def value(): return 3\n")
    fixture = {"scenarios": [{"id": "S1", "purpose": "value", "requirements": ["R1"], "paths": ["product.py"],
                               "setup": "none", "inputs": "none", "expected": "3", "kind": "normal", "allowed_mocks": []}]}
    requests = []

    def handler(request):
        data = json.loads(request.content)
        requests.append(data)
        assert data["tool_choice"] == ("auto" if len(requests) == 1 else "none")
        if len(requests) == 1 or ignores_limit:
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "tool-1", "type": "function",
                       "function": {"name": "read_file", "arguments": '{"path":"product.py"}'}}]}
        else:
            message = {"role": "assistant", "content": json.dumps(fixture)}
        return httpx.Response(200, json={"model": "explicit-mock", "usage": {"total_tokens": 100},
                              "choices": [{"finish_reason": "stop", "message": message}]})

    settings = Settings(_env_file=None, model_mode="local", max_tool_steps=1)
    model = Model(settings, store, httpx.MockTransport(handler))
    run_id = store.new_run(12, 3, "a" * 40, "coverage", {})
    repo = Repository(tmp_path)
    if ignores_limit:
        with pytest.raises(BudgetExceeded, match="tool-step limit"):
            model.generate("Analyst", store.run(run_id)["initial_state"], {}, repo, Scenarios, lambda: None)
        assert not store.query("SELECT 1 FROM lab_actions WHERE status='completed'")
    else:
        result = model.generate("Analyst", store.run(run_id)["initial_state"], {}, repo, Scenarios, lambda: None)
        assert result.scenarios[0].id == "S1"
    assert len(requests) == 2
