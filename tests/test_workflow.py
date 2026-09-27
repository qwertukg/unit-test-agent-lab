import pytest
from langgraph.checkpoint.postgres import PostgresSaver

from agent_lab.config import Settings
from agent_lab.contracts import Blocked, BudgetExceeded, PolicyError, RetryLater
from agent_lab.events import Events
from agent_lab.workflow import Workflow
from tests.fakes import FakeGitLab, FakeModel


def build(store, project, mr, report, tmp_path, monkeypatch, decisions=None, **model_flags):
    gitlab = FakeGitLab(mr, report, project)
    model = FakeModel(decisions, **model_flags)
    settings = Settings(_env_file=None, workspace_root=tmp_path, max_run_seconds=3600)
    monkeypatch.setattr("agent_lab.workflow.publish_git", gitlab.push)
    run = store.new_run(project.id, mr["iid"], mr["sha"], "coverage", {"source_pipeline": 41})
    workflow = Workflow(settings, store, gitlab, model, project)
    return settings, gitlab, model, run, workflow


def test_queue_wait_does_not_spend_runtime_but_restart_never_resets_deadline(store, project, mr, report, tmp_path, monkeypatch):
    _, _, _, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch)
    store.query("UPDATE lab_runs SET created_at=now()-interval '2 days' WHERE id=%s", (run,))
    state = store.run(run)["initial_state"]
    workflow.guard(state)
    started = store.run(run)["started_at"]
    store.setup()
    workflow.guard(state)
    assert store.run(run)["started_at"] == started
    store.query("UPDATE lab_runs SET started_at=now()-interval '2 hours',attempts=1 WHERE id=%s", (run,))
    store.setup()
    with pytest.raises(BudgetExceeded, match="wall-clock"):
        workflow.guard(state)


@pytest.mark.parametrize("decisions,counts", [(["publish"], (1, 1, 1)), (["revise_tests", "publish"], (1, 2, 2)),
                                               (["revise_scenarios", "publish"], (2, 2, 2))])
def test_actual_langgraph_routes_with_explicit_doubles(store, project, mr, report, tmp_path, monkeypatch, decisions, counts):
    _, gitlab, model, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch, decisions)
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        graph = workflow.graph(saver)
        result = graph.invoke(store.run(run)["initial_state"], {"configurable": {"thread_id": run}})
    assert result["result"] == "published"
    assert tuple(model.calls.count(r) for r in ("Analyst", "Coder", "Reviewer")) == counts
    assert gitlab.created_mrs == 1
    assert gitlab.test_mrs[0]["target_branch"] == mr["source_branch"]
    assert set(store.latest(run)) == {f"O{i}" for i in range(1, 9)}
    assert store.latest(run)["O2"]["payload"]["sha"] == result["candidate_sha"]


def test_restart_while_job_running_keeps_one_commit_and_pipeline(store, project, mr, report, tmp_path, monkeypatch):
    _, gitlab, model, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch)
    gitlab.wait_once = True
    cfg = {"configurable": {"thread_id": run}}
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        with pytest.raises(RetryLater):
            workflow.graph(saver).invoke(store.run(run)["initial_state"], cfg)
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        result = workflow.graph(saver).invoke(None, cfg)
    assert result["result"] == "published"
    assert gitlab.created_commits == gitlab.created_mrs == 1
    assert model.calls == ["Analyst", "Coder", "Reviewer"]
    assert len(store.query("SELECT * FROM lab_actions WHERE kind='pipeline'")) == 1


def test_build_failure_returns_coder_but_correct_failing_test_stops(store, project, mr, report, tmp_path, monkeypatch):
    _, gitlab, model, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch)
    gitlab.build_failures = 1
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        assert workflow.graph(saver).invoke(store.run(run)["initial_state"], {"configurable": {"thread_id": run}})["result"] == "published"
    assert model.calls == ["Analyst", "Coder", "Coder", "Reviewer"]


def test_product_defect_never_publishes_success(store, project, mr, report, tmp_path, monkeypatch):
    _, gitlab, _, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch, defect=True)
    gitlab.failed_assertions = True
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        result = workflow.graph(saver).invoke(store.run(run)["initial_state"], {"configurable": {"thread_id": run}})
    assert result["result"].startswith("needs_human")
    assert gitlab.created_mrs == 0
    assert store.latest(run)["O2"]["payload"]["failures"] == 1


