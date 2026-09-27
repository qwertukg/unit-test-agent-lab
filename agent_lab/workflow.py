import json
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agent_lab.actions import Actions
from agent_lab.contracts import Blocked, BudgetExceeded, Report, Review, Scenarios, Tests
from agent_lab.policy import POLICY_VERSION, eligible, validate_tests
from agent_lab.repository import Repository, publish_git


class State(TypedDict, total=False):
    run_id: str
    project_id: int
    mr_iid: int
    source_sha: str
    trigger: str
    iteration: int
    infra_attempt: int
    source_pipeline: int
    previous_run: str
    route: str
    candidate_sha: str
    candidate_ref: str
    pipeline_id: int
    feedback_cursor: int
    result: str


class Workflow:
    def __init__(self, settings, store, gitlab, model, project, lock_conn=None):
        self.settings, self.store, self.gitlab, self.model, self.project = settings, store, gitlab, model, project
        self.lock_conn = lock_conn

    def guard(self, state):
        if self.lock_conn is not None:
            self.lock_conn.execute("SELECT 1")  # Fail closed if session lock was lost.
        row = self.store.query("UPDATE lab_runs SET started_at=coalesce(started_at,now()) WHERE id=%s RETURNING *",
                               (state["run_id"],), one=True)
        if row["cancel_requested"]:
            raise Blocked("cancelled by operator")
        if (datetime.now(timezone.utc) - row["started_at"]).total_seconds() > self.settings.max_run_seconds:
            raise BudgetExceeded("run wall-clock limit")
        self.store.query("UPDATE lab_runs SET lease_until=now()+interval '180 seconds',updated_at=now(),iteration=%s WHERE id=%s",
                         (state["iteration"], state["run_id"]))

    def actions(self, state):
        return Actions(self.store, state["run_id"], lambda: self.guard(state), self.settings.max_external_attempts)

    def repo(self, state):
        path = self.settings.workspace_root / str(state["project_id"]) / state["run_id"] / state["source_sha"]
        if (path / ".snapshot-ready").exists():
            return Repository(path)
        return Repository.unpack(path, self.gitlab.snapshot(state["project_id"], state["source_sha"]))

    def context(self, state):
        rows = self.store.latest(state["run_id"])
        return {"policy_version": POLICY_VERSION, "run_id": state["run_id"], "iteration": state["iteration"],
                "source_sha": state["source_sha"], "project": self.project.model_dump(mode="json"),
                "objects": {k: {"version": v["version"], "content_hash": v["content_hash"],
                                 "source_sha": v["source_sha"], "test_sha": v["test_sha"],
                                 "payload": v["payload"]} for k, v in rows.items()}}

    def save(self, state, object_id, payload, producer, slot=None, test_sha=None):
        inputs = {k: {"id": v["id"], "hash": v["content_hash"]} for k, v in self.store.latest(state["run_id"]).items()}
        return self.store.object(state, object_id, slot or f"{producer}:{state['iteration']}", payload, producer,
                                 inputs, test_sha)

    def feedback(self, state, slot):
        rows = self.store.query("SELECT * FROM lab_feedback WHERE project_id=%s AND mr_iid=%s ORDER BY seq",
                                (state["project_id"], state["mr_iid"]))
        if rows:
            obj = self.save(state, "O9", {"events": [r["payload"] for r in rows], "cursor": rows[-1]["seq"]},
                            "feedback", slot)
            return obj["payload"]["cursor"]
        return 0

    def freshness(self, state):
        mr = self.gitlab.mr(state["project_id"], state["mr_iid"])
        if mr["state"] != "opened":
            raise Blocked("source MR closed/merged")
        if mr["sha"] != state["source_sha"]:
            # Persist a fresh-source check before ending the obsolete run.
            self.store.event(f"refresh:{state['run_id']}:{mr['sha']}", "refresh", state["project_id"],
                             {"mr_iid": state["mr_iid"], "feedback": state["trigger"] != "coverage",
                              "previous_run": state["run_id"], "sha": mr["sha"]})
            raise Blocked("source SHA changed; fresh eligibility check queued")
        return mr

    def collect(self, state):
        self.guard(state)
        mr = self.freshness(state)
        if (mr["target_branch"] != self.project.target_branch
                or not mr["source_branch"].startswith(self.project.source_prefix)
                or mr["source_project_id"] != mr["target_project_id"]):
            raise Blocked("source MR no longer allowed")
        repo = self.repo(state)
        repo.verify_config(self.project)
        reports = self.gitlab.reports(self.project, state["source_pipeline"], state["source_sha"])
        if reports.get("infra_error"):
            raise Blocked("source CI infrastructure failure")
        report = Report.model_validate(reports["report"])
        if state["trigger"] == "coverage" and not eligible(mr, report, self.project):
            raise Blocked("source coverage eligibility no longer satisfied")
        self.save(state, "O1", self.gitlab.diff(state["project_id"], mr), "collector", "source")
        self.save_reports(state, reports, "source")
        task = json.loads(self.project.task_fixture.read_text(encoding="utf-8"))
        self.save(state, "O4", {"adapter": "demo_fixture", **task}, "collector", "source")
        self.save(state, "O5", {"sha": state["source_sha"], "paths": repo.files(),
                               "config": json.loads(repo.read(self.project.config_path)),
                               "workspace": str(repo.root), "policy_version": POLICY_VERSION}, "collector", "source")
        route = "analyst"
        previous = state.get("previous_run")
        if previous:
            latest_link = self.store.query("SELECT last_run_id FROM lab_links WHERE project_id=%s AND mr_iid=%s",
                                           (state["project_id"], state["mr_iid"]), one=True)
            if latest_link:
                previous = latest_link["last_run_id"]
        updates = {}
        if previous:
            old = self.store.latest(previous)
            for oid in ("O6", "O7", "O8"):
                if oid in old:
                    self.save(state, oid, {**old[oid]["payload"], "inherited_from": previous},
                              "collector", "inherited", old[oid]["test_sha"])
            if "O7" in old and old["O7"]["source_sha"] == state["source_sha"] and old["O7"]["test_sha"]:
                for oid in ("O2", "O3"):
                    self.save(state, oid, old[oid]["payload"], "collector", "inherited-validation", old[oid]["test_sha"])
                updates = {"candidate_sha": old["O7"]["test_sha"],
                           "pipeline_id": old["O2"]["payload"]["pipeline_id"]}
                route = "reviewer"
        cursor = self.feedback(state, "collect")
        return {"route": route, "feedback_cursor": cursor, **updates}

    def save_reports(self, state, result, slot):
        report = result["report"]
        artifact_links = {path: self.settings.gitlab_public_url + "/" + result["job"]["web_url"].split("/", 3)[-1]
                          + "/artifacts/file/" + path for path in result["artifacts"]}
        common = {"sha": report["sha"], "source_sha": report["source_sha"], "origin": slot,
                  "pipeline_id": report["pipeline_id"], "job_id": report["job_id"], "links": artifact_links,
                  "pipeline_url": result["pipeline"]["web_url"]}
        self.save(state, "O2", {**common, "status": report["status"], "tests": report["tests"],
                               "failures": report["failures"], "errors": report["errors"],
                               "log": result["artifacts"].get(report["log_path"], "")[-12000:]},
                  "Runner", slot, report["sha"] if slot != "source" else None)
        self.save(state, "O3", {**common, "coverage": report["coverage"], "diff_coverage": report["diff_coverage"],
                               "threshold": report["threshold"], "metric": report["metric"], "gate": report["gate"],
                               "tool": report["tool"]}, "Runner", slot, report["sha"] if slot != "source" else None)

    def analyst(self, state):
        self.guard(state)
        self.freshness(state)
        if state["iteration"] >= self.settings.max_iterations:
            raise BudgetExceeded("iteration limit")
        repo = self.repo(state)
        result = self.model.generate("Analyst", state, self.context(state), repo, Scenarios, lambda: self.guard(state))
        self.save(state, "O6", {**result.model_dump(), "used_paths": sorted(repo.used)}, "Analyst")
        return {"route": "coder"}

    def coder(self, state):
        self.guard(state)
        self.freshness(state)
        iteration = state["iteration"] + 1
        if iteration > self.settings.max_iterations:
            raise BudgetExceeded("iteration limit")
        current = {**state, "iteration": iteration}
        repo = self.repo(state)
        result = self.model.generate("Coder", current, self.context(current), repo, Tests, lambda: self.guard(state))
        scenarios = Scenarios.model_validate({"scenarios": self.store.latest(state["run_id"])["O6"]["payload"]["scenarios"]})
        validate_tests(result, scenarios, self.project, set(repo.files()))
        self.save(current, "O7", {**result.model_dump(), "used_paths": sorted(repo.used)}, "Coder")
        return {"iteration": iteration, "infra_attempt": 0, "route": "runner"}

    def runner(self, state):
        self.guard(state)
        self.freshness(state)
        actions = self.actions(state)
        iteration, attempt = state["iteration"], state.get("infra_attempt", 0)
        oid = self.store.latest(state["run_id"])["O7"]["payload"]
        ref = f"agent-verify/{state['mr_iid']}/{state['run_id']}/{iteration}-{attempt}"
        key = f"{state['run_id']}:candidate:{iteration}:{attempt}"
        commit = actions.perform(key, "commit", {"ref": ref, "source_sha": state["source_sha"], "files": oid["files"]},
            lambda: self.gitlab.commit_on_ref(state["project_id"], ref, key),
            lambda: self.gitlab.create_candidate(state["project_id"], ref, state["source_sha"], oid["files"], key))
        sha = commit["id"]
        self.gitlab.verify_candidate(state["project_id"], state["source_sha"], sha, oid["files"])
        self.save(state, "O7", {**oid, "commit_sha": sha, "ref": ref}, "Runner", f"candidate:{iteration}:{attempt}", sha)
        pipeline = actions.observe_pipeline(key + ":pipeline", {"ref": ref, "sha": sha},
            lambda: self.gitlab.pipeline_on_ref(state["project_id"], ref, sha))
        result = self.gitlab.reports(self.project, pipeline["id"], sha)
        if result.get("infra_error") or result.get("report", {}).get("status") == "infra_error":
            self.save(state, "O2", {"status": "infra_error", "sha": sha, "pipeline_id": pipeline["id"],
                                   "job_id": result["job"]["id"]}, "Runner", f"infra:{iteration}:{attempt}", sha)
            self.save(state, "O3", {"gate": "unknown", "coverage": None, "sha": sha}, "Runner",
                      f"infra:{iteration}:{attempt}", sha)
            if attempt >= self.settings.max_infra_retries:
                raise BudgetExceeded("Runner infrastructure retry limit")
            return {"infra_attempt": attempt + 1, "route": "runner"}
        if result["report"]["source_sha"] != state["source_sha"]:
            raise Blocked("candidate was not tested against pinned source")
        self.save_reports(state, result, f"verification:{iteration}:{attempt}")
        route = "coder" if result["report"]["status"] == "build_error" else "reviewer"
        return {"candidate_sha": sha, "candidate_ref": ref, "pipeline_id": pipeline["id"], "route": route}

    def reviewer(self, state):
        self.guard(state)
        self.freshness(state)
        cursor = self.feedback(state, f"review:{state['iteration']}")
        repo = self.repo(state)
        result = self.model.generate("Reviewer", state, self.context(state), repo, Review, lambda: self.guard(state))
        available = set(self.store.latest(state["run_id"]))
        if not available <= {f.object_id for f in result.findings}:
            raise Blocked("Reviewer omitted available objects")
        self.save(state, "O8", {**result.model_dump(), "used_paths": sorted(repo.used)}, "Reviewer")
        rows = self.store.latest(state["run_id"])
        if result.product_defect:
            return {"route": "stop", "result": "needs_human: product defect", "feedback_cursor": cursor}
        if rows["O2"]["payload"]["status"] == "assertion_failure" and result.decision == "publish":
            return {"route": "stop", "result": "needs_human: failing assertions", "feedback_cursor": cursor}
        return {"route": {"publish": "publish", "revise_tests": "coder", "revise_scenarios": "analyst"}[result.decision],
                "feedback_cursor": cursor}

    def publish(self, state):
        self.guard(state)
        mr = self.freshness(state)
        rows = self.store.latest(state["run_id"])
        test, coverage = rows["O2"]["payload"], rows["O3"]["payload"]
        if (test["sha"] != state["candidate_sha"] or coverage["sha"] != state["candidate_sha"]
                or test["status"] != "passed" or test["tests"] == 0 or test["failures"] or test["errors"]
                or coverage["gate"] != "passed" or not rows["O7"]["payload"]["files"]):
            raise Blocked("deterministic publication gate rejected stale/failing/missing evidence")
        # Re-read real pipeline and artifacts just before publication.
        self.gitlab.verify_candidate(state["project_id"], state["source_sha"], state["candidate_sha"], rows["O7"]["payload"]["files"])
        live = self.gitlab.reports(self.project, state["pipeline_id"], state["candidate_sha"])
        if live.get("infra_error") or live["report"]["gate"] != "passed" or live["report"]["status"] != "passed":
            raise Blocked("live verification gate failed")
        link = self.store.query("SELECT * FROM lab_links WHERE project_id=%s AND mr_iid=%s",
                                (state["project_id"], state["mr_iid"]), one=True)
        if link and self.gitlab.mr(state["project_id"], link["test_mr_iid"])["state"] != "opened":
            raise Blocked("test MR closed/merged; not reopening")
        project_data = self.gitlab.get(f"projects/{state['project_id']}")
        branch = self.project.generated_prefix + str(state["mr_iid"])
        existing = self.gitlab.branch(state["project_id"], branch)
        expected = existing["commit"]["id"] if existing else None
        actions = self.actions(state)
        key = f"{state['run_id']}:publish:{state['iteration']}"

        def pushed():
            current = self.gitlab.branch(state["project_id"], branch)
            return {"sha": state["candidate_sha"], "branch": branch} if current and current["commit"]["id"] == state["candidate_sha"] else None

        # Expected head is persisted before push, then reused after crashes.
        saved = self.store.query("SELECT intent FROM lab_actions WHERE action_key=%s", (key,), one=True)
        if saved:
            expected = saved["intent"]["expected_sha"]
        elif existing and not link and existing["commit"]["id"] != state["candidate_sha"]:
            raise Blocked("unowned publication branch already exists")
        elif existing and link and existing["commit"]["id"] != state["candidate_sha"]:
            previous_tests = self.store.latest(link["last_run_id"]).get("O7")
            if not previous_tests or existing["commit"]["id"] != previous_tests["test_sha"]:
                raise Blocked("test branch has external edits; refusing to overwrite")
        intent = self.store.intent(key, state["run_id"], "push", {"branch": branch, "sha": state["candidate_sha"], "expected_sha": expected})
        actions.perform(key, "push", intent["intent"], pushed,
                        lambda: publish_git(self.settings, project_data["path_with_namespace"], state["candidate_sha"],
                                            branch, expected, state["run_id"]))
        self.freshness(state)
        marker = f"<!-- unit-test-agent:{state['project_id']}:{state['mr_iid']} -->"
        description = (f"{marker}\nSource MR !{state['mr_iid']}\nSource SHA `{state['source_sha']}`\n"
                       f"Test SHA `{state['candidate_sha']}`\nTask: {rows['O4']['payload']['id']}\n"
                       f"Pipeline: {test['pipeline_url']}\n\nReviewer:\n{json.dumps(rows['O8']['payload'], ensure_ascii=False, indent=2)}"
                       f"\n\nArtifacts:\n{json.dumps(test['links'], indent=2)}\n\nHuman review and merge required.")

        def find_mr():
            found = self.gitlab.all(f"projects/{state['project_id']}/merge_requests", source_branch=branch,
                                   target_branch=mr["source_branch"], state="all")
            matching = [m for m in found if marker in (m.get("description") or "")]
            if found and not matching:
                raise Blocked("unowned MR already uses publication branch")
            if len(matching) > 1:
                raise Blocked("duplicate test MR identity")
            if matching and matching[0]["state"] != "opened":
                raise Blocked("test MR closed/merged; not reopening")
            return matching[0] if matching else None

        result = actions.perform(key + ":mr", "merge_request", {"source": branch, "target": mr["source_branch"], "marker": marker},
                                 find_mr, lambda: self.gitlab.post(f"projects/{state['project_id']}/merge_requests", {
                                     "source_branch": branch, "target_branch": mr["source_branch"],
                                     "title": f"Unit tests for !{state['mr_iid']}", "description": description,
                                     "remove_source_branch": False}))
        self.guard(state)
        def updated_description():
            existing_mr = self.gitlab.mr(state["project_id"], result["iid"])
            return existing_mr if existing_mr.get("description") == description else None

        actions.perform(key + ":description", "update_mr", {"iid": result["iid"], "description": description},
                        updated_description, lambda: self.gitlab.put(
                            f"projects/{state['project_id']}/merge_requests/{result['iid']}", {"description": description}))
        self.store.query("""INSERT INTO lab_links(project_id,mr_iid,author_id,test_mr_iid,source_branch,last_run_id)
            VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(project_id,mr_iid) DO UPDATE SET last_run_id=excluded.last_run_id""",
            (state["project_id"], state["mr_iid"], mr["author"]["id"], result["iid"], mr["source_branch"], state["run_id"]))
        self.freshness(state)
        return {"result": "published", "route": "stop"}

    def stop(self, state):
        return {"result": state.get("result", "needs_human")}

    def graph(self, checkpointer):
        graph = StateGraph(State)
        for node in ("collect", "analyst", "coder", "runner", "reviewer", "publish", "stop"):
            graph.add_node(node, getattr(self, node))
        graph.add_edge(START, "collect")
        graph.add_conditional_edges("collect", lambda s: s["route"], {"analyst": "analyst", "reviewer": "reviewer"})
        graph.add_edge("analyst", "coder")
        graph.add_edge("coder", "runner")
        graph.add_conditional_edges("runner", lambda s: s["route"], {x: x for x in ("runner", "coder", "reviewer")})
        graph.add_conditional_edges("reviewer", lambda s: s["route"], {x: x for x in ("analyst", "coder", "publish", "stop")})
        graph.add_edge("publish", "stop")
        graph.add_edge("stop", END)
        return graph.compile(checkpointer=checkpointer)
