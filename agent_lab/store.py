from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agent_lab.contracts import BudgetExceeded, digest


class Store:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)

    def setup(self):
        with self.connect() as c:
            c.execute(Path(__file__).with_name("schema.sql").read_text())

    def query(self, sql, params=(), one=False):
        with self.connect() as c:
            cur = c.execute(sql, params)
            return (cur.fetchone() if one else cur.fetchall()) if cur.description else None

    def event(self, key, kind, project_id, payload):
        return self.query("""INSERT INTO lab_events(event_key,kind,project_id,payload)
            VALUES (%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING RETURNING seq""",
                          (key, kind, project_id, Jsonb(payload)), one=True)

    def new_run(self, project_id, mr_iid, sha, trigger, state):
        run_id = digest([project_id, mr_iid, sha, trigger])[:32]
        state = {**state, "run_id": run_id, "project_id": project_id, "mr_iid": mr_iid,
                 "source_sha": sha, "iteration": 0, "trigger": trigger}
        self.query("""INSERT INTO lab_runs(id,project_id,mr_iid,source_sha,trigger,initial_state)
            VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING""",
                   (run_id, project_id, mr_iid, sha, trigger, Jsonb(state)))
        return run_id

    def run(self, run_id):
        return self.query("SELECT * FROM lab_runs WHERE id=%s", (run_id,), one=True)

    def status(self, run_id, status, reason=None, iteration=None):
        self.query("""UPDATE lab_runs SET status=%s,reason=%s,iteration=coalesce(%s,iteration),
            updated_at=now(),lease_until=NULL WHERE id=%s""", (status, reason, iteration, run_id))

    def object(self, state, object_id, slot, payload, producer, inputs=None, test_sha=None):
        # Serialize versions and replay an already completed node without duplicate versions.
        with self.connect() as c, c.transaction():
            c.execute("SELECT id FROM lab_runs WHERE id=%s FOR UPDATE", (state["run_id"],))
            old = c.execute("SELECT * FROM lab_objects WHERE run_id=%s AND object_id=%s AND slot=%s",
                            (state["run_id"], object_id, slot)).fetchone()
            if old:
                return old
            return c.execute("""INSERT INTO lab_objects
                (run_id,object_id,slot,version,iteration,project_id,mr_iid,source_sha,test_sha,
                 producer,content_hash,payload,inputs)
                VALUES (%s,%s,%s,(SELECT coalesce(max(version),0)+1 FROM lab_objects
                    WHERE run_id=%s AND object_id=%s),%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (state["run_id"], object_id, slot, state["run_id"], object_id, state["iteration"],
                 state["project_id"], state["mr_iid"], state["source_sha"], test_sha, producer,
                 digest(payload), Jsonb(payload), Jsonb(inputs or {}))).fetchone()

    def latest(self, run_id):
        rows = self.query("""SELECT DISTINCT ON (object_id) * FROM lab_objects
            WHERE run_id=%s ORDER BY object_id,version DESC""", (run_id,))
        return {r["object_id"]: r for r in rows}

    def intent(self, key, run_id, kind, intent):
        self.query("""INSERT INTO lab_actions(action_key,run_id,kind,intent) VALUES (%s,%s,%s,%s)
            ON CONFLICT(action_key) DO NOTHING""", (key, run_id, kind, Jsonb(intent)))
        row = self.query("SELECT * FROM lab_actions WHERE action_key=%s", (key,), one=True)
        if row["intent"] != intent:
            raise ValueError("action identity reused for a different intent")
        return row

    def complete(self, key, result):
        self.query("UPDATE lab_actions SET status='completed',result=%s,updated_at=now() WHERE action_key=%s",
                   (Jsonb(result), key))

    def attempt(self, key, limit):
        row = self.query("""UPDATE lab_actions SET attempts=attempts+1,updated_at=now()
            WHERE action_key=%s AND attempts<%s RETURNING attempts""", (key, limit), one=True)
        if not row:
            raise BudgetExceeded("external action attempt limit")

    def reserve(self, run_id, tokens, price, max_tokens, max_calls, max_cost):
        cost = tokens * price / 1_000_000
        row = self.query("""UPDATE lab_runs SET model_calls=model_calls+1,
            tokens_reserved=tokens_reserved+%s,cost_reserved=cost_reserved+%s
            WHERE id=%s AND model_calls<%s AND tokens_reserved+tokens_used+%s<=%s
            AND cost_reserved+cost_used+%s<=%s RETURNING id""",
            (tokens, cost, run_id, max_calls, tokens, max_tokens, cost, max_cost), one=True)
        if not row:
            raise BudgetExceeded("model token/call/cost budget")

    def settle(self, run_id, reserved, used, price):
        self.query("""UPDATE lab_runs SET tokens_reserved=tokens_reserved-%s,tokens_used=tokens_used+%s,
            cost_reserved=cost_reserved-%s,cost_used=cost_used+%s WHERE id=%s""",
                   (reserved, used, reserved * price / 1e6, used * price / 1e6, run_id))

    @contextmanager
    def lock(self, project_id, mr_iid):
        with self.connect() as c:
            got = c.execute("SELECT pg_try_advisory_lock(%s,%s) AS locked", (project_id, mr_iid)).fetchone()
            try:
                yield c if got["locked"] else None
            finally:
                if got["locked"] and not c.closed:
                    c.execute("SELECT pg_advisory_unlock(%s,%s)", (project_id, mr_iid))
