import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agent_lab.config import Project
from agent_lab.store import Store


@pytest.fixture
def store():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is required for real PostgreSQL integration")
    schema = "test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated = Store(make_conninfo(dsn, options=f"-csearch_path={schema}"))
    isolated.setup()
    try:
        yield isolated
    finally:
        with psycopg.connect(dsn, autocommit=True) as c:
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def project():
    return Project(id=12, name="synthetic", stack="python", test_paths=["tests"],
                   task_fixture=Path("demo/tasks/pricing.json"))


@pytest.fixture
def mr():
    return {"iid": 3, "state": "opened", "target_branch": "dev", "source_branch": "feature/discounts",
            "source_project_id": 12, "target_project_id": 12, "sha": "a" * 40, "author": {"id": 7},
            "diff_refs": {"base_sha": "b" * 40}}


@pytest.fixture
def report(mr):
    from agent_lab.contracts import Report
    return Report(sha=mr["sha"], source_sha=mr["sha"], pipeline_id=41, job_id=51, status="passed",
                  tests=1, failures=0, errors=0, coverage=40, diff_coverage=40, threshold=80, metric="diff_line",
                  gate="failed", junit_paths=["reports/junit.xml"], coverage_path="reports/coverage.xml",
                  log_path="reports/run.log", tool="cobertura", created_at="2026-09-27T00:00:00Z")
