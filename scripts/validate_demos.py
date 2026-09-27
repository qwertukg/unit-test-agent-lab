"""Real local builds and mutation check with HAND-WRITTEN fixtures, not cloud acceptance."""
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(stack):
    work = ROOT / ".state" / "local-demos" / (stack + "-" + uuid.uuid4().hex[:8])
    shutil.copytree(ROOT / "demo" / stack, work, ignore=shutil.ignore_patterns("target", "__pycache__", ".pytest_cache"))
    shutil.copy(ROOT / "ci/run.py", work / ".agent-ci.py")
    source = work / ("src/main/java/lab/Pricing.java" if stack == "java" else "product/pricing.py")
    feature = source.read_text()
    baseline = ("package lab;\npublic class Pricing {\n    public int total(int cents, boolean member, String coupon) {\n        return cents;\n    }\n}\n"
                if stack == "java" else "def total(cents: int, member: bool, coupon: str | None = None) -> int:\n    return cents\n")
    source.write_text(baseline)

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=work, stderr=subprocess.DEVNULL, text=True).strip()

    git("init", "-b", "dev")
    git("config", "user.name", "Synthetic Demo")
    git("config", "user.email", "demo@unit-test-lab.invalid")
    git("add", ".")
    git("commit", "-m", "baseline")
    git("update-ref", "refs/remotes/origin/dev", "HEAD")
    git("checkout", "-b", "feature/discounts")
    source.write_text(feature)
    git("add", ".")
    git("commit", "-m", "DEMO-1 discounts")
    env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "CI_COMMIT_REF_NAME": "feature/discounts", "CI_PIPELINE_ID": "1", "CI_JOB_ID": "1"}
    if stack == "java" and not shutil.which("mvn"):
        maven = ROOT / ".tools/maven/apache-maven-3.9.11/bin"
        env["PATH"] = str(maven) + os.pathsep + env["PATH"]
        jdk = Path("C:/Program Files/Java/jdk-18.0.2.1")
        if jdk.exists():
            env["JAVA_HOME"] = str(jdk)

    def check(label):
        p = subprocess.run([sys.executable, ".agent-ci.py"], cwd=work, env=env, capture_output=True, text=True, timeout=600)
        report_path = work / "reports/agent-report.json"
        if not report_path.exists():
            raise RuntimeError(f"{stack} {label}: no report: {p.stderr[-1000:]}")
        report = json.loads(report_path.read_text())
        destination = ROOT / "artifacts/local-demos" / stack / label
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(work / "reports", destination / "reports", dirs_exist_ok=True)
        if stack == "java":
            for path in ("target/surefire-reports", "target/site/jacoco"):
                if (work / path).exists():
                    shutil.copytree(work / path, destination / path, dirs_exist_ok=True)
        return report

    before = check("baseline")
    assert before["status"] == "passed" and before["gate"] == "failed", before
    fixture = ROOT / "tests/fixtures" / ("AgentPricingTest.java" if stack == "java" else "test_agent_pricing.py")
    target = work / ("src/test/java/lab/AgentPricingTest.java" if stack == "java" else "tests/test_agent_pricing.py")
    shutil.copy(fixture, target)
    git("add", target.relative_to(work).as_posix())
    git("commit", "-m", "handwritten fixture tests (not LLM)")
    env["CI_COMMIT_REF_NAME"] = "agent-verify/local/1"
    env["CI_PIPELINE_ID"] = env["CI_JOB_ID"] = "2"
    after = check("tests")
    assert after["status"] == "passed" and after["gate"] == "passed", after
    # Disposable working tree only. Not committed, never put into a tests MR.
    mutated = feature.replace("cents >= 10000", "cents > 10000")
    assert mutated != feature
    source.write_text(mutated)
    mutation = check("mutation")
    assert mutation["status"] == "assertion_failure" and mutation["failures"] > 0, mutation
    source.write_text(feature)
    result = {"stack": stack, "mode": "local actual execution, handwritten tests, no GitLab/cloud", "workspace": str(work),
              "before": before, "after": after, "mutation": mutation}
    print(json.dumps({"stack": stack, "before": before["diff_coverage"], "after": after["diff_coverage"],
                      "mutation_failures": mutation["failures"]}), flush=True)
    return result


def main():
    results = [run(stack) for stack in ("python", "java")]
    (ROOT / "artifacts/local-demos/summary.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
