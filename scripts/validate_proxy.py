"""Launch actual LiteLLM Proxy + 3 actual gateways, backed by an explicit HTTP mock.

Native processes, NOT containers/cloud. All processes belong to this script and are stopped.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    work = ROOT / ".state/proxy-validation"
    work.mkdir(parents=True, exist_ok=True)
    processes, logs = [], []
    env = {**os.environ, "CLOUD_API_KEY": "explicit-local-mock-key", "CLOUD_MODEL": "openai/mock-upstream",
           "CLOUD_BASE_URL": "http://127.0.0.1:18500/v1", "GATEWAY_KEY": "gateway-local-mock-key",
           "MODEL_API_KEY": "sk-proxy-local-mock-key", "LITELLM_LOG": "ERROR"}
    # The host has an unrelated DEBUG=release variable, incompatible with Click's boolean parser.
    env.pop("DEBUG", None)
    env.pop("DATABASE_URL", None)
    env.pop("DIRECT_URL", None)
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    config = yaml.safe_load((ROOT / "config/litellm.yaml").read_text())
    for i, item in enumerate(config["model_list"], 1):
        item["litellm_params"]["api_base"] = f"http://127.0.0.1:{18500+i}/v1"
    (work / "litellm.yaml").write_text(yaml.safe_dump(config))

    def start(name, args, extra=None):
        output = (work / (name + ".log")).open("w", encoding="utf-8")
        logs.append(output)
        process = subprocess.Popen(args, cwd=ROOT, env={**env, **(extra or {})}, stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        processes.append(process)
        return process

    def ready(url, process):
        for _ in range(90):
            if process.poll() is not None:
                raise RuntimeError("local validation service failed; inspect .state/proxy-validation logs")
            try:
                if httpx.get(url, timeout=2).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise RuntimeError("local validation readiness timeout")

    request = {"model": "test-agent", "messages": [{"role": "user", "content": "Return JSON: total=900"}],
               "max_tokens": 30, "response_format": {"type": "json_object"}}
    try:
        upstream = start("upstream", [sys.executable, "-m", "uvicorn", "tests.upstream:app", "--port", "18500", "--no-access-log"])
        ready("http://127.0.0.1:18500/openapi.json", upstream)
        gateways = [start(f"spark-{i}", [sys.executable, "-m", "uvicorn", "agent_lab.gateway:app", "--port", str(18500+i), "--no-access-log"],
                          {"GATEWAY_ID": f"spark-{i}"}) for i in (1, 2, 3)]
        for i, gateway in enumerate(gateways, 1):
            ready(f"http://127.0.0.1:{18500+i}/health", gateway)
        executable = Path(sys.executable).parent / ("litellm.exe" if os.name == "nt" else "litellm")
        proxy = start("proxy", [str(executable), "--config", str(work / "litellm.yaml"), "--port", "18504", "--host", "127.0.0.1"])
        ready("http://127.0.0.1:18504/health/liveliness", proxy)
        result = {"mode": "real native LiteLLM Proxy + real gateway code; upstream HTTP mock; no cloud/containers", "direct": [], "proxy": []}
        for i in (1, 2, 3):
            response = httpx.post(f"http://127.0.0.1:{18500+i}/v1/chat/completions", json=request,
                                  headers={"Authorization": "Bearer " + env["GATEWAY_KEY"]}, timeout=150)
            if response.status_code != 200:
                raise RuntimeError(f"gateway {i} HTTP {response.status_code}")
            assert json.loads(response.json()["choices"][0]["message"]["content"])["total"] == 900
            result["direct"].append({"gateway": response.headers.get("x-gateway-id"), "usage": response.json()["usage"]})
        gateways[0].terminate()
        gateways[0].wait(timeout=10)
        for _ in range(4):
            response = httpx.post("http://127.0.0.1:18504/v1/chat/completions", json=request,
                                  headers={"Authorization": "Bearer " + env["MODEL_API_KEY"]}, timeout=150)
            if response.status_code != 200:
                raise RuntimeError(f"proxy HTTP {response.status_code}")
            assert json.loads(response.json()["choices"][0]["message"]["content"])["total"] == 900
            result["proxy"].append({"deployment": response.headers.get("x-litellm-model-id"), "usage": response.json()["usage"]})
        result["stopped_gateway"] = "spark-1"
        (ROOT / "artifacts/proxy-local.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for output in logs:
            output.close()


if __name__ == "__main__":
    main()
