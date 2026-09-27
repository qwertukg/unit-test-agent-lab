import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def digest(value) -> str:
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scenario(Strict):
    id: str
    purpose: str
    requirements: list[str] = Field(min_length=1)
    paths: list[str] = Field(min_length=1)
    setup: str
    inputs: str
    expected: str
    kind: Literal["normal", "boundary", "negative"]
    allowed_mocks: list[str]


class Scenarios(Strict):
    scenarios: list[Scenario] = Field(min_length=1, max_length=40)


class TestFile(Strict):
    path: str
    content: str = Field(min_length=1, max_length=60000)
    scenario_ids: list[str] = Field(min_length=1)


class Tests(Strict):
    files: list[TestFile] = Field(min_length=1, max_length=12)
    rationale: str


class Finding(Strict):
    object_id: Literal["O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8", "O9"]
    assessment: str


class Review(Strict):
    decision: Literal["publish", "revise_scenarios", "revise_tests"]
    findings: list[Finding] = Field(min_length=7)
    rationale: str
    product_defect: bool
    evidence: list[str]


class Report(Strict):
    schema_version: Literal[1] = 1
    sha: str
    source_sha: str
    pipeline_id: int
    job_id: int
    status: Literal["passed", "assertion_failure", "build_error", "infra_error"]
    tests: int = Field(ge=0)
    failures: int = Field(ge=0)
    errors: int = Field(ge=0)
    coverage: float | None = Field(ge=0, le=100)
    diff_coverage: float | None = Field(ge=0, le=100)
    threshold: float = Field(ge=0, le=100)
    metric: Literal["line", "diff_line"]
    gate: Literal["passed", "failed", "unknown"]
    junit_paths: list[str]
    coverage_path: str | None
    log_path: str
    tool: str
    created_at: str


class PolicyError(Exception):
    pass


class Blocked(Exception):
    pass


class BudgetExceeded(Exception):
    pass


class RetryLater(Exception):
    pass
