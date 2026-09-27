import asyncio
import hmac
import os

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Model API gateway: synthetic Spark endpoint")
semaphore = asyncio.Semaphore(1)


@app.get("/health")
def health():
    return {"status": "ok", "gateway": os.getenv("GATEWAY_ID", "unset"), "mode": os.getenv("MODEL_MODE", "cloud")}


@app.post("/v1/chat/completions")
async def completion(request: Request, authorization: str = Header(default="")):
    secret = os.getenv("GATEWAY_KEY", "")
    if not secret or not hmac.compare_digest(authorization, "Bearer " + secret):
        raise HTTPException(401)
    if os.getenv("GATEWAY_DISABLED") == "1":
        raise HTTPException(503, "gateway disabled for failover test")
    if int(request.headers.get("content-length", "0")) > 500000:
        raise HTTPException(413)
    body = await request.json()
    allowed = {"messages", "tools", "tool_choice", "max_tokens", "response_format", "stream", "temperature"}
    if set(body) - allowed - {"model"} or body.get("stream"):
        raise HTTPException(400, "unsupported request")
    model, key = os.getenv("CLOUD_MODEL"), os.getenv("CLOUD_API_KEY")
    if not model or not key:
        raise HTTPException(503, "cloud configuration missing")
    import litellm
    litellm.telemetry = False
    litellm.suppress_debug_info = True
    params = {k: v for k, v in body.items() if k in allowed}
    params["max_tokens"] = min(params.get("max_tokens", 6000), 8000)
    try:
        async with semaphore:
            result = await litellm.acompletion(model=model, api_key=key,
                                              api_base=os.getenv("CLOUD_BASE_URL") or None,
                                              timeout=float(os.getenv("UPSTREAM_TIMEOUT_SECONDS", "120")),
                                              num_retries=0, **params)
    except Exception as e:
        status = getattr(e, "status_code", 502)
        # Upstream exception strings can contain key, prompt or repository data.
        raise HTTPException(status if status in (429, 500, 502, 503, 504) else 502,
                            "upstream request failed") from None
    return JSONResponse(result.model_dump(), headers={"x-gateway-id": os.getenv("GATEWAY_ID", "unset")})
