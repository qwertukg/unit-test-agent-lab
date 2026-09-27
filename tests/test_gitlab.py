import httpx
import pytest

from agent_lab.config import Settings
from agent_lab.contracts import Blocked
from agent_lab.gitlab import GitLab


@pytest.mark.parametrize("case", ["wrong_parent", "production_edit", "existing_file", "tampered_test", "incomplete"])
def test_remote_tree_is_checked_not_just_model_intent(case):
    def handler(request):
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"parent_ids": ["wrong" if case == "wrong_parent" else "source"]})
        if request.url.path.endswith("/compare"):
            return httpx.Response(200, json={"compare_timeout": case == "incomplete", "diffs": [{
                "new_path": "product.py" if case == "production_edit" else "tests/test_agent_x.py",
                "new_file": case != "existing_file", "deleted_file": False, "renamed_file": False}]})
        return httpx.Response(200, text="tampered" if case == "tampered_test" else "expected")
    client = GitLab(Settings(_env_file=None), httpx.MockTransport(handler))
    with pytest.raises(Blocked):
        client.verify_candidate(12, "source", "candidate", [{"path": "tests/test_agent_x.py", "content": "expected"}])
