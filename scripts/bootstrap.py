"""Idempotent LOCAL GitLab bootstrap. Never contacts a corporate instance."""
import json
import secrets
import subprocess
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml
from dotenv import dotenv_values

from agent_lab.contracts import digest

COMPOSE = ["docker", "compose", "--env-file", ".env", "--env-file", "config/images.env"]


def compose(*args, stdin=None):
    result = subprocess.run([*COMPOSE, *args], input=stdin, text=True, capture_output=True, timeout=1800)
    if result.returncode:
        # Captured output may contain registration tokens. Do not echo it.
        raise RuntimeError("Compose step failed: " + args[0] + "; inspect local service status without dumping environment")
    return result.stdout


def save_env(values):
    Path(".env").write_text("\n".join(k + "='" + (v or "").replace("\\", "\\\\").replace("'", "\\'") + "'"
                                      for k, v in values.items()) + "\n", encoding="utf-8")


def main():
    env = dict(dotenv_values(".env"))
    url = env.get("GITLAB_URL", "http://localhost:8929")
    parsed = urlparse(url)
    if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.port != 8929:
        raise SystemExit("Bootstrap is restricted to the isolated localhost:8929 lab")
    state_path = Path(".state/bootstrap.json")
    state_path.parent.mkdir(exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    def save():
        state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print("Starting isolated GitLab/PostgreSQL/DinD; first GitLab boot can take several minutes.", flush=True)
    # Monitoring endpoints allow container-local IPs by default. A host request
    # through Docker NAT can get 404 despite healthy GitLab; use its healthcheck.
    compose("up", "-d", "--wait", "--wait-timeout", "900", "postgres", "gitlab", "dind")
    httpx.get(url + "/users/sign_in", timeout=30).raise_for_status()
    state.setdefault("root_token", secrets.token_hex(24))
    save()
    # Bootstrap credential stays in .state; no runtime/model service receives it.
    ruby = ("u=User.find_by_username('root'); "
            "t=u.personal_access_tokens.find_or_initialize_by(name:'unit-test-lab-bootstrap'); "
            "t.scopes=['api']; t.expires_at=30.days.from_now; t.revoked=false; "
            f"t.set_token('{state['root_token']}'); t.save!; puts 'bootstrap-token-ready'")
    compose("exec", "-T", "gitlab", "gitlab-rails", "runner", "-", stdin=ruby)
    client = httpx.Client(base_url=url + "/api/v4/", headers={"PRIVATE-TOKEN": state["root_token"]}, timeout=90)

    def call(method, path, data=None, params=None):
        result = client.request(method, path, json=data, params=params)
        if result.status_code >= 400:
            raise RuntimeError(f"bootstrap API {method} {path}: {result.status_code}")
        return result.json() if result.content else None

    call("PUT", "application/settings", {"allow_local_requests_from_web_hooks_and_services": True, "signup_enabled": False})
    users = call("GET", "users", params={"username": "unit-test-lab-bot"})
    bot = users[0] if users else call("POST", "users", {
        "username": "unit-test-lab-bot", "name": "Unit Test Lab Bot", "email": "bot@unit-test-lab.invalid",
        "password": secrets.token_urlsafe(32), "skip_confirmation": True, "admin": False})
    if not env.get("GITLAB_TOKEN"):
        token = call("POST", f"users/{bot['id']}/personal_access_tokens", {
            "name": "unit-test-lab-runtime", "scopes": ["api"], "expires_at": str(date.today() + timedelta(days=30))})
        env["GITLAB_TOKEN"] = token["token"]
        save_env(env)
    projects = []
    evidence = []
    root_user = call("GET", "user")
    for stack in ("java", "python"):
        name = "unit-test-agent-demo-" + stack
        listed = call("GET", "projects", params={"search": name, "owned": "true", "per_page": 100})
        exact = [p for p in listed if p["path"] == name and p["namespace"]["id"] == root_user["namespace_id"]] if "namespace_id" in root_user else [p for p in listed if p["path"] == name]
        project = exact[0] if exact else call("POST", "projects", {
            "name": name, "path": name, "visibility": "private", "initialize_with_readme": False,
            "description": "Synthetic Unit Test Agent Lab product; no corporate source"})
        pid = project["id"]
        members = call("GET", f"projects/{pid}/members/all", params={"user_ids[]": bot["id"]})
        if not any(m["id"] == bot["id"] for m in members):
            call("POST", f"projects/{pid}/members", {"user_id": bot["id"], "access_level": 30})
        files = {p.relative_to(Path("demo") / stack).as_posix(): p.read_text(encoding="utf-8")
                 for p in (Path("demo") / stack).rglob("*") if p.is_file() and "target" not in p.parts and "__pycache__" not in p.parts}
        files[".gitlab-ci.yml"] = Path("ci/gitlab-ci.yml").read_text()
        files[".agent-ci.py"] = Path("ci/run.py").read_text()
        files["README.md"] = "Synthetic demo pricing product. Requirements: DEMO-1.\n"
        source_path = "src/main/java/lab/Pricing.java" if stack == "java" else "product/pricing.py"
        feature_content = files[source_path]
        files[source_path] = ("package lab;\npublic class Pricing {\n    public int total(int cents, boolean member, String coupon) {\n        return cents;\n    }\n}\n"
                              if stack == "java" else "def total(cents: int, member: bool, coupon: str | None = None) -> int:\n    return cents\n")
        branches = call("GET", f"projects/{pid}/repository/branches")
        if not any(b["name"] == "dev" for b in branches):
            call("POST", f"projects/{pid}/repository/commits", {"branch": "dev", "commit_message": "Synthetic baseline",
                 "actions": [{"action": "create", "file_path": p, "content": text} for p, text in files.items()]})
            call("PUT", f"projects/{pid}", {"default_branch": "dev"})
        branch = "feature/discounts"
        if not any(b["name"] == branch for b in branches):
            call("POST", f"projects/{pid}/repository/commits", {"branch": branch, "start_branch": "dev",
                 "commit_message": "DEMO-1 add discounts without sufficient tests",
                 "actions": [{"action": "update", "file_path": source_path, "content": feature_content}]})
        mrs = call("GET", f"projects/{pid}/merge_requests", params={"source_branch": branch, "target_branch": "dev", "state": "all"})
        mr = mrs[0] if mrs else call("POST", f"projects/{pid}/merge_requests", {
            "source_branch": branch, "target_branch": "dev", "title": "DEMO-1 Discounts", "description": "Synthetic coverage-gate demonstration"})
        hooks = call("GET", f"projects/{pid}/hooks")
        hook = {"url": "http://api:8000/webhooks/gitlab", "token": env["WEBHOOK_SECRET"],
                "pipeline_events": True, "merge_requests_events": True, "note_events": True,
                "enable_ssl_verification": False}
        old_hook = next((h for h in hooks if h["url"] == hook["url"]), None)
        call("PUT" if old_hook else "POST", f"projects/{pid}/hooks" + (f"/{old_hook['id']}" if old_hook else ""), hook)
        trusted = [".test-agent.json", ".gitlab-ci.yml", ".agent-ci.py", "pom.xml" if stack == "java" else "requirements.lock"]
        projects.append({"id": pid, "name": name, "stack": stack, "test_paths": ["src/test/java" if stack == "java" else "tests"],
                         "trusted_files": {p: digest(files[p].encode()) for p in trusted},
                         "task_fixture": "demo/tasks/pricing.json"})
        evidence.append({"project_id": pid, "source_mr": mr["web_url"], "source_iid": mr["iid"], "stack": stack})
    Path("config/projects.yaml").write_text(yaml.safe_dump({"projects": projects}, sort_keys=False), encoding="utf-8")
    Path("artifacts").mkdir(exist_ok=True)
    Path("artifacts/bootstrap.json").write_text(json.dumps(evidence, indent=2))
    if "runner_token" not in state:
        runners = call("GET", "runners/all", params={"type": "project_type", "per_page": 100})
        old = next((r for r in runners if r.get("description") == "unit-test-agent-lab"), None)
        if old:
            runner = call("POST", f"runners/{old['id']}/reset_authentication_token")
            runner["id"] = old["id"]
        else:
            runner = call("POST", "user/runners", {"runner_type": "project_type", "project_id": projects[0]["id"],
                "description": "unit-test-agent-lab", "tag_list": ["unit-test-lab"], "run_untagged": False, "locked": False})
        state.update(runner_id=runner["id"], runner_token=runner["token"])
        save()
    for project in projects[1:]:
        assigned = call("GET", f"projects/{project['id']}/runners")
        if not any(r["id"] == state["runner_id"] for r in assigned):
            call("POST", f"projects/{project['id']}/runners", {"runner_id": state["runner_id"]})
    # Build before exposing runner configuration: pending jobs must not race the image build.
    print("Building dedicated CI image inside the isolated runner daemon.", flush=True)
    compose("run", "--rm", "ci-builder")
    compose("up", "-d", "runner")
    config = f'''concurrent = 1
check_interval = 3
[[runners]]
  name = "unit-test-agent-lab"
  url = "http://gitlab:8929"
  clone_url = "http://gitlab:8929"
  id = {state['runner_id']}
  token = "{state['runner_token']}"
  executor = "docker"
  [runners.docker]
    host = "tcp://dind:2376"
    tls_verify = true
    tls_cert_path = "/certs/client"
    image = "unit-test-agent-ci:0.1"
    privileged = false
    network_mode = "host"
    pull_policy = "if-not-present"
    allowed_images = ["unit-test-agent-ci:0.1"]
    allowed_services = []
    memory = "2g"
    cpus = "2"
    volumes = []
'''
    compose("exec", "-T", "runner", "sh", "-c", "umask 077; cat > /etc/gitlab-runner/config.toml", stdin=config)
    compose("restart", "runner")
    compose("exec", "-T", "runner", "gitlab-runner", "verify")
    mode = env.get("MODEL_MODE", "cloud")
    model_ready = bool(mode in ("local", "cloud") and env.get("CLOUD_MODEL") and env.get("CLOUD_API_KEY")
                       and (mode == "local" or float(env.get("TOKEN_PRICE_UPPER_USD") or 0) > 0))
    services = ["api", "litellm", "spark-1", "spark-2", "spark-3"]
    compose("stop", "worker")
    compose("build", "api", "litellm")
    compose("up", "-d", "--no-build", "--wait", "--wait-timeout", "180", *services)
    runtime = {"gitlab": call("GET", "version"), "compose_images": compose("images", "--format", "json"),
               "ci_image_id": compose("run", "--rm", "ci-builder", "image", "inspect", "unit-test-agent-ci:0.1", "--format", "{{.Id}}").strip()}
    Path("artifacts/runtime-images.json").write_text(json.dumps(runtime, indent=2))
    if model_ready:
        print("Checking real model JSON/tool capabilities before starting the worker.", flush=True)
        # Fail closed: a configured URL/key alone does not prove model compatibility.
        try:
            probe_result = compose("exec", "-T", "litellm", "python", "-m", "agent_lab.probe")
        except RuntimeError:
            Path("artifacts/model-probe.json").write_text(json.dumps({"status": "blocked", "mode": mode,
                "reason": "Real JSON/tool capability probe failed", "worker_started": False}, indent=2))
            raise SystemExit("Model capability probe failed; worker remains stopped. Check the configured model server.") from None
        Path("artifacts/model-probe.json").write_text(probe_result, encoding="utf-8")
        compose("up", "-d", "worker")
    print("Bootstrap complete. GitLab http://localhost:8929 ; API http://localhost:8000 ; LiteLLM http://localhost:4000")
    print("Source MRs recorded in artifacts/bootstrap.json. Model acceptance uses the configured external endpoint.")
    if not model_ready:
        print("Worker not started: configure model/key, MODEL_MODE, and a positive token price for cloud mode.")


if __name__ == "__main__":
    main()
