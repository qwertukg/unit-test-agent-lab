"""Read-only acceptance of real GitLab/Runner baseline, without model calls."""
import argparse
import json
import time
from pathlib import Path

import httpx

from agent_lab.config import Settings, projects
from agent_lab.contracts import Report, RetryLater
from agent_lab.gitlab import GitLab
from agent_lab.policy import eligible


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    settings = Settings()
    if settings.gitlab_url.rstrip("/") not in ("http://localhost:8929", "http://127.0.0.1:8929"):
        raise SystemExit("Validation is restricted to the local lab")
    gl = GitLab(settings)
    project_map = projects(settings)
    evidence = json.loads(Path("artifacts/bootstrap.json").read_text())
    result = {"mode": "real Docker GitLab Runner; no model calls", "projects": []}
    bot = gl.get("user")
    assert bot["username"] == "unit-test-lab-bot" and not bot.get("is_admin", False)
    result["runtime_bot"] = {"id": bot["id"], "username": bot["username"], "is_admin": False}
    with httpx.Client(base_url="http://localhost:8000", timeout=20) as api:
        response = api.get("/ready")
        response.raise_for_status()
        result["api_ready"] = response.json()
        assert result["api_ready"]["projects"] == len(evidence) == 2
        assert api.get("/runs").status_code == 401
        response = api.get("/runs", headers={"Authorization": "Bearer " + settings.api_token.get_secret_value()})
        response.raise_for_status()
        result["authenticated_api"] = True
    for item in evidence:
        project = project_map[item["project_id"]]
        assert project.name.startswith("unit-test-agent-demo-")
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            mr = gl.mr(project.id, item["source_iid"])
            pipeline = gl.latest_source_pipeline(project.id, mr)
            if pipeline:
                try:
                    reports = gl.reports(project, pipeline["id"], mr["sha"])
                    break
                except RetryLater:
                    pass
            time.sleep(5)
        else:
            raise RuntimeError(f"baseline CI timeout: project {project.id}")
        assert not reports.get("infra_error"), f"infrastructure failure: project {project.id}"
        report = Report.model_validate(reports["report"])
        assert eligible(mr, report, project), f"baseline is not an eligible coverage failure: project {project.id}"
        assert pipeline["status"] == "failed" and reports["job"]["status"] == "failed"
        record = {"project_id": project.id, "stack": project.stack, "source_mr": item["source_mr"],
                  "source_sha": mr["sha"], "pipeline_id": pipeline["id"], "job_id": reports["job"]["id"],
                  "job_url": reports["job"]["web_url"], "eligible": True, "report": report.model_dump(),
                  "verified_artifacts": list(reports["artifacts"])}
        result["projects"].append(record)
        print(f"{project.stack}: real Runner tests={report.tests}, diff coverage={report.diff_coverage}, eligible=True", flush=True)
    result["status"] = "passed"
    Path("artifacts/infrastructure.json").write_text(json.dumps(result, indent=2))
    print("Actual evidence recorded in artifacts/infrastructure.json. Cloud E2E remains separate.")


if __name__ == "__main__":
    main()
