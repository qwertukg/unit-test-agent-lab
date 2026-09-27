"""Read-only cloud acceptance capture, plus explicit authorized demo feedback/restart options.

Requires the real local Docker lab. Never marks an unrun case passed.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

COMPOSE = ["docker", "compose", "--env-file", ".env", "--env-file", "config/images.env"]


def check_mutation(gl, run, objects, timeout):
    """Mutation in a dedicated synthetic branch, never in the test MR; actual Runner executes it."""
    project = gl.get(f"projects/{run['project_id']}").json()
    if not project["path"].startswith("unit-test-agent-demo-"):
        raise RuntimeError("mutation is restricted to synthetic bootstrap projects")
    o7 = max((o for o in objects if o["object_id"] == "O7"), key=lambda o: o["version"])
    sha = o7["test_sha"]
    source = "src/main/java/lab/Pricing.java" if project["path"].endswith("java") else "product/pricing.py"
    body = gl.get(f"projects/{run['project_id']}/repository/files/{quote(source, safe='')}/raw", params={"ref": sha})
    body.raise_for_status()
    assert body.text.count("cents >= 10000") == 1
    ref = f"agent-mutation/{run['mr_iid']}/{sha[:12]}"
    endpoint = f"projects/{run['project_id']}"
    branch = gl.get(endpoint + "/repository/branches/" + quote(ref, safe=""))
    marker = "Synthetic mutation check for " + sha
    if branch.status_code == 404:
        created = gl.post(endpoint + "/repository/commits", json={"branch": ref, "start_sha": sha,
            "commit_message": marker, "actions": [{"action": "update", "file_path": source,
            "content": body.text.replace("cents >= 10000", "cents > 10000")} ]})
        created.raise_for_status()
        mutation_sha = created.json()["id"]
    else:
        branch.raise_for_status()
        assert marker in branch.json()["commit"]["message"]
        mutation_sha = branch.json()["commit"]["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pipelines = gl.get(endpoint + "/pipelines", params={"ref": ref, "sha": mutation_sha}).json()
        if pipelines and pipelines[0]["status"] in ("success", "failed"):
            pipeline = pipelines[0]
            jobs = gl.get(endpoint + f"/pipelines/{pipeline['id']}/jobs").json()
            job = next(j for j in jobs if j["name"] == "unit")
            report = gl.get(endpoint + f"/jobs/{job['id']}/artifacts/reports/agent-report.json")
            report.raise_for_status()
            data = report.json()
            assert data["sha"] == mutation_sha and data["job_id"] == job["id"]
            assert data["status"] == "assertion_failure" and data["failures"] > 0, "generated tests missed known regression"
            return {"tested_commit": sha, "mutation_commit": mutation_sha, "ref": ref,
                    "pipeline_id": pipeline["id"], "job_id": job["id"], "failures": data["failures"], "job_url": job["web_url"]}
        time.sleep(5)
    raise RuntimeError("mutation CI timeout")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feedback", action="store_true", help="Post a demo author's thumbsdown and tagged comment")
    parser.add_argument("--restart", action="store_true", help="Restart only this lab's worker during an existing verification pipeline")
    parser.add_argument("--mutation", action="store_true", help="Check generated tests catch a known defect on a separate synthetic branch")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    env = dotenv_values(".env")
    api = httpx.Client(base_url="http://localhost:8000", headers={"Authorization": "Bearer " + env["API_TOKEN"]}, timeout=20)
    deadline = time.monotonic() + args.timeout
    restart = None
    print("Waiting for real lab runs; no mock results are accepted.", flush=True)
    while time.monotonic() < deadline:
        response = api.get("/runs")
        response.raise_for_status()
        runs = response.json()
        if args.restart and restart is None:
            for run in runs:
                if run["status"] not in ("running", "waiting"):
                    continue
                actions = api.get(f"/runs/{run['id']}/actions").json()
                pipelines = [a for a in actions if a["kind"] == "pipeline" and a["status"] == "completed"]
                if pipelines:
                    pipeline_id = pipelines[-1]["result"]["id"]
                    # Verify CI is actually active, not just a completed action in history.
                    with httpx.Client(base_url="http://localhost:8929/api/v4/", headers={"PRIVATE-TOKEN": env["GITLAB_TOKEN"]}) as gl:
                        pipeline = gl.get(f"projects/{run['project_id']}/pipelines/{pipeline_id}").json()
                    if pipeline["status"] not in ("running", "pending", "created"):
                        continue
                    subprocess.run([*COMPOSE, "kill", "-s", "SIGKILL", "worker"], check=True, capture_output=True)
                    subprocess.run([*COMPOSE, "up", "-d", "worker"], check=True, capture_output=True)
                    restart = {"run_id": run["id"], "pipeline_id": pipeline_id, "action_key": pipelines[-1]["action_key"]}
                    print("Worker killed/restarted while actual CI was active.", flush=True)
                    break
        stopped = [r for r in runs if r["status"] in ("needs_human", "cancelled")]
        if stopped:
            raise SystemExit("Real run stopped; inspect authenticated /runs: " + ", ".join(r["id"] for r in stopped))
        completed = [r for r in runs if r["status"] == "published"]
        if len({r["project_id"] for r in completed}) >= 2:
            break
        time.sleep(5)
    else:
        raise SystemExit("Timed out waiting for real cloud/GitLab E2E")
    result = {"mode": "real " + (env.get("MODEL_MODE") or "cloud") + " model and real GitLab",
              "runs": [], "restart": restart, "feedback": None}
    result["mutations"] = []
    runtime_gl = httpx.Client(base_url="http://localhost:8929/api/v4/", headers={"PRIVATE-TOKEN": env["GITLAB_TOKEN"]}, timeout=30)
    if args.restart and restart is None:
        raise SystemExit("Restart scenario NOT RUN: no active verification CI observed. Use documented CI delay and a new source revision.")
    for run in completed:
        objects = api.get(f"/runs/{run['id']}/objects").json()
        calls = api.get(f"/runs/{run['id']}/calls").json()
        actions = api.get(f"/runs/{run['id']}/actions").json()
        if args.mutation:
            result["mutations"].append(check_mutation(runtime_gl, run, objects, args.timeout))
        if not {f"O{i}" for i in range(1, 9)} <= {o["object_id"] for o in objects}:
            raise SystemExit("Missing O1-O8")
        if not {"Analyst", "Coder", "Reviewer"} <= {c["role"] for c in calls}:
            raise SystemExit("Missing actual role usage; mocks cannot count")
        result["runs"].append({"run": run, "objects": [{k: o[k] for k in ("id", "object_id", "version", "source_sha", "test_sha", "content_hash")} for o in objects],
                                "calls": calls, "actions": [{"key": a["action_key"], "kind": a["kind"], "status": a["status"], "result": a["result"]} for a in actions]})
    if restart:
        actions = api.get(f"/runs/{restart['run_id']}/actions").json()
        recovered = next(a for a in actions if a["action_key"] == restart["action_key"])
        assert recovered["result"]["id"] == restart["pipeline_id"]
    if args.feedback:
        root = json.loads(Path(".state/bootstrap.json").read_text())
        gl = httpx.Client(base_url="http://localhost:8929/api/v4/", headers={"PRIVATE-TOKEN": root["root_token"]}, timeout=30)
        run = completed[0]
        source = gl.get(f"projects/{run['project_id']}/merge_requests/{run['mr_iid']}").json()
        assert source["author"]["id"] == gl.get("user").json()["id"]
        found = gl.get(f"projects/{run['project_id']}/merge_requests", params={"source_branch": f"ai-tests/{run['mr_iid']}", "state": "opened"}).json()
        assert len(found) == 1
        test_mr = found[0]
        base = f"projects/{run['project_id']}/merge_requests/{test_mr['iid']}"
        old_runs = {r["id"] for r in api.get("/runs").json()}
        # Remove an old demo reaction if present; a new award has a new stable ID.
        for award in gl.get(base + "/award_emoji").json():
            if award["name"] == "thumbsdown" and award["user"]["id"] == source["author"]["id"]:
                gl.delete(base + f"/award_emoji/{award['id']}").raise_for_status()
        gl.post(base + "/notes", json={"body": "@unit-test-agent Please explicitly verify the 9999/10000 threshold and rounding of 101 cents. Preserve expected behavior and revise the tests if any case is missing."}).raise_for_status()
        award = gl.post(base + "/award_emoji", json={"name": "thumbsdown"})
        award.raise_for_status()
        until = time.monotonic() + args.timeout
        while time.monotonic() < until:
            newer = [r for r in api.get("/runs").json() if r["id"] not in old_runs and r["project_id"] == run["project_id"]]
            if newer and all(r["status"] == "published" for r in newer):
                break
            if any(r["status"] in ("needs_human", "cancelled") for r in newer):
                raise SystemExit("Feedback needs human; inspect run")
            time.sleep(5)
        else:
            raise SystemExit("Feedback timeout")
        after = gl.get(f"projects/{run['project_id']}/merge_requests", params={"source_branch": test_mr["source_branch"], "state": "opened"}).json()
        assert len(after) == 1 and after[0]["iid"] == test_mr["iid"]
        result["feedback"] = {"award_id": award.json()["id"], "test_mr_iid": test_mr["iid"], "runs": newer}
    Path("artifacts/cloud-acceptance.json").write_text(json.dumps(result, indent=2))
    print("Recorded actual evidence in artifacts/cloud-acceptance.json. No MR was merged.")


if __name__ == "__main__":
    main()
