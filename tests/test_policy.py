import io
import tarfile

import pytest

from agent_lab.contracts import PolicyError, Scenarios
from agent_lab.contracts import Tests as GeneratedFiles
from agent_lab.policy import consistent, eligible, safe_path, validate_tests
from agent_lab.repository import Repository


def scenarios():
    return Scenarios(scenarios=[{"id": "S1", "purpose": "discount", "requirements": ["R2"], "paths": ["product/pricing.py"],
                                 "setup": "none", "inputs": "1000 SAVE10", "expected": "900", "kind": "normal", "allowed_mocks": []}])


def manifest(path="tests/test_agent_pricing.py", content="def test_discount():\n    assert total(1000, False, 'SAVE10') == 900\n"):
    return GeneratedFiles(files=[{"path": path, "content": content, "scenario_ids": ["S1"]}], rationale="R2")


def test_only_coverage_fail_triggers(project, mr, report):
    assert eligible(mr, report, project)
    for values in ({"gate": "passed"}, {"gate": "unknown", "coverage": None}, {"status": "infra_error"},
                   {"status": "assertion_failure", "failures": 1}, {"status": "build_error"}, {"source_sha": "wrong"}):
        assert not eligible(mr, report.model_copy(update=values), project)
    for values in ({"target_branch": "main"}, {"source_branch": "ai-tests/3"}, {"state": "merged"},
                   {"sha": "obsolete"}, {"source_project_id": 99}):
        assert not eligible({**mr, **values}, report, project)


def test_report_sha_threshold_and_gate_are_checked(project, mr, report):
    assert consistent(report, mr["sha"], 41, 51, project)
    for values in ({"sha": "stale"}, {"pipeline_id": 42}, {"job_id": 52}, {"threshold": 10},
                   {"gate": "passed"}, {"metric": "line"}, {"coverage_path": None}):
        assert not consistent(report.model_copy(update=values), mr["sha"], 41, 51, project)


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "C:/secret", "tests/../product/a.py", "tests\\a.py", "./a", "tests//a", "a\x00"])
def test_path_escape(path):
    with pytest.raises(PolicyError):
        safe_path(path)


@pytest.mark.parametrize("path", ["product/pricing.py", "pom.xml", ".gitlab-ci.yml", "tests/conftest.py", "tests/.config", "tests/test_old.py"])
def test_edit_restrictions(project, path):
    with pytest.raises(PolicyError):
        validate_tests(manifest(path), scenarios(), project, {"tests/test_old.py"})


@pytest.mark.parametrize("content", ["def test_x():\n assert True", "pytest.skip('injected instructions')", "exec('print(1)')", "import subprocess", "def test_x():\n pass"])
def test_bypasses_rejected(project, content):
    with pytest.raises(PolicyError):
        validate_tests(manifest(content=content), scenarios(), project, set())


def test_valid_test_and_syntax_failure_goes_to_runner(project):
    validate_tests(manifest(), scenarios(), project, set())
    validate_tests(manifest(content="def test_agent(:\n assert total(1)==1"), scenarios(), project, set())


def test_archive_symlink_and_traversal(tmp_path):
    for name, is_link in (("repo/../../escape", False), ("repo/tests/link", True)):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w:gz") as tar:
            item = tarfile.TarInfo(name)
            if is_link:
                item.type, item.linkname = tarfile.SYMTYPE, "../../secret"
            tar.addfile(item)
        with pytest.raises(PolicyError):
            Repository.unpack(tmp_path, stream.getvalue())


def test_model_tools_cannot_read_secrets_or_execute(tmp_path):
    (tmp_path / ".env").write_text("secret=not-for-model")
    repo = Repository(tmp_path)
    with pytest.raises(PolicyError):
        repo.tool("read_file", {"path": ".env"})
    with pytest.raises(PolicyError):
        repo.tool("shell", {"command": "anything"})
