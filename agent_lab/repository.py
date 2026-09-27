import base64
import io
import os
import subprocess
import tarfile
from pathlib import Path

from agent_lab.contracts import Blocked, PolicyError, digest
from agent_lab.policy import safe_path

MAX_FILE = 250_000
MAX_REPO = 30_000_000


class Repository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.used: set[str] = set()

    @classmethod
    def unpack(cls, root: Path, archive: bytes):
        root.mkdir(parents=True, exist_ok=True)
        if (root / ".snapshot-ready").exists():
            return cls(root)
        total = 0
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar:
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1] or member.isdir():
                    continue
                rel = parts[1]
                safe_path(rel)
                if not member.isfile() or member.issym() or member.islnk():
                    raise PolicyError("repository links/devices forbidden")
                if ".git" in rel.split("/") or rel == ".snapshot-ready":
                    raise PolicyError("reserved path")
                total += member.size
                if member.size > MAX_FILE or total > MAX_REPO:
                    raise Blocked("repository exceeds local snapshot budget")
                target = root.joinpath(*rel.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(tar.extractfile(member).read())
        (root / ".snapshot-ready").write_text("1")
        return cls(root)

    def files(self):
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                      if p.is_file() and not p.is_symlink() and p.name != ".snapshot-ready")

    def read(self, path: str):
        safe_path(path)
        p = self.root.joinpath(*path.split("/"))
        if not p.resolve().is_relative_to(self.root) or p.is_symlink():
            raise PolicyError("path escape")
        # Never expose credentials even if accidentally committed to a demo product.
        if any(part.startswith(".env") or part in (".git", ".ssh", ".aws") for part in p.parts):
            raise PolicyError("sensitive path")
        if p.stat().st_size > MAX_FILE:
            raise Blocked("file too large; explicit adapter/context selection required")
        self.used.add(path)
        return p.read_text(encoding="utf-8")

    def tool(self, name: str, args: dict):
        if name == "list_files":
            return self.files()[:1500]
        if name == "read_file":
            return self.read(args["path"])
        if name == "search":
            needle = args["text"]
            if not isinstance(needle, str) or not 1 <= len(needle) <= 200:
                raise PolicyError("invalid search")
            hits = []
            for path in self.files():
                try:
                    lines = self.read(path).splitlines()
                except (UnicodeError, PolicyError, OSError):
                    continue
                for line, value in enumerate(lines, 1):
                    if needle in value:
                        hits.append({"path": path, "line": line, "text": value[:500]})
                        if len(hits) >= 80:
                            return hits
            return hits
        raise PolicyError("unknown tool")

    def verify_config(self, project):
        if not project.trusted_files or project.config_path not in project.trusted_files:
            raise Blocked("project configuration must be reviewed and pinned")
        for path, expected in project.trusted_files.items():
            if digest(self.read(path).encode()) != expected:
                raise Blocked("reviewed project config/build/CI changed: " + path)


def publish_git(settings, project_path, candidate_sha, branch, expected_sha, run_id):
    """Push exactly the tested commit using an atomic force-with-lease, never merge."""
    root = settings.workspace_root.resolve() / "publish" / run_id
    root.mkdir(parents=True, exist_ok=True)
    url = settings.gitlab_url.rstrip("/") + "/" + project_path + ".git"
    token = base64.b64encode(("oauth2:" + settings.gitlab_token.get_secret_value()).encode()).decode()
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_COUNT": "2",
           "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": "Authorization: Basic " + token,
           "GIT_CONFIG_KEY_1": "core.hooksPath", "GIT_CONFIG_VALUE_1": os.devnull}

    def git(*args):
        p = subprocess.run(["git", *args], cwd=root, env=env, capture_output=True, timeout=120)
        if p.returncode:
            # Git diagnostics may contain URLs/headers. Do not propagate them.
            raise Blocked("git operation failed: " + args[0])

    git("init", "--bare", ".")
    git("fetch", "--no-tags", url, candidate_sha)
    git("push", f"--force-with-lease=refs/heads/{branch}:{expected_sha or ''}",
        url, f"{candidate_sha}:refs/heads/{branch}")
    return {"sha": candidate_sha, "branch": branch}
