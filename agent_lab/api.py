import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from agent_lab.config import Settings, projects
from agent_lab.contracts import digest
from agent_lab.store import Store


def create_app(settings=None, store=None):
    settings = settings or Settings()
    store = store or Store(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app):
        store.setup()
        yield

    app = FastAPI(title="Unit Test Agent Lab", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def auth(authorization: str = Header(default="")):
        token = settings.api_token.get_secret_value()
        if not token or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        try:
            store.query("SELECT 1")
        except Exception:
            raise HTTPException(503, "database unavailable") from None
        return {"status": "ready", "projects": len(projects(settings))}

    @app.post("/webhooks/gitlab", status_code=202)
    async def webhook(request: Request, x_gitlab_token: str = Header(default=""),
                      x_gitlab_event_uuid: str = Header(default="")):
        secret = settings.webhook_secret.get_secret_value()
        if not secret or not hmac.compare_digest(x_gitlab_token, secret):
            raise HTTPException(401)
        raw = await request.body()
        if len(raw) > 1_000_000:
            raise HTTPException(413)
        import json
        try:
            data = json.loads(raw)
            project_id = int(data.get("project", {}).get("id", 0))
        except (ValueError, AttributeError):
            raise HTTPException(400, "invalid webhook") from None
        if project_id not in projects(settings):
            raise HTTPException(403, "project not allowed")
        # Preserve tagged note text durably even if edited before worker polling.
        # Identity/author is reconciled with canonical API; this text remains untrusted data.
        payload = {"kind": data.get("object_kind", "unknown")}
        attributes = data.get("object_attributes", {})
        if payload["kind"] == "note" and attributes.get("noteable_type") == "MergeRequest":
            payload["note_snapshot"] = {"id": attributes.get("id"), "body": attributes.get("note", ""),
                                        "test_mr_iid": data.get("merge_request", {}).get("iid"),
                                        "author_id": data.get("user", {}).get("id"),
                                        "created_at": attributes.get("created_at"), "updated_at": attributes.get("updated_at")}
        key = f"webhook:{project_id}:" + (x_gitlab_event_uuid or digest(raw))
        inserted = store.event(key, "webhook", project_id, payload)
        return {"accepted": True, "duplicate": inserted is None}

    @app.get("/runs", dependencies=[Depends(auth)])
    def runs():
        return store.query("""SELECT id,project_id,mr_iid,source_sha,status,reason,iteration,created_at,
            updated_at,model_calls,tokens_used,cost_used FROM lab_runs ORDER BY created_at DESC LIMIT 100""")

    @app.get("/runs/{run_id}", dependencies=[Depends(auth)])
    def run(run_id: str):
        row = store.run(run_id)
        if not row:
            raise HTTPException(404)
        return row

    @app.get("/runs/{run_id}/objects", dependencies=[Depends(auth)])
    def objects(run_id: str):
        return store.query("SELECT * FROM lab_objects WHERE run_id=%s ORDER BY id", (run_id,))

    @app.get("/runs/{run_id}/actions", dependencies=[Depends(auth)])
    def actions(run_id: str):
        return store.query("SELECT * FROM lab_actions WHERE run_id=%s ORDER BY created_at", (run_id,))

    @app.get("/runs/{run_id}/calls", dependencies=[Depends(auth)])
    def calls(run_id: str):
        return store.query("SELECT * FROM lab_calls WHERE run_id=%s ORDER BY id", (run_id,))

    @app.post("/runs/{run_id}/cancel", dependencies=[Depends(auth)])
    def cancel(run_id: str):
        row = store.query("UPDATE lab_runs SET cancel_requested=true WHERE id=%s RETURNING id", (run_id,), one=True)
        if not row:
            raise HTTPException(404)
        return {"cancel_requested": True}

    @app.post("/runs/{run_id}/resume", dependencies=[Depends(auth)])
    def resume(run_id: str):
        row = store.query("""UPDATE lab_runs SET status='queued',lease_until=NULL,reason=NULL
            WHERE id=%s AND status='needs_human' AND NOT cancel_requested RETURNING id""", (run_id,), one=True)
        if not row:
            raise HTTPException(409, "run not resumable")
        # Budgets, actions and checkpoints are preserved; resume never resets spending limits.
        return {"queued": True}

    @app.get("/events", dependencies=[Depends(auth)])
    def events():
        return store.query("SELECT seq,event_key,kind,disposition,handled_at FROM lab_events ORDER BY seq DESC LIMIT 100")

    return app


app = create_app()