def test_infrastructure_retries_are_bounded_same_manifest(store, project, mr, report, tmp_path, monkeypatch):
    _, gitlab, model, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch)
    gitlab.infrastructure_failures = 10
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        with pytest.raises(BudgetExceeded, match="infrastructure retry limit"):
            workflow.graph(saver).invoke(store.run(run)["initial_state"], {"configurable": {"thread_id": run}})
    assert gitlab.created_commits == 3
    assert len({str(c["files"]) for c in gitlab.candidates.values()}) == 1
    assert model.calls == ["Analyst", "Coder"]
    assert gitlab.created_mrs == 0


def test_model_injection_cannot_edit_product(store, project, mr, report, tmp_path, monkeypatch):
    _, gitlab, _, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch, bad_tests=True)
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        with pytest.raises(PolicyError):
            workflow.graph(saver).invoke(store.run(run)["initial_state"], {"configurable": {"thread_id": run}})
    assert gitlab.created_commits == 0


def test_stale_source_queues_recheck_without_publication(store, project, mr, report, tmp_path, monkeypatch):
    _, gitlab, _, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch)
    state = store.run(run)["initial_state"]
    gitlab.source["sha"] = "c" * 40
    with pytest.raises(Blocked, match="source SHA changed"):
        workflow.freshness(state)
    event = store.query("SELECT * FROM lab_events", one=True)
    assert event["kind"] == "refresh"
    assert gitlab.created_mrs == 0


def test_author_feedback_duplicate_and_late_comment_same_mr(store, project, mr, report, tmp_path, monkeypatch):
    settings, gitlab, model, run, workflow = build(store, project, mr, report, tmp_path, monkeypatch)
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        workflow.graph(saver).invoke(store.run(run)["initial_state"], {"configurable": {"thread_id": run}})
    events = Events(settings, store, gitlab, {project.id: project})
    store.event("foreign", "award", project.id, {"mr_iid": 3, "test_mr_iid": 20, "id": 1, "user": {"id": 8}})
    store.event("author", "award", project.id, {"mr_iid": 3, "test_mr_iid": 20, "id": 2, "user": {"id": 7}})
    assert store.event("author", "award", project.id, {}) is None
    events.drain()
    assert len(store.query("SELECT * FROM lab_runs")) == 2
    assert "not source MR author" in store.query("SELECT disposition FROM lab_events WHERE event_key='foreign'", one=True)["disposition"]
    feedback_run = store.query("SELECT * FROM lab_runs WHERE trigger='author'", one=True)
    # QG passes now: feedback MUST still run.
    gitlab.source_report.update(gate="passed", coverage=100, diff_coverage=100)
    model.decisions = ["revise_tests", "publish"]
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        result = workflow.graph(saver).invoke(feedback_run["initial_state"], {"configurable": {"thread_id": feedback_run["id"]}})
    assert result["result"] == "published"
    assert gitlab.created_mrs == 1
    assert "O9" in model.contexts[-1]["objects"]
    store.event("late", "note", project.id, {"mr_iid": 3, "test_mr_iid": 20, "id": 3, "author": {"id": 7}, "body": "@unit-test-agent boundary"})
    events.drain()
    assert store.query("SELECT count(*) AS n FROM lab_feedback", one=True)["n"] == 2
    assert store.query("SELECT count(*) AS n FROM lab_runs", one=True)["n"] == 3


def test_malformed_event_does_not_starve_later_events(store, project, mr, report, tmp_path, monkeypatch):
    settings, gitlab, _, _, _ = build(store, project, mr, report, tmp_path, monkeypatch)
    events = Events(settings, store, gitlab, {project.id: project})
    store.event("bad-schema", "pipeline", project.id, {})
    store.event("valid-schema", "pipeline", project.id, {"mr_iid": 3, "pipeline_id": 41})
    events.drain()
    rows = store.query("SELECT disposition FROM lab_events ORDER BY seq")
    assert rows[0]["disposition"] == "blocked: invalid event or report schema"
    assert rows[1]["disposition"].startswith("queued coverage")
