"""Reviewed CI entrypoint, copied into demo repositories and pinned by SHA256.

Only the runner executes this file. The orchestrator never runs product code.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def command(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()


def coverage(path, fmt, root):
    tree = ET.parse(path).getroot()
    lines = {}
    if fmt == "jacoco":
        for package in tree.findall("package"):
            for file in package.findall("sourcefile"):
                name = "/".join(p for p in (root, package.get("name"), file.get("name")) if p)
                lines[name] = {int(line.get("nr")): int(line.get("ci")) > 0 for line in file.findall("line")}
    elif fmt == "cobertura":
        for file in tree.findall(".//class"):
            name = file.get("filename").replace("\\", "/")
            if not name.startswith(root + "/"):
                name = root + "/" + name
            lines.setdefault(name, {}).update({int(line.get("number")): int(line.get("hits")) > 0
                                              for line in file.findall("./lines/line")})
    else:
        raise ValueError("unregistered coverage adapter")
    total = sum(len(v) for v in lines.values())
    if not total:
        raise ValueError("empty coverage report")
    covered = sum(sum(v.values()) for v in lines.values())
    return lines, covered / total * 100


def diff_lines(base, source):
    diff = command(["git", "diff", "--no-ext-diff", "--no-renames", "--unified=0", base, source, "--"])
    changed, path = {}, None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@") and path:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            start, count = int(m.group(1)), int(m.group(2) or 1)
            changed.setdefault(path, set()).update(range(start, start + count))
    return changed


def main():
    cfg = json.loads(Path(".test-agent.json").read_text())
    out = Path("reports")
    out.mkdir(exist_ok=True)
    # Fresh checkout per job; additionally reject any stale XML before running.
    for item in [*glob.glob(cfg["junit_glob"]), cfg["coverage_path"]]:
        path = Path(item).resolve()
        if not path.is_relative_to(Path.cwd().resolve()):
            raise ValueError("report path outside workspace")
        if path.is_file():
            path.unlink()
    sha = os.environ.get("CI_COMMIT_SHA") or command(["git", "rev-parse", "HEAD"])
    ref = os.environ.get("CI_COMMIT_REF_NAME", "")
    source = command(["git", "rev-parse", sha + "^"]) if ref.startswith(("agent-verify/", "ai-tests/")) else sha
    base = command(["git", "merge-base", "origin/" + cfg["target_branch"], source])
    # Optional, bounded delay for the explicit restart acceptance scenario only.
    if ref.startswith("agent-verify/"):
        time.sleep(min(60, max(0, int(os.getenv("LAB_TEST_DELAY_SECONDS", "0")))))
    statuses = []
    with (out / "run.log").open("w", encoding="utf-8") as log:
        for args in cfg["commands"]:
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise ValueError("commands must be argv arrays")
            try:
                executable = shutil.which(args[0])
                if not executable:
                    raise FileNotFoundError(args[0])
                result = subprocess.run([executable, *args[1:]], stdout=log, stderr=subprocess.STDOUT, timeout=300, shell=False)
                statuses.append(result.returncode)
            except (OSError, subprocess.TimeoutExpired) as e:
                log.write(type(e).__name__)
                statuses.append(127)
    junit = glob.glob(cfg["junit_glob"])
    tests = failures = errors = 0
    for path in junit:
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
        for suite in suites:
            tests += int(suite.get("tests", 0))
            failures += int(suite.get("failures", 0))
            errors += int(suite.get("errors", 0))
    status = "passed"
    if 127 in statuses:
        status = "infra_error"
    elif not tests or errors or (any(statuses) and not failures):
        status = "build_error"
    elif failures:
        status = "assertion_failure"
    cov = diff_cov = None
    coverage_path = cfg["coverage_path"] if Path(cfg["coverage_path"]).exists() else None
    if coverage_path:
        lines, cov = coverage(coverage_path, cfg["coverage_format"], cfg["source_root"])
        changes = diff_lines(base, source)
        hits = [hit for path, rows in lines.items() for line, hit in rows.items() if line in changes.get(path, set())]
        diff_cov = sum(hits) / len(hits) * 100 if hits else None
    metric_value = diff_cov if cfg["metric"] == "diff_line" else cov
    gate = "unknown" if metric_value is None else "passed" if metric_value >= cfg["threshold"] else "failed"
    report = {"schema_version": 1, "sha": sha, "source_sha": source,
              "pipeline_id": int(os.environ.get("CI_PIPELINE_ID", 0)), "job_id": int(os.environ.get("CI_JOB_ID", 0)),
              "status": status, "tests": tests, "failures": failures, "errors": errors, "coverage": cov,
              "diff_coverage": diff_cov, "threshold": cfg["threshold"], "metric": cfg["metric"], "gate": gate,
              "junit_paths": [Path(p).as_posix() for p in junit], "coverage_path": coverage_path,
              "log_path": "reports/run.log", "tool": cfg["coverage_format"], "created_at": datetime.now(timezone.utc).isoformat()}
    (out / "agent-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": status, "tests": tests, "coverage": cov, "diff_coverage": diff_cov, "gate": gate}))
    return 0 if status == "passed" and gate == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
