"""HTTP upstream double for native proxy/gateway transport validation only."""
import time

from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/v1/chat/completions")
async def completion(request: Request):
    await request.json()
    return {"id": "explicit-http-mock", "object": "chat.completion", "created": int(time.time()), "model": "mock-upstream",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": '{"total":900}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
