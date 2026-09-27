from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: SecretStr = SecretStr("postgresql://lab:lab@localhost:55432/lab")
    gitlab_url: str = "http://localhost:8929"
    gitlab_public_url: str = "http://localhost:8929"
    gitlab_token: SecretStr = SecretStr("")
    api_token: SecretStr = SecretStr("")
    webhook_secret: SecretStr = SecretStr("")
    model_base_url: str = "http://localhost:4000/v1"
    model_alias: str = "test-agent"
    model_mode: Literal["cloud", "local"] = "cloud"
    model_json_mode: Literal["json_object", "prompt"] = "json_object"
    model_api_key: SecretStr = SecretStr("")
    model_timeout: int = 120
    max_output_tokens: int = 6000
    max_context_chars: int = 90000
    max_tool_steps: int = 8
    max_iterations: int = 4
    max_run_seconds: int = 3600
    max_model_calls: int = 32
    max_total_tokens: int = 250000
    # Conservative user-supplied upper bound per million tokens. No invented pricing.
    token_price_upper_usd: float = 0
    max_cost_usd: float = 5
    max_external_attempts: int = 3
    max_infra_retries: int = 2
    poll_seconds: float = 5
    project_config: Path = Path("config/projects.yaml")
    workspace_root: Path = Path("workspaces")


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    name: str
    stack: str
    target_branch: str = "dev"
    source_prefix: str = "feature/"
    generated_prefix: str = "ai-tests/"
    test_paths: list[str]
    report_job: str = "unit"
    report_path: str = "reports/agent-report.json"
    threshold: float = Field(default=80, ge=0, le=100)
    metric: str = "diff_line"
    config_path: str = ".test-agent.json"
    # SHA256 of the reviewed project config and CI/build files, set by bootstrap.
    trusted_files: dict[str, str] = Field(default_factory=dict)
    task_fixture: Path
    feedback_tag: str = "@unit-test-agent"


def projects(settings: Settings) -> dict[int, Project]:
    if not settings.project_config.exists():
        return {}
    raw = yaml.safe_load(settings.project_config.read_text(encoding="utf-8")) or {}
    result = [Project.model_validate(p) for p in raw.get("projects", [])]
    if len({p.id for p in result}) != len(result):
        raise ValueError("duplicate project id")
    return {p.id: p for p in result}
