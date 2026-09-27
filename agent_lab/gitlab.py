import json
from urllib.parse import quote

import httpx

from agent_lab.contracts import Blocked, Report, RetryLater


class GitLab:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.client = httpx.Client(base_url=settings.gitlab_url.rstrip("/") + "/api/v4/",
                                   headers={"PRIVATE-TOKEN": settings.gitlab_token.get_secret_value()},
                                   timeout=45, transport=transport)

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise RetryLater("GitLab transport unavailable") from e
        if response.status_code in (429, 502, 503, 504):
            raise RetryLater("GitLab temporarily unavailable")
        if response.status_code >= 400:
            raise Blocked(f"GitLab {method} {path.split('?')[0]} HTTP {response.status_code}")
        return response

    def get(self, path, **params):
        return self.request("GET", path, params=params).json()

    def post(self, path, data):
        return self.request("POST", path, json=data).json()

    def put(self, path, data):
        return self.request("PUT", path, json=data).json()

    def all(self, path, **params):
        result = []
        for page in range(1, 101):
            r = self.request("GET", path, params={**params, "page": page, "per_page": 100})
            result.extend(r.json())
            if not r.headers.get("x-next-page"):
                return result
        raise Blocked("pagination budget exhausted")

    def mr(self, project, iid):
        return self.get(f"projects/{project}/merge_requests/{iid}")

    def branch(self, project, name):
        path = f"projects/{project}/repository/branches/{quote(name, safe='')}"
        r = self.client.get(path)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise RetryLater("branch lookup failed")
        return r.json()

    def commit_on_ref(self, project, ref, marker):
        branch = self.branch(project, ref)
        if branch is None:
            return None
        commit = branch["commit"]
        if marker not in commit["message"]:
            raise Blocked("verification ref identity conflict")
        return commit

    def create_candidate(self, project, ref, source_sha, files, marker):
        return self.post(f"projects/{project}/repository/commits", {
            "branch": ref, "start_sha": source_sha, "commit_message": marker,
            "actions": [{"action": "create", "file_path": f["path"], "content": f["content"]} for f in files]})

    def verify_candidate(self, project, source_sha, test_sha, files):
        """Validate the actual remote tree, including reconciliation of an existing ref."""
        commit = self.get(f"projects/{project}/repository/commits/{test_sha}")
        if commit.get("parent_ids") != [source_sha]:
            raise Blocked("candidate parent differs from exact source SHA")
        comparison = self.get(f"projects/{project}/repository/compare", **{"from": source_sha, "to": test_sha, "straight": "true"})
        diffs = comparison.get("diffs", [])
        expected = {f["path"]: f["content"] for f in files}
        if comparison.get("compare_timeout") or {d["new_path"] for d in diffs} != set(expected):
            raise Blocked("actual candidate diff differs from approved test manifest")
        for diff in diffs:
            if not diff.get("new_file") or diff.get("deleted_file") or diff.get("renamed_file") or diff.get("too_large") or diff.get("collapsed"):
                raise Blocked("actual candidate changed existing files or has incomplete diff")
            response = self.request("GET", f"projects/{project}/repository/files/{quote(diff['new_path'], safe='')}/raw", params={"ref": test_sha})
            if response.content != expected[diff["new_path"]].encode("utf-8"):
                raise Blocked("actual candidate content differs from approved test manifest")

    def pipeline_on_ref(self, project, ref, sha):
        rows = self.all(f"projects/{project}/pipelines", ref=ref, sha=sha, source="push")
        if len(rows) > 1:
            raise Blocked("multiple pipelines for immutable ref")
        return rows[0] if rows else None

    def latest_source_pipeline(self, project, mr):
        rows = self.all(f"projects/{project}/pipelines", ref=mr["source_branch"], sha=mr["sha"])
        return max(rows, key=lambda r: r["id"]) if rows else None

    def reports(self, project, pipeline_id, sha):
        pipeline = self.get(f"projects/{project.id}/pipelines/{pipeline_id}")
        if pipeline["sha"] != sha:
            raise Blocked("pipeline SHA mismatch")
        if pipeline["status"] not in ("success", "failed", "canceled", "skipped"):
            raise RetryLater("CI still running")
        jobs = self.all(f"projects/{project.id}/pipelines/{pipeline_id}/jobs", include_retried="false")
        unit = [j for j in jobs if j["name"] == project.report_job]
        if len(unit) != 1:
            raise Blocked("missing/ambiguous report job")
        job = unit[0]
        if job["status"] in ("pending", "running", "created", "preparing"):
            raise RetryLater("CI job still running")
        if job["status"] == "canceled" or job.get("failure_reason") in (
                "runner_system_failure", "stuck_or_timeout_failure", "scheduler_failure"):
            return {"infra_error": True, "job": job, "pipeline": pipeline}
        raw = self.artifact(project.id, job["id"], project.report_path)
        report = Report.model_validate(json.loads(raw))
        from agent_lab.policy import consistent
        if not consistent(report, sha, pipeline_id, job["id"], project):
            raise Blocked("report provenance/configuration mismatch")
        # Verify actual artifact existence, not merely links from an untrusted summary.
        artifacts = {}
        for path in [*report.junit_paths, report.coverage_path, report.log_path]:
            if path:
                artifacts[path] = self.artifact(project.id, job["id"], path).decode("utf-8", errors="replace")
        return {"report": report.model_dump(), "job": job, "pipeline": pipeline, "artifacts": artifacts}

    def artifact(self, project, job, path):
        response = self.request("GET", f"projects/{project}/jobs/{job}/artifacts/{quote(path, safe='/')}")
        if len(response.content) > 5_000_000:
            raise Blocked("artifact too large")
        return response.content

    def snapshot(self, project, sha):
        response = self.request("GET", f"projects/{project}/repository/archive.tar.gz", params={"sha": sha})
        if len(response.content) > 30_000_000:
            raise Blocked("archive too large")
        return response.content

    def diff(self, project, mr):
        diffs = self.all(f"projects/{project}/merge_requests/{mr['iid']}/diffs")
        if any(d.get("too_large") or d.get("collapsed") for d in diffs):
            raise Blocked("incomplete MR diff")
        return {"base_sha": mr["diff_refs"]["base_sha"], "head_sha": mr["sha"], "files": diffs}
