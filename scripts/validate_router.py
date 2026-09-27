"""Real LiteLLM router over three HTTP test-double endpoints; NO cloud/GPU claim."""
import asyncio
import json
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request


async def validate():
    import litellm
    from litellm import Router
    litellm.telemetry = False
    litellm.suppress_debug_info = True
    hits = [0, 0, 0]
    servers = []
    models = []
    for i in range(3):
        app = FastAPI()

        def install(index, application):
            @application.post("/v1/chat/completions")
            async def reply(request: Request):
                hits[index] += 1
                if index == 0:
                    raise HTTPException(503, "deliberate endpoint failure")
                body = await request.json()
                return {"id": f"stub-{index}", "object": "chat.completion", "created": int(time.time()),
                        "model": body["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"ok":true}'}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6}}
        install(i, app)
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=18401 + i, log_level="error"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        servers.append((server, thread))
        models.append({"model_name": "lab", "litellm_params": {"model": "openai/mock", "api_base": f"http://127.0.0.1:{18401+i}/v1",
                                                             "api_key": "local-test-only"}, "model_info": {"id": f"stub-{i}"}})
    try:
        for _ in range(100):
            if all(s.started for s, _ in servers):
                break
            await asyncio.sleep(.05)
        router = Router(model_list=models, num_retries=2, allowed_fails=1, cooldown_time=30, timeout=10)
        successes = 0
        # Direct named deployments prove all three HTTP endpoints are reachable; first is deliberately failing.
        for deployment in models:
            try:
                await litellm.acompletion(**deployment["litellm_params"], messages=[{"role": "user", "content": "probe"}])
            except litellm.ServiceUnavailableError:
                assert deployment["model_info"]["id"] == "stub-0"
        for _ in range(12):
            response = await router.acompletion(model="lab", messages=[{"role": "user", "content": "return JSON"}])
            assert json.loads(response.choices[0].message.content)["ok"]
            successes += 1
        assert all(hits) and successes == 12
        result = {"mode": "actual LiteLLM routing; HTTP test doubles, no cloud", "requests_to_endpoints": hits,
                  "successful_routed_requests": successes, "failed_endpoint": "stub-0", "litellm": "1.102.1"}
        Path("artifacts").mkdir(exist_ok=True)
        Path("artifacts/router-local.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        for server, _thread in servers:
            server.should_exit = True
        for _server, thread in servers:
            thread.join(timeout=5)


if __name__ == "__main__":
    asyncio.run(validate())
