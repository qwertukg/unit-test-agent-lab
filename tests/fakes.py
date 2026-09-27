"""Explicit test doubles. These are NOT evidence of real GitLab/cloud acceptance."""
import io
import tarfile
from copy import deepcopy
from pathlib import Path

from agent_lab.contracts import RetryLater, digest


class FakeGitLab:
    def __init__(self, mr, report, project):
        self.source = deepcopy(mr)
        self.source_report = report.model_dump()
        self.project = project
        self.branches = {}
        self.test_mrs = []
        self.created_commits = 0
        self.created_mrs = 0
        self.candidates = {}
        self.wait_once = False
        self.failed_assertions = False
        self.build_failures = 0
        self.infrastructure_failures = 0
        self.reads = []
        self.files = {"product/pricing.py": Path("demo/python/product/pricing.py").read_text(),
                      "tests/test_baseline.py": Path("demo/python/tests/test_baseline.py").read_text(),
                      ".test-agent.json": Path("demo/python/.test-agent.json").read_text()}
        project.trusted_files = {".test-agent.json": digest(self.files[".test-agent.json"].encode())}

    def mr(self, project, iid):
        if iid == self.source["iid"]:
            return deepcopy(self.source)
        return deepcopy(next(m for m in self.test_mrs if m["iid"] == iid))

    def snapshot(self, project, sha):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            for path, content in self.files.items():
                data = content.encode()
                info = tarfile.TarInfo("repo/" + path)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return stream.getvalue()

    def diff(self, project, mr):
        return {"base_sha": "b" * 40, "head_sha": mr["sha"], "files": [{"new_path": "product/pricing.py", "diff": "+discounts"}]}

    def reports(self, project, pipeline_id, sha):
        self.reads.append((pipeline_id, sha))
        if sha == self.source["sha"]:
            report = deepcopy(self.source_report)
        else:
            if self.wait_once:
                self.wait_once = False
                raise RetryLater("mock CI running")
            report = {**self.source_report, "sha": sha, "source_sha": self.source["sha"], "pipeline_id": pipeline_id,
                      "job_id": pipeline_id + 100, "tests": 11, "coverage": 100, "diff_coverage": 100, "gate": "passed"}
            if self.failed_assertions:
                report.update(status="assertion_failure", failures=1)
            if self.build_failures:
                report.update(status="build_error", errors=1)
                self.build_failures -= 1
            if self.infrastructure_failures:
                self.infrastructure_failures -= 1
                return {"infra_error": True, "job": {"id": pipeline_id + 100}, "pipeline": {"id": pipeline_id}}
        return {"report": report, "job": {"id": report["job_id"], "web_url": f"http://localhost:8929/demo/-/jobs/{report['job_id']}"},
                "pipeline": {"id": pipeline_id, "web_url": f"http://localhost:8929/demo/-/pipelines/{pipeline_id}"},
                "artifacts": {"reports/run.log": "actual execution is stubbed in this component test", "reports/junit.xml": "stub", "reports/coverage.xml": "stub"}}

    def branch(self, project, branch):
        return deepcopy(self.branches.get(branch))

    def commit_on_ref(self, project, ref, marker):
        old = self.branch(project, ref)
        return old["commit"] if old else None

    def create_candidate(self, project, ref, source_sha, files, marker):
        self.created_commits += 1
        commit = {"id": digest([ref, files])[:40], "message": marker}
        self.branches[ref] = {"commit": commit}
        self.candidates[commit["id"]] = {"source": source_sha, "files": deepcopy(files)}
        return commit

    def verify_candidate(self, project, source_sha, test_sha, files):
        assert self.candidates[test_sha] == {"source": source_sha, "files": files}

    def pipeline_on_ref(self, project, ref, sha):
        return {"id": 1000 + list(self.branches).index(ref), "sha": sha, "status": "success"}

    def latest_source_pipeline(self, project, mr):
        return {"id": 41, "sha": mr["sha"], "status": "failed"}

    def get(self, path):
        if "/pipelines/" in path:
            return self.latest_source_pipeline(self.project.id, self.source)
        return {"path_with_namespace": "demo/pricing"}

    def all(self, path, **params):
        return deepcopy(self.test_mrs)

    def post(self, path, data):
        self.created_mrs += 1
        result = {**data, "iid": 20, "state": "opened", "web_url": "http://localhost:8929/demo/-/merge_requests/20"}
        self.test_mrs.append(result)
        return deepcopy(result)

    def put(self, path, data):
        self.test_mrs[0].update(data)
        return deepcopy(self.test_mrs[0])

    def push(self, settings, project_path, sha, branch, expected, run_id):
        current = self.branches.get(branch, {}).get("commit", {}).get("id")
        assert current == expected
        self.branches[branch] = {"commit": {"id": sha}}
        return {"sha": sha, "branch": branch}


class FakeModel:
    def __init__(self, decisions=None, bad_tests=False, defect=False):
        self.calls = []
        self.decisions = list(decisions or ["publish"])
        self.bad_tests = bad_tests
        self.defect = defect
        self.contexts = []

    def generate(self, role, state, context, repo, schema, guard):
        guard()
        self.calls.append(role)
        self.contexts.append(deepcopy(context))
        repo.read("product/pricing.py")
        if role == "Analyst":
            return schema.model_validate({"scenarios": [{"id": "S1", "purpose": "discounts", "requirements": ["R2"],
                "paths": ["product/pricing.py"], "setup": "none", "inputs": "1000 SAVE10", "expected": "900",
                "kind": "normal", "allowed_mocks": []}]})
        if role == "Coder":
            return schema.model_validate({"files": [{"path": "product/pricing.py" if self.bad_tests else "tests/test_agent_pricing.py",
                "content": "from product.pricing import total\ndef test_discount():\n    assert total(1000, False, 'SAVE10') == 900\n",
                "scenario_ids": ["S1"]}], "rationale": "mock fixture"})
        decision = self.decisions.pop(0) if self.decisions else "publish"
        return schema.model_validate({"decision": decision, "findings": [{"object_id": k, "assessment": "mock"} for k in context["objects"]],
                                      "rationale": "mock", "product_defect": self.defect, "evidence": ["mock pipeline"]})
