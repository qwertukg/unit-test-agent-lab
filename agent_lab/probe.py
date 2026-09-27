"""Small REAL external endpoint/tool/JSON probes. No mock responses."""
import json
import os

import httpx


def probe(base, key, name):
    client = httpx.Client(base_url=base.rstrip("/") + "/", headers={"Authorization": "Bearer " + key}, timeout=150)
    mode = os.getenv("MODEL_JSON_MODE", "json_object")
    if mode not in ("json_object", "prompt"):
        raise ValueError("invalid MODEL_JSON_MODE")
    json_options = {"response_format": {"type": "json_object"}} if mode == "json_object" else {}
    # Fail fast for servers which advertise chat but reject the required JSON mode.
    check = client.post("chat/completions", json={"model": "test-agent",
        "messages": [{"role": "user", "content": 'Return only JSON: {"ready":true}'}],
        "max_tokens": 1024, **json_options})
    if check.status_code != 200:
        raise RuntimeError(f"{name}: JSON capability probe HTTP {check.status_code} (body suppressed)")
    check_data = check.json()
    if json.loads(check_data["choices"][0]["message"]["content"]) != {"ready": True}:
        raise RuntimeError(f"{name}: invalid JSON capability response")
    fn = {"type": "function", "function": {"name": "read_fixture", "description": "Read synthetic demo fixture",
          "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}}}
    messages = [{"role": "user", "content": "Call read_fixture with name pricing. This is a synthetic API capability probe."}]
    first = client.post("chat/completions", json={"model": "test-agent", "messages": messages, "tools": [fn],
                      "tool_choice": {"type": "function", "function": {"name": "read_fixture"}}, "max_tokens": 1024})
    if first.status_code != 200:
        raise RuntimeError(f"{name}: tool probe HTTP {first.status_code} (body suppressed)")
    data = first.json()
    msg = data["choices"][0]["message"]
    call = msg["tool_calls"][0]
    if call["function"]["name"] != "read_fixture" or json.loads(call["function"]["arguments"]) != {"name": "pricing"}:
        raise RuntimeError("invalid tool arguments")
    messages.extend([{k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")},
                     {"role": "tool", "tool_call_id": call["id"], "content": '{"total":900}'},
                     {"role": "user", "content": 'Return only a JSON object {"total":900} from the tool result.'}])
    second = client.post("chat/completions", json={"model": "test-agent", "messages": messages,
                       "max_tokens": 1024, **json_options})
    if second.status_code != 200:
        raise RuntimeError(f"{name}: JSON probe HTTP {second.status_code} (body suppressed)")
    answer = second.json()
    if json.loads(answer["choices"][0]["message"]["content"]) != {"total": 900}:
        raise RuntimeError("invalid JSON/tool result")
    return {"endpoint": name, "model": data["model"], "json_mode": mode, "gateway": first.headers.get("x-gateway-id"),
            "tool_call": "passed", "structured_response": "passed",
            "usage": [check_data.get("usage"), data.get("usage"), answer.get("usage")]}


def main():
    key = os.environ["GATEWAY_KEY"]
    results = [probe(f"http://spark-{i}:8000/v1", key, f"spark-{i}") for i in (1, 2, 3)]
    results.append(probe("http://litellm:4000/v1", os.environ["MODEL_API_KEY"], "litellm"))
    print(json.dumps({"mode": "real " + os.getenv("MODEL_MODE", "cloud") + " model", "probes": results}, indent=2))


if __name__ == "__main__":
    main()
