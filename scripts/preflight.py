"""Read-only readiness check. No installs, external writes, model calls, or secret values."""
import ctypes
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


def memory_gib():
    if os.name == "nt":
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in (
                            "total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "avail_extended")]]
        value = Memory()
        value.length = ctypes.sizeof(Memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
            return {"total": round(value.total_phys / 2**30, 1), "available": round(value.avail_phys / 2**30, 1)}
    return None


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        return result.stdout if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def main():
    env = dotenv_values(".env") if Path(".env").exists() else {}
    report = {"mode": "read-only preflight", "os": platform.platform(), "architecture": platform.machine(),
              "python": platform.python_version(), "memory_gib": memory_gib(),
              "workspace_free_gib": round(shutil.disk_usage(Path.cwd()).free / 2**30, 1),
              "administrator": bool(ctypes.windll.shell32.IsUserAnAdmin()) if os.name == "nt" else os.geteuid() == 0,
              "cloud": {k: bool(env.get(k)) for k in ("CLOUD_MODEL", "CLOUD_API_KEY", "CLOUD_BASE_URL")},
              "docker": {}, "occupied_ports": [], "blockers": []}
    for port in (8000, 4000, 8929, 2224):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                report["occupied_ports"].append(port)
    docker = shutil.which("docker")
    defaults = [Path("C:/Program Files/Docker/Docker/resources/bin/docker.exe")]
    if os.getenv("LOCALAPPDATA"):
        defaults.append(Path(os.environ["LOCALAPPDATA"]) / "Programs/DockerDesktop/resources/bin/docker.exe")
    if not docker:
        docker = next((str(path) for path in defaults if path.exists()), None)
    report["docker"]["cli_present"] = bool(docker)
    if not docker:
        report["blockers"].append("Docker CLI/Engine not available; install/start local Docker with Linux containers and Compose v2")
    else:
        endpoint = os.getenv("DOCKER_HOST")
        if not endpoint:
            raw = command([docker, "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}"])
            endpoint = json.loads(raw) if raw else None
        local = bool(endpoint and endpoint.startswith(("npipe://", "unix://")))
        report["docker"]["local_context"] = local
        if not local:
            report["blockers"].append("Docker context is not a verified local socket; remote hosts are outside this bootstrap scope")
        else:
            raw = command([docker, "version", "--format", "{{json .}}"])
            version = json.loads(raw) if raw else {}
            server = version.get("Server") or {}
            report["docker"]["client_version"] = (version.get("Client") or {}).get("Version")
            report["docker"]["server_version"] = server.get("Version")
            report["docker"]["server_os"] = server.get("Os")
            report["docker"]["compose_available"] = command([docker, "compose", "version", "--short"]) is not None
            if not server or server.get("Os") != "linux":
                report["blockers"].append("Local Docker Linux engine is not ready")
            if not report["docker"]["compose_available"]:
                report["blockers"].append("Docker Compose v2 is unavailable")
    if not env.get("CLOUD_MODEL") or not env.get("CLOUD_API_KEY"):
        report["blockers"].append("Select cloud provider/model and enter CLOUD_API_KEY locally in .env; do not send keys in chat")
    try:
        priced = float(env.get("TOKEN_PRICE_UPPER_USD") or 0) > 0
    except ValueError:
        priced = False
    report["cloud"]["price_upper_configured"] = priced
    mode = env.get("MODEL_MODE") or "cloud"
    report["cloud"]["mode"] = mode
    if mode not in ("local", "cloud"):
        report["blockers"].append("MODEL_MODE must be local or cloud")
    if not priced and mode != "local":
        report["blockers"].append("After selecting the model, set a verified conservative TOKEN_PRICE_UPPER_USD")
    report["status"] = "blocked" if report["blockers"] else "passed"
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/preflight.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 2 if report["blockers"] else 0


if __name__ == "__main__":
    sys.exit(main())
