from psycopg.types.json import Jsonb

from agent_lab.contracts import Blocked, Report, RetryLater, digest
from agent_lab.policy import eligible


class Events:
    def __init__(self, settings, store, gitlab, project_map):
        self.settings, self.store, self.gitlab, self.projects = settings, store, gitlab, project_map

    def poll(self):
        """Polling is event reconciliation, never scheduled unconditional generation."""
        for project in self.projects.values():
            for mr in self.gitlab.all(f"projects/{project.id}/merge_requests", state="opened", target_branch=project.target_branch):
                if not mr["source_branch"].startswith(project.source_prefix):
                    continue
                full = self.gitlab.mr(project.id, mr["iid"])
                pipeline = self.gitlab.latest_source_pipeline(project.id, full)
                if pipeline and pipeline["status"] in ("failed", "success", "canceled", "skipped"):
                    self.store.event(f"pipeline:{project.id}:{pipeline['id']}", "pipeline", project.id,
                                     {"pipeline_id": pipeline["id"], "mr_iid": mr["iid"], "sha": full["sha"]})
            links = self.store.query("SELECT * FROM lab_links WHERE project_id=%s", (project.id,))
            for link in links:
                base = f"projects/{project.id}/merge_requests/{link['test_mr_iid']}"
                for award in self.gitlab.all(base + "/award_emoji"):
                    if award["name"] in ("thumbsdown", "thumbs_down", "-1"):
                        self.store.event(f"award:{project.id}:{link['test_mr_iid']}:{award['id']}", "award", project.id,
                                         {"mr_iid": link["mr_iid"], "test_mr_iid": link["test_mr_iid"], **award})
                for note in self.gitlab.all(base + "/notes", order_by="created_at", sort="asc"):
                    if project.feedback_tag in note["body"] and not note.get("system"):
                        key = f"note:{project.id}:{link['test_mr_iid']}:{note['id']}:{digest(note['body'])[:16]}"
                        self.store.event(key, "note", project.id,
                                         {"mr_iid": link["mr_iid"], "test_mr_iid": link["test_mr_iid"], **note})

    def handle(self, event):
        project = self.projects.get(event["project_id"])
        if project is None:
            return "ignored: project not allowed"
        data = event["payload"]
        kind = event["kind"]
        if kind == "webhook":
            # Payload never grants authority: poll authenticated API for canonical objects.
            note = data.get("note_snapshot")
            if note and project.feedback_tag in note["body"] and note.get("test_mr_iid") and note.get("id"):
                link = self.store.query("SELECT * FROM lab_links WHERE project_id=%s AND test_mr_iid=%s",
                                        (project.id, note["test_mr_iid"]), one=True)
                if link:
                    canonical = self.gitlab.get(f"projects/{project.id}/merge_requests/{note['test_mr_iid']}/notes/{note['id']}")
                    if canonical["author"]["id"] != note["author_id"]:
                        return "ignored: note author mismatch"
                    key = f"note:{project.id}:{note['test_mr_iid']}:{note['id']}:{digest(note['body'])[:16]}"
                    self.store.event(key, "note", project.id, {**canonical, "body": note["body"],
                        "mr_iid": link["mr_iid"], "test_mr_iid": note["test_mr_iid"], "snapshot_updated_at": note["updated_at"]})
            self.poll()
            return "reconciled via API"
        iid = data["mr_iid"]
        mr = self.gitlab.mr(project.id, iid)
        feedback = kind in ("award", "note") or (kind == "refresh" and data.get("feedback"))
        if feedback:
            link = self.store.query("SELECT * FROM lab_links WHERE project_id=%s AND mr_iid=%s",
                                    (project.id, iid), one=True)
            if not link:
                return "ignored: no published test MR"
            if kind in ("award", "note"):
                author_id = data["user"]["id"] if kind == "award" else data["author"]["id"]
                if author_id != mr["author"]["id"]:
                    return "ignored: not source MR author"
                self.store.query("""INSERT INTO lab_feedback(project_id,mr_iid,event_key,seq,payload)
                    VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (project.id, iid, event["event_key"], event["seq"], Jsonb({"kind": kind, **data})))
            test_mr = self.gitlab.mr(project.id, link["test_mr_iid"])
            if mr["state"] != "opened" or test_mr["state"] != "opened":
                return "saved: MR closed/merged; not reopening"
            if kind == "note":
                awards = self.store.query("""SELECT 1 FROM lab_feedback WHERE project_id=%s AND mr_iid=%s
                    AND payload->>'kind'='award' LIMIT 1""", (project.id, iid))
                if not awards:
                    return "saved: comment awaits author thumbsdown"
            pipeline = self.gitlab.latest_source_pipeline(project.id, mr)
            if not pipeline:
                raise Blocked("feedback context source pipeline missing")
            run = self.store.new_run(project.id, iid, mr["sha"], event["event_key"],
                                     {"source_pipeline": pipeline["id"], "previous_run": link["last_run_id"]})
            return "queued feedback: " + run
        pipeline = (self.gitlab.get(f"projects/{project.id}/pipelines/{data['pipeline_id']}") if kind == "pipeline"
                    else self.gitlab.latest_source_pipeline(project.id, mr))
        if not pipeline or pipeline["sha"] != mr["sha"]:
            return "ignored: obsolete/missing pipeline"
        result = self.gitlab.reports(project, pipeline["id"], mr["sha"])
        if result.get("infra_error"):
            return "ignored: infrastructure failure"
        report = Report.model_validate(result["report"])
        if not eligible(mr, report, project):
            return "ignored: not eligible coverage failure"
        run = self.store.new_run(project.id, iid, mr["sha"], "coverage", {"source_pipeline": pipeline["id"]})
        return "queued coverage: " + run

    def drain(self):
        with self.store.lock(0, 17012026) as lock:
            if lock is None:
                return
            for event in self.store.query("SELECT * FROM lab_events WHERE handled_at IS NULL ORDER BY seq LIMIT 100"):
                try:
                    disposition = self.handle(event)
                except RetryLater:
                    # Bounded retries; do not block every following event.
                    self.store.query("UPDATE lab_events SET attempts=attempts+1 WHERE seq=%s", (event["seq"],))
                    if event["attempts"] < 120:
                        continue
                    disposition = "blocked: event reconciliation timeout"
                except Blocked as e:
                    disposition = "blocked: " + str(e)
                except (ValueError, KeyError, TypeError):
                    # A malformed canonical payload/report must not starve all later events.
                    disposition = "blocked: invalid event or report schema"
                self.store.query("UPDATE lab_events SET handled_at=now(),disposition=%s WHERE seq=%s",
                                 (disposition, event["seq"]))
