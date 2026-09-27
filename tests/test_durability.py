from concurrent.futures import ThreadPoolExecutor

import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agent_lab.actions import Actions
from agent_lab.contracts import BudgetExceeded
from agent_lab.store import Store


def make_run(store):
    run_id = store.new_run(12, 3, "a" * 40, "coverage", {"source_pipeline": 41})
    return store.run(run_id)["initial_state"]


def test_concurrent_event_and_run_deduplication(store):
    def create(_):
        store.event("same-event", "pipeline", 12, {"id": 41})
        return make_run(store)["run_id"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(create, range(16)))
    assert len(set(ids)) == 1
    assert len(store.query("SELECT * FROM lab_runs")) == 1
    assert len(store.query("SELECT * FROM lab_events")) == 1
    with store.lock(12, 3) as first:
        assert first is not None
        with store.lock(12, 3) as second:
            assert second is None
        with store.lock(13, 3) as other_project:
            assert other_project is not None


def test_external_write_crash_reconciles_without_duplicate(store):
    state = make_run(store)
    external = []

    def create():
        external.append({"id": 51})
        return external[0]

    def crash(key):
        raise RuntimeError("injected death after external success")

    a = Actions(store, state["run_id"], after_effect=crash)
    with pytest.raises(RuntimeError):
        a.perform("stable-mr", "mr", {"branch": "ai-tests/3"}, lambda: None, create)
    assert store.query("SELECT status FROM lab_actions", one=True)["status"] == "pending"
    restarted = Actions(Store(store.dsn), state["run_id"])
    assert restarted.perform("stable-mr", "mr", {"branch": "ai-tests/3"}, lambda: external[0], create)["id"] == 51
    assert len(external) == 1


def test_objects_are_versioned_and_provenance_is_immutable(store):
    state = make_run(store)
    first = store.object(state, "O2", "source", {"sha": "a" * 40}, "Runner")
    assert store.object(state, "O2", "source", {}, "Runner")["id"] == first["id"]
    second = store.object({**state, "iteration": 1}, "O2", "verify:1", {"sha": "b" * 40}, "Runner", test_sha="b" * 40)
    assert (first["version"], second["version"]) == (1, 2)
    assert first["source_sha"] == second["source_sha"]
    assert second["test_sha"] != first["test_sha"]
    assert store.latest(state["run_id"])["O2"]["version"] == 2


def test_ambiguous_model_call_retains_budget(store):
    state = make_run(store)
    store.reserve(state["run_id"], 600, 10, 1000, 5, 1)
    with pytest.raises(BudgetExceeded):
        store.reserve(state["run_id"], 600, 10, 1000, 5, 1)
    store.settle(state["run_id"], 600, 100, 10)
    store.reserve(state["run_id"], 600, 10, 1000, 5, 1)


def test_real_postgres_checkpoint_resumes_pending_node(store):
    class S(TypedDict):
        count: int
    counts = {"first": 0, "second": 0}

    def first(state):
        counts["first"] += 1
        return {"count": state["count"] + 1}

    def second(state):
        counts["second"] += 1
        if counts["second"] == 1:
            raise RuntimeError("worker interrupted during CI polling")
        return {"count": state["count"] + 1}

    def graph(saver):
        g = StateGraph(S)
        g.add_node("first", first)
        g.add_node("second", second)
        g.add_edge(START, "first")
        g.add_edge("first", "second")
        g.add_edge("second", END)
        return g.compile(checkpointer=saver)

    cfg = {"configurable": {"thread_id": "restart"}}
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        with pytest.raises(RuntimeError):
            graph(saver).invoke({"count": 0}, cfg)
    with PostgresSaver.from_conn_string(store.dsn) as new_saver:
        result = graph(new_saver).invoke(None, cfg)
    assert result["count"] == 2
    assert counts == {"first": 1, "second": 2}
