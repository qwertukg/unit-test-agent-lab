import logging
import os
import time
import uuid

from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import ValidationError

from agent_lab.config import Settings, projects
from agent_lab.contracts import Blocked, BudgetExceeded, PolicyError, RetryLater
from agent_lab.events import Events
from agent_lab.gitlab import GitLab
from agent_lab.model import Model
from agent_lab.store import Store
from agent_lab.workflow import Workflow

log = logging.getLogger("lab.worker")


def execute(settings, store, gitlab, model, project_map, saver, run, owner):
    with store.lock(run["project_id"], run["mr_iid"]) as lock:
        if lock is None:
            return False
        # Recheck after taking the session lock; another worker may have completed it.
        row = store.query("""UPDATE lab_runs SET status='running',owner=%s,attempts=attempts+1,
            lease_until=now()+interval '180 seconds',updated_at=now(),started_at=coalesce(started_at,now())
            WHERE id=%s AND status IN ('queued','waiting','running')
            AND (lease_until IS NULL OR lease_until<now()) RETURNING *""", (owner, run["id"]), one=True)
        if not row:
            return False
        flow = Workflow(settings, store, gitlab, model, project_map[run["project_id"]], lock)
        graph = flow.graph(saver)
        config = {"configurable": {"thread_id": run["id"]}, "recursion_limit": 100}
        try:
            snapshot = graph.get_state(config)
            if snapshot.values and not snapshot.next:
                result = snapshot.values
            else:
                result = graph.invoke(None if snapshot.values else run["initial_state"], config)
            outcome = result.get("result", "needs_human")
            store.status(run["id"], "published" if outcome == "published" else "needs_human", outcome, result["iteration"])
            log.info("run_id=%s outcome=%s iteration=%s", run["id"], outcome, result["iteration"])
        except RetryLater as e:
            store.query("""UPDATE lab_runs SET status='waiting',reason=%s,
                lease_until=now()+(%s * interval '1 second'),updated_at=now() WHERE id=%s""",
                        (str(e), settings.poll_seconds, run["id"]))
        except (Blocked, PolicyError, BudgetExceeded, ValidationError) as e:
            reason = str(e) if not isinstance(e, ValidationError) else (
                "structured model/report validation failed: "
                + ", ".join(sorted({item["type"] for item in e.errors(include_input=False, include_context=False)})))
            status = "cancelled" if store.run(run["id"])["cancel_requested"] else "needs_human"
            store.status(run["id"], status, type(e).__name__ + ": " + reason)
            log.warning("run_id=%s stopped=%s", run["id"], type(e).__name__)
        except Exception as e:
            # Persist a bounded retry, without leaking HTTP headers or source in tracebacks.
            if row["attempts"] >= settings.max_external_attempts:
                store.status(run["id"], "needs_human", "unexpected " + type(e).__name__)
            else:
                store.query("""UPDATE lab_runs SET status='waiting',reason=%s,
                    lease_until=now()+interval '10 seconds' WHERE id=%s""", (type(e).__name__, run["id"]))
            log.error("run_id=%s error_type=%s", run["id"], type(e).__name__)
        return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    store = Store(settings.database_url.get_secret_value())
    store.setup()
    gitlab, model = GitLab(settings), Model(settings, store)
    owner = f"{os.getpid()}:{uuid.uuid4().hex[:8]}"
    with PostgresSaver.from_conn_string(store.dsn) as saver:
        saver.setup()
        next_poll = 0
        while True:
            project_map = projects(settings)
            events = Events(settings, store, gitlab, project_map)
            try:
                if time.monotonic() >= next_poll:
                    events.poll()
                    next_poll = time.monotonic() + 15
                events.drain()
            except Exception as e:
                log.warning("event_poll error_type=%s", type(e).__name__)
                next_poll = time.monotonic() + 15
            rows = store.query("""SELECT * FROM lab_runs WHERE status IN ('queued','waiting','running')
                AND (lease_until IS NULL OR lease_until<now()) ORDER BY created_at LIMIT 20""")
            for run in rows:
                if run["project_id"] in project_map:
                    execute(settings, store, gitlab, model, project_map, saver, run, owner)
            time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    main()
